import argparse
import json
import math
from pathlib import Path

import pandas as pd

from institutional_research import load_institutional_research_frame
from research_evaluation import replay_temporal_partitions


ATTRIBUTION_VERSION = "institutional_segment_attribution_v1"
DISCOVERY_EMBARGO_DATES = 10
MIN_SIGNAL_ROWS = 200
MIN_CONTROL_ROWS = 500
MIN_PAIRED_DATES = 100

FLOW_RULES = {
    "foreign_accumulation": (
        ("foreign_net_z20", ">=", 1.0),
        ("foreign_buy_ratio_5d", ">=", 0.60),
    ),
    "trust_accumulation": (
        ("trust_net_z20", ">=", 1.0),
        ("trust_buy_ratio_5d", ">=", 0.60),
    ),
    "foreign_trust_consensus_buy": (
        ("foreign_net_z20", ">", 0.0),
        ("trust_net_z20", ">", 0.0),
        ("agreement_score_1d", ">=", 2.0),
    ),
    "broad_consensus_buy": (
        ("total_net_z20", ">=", 1.0),
        ("total_buy_ratio_5d", ">=", 0.60),
        ("agreement_score_1d", ">=", 2.0),
    ),
}

SEGMENT_RULES = {
    "all_candidates": (),
    "strategy_trend": (("strategy_trend", "==", 1.0),),
    "strategy_reversal": (("strategy_reversal", "==", 1.0),),
    "strategy_wave": (("strategy_wave", "==", 1.0),),
    "market_breadth_bullish": (("market_up_ratio", ">=", 55.0),),
    "market_breadth_neutral": (
        ("market_up_ratio", ">=", 45.0),
        ("market_up_ratio", "<", 55.0),
    ),
    "market_breadth_weak": (("market_up_ratio", "<", 45.0),),
    "industry_breadth_strong": (("industry_up_ratio", ">=", 55.0),),
    "industry_breadth_neutral": (
        ("industry_up_ratio", ">=", 45.0),
        ("industry_up_ratio", "<", 55.0),
    ),
    "industry_breadth_weak": (("industry_up_ratio", "<", 45.0),),
    "turnover_5_to_10_billion": (
        ("turnover_billion", ">=", 5.0),
        ("turnover_billion", "<", 10.0),
    ),
    "turnover_10_to_30_billion": (
        ("turnover_billion", ">=", 10.0),
        ("turnover_billion", "<", 30.0),
    ),
    "turnover_30_billion_plus": (("turnover_billion", ">=", 30.0),),
    "volume_confirmed": (
        ("volume_ratio_20", ">=", 1.20),
        ("volume_ratio_20", "<=", 3.0),
    ),
    "volume_neutral": (
        ("volume_ratio_20", ">=", 0.80),
        ("volume_ratio_20", "<", 1.20),
    ),
    "volume_quiet": (("volume_ratio_20", "<", 0.80),),
    "volume_overheated": (("volume_ratio_20", ">", 3.0),),
}


def _rule_mask(frame, rules):
    mask = pd.Series(True, index=frame.index)
    operators = {
        ">=": lambda values, threshold: values >= threshold,
        "<=": lambda values, threshold: values <= threshold,
        ">": lambda values, threshold: values > threshold,
        "<": lambda values, threshold: values < threshold,
        "==": lambda values, threshold: values == threshold,
    }
    for column, operator, threshold in rules:
        if column not in frame.columns:
            raise ValueError(f"Institutional attribution column not found: {column}")
        values = pd.to_numeric(frame[column], errors="coerce")
        mask &= values.notna() & operators[operator](values, threshold)
    return mask


