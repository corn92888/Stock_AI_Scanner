import twstock
import yfinance as yf
import pandas as pd
from tqdm import tqdm
import datetime
import json
import os
import sys
import time
import requests 
import argparse
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed

try:
    from logic import calculate_indicators, check_trend_strict, check_reversal_strict, check_wave_strict
    from database import DB_PATH, record_scan_results
    from research_monitor import build_research_health
except ImportError:
    print("❌ 找不到必要模組，請確認 logic.py 與 database.py 在同一個資料夾內。")
    sys.exit()

from dotenv import load_dotenv
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "") 
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")    
TAIPEI_TZ = datetime.timezone(datetime.timedelta(hours=8), name="Asia/Taipei")
TWSTOCK_TIMEOUT_SECONDS = 8
MIN_REALTIME_COVERAGE = 0.65
DEFAULT_REALTIME_WORKERS = 6
DEFAULT_QUOTE_MAX_ATTEMPTS = 6
DEFAULT_QUOTE_RETRY_DELAY_SECONDS = 60
DEFAULT_COVERAGE_MIN_TURNOVER_TWD = 50_000_000
DEFAULT_COVERAGE_MIN_VOLUME_SHARES = 200_000
DEFAULT_COVERAGE_MIN_SYMBOLS = 300
DEFAULT_VOLUME_PROJECTION_MINUTES = 15
COVERAGE_POLICY_VERSION = "previous_session_liquid_turnover_v1"


class RealtimeCoverageError(RuntimeError):
    """Raised when current-session quotes remain too sparse after retries."""

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

def batch_download(ticker_list, period="1y", chunk_size=200):
    all_data = {}
    for i in tqdm(range(0, len(ticker_list), chunk_size), desc=f"📥 下載歷史基準({period})"):
        chunk = ticker_list[i:i+chunk_size]
        try:
            raw = yf.download(chunk, period=period, progress=False, auto_adjust=False, threads=True)
            if raw.empty: continue
            
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
                    # 確保不包含今天的歷史 K 線 (避免跟盤中重複)
                    df = df[df.index < pd.Timestamp.now().normalize()]
                    all_data[t] = df
            else:
                for t in chunk:
                    try:
                        if isinstance(raw.columns, pd.MultiIndex):
                            ticker_cols = raw.columns.get_level_values(1)
                            if t not in ticker_cols.unique(): continue
                            df = raw.loc[:, raw.columns.get_level_values(1) == t].copy()
                            df.columns = df.columns.get_level_values(0)
                        else:
                            df = raw.copy()
                        
                        df = df.loc[:, ~df.columns.duplicated()]
                        for c in ['Open', 'High', 'Low', 'Close', 'Volume']:
                            if c in df.columns: df[c] = pd.to_numeric(df[c], errors='coerce')
                        df.dropna(subset=['Close'], inplace=True)
                        if not df.empty:
                            df = df[df.index < pd.Timestamp.now().normalize()]
                            all_data[t] = df
                    except: pass
        except: pass
    return all_data

def _extract_yf_frame(raw, ticker):
    if raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        ticker_level = raw.columns.get_level_values(1)
        if ticker in ticker_level.unique():
            df = raw.loc[:, ticker_level == ticker].copy()
            df.columns = df.columns.get_level_values(0)
        else:
            df = raw.copy()
            df.columns = [col[0] for col in df.columns]
    else:
        df = raw.copy()

    df = df.loc[:, ~df.columns.duplicated()]
    for c in ['Open', 'High', 'Low', 'Close', 'Volume']:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')
    df.dropna(subset=['Close'], inplace=True)
    return df

