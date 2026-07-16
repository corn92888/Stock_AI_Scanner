import argparse
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

from database import (
    CANDIDATE_EXECUTION_VERSION,
    DB_PATH,
    LEGACY_CANDIDATE_EXECUTION_VERSION,
    get_connection,
    get_git_commit,
    get_taipei_now,
    init_db,
)


EVALUATION_FAMILY = "walk_forward_after_costs_v1"
REPLAY_EVALUATION_FAMILY = "replay_temporal_holdout_after_costs_v1"
REPLAY_EXECUTION_VERSION = "point_in_time_eod_replay_v2"
DEFAULT_REPLAY_DATASET = Path("data/replay_training_samples.csv.gz")
REPLAY_DEVELOPMENT_FRACTION = 0.60
REPLAY_VALIDATION_FRACTION = 0.20
REPLAY_EMBARGO_TRADE_DATES = 3
DECISION_HORIZON = 3
STRATEGY_ALIASES = {
    "trend": {"trend", "順勢突破"},
    "reversal": {"reversal", "低檔爆量"},
    "wave": {"wave", "波段蓄勢"},
}


@dataclass(frozen=True)
class PromotionGates:
    min_trade_dates: int = 120
    min_trades: int = 300
    min_mean_net_return: float = 0.0
    min_mean_excess_return: float = 0.0
    min_probabilistic_sharpe: float = 0.95
    max_drawdown: float = -12.0
    min_profitable_fold_rate: float = 0.60


@dataclass(frozen=True)
class ExperimentSpec:
    key: str
    name: str
    hypothesis: str
    strategy_family: str
    execution_version: str
    selector: str
    strategy: str | None = None
    filters: tuple[tuple[str, str, float], ...] = ()
    parameters: tuple[tuple[str, object], ...] = ()
    evaluation_family: str = EVALUATION_FAMILY


BASELINE_EXPERIMENTS = (
    ExperimentSpec(
        key="legacy_formal_rule_v1",
        name="Legacy formal rule baseline",
        hypothesis="Measure the frozen formal rule policy under the legacy next-open execution.",
        strategy_family="rule_baseline",
        execution_version=LEGACY_CANDIDATE_EXECUTION_VERSION,
        selector="formal",
    ),
    ExperimentSpec(
        key="mode_aligned_formal_rule_v2",
        name="Mode-aligned formal rule baseline",
        hypothesis="Measure the same policy with entry timing aligned to the scan mode.",
        strategy_family="rule_baseline",
        execution_version=CANDIDATE_EXECUTION_VERSION,
        selector="formal",
    ),
    ExperimentSpec(
        key="mode_aligned_trend_v2",
        name="Mode-aligned trend sleeve",
        hypothesis="Test whether trend candidates create positive after-cost excess returns.",
        strategy_family="trend",
        execution_version=CANDIDATE_EXECUTION_VERSION,
        selector="strategy",
        strategy="trend",
    ),
    ExperimentSpec(
        key="mode_aligned_reversal_v2",
        name="Mode-aligned reversal sleeve",
        hypothesis="Test whether reversal candidates create positive after-cost excess returns.",
        strategy_family="reversal",
        execution_version=CANDIDATE_EXECUTION_VERSION,
        selector="strategy",
        strategy="reversal",
    ),
    ExperimentSpec(
        key="mode_aligned_wave_v2",
        name="Mode-aligned wave sleeve",
        hypothesis="Test whether consolidation candidates create positive after-cost excess returns.",
        strategy_family="wave",
        execution_version=CANDIDATE_EXECUTION_VERSION,
        selector="strategy",
        strategy="wave",
    ),
)


