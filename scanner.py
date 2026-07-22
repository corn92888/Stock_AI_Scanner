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
    from database import record_scan_results
except ImportError:
    print("❌ 找不到必要模組，請確認 logic.py 與 database.py 在同一個資料夾內。")
    sys.exit()

from dotenv import load_dotenv
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "") 
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")    
TAIPEI_TZ = datetime.timezone(datetime.timedelta(hours=8), name="Asia/Taipei")

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

def _parse_realtime_price(rt):
    raw_price = rt.get('latest_trade_price', '-')
    if raw_price and raw_price != '-':
        try: return float(raw_price)
        except: pass
    bid_prices = rt.get('best_bid_price', [])
    if bid_prices and bid_prices[0] and bid_prices[0] != '-':
        try: return float(bid_prices[0])
        except: pass
    return 0.0

def _safe_float(val, default=0.0):
    if val and val != '-':
        try: return float(val)
        except: pass
    return default

def fetch_realtime_prices(ticker_list, chunk_size=20):
    result = {}
    for i in tqdm(range(0, len(ticker_list), chunk_size), desc="📡 補齊最新報價"):
        chunk = ticker_list[i:i+chunk_size]
        try:
            data = twstock.realtime.get(chunk)
            if not data: continue
            if len(chunk) == 1:
                if data.get('success') and 'realtime' in data:
                    rt = data['realtime']
                    price = _parse_realtime_price(rt)
                    if price > 0:
                        result[chunk[0]] = {
                            'Open': _safe_float(rt.get('open')),
                            'High': _safe_float(rt.get('high')),
                            'Low': _safe_float(rt.get('low')),
                            'Close': price,
                            'Volume': _safe_float(rt.get('accumulate_trade_volume'))
                        }
            else:
                for code, info in data.items():
                    if code == 'success': continue
                    if isinstance(info, dict) and info.get('success') and 'realtime' in info:
                        rt = info['realtime']
                        price = _parse_realtime_price(rt)
                        if price > 0:
                            result[code] = {
                                'Open': _safe_float(rt.get('open')),
                                'High': _safe_float(rt.get('high')),
                                'Low': _safe_float(rt.get('low')),
                                'Close': price,
                                'Volume': _safe_float(rt.get('accumulate_trade_volume'))
                            }
            time.sleep(0.3)
        except: pass
    return result

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
    
    # 檢查是否有資料延遲 (並使用 twstock.realtime 強制補齊今日最新一筆 K 棒)
    if all_stock_data:
        sample_df = next(iter(all_stock_data.values()))
        latest_date = sample_df.index[-1]
        print(f"📆 yfinance 最新日期: {latest_date.strftime('%Y-%m-%d')} ({latest_date.strftime('%A')})")
        
        taipei_now = datetime.datetime.now(TAIPEI_TZ)
        today = taipei_now.date()
        today_ts = pd.Timestamp(today)
        
        # 當 yfinance 資料落後且今天是平日，啟動強制回補機制
        if latest_date.date() < today and today.weekday() < 5:
            print(f"⚠️ 偵測到 yfinance 報價延遲，啟動 twstock.realtime 強制回補今日收盤價...")
            rt_prices = fetch_realtime_prices(tickers, chunk_size=20)
            
            appended = 0
            if rt_prices:
                for yf_ticker in list(all_stock_data.keys()):
                    code = yf_to_code.get(yf_ticker)
                    if code and code in rt_prices:
                        rt = rt_prices[code]
                        if rt['Close'] > 0:
                            # 補齊今日 K 棒 (twstock 成交量張數轉為 yf 習慣的股數)
                            new_bar = pd.DataFrame({
                                'Open': [rt['Open']],
                                'High': [rt['High'] if rt['High'] > 0 else rt['Close']],
                                'Low': [rt['Low'] if rt['Low'] > 0 else rt['Close']],
                                'Close': [rt['Close']],
                                'Volume': [rt['Volume'] * 1000]
                            }, index=[today_ts])
                            
                            common_cols = [c for c in new_bar.columns if c in all_stock_data[yf_ticker].columns]
                            all_stock_data[yf_ticker] = pd.concat([
                                all_stock_data[yf_ticker][common_cols], 
                                new_bar[common_cols]
                            ])
                            appended += 1
                if appended > 0:
                    print(f"✅ 成功補齊 {appended} 檔今日最新 K 線！現在分析將基於今日真實價格💯。")
                else:
                    print("⚠️ 即時報價補齊失敗，將沿用舊有資料進行分析。")
        elif latest_date.date() < today and today.weekday() >= 5:
            print("ℹ️ 今天是假日，yfinance 資料停留在上一個交易日 (正常)。")
            
    print(f"📊 即將開始分析: {len(all_stock_data)} 檔")
    
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
    now = datetime.datetime.now(TAIPEI_TZ)
    timestamp = now.strftime('%Y-%m-%d_%H%M')
    filename = f"Reports/選股日報_{timestamp}.xlsx"
    today_str = now.strftime('%m/%d')
    trade_date = now.date().isoformat()
    strategy_frames = {}
    
    with pd.ExcelWriter(filename) as writer:
        LIMIT_N = 10
        wrote_sheet = False
        
        msg_trend = [f"📅 {today_str} 玉米帶你盤後回顧\n"]
        if list_trend:
            df_t = sort_by_industry_heat(list_trend, secondary_sort_key='RSI', ascending=False)
            strategy_frames["trend"] = df_t
            add_url_column(df_t).to_excel(writer, sheet_name='順勢突破', index=False)
            wrote_sheet = True
            msg_trend.extend([f"🚀 順勢噴出 (Top {min(len(list_trend), LIMIT_N)})\n", "----------------\n"])
            for _, row in df_t.head(LIMIT_N).iterrows():
                msg_trend.append(f"🏭[{row['產業族群']}] {row['代號']} {row['名稱']} (${row['現價']})\n   └ 漲:{row['漲跌幅']}% | 防守:${row['防守價']} | 量:{row['成交量(張)']}張\n")
        else:
            msg_trend.append("🚀 順勢噴出: 無\n")
            
        send_telegram_message(msg_trend)

        msg_rev = []
        if list_reversal:
            df_r = sort_by_industry_heat(list_reversal, secondary_sort_key='漲跌幅', ascending=True)
            strategy_frames["reversal"] = df_r
            add_url_column(df_r).to_excel(writer, sheet_name='低檔爆量', index=False)
            wrote_sheet = True
            msg_rev.extend([f"↩️ 逆勢抄底 (Top {min(len(list_reversal), LIMIT_N)})\n", "----------------\n"])
            for _, row in df_r.head(LIMIT_N).iterrows():
                msg_rev.append(f"🏭[{row['產業族群']}] {row['代號']} {row['名稱']} (${row['現價']})\n   └ 漲:{row['漲跌幅']}% | 防守:${row['防守價']} | 量:{row['成交量(張)']}張\n")
        else:
            msg_rev.append("↩️ 逆勢抄底: 無\n")

        send_telegram_message(msg_rev)

        msg_wave = []
        if list_wave:
            df_w = sort_by_industry_heat(list_wave, secondary_sort_key='漲跌幅', ascending=True)
            strategy_frames["wave"] = df_w
            add_url_column(df_w).to_excel(writer, sheet_name='波段蓄勢', index=False)
            wrote_sheet = True
            msg_wave.extend([f"🌊 波段蓄勢 (Top {min(len(list_wave), LIMIT_N)})\n", "----------------\n"])
            for _, row in df_w.head(LIMIT_N).iterrows():
                msg_wave.append(f"🏭[{row['產業族群']}] {row['代號']} {row['名稱']} (${row['現價']})\n   └ 漲:{row['漲跌幅']}% | 防守:${row['防守價']} | 量:{row['成交量(張)']}張\n")
        else:
            msg_wave.append("🌊 波段蓄勢: 無\n")
            
        msg_wave.extend(["================\n", "🔗 點此查詢: https://tw.stock.yahoo.com/"])
        send_telegram_message(msg_wave)

        if not wrote_sheet:
            pd.DataFrame([{"狀態": "本次盤後掃描無符合策略條件的標的"}]).to_excel(writer, sheet_name='無符合標的', index=False)

    try:
        db_result = record_scan_results(
            mode="eod",
            trade_date=trade_date,
            strategy_frames=strategy_frames,
            report_path=filename,
        )
        print(f"🗃️  已寫入訊號資料庫: {db_result['db_path']} ({db_result['signals']} 筆訊號)")
    except Exception as e:
        raise RuntimeError(f"訊號資料庫寫入失敗: {e}") from e

    try:
        from eod_research import save_eod_research_candidates

        research = save_eod_research_candidates(
            db_result["run_id"],
            strategy_frames,
            all_stock_data,
            yf_to_code,
            codes,
            captured_at=now,
        )
        print(
            f"🧪 盤後研究候選已建立: {research['saved']} 筆，"
            f"正式模擬入選 {research['selected']} 筆"
        )
    except Exception as e:
        raise RuntimeError(f"盤後研究候選建立失敗: {e}") from e

    alpha_result = {
        "status": "unavailable",
        "reason": "model_artifact_missing",
        "selected": [],
    }
    alpha_model_path = "data/models/alpha_strategy_v2_model.joblib"
    if os.path.exists(alpha_model_path):
        try:
            from alpha_live import run_alpha_live_scoring

            benchmark_data = batch_download(["^TWII"], period="2y", chunk_size=1)
            benchmark = benchmark_data.get("^TWII")
            alpha_result = run_alpha_live_scoring(
                all_stock_data,
                yf_to_code,
                codes,
                benchmark,
                model_path=alpha_model_path,
                db_path="data/stock_scanner.db",
                trade_date=trade_date,
            )
            alpha_message = [
                f"Alpha v2 模擬訊號｜{trade_date}\n",
                "PAPER ONLY｜隔日開盤模擬成交｜持有至 T+10\n",
                (
                    f"信心 {alpha_result.get('confidence') or 0:.3f} / "
                    f"門檻 {alpha_result.get('confidence_threshold') or 0:.3f}\n"
                ),
            ]
            if alpha_result["selected"]:
                for row in alpha_result["selected"]:
                    alpha_message.append(
                        f"#{row['rank_order']} {row['code']} {row.get('name') or ''} "
                        f"${float(row['signal_price']):.2f}｜"
                        f"預測超額 {float(row['predicted_alpha']):.2f}%\n"
                    )
            else:
                alpha_message.append("本日信心不足，模擬資金維持現金。\n")
            send_telegram_message(alpha_message)
            print(
                f"Alpha v2 盤後訊號: {alpha_result['status']}，"
                f"入選 {len(alpha_result['selected'])} 檔"
            )
        except Exception as e:
            raise RuntimeError(f"Alpha v2 全市場評分失敗: {e}") from e
    else:
        print("Alpha v2 模型檔尚未發布，本次只保留舊策略研究輸出。")
    
    elapsed = time.time() - start_time
    minutes, seconds = divmod(int(elapsed), 60)
    print(f"\n📊 掃描完成！Excel 已儲存: {filename}")
    print(f"⏱️  總耗時: {minutes} 分 {seconds} 秒")
    return {
        "run_id": db_result["run_id"],
        "signal_count": db_result["signals"],
        "candidate_count": research["saved"],
        "selected_count": research["selected"],
        "alpha_status": alpha_result["status"],
        "alpha_selected_count": len(alpha_result["selected"]),
        "report_path": filename,
    }

if __name__ == "__main__":
    run_scanner()
