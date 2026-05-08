import argparse
import datetime as dt
import glob
import os
import re
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

STRATEGY_SHEET_NAMES = ["順勢突破", "低檔爆量", "波段蓄勢"]
TAIPEI_TZ = dt.timezone(dt.timedelta(hours=8), name="Asia/Taipei")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def normalize_code(value):
    if value is None or value != value:
        return ""
    code = str(value).strip().upper().replace(".TW", "").replace(".TWO", "")
    if code.endswith(".0"):
        code = code[:-2]
    return code.zfill(4) if code.isdigit() and len(code) < 4 else code


def latest_report(patterns, exclude_keywords=None):
    exclude_keywords = exclude_keywords or []
    files = []
    for pattern in patterns:
        files.extend(glob.glob(pattern))
    files = [
        path
        for path in files
        if all(keyword not in os.path.basename(path) for keyword in exclude_keywords)
    ]
    return max(files, key=os.path.getmtime) if files else None


def _safe_float(value, default=None):
    try:
        if value is None or value == "" or value != value:
            return default
        if isinstance(value, str):
            value = value.replace("%", "").replace(",", "")
        return float(value)
    except Exception:
        return default


def _fmt(value, digits=2, suffix=""):
    value = _safe_float(value)
    if value is None:
        return "NA"
    return f"{value:.{digits}f}{suffix}"


def _fmt_int(value):
    value = _safe_float(value)
    return "NA" if value is None else f"{value:,.0f}"


def _timestamp_from_path(path):
    match = re.search(r"(\d{4}-\d{2}-\d{2}_\d{4})", str(path or ""))
    if match:
        return match.group(1)
    return dt.datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d_%H%M")


def _summary_map(summary_df):
    if summary_df is None or summary_df.empty or not {"項目", "數值"}.issubset(summary_df.columns):
        return {}
    return dict(zip(summary_df["項目"].astype(str), summary_df["數值"].astype(str)))


def load_scan_signals(scan_path):
    if not scan_path or not Path(scan_path).exists():
        return pd.DataFrame()

    frames = []
    xl = pd.ExcelFile(scan_path)
    for sheet in xl.sheet_names:
        if sheet not in STRATEGY_SHEET_NAMES:
            continue
        df = pd.read_excel(scan_path, sheet_name=sheet)
        if df.empty or "代號" not in df.columns:
            continue
        df = df.copy()
        df["代號"] = df["代號"].apply(normalize_code)
        df["策略"] = sheet
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def load_market_report(market_path):
    if not market_path or not Path(market_path).exists():
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    market = pd.read_excel(market_path, sheet_name="全市場明細")
    industry = pd.read_excel(market_path, sheet_name="產業熱度")
    summary = pd.read_excel(market_path, sheet_name="市場總覽")
    focus = pd.read_excel(market_path, sheet_name="資金焦點")
    if "代號" in market.columns:
        market["代號"] = market["代號"].apply(normalize_code)
    if "代號" in focus.columns:
        focus["代號"] = focus["代號"].apply(normalize_code)
    return market, industry, summary, focus


def summarize_signals(signals):
    if signals.empty:
        return pd.DataFrame()

    rows = []
    for (code, name, industry), group in signals.groupby(["代號", "名稱", "產業族群"], dropna=False):
        stop_values = pd.to_numeric(group.get("防守價", pd.Series(dtype=float)), errors="coerce").dropna()
        rsi_values = pd.to_numeric(group.get("RSI", pd.Series(dtype=float)), errors="coerce").dropna()
        volume_values = pd.to_numeric(group.get("成交(張)(含預估)", pd.Series(dtype=float)), errors="coerce").dropna()
        pct_values = pd.to_numeric(group.get("漲跌幅", pd.Series(dtype=float)), errors="coerce").dropna()
        rows.append(
            {
                "代號": code,
                "名稱": name,
                "產業族群": industry,
                "策略": " / ".join(sorted(group["策略"].dropna().astype(str).unique())),
                "策略數": int(group["策略"].nunique()),
                "掃描現價": _safe_float(group["現價"].iloc[-1]) if "現價" in group.columns else None,
                "掃描漲跌幅": float(pct_values.max()) if not pct_values.empty else None,
                "防守價": float(stop_values.max()) if not stop_values.empty else None,
                "掃描成交預估張": float(volume_values.max()) if not volume_values.empty else None,
                "RSI": float(rsi_values.max()) if not rsi_values.empty else None,
                "條件": "；".join(sorted(group.get("條件", pd.Series(dtype=str)).dropna().astype(str).unique())),
            }
        )
    return pd.DataFrame(rows)