REPLAY_OVERLAY_EXPERIMENTS = (
    ExperimentSpec(
        key="replay_formal_rule_v2",
        name="Five-year formal rule baseline",
        hypothesis="Measure the frozen formal rule on the final embargoed replay holdout.",
        strategy_family="replay_baseline",
        execution_version=REPLAY_EXECUTION_VERSION,
        selector="replay_formal",
        evaluation_family=REPLAY_EVALUATION_FAMILY,
    ),
    ExperimentSpec(
        key="replay_market_confirmation_v1",
        name="Market breadth confirmation",
        hypothesis="Avoid formal entries when broad-market participation and average return are weak.",
        strategy_family="market_regime",
        execution_version=REPLAY_EXECUTION_VERSION,
        selector="replay_formal",
        filters=(("market_up_ratio", ">=", 50.0), ("market_avg_return", ">=", 0.0)),
        evaluation_family=REPLAY_EVALUATION_FAMILY,
    ),
    ExperimentSpec(
        key="replay_industry_confirmation_v1",
        name="Industry breadth confirmation",
        hypothesis="Require positive industry participation before accepting a formal entry.",
        strategy_family="industry_confirmation",
        execution_version=REPLAY_EXECUTION_VERSION,
        selector="replay_formal",
        filters=(("industry_up_ratio", ">=", 50.0), ("industry_avg_return", ">=", 0.0)),
        evaluation_family=REPLAY_EVALUATION_FAMILY,
    ),
    ExperimentSpec(
        key="replay_volume_expansion_v1",
        name="Controlled volume expansion",
        hypothesis="Test whether 20-day volume expansion adds edge when extreme bursts are excluded.",
        strategy_family="volume_confirmation",
        execution_version=REPLAY_EXECUTION_VERSION,
        selector="replay_formal",
        filters=(
            ("volume_ratio_20", ">=", 1.5),
            ("volume_ratio_20", "<=", 5.0),
            ("volume_ratio_5", ">=", 1.2),
            ("volume_ratio_5", "<=", 6.0),
        ),
        evaluation_family=REPLAY_EVALUATION_FAMILY,
    ),
    ExperimentSpec(
        key="replay_balanced_volume_v1",
        name="Balanced volume confirmation",
        hypothesis="Prefer moderate volume confirmation instead of low participation or exhaustion spikes.",
        strategy_family="volume_quality",
        execution_version=REPLAY_EXECUTION_VERSION,
        selector="replay_formal",
        filters=(
            ("volume_ratio_20", ">=", 0.8),
            ("volume_ratio_20", "<=", 3.0),
            ("volume_ratio_5", ">=", 1.0),
            ("volume_ratio_5", "<=", 6.0),
        ),
        evaluation_family=REPLAY_EVALUATION_FAMILY,
    ),
    ExperimentSpec(
        key="replay_extension_control_v1",
        name="Price extension control",
        hypothesis="Avoid formal entries after an excessive same-day move or urgent short-term volume burst.",
        strategy_family="extension_control",
        execution_version=REPLAY_EXECUTION_VERSION,
        selector="replay_formal",
        filters=(("pct_change", "<=", 6.5), ("volume_ratio_5", "<=", 6.0)),
        evaluation_family=REPLAY_EVALUATION_FAMILY,
    ),
    ExperimentSpec(
        key="replay_breadth_consensus_v1",
        name="Market and industry breadth consensus",
        hypothesis="Accept formal entries only when both the market and industry have positive participation.",
        strategy_family="breadth_consensus",
        execution_version=REPLAY_EXECUTION_VERSION,
        selector="replay_formal",
        filters=(
            ("market_up_ratio", ">=", 50.0),
            ("market_avg_return", ">=", 0.0),
            ("industry_up_ratio", ">=", 50.0),
            ("industry_avg_return", ">=", 0.0),
        ),
        evaluation_family=REPLAY_EVALUATION_FAMILY,
    ),
    ExperimentSpec(
        key="replay_quality_stack_v1",
        name="Breadth, volume, and extension quality stack",
        hypothesis="Combine independent participation, volume-quality, and overextension controls.",
        strategy_family="quality_stack",
        execution_version=REPLAY_EXECUTION_VERSION,
        selector="replay_formal",
        filters=(
            ("market_up_ratio", ">=", 50.0),
            ("market_avg_return", ">=", 0.0),
            ("industry_up_ratio", ">=", 50.0),
            ("industry_avg_return", ">=", 0.0),
            ("volume_ratio_20", ">=", 0.8),
            ("volume_ratio_20", "<=", 3.0),
            ("volume_ratio_5", ">=", 1.0),
            ("volume_ratio_5", "<=", 6.0),
            ("pct_change", "<=", 6.5),
        ),
        evaluation_family=REPLAY_EVALUATION_FAMILY,
    ),
)


