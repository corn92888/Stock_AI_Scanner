import argparse
import datetime
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError

import numpy as np
import pandas as pd
import requests
import twstock
import yfinance as yf
from tqdm import tqdm

from dotenv import load_dotenv

load_dotenv()

TAIPEI_TZ = datetime.timezone(datetime.timedelta(hours=8), name="Asia/Taipei")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TWSTOCK_TIMEOUT_SECONDS = 8
MIN_MARKET_COVERAGE = 0.65


def get_taipei_now():
    return datetime.datetime.now(TAIPEI_TZ)


def is_market_open(now=None):
    now = now or get_taipei_now()
    if now.weekday() >= 5:
        return False
    return datetime.time(9, 0) <= now.time() <= datetime.time(13, 30)


def safe_float(value, default=0.0):
    if value and value != "-":
        try:
            return float(value)
        except Exception:
            return default
    return default


def parse_realtime_price(rt):
    price = safe_float(rt.get("latest_trade_price"), 0.0)
    if price > 0:
        return price
    bid_prices = rt.get("best_bid_price", [])
    if bid_prices:
        return safe_float(bid_prices[0], 0.0)
    return 0.0


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


def get_projected_volume(current_volume_lots, now=None):
    now = now or get_taipei_now()
    market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
    elapsed_seconds = (now - market_open).total_seconds()
    total_seconds = 4.5 * 3600
    if elapsed_seconds <= 0:
        return 0.0
    if elapsed_seconds >= total_seconds:
        return current_volume_lots
    return current_volume_lots * (total_seconds / elapsed_seconds)


def fetch_realtime_prices(ticker_list, chunk_size=20):
    result = {}
    for i in tqdm(range(0, len(ticker_list), chunk_size), desc="📡 全市場即時報價"):
        chunk = ticker_list[i : i + chunk_size]
        try:
            data = twstock_realtime_get_with_timeout(chunk)
            if not data:
                continue
            if len(chunk) == 1:
                if data.get("success") and "realtime" in data:
                    rt = data["realtime"]
                    price = parse_realtime_price(rt)
                    if price > 0:
                        result[chunk[0]] = {
                            "open": safe_float(rt.get("open")),
                            "high": safe_float(rt.get("high")),
                            "low": safe_float(rt.get("low")),
                            "price": price,
                            "volume_lots": safe_float(rt.get("accumulate_trade_volume")),
                        }
            else:
                for code, info in data.items():
                    if code == "success":
                        continue
                    if isinstance(info, dict) and info.get("success") and "realtime" in info:
                        rt = info["realtime"]
                        price = parse_realtime_price(rt)
                        if price > 0:
                            result[code] = {
                                "open": safe_float(rt.get("open")),
                                "high": safe_float(rt.get("high")),
                                "low": safe_float(rt.get("low")),
                                "price": price,
                                "volume_lots": safe_float(rt.get("accumulate_trade_volume")),
                            }
            time.sleep(0.25)
        except Exception:
            continue
    return result


def batch_download_recent(yf_tickers, period="3mo", chunk_size=200):
    all_data = {}
    for i in tqdm(range(0, len(yf_tickers), chunk_size), desc="📥 近期歷史量價"):
        chunk = yf_tickers[i : i + chunk_size]
        try:
            raw = yf.download(chunk, period=period, progress=False, auto_adjust=False, threads=True)
            if raw.empty:
                continue
            for ticker in chunk:
                try:
                    if len(chunk) == 1:
                        df = raw.copy()
                        if isinstance(df.columns, pd.MultiIndex):
                            df.columns = [col[0] for col in df.columns]
                    elif isinstance(raw.columns, pd.MultiIndex):
                        if ticker not in raw.columns.get_level_values(1):
                            continue
                        df = raw.loc[:, raw.columns.get_level_values(1) == ticker].copy()
                        df.columns = df.columns.get_level_values(0)
                    else:
                        df = raw.copy()

                    df = df.loc[:, ~df.columns.duplicated()]
                    for col in ["Open", "High", "Low", "Close", "Volume"]:
                        if col in df.columns:
                            df[col] = pd.to_numeric(df[col], errors="coerce")
                    df.dropna(subset=["Close", "Volume"], inplace=True)
                    if not df.empty:
                        all_data[ticker] = df
                except Exception:
                    continue
        except Exception:
            continue
    return all_data


