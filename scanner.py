import twstock
import pandas as pd
from tqdm import tqdm
import datetime
import os
import sys
import time
import requests 

try:
    from logic import get_stock_data, calculate_indicators, check_trend_strict, check_reversal_strict, check_wave_strict
except ImportError:
    print("❌ 找不到 logic.py，請確認它在同一個資料夾內。")
    sys.exit()

# 已填入您的真實 Token 和頻道 ID
TELEGRAM_BOT_TOKEN = "8586311109:AAG-JPDnQpVee_VAp3bwPOThbpEcLBu7uvA" 
TELEGRAM_CHAT_ID = "@yumirobt213"          

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

def run_scanner():
    print("🔄 讀取台股清單...")
    codes = twstock.codes
    tickers = [c for c in codes.keys() if codes[c].type == '股票']
    
    print(f"📊 掃描標的: {len(tickers)} 檔")
    print("🎯 模式: 高防禦+張數對齊版")
    print("-" * 40)
    
    list_trend, list_reversal, list_wave = [], [], []
    
    for ticker in tqdm(tickers):
        try:
            df_raw = get_stock_data(ticker, period="2y")
            if df_raw is None: continue
            df = calculate_indicators(df_raw)
            if df is None: continue
            
            stock_info = codes[ticker]
            name, industry = stock_info.name, stock_info.group if stock_info.group else "其他"
            last = df.iloc[-1]
            
            # A. 順勢
            is_trend, note_trend, pct_change, sl_t = check_trend_strict(df)
            if is_trend:
                list_trend.append({
                    "產業族群": industry, "代號": ticker, "名稱": name,
                    "現價": round(last['Close'], 2), "防守價": round(sl_t, 2),
                    "漲跌幅": round(pct_change, 2), "成交量(張)": int(last['Volume'] / 1000),
                    "RSI": round(last['RSI'], 1), "條件": note_trend
                })
            
            # B. 逆勢
            is_rev, note_rev, pct_change, sl_r = check_reversal_strict(df)
            if is_rev:
                list_reversal.append({
                    "產業族群": industry, "代號": ticker, "名稱": name,
                    "現價": round(last['Close'], 2), "防守價": round(sl_r, 2),
                    "漲跌幅": round(pct_change, 2), "成交量(張)": int(last['Volume'] / 1000),
                    "條件": note_rev
                })

            # C. 波段蓄勢
            is_wave, note_wave, pct_change, sl_w = check_wave_strict(df)
            if is_wave:
                list_wave.append({
                    "產業族群": industry, "代號": ticker, "名稱": name,
                    "現價": round(last['Close'], 2), "防守價": round(sl_w, 2),
                    "漲跌幅": round(pct_change, 2), "成交量(張)": int(last['Volume'] / 1000),
                    "條件": note_wave
                })

        except KeyboardInterrupt: break
        except: continue
            
    if not os.path.exists('Reports'): os.makedirs('Reports')
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d_%H%M')
    filename = f"Reports/選股日報_{timestamp}.xlsx"
    today_str = datetime.datetime.now().strftime('%m/%d')
    
    with pd.ExcelWriter(filename) as writer:
        LIMIT_N = 10
        
        msg_trend = [f"📅 {today_str} 選股日報 (高防禦版)\n"]
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
    
    print(f"\n📊 掃描完成！Excel 已儲存: {filename}")

if __name__ == "__main__":
    run_scanner()
    