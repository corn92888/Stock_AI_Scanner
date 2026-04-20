import twstock
import yfinance as yf
import pandas as pd
from tqdm import tqdm
import datetime
import os
import sys
import time
import requests 
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from logic import calculate_indicators, check_trend_strict, check_reversal_strict, check_wave_strict
except ImportError:
    print("❌ 找不到 logic.py，請確認它在同一個資料夾內。")
    sys.exit()

from dotenv import load_dotenv
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "") 
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")    

def send_telegram_message(msg_lines):
    try:
        full_text = "".join(msg_lines)
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": full_text, "disable_web_page_preview": True}
        response = requests.post(url, data=payload)
        if response.status_code == 200: print("✅ Telegram 訊息發送成功！")
        else: print(f"❌ 發送失敗: {response.text}")
    except Exception as e: print(f"❌ Telegram 連線錯誤: {e}")

def sort_by_industry_heat(data_list, secondary_sort_key, ascending=False):
    if not data_list: return pd.DataFrame()
    df = pd.DataFrame(data_list)
    industry_means = df.groupby('產業族群')['漲跌幅'].mean()
    df['產業熱度'] = df['產業族群'].map(industry_means)
    df = df.sort_values(by=['產業熱度', secondary_sort_key], ascending=[False, ascending])
    return df

def add_url_column(df, col_name='代號'):
    if df.empty: return df
    df_link = df.copy()
    df_link['K線圖'] = df_link[col_name].apply(lambda x: f'https://tw.stock.yahoo.com/quote/{x}')
    return df_link

def batch_download(ticker_list, period="2y", chunk_size=200):
    """批次下載股票資料，回傳 {yf_ticker: DataFrame} 字典"""
    all_data = {}
    
    for i in tqdm(range(0, len(ticker_list), chunk_size), desc="📥 批次下載"):
        chunk = ticker_list[i:i+chunk_size]
        try:
            raw = yf.download(chunk, period=period, progress=False, auto_adjust=False, threads=True)
            if raw.empty:
                continue
            
            if len(chunk) == 1:
                t = chunk[0]
                df = raw.copy()
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = [col[0] for col in df.columns]
                df = df.loc[:, ~df.columns.duplicated()]
                for c in ['Open', 'High', 'Low', 'Close', 'Volume']:
                    if c in df.columns: df[c] = pd.to_numeric(df[c], errors='coerce')
                df.dropna(subset=['Close'], inplace=True)
                if not df.empty:
                    all_data[t] = df
            else:
                for t in chunk:
                    try:
                        if isinstance(raw.columns, pd.MultiIndex):
                            ticker_cols = raw.columns.get_level_values(1)
                            if t not in ticker_cols.unique():
                                continue
                            df = raw.loc[:, raw.columns.get_level_values(1) == t].copy()
                            df.columns = df.columns.get_level_values(0)
                        else:
                            df = raw.copy()
                        
                        df = df.loc[:, ~df.columns.duplicated()]
                        for c in ['Open', 'High', 'Low', 'Close', 'Volume']:
                            if c in df.columns: df[c] = pd.to_numeric(df[c], errors='coerce')
                        df.dropna(subset=['Close'], inplace=True)
                        if not df.empty:
                            all_data[t] = df
                    except Exception:
                        pass
        except Exception:
            pass
    
    return all_data

