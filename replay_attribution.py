import argparse
import json
import math
from collections import defaultdict

import pandas as pd

from database import (
    DB_PATH,
    HISTORICAL_ATTRIBUTION_VERSION,
    get_connection,
    get_taipei_now,
    init_db,
)


STRATEGY_KEYS = {
    "順勢突破": ("trend", "順勢突破"),
    "低檔爆量": ("reversal", "低檔爆量"),
    "波段蓄勢": ("wave", "波段蓄勢"),
    "trend": ("trend", "順勢突破"),
    "reversal": ("reversal", "低檔爆量"),
    "wave": ("wave", "波段蓄勢"),
}

DIMENSION_LABELS = {
    "selection": "是否入選",
    "strategy": "策略",
    "score_band": "分數區間",
    "volume_ratio_5": "5 日量比",
    "volume_ratio_20": "20 日量比",
    "turnover_billion": "成交值",
    "stop_distance_pct": "防守距離",
    "market_breadth": "市場廣度",
    "industry_strength": "產業廣度",
    "industry": "產業",
    "selection_status": "政策結果",
    "calendar_year": "年度",
    "calendar_quarter": "季度",
}

SELECTION_LABELS = {
    "selected": "正式入選",
    "rejected": "未入選",
    "all": "全部候選",
}

STATUS_LABELS = {
    "selected": "正式入選",
    "blocked": "資料阻擋",
    "score_below_threshold": "分數不足",
    "daily_limit": "每日名額已滿",
    "industry_limit": "產業名額已滿",
    "duplicate_daily_eligible": "當日重複候選",
}


def _safe_json(value, fallback):
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def _safe_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _bucket(value, ranges, missing_label="資料缺失"):
    number = _safe_float(value)
    if number is None:
        return "missing", missing_label, len(ranges)
    for order, (upper, key, label) in enumerate(ranges):
        if upper is None or number < upper:
            return key, label, order
    return "missing", missing_label, len(ranges)


def _snapshot_value(row, key):
    return row["snapshot"].get(key)


def _dimension_memberships(row):
    selected = bool(row["is_selected"])
    yield "selection", "selected" if selected else "rejected", SELECTION_LABELS[
        "selected" if selected else "rejected"
    ], 0 if selected else 1

    strategies = _safe_json(row.get("strategies_json"), [])
    for strategy in strategies:
        key, label = STRATEGY_KEYS.get(str(strategy), (str(strategy), str(strategy)))
        yield "strategy", key, label, {"trend": 0, "reversal": 1, "wave": 2}.get(key, 9)

    yield (
        "score_band",
        *_bucket(
            row.get("score"),
            [
                (60, "lt_60", "低於 60"),
                (70, "60_69", "60–69"),
                (80, "70_79", "70–79"),
                (None, "gte_80", "80 以上"),
            ],
        ),
    )
    volume_ranges = [
        (0.8, "lt_0_8", "低於 0.8x"),
        (1.2, "0_8_1_19", "0.8–1.19x"),
        (2.0, "1_2_1_99", "1.2–1.99x"),
        (3.5, "2_3_49", "2.0–3.49x"),
        (None, "gte_3_5", "3.5x 以上"),
    ]
    yield "volume_ratio_5", *_bucket(row.get("volume_ratio_5"), volume_ranges)
    yield "volume_ratio_20", *_bucket(row.get("volume_ratio_20"), volume_ranges)
    yield (
        "turnover_billion",
        *_bucket(
            row.get("turnover_billion"),
            [
                (1, "lt_1", "低於 1 億"),
                (3, "1_2_99", "1–2.99 億"),
                (10, "3_9_99", "3–9.99 億"),
                (30, "10_29_99", "10–29.99 億"),
                (None, "gte_30", "30 億以上"),
            ],
        ),
    )
    yield (
        "stop_distance_pct",
        *_bucket(
            row.get("stop_distance_pct"),
            [
                (2, "lt_2", "低於 2%"),
                (5, "2_4_99", "2–4.99%"),
                (8, "5_7_99", "5–7.99%"),
                (12, "8_11_99", "8–11.99%"),
                (None, "gte_12", "12% 以上"),
            ],
        ),
    )
    breadth_ranges = [
        (40, "lt_40", "低於 40%"),
        (50, "40_49_99", "40–49.99%"),
        (60, "50_59_99", "50–59.99%"),
        (None, "gte_60", "60% 以上"),
    ]
    yield "market_breadth", *_bucket(
        _snapshot_value(row, "市場上漲比例"), breadth_ranges
    )
    yield "industry_strength", *_bucket(
        _snapshot_value(row, "產業上漲比例"), breadth_ranges
    )

    industry = str(row.get("industry") or "未知產業")
    yield "industry", industry, industry, 0
    status = str(row.get("selection_status") or "unknown")
    yield "selection_status", status, STATUS_LABELS.get(status, status), 0

    trade_date = pd.Timestamp(row["trade_date"])
    yield "calendar_year", str(trade_date.year), str(trade_date.year), trade_date.year
    quarter = f"{trade_date.year}-Q{trade_date.quarter}"
    yield "calendar_quarter", quarter, quarter, trade_date.year * 10 + trade_date.quarter