def fetch_yfinance_current_bars(yf_tickers, yf_to_code, chunk_size=200, now=None):
    """Aggregate today's one-minute bars when exchange quotes are unavailable."""
    today = (now or datetime.datetime.now(TAIPEI_TZ)).date()
    result = {}
    for i in tqdm(range(0, len(yf_tickers), chunk_size), desc="📥 yfinance分鐘備援"):
        chunk = yf_tickers[i:i+chunk_size]
        try:
            raw = yf.download(
                chunk,
                period="1d",
                interval="1m",
                progress=False,
                auto_adjust=False,
                threads=True,
            )
            if raw.empty:
                continue
            for ticker in chunk:
                try:
                    df = _extract_yf_frame(raw, ticker)
                    if df.empty:
                        continue
                    index = pd.DatetimeIndex(df.index)
                    if index.tz is not None:
                        session_dates = index.tz_convert(TAIPEI_TZ).date
                    else:
                        session_dates = index.date
                    session = df[session_dates == today]
                    if session.empty:
                        continue
                    close = pd.to_numeric(session.get('Close'), errors='coerce').dropna()
                    if close.empty:
                        continue
                    open_values = pd.to_numeric(session.get('Open'), errors='coerce').dropna()
                    high_values = pd.to_numeric(session.get('High'), errors='coerce').dropna()
                    low_values = pd.to_numeric(session.get('Low'), errors='coerce').dropna()
                    volume = pd.to_numeric(
                        session.get('Volume'), errors='coerce'
                    ).fillna(0)
                    price = float(close.iloc[-1])
                    code = yf_to_code.get(ticker)
                    if not code or price <= 0:
                        continue
                    result[code] = {
                        'Open': float(open_values.iloc[0]) if not open_values.empty else price,
                        'High': float(high_values.max()) if not high_values.empty else price,
                        'Low': float(low_values.min()) if not low_values.empty else price,
                        'Close': price,
                        'Volume': float(volume.sum()) / 1000,
                    }
                except Exception:
                    continue
        except Exception:
            continue
    return result

def is_market_open(now=None):
    now = now or datetime.datetime.now(TAIPEI_TZ)
    if now.weekday() >= 5: return False
    return datetime.time(9, 0) <= now.time() <= datetime.time(13, 30)


def is_intraday_scan_window(now=None, automation_slot=None):
    now = now or datetime.datetime.now(TAIPEI_TZ)
    if is_market_open(now):
        return True
    automation_slot = automation_slot or os.getenv("INTRADAY_AUTOMATION_SLOT", "")
    return (
        now.weekday() < 5
        and automation_slot == "13:30"
        and datetime.time(13, 30) < now.time() <= datetime.time(14, 10)
    )

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

def get_projected_volume(
    current_volume_lots,
    now=None,
    min_elapsed_minutes=None,
):
    """根據開盤時間推算今天收盤時的預估量 (張)"""
    now = now or datetime.datetime.now(TAIPEI_TZ)
    market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
    elapsed_seconds = (now - market_open).total_seconds()
    total_seconds = 4.5 * 3600

    if elapsed_seconds <= 0:
        return 0
    if elapsed_seconds >= total_seconds:
        return current_volume_lots
    configured_floor = (
        min_elapsed_minutes
        if min_elapsed_minutes is not None
        else os.getenv(
            "INTRADAY_VOLUME_PROJECTION_MINUTES",
            DEFAULT_VOLUME_PROJECTION_MINUTES,
        )
    )
    floor_seconds = max(1, int(configured_floor)) * 60
    effective_elapsed = max(elapsed_seconds, floor_seconds)
    return current_volume_lots * (total_seconds / effective_elapsed)


