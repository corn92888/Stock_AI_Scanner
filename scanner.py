import twstock
import pandas as pd
from tqdm import tqdm
import datetime
import os
import sys
import time
import requests 

# --- 確保 logic.py 存在 ---
try:
    from logic import get_stock_data, calculate_indicators, check_trend_strict, check_reversal_strict, check_wave_strict
except ImportError:
    print("❌ 找不到 logic.py，請確認它在同一個資料夾內。")
    sys.exit()

from dotenv import load_dotenv

# 載入 .env 環境變數
load_dotenv()

# ==========================================
# 👇 Telegram 設定區 (.env 設定)
# ==========================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "") 
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")          
# ==========================================

def send_telegram_message(msg_lines):
    """
    發送 Telegram 訊息 (通用函式)
    """
    try:
        if not TELEGRAM_BOT_TOKEN or "您的" in TELEGRAM_BOT_TOKEN:
            print("⚠️ Telegram Token 尚未設定，跳過發送。")
            return

        full_text = "".join(msg_lines)
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": full_text,
            "disable_web_page_preview": True 
        }
        
        response = requests.post(url, data=payload)
        if response.status_code == 200:
            print("✅ Telegram 訊息發送成功！")
        else:
            print(f"❌ 發送失敗: {response.text}")
            
    except Exception as e:
        print(f"❌ Telegram 連線錯誤: {e}")

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
    df_link['K線圖'] = df_link[col_name].apply(
        lambda x: f'https://tw.stock.yahoo.com/quote/{x}'
    )
    return df_link