def _mean(values):
    usable = [value for value in (_safe_float(item) for item in values) if value is not None]
    return sum(usable) / len(usable) if usable else None


def _metrics(rows):
    net_3d = [
        value
        for value in (_safe_float(row.get("net_return_3d")) for row in rows)
        if value is not None
    ]
    mean_3d = _mean(net_3d)
    standard_error = None
    ci_low = None
    ci_high = None
    if len(net_3d) >= 2:
        standard_error = float(pd.Series(net_3d).std(ddof=1) / math.sqrt(len(net_3d)))
        ci_low = mean_3d - 1.96 * standard_error
        ci_high = mean_3d + 1.96 * standard_error
    successes = [row.get("success_t3") for row in rows if row.get("success_t3") is not None]
    return {
        "sample_count": len(net_3d),
        "selected_count": sum(bool(row.get("is_selected")) for row in rows),
        "mean_net_return_1d": _mean(row.get("fixed_net_return_1d") for row in rows),
        "mean_net_return_3d": mean_3d,
        "mean_net_return_5d": _mean(row.get("fixed_net_return_5d") for row in rows),
        "mean_excess_return_3d": _mean(row.get("excess_return_3d") for row in rows),
        "positive_rate_3d": (
            sum(value > 0 for value in net_3d) / len(net_3d) * 100 if net_3d else None
        ),
        "success_rate_t3": (
            sum(bool(value) for value in successes) / len(successes) * 100
            if successes
            else None
        ),
        "mean_max_drawdown_3d": _mean(row.get("max_drawdown_3d") for row in rows),
        "standard_error_3d": standard_error,
        "ci95_low_3d": ci_low,
        "ci95_high_3d": ci_high,
        "mature_t1": sum(row.get("fixed_net_return_1d") is not None for row in rows),
        "mature_t3": len(net_3d),
        "mature_t5": sum(row.get("fixed_net_return_5d") is not None for row in rows),
    }