def build_yfinance_realtime_fallback(hist_data, yf_to_code, now):
    today_ts = pd.Timestamp(now.date())
    fallback = {}
    for yf_ticker, code in yf_to_code.items():
        hist = hist_data.get(yf_ticker)
        if hist is None or hist.empty:
            continue
        try:
            latest_date = pd.Timestamp(hist.index[-1]).tz_localize(None).normalize()
            if latest_date != today_ts:
                continue
            row = hist.iloc[-1]
            price = safe_float(row.get("Close"), 0.0)
            if price <= 0:
                continue
            fallback[code] = {
                "open": safe_float(row.get("Open"), 0.0),
                "high": safe_float(row.get("High"), 0.0),
                "low": safe_float(row.get("Low"), 0.0),
                "price": price,
                "volume_lots": safe_float(row.get("Volume"), 0.0) / 1000,
            }
        except Exception:
            continue
    return fallback


def build_ticker_maps():
    codes = twstock.codes
    tickers = [code for code in codes.keys() if codes[code].type == "股票"]
    yf_to_code = {}
    for code in tickers:
        suffix = "TWO" if codes[code].market == "上櫃" else "TW"
        yf_to_code[f"{code}.{suffix}"] = code
    return codes, tickers, yf_to_code


def _scanner_realtime_context(market_context):
    realtime = {}
    for code, quote in market_context["realtime"].items():
        price = safe_float(quote.get("Close"), 0.0)
        if price <= 0:
            continue
        realtime[code] = {
            "open": safe_float(quote.get("Open"), 0.0),
            "high": safe_float(quote.get("High"), 0.0),
            "low": safe_float(quote.get("Low"), 0.0),
            "price": price,
            "volume_lots": safe_float(quote.get("Volume"), 0.0),
        }
    return realtime