def score_candidate(row):
    score = 0
    notes = []

    strategies = str(row.get("策略", ""))
    if row.get("策略數", 0) >= 2:
        score += 18
        notes.append("多策略")
    elif "順勢突破" in strategies:
        score += 12
        notes.append("順勢")
    elif "波段蓄勢" in strategies:
        score += 8
        notes.append("VCP")
    elif "低檔爆量" in strategies:
        score += 6
        notes.append("反轉")

    industry_heat = _safe_float(row.get("產業熱度分數"))
    if industry_heat is not None:
        if industry_heat >= 11:
            score += 16
            notes.append("熱區")
        elif industry_heat >= 8:
            score += 9
            notes.append("中熱區")

    turnover = _safe_float(row.get("成交值(億)"))
    if turnover is not None:
        if turnover >= 30:
            score += 14
            notes.append("成交值足")
        elif turnover >= 5:
            score += 8
            notes.append("成交值可")
        else:
            score -= 8
            notes.append("流動性弱")

    volume_ratio = _safe_float(row.get("量比5"))
    if volume_ratio is not None:
        if 2 <= volume_ratio <= 8:
            score += 12
            notes.append("量能健康")
        elif 8 < volume_ratio <= 12:
            score += 4
            notes.append("量偏熱")
        elif volume_ratio > 12:
            score -= 5
            notes.append("量過熱")

    pct = _safe_float(row.get("漲跌幅"))
    if pct is not None:
        if 1 <= pct <= 7:
            score += 10
            notes.append("漲幅可追蹤")
        elif 7 < pct < 9.5:
            score += 2
            notes.append("漲幅偏高")
        elif pct >= 9.5:
            score -= 8
            notes.append("近漲停")
        elif pct < 0:
            score -= 10
            notes.append("逆勢弱")

    intraday_position = _safe_float(row.get("收盤位置"))
    if intraday_position is not None:
        if 0.35 <= intraday_position <= 0.85:
            score += 10
            notes.append("位置健康")
        elif intraday_position > 0.9:
            score -= 3
            notes.append("接近日高")
        elif intraday_position < 0.25:
            score -= 8
            notes.append("日內回落")

    return pd.Series({"分數": score, "理由": "、".join(notes)})


def build_candidate_ranking(scan_path, market_path):
    signals = load_scan_signals(scan_path)
    market, industry, summary, focus = load_market_report(market_path)

    if signals.empty:
        return pd.DataFrame(), signals, market, industry, summary, focus

    signal_summary = summarize_signals(signals)
    if market.empty or "代號" not in market.columns:
        ranked = signal_summary.copy()
        ranked["分數"] = 0
        ranked["理由"] = ""
        return ranked, signals, market, industry, summary, focus

    merge_cols = ["代號"]
    extra_market_cols = [
        "產業族群",
        "名稱",
        "現價",
        "漲跌幅",
        "成交值(億)",
        "量比5",
        "量比20",
        "收盤位置",
    ]
    available_market_cols = merge_cols + [col for col in extra_market_cols if col in market.columns]
    market_small = market[available_market_cols].copy()
    ranked = signal_summary.merge(market_small, on="代號", how="left", suffixes=("", "_市場"))

    for col in ["名稱", "產業族群"]:
        market_col = f"{col}_市場"
        if market_col in ranked.columns:
            ranked[col] = ranked[col].fillna(ranked[market_col])
            ranked.drop(columns=[market_col], inplace=True)

    if not industry.empty and "產業族群" in industry.columns:
        industry_cols = ["產業族群", "熱度分數", "上漲比例", "平均漲跌幅", "成交值合計_億"]
        industry_small = industry[[col for col in industry_cols if col in industry.columns]].rename(
            columns={
                "熱度分數": "產業熱度分數",
                "上漲比例": "產業上漲比例",
                "平均漲跌幅": "產業平均漲跌幅",
                "成交值合計_億": "產業成交值億",
            }
        )
        ranked = ranked.merge(industry_small, on="產業族群", how="left")

    ranked = pd.concat([ranked, ranked.apply(score_candidate, axis=1)], axis=1)
    sort_cols = [col for col in ["分數", "成交值(億)", "策略數"] if col in ranked.columns]
    ranked = ranked.sort_values(sort_cols, ascending=[False] * len(sort_cols)) if sort_cols else ranked
    return ranked, signals, market, industry, summary, focus