def build_liquid_coverage_universe(
    history_data,
    *,
    min_turnover_twd=None,
    min_volume_shares=None,
    min_symbols=None,
):
    """Build a previous-session liquidity universe for quote quality checks."""
    turnover_floor = float(
        min_turnover_twd
        if min_turnover_twd is not None
        else os.getenv(
            "INTRADAY_COVERAGE_MIN_TURNOVER_TWD",
            DEFAULT_COVERAGE_MIN_TURNOVER_TWD,
        )
    )
    volume_floor = float(
        min_volume_shares
        if min_volume_shares is not None
        else os.getenv(
            "INTRADAY_COVERAGE_MIN_VOLUME_SHARES",
            DEFAULT_COVERAGE_MIN_VOLUME_SHARES,
        )
    )
    minimum_count = max(
        1,
        int(
            min_symbols
            if min_symbols is not None
            else os.getenv(
                "INTRADAY_COVERAGE_MIN_SYMBOLS",
                DEFAULT_COVERAGE_MIN_SYMBOLS,
            )
        ),
    )
    ranked = []
    selected = []
    for ticker, frame in history_data.items():
        if frame is None or frame.empty or "Close" not in frame or "Volume" not in frame:
            continue
        recent = pd.DataFrame(
            {
                "Close": pd.to_numeric(frame["Close"], errors="coerce"),
                "Volume": pd.to_numeric(frame["Volume"], errors="coerce"),
            }
        ).dropna()
        recent = recent[(recent["Close"] > 0) & (recent["Volume"] >= 0)].tail(20)
        if len(recent) < 10:
            continue
        median_volume = float(recent["Volume"].median())
        median_turnover = float((recent["Close"] * recent["Volume"]).median())
        ranked.append((ticker, median_turnover, median_volume))
        if median_turnover >= turnover_floor and median_volume >= volume_floor:
            selected.append(ticker)

    if len(selected) < minimum_count:
        ranked.sort(key=lambda row: (row[1], row[2], row[0]), reverse=True)
        selected = [row[0] for row in ranked[:minimum_count]]
    return selected


def append_realtime_bars(history_data, realtime, yf_to_code, now=None):
    """Return only symbols that received a valid current-session bar."""
    captured_at = now or datetime.datetime.now(TAIPEI_TZ)
    today_ts = pd.Timestamp(captured_at.date())
    fresh_data = {}
    fresh_codes = set()
    for yf_ticker, history in history_data.items():
        code = yf_to_code.get(yf_ticker)
        rt = realtime.get(code) if code else None
        if not rt or _safe_float(rt.get("Close")) <= 0:
            continue
        price = _safe_float(rt.get("Close"))
        projected_lots = get_projected_volume(
            _safe_float(rt.get("Volume")),
            now=captured_at,
        )
        new_bar = pd.DataFrame(
            {
                "Open": [_safe_float(rt.get("Open"), price)],
                "High": [_safe_float(rt.get("High"), price) or price],
                "Low": [_safe_float(rt.get("Low"), price) or price],
                "Close": [price],
                "Volume": [projected_lots * 1000],
            },
            index=[today_ts],
        )
        common_cols = [column for column in new_bar.columns if column in history.columns]
        if not common_cols:
            continue
        fresh_data[yf_ticker] = pd.concat(
            [history[common_cols], new_bar[common_cols]]
        )
        fresh_codes.add(code)
    return fresh_data, fresh_codes

def twstock_realtime_get_with_timeout(chunk, timeout=TWSTOCK_TIMEOUT_SECONDS):
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(twstock.realtime.get, chunk)
    try:
        return future.result(timeout=timeout)
    except TimeoutError:
        first = chunk[0] if chunk else ""
        last = chunk[-1] if chunk else ""
        print(f"⚠️ TWSE 即時報價逾時，跳過批次 {first}-{last}")
        return None
    except Exception:
        return None
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

def _parse_realtime_batch(data, chunk):
    result = {}
    if not data:
        return result
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
            if code == 'success':
                continue
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
    return result


def fetch_realtime_prices(ticker_list, chunk_size=20, max_workers=None):
    chunks = [ticker_list[i:i + chunk_size] for i in range(0, len(ticker_list), chunk_size)]
    if not chunks:
        return {}

    configured_workers = max_workers or int(
        os.getenv("INTRADAY_REALTIME_WORKERS", DEFAULT_REALTIME_WORKERS)
    )
    worker_count = max(1, min(configured_workers, len(chunks), 12))
    result = {}
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(twstock_realtime_get_with_timeout, chunk): chunk
            for chunk in chunks
        }
        for future in tqdm(
            as_completed(futures),
            total=len(futures),
            desc=f"📡 盤中即時報價({worker_count}併發)",
        ):
            chunk = futures[future]
            try:
                result.update(_parse_realtime_batch(future.result(), chunk))
            except Exception:
                continue
    return result