def _paired_daily_lift(signal, control, column):
    signal_daily = signal.groupby("trade_date", sort=True)[column].mean()
    control_daily = control.groupby("trade_date", sort=True)[column].mean()
    paired = pd.concat(
        [signal_daily.rename("signal"), control_daily.rename("control")],
        axis=1,
        join="inner",
    ).dropna()
    differences = paired["signal"] - paired["control"]
    count = int(len(differences))
    mean = float(differences.mean()) if count else None
    standard_error = (
        float(differences.std(ddof=1) / math.sqrt(count)) if count >= 2 else None
    )
    return {
        "paired_dates": count,
        "signal_mean_excess_return": (
            float(paired["signal"].mean()) if count else None
        ),
        "control_mean_excess_return": (
            float(paired["control"].mean()) if count else None
        ),
        "mean_excess_lift": mean,
        "positive_lift_rate": (
            float((differences > 0).mean()) if count else None
        ),
        "standard_error": standard_error,
        "ci95_low": (
            mean - 1.96 * standard_error
            if mean is not None and standard_error is not None
            else None
        ),
        "ci95_high": (
            mean + 1.96 * standard_error
            if mean is not None and standard_error is not None
            else None
        ),
    }


def build_institutional_attribution(dataset_path):
    frame = load_institutional_research_frame(dataset_path)
    if frame.empty:
        raise ValueError("No complete institutional replay samples are available.")
    partitions = replay_temporal_partitions(
        frame, embargo_trade_dates=DISCOVERY_EMBARGO_DATES
    )
    development_dates = set(partitions["development"])
    discovery = frame[
        frame["trade_date"].isin(development_dates) & (frame["tradable"] == 1)
    ].copy()
    rows = []
    for flow_key, flow_rules in FLOW_RULES.items():
        flow_mask = _rule_mask(discovery, flow_rules)
        for segment_key, segment_rules in SEGMENT_RULES.items():
            segment = discovery[_rule_mask(discovery, segment_rules)].copy()
            signal = segment[flow_mask.loc[segment.index]].copy()
            control = segment[~flow_mask.loc[segment.index]].copy()
            horizons = {}
            eligible = len(signal) >= MIN_SIGNAL_ROWS and len(control) >= MIN_CONTROL_ROWS
            for horizon in (5, 10):
                column = f"next_open_excess_return_{horizon}d"
                metrics = _paired_daily_lift(signal, control, column)
                horizons[f"t{horizon}"] = metrics
                eligible = (
                    eligible
                    and metrics["paired_dates"] >= MIN_PAIRED_DATES
                    and metrics["mean_excess_lift"] is not None
                    and metrics["mean_excess_lift"] > 0
                )
            score = sum(
                horizons[key]["mean_excess_lift"] or 0.0 for key in ("t5", "t10")
            )
            rows.append(
                {
                    "flow_key": flow_key,
                    "segment_key": segment_key,
                    "signal_rows": int(len(signal)),
                    "control_rows": int(len(control)),
                    "eligible_for_confirmation": bool(eligible),
                    "discovery_score": float(score),
                    "horizons": horizons,
                }
            )
    rows.sort(
        key=lambda row: (
            not row["eligible_for_confirmation"],
            -row["discovery_score"],
            row["flow_key"],
            row["segment_key"],
        )
    )
    selected = next(
        (row for row in rows if row["eligible_for_confirmation"]), None
    )
    return {
        "attribution_version": ATTRIBUTION_VERSION,
        "evaluation_scope": "historical_development_discovery_only",
        "development_start": min(partitions["development"]),
        "development_end": max(partitions["development"]),
        "development_trade_dates": len(partitions["development"]),
        "development_rows": int(len(discovery)),
        "validation_evaluated": False,
        "holdout_evaluated": False,
        "reserved_validation_trade_dates": len(partitions["validation"]),
        "reserved_holdout_trade_dates": len(partitions["holdout"]),
        "selection_policy": {
            "min_signal_rows": MIN_SIGNAL_ROWS,
            "min_control_rows": MIN_CONTROL_ROWS,
            "min_paired_dates_per_horizon": MIN_PAIRED_DATES,
            "requires_positive_t5_and_t10_excess_lift": True,
        },
        "selected_confirmation_candidate": selected,
        "comparisons": rows,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Build a development-only institutional segment attribution report."
    )
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    report = build_institutional_attribution(args.dataset)
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    print(payload, end="")


if __name__ == "__main__":
    main()
