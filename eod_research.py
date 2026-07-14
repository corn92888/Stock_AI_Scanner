import datetime as dt
import math

import pandas as pd

from database import DB_PATH, get_daily_candidate_state, save_candidate_events
from intraday_analysis_report import build_candidate_ranking_from_data
from market_monitor import build_market_snapshot
from selection_policy import (
    DEFAULT_SELECTION_POLICY,
    apply_selection_policy,
    candidate_event_records,
)


STRATEGY_LABELS = {
    "trend": "順勢突破",
    "reversal": "低檔爆量",
    "wave": "波段蓄勢",
}


def _finite_float(value, default=0.0):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _eod_signal_frame(strategy_frames):
    frames = []
    for strategy, frame in strategy_frames.items():
        if frame is None or frame.empty:
            continue
        work = frame.copy()
        work["策略"] = STRATEGY_LABELS.get(strategy, strategy)
        frames.append(work)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _eod_market_context(all_stock_data, yf_to_code, codes, captured_at):
    trade_date = captured_at.date()
    realtime = {}
    for yf_ticker, frame in all_stock_data.items():
        if frame is None or frame.empty:
            continue
        last = frame.iloc[-1]
        last_at = pd.Timestamp(frame.index[-1])
        if last_at.date() != trade_date:
            continue
        code = yf_to_code.get(yf_ticker)
        price = _finite_float(last.get("Close"))
        if not code or price <= 0:
            continue
        realtime[code] = {
            "Open": _finite_float(last.get("Open"), price),
            "High": _finite_float(last.get("High"), price),
            "Low": _finite_float(last.get("Low"), price),
            "Close": price,
            "Volume": _finite_float(last.get("Volume")) / 1000,
        }
    return {
        "captured_at": captured_at,
        "codes": codes,
        "yf_to_code": yf_to_code,
        "history": all_stock_data,
        "realtime": realtime,
    }


def save_eod_research_candidates(
    run_id,
    strategy_frames,
    all_stock_data,
    yf_to_code,
    codes,
    captured_at=None,
    db_path=DB_PATH,
):
    captured_at = captured_at or dt.datetime.now(
        dt.timezone(dt.timedelta(hours=8), name="Asia/Taipei")
    )
    signals = _eod_signal_frame(strategy_frames)
    if signals.empty:
        return {"saved": 0, "selected": 0, "ranked": pd.DataFrame()}

    context = _eod_market_context(
        all_stock_data,
        yf_to_code,
        codes,
        captured_at,
    )
    market, industry, summary, _ = build_market_snapshot(market_context=context)
    ranked, *_ = build_candidate_ranking_from_data(
        signals,
        market,
        industry,
        summary,
        pd.DataFrame(),
    )
    daily_state = get_daily_candidate_state(
        run_id,
        DEFAULT_SELECTION_POLICY.version,
        db_path=db_path,
    )
    ranked = apply_selection_policy(
        ranked,
        daily_state=daily_state,
        policy=DEFAULT_SELECTION_POLICY,
    )
    saved = save_candidate_events(
        run_id,
        candidate_event_records(ranked, policy=DEFAULT_SELECTION_POLICY),
        db_path=db_path,
    )
    return {
        "saved": saved,
        "selected": int(ranked["政策入選"].sum()) if "政策入選" in ranked else 0,
        "ranked": ranked,
    }
