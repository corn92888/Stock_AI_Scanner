import argparse
import json
import math
from dataclasses import asdict, dataclass
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
        "evaluation_family": EVALUATION_FAMILY,
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


def evaluate_frame(frame, gates=None, fold_count=5):
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
        }
    else:
        daily = frame.groupby("trade_date", sort=True).agg(
            net_return=("net_return_3d", "mean"),
            excess_return=("excess_return_3d", "mean"),
        )
        daily["portfolio_return"] = daily["net_return"] / DECISION_HORIZON
        daily["portfolio_excess"] = daily["excess_return"] / DECISION_HORIZON
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
            "trade_dates": int(len(daily)),
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
        }

    reasons = []
    if metrics["trade_dates"] < gates.min_trade_dates:
        reasons.append("insufficient_trade_dates")
    if metrics["trades"] < gates.min_trades:
        reasons.append("insufficient_trades")
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
    evaluation_version = (
        f"{EVALUATION_FAMILY}:{spec.execution_version}:"
        f"{sample_end or 'empty'}:n{metrics['trades']}"
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


def main():
    parser = argparse.ArgumentParser(description="Evaluate registered strategy baselines.")
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()
    print(json.dumps(run_baseline_evaluations(db_path=args.db), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
