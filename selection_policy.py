import json
from dataclasses import asdict, dataclass

import pandas as pd


@dataclass(frozen=True)
class SelectionPolicy:
    version: str = "tradability_v1"
    max_daily_selections: int = 3
    max_per_industry: int = 1
    min_selection_score: float = 50.0
    min_turnover_billion: float = 5.0
    near_limit_pct: float = 9.5
    max_volume_ratio_5: float = 10.0
    min_intraday_position: float = 0.20
    max_stop_distance_pct: float = 12.0
    turnover_warning_billion: float = 10.0
    min_healthy_volume_ratio_5: float = 1.2
    urgent_volume_ratio_5: float = 6.0
    hot_pct_change: float = 6.5
    weak_intraday_position: float = 0.35
    stop_distance_warning_pct: float = 8.0


DEFAULT_SELECTION_POLICY = SelectionPolicy()


def _safe_float(value):
    try:
        if value is None or value == "" or pd.isna(value):
            return None
        if isinstance(value, str):
            value = value.replace("%", "").replace(",", "")
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value):
    number = _safe_float(value)
    return None if number is None else int(number)


def _normalize_code(value):
    if value is None or pd.isna(value):
        return ""
    code = str(value).strip().upper().replace(".TW", "").replace(".TWO", "")
    if code.endswith(".0"):
        code = code[:-2]
    return code.zfill(4) if code.isdigit() and len(code) < 4 else code


