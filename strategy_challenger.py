import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from cross_sectional_research import (
    ALPHA_PREDICTION_QUANTILE,
    CROSS_SECTIONAL_FEATURE_MODE,
    EXECUTION_RESEARCH_VERSION,
    RAW_FEATURE_MODE,
    RankingSpec,
    _label_columns,
    fit_rank_phase,
    load_ranking_dataset,
)
from database import DB_PATH, get_connection, get_taipei_now, init_db
from research_evaluation import (
    DEFAULT_REPLAY_DATASET,
    ExperimentSpec,
    PromotionGates,
    evaluate_frame,
    register_experiment,
    replay_temporal_partitions,
    save_evaluation,
)


STRATEGY_CHALLENGER_VERSION = "purged_walk_forward_challenger_v1"
STRATEGY_CHALLENGER_FAMILY = "purged_walk_forward_research_v1"
LOCKED_COMPARISONS = 6
MULTIPLE_TESTING_PSR_GATE = 1.0 - (0.05 / LOCKED_COMPARISONS)
DEFAULT_INITIAL_TRAIN_DATES = 504
DEFAULT_TEST_WINDOW_DATES = 63
DEFAULT_MIN_TRAIN_ROWS = 500
DEFAULT_HORIZONS = (5, 10, 20)
DEFAULT_TARGETS = ("excess", "peer_rank")


def locked_specs():
    specs = []
    for target in DEFAULT_TARGETS:
        for horizon in DEFAULT_HORIZONS:
            prefix = "alpha" if target == "excess" else "peer"
            specs.append(
                RankingSpec(
                    method="next_open",
                    horizon=horizon,
                    target=target,
                    prediction_quantile=ALPHA_PREDICTION_QUANTILE,
                    feature_mode=(
                        CROSS_SECTIONAL_FEATURE_MODE
                        if target == "peer_rank"
                        else RAW_FEATURE_MODE
                    ),
                    experiment_key=(
                        f"walk_forward_{prefix}_next_open_t{horizon}_q80_v1"
                    ),
                )
            )
    return tuple(specs)


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _experiment(spec):
    target_name = "benchmark excess return" if spec.target == "excess" else "industry-relative peer rank"
    return ExperimentSpec(
        key=spec.key,
        name=f"Walk-forward next-open T+{spec.horizon} {spec.target} challenger",
        hypothesis=(
            f"An expanding-window model of {target_name} can select a daily Top 3 "
            "portfolio with positive after-cost absolute and benchmark-relative "
            "returns before the reserved holdout period."
        ),
        strategy_family="walk_forward_cross_sectional_challenger",
        execution_version=(
            f"{EXECUTION_RESEARCH_VERSION}:next_open:t{spec.horizon}"
        ),
        selector="tradable_candidates_q80_top3",
        parameters=(
            ("target", spec.target),
            ("holding_horizon", spec.horizon),
            ("prediction_quantile", spec.prediction_quantile),
            ("feature_mode", spec.feature_mode),
            ("locked_comparisons", LOCKED_COMPARISONS),
            ("holdout_used_for_selection", False),
        ),
        evaluation_family=STRATEGY_CHALLENGER_FAMILY,
    )


def _standardized_returns(frame, columns):
    return pd.DataFrame(
        {
            "trade_date": frame["trade_date"].astype(str),
            "code": frame["code"].astype(str),
            "net_return_3d": pd.to_numeric(frame[columns["net"]], errors="coerce"),
            "excess_return_3d": pd.to_numeric(
                frame[columns["excess"]], errors="coerce"
            ),
            "max_drawdown_3d": pd.to_numeric(
                frame[columns["drawdown"]], errors="coerce"
            ),
        },
        index=frame.index,
    )


def _formal_baseline(frame, evaluation_dates, spec):
    columns = _label_columns(spec)
    baseline = frame[
        frame["trade_date"].isin(set(evaluation_dates))
        & (frame["rule_selected"] == 1)
        & frame[columns["net"]].notna()
        & frame[columns["excess"]].notna()
    ].copy()
    return _standardized_returns(baseline, columns)


def _append_reason(reasons, condition, reason):
    if condition and reason not in reasons:
        reasons.append(reason)