def _load_replay_rows(conn, replay_run_id):
    rows = conn.execute(
        """
        SELECT hre.*, hro.fixed_net_return_1d, hro.fixed_net_return_3d,
               hro.fixed_net_return_5d, hro.net_return_3d,
               hro.excess_return_3d, hro.max_drawdown_3d,
               hro.success_t3, hro.matured_horizon
        FROM historical_replay_events hre
        JOIN historical_replay_outcomes hro ON hro.replay_event_id=hre.id
        WHERE hre.replay_run_id=?
          AND hro.entry_status='filled'
          AND hro.matured_horizon >= 3
        ORDER BY hre.trade_date, hre.raw_rank, hre.id
        """,
        (replay_run_id,),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["snapshot"] = _safe_json(item.get("snapshot_json"), {})
        result.append(item)
    return result


def generate_replay_attribution(
    db_path=DB_PATH,
    replay_run_id=None,
    attribution_version=HISTORICAL_ATTRIBUTION_VERSION,
):
    generated_at = get_taipei_now().isoformat(timespec="seconds")
    with get_connection(db_path) as conn:
        init_db(conn)
        if replay_run_id is None:
            latest = conn.execute(
                """
                SELECT id FROM historical_replay_runs
                WHERE status='completed'
                ORDER BY COALESCE(finished_at, started_at) DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
            if not latest:
                raise ValueError("No completed historical replay is available.")
            replay_run_id = latest["id"]
        replay = conn.execute(
            "SELECT id, status FROM historical_replay_runs WHERE id=?",
            (replay_run_id,),
        ).fetchone()
        if not replay:
            raise ValueError(f"Historical replay run {replay_run_id} does not exist.")
        if replay["status"] != "completed":
            raise ValueError("Attribution requires a completed historical replay.")
        rows = _load_replay_rows(conn, replay_run_id)

        groups = defaultdict(list)
        bucket_meta = {}
        for row in rows:
            row_scope = "selected" if bool(row["is_selected"]) else "rejected"
            for dimension, key, label, order in _dimension_memberships(row):
                bucket_meta[(dimension, key)] = (label, order)
                groups[(dimension, key, "all")].append(row)
                groups[(dimension, key, row_scope)].append(row)

        records = []
        for (dimension, key, scope), grouped_rows in sorted(groups.items()):
            label, sort_order = bucket_meta[(dimension, key)]
            metrics = _metrics(grouped_rows)
            if metrics["sample_count"] == 0:
                continue
            records.append(
                {
                    "replay_run_id": replay_run_id,
                    "attribution_version": attribution_version,
                    "generated_at": generated_at,
                    "dimension": dimension,
                    "bucket_key": key,
                    "bucket_label": label,
                    "sort_order": sort_order,
                    "selection_scope": scope,
                    **metrics,
                }
            )

        conn.execute(
            "DELETE FROM historical_replay_attributions "
            "WHERE replay_run_id=? AND attribution_version=?",
            (replay_run_id, attribution_version),
        )
        conn.executemany(
            """
            INSERT INTO historical_replay_attributions (
                replay_run_id, attribution_version, generated_at, dimension,
                bucket_key, bucket_label, sort_order, selection_scope,
                sample_count, selected_count, mean_net_return_1d,
                mean_net_return_3d, mean_net_return_5d,
                mean_excess_return_3d, positive_rate_3d, success_rate_t3,
                mean_max_drawdown_3d, standard_error_3d, ci95_low_3d,
                ci95_high_3d, metrics_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    record["replay_run_id"],
                    record["attribution_version"],
                    record["generated_at"],
                    record["dimension"],
                    record["bucket_key"],
                    record["bucket_label"],
                    record["sort_order"],
                    record["selection_scope"],
                    record["sample_count"],
                    record["selected_count"],
                    record["mean_net_return_1d"],
                    record["mean_net_return_3d"],
                    record["mean_net_return_5d"],
                    record["mean_excess_return_3d"],
                    record["positive_rate_3d"],
                    record["success_rate_t3"],
                    record["mean_max_drawdown_3d"],
                    record["standard_error_3d"],
                    record["ci95_low_3d"],
                    record["ci95_high_3d"],
                    json.dumps(
                        {
                            "dimensionLabel": DIMENSION_LABELS.get(
                                record["dimension"], record["dimension"]
                            ),
                            "selectionLabel": SELECTION_LABELS[record["selection_scope"]],
                            "matureT1": record["mature_t1"],
                            "matureT3": record["mature_t3"],
                            "matureT5": record["mature_t5"],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                )
                for record in records
            ],
        )

    return {
        "replay_run_id": replay_run_id,
        "attribution_version": attribution_version,
        "generated_at": generated_at,
        "source_rows": len(rows),
        "attribution_rows": len(records),
        "dimensions": sorted({record["dimension"] for record in records}),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Generate diagnostic factor attribution for a completed replay."
    )
    parser.add_argument("--db-path", default=str(DB_PATH))
    parser.add_argument("--replay-run-id", type=int)
    args = parser.parse_args()
    result = generate_replay_attribution(
        db_path=args.db_path,
        replay_run_id=args.replay_run_id,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