def collect_realtime_prices(
    history_tickers,
    yf_to_code,
    coverage_tickers=None,
    max_attempts=None,
    retry_delay_seconds=None,
    sleep_fn=time.sleep,
):
    """Collect current bars, retrying only symbols that are still missing."""
    max_attempts = max(
        1,
        int(
            max_attempts
            if max_attempts is not None
            else os.getenv(
                "INTRADAY_QUOTE_MAX_ATTEMPTS",
                DEFAULT_QUOTE_MAX_ATTEMPTS,
            )
        ),
    )
    retry_delay_seconds = max(
        0,
        int(
            retry_delay_seconds
            if retry_delay_seconds is not None
            else os.getenv(
                "INTRADAY_QUOTE_RETRY_DELAY_SECONDS",
                DEFAULT_QUOTE_RETRY_DELAY_SECONDS,
            )
        ),
    )
    eligible_tickers = [ticker for ticker in history_tickers if yf_to_code.get(ticker)]
    eligible_codes = [yf_to_code[ticker] for ticker in eligible_tickers]
    requested_coverage_tickers = coverage_tickers or eligible_tickers
    coverage_codes = {
        yf_to_code[ticker]
        for ticker in requested_coverage_tickers
        if yf_to_code.get(ticker)
    }
    if not coverage_codes:
        coverage_codes = set(eligible_codes)
    realtime = {}
    coverage = 0.0

    for attempt in range(1, max_attempts + 1):
        missing_codes = [code for code in eligible_codes if code not in realtime]
        if missing_codes:
            realtime.update(fetch_realtime_prices(missing_codes, chunk_size=20))

        covered = sum(
            1
            for code in coverage_codes
            if code in realtime and _safe_float(realtime[code].get("Close")) > 0
        )
        coverage = covered / len(coverage_codes) if coverage_codes else 0.0
        if coverage >= MIN_REALTIME_COVERAGE:
            print(
                f"📡 當日報價嘗試 {attempt}/{max_attempts}: 品質母體 "
                f"{covered}/{len(coverage_codes)} 檔 ({coverage:.1%})"
            )
            return realtime, coverage, attempt

        missing_yf_tickers = [
            ticker
            for ticker in eligible_tickers
            if yf_to_code[ticker] not in realtime
        ]
        should_use_yfinance = attempt in {1, max_attempts}
        if missing_yf_tickers and should_use_yfinance:
            if realtime:
                print(
                    f"⚠️ 第 {attempt} 次即時報價仍缺 {len(missing_yf_tickers)} 檔，"
                    "改用 yfinance 分鐘資料補足"
                )
            else:
                print("⚠️ TWSE 即時報價無法使用，改用 yfinance 分鐘資料備援")
            fallback = fetch_yfinance_current_bars(
                missing_yf_tickers,
                yf_to_code,
                chunk_size=200,
            )
            realtime.update(fallback)
            if fallback:
                print(f"✅ yfinance 分鐘資料補足 {len(fallback)} 檔")
        elif missing_yf_tickers:
            print(
                f"ℹ️ 第 {attempt} 次僅重試交易所報價；"
                "yfinance 留到最後一次再驗證。"
            )

        covered = sum(
            1
            for code in coverage_codes
            if code in realtime and _safe_float(realtime[code].get("Close")) > 0
        )
        coverage = covered / len(coverage_codes) if coverage_codes else 0.0
        print(
            f"📡 當日報價嘗試 {attempt}/{max_attempts}: 品質母體 "
            f"{covered}/{len(coverage_codes)} 檔 ({coverage:.1%})"
        )
        if coverage >= MIN_REALTIME_COVERAGE:
            return realtime, coverage, attempt

        if attempt < max_attempts:
            print(
                f"⏳ 覆蓋率低於 {MIN_REALTIME_COVERAGE:.0%}，"
                f"{retry_delay_seconds} 秒後只重抓缺漏股票。"
            )
            sleep_fn(retry_delay_seconds)

    raise RealtimeCoverageError(
        f"當日報價覆蓋率僅 {coverage:.1%}，低於 {MIN_REALTIME_COVERAGE:.0%}，"
        f"已重試 {max_attempts} 次，拒絕產生可能失真的盤中報表。"
    )