def evaluate_walk_forward_spec(
    frame,
    spec,
    dataset_fingerprint,
    db_path=DB_PATH,
    initial_train_dates=DEFAULT_INITIAL_TRAIN_DATES,
    test_window_dates=DEFAULT_TEST_WINDOW_DATES,
    min_train_rows=DEFAULT_MIN_TRAIN_ROWS,
    gates=None,
):
    gates = gates or PromotionGates(
        min_trade_dates=120,
        min_trades=300,
        min_probabilistic_sharpe=MULTIPLE_TESTING_PSR_GATE,
        max_drawdown=-12.0,
        min_profitable_fold_rate=0.60,
    )
    columns = _label_columns(spec)
    scoped = frame.copy()
    for column in columns.values():
        scoped[column] = pd.to_numeric(scoped[column], errors="coerce")
    scoped = scoped[
        scoped["scenario_version"] == EXECUTION_RESEARCH_VERSION
    ].copy()
    partitions = replay_temporal_partitions(
        scoped, embargo_trade_dates=max(spec.horizon, 3)
    )
    reserved_holdout = list(partitions["holdout"])
    holdout_embargo = list(partitions["validation_holdout_embargo"])
    research_cutoff = min(holdout_embargo or reserved_holdout)
    research_dates = [
        value
        for value in sorted(scoped["trade_date"].unique())
        if value < research_cutoff
    ]
    if len(research_dates) <= initial_train_dates + max(spec.horizon, 3):
        raise ValueError(
            f"{spec.key} needs more than {initial_train_dates} pre-holdout dates."
        )

    selected_folds = []
    fold_diagnostics = []
    evaluation_dates = []
    embargo = max(spec.horizon, 3)
    for fold_index, start in enumerate(
        range(initial_train_dates, len(research_dates), test_window_dates), start=1
    ):
        train_end = max(0, start - embargo)
        train_dates = research_dates[:train_end]
        test_dates = research_dates[start : start + test_window_dates]
        if not test_dates:
            continue
        selected, diagnostics = fit_rank_phase(
            scoped,
            train_dates,
            test_dates,
            spec,
            min_train_rows=min_train_rows,
        )
        evaluation_dates.extend(test_dates)
        if not selected.empty:
            selected = selected.copy()
            selected["fold_index"] = fold_index
            selected_folds.append(selected)
        fold_diagnostics.append(
            {
                "fold": fold_index,
                "training_start": train_dates[0] if train_dates else None,
                "training_end": train_dates[-1] if train_dates else None,
                "evaluation_start": test_dates[0],
                "evaluation_end": test_dates[-1],
                "embargo_trade_dates": embargo,
                **diagnostics,
            }
        )

    selected = (
        pd.concat(selected_folds, ignore_index=True)
        if selected_folds
        else pd.DataFrame(
            columns=[
                "trade_date",
                "code",
                "net_return_3d",
                "excess_return_3d",
                "max_drawdown_3d",
                "fold_index",
            ]
        )
    )
    metrics, reasons = evaluate_frame(
        selected,
        gates=gates,
        decision_horizon=spec.horizon,
        observation_dates=evaluation_dates,
    )
    baseline = _formal_baseline(scoped, evaluation_dates, spec)
    baseline_metrics, _ = evaluate_frame(
        baseline,
        gates=gates,
        decision_horizon=spec.horizon,
        observation_dates=evaluation_dates,
    )
    net_lift = (
        metrics["mean_daily_net_return"]
        - baseline_metrics["mean_daily_net_return"]
        if metrics["mean_daily_net_return"] is not None
        and baseline_metrics["mean_daily_net_return"] is not None
        else None
    )
    excess_lift = (
        metrics["mean_daily_excess_return"]
        - baseline_metrics["mean_daily_excess_return"]
        if metrics["mean_daily_excess_return"] is not None
        and baseline_metrics["mean_daily_excess_return"] is not None
        else None
    )
    _append_reason(
        reasons,
        metrics["mean_daily_net_return"] is None
        or metrics["mean_daily_net_return"] <= 0,
        "non_positive_daily_net_return",
    )
    _append_reason(
        reasons,
        metrics["mean_daily_excess_return"] is None
        or metrics["mean_daily_excess_return"] <= 0,
        "non_positive_daily_excess_return",
    )
    _append_reason(
        reasons,
        net_lift is None or net_lift <= 0,
        "no_formal_net_lift",
    )
    _append_reason(
        reasons,
        excess_lift is None or excess_lift <= 0,
        "no_formal_excess_lift",
    )
    _append_reason(
        reasons,
        len(fold_diagnostics) < 4,
        "insufficient_walk_forward_folds",
    )

    metrics.update(
        {
            "sample_start": evaluation_dates[0] if evaluation_dates else None,
            "sample_end": evaluation_dates[-1] if evaluation_dates else None,
            "evaluation_fingerprint": dataset_fingerprint[:10],
            "dataset_fingerprint": dataset_fingerprint,
            "dataset_rows": int(len(scoped)),
            "challenger_version": STRATEGY_CHALLENGER_VERSION,
            "evaluation_scope": "pre_holdout_expanding_walk_forward",
            "entry_method": spec.method,
            "holding_horizon": spec.horizon,
            "ranking_target": spec.target,
            "prediction_quantile": spec.prediction_quantile,
            "walk_forward_folds": len(fold_diagnostics),
            "initial_train_dates": initial_train_dates,
            "test_window_dates": test_window_dates,
            "fold_diagnostics": fold_diagnostics,
            "formal_baseline_trades": baseline_metrics["trades"],
            "formal_baseline_mean_daily_net_return": baseline_metrics[
                "mean_daily_net_return"
            ],
            "formal_baseline_mean_daily_excess_return": baseline_metrics[
                "mean_daily_excess_return"
            ],
            "formal_net_lift": net_lift,
            "formal_excess_lift": excess_lift,
            "multiple_testing_family_size": LOCKED_COMPARISONS,
            "multiple_testing_psr_gate": MULTIPLE_TESTING_PSR_GATE,
            "holdout_evaluated": False,
            "reserved_holdout_start": (
                reserved_holdout[0] if reserved_holdout else None
            ),
            "reserved_holdout_end": reserved_holdout[-1] if reserved_holdout else None,
            "reserved_holdout_trade_dates": len(reserved_holdout),
            "holdout_embargo_trade_dates": holdout_embargo,
            "formal_ranking_enabled": False,
        }
    )
    experiment = _experiment(spec)
    experiment_id = register_experiment(experiment, db_path=db_path)
    evaluation_version = save_evaluation(
        experiment_id,
        experiment,
        metrics,
        reasons,
        gates=gates,
        db_path=db_path,
    )
    return {
        "experimentKey": spec.key,
        "name": experiment.name,
        "evaluationVersion": evaluation_version,
        "qualified": not reasons,
        "rejectionReasons": reasons,
        **metrics,
    }