def _decode_strategies(value):
    try:
        result = json.loads(value or "[]")
        return {str(item).strip().lower() for item in result}
    except (TypeError, ValueError, json.JSONDecodeError):
        return set()


def _below_or_missing(value, threshold, inclusive=False):
    if value is None or not math.isfinite(float(value)):
        return True
    return value <= threshold if inclusive else value < threshold


def strategy_matches(value, strategy):
    observed = _decode_strategies(value)
    aliases = STRATEGY_ALIASES.get(str(strategy).lower(), {str(strategy).lower()})
    return bool(observed.intersection(aliases))


def register_experiment(spec, db_path=DB_PATH):
    now = get_taipei_now().isoformat(timespec="seconds")
    config = {
        "selector": spec.selector,
        "strategy": spec.strategy,
        "filters": [list(item) for item in spec.filters],
        "parameters": dict(spec.parameters),
        "evaluation_family": spec.evaluation_family,
    }
    with get_connection(db_path) as conn:
        init_db(conn)
        conn.execute(
            """
            INSERT INTO research_experiments (
                experiment_key, name, hypothesis, strategy_family,
                execution_version, objective, status, config_json,
                git_commit, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'after_cost_excess_return', 'candidate', ?, ?, ?, ?)
            ON CONFLICT(experiment_key) DO UPDATE SET
                name=excluded.name, hypothesis=excluded.hypothesis,
                strategy_family=excluded.strategy_family,
                execution_version=excluded.execution_version,
                objective=excluded.objective, config_json=excluded.config_json,
                git_commit=excluded.git_commit, updated_at=excluded.updated_at
            """,
            (
                spec.key,
                spec.name,
                spec.hypothesis,
                spec.strategy_family,
                spec.execution_version,
                json.dumps(config, sort_keys=True),
                get_git_commit(),
                now,
                now,
            ),
        )
        row = conn.execute(
            "SELECT id FROM research_experiments WHERE experiment_key=?",
            (spec.key,),
        ).fetchone()
    return int(row["id"])


def load_experiment_frame(spec, db_path=DB_PATH):
    with get_connection(db_path) as conn:
        init_db(conn)
        frame = pd.read_sql_query(
            """
            SELECT sr.trade_date, sr.mode, ce.code, ce.is_selected,
                   ce.strategies_json, co.net_return_3d,
                   co.excess_return_3d, co.max_drawdown_3d,
                   co.success_t3, co.entry_status
            FROM candidate_events ce
            JOIN scan_runs sr ON sr.id=ce.run_id
            JOIN candidate_outcomes co ON co.candidate_id=ce.id
            WHERE co.execution_version=?
              AND co.entry_status='filled'
              AND co.matured_horizon>=3
              AND co.net_return_3d IS NOT NULL
              AND co.excess_return_3d IS NOT NULL
            ORDER BY sr.trade_date, ce.as_of, ce.id
            """,
            conn,
            params=(spec.execution_version,),
        )
    if frame.empty:
        return frame
    if spec.selector == "formal":
        return frame[frame["is_selected"].astype(int) == 1].copy()
    if spec.selector == "strategy":
        return frame[
            frame["strategies_json"].map(
                lambda value: strategy_matches(value, spec.strategy)
            )
        ].copy()
    return frame


