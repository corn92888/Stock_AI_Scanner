import argparse
import datetime as dt
import glob
import os
import re
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

from database import DB_PATH, find_scan_run, get_daily_candidate_state, save_candidate_events
from selection_policy import (
    DEFAULT_SELECTION_POLICY,
    apply_selection_policy,
    candidate_event_records,
)

load_dotenv()

STRATEGY_SHEET_NAMES = ["順勢突破", "低檔爆量", "波段蓄勢"]
TAIPEI_TZ = dt.timezone(dt.timedelta(hours=8), name="Asia/Taipei")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
SWING_HOLDING_RULE = "以 T+1/T+3 續漲為目標；買進當天不做賣出判斷，隔日收盤再驗證。"


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


def _report_datetime_from_path(path):
    match = re.search(r"(\d{4}-\d{2}-\d{2})_(\d{4})", str(path or ""))
    if not match:
        return None
    try:
        parsed = dt.datetime.strptime("_".join(match.groups()), "%Y-%m-%d_%H%M")
        return parsed.replace(tzinfo=TAIPEI_TZ)
    except ValueError:
        return None


def validate_report_freshness(scan_path, market_path, max_skew_minutes=45):
    scan_time = _report_datetime_from_path(scan_path)
    market_time = _report_datetime_from_path(market_path)
    if scan_time is None or market_time is None:
        return
    if scan_time.date() != market_time.date():
        raise RuntimeError(
            f"盤中日報日期 {scan_time.date()} 與市場快照日期 {market_time.date()} 不一致。"
        )
    skew_minutes = abs((market_time - scan_time).total_seconds()) / 60
    if skew_minutes > max_skew_minutes:
        raise RuntimeError(
            f"盤中日報與市場快照相差 {skew_minutes:.0f} 分鐘，"
            f"超過允許的 {max_skew_minutes} 分鐘。"
        )


def _skipped_result(reason, message):
    return {
        "status": "skipped",
        "reason": reason,
        "text": message,
        "scan_path": "",
        "market_path": "",
        "report_path": "",
        "ranking_path": "",
        "telegram_sent": False,
        "telegram_message": "盤中流程略過，未發送分析報告。",
        "signal_count": 0,
        "selected_count": 0,
        "candidate_events_saved": 0,
        "scan_run_id": None,
        "ai_result": None,
    }


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


def _risk_pct(row):
    price = _safe_float(row.get("現價"))
    support = _safe_float(row.get("防守價"))
    if price is None or support is None or price <= 0 or support <= 0 or support >= price:
        return None
    return (price - support) / price * 100


def _observation_price(row):
    price = _safe_float(row.get("現價"))
    support = _safe_float(row.get("防守價"))
    if support is not None and support > 0:
        return support
    return price * 0.96 if price is not None else None


def _chase_limit(row):
    price = _safe_float(row.get("現價"))
    high = _safe_float(row.get("最高"))
    if price is None:
        return None
    if high is not None and high > price:
        return min(high, price * 1.025)
    return price * 1.015


def _swing_profile(row):
    strategies = str(row.get("策略", ""))
    if "波段蓄勢" in strategies:
        return "波段續漲"
    if "順勢突破" in strategies:
        return "隔日延續"
    if "低檔爆量" in strategies:
        return "反彈驗證"
    return "觀察"


def _holding_plan(row):
    observation = _observation_price(row)
    chase_limit = _chase_limit(row)
    return (
        f"今日買進不賣；隔日收盤未守{_fmt(observation)}再檢討；"
        f"高於{_fmt(chase_limit)}不追。"
    )