def run_scanner():
    print("🔄 讀取台股清單...")
    codes = twstock.codes
    tickers = [c for c in codes.keys() if codes[c].type == '股票']
    
    print(f"📊 掃描標的: {len(tickers)} 檔")
    print("🎯 模式: 三大策略分開傳送 (各10檔)")
    print("-" * 40)
    
    list_trend = []    # A. 順勢
    list_reversal = [] # B. 逆勢
    list_wave = []     # C. 波段
    
    # tickers = tickers[:100] # 測試用 (若想跑全部請註解這行)
    
    for ticker in tqdm(tickers):
        try:
            df_raw = get_stock_data(ticker, period="1y")
            if df_raw is None: continue
            df = calculate_indicators(df_raw)
            if df is None: continue
            
            stock_info = codes[ticker]
            name = stock_info.name
            industry = stock_info.group if stock_info.group else "其他"
            last = df.iloc[-1]
            
            # A. 順勢
            is_trend, note_trend, pct_change = check_trend_strict(df)
            if is_trend:
                list_trend.append({
                    "產業族群": industry, "代號": ticker, "名稱": name,
                    "現價": round(last['Close'], 2), 
                    "漲跌幅": round(pct_change, 2),
                    "成交量": int(last['Volume']),
                    "RSI": round(last['RSI'], 1), 
                    "條件": note_trend
                })
            
            # B. 逆勢
            is_rev, note_rev, pct_change = check_reversal_strict(df)
            if is_rev:
                rng = last['High60'] - last['Low60']
                pos = (last['Close'] - last['Low60']) / rng * 100 if rng != 0 else 0
                list_reversal.append({
                    "產業族群": industry, "代號": ticker, "名稱": name,
                    "現價": round(last['Close'], 2), 
                    "漲跌幅": round(pct_change, 2),
                    "成交量": int(last['Volume']),
                    "位階%": round(pos, 1),
                    "條件": note_rev,
                    "K值": round(last['K'], 2)
                })

            # C. 波段蓄勢
            is_wave, note_wave, pct_change = check_wave_strict(df)
            if is_wave:
                list_wave.append({
                    "產業族群": industry, "代號": ticker, "名稱": name,
                    "現價": round(last['Close'], 2), 
                    "漲跌幅": round(pct_change, 2),
                    "成交量": int(last['Volume']),
                    "條件": note_wave
                })

        except KeyboardInterrupt: break
        except: continue
            
    # --- 產出 Excel ---
    if not os.path.exists('Reports'): os.makedirs('Reports')
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d_%H%M')
    filename = f"Reports/選股日報_{timestamp}.xlsx"
    today_str = datetime.datetime.now().strftime('%m/%d')
    
    with pd.ExcelWriter(filename) as writer:
        
        # 設定每個策略都取前 10 名
        LIMIT_N = 10
        
        # ==========================================
        # 📨 訊息 1: 順勢突破 (Trend)
        # ==========================================
        msg_trend = []
        msg_trend.append(f"📅 {today_str} 選股日報\n")
        
        if list_trend:
            df_t = sort_by_industry_heat(list_trend, secondary_sort_key='RSI', ascending=False)
            df_t_excel = add_url_column(df_t) 
            df_t_excel.to_excel(writer, sheet_name='順勢突破', index=False)
            
            count_t = len(list_trend)
            msg_trend.append(f"🚀 順勢噴出 (Top {min(count_t, LIMIT_N)}/{count_t})\n")
            msg_trend.append("----------------\n")
            
            top_trend = df_t.head(LIMIT_N)
            for _, row in top_trend.iterrows():
                ind_str = f"{row['產業族群']} {row['產業熱度']:.1f}%"
                line1 = f"🏭[{ind_str}] {row['代號']} {row['名稱']} (${row['現價']})\n"
                line2 = f"   └ 漲:{row['漲跌幅']}% | RSI:{row['RSI']}\n"
                msg_trend.append(line1 + line2)
        else:
            msg_trend.append("🚀 順勢噴出: 無\n")
            pd.DataFrame(["無"]).to_excel(writer, sheet_name='順勢突破')
            
        print("📨 發送第一則: 順勢股...")
        send_telegram_message(msg_trend)
        time.sleep(1) # 休息一下，避免訊息順序錯亂

        # ==========================================
        # 📨 訊息 2: 逆勢抄底 (Reversal)
        # ==========================================
        msg_rev = []
        
        if list_reversal:
            df_r = sort_by_industry_heat(list_reversal, secondary_sort_key='位階%', ascending=True)
            df_r_excel = add_url_column(df_r)
            df_r_excel.to_excel(writer, sheet_name='低檔爆量', index=False)
            
            count_r = len(list_reversal)
            msg_rev.append(f"↩️ 逆勢抄底 (Top {min(count_r, LIMIT_N)}/{count_r})\n")
            msg_rev.append("----------------\n")
            
            top_rev = df_r.head(LIMIT_N)
            for _, row in top_rev.iterrows():
                vol_k = int(row['成交量']/1000)
                ind_str = f"{row['產業族群']} {row['產業熱度']:.1f}%"
                line1 = f"🏭[{ind_str}] {row['代號']} {row['名稱']} (${row['現價']})\n"
                line2 = f"   └ 漲:{row['漲跌幅']}% | 位階:{row['位階%']}% | 量:{vol_k}張\n"
                msg_rev.append(line1 + line2)
        else:
            msg_rev.append("↩️ 逆勢抄底: 無\n")
            pd.DataFrame(["無"]).to_excel(writer, sheet_name='低檔爆量')

        print("📨 發送第二則: 逆勢股...")
        send_telegram_message(msg_rev)
        time.sleep(1)

        # ==========================================
        # 📨 訊息 3: 波段蓄勢 (Wave / VCP)
        # ==========================================
        msg_wave = []
        
        if list_wave:
            df_w = sort_by_industry_heat(list_wave, secondary_sort_key='漲跌幅', ascending=True)
            df_w_excel = add_url_column(df_w)
            df_w_excel.to_excel(writer, sheet_name='波段蓄勢', index=False)
            
            count_w = len(list_wave)
            msg_wave.append(f"🌊 波段蓄勢 (Top {min(count_w, LIMIT_N)}/{count_w})\n")
            msg_wave.append("----------------\n")
            
            top_wave = df_w.head(LIMIT_N)
            for _, row in top_wave.iterrows():
                ind_str = f"{row['產業族群']} {row['產業熱度']:.1f}%"
                line1 = f"🏭[{ind_str}] {row['代號']} {row['名稱']} (${row['現價']})\n"
                line2 = f"   └ 漲:{row['漲跌幅']}% | {row['條件']}\n"
                msg_wave.append(line1 + line2)
        else:
            msg_wave.append("🌊 波段蓄勢: 無 (無符合VCP型態)\n")
            pd.DataFrame(["無"]).to_excel(writer, sheet_name='波段蓄勢')
            
        # 結尾連結
        msg_wave.append("================\n")
        msg_wave.append("🔗 點此查詢: https://tw.stock.yahoo.com/")

        print("📨 發送第三則: 波段股...")
        send_telegram_message(msg_wave)
    
    print(f"\n📊 掃描完成！Excel 已儲存: {filename}")

if __name__ == "__main__":
    run_scanner()