def _probabilistic_sharpe(returns, benchmark_sharpe=0.0):
    values = pd.Series(returns, dtype=float).dropna()
    if len(values) < 3 or values.std(ddof=1) <= 0:
        return None
    daily_sharpe = values.mean() / values.std(ddof=1)
    skew = float(values.skew()) if len(values) >= 3 else 0.0
    kurtosis = float(values.kurtosis()) + 3.0 if len(values) >= 4 else 3.0
    denominator = math.sqrt(
        max(1e-12, (1 - skew * daily_sharpe + ((kurtosis - 1) / 4) * daily_sharpe**2) / (len(values) - 1))
    )
    z_score = (daily_sharpe - benchmark_sharpe / math.sqrt(252)) / denominator
    return NormalDist().cdf(z_score)


def _max_drawdown(daily_returns):
    values = pd.Series(daily_returns, dtype=float).fillna(0.0) / 100.0
    if values.empty:
        return None
    equity = (1.0 + values).cumprod()
    drawdown = (equity / equity.cummax() - 1.0) * 100.0
    return float(drawdown.min())


def evaluate_frame(
    frame,
    gates=None,
    fold_count=5,
    decision_horizon=DECISION_HORIZON,
    observation_dates=None,
):
    gates = gates or PromotionGates()
    if frame.empty:
        metrics = {
            "trade_dates": 0,
            "trades": 0,
            "folds": 0,
            "mean_net_return": None,
            "mean_excess_return": None,
            "positive_rate": None,
            "annualized_sharpe": None,
            "probabilistic_sharpe": None,
            "max_drawdown": None,
            "profitable_fold_rate": None,
            "decision_dates": len(set(observation_dates or [])),
            "participation_rate_pct": 0.0,
            "mean_daily_net_return": 0.0 if observation_dates else None,
            "mean_daily_excess_return": 0.0 if observation_dates else None,
        }
    else:
        daily = frame.groupby("trade_date", sort=True).agg(
            net_return=("net_return_3d", "mean"),
            excess_return=("excess_return_3d", "mean"),
        )
        active_trade_dates = int(len(daily))
        if observation_dates is not None:
            decision_dates = sorted(
                set(str(value) for value in observation_dates).union(daily.index.astype(str))
            )
            daily.index = daily.index.astype(str)
            daily = daily.reindex(decision_dates, fill_value=0.0)
        daily["portfolio_return"] = daily["net_return"] / decision_horizon
        daily["portfolio_excess"] = daily["excess_return"] / decision_horizon
        std = (
            float(daily["portfolio_return"].std(ddof=1))
            if len(daily) > 1
            else 0.0
        )
        sharpe = (
            float(daily["portfolio_return"].mean() / std * math.sqrt(252))
            if std > 0
            else None
        )
        fold_indices = np.array_split(
            np.arange(len(daily)), min(fold_count, len(daily))
        )
        folds = [daily.iloc[index] for index in fold_indices if len(index)]
        profitable = [
            float(fold["portfolio_excess"].mean()) > 0 for fold in folds
        ]
        metrics = {
            "trade_dates": active_trade_dates,
            "trades": int(len(frame)),
            "folds": int(len(folds)),
            "mean_net_return": float(frame["net_return_3d"].mean()),
            "mean_excess_return": float(frame["excess_return_3d"].mean()),
            "positive_rate": float((frame["net_return_3d"] > 0).mean() * 100),
            "annualized_sharpe": sharpe,
            "probabilistic_sharpe": _probabilistic_sharpe(
                daily["portfolio_return"]
            ),
            "max_drawdown": _max_drawdown(daily["portfolio_return"]),
            "profitable_fold_rate": float(np.mean(profitable)) if profitable else None,
            "decision_dates": int(len(daily)),
            "participation_rate_pct": float(active_trade_dates / len(daily) * 100),
            "mean_daily_net_return": float(daily["net_return"].mean()),
            "mean_daily_excess_return": float(daily["excess_return"].mean()),
        }

    reasons = []
    if metrics["trade_dates"] < gates.min_trade_dates:
        reasons.append("insufficient_trade_dates")
    if metrics["trades"] < gates.min_trades:
        reasons.append("insufficient_trades")
    if _below_or_missing(
        metrics["mean_net_return"],
        gates.min_mean_net_return,
        inclusive=True,
    ):
        reasons.append("non_positive_net_return")
    if _below_or_missing(
        metrics["mean_excess_return"],
        gates.min_mean_excess_return,
        inclusive=True,
    ):
        reasons.append("non_positive_excess_return")
    if _below_or_missing(
        metrics["probabilistic_sharpe"], gates.min_probabilistic_sharpe
    ):
        reasons.append("probabilistic_sharpe_below_gate")
    if metrics["max_drawdown"] is None or metrics["max_drawdown"] < gates.max_drawdown:
        reasons.append("drawdown_gate_failed")
    if _below_or_missing(
        metrics["profitable_fold_rate"], gates.min_profitable_fold_rate
    ):
        reasons.append("fold_stability_gate_failed")
    return metrics, reasons