def score_candidate(row):
    score = 0
    notes = []

    strategies = str(row.get("策略", ""))
    if row.get("策略數", 0) >= 2:
        score += 18
        notes.append("多策略")
    if "波段蓄勢" in strategies:
        score += 18
        notes.append("波段整理")
    if "順勢突破" in strategies:
        score += 14
        notes.append("趨勢延續")
    if "低檔爆量" in strategies:
        score += 4
        notes.append("反彈待驗證")
    if row.get("策略數", 0) < 2 and not any(name in strategies for name in STRATEGY_SHEET_NAMES):
        score -= 4
        notes.append("策略訊號弱")

    industry_up_ratio = _safe_float(row.get("產業上漲比例"))
    if industry_up_ratio is not None:
        if industry_up_ratio >= 60:
            score += 14
            notes.append("族群廣度佳")
        elif industry_up_ratio >= 45:
            score += 7
            notes.append("族群尚可")
        elif industry_up_ratio < 35:
            score -= 8
            notes.append("族群分歧")

    industry_avg_pct = _safe_float(row.get("產業平均漲跌幅"))
    if industry_avg_pct is not None:
        if industry_avg_pct >= 0.3:
            score += 6
            notes.append("族群收紅")
        elif industry_avg_pct < 0:
            score -= 5
            notes.append("族群偏弱")

    industry_heat = _safe_float(row.get("產業熱度分數"))
    if industry_heat is not None:
        if industry_heat >= 11:
            score += 8
            notes.append("熱區")
        elif industry_heat >= 8:
            score += 4
            notes.append("中熱區")

    turnover = _safe_float(row.get("成交值(億)"))
    if turnover is not None:
        if 10 <= turnover <= 150:
            score += 12
            notes.append("流動性佳")
        elif turnover > 150:
            score += 8
            notes.append("大資金")
        elif turnover >= 3:
            score += 4
            notes.append("流動性可")
        else:
            score -= 10
            notes.append("流動性弱")

    volume_ratio = _safe_float(row.get("量比5"))
    if volume_ratio is not None:
        if 1.2 <= volume_ratio <= 3.5:
            score += 16
            notes.append("續漲量能")
        elif 3.5 < volume_ratio <= 6:
            score += 8
            notes.append("量能偏強")
        elif 6 < volume_ratio <= 10:
            score -= 2
            notes.append("量能偏急")
        elif volume_ratio > 10:
            score -= 12
            notes.append("爆量過熱")
        elif volume_ratio < 0.8:
            score -= 6
            notes.append("量能不足")

    pct = _safe_float(row.get("漲跌幅"))
    if pct is not None:
        if 0.5 <= pct <= 4.5:
            score += 14
            notes.append("漲幅適中")
        elif 4.5 < pct <= 6.5:
            score += 6
            notes.append("漲幅偏強")
        elif 6.5 < pct < 9.5:
            score -= 4
            notes.append("短線偏熱")
        elif pct >= 9.5:
            score -= 18
            notes.append("近漲停")
        elif pct < 0:
            score -= 14
            notes.append("逆勢弱")
        else:
            score += 2
            notes.append("低漲幅")

    intraday_position = _safe_float(row.get("收盤位置"))
    if intraday_position is not None:
        if 0.55 <= intraday_position <= 0.85:
            score += 16
            notes.append("收位健康")
        elif 0.35 <= intraday_position < 0.55:
            score += 6
            notes.append("收位普通")
        elif 0.85 < intraday_position <= 0.95:
            score += 8
            notes.append("收近高")
        elif intraday_position > 0.95:
            score -= 6
            notes.append("近高追價")
        elif intraday_position < 0.25:
            score -= 18
            notes.append("日內回落")

    risk = _risk_pct(row)
    if risk is not None:
        if 2 <= risk <= 8:
            score += 10
            notes.append("隔日觀察價合理")
        elif risk < 2:
            score += 4
            notes.append("觀察價很近")
        elif 8 < risk <= 12:
            score -= 4
            notes.append("觀察價偏遠")
        else:
            score -= 12
            notes.append("波動風險大")

    return pd.Series(
        {
            "分數": score,
            "續漲型態": _swing_profile(row),
            "隔日觀察價": _observation_price(row),
            "追價上限": _chase_limit(row),
            "持有計畫": _holding_plan(row),
            "理由": "、".join(notes),
        }
    )


def build_candidate_ranking(scan_path, market_path):
    signals = load_scan_signals(scan_path)
    market, industry, summary, focus = load_market_report(market_path)
    quote_time = _summary_map(summary).get("更新時間", "")

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
        "開盤",
        "最高",
        "最低",
        "現價",
        "漲跌幅",
        "目前成交量(張)",
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
    market_values = _summary_map(summary)
    ranked["市場上漲比例"] = _safe_float(market_values.get("上漲比例"))
    ranked["市場平均漲跌幅"] = _safe_float(market_values.get("平均漲跌幅"))
    ranked["市場中位數漲跌幅"] = _safe_float(market_values.get("中位數漲跌幅"))
    if quote_time:
        ranked["報價時間"] = quote_time
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


def _policy_status_text(row):
    status = str(row.get("政策狀態", "") or "")
    labels = {
        "selected": "正式入選",
        "blocked": f"阻擋：{row.get('阻擋原因') or '資料未通過'}",
        "duplicate_daily_eligible": "今日較早批次已合格",
        "daily_limit": "當日名額已滿",
        "industry_limit": "同產業已有入選",
        "score_below_threshold": "分數未達正式門檻",
    }
    return labels.get(status, status)