def _strategy_count_text(signals):
    if signals.empty:
        return "0 筆"
    counts = signals.groupby("策略").size().to_dict()
    parts = [f"{name}{int(counts.get(name, 0))}" for name in STRATEGY_SHEET_NAMES]
    return f"{len(signals)} 筆（{' / '.join(parts)}）"


def _top_industry_text(industry, n=4):
    if industry.empty:
        return "無產業資料"
    parts = []
    for _, row in industry.head(n).iterrows():
        parts.append(
            f"{row['產業族群']} { _fmt(row.get('上漲比例'), 1, '%') } / 均{ _fmt(row.get('平均漲跌幅'), 2, '%') }"
        )
    return "；".join(parts)


def _signal_industry_text(signals, n=4):
    if signals.empty:
        return "無策略訊號"
    counts = signals.groupby("產業族群").size().sort_values(ascending=False).head(n)
    return "；".join([f"{industry}{int(count)}" for industry, count in counts.items()])


def _focus_text(focus, n=5):
    if focus.empty:
        return "無資金焦點"
    rows = []
    for _, row in focus.head(n).iterrows():
        rows.append(
            f"{normalize_code(row.get('代號'))} {row.get('名稱')} "
            f"{_fmt(row.get('漲跌幅'), 2, '%')} / { _fmt(row.get('成交值(億)'), 1, '億') }"
        )
    return "；".join(rows)


def _candidate_text(ranked, n=5):
    if ranked.empty:
        return "無候選名單"
    rows = []
    display = ranked.head(n)
    for _, row in display.iterrows():
        rows.append(
            f"{normalize_code(row.get('代號'))} {row.get('名稱')} "
            f"{row.get('策略')}｜價{_fmt(row.get('現價'))} "
            f"{_fmt(row.get('漲跌幅'), 2, '%')}｜量比{_fmt(row.get('量比5'))}｜守{_fmt(row.get('防守價'))}"
        )
    return "\n".join([f"- {row}" for row in rows])


def _market_stance(summary):
    values = _summary_map(summary)
    up_ratio = _safe_float(values.get("上漲比例"))
    avg_pct = _safe_float(values.get("平均漲跌幅"))
    median_pct = _safe_float(values.get("中位數漲跌幅"))
    if up_ratio is None:
        return "資料不足，先以個股風險控管為主。"
    if up_ratio < 45 and (median_pct is None or median_pct <= 0):
        return "主流族群撐盤，但市場廣度不足；追價保守，優先等回測。"
    if up_ratio >= 55 and avg_pct is not None and avg_pct > 0:
        return "盤面廣度偏多，可聚焦主流族群內的強勢延續。"
    return "盤面中性偏分歧，僅挑量價健康且能設防守價的標的。"


def build_report_text(ranked, signals, industry, summary, focus):
    values = _summary_map(summary)
    update_time = values.get("更新時間") or dt.datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M")
    lines = [
        f"盤中分析快報｜{update_time}",
        "",
        "市場狀態：",
        (
            f"有效{values.get('有效即時股票數', 'NA')}檔，"
            f"上漲比例{values.get('上漲比例', 'NA')}，"
            f"平均{values.get('平均漲跌幅', 'NA')}，"
            f"中位數{values.get('中位數漲跌幅', 'NA')}。"
        ),
        f"判斷：{_market_stance(summary)}",
        "",
        "主流族群：",
        _top_industry_text(industry),
        "",
        "策略訊號：",
        f"{_strategy_count_text(signals)}；集中：{_signal_industry_text(signals)}。",
        "",
        "資金焦點：",
        _focus_text(focus),
        "",
        "觀察名單：",
        _candidate_text(ranked),
        "",
        "操作原則：",
        "強股續抱、弱股控風險；不追近漲停，優先等回測；候選跌破防守價先淘汰。",
    ]
    return "\n".join(lines)