def save_evaluation(experiment_id, spec, metrics, reasons, gates=None, db_path=DB_PATH):
    gates = gates or PromotionGates()
    now = get_taipei_now().isoformat(timespec="seconds")
    sample_end = metrics.get("sample_end")
    fingerprint = metrics.get("evaluation_fingerprint")
    fingerprint_suffix = f":d{fingerprint}" if fingerprint else ""
    evaluation_version = (
        f"{spec.evaluation_family}:{spec.execution_version}:"
        f"{sample_end or 'empty'}:n{metrics['trades']}{fingerprint_suffix}"
    )
    payload = {**metrics, "gates": asdict(gates)}
    with get_connection(db_path) as conn:
        init_db(conn)
        conn.execute(
            """
            INSERT INTO experiment_evaluations (
                experiment_id, evaluation_version, evaluated_at,
                sample_start, sample_end, trade_dates, trades, folds,
                mean_net_return, mean_excess_return, positive_rate,
                annualized_sharpe, probabilistic_sharpe, max_drawdown,
                profitable_fold_rate, qualified, rejection_reasons_json,
                metrics_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(experiment_id, evaluation_version) DO UPDATE SET
                evaluated_at=excluded.evaluated_at,
                sample_start=excluded.sample_start, sample_end=excluded.sample_end,
                trade_dates=excluded.trade_dates, trades=excluded.trades,
                folds=excluded.folds, mean_net_return=excluded.mean_net_return,
                mean_excess_return=excluded.mean_excess_return,
                positive_rate=excluded.positive_rate,
                annualized_sharpe=excluded.annualized_sharpe,
                probabilistic_sharpe=excluded.probabilistic_sharpe,
                max_drawdown=excluded.max_drawdown,
                profitable_fold_rate=excluded.profitable_fold_rate,
                qualified=excluded.qualified,
                rejection_reasons_json=excluded.rejection_reasons_json,
                metrics_json=excluded.metrics_json
            """,
            (
                experiment_id,
                evaluation_version,
                now,
                metrics.get("sample_start"),
                sample_end,
                metrics["trade_dates"],
                metrics["trades"],
                metrics["folds"],
                metrics["mean_net_return"],
                metrics["mean_excess_return"],
                metrics["positive_rate"],
                metrics["annualized_sharpe"],
                metrics["probabilistic_sharpe"],
                metrics["max_drawdown"],
                metrics["profitable_fold_rate"],
                int(not reasons),
                json.dumps(reasons, sort_keys=True),
                json.dumps(payload, sort_keys=True),
            ),
        )
    return evaluation_version