def run_intraday_scanner(send_telegram=True, now=None):
    start_time = time.time()
    started_at = now or datetime.datetime.now(TAIPEI_TZ)
    automation_slot = os.getenv("INTRADAY_AUTOMATION_SLOT", "")
    scan_window_open = is_intraday_scan_window(started_at, automation_slot)
    print("⚡️ 啟動: 盤中即時策略全掃描 (A/B/C 三合一)")
    print("-" * 40)

    if not scan_window_open:
        reason = "目前非交易時段，盤中掃描安全略過。"
        print(f"\nℹ️ {reason}")
        return {
            "status": "skipped",
            "reason": "outside_market_hours",
            "message": reason,
            "report_path": "",
        }
    
    codes = twstock.codes
    tickers = [c for c in codes.keys() if codes[c].type == '股票']
    yf_to_code = {f"{t}.{'TWO' if codes[t].market == '上櫃' else 'TW'}": t for t in tickers}
    all_yf_tickers = list(yf_to_code.keys())
    
    # 1. 批次下載歷史 K 線 (扣除今日)
    history_started = time.perf_counter()
    all_stock_data = batch_download(all_yf_tickers, period="1y", chunk_size=200)
    history_seconds = time.perf_counter() - history_started
    print(f"📊 歷史基準下載: {len(all_stock_data)} 檔")
    print(f"⏱️  歷史資料階段: {history_seconds:.1f} 秒")
    coverage_tickers = build_liquid_coverage_universe(all_stock_data)
    print(
        f"🧪 報價品質母體: {len(coverage_tickers)} 檔 "
        f"({COVERAGE_POLICY_VERSION})"
    )
    
    # 2. 抓取即時報價並推算預估量
    is_realtime_mode = False
    if scan_window_open:
        print("\n📡 台股盤中！正在抓取即時報價並結合歷史資料...")
        realtime_started = time.perf_counter()
        rt_prices, coverage, quote_attempts = collect_realtime_prices(
            list(all_stock_data.keys()),
            yf_to_code,
            coverage_tickers=coverage_tickers,
        )
        realtime_seconds = time.perf_counter() - realtime_started
        quote_captured_at = datetime.datetime.now(TAIPEI_TZ)
        print(f"⏱️  即時報價階段: {realtime_seconds:.1f} 秒")
        
        if rt_prices:
            full_universe_size = len(all_stock_data)
            fresh_stock_data, fresh_codes = append_realtime_bars(
                all_stock_data,
                rt_prices,
                yf_to_code,
                now=quote_captured_at,
            )
            coverage_codes = {
                yf_to_code[ticker]
                for ticker in coverage_tickers
                if yf_to_code.get(ticker)
            }
            appended = len(fresh_stock_data)
            appended_coverage = (
                len(fresh_codes & coverage_codes) / len(coverage_codes)
                if coverage_codes
                else 0.0
            )
            all_market_coverage = (
                appended / full_universe_size if full_universe_size else 0.0
            )
            print(
                f"✅ 已為 {appended} 檔追加盤中即時 K 棒 "
                f"(品質母體 {appended_coverage:.1%} / "
                f"全市場 {all_market_coverage:.1%})"
            )
            if appended_coverage < MIN_REALTIME_COVERAGE:
                raise RealtimeCoverageError(
                    f"流動性品質母體的當日報價覆蓋率僅 {appended_coverage:.1%}，"
                    f"低於 {MIN_REALTIME_COVERAGE:.0%}，拒絕產生可能失真的盤中報表。"
                )
            all_stock_data = fresh_stock_data
            coverage = appended_coverage
            is_realtime_mode = True
        else:
            raise RuntimeError("無法取得任何當日報價，拒絕用昨日資料冒充盤中掃描。")
    else:
        print("\nℹ️ 目前非交易時段，若要確認當日收盤表現，請使用 scanner.py")
        sys.exit(0)
    
    # 3. 丟入 logic.py 多線程測略分析
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
                    "漲跌幅": round(pct_change, 2), "成交(張)(含預估)": int(last['Volume'] / 1000),
                    "RSI": round(last['RSI'], 1), "條件": note_trend
                }
            
            is_rev, note_rev, pct_change, sl_r = check_reversal_strict(df)
            if is_rev:
                res_r = {
                    "產業族群": industry, "代號": code, "名稱": name,
                    "現價": round(last['Close'], 2), "防守價": round(sl_r, 2),
                    "漲跌幅": round(pct_change, 2), "成交(張)(含預估)": int(last['Volume'] / 1000),
                    "條件": note_rev
                }

            is_wave, note_wave, pct_change, sl_w = check_wave_strict(df)
            if is_wave:
                res_w = {
                    "產業族群": industry, "代號": code, "名稱": name,
                    "現價": round(last['Close'], 2), "防守價": round(sl_w, 2),
                    "漲跌幅": round(pct_change, 2), "成交(張)(含預估)": int(last['Volume'] / 1000),
                    "條件": note_wave
                }
            return (res_t, res_r, res_w)
        except:
            return None
    
    analysis_started = time.perf_counter()
    cpu_workers = min(os.cpu_count() or 4, 8)
    with ThreadPoolExecutor(max_workers=cpu_workers) as executor:
        futures = {executor.submit(process_stock, yt, df): yt for yt, df in all_stock_data.items()}
        for future in tqdm(as_completed(futures), total=len(futures), desc="🧠 分配極速運算中"):
            res = future.result()
            if res:
                if res[0]: list_trend.append(res[0])
                if res[1]: list_reversal.append(res[1])
                if res[2]: list_wave.append(res[2])
    analysis_seconds = time.perf_counter() - analysis_started
    print(f"⏱️  指標與策略階段: {analysis_seconds:.1f} 秒")
            
    if not os.path.exists('Reports'): os.makedirs('Reports')
    now = datetime.datetime.now(TAIPEI_TZ)
    timestamp = now.strftime('%Y-%m-%d_%H%M')
    filename = f"Reports/盤中日報_{timestamp}.xlsx"
    today_str = now.strftime('%m/%d')
    trade_date = now.date().isoformat()
    strategy_frames = {}
    try:
        integrity_gate = build_research_health(DB_PATH).get("integrity_gate", {})
        research_only = not bool(
            integrity_gate.get("formal_recommendations_allowed")
        )
        gate_notice = (
            "⚠️ 研究完整性閘門未通過；以下三策略為研究訊號，不是買進建議。\n\n"
            if research_only
            else "✅ 研究完整性閘門已核准；仍須自行核對即時價格與風險。\n\n"
        )
    except Exception:
        gate_notice = "⚠️ 無法驗證研究完整性閘門；以下僅能視為研究訊號。\n\n"
    
    with pd.ExcelWriter(filename) as writer:
        LIMIT_N = 10
        msg_trend = [
            f"📅 {today_str} 選股日報 (⚡️盤中三策略合一)\n",
            gate_notice,
        ]
        wrote_sheet = False
        
        if list_trend:
            df_t = sort_by_industry_heat(list_trend, secondary_sort_key='RSI', ascending=False)
            strategy_frames["trend"] = df_t
            add_url_column(df_t).to_excel(writer, sheet_name='順勢突破', index=False)
            wrote_sheet = True
            msg_trend.extend([f"🚀 玉米順勢噴出 (Top {min(len(list_trend), LIMIT_N)})\n", "----------------\n"])
            for _, row in df_t.head(LIMIT_N).iterrows():
                msg_trend.append(f"🏭[{row['產業族群']}] {row['代號']} {row['名稱']} (${row['現價']})\n   └ 漲:{row['漲跌幅']}% | 預估總量:{row['成交(張)(含預估)']}張\n")
        else:
            msg_trend.append("🚀 玉米順勢噴出: 無\n")
        if send_telegram:
            send_telegram_message(msg_trend)

        msg_rev = []
        if list_reversal:
            df_r = sort_by_industry_heat(list_reversal, secondary_sort_key='漲跌幅', ascending=True)
            strategy_frames["reversal"] = df_r
            add_url_column(df_r).to_excel(writer, sheet_name='低檔爆量', index=False)
            wrote_sheet = True
            msg_rev.extend([f"↩️ 逆勢抄底 (Top {min(len(list_reversal), LIMIT_N)})\n", "----------------\n"])
            for _, row in df_r.head(LIMIT_N).iterrows():
                msg_rev.append(f"🏭[{row['產業族群']}] {row['代號']} {row['名稱']} (${row['現價']})\n   └ 漲:{row['漲跌幅']}% | 預估總量:{row['成交(張)(含預估)']}張\n")
        else:
            msg_rev.append("↩️ 逆勢抄底: 無\n")
        if send_telegram:
            send_telegram_message(msg_rev)

        msg_wave = []
        if list_wave:
            df_w = sort_by_industry_heat(list_wave, secondary_sort_key='漲跌幅', ascending=True)
            strategy_frames["wave"] = df_w
            add_url_column(df_w).to_excel(writer, sheet_name='波段蓄勢', index=False)
            wrote_sheet = True
            msg_wave.extend([f"🌊 波段蓄勢 (Top {min(len(list_wave), LIMIT_N)})\n", "----------------\n"])
            for _, row in df_w.head(LIMIT_N).iterrows():
                msg_wave.append(f"🏭[{row['產業族群']}] {row['代號']} {row['名稱']} (${row['現價']})\n   └ 漲:{row['漲跌幅']}% | 預估總量:{row['成交(張)(含預估)']}張\n")
        else:
            msg_wave.append("🌊 波段蓄勢: 無\n")
            
        msg_wave.extend(["================\n", "🔗 點此查詢: https://tw.stock.yahoo.com/"])
        if send_telegram:
            send_telegram_message(msg_wave)

        if not wrote_sheet:
            pd.DataFrame([{"狀態": "本次盤中掃描無符合策略條件的標的"}]).to_excel(writer, sheet_name='無符合標的', index=False)

    automation_metadata = {
        key: value
        for key, value in {
            "automation_slot": os.getenv("INTRADAY_AUTOMATION_SLOT", ""),
            "github_run_id": os.getenv("GITHUB_RUN_ID", ""),
            "github_run_attempt": os.getenv("GITHUB_RUN_ATTEMPT", ""),
            "realtime_coverage": round(coverage, 4),
            "realtime_all_coverage": round(all_market_coverage, 4),
            "coverage_universe_size": len(coverage_tickers),
            "coverage_policy": COVERAGE_POLICY_VERSION,
            "history_seconds": round(history_seconds, 2),
            "realtime_seconds": round(realtime_seconds, 2),
            "quote_attempts": quote_attempts,
            "analysis_seconds": round(analysis_seconds, 2),
        }.items()
        if value not in (None, "")
    }
    try:
        db_result = record_scan_results(
            mode="intraday",
            trade_date=trade_date,
            strategy_frames=strategy_frames,
            report_path=filename,
            notes=json.dumps(automation_metadata, ensure_ascii=False, sort_keys=True),
        )
        print(f"🗃️  已寫入訊號資料庫: {db_result['db_path']} ({db_result['signals']} 筆訊號)")
    except Exception as e:
        raise RuntimeError(f"訊號資料庫寫入失敗: {e}") from e
    
    elapsed = time.time() - start_time
    minutes, seconds = divmod(int(elapsed), 60)
    print(f"\n📊 掃描完成！Excel 已儲存: {filename}")
    print(f"⏱️  總耗時: {minutes} 分 {seconds} 秒")
    return {
        "status": "completed",
        "reason": "",
        "message": "盤中掃描完成。",
        "report_path": filename,
        "run_id": db_result["run_id"],
        "signal_count": db_result["signals"],
        "realtime_coverage": coverage,
        "stage_seconds": {
            "history": round(history_seconds, 2),
            "realtime": round(realtime_seconds, 2),
            "analysis": round(analysis_seconds, 2),
            "total": round(elapsed, 2),
        },
        "market_context": {
            "codes": codes,
            "yf_to_code": yf_to_code,
            "history": all_stock_data,
            "realtime": rt_prices,
            "captured_at": quote_captured_at,
            "coverage_codes": sorted(coverage_codes),
            "coverage_policy": COVERAGE_POLICY_VERSION,
            "all_universe_size": full_universe_size,
        },
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the intraday stock scanner.")
    parser.add_argument("--no-telegram", action="store_true", help="不發送原始三策略清單到 Telegram")
    args = parser.parse_args()
    run_intraday_scanner(send_telegram=not args.no_telegram)