def run_scanner():
    start_time = time.time()
    print("🔄 讀取台股清單...")
    codes = twstock.codes
    tickers = [c for c in codes.keys() if codes[c].type == '股票']
    
    print(f"📊 掃描標的: {len(tickers)} 檔")
    print("🎯 模式: 高防禦+張數對齊版 (批次加速)")
    print("-" * 40)
    
    # Step 1: 精準分類上市/上櫃，建立對照表
    yf_to_code = {}
    for t in tickers:
        suffix = 'TWO' if codes[t].market == '上櫃' else 'TW'
        yf_to_code[f"{t}.{suffix}"] = t
    
    all_yf_tickers = list(yf_to_code.keys())
    
    # Step 2: 批次下載 K 線資料
    all_stock_data = batch_download(all_yf_tickers, period="2y", chunk_size=200)
    
    # 顯示資料日期 (排查資料是否最新)
    if all_stock_data:
        sample_df = next(iter(all_stock_data.values()))
        latest_date = sample_df.index[-1]
        print(f"📆 最新資料日期: {latest_date.strftime('%Y-%m-%d')} ({latest_date.strftime('%A')})")
        today = datetime.date.today()
        if latest_date.date() < today:
            weekday = today.weekday()
            if weekday >= 5: # 週六日
                print("ℹ️  今天是假日，資料為最近一個交易日的收盤價 (正常)")
            else:
                print("⚠️  資料日期不是今天，可能尚未開盤或盤中資料延遲")
    
    print(f"📊 成功下載: {len(all_stock_data)}/{len(all_yf_tickers)} 檔")
    
    # Step 3: 多執行緒計算技術指標與策略判斷
    list_trend, list_reversal, list_wave = [], [], []
    
    def process_stock(yf_ticker, df_raw):
        try:
            code = yf_to_code[yf_ticker]
            df = calculate_indicators(df_raw)
            if df is None: return None
            
            stock_info = codes[code]
            name, industry = stock_info.name, stock_info.group if stock_info.group else "其他"
            last = df.iloc[-1]
            
            res_t, res_r, res_w = None, None, None
            
            is_trend, note_trend, pct_change, sl_t = check_trend_strict(df)
            if is_trend:
                res_t = {
                    "產業族群": industry, "代號": code, "名稱": name,
                    "現價": round(last['Close'], 2), "防守價": round(sl_t, 2),
                    "漲跌幅": round(pct_change, 2), "成交量(張)": int(last['Volume'] / 1000),
                    "RSI": round(last['RSI'], 1), "條件": note_trend
                }
            
            is_rev, note_rev, pct_change, sl_r = check_reversal_strict(df)
            if is_rev:
                res_r = {
                    "產業族群": industry, "代號": code, "名稱": name,
                    "現價": round(last['Close'], 2), "防守價": round(sl_r, 2),
                    "漲跌幅": round(pct_change, 2), "成交量(張)": int(last['Volume'] / 1000),
                    "條件": note_rev
                }

            is_wave, note_wave, pct_change, sl_w = check_wave_strict(df)
            if is_wave:
                res_w = {
                    "產業族群": industry, "代號": code, "名稱": name,
                    "現價": round(last['Close'], 2), "防守價": round(sl_w, 2),
                    "漲跌幅": round(pct_change, 2), "成交量(張)": int(last['Volume'] / 1000),
                    "條件": note_wave
                }
            return (res_t, res_r, res_w)
        except:
            return None
    
    cpu_workers = min(os.cpu_count() or 4, 8)
    with ThreadPoolExecutor(max_workers=cpu_workers) as executor:
        futures = {executor.submit(process_stock, yt, df): yt for yt, df in all_stock_data.items()}
        for future in tqdm(as_completed(futures), total=len(futures), desc="🧠 分析指標"):
            res = future.result()
            if res:
                if res[0]: list_trend.append(res[0])
                if res[1]: list_reversal.append(res[1])
                if res[2]: list_wave.append(res[2])
            
    if not os.path.exists('Reports'): os.makedirs('Reports')
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d_%H%M')
    filename = f"Reports/選股日報_{timestamp}.xlsx"
    today_str = datetime.datetime.now().strftime('%m/%d')
    
    with pd.ExcelWriter(filename) as writer:
        LIMIT_N = 10
        
        msg_trend = [f"📅 {today_str} 玉米帶你盤後回顧\n"]
        if list_trend:
            df_t = sort_by_industry_heat(list_trend, secondary_sort_key='RSI', ascending=False)
            add_url_column(df_t).to_excel(writer, sheet_name='順勢突破', index=False)
            msg_trend.extend([f"🚀 順勢噴出 (Top {min(len(list_trend), LIMIT_N)})\n", "----------------\n"])
            for _, row in df_t.head(LIMIT_N).iterrows():
                msg_trend.append(f"🏭[{row['產業族群']}] {row['代號']} {row['名稱']} (${row['現價']})\n   └ 漲:{row['漲跌幅']}% | 防守:${row['防守價']} | 量:{row['成交量(張)']}張\n")
        else:
            msg_trend.append("🚀 順勢噴出: 無\n")
            
        send_telegram_message(msg_trend)

        msg_rev = []
        if list_reversal:
            df_r = sort_by_industry_heat(list_reversal, secondary_sort_key='漲跌幅', ascending=True)
            add_url_column(df_r).to_excel(writer, sheet_name='低檔爆量', index=False)
            msg_rev.extend([f"↩️ 逆勢抄底 (Top {min(len(list_reversal), LIMIT_N)})\n", "----------------\n"])
            for _, row in df_r.head(LIMIT_N).iterrows():
                msg_rev.append(f"🏭[{row['產業族群']}] {row['代號']} {row['名稱']} (${row['現價']})\n   └ 漲:{row['漲跌幅']}% | 防守:${row['防守價']} | 量:{row['成交量(張)']}張\n")
        else:
            msg_rev.append("↩️ 逆勢抄底: 無\n")

        send_telegram_message(msg_rev)

        msg_wave = []
        if list_wave:
            df_w = sort_by_industry_heat(list_wave, secondary_sort_key='漲跌幅', ascending=True)
            add_url_column(df_w).to_excel(writer, sheet_name='波段蓄勢', index=False)
            msg_wave.extend([f"🌊 波段蓄勢 (Top {min(len(list_wave), LIMIT_N)})\n", "----------------\n"])
            for _, row in df_w.head(LIMIT_N).iterrows():
                msg_wave.append(f"🏭[{row['產業族群']}] {row['代號']} {row['名稱']} (${row['現價']})\n   └ 漲:{row['漲跌幅']}% | 防守:${row['防守價']} | 量:{row['成交量(張)']}張\n")
        else:
            msg_wave.append("🌊 波段蓄勢: 無\n")
            
        msg_wave.extend(["================\n", "🔗 點此查詢: https://tw.stock.yahoo.com/"])
        send_telegram_message(msg_wave)
    
    elapsed = time.time() - start_time
    minutes, seconds = divmod(int(elapsed), 60)
    print(f"\n📊 掃描完成！Excel 已儲存: {filename}")
    print(f"⏱️  總耗時: {minutes} 分 {seconds} 秒")

if __name__ == "__main__":
    run_scanner()