def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False, "尚未設定 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID。"

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    chunks = [text[i : i + 3800] for i in range(0, len(text), 3800)] or [text]
    for chunk in chunks:
        try:
            response = requests.post(
                url,
                data={"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "disable_web_page_preview": True},
                timeout=20,
            )
        except requests.RequestException as exc:
            return False, f"Telegram 網路連線失敗: {exc.__class__.__name__}"
        if response.status_code != 200:
            return False, response.text
    return True, "Telegram 訊息發送成功。"


def generate_intraday_analysis_report(
    scan_path=None,
    market_path=None,
    run_scanner=False,
    run_market_monitor=False,
    send_telegram=False,
    send_raw_scanner_telegram=False,
    save_report=True,
):
    if run_scanner:
        import intraday_scanner

        try:
            intraday_scanner.run_intraday_scanner(send_telegram=send_raw_scanner_telegram)
        except SystemExit as exc:
            if exc.code not in (0, None):
                raise
        scan_path = latest_report(
            ["Reports/盤中日報_*.xlsx"],
            exclude_keywords=["策略市場交叉分析", "二次篩選分析"],
        )

    if run_market_monitor:
        import market_monitor

        generated_market_path = market_monitor.run_market_monitor(send_telegram=False)
        if not generated_market_path:
            raise RuntimeError("全市場監控沒有產生新報表，停止產生盤中分析，避免混用舊市場快照。")
        market_path = generated_market_path

    scan_path = scan_path or latest_report(
        ["Reports/盤中日報_*.xlsx"],
        exclude_keywords=["策略市場交叉分析", "二次篩選分析"],
    )
    market_path = market_path or latest_report(["Reports/市場監控_*.xlsx"])

    if not scan_path:
        raise FileNotFoundError("找不到盤中日報，請先執行 intraday_scanner.py。")
    if not market_path:
        raise FileNotFoundError("找不到市場監控報表，請先執行 market_monitor.py。")

    ranked, signals, market, industry, summary, focus = build_candidate_ranking(scan_path, market_path)
    report_text = build_report_text(ranked, signals, industry, summary, focus)

    os.makedirs("Reports", exist_ok=True)
    timestamp = _timestamp_from_path(market_path)
    report_path = None
    ranking_path = None
    if save_report:
        report_path = Path(f"Reports/盤中分析報告_{timestamp}.txt")
        report_path.write_text(report_text, encoding="utf-8")
        if not ranked.empty:
            ranking_cols = [
                "分數",
                "代號",
                "名稱",
                "產業族群",
                "策略",
                "現價",
                "漲跌幅",
                "成交值(億)",
                "量比5",
                "量比20",
                "收盤位置",
                "防守價",
                "理由",
                "條件",
            ]
            ranking_path = Path(f"Reports/盤中候選交叉排名_{timestamp}.csv")
            ranked[[col for col in ranking_cols if col in ranked.columns]].to_csv(
                ranking_path,
                index=False,
                encoding="utf-8-sig",
            )

    telegram_sent = False
    telegram_message = ""
    if send_telegram:
        telegram_sent, telegram_message = send_telegram_message(report_text)

    return {
        "text": report_text,
        "scan_path": str(scan_path),
        "market_path": str(market_path),
        "report_path": str(report_path) if report_path else "",
        "ranking_path": str(ranking_path) if ranking_path else "",
        "telegram_sent": telegram_sent,
        "telegram_message": telegram_message,
        "signal_count": len(signals),
    }


def main():
    parser = argparse.ArgumentParser(description="Build a concise intraday market analysis report.")
    parser.add_argument("--scan-report", help="指定盤中日報 Excel")
    parser.add_argument("--market-report", help="指定市場監控 Excel")
    parser.add_argument("--run-scanner", action="store_true", help="先執行 intraday_scanner.py")
    parser.add_argument("--run-market-monitor", action="store_true", help="先執行 market_monitor.py")
    parser.add_argument("--send-telegram", action="store_true", help="同步發送精簡分析報告到 Telegram")
    parser.add_argument(
        "--send-raw-scanner-telegram",
        action="store_true",
        help="搭配 --run-scanner 時，也發送原始三策略清單。",
    )
    parser.add_argument("--no-save", action="store_true", help="不輸出 txt/csv 報告檔")
    args = parser.parse_args()

    result = generate_intraday_analysis_report(
        scan_path=args.scan_report,
        market_path=args.market_report,
        run_scanner=args.run_scanner,
        run_market_monitor=args.run_market_monitor,
        send_telegram=args.send_telegram,
        send_raw_scanner_telegram=args.send_raw_scanner_telegram,
        save_report=not args.no_save,
    )
    print(result["text"])
    if result["report_path"]:
        print(f"\n報告已儲存: {result['report_path']}")
    if result["ranking_path"]:
        print(f"交叉排名已儲存: {result['ranking_path']}")
    if args.send_telegram:
        status = "成功" if result["telegram_sent"] else "失敗"
        print(f"Telegram {status}: {result['telegram_message']}")


if __name__ == "__main__":
    main()