def _phase_matrix(frame, dates, method, horizon):
    net_column = f"{method}_net_return_{horizon}d"
    excess_column = f"{method}_excess_return_{horizon}d"
    scoped = frame[
        frame["trade_date"].isin(set(dates))
        & (frame["rule_selected"] == 1)
        & frame[net_column].notna()
        & frame[excess_column].notna()
    ].copy()
    daily = (
        scoped.groupby("trade_date")[[net_column, excess_column]]
        .mean()
        .reindex(dates, fill_value=0.0)
    )
    participating = int(scoped["trade_date"].nunique())
    return {
        "decisionDates": len(dates),
        "participatingDates": participating,
        "participationRatePct": (
            participating / len(dates) * 100 if dates else 0.0
        ),
        "trades": int(len(scoped)),
        "meanNetReturn": (
            float(scoped[net_column].mean()) if not scoped.empty else None
        ),
        "meanExcessReturn": (
            float(scoped[excess_column].mean()) if not scoped.empty else None
        ),
        "meanDailyNetReturn": float(daily[net_column].mean()),
        "meanDailyExcessReturn": float(daily[excess_column].mean()),
        "positiveDayRate": float((daily[net_column] > 0).mean() * 100),
    }


def build_execution_matrix(frame):
    partitions = replay_temporal_partitions(frame, embargo_trade_dates=20)
    rows = []
    for method in (
        "next_open",
        "next_ohlc4_proxy",
        "next_close",
        "pullback_2pct_3d",
    ):
        for horizon in (1, 3, 5, 10, 20):
            required = {
                f"{method}_net_return_{horizon}d",
                f"{method}_excess_return_{horizon}d",
            }
            if not required.issubset(frame.columns):
                continue
            rows.append(
                {
                    "key": f"{method}_t{horizon}",
                    "entryMethod": method,
                    "holdingHorizon": horizon,
                    "development": _phase_matrix(
                        frame, partitions["development"], method, horizon
                    ),
                    "validation": _phase_matrix(
                        frame, partitions["validation"], method, horizon
                    ),
                    "holdoutAudit": _phase_matrix(
                        frame, partitions["holdout"], method, horizon
                    ),
                }
            )
    return rows