def evaluate_experiment(spec, gates=None, db_path=DB_PATH):
    experiment_id = register_experiment(spec, db_path=db_path)
    frame = load_experiment_frame(spec, db_path=db_path)
    metrics, reasons = evaluate_frame(frame, gates=gates)
    if not frame.empty:
        metrics["sample_start"] = str(frame["trade_date"].min())
        metrics["sample_end"] = str(frame["trade_date"].max())
    else:
        metrics["sample_start"] = None
        metrics["sample_end"] = None
    version = save_evaluation(
        experiment_id,
        spec,
        metrics,
        reasons,
        gates=gates,
        db_path=db_path,
    )
    return {
        "experimentKey": spec.key,
        "evaluationVersion": version,
        "qualified": not reasons,
        "rejectionReasons": reasons,
        **metrics,
    }


def run_baseline_evaluations(db_path=DB_PATH, gates=None):
    return [
        evaluate_experiment(spec, gates=gates, db_path=db_path)
        for spec in BASELINE_EXPERIMENTS
    ]


def _file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_replay_formal_frame(dataset_path=DEFAULT_REPLAY_DATASET):
    dataset_path = Path(dataset_path)
    if not dataset_path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(dataset_path)
    required = {
        "trade_date",
        "code",
        "rule_selected",
        "net_return_3d",
        "excess_return_3d",
        "max_drawdown_3d",
    }
    required.update(
        column
        for spec in REPLAY_OVERLAY_EXPERIMENTS
        for column, _, _ in spec.filters
    )
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(
            "Replay training dataset missing evaluation columns: "
            + ", ".join(missing)
        )
    numeric_columns = sorted(
        (required - {"trade_date", "code"}) | {"rule_selected"}
    )
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["trade_date"] = frame["trade_date"].astype(str)
    frame = frame[
        (frame["rule_selected"] == 1)
        & frame["net_return_3d"].notna()
        & frame["excess_return_3d"].notna()
        & frame["max_drawdown_3d"].notna()
    ].copy()
    return frame.sort_values(["trade_date", "code"], kind="stable")


def apply_experiment_filters(frame, spec):
    result = frame.copy()
    operators = {
        ">=": lambda series, value: series >= value,
        "<=": lambda series, value: series <= value,
        ">": lambda series, value: series > value,
        "<": lambda series, value: series < value,
    }
    for column, operator, threshold in spec.filters:
        if column not in result.columns:
            raise ValueError(f"Replay experiment column not found: {column}")
        if operator not in operators:
            raise ValueError(f"Unsupported replay experiment operator: {operator}")
        values = pd.to_numeric(result[column], errors="coerce")
        result = result[values.notna() & operators[operator](values, threshold)]
    return result.copy()


def replay_temporal_partitions(frame, embargo_trade_dates=REPLAY_EMBARGO_TRADE_DATES):
    trade_dates = sorted(frame["trade_date"].dropna().astype(str).unique())
    minimum_dates = embargo_trade_dates * 2 + 3
    if len(trade_dates) < minimum_dates:
        raise ValueError(
            f"Replay evaluation requires at least {minimum_dates} trade dates."
        )
    development_end = int(len(trade_dates) * REPLAY_DEVELOPMENT_FRACTION)
    validation_end = int(
        len(trade_dates)
        * (REPLAY_DEVELOPMENT_FRACTION + REPLAY_VALIDATION_FRACTION)
    )
    first_embargo_end = development_end + embargo_trade_dates
    second_embargo_end = validation_end + embargo_trade_dates
    if first_embargo_end >= validation_end or second_embargo_end >= len(trade_dates):
        raise ValueError("Replay temporal split is too short after embargo periods.")
    return {
        "development": trade_dates[:development_end],
        "development_validation_embargo": trade_dates[
            development_end:first_embargo_end
        ],
        "validation": trade_dates[first_embargo_end:validation_end],
        "validation_holdout_embargo": trade_dates[
            validation_end:second_embargo_end
        ],
        "holdout": trade_dates[second_embargo_end:],
    }