def _candidate_text(ranked, n=5, show_policy=False):
    if ranked.empty:
        return "無候選名單"
    rows = []
    display = ranked.head(n)
    for _, row in display.iterrows():
        quote_time = str(row.get("報價時間", "") or "")
        quote_clock = quote_time[11:16] if len(quote_time) >= 16 else quote_time[-5:]
        quote_label = f"@{quote_clock}" if quote_clock else ""
        text = (
            f"{normalize_code(row.get('代號'))} {row.get('名稱')}｜{row.get('續漲型態')}｜"
            f"分{_fmt(row.get('分數'), 0)}｜價{_fmt(row.get('現價'))}{quote_label} "
            f"{_fmt(row.get('漲跌幅'), 2, '%')}｜量比{_fmt(row.get('量比5'))}｜"
            f"隔日觀察{_fmt(row.get('隔日觀察價'))}｜不追>{_fmt(row.get('追價上限'))}"
        )
        if show_policy and "政策狀態" in row:
            text += f"｜{_policy_status_text(row)}"
        risk_flags = str(row.get("風險標記", "") or "")
        if risk_flags:
            text += f"｜風險：{risk_flags}"
        rows.append(text)
    return "\n".join([f"- {row}" for row in rows])


def _market_stance(summary):
    values = _summary_map(summary)
    up_ratio = _safe_float(values.get("上漲比例"))
    avg_pct = _safe_float(values.get("平均漲跌幅"))
    median_pct = _safe_float(values.get("中位數漲跌幅"))
    if up_ratio is None:
        return "資料不足，先以個股風險控管為主。"
    if up_ratio < 45 and (median_pct is None or median_pct <= 0):
        return "主流族群撐盤但市場廣度不足；只挑隔日續漲結構完整、收位健康的標的。"
    if up_ratio >= 55 and avg_pct is not None and avg_pct > 0:
        return "盤面廣度偏多，可聚焦主流族群內的 T+1/T+3 強勢延續。"
    return "盤面中性偏分歧，僅挑量價健康且隔日觀察價合理的標的。"


def build_report_text(ranked, signals, industry, summary, focus):
    values = _summary_map(summary)
    update_time = values.get("更新時間") or dt.datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M")
    if "政策入選" in ranked.columns:
        selected = ranked[ranked["政策入選"] == True]  # noqa: E712
        research = ranked[ranked["政策入選"] != True]  # noqa: E712
    else:
        selected = ranked.head(3)
        research = ranked.iloc[3:]

    lines = [
        f"盤中續漲分析快報｜{update_time}",
        SWING_HOLDING_RULE,
        "價格為本報告產生時間的快照，不是即時追價；盤中價格若已變動，請重新跑一次。",
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
        "正式模擬入選（每日最多3檔、同產業最多1檔）：",
        _candidate_text(selected, n=3, show_policy=True),
        "",
        "其餘研究候選：",
        _candidate_text(research, n=5, show_policy=True),
        "",
        "操作原則：",
        "今天買進後不做當日賣出判斷；隔日收盤再看是否守住觀察價、族群是否仍強、量能是否延續。盤中急殺只記錄風險，不直接給賣出訊號。",
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
    send_raw_scanner_telegram=None,
    run_ai=False,
    save_report=True,
    db_path=DB_PATH,
):
    if send_raw_scanner_telegram is None:
        send_raw_scanner_telegram = bool(run_scanner and send_telegram)

    if run_scanner:
        import intraday_scanner

        scanner_result = intraday_scanner.run_intraday_scanner(
            send_telegram=send_raw_scanner_telegram
        )
        if scanner_result.get("status") != "completed":
            return _skipped_result(
                scanner_result.get("reason", "scanner_skipped"),
                scanner_result.get("message", "盤中掃描未產生新報表。"),
            )
        scan_path = scanner_result["report_path"]

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

    validate_report_freshness(scan_path, market_path)

    ranked, signals, market, industry, summary, focus = build_candidate_ranking(scan_path, market_path)
    scan_run = find_scan_run(scan_path, db_path=db_path)
    daily_state = None
    if scan_run:
        daily_state = get_daily_candidate_state(
            scan_run["id"],
            DEFAULT_SELECTION_POLICY.version,
            db_path=db_path,
        )
    ranked = apply_selection_policy(
        ranked,
        daily_state=daily_state,
        policy=DEFAULT_SELECTION_POLICY,
    )
    candidate_events_saved = 0
    if scan_run and not ranked.empty:
        candidate_events_saved = save_candidate_events(
            scan_run["id"],
            candidate_event_records(ranked, policy=DEFAULT_SELECTION_POLICY),
            db_path=db_path,
        )
    report_text = build_report_text(ranked, signals, industry, summary, focus)
    ai_result = None
    if run_ai and scan_run and candidate_events_saved:
        try:
            from ai_pipeline import run_ai_pipeline

            ai_result = run_ai_pipeline(run_id=scan_run["id"], db_path=db_path)
            if ai_result.get("report_text"):
                report_text += ai_result["report_text"]
        except Exception as exc:
            ai_result = {"status": "failed", "error": str(exc)}
            report_text += (
                "\n\nAI 影子研究：\n"
                f"- 本批次 AI 管線失敗（{exc.__class__.__name__}），正式規則名單不受影響。"
            )

    os.makedirs("Reports", exist_ok=True)
    timestamp = _timestamp_from_path(market_path)
    report_path = None
    ranking_path = None
    if save_report:
        report_path = Path(f"Reports/盤中分析報告_{timestamp}.txt")
        report_path.write_text(report_text, encoding="utf-8")
        if not ranked.empty:
            ranking_cols = [
                "政策入選",
                "政策排名",
                "政策狀態",
                "可交易",
                "每日首次合格",
                "阻擋原因",
                "風險標記",
                "防守距離%",
                "政策版本",
                "分數",
                "續漲型態",
                "報價時間",
                "代號",
                "名稱",
                "產業族群",
                "策略",
                "開盤",
                "最高",
                "最低",
                "現價",
                "漲跌幅",
                "目前成交量(張)",
                "成交值(億)",
                "量比5",
                "量比20",
                "收盤位置",
                "隔日觀察價",
                "追價上限",
                "防守價",
                "持有計畫",
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
        "status": "completed",
        "reason": "",
        "text": report_text,
        "scan_path": str(scan_path),
        "market_path": str(market_path),
        "report_path": str(report_path) if report_path else "",
        "ranking_path": str(ranking_path) if ranking_path else "",
        "telegram_sent": telegram_sent,
        "telegram_message": telegram_message,
        "signal_count": len(signals),
        "selected_count": int(ranked["政策入選"].sum()) if "政策入選" in ranked else 0,
        "candidate_events_saved": candidate_events_saved,
        "scan_run_id": scan_run["id"] if scan_run else None,
        "ai_result": ai_result,
    }