def build_market_snapshot(market_context=None):
    now = market_context.get("captured_at") if market_context else get_taipei_now()
    now = now or get_taipei_now()
    today_ts = pd.Timestamp(now.date())
    if market_context:
        codes = market_context["codes"]
        yf_to_code = market_context["yf_to_code"]
        hist_data = market_context["history"]
        realtime = _scanner_realtime_context(market_context)
        print(
            f"♻️ 重用掃描器行情快照：歷史 {len(hist_data)} 檔、"
            f"即時 {len(realtime)} 檔"
        )
    else:
        codes, tickers, yf_to_code = build_ticker_maps()
        hist_data = batch_download_recent(list(yf_to_code.keys()), period="3mo", chunk_size=200)
        realtime = fetch_realtime_prices(tickers, chunk_size=20)
        yf_realtime = build_yfinance_realtime_fallback(hist_data, yf_to_code, now)
        if yf_realtime:
            missing_count = len([code for code in yf_realtime if code not in realtime])
            realtime.update({code: data for code, data in yf_realtime.items() if code not in realtime})
            if missing_count:
                print(f"⚠️ 已用 yfinance 當日資料補足 {missing_count} 檔即時報價")

    rows = []
    for yf_ticker, code in yf_to_code.items():
        if code not in realtime or yf_ticker not in hist_data:
            continue
        hist = hist_data[yf_ticker]
        hist = hist[hist.index < today_ts]
        if hist.empty:
            continue

        rt = realtime[code]
        price = rt["price"]
        prev_close = float(hist["Close"].iloc[-1])
        if prev_close <= 0 or price <= 0:
            continue

        projected_lots = get_projected_volume(rt["volume_lots"], now) if is_market_open(now) else rt["volume_lots"]
        projected_shares = projected_lots * 1000
        avg5_volume = float(hist["Volume"].tail(5).mean())
        avg20_volume = float(hist["Volume"].tail(20).mean())
        high = rt["high"] if rt["high"] > 0 else price
        low = rt["low"] if rt["low"] > 0 else price
        day_range = high - low
        close_position = (price - low) / day_range if day_range > 0 else np.nan
        stock = codes[code]

        rows.append(
            {
                "產業族群": stock.group if stock.group else "其他",
                "市場": stock.market,
                "代號": code,
                "名稱": stock.name,
                "現價": round(price, 2),
                "昨收": round(prev_close, 2),
                "漲跌幅": round((price - prev_close) / prev_close * 100, 2),
                "開盤": rt["open"],
                "最高": high,
                "最低": low,
                "目前成交量(張)": int(rt["volume_lots"]),
                "預估成交量(張)": int(projected_lots),
                "成交值(億)": round(price * projected_shares / 100000000, 2),
                "量比5": round(projected_shares / avg5_volume, 2) if avg5_volume > 0 else np.nan,
                "量比20": round(projected_shares / avg20_volume, 2) if avg20_volume > 0 else np.nan,
                "收盤位置": round(close_position, 2) if close_position == close_position else np.nan,
                "K線圖": f"https://tw.stock.yahoo.com/quote/{code}",
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df, pd.DataFrame(), pd.DataFrame(), now
    coverage = len(df) / len(yf_to_code) if yf_to_code else 0.0
    if coverage < MIN_MARKET_COVERAGE:
        raise RuntimeError(
            f"全市場報價覆蓋率僅 {coverage:.1%}，低於 {MIN_MARKET_COVERAGE:.0%}，"
            "拒絕產生失真的市場廣度報表。"
        )

    df["上漲"] = df["漲跌幅"] > 0
    df["下跌"] = df["漲跌幅"] < 0
    industry = (
        df.groupby("產業族群")
        .agg(
            股票數=("代號", "count"),
            上漲家數=("上漲", "sum"),
            下跌家數=("下跌", "sum"),
            平均漲跌幅=("漲跌幅", "mean"),
            中位數漲跌幅=("漲跌幅", "median"),
            成交值合計_億=("成交值(億)", "sum"),
            平均量比5=("量比5", "mean"),
        )
        .reset_index()
    )
    industry["上漲比例"] = industry["上漲家數"] / industry["股票數"] * 100
    industry["熱度分數"] = (
        industry["平均漲跌幅"] * 3
        + industry["上漲比例"] / 20
        + np.log1p(industry["成交值合計_億"])
    )
    industry = industry.sort_values(["熱度分數", "成交值合計_億"], ascending=False)

    summary = pd.DataFrame(
        [
            {"項目": "更新時間", "數值": now.strftime("%Y-%m-%d %H:%M:%S")},
            {"項目": "有效即時股票數", "數值": len(df)},
            {"項目": "報價覆蓋率", "數值": f"{coverage * 100:.2f}%"},
            {"項目": "上漲家數", "數值": int(df["上漲"].sum())},
            {"項目": "下跌家數", "數值": int(df["下跌"].sum())},
            {"項目": "上漲比例", "數值": f"{df['上漲'].mean() * 100:.2f}%"},
            {"項目": "平均漲跌幅", "數值": f"{df['漲跌幅'].mean():.2f}%"},
            {"項目": "中位數漲跌幅", "數值": f"{df['漲跌幅'].median():.2f}%"},
            {"項目": "預估成交值合計(億)", "數值": round(df["成交值(億)"].sum(), 2)},
            {"項目": "最熱產業", "數值": industry.iloc[0]["產業族群"] if not industry.empty else ""},
        ]
    )
    return df, industry, summary, now


def write_report(df, industry, summary, now):
    os.makedirs("Reports", exist_ok=True)
    path = f"Reports/市場監控_{now.strftime('%Y-%m-%d_%H%M')}.xlsx"

    focus = df[
        (df["成交值(億)"] >= 1)
        & (df["量比5"] >= 2)
        & (df["量比20"] >= 1.5)
        & (df["漲跌幅"] > 0)
    ].copy()
    focus = focus.sort_values(["成交值(億)", "量比5", "漲跌幅"], ascending=False)

    with pd.ExcelWriter(path) as writer:
        summary.to_excel(writer, sheet_name="市場總覽", index=False)
        industry.round(2).to_excel(writer, sheet_name="產業熱度", index=False)
        focus.head(100).to_excel(writer, sheet_name="資金焦點", index=False)
        df.sort_values("漲跌幅", ascending=False).head(100).to_excel(writer, sheet_name="漲幅排行", index=False)
        df.sort_values("漲跌幅", ascending=True).head(100).to_excel(writer, sheet_name="跌幅排行", index=False)
        df.sort_values("成交值(億)", ascending=False).head(100).to_excel(writer, sheet_name="成交值排行", index=False)
        df.sort_values("量比5", ascending=False).head(100).to_excel(writer, sheet_name="量能異常", index=False)
        df.sort_values(["產業族群", "成交值(億)"], ascending=[True, False]).to_excel(writer, sheet_name="全市場明細", index=False)

    return path, focus


def send_telegram_summary(summary_lines):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    text = "\n".join(summary_lines)
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True}
    requests.post(url, data=payload, timeout=20)


def run_market_monitor(send_telegram=False, market_context=None):
    start = time.time()
    df, industry, summary, now = build_market_snapshot(market_context=market_context)
    if df.empty:
        print("❌ 無法取得有效市場即時資料。")
        return None

    report_path, focus = write_report(df, industry, summary, now)
    print(f"📊 全市場有效股票數: {len(df)}")
    print(f"📈 上漲比例: {df['上漲'].mean() * 100:.2f}% | 平均漲跌幅: {df['漲跌幅'].mean():.2f}% | 中位數: {df['漲跌幅'].median():.2f}%")
    print(f"🔥 最熱產業: {industry.iloc[0]['產業族群']} | 上漲比例 {industry.iloc[0]['上漲比例']:.2f}% | 平均漲跌幅 {industry.iloc[0]['平均漲跌幅']:.2f}%")
    print("\n🏭 產業熱度 Top 10")
    print(industry.head(10).round(2).to_string(index=False))
    print("\n💰 資金焦點 Top 20")
    print(focus.head(20).to_string(index=False))
    print(f"\n✅ 市場監控報表已儲存: {report_path}")
    print(f"⏱️  總耗時: {int(time.time() - start)} 秒")

    if send_telegram:
        top_industries = "、".join(industry.head(3)["產業族群"].tolist())
        top_focus = "\n".join(
            [
                f"{row['代號']} {row['名稱']} {row['漲跌幅']}% | {row['成交值(億)']}億 | 量比5 {row['量比5']}"
                for _, row in focus.head(8).iterrows()
            ]
        )
        send_telegram_summary(
            [
                f"📡 {now.strftime('%m/%d %H:%M')} 全市場盯盤摘要",
                f"上漲比例: {df['上漲'].mean() * 100:.1f}% | 平均漲跌幅: {df['漲跌幅'].mean():.2f}%",
                f"熱區: {top_industries}",
                "------",
                top_focus if top_focus else "目前無符合資金焦點條件標的",
            ]
        )

    return report_path


def main():
    parser = argparse.ArgumentParser(description="Collect a full-market Taiwan stock intraday snapshot.")
    parser.add_argument("--send-telegram", action="store_true", help="發送全市場盯盤摘要到 Telegram")
    args = parser.parse_args()
    run_market_monitor(send_telegram=args.send_telegram)


if __name__ == "__main__":
    main()