def select_challenger(results):
    def score(result):
        daily_excess = result.get("mean_daily_excess_return")
        daily_net = result.get("mean_daily_net_return")
        excess_lift = result.get("formal_excess_lift")
        return (
            float(daily_excess) if daily_excess is not None else -999.0,
            float(daily_net) if daily_net is not None else -999.0,
            float(excess_lift) if excess_lift is not None else -999.0,
            int(result.get("trades") or 0),
        )

    ranked = sorted(results, key=score, reverse=True)
    qualified = [result for result in ranked if result.get("qualified")]
    selected = qualified[0] if qualified else None
    diagnostic = ranked[0] if ranked else None
    return {
        "status": "prospective_shadow_ready" if selected else "blocked",
        "recommendationMode": "shadow" if selected else "cash",
        "selectedExperimentKey": (
            selected.get("experimentKey") if selected else None
        ),
        "diagnosticLeaderKey": (
            diagnostic.get("experimentKey") if diagnostic else None
        ),
        "qualifiedCandidates": len(qualified),
        "candidateCount": len(ranked),
        "candidateLeaderboard": ranked,
    }


def save_strategy_snapshot(payload, dataset_fingerprint, db_path=DB_PATH):
    evaluated_at = get_taipei_now().isoformat(timespec="seconds")
    with get_connection(db_path) as conn:
        init_db(conn)
        conn.execute(
            """
            INSERT INTO strategy_challenger_snapshots (
                evaluated_at, challenger_version, dataset_fingerprint,
                status, selected_experiment_key, recommendation_mode,
                qualified_candidates, candidate_count, metrics_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(challenger_version, dataset_fingerprint) DO UPDATE SET
                evaluated_at=excluded.evaluated_at,
                status=excluded.status,
                selected_experiment_key=excluded.selected_experiment_key,
                recommendation_mode=excluded.recommendation_mode,
                qualified_candidates=excluded.qualified_candidates,
                candidate_count=excluded.candidate_count,
                metrics_json=excluded.metrics_json
            """,
            (
                evaluated_at,
                STRATEGY_CHALLENGER_VERSION,
                dataset_fingerprint,
                payload["status"],
                payload.get("selectedExperimentKey"),
                payload["recommendationMode"],
                payload["qualifiedCandidates"],
                payload["candidateCount"],
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        )
    return evaluated_at


def run_strategy_challenger(
    dataset_path=DEFAULT_REPLAY_DATASET,
    db_path=DB_PATH,
    specs=None,
    initial_train_dates=DEFAULT_INITIAL_TRAIN_DATES,
    test_window_dates=DEFAULT_TEST_WINDOW_DATES,
    min_train_rows=DEFAULT_MIN_TRAIN_ROWS,
):
    dataset_path = Path(dataset_path)
    frame = load_ranking_dataset(dataset_path)
    if frame.empty:
        raise ValueError(f"Strategy dataset is empty: {dataset_path}")
    fingerprint = _sha256(dataset_path)
    results = [
        evaluate_walk_forward_spec(
            frame,
            spec,
            fingerprint,
            db_path=db_path,
            initial_train_dates=initial_train_dates,
            test_window_dates=test_window_dates,
            min_train_rows=min_train_rows,
        )
        for spec in (specs or locked_specs())
    ]
    selection = select_challenger(results)
    payload = {
        "version": STRATEGY_CHALLENGER_VERSION,
        "datasetFingerprint": fingerprint,
        "datasetRows": int(len(frame)),
        "datasetStart": str(frame["trade_date"].min()),
        "datasetEnd": str(frame["trade_date"].max()),
        "lockedComparisons": LOCKED_COMPARISONS,
        "multipleTestingPsrGate": MULTIPLE_TESTING_PSR_GATE,
        "selectionUsesHoldout": False,
        "formalRankingEnabled": False,
        "executionMatrix": build_execution_matrix(frame),
        **selection,
    }
    payload["evaluatedAt"] = save_strategy_snapshot(
        payload, fingerprint, db_path=db_path
    )
    return payload


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate locked walk-forward strategy challengers without holdout selection."
    )
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--dataset", default=str(DEFAULT_REPLAY_DATASET))
    parser.add_argument("--initial-train-dates", type=int, default=DEFAULT_INITIAL_TRAIN_DATES)
    parser.add_argument("--test-window-dates", type=int, default=DEFAULT_TEST_WINDOW_DATES)
    parser.add_argument("--min-train-rows", type=int, default=DEFAULT_MIN_TRAIN_ROWS)
    args = parser.parse_args()
    result = run_strategy_challenger(
        dataset_path=args.dataset,
        db_path=args.db,
        initial_train_dates=args.initial_train_dates,
        test_window_dates=args.test_window_dates,
        min_train_rows=args.min_train_rows,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