def main():
    parser = argparse.ArgumentParser(description="Build a concise intraday market analysis report.")
    parser.add_argument("--scan-report", help="指定盤中日報 Excel")
    parser.add_argument("--market-report", help="指定市場監控 Excel")
    parser.add_argument("--run-scanner", action="store_true", help="先執行 intraday_scanner.py")
    parser.add_argument("--run-market-monitor", action="store_true", help="先執行 market_monitor.py")
    parser.add_argument("--send-telegram", action="store_true", help="同步發送精簡分析報告到 Telegram")
    parser.add_argument(
        "--run-ai",
        action="store_true",
        help="建立特徵、執行影子模型與新聞 AI，並附加到分析報告",
    )
    parser.add_argument(
        "--send-raw-scanner-telegram",
        action="store_true",
        help="搭配 --run-scanner 時，也發送原始三策略清單；現在 --run-scanner --send-telegram 已預設開啟。",
    )
    parser.add_argument(
        "--no-raw-scanner-telegram",
        action="store_true",
        help="搭配 --run-scanner --send-telegram 時，不發送原始三策略清單，只發送精簡分析。",
    )
    parser.add_argument("--no-save", action="store_true", help="不輸出 txt/csv 報告檔")
    args = parser.parse_args()

    send_raw_scanner_telegram = None
    if args.send_raw_scanner_telegram:
        send_raw_scanner_telegram = True
    if args.no_raw_scanner_telegram:
        send_raw_scanner_telegram = False

    result = generate_intraday_analysis_report(
        scan_path=args.scan_report,
        market_path=args.market_report,
        run_scanner=args.run_scanner,
        run_market_monitor=args.run_market_monitor,
        send_telegram=args.send_telegram,
        send_raw_scanner_telegram=send_raw_scanner_telegram,
        run_ai=args.run_ai,
        save_report=not args.no_save,
    )
    print(result["text"])
    if result["status"] == "skipped":
        print(f"盤中流程略過: {result['reason']}")
        return
    if result["report_path"]:
        print(f"\n報告已儲存: {result['report_path']}")
    if result["ranking_path"]:
        print(f"交叉排名已儲存: {result['ranking_path']}")
    if args.send_telegram:
        status = "成功" if result["telegram_sent"] else "失敗"
        print(f"Telegram {status}: {result['telegram_message']}")


if __name__ == "__main__":
    main()