def _phase_evaluation(frame, trade_dates, gates):
    phase = frame[frame["trade_date"].isin(set(trade_dates))].copy()
    metrics, reasons = evaluate_frame(phase, gates=gates)
    metrics["sample_start"] = str(phase["trade_date"].min()) if not phase.empty else None
    metrics["sample_end"] = str(phase["trade_date"].max()) if not phase.empty else None
    return phase, metrics, reasons


def evaluate_replay_overlay(
    spec,
    base_frame,
    partitions,
    dataset_fingerprint,
    gates=None,
    db_path=DB_PATH,
):
    gates = gates or PromotionGates()
    experiment_id = register_experiment(spec, db_path=db_path)
    filtered = apply_experiment_filters(base_frame, spec)
    phase_results = {}
    for phase_name in ("development", "validation", "holdout"):
        _, phase_metrics, phase_reasons = _phase_evaluation(
            filtered, partitions[phase_name], gates
        )
        phase_results[phase_name] = {
            "metrics": phase_metrics,
            "rejection_reasons": phase_reasons,
        }

    holdout_metrics = dict(phase_results["holdout"]["metrics"])
    holdout_reasons = list(phase_results["holdout"]["rejection_reasons"])
    if phase_results["development"]["rejection_reasons"]:
        holdout_reasons.append("development_gate_failed")
    if phase_results["validation"]["rejection_reasons"]:
        holdout_reasons.append("validation_gate_failed")
    holdout_reasons = list(dict.fromkeys(holdout_reasons))
    holdout_metrics.update(
        {
            "evaluation_fingerprint": dataset_fingerprint[:10],
            "dataset_fingerprint": dataset_fingerprint,
            "dataset_rows": int(len(base_frame)),
            "filters": [list(item) for item in spec.filters],
            "temporal_split": {
                "development_fraction": REPLAY_DEVELOPMENT_FRACTION,
                "validation_fraction": REPLAY_VALIDATION_FRACTION,
                "embargo_trade_dates": REPLAY_EMBARGO_TRADE_DATES,
                "development_validation_embargo": partitions[
                    "development_validation_embargo"
                ],
                "validation_holdout_embargo": partitions[
                    "validation_holdout_embargo"
                ],
            },
            "phase_results": phase_results,
        }
    )
    version = save_evaluation(
        experiment_id,
        spec,
        holdout_metrics,
        holdout_reasons,
        gates=gates,
        db_path=db_path,
    )
    return {
        "experimentKey": spec.key,
        "evaluationVersion": version,
        "qualified": not holdout_reasons,
        "rejectionReasons": holdout_reasons,
        **holdout_metrics,
    }


def run_replay_overlay_evaluations(
    db_path=DB_PATH,
    dataset_path=DEFAULT_REPLAY_DATASET,
    gates=None,
    specs=REPLAY_OVERLAY_EXPERIMENTS,
):
    dataset_path = Path(dataset_path)
    base_frame = load_replay_formal_frame(dataset_path)
    if base_frame.empty:
        return []
    partitions = replay_temporal_partitions(base_frame)
    dataset_fingerprint = _file_sha256(dataset_path)
    return [
        evaluate_replay_overlay(
            spec,
            base_frame,
            partitions,
            dataset_fingerprint,
            gates=gates,
            db_path=db_path,
        )
        for spec in specs
    ]


def main():
    parser = argparse.ArgumentParser(description="Evaluate registered strategy baselines.")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--replay-dataset", default=str(DEFAULT_REPLAY_DATASET))
    parser.add_argument("--skip-replay", action="store_true")
    parser.add_argument("--skip-ranking", action="store_true")
    args = parser.parse_args()
    results = run_baseline_evaluations(db_path=args.db)
    if not args.skip_replay:
        results.extend(
            run_replay_overlay_evaluations(
                db_path=args.db,
                dataset_path=args.replay_dataset,
            )
        )
    if not args.skip_ranking:
        from cross_sectional_research import run_cross_sectional_evaluations

        results.extend(
            run_cross_sectional_evaluations(
                db_path=args.db,
                dataset_path=args.replay_dataset,
            )
        )
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