def _safe_text(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _split_text(value, separator="/"):
    if value is None or pd.isna(value):
        return []
    return [item.strip() for item in str(value).split(separator) if item.strip()]


def _join_unique(values, separator=" / "):
    seen = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return separator.join(seen)


def _json_safe(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def deduplicate_candidates(ranked):
    if ranked is None or ranked.empty:
        return pd.DataFrame() if ranked is None else ranked.copy()

    work = ranked.copy()
    work["代號"] = work["代號"].apply(_normalize_code)
    work["_source_order"] = range(len(work))
    sort_columns = [col for col in ["分數", "成交值(億)", "策略數"] if col in work.columns]
    if sort_columns:
        work = work.sort_values(
            sort_columns + ["_source_order"],
            ascending=[False] * len(sort_columns) + [True],
            kind="stable",
        )

    rows = []
    for _, group in work.groupby("代號", sort=False, dropna=False):
        row = group.iloc[0].copy()
        strategies = []
        for value in group.get("策略", pd.Series(dtype=str)):
            strategies.extend(_split_text(value))
        conditions = [
            str(value).strip()
            for value in group.get("條件", pd.Series(dtype=str))
            if value is not None and not pd.isna(value) and str(value).strip()
        ]
        if strategies:
            row["策略"] = _join_unique(strategies)
            row["策略數"] = len(set(strategies))
        if conditions:
            row["條件"] = _join_unique(conditions, separator="；")
        rows.append(row)

    result = pd.DataFrame(rows).drop(columns=["_source_order"], errors="ignore")
    result["原始排名"] = range(1, len(result) + 1)
    return result.reset_index(drop=True)


def evaluate_tradability(row, policy=DEFAULT_SELECTION_POLICY):
    code = _normalize_code(row.get("代號"))
    price = _safe_float(row.get("現價"))
    pct_change = _safe_float(row.get("漲跌幅"))
    turnover = _safe_float(row.get("成交值(億)"))
    volume_ratio = _safe_float(row.get("量比5"))
    intraday_position = _safe_float(row.get("收盤位置"))
    observation_price = _safe_float(row.get("隔日觀察價"))
    chase_limit = _safe_float(row.get("追價上限"))

    stop_distance = None
    if price is not None and price > 0 and observation_price is not None:
        stop_distance = (price - observation_price) / price * 100

    blocks = []
    risks = []
    if not code:
        blocks.append("缺少股票代號")
    if price is None or price <= 0:
        blocks.append("缺少有效現價")
    if pct_change is None:
        blocks.append("缺少漲跌幅")
    elif abs(pct_change) >= policy.near_limit_pct:
        blocks.append("接近漲跌停")
    if turnover is None:
        blocks.append("缺少成交值")
    elif turnover < policy.min_turnover_billion:
        blocks.append(f"成交值低於{policy.min_turnover_billion:g}億")
    if volume_ratio is None:
        blocks.append("缺少量比5")
    elif volume_ratio > policy.max_volume_ratio_5:
        blocks.append("量比過熱")
    if intraday_position is None:
        blocks.append("缺少日內收盤位置")
    elif intraday_position < policy.min_intraday_position:
        blocks.append("日內回落過深")
    if stop_distance is None or stop_distance < 0:
        blocks.append("缺少有效觀察價")
    elif stop_distance > policy.max_stop_distance_pct:
        blocks.append("觀察價距離過遠")
    if price is not None and chase_limit is not None and price > chase_limit:
        blocks.append("現價超過追價上限")

    if turnover is not None and policy.min_turnover_billion <= turnover < policy.turnover_warning_billion:
        risks.append("成交值僅達基本門檻")
    if volume_ratio is not None:
        if volume_ratio < policy.min_healthy_volume_ratio_5:
            risks.append("量能尚未放大")
        elif volume_ratio > policy.urgent_volume_ratio_5:
            risks.append("量能偏急")
    if pct_change is not None and pct_change > policy.hot_pct_change:
        risks.append("短線漲幅偏熱")
    if intraday_position is not None and intraday_position < policy.weak_intraday_position:
        risks.append("收位偏弱")
    if stop_distance is not None and stop_distance > policy.stop_distance_warning_pct:
        risks.append("觀察價偏遠")
    industry = _safe_text(row.get("產業族群"))
    if not industry:
        risks.append("產業資料缺失")

    return {
        "tradable": not blocks,
        "block_reasons": blocks,
        "risk_flags": risks,
        "stop_distance_pct": stop_distance,
    }


def apply_selection_policy(ranked, daily_state=None, policy=DEFAULT_SELECTION_POLICY):
    result = deduplicate_candidates(ranked)
    if result.empty:
        return result

    state = daily_state or {}
    eligible_codes = {_normalize_code(code) for code in state.get("eligible_codes", set())}
    selected_count = int(state.get("selected_count", 0) or 0)
    selected_industry_counts = {
        _safe_text(industry): int(count)
        for industry, count in state.get("selected_industry_counts", {}).items()
        if _safe_text(industry)
    }
    for industry in state.get("selected_industries", set()):
        industry = _safe_text(industry)
        if industry:
            selected_industry_counts.setdefault(industry, 1)

    evaluations = [evaluate_tradability(row, policy) for _, row in result.iterrows()]
    result["可交易"] = [item["tradable"] for item in evaluations]
    result["阻擋原因"] = ["；".join(item["block_reasons"]) for item in evaluations]
    result["風險標記"] = ["；".join(item["risk_flags"]) for item in evaluations]
    result["防守距離%"] = [item["stop_distance_pct"] for item in evaluations]
    result["每日首次合格"] = False
    result["政策入選"] = False
    result["政策排名"] = pd.NA
    result["政策狀態"] = "blocked"
    result["政策版本"] = policy.version

    current_selected = 0
    for index, row in result.iterrows():
        code = _normalize_code(row.get("代號"))
        industry = _safe_text(row.get("產業族群"))
        industry_key = industry or f"__unknown__:{code}"

        if not bool(row["可交易"]):
            continue
        score = _safe_float(row.get("分數"))
        if score is None or score < policy.min_selection_score:
            result.at[index, "政策狀態"] = "score_below_threshold"
            continue
        if code in eligible_codes:
            result.at[index, "政策狀態"] = "duplicate_daily_eligible"
            continue

        result.at[index, "每日首次合格"] = True
        eligible_codes.add(code)
        if selected_count + current_selected >= policy.max_daily_selections:
            result.at[index, "政策狀態"] = "daily_limit"
            continue
        if selected_industry_counts.get(industry_key, 0) >= policy.max_per_industry:
            result.at[index, "政策狀態"] = "industry_limit"
            continue

        current_selected += 1
        selected_industry_counts[industry_key] = selected_industry_counts.get(industry_key, 0) + 1
        result.at[index, "政策入選"] = True
        result.at[index, "政策排名"] = selected_count + current_selected
        result.at[index, "政策狀態"] = "selected"

    return result


def candidate_event_records(ranked, policy=DEFAULT_SELECTION_POLICY):
    records = []
    for _, row in ranked.iterrows():
        strategies = _split_text(row.get("策略"))
        block_reasons = [item for item in _safe_text(row.get("阻擋原因")).split("；") if item]
        risk_flags = [item for item in _safe_text(row.get("風險標記")).split("；") if item]
        snapshot = {str(key): _json_safe(value) for key, value in row.to_dict().items()}
        records.append(
            {
                "code": _normalize_code(row.get("代號")),
                "name": _json_safe(row.get("名稱")),
                "as_of": _json_safe(row.get("報價時間")),
                "industry": _json_safe(row.get("產業族群")),
                "strategies_json": json.dumps(strategies, ensure_ascii=False),
                "strategy_count": _safe_int(row.get("策略數")) or len(strategies),
                "raw_rank": _safe_int(row.get("原始排名")),
                "score": _safe_float(row.get("分數")),
                "signal_price": _safe_float(row.get("現價")),
                "pct_change": _safe_float(row.get("漲跌幅")),
                "turnover_billion": _safe_float(row.get("成交值(億)")),
                "volume_ratio_5": _safe_float(row.get("量比5")),
                "intraday_position": _safe_float(row.get("收盤位置")),
                "observation_price": _safe_float(row.get("隔日觀察價")),
                "chase_limit": _safe_float(row.get("追價上限")),
                "stop_distance_pct": _safe_float(row.get("防守距離%")),
                "tradable": bool(row.get("可交易")),
                "block_reasons_json": json.dumps(block_reasons, ensure_ascii=False),
                "risk_flags_json": json.dumps(risk_flags, ensure_ascii=False),
                "is_first_eligible_event": bool(row.get("每日首次合格")),
                "is_selected": bool(row.get("政策入選")),
                "selection_rank": _safe_int(row.get("政策排名")),
                "selection_status": str(row.get("政策狀態") or "blocked"),
                "policy_version": policy.version,
                "snapshot_json": json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                "policy_config_json": json.dumps(asdict(policy), ensure_ascii=False, sort_keys=True),
            }
        )
    return records
