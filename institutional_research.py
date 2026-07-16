import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from ai_pipeline import MODEL_FEATURES
from cross_sectional_research import (
    ALPHA_PREDICTION_QUANTILE,
    CROSS_SECTIONAL_FEATURE_MODE,
    RankingSpec,
    _development_dates,
    _evaluate_phase,
    _label_columns,
    load_ranking_dataset,
)
from database import DB_PATH, INSTITUTIONAL_FEATURE_VERSION
from execution_research import EXECUTION_RESEARCH_VERSION
from institutional_replay_dataset import INSTITUTIONAL_MODEL_FEATURES
from research_evaluation import (
    ExperimentSpec,
    PromotionGates,
    register_experiment,
    replay_temporal_partitions,
    save_evaluation,
)


GENERATION_2_EVALUATION_FAMILY = "generation_2_institutional_ablation_v1"
GENERATION_2_MODEL_VERSION = "hist_gradient_peer_ranker_institutional_v1"
GENERATION_2_COMPARISONS = 2
GENERATION_2_PSR_GATE = 1.0 - (0.05 / GENERATION_2_COMPARISONS)
GENERATION_2_FEATURES = tuple(MODEL_FEATURES) + tuple(INSTITUTIONAL_MODEL_FEATURES)
GENERATION_2_SPECS = (
    RankingSpec(
        method="next_open",
        horizon=5,
        target="peer_rank",
        prediction_quantile=ALPHA_PREDICTION_QUANTILE,
        feature_mode=CROSS_SECTIONAL_FEATURE_MODE,
        feature_columns=GENERATION_2_FEATURES,
        experiment_key="g2_institutional_next_open_t5_q80_v1",
    ),
    RankingSpec(
        method="next_open",
        horizon=10,
        target="peer_rank",
        prediction_quantile=ALPHA_PREDICTION_QUANTILE,
        feature_mode=CROSS_SECTIONAL_FEATURE_MODE,
        feature_columns=GENERATION_2_FEATURES,
        experiment_key="g2_institutional_next_open_t10_q80_v1",
    ),
)


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _delta(augmented, baseline, key):
    left = augmented.get(key)
    right = baseline.get(key)
    if left is None or right is None:
        return None
    return float(left - right)


def _control_spec(spec):
    return RankingSpec(
        method=spec.method,
        horizon=spec.horizon,
        top_k=spec.top_k,
        target=spec.target,
        prediction_quantile=spec.prediction_quantile,
        feature_mode=spec.feature_mode,
        feature_columns=tuple(MODEL_FEATURES),
    )


def _experiment(spec):
    quantile = round(spec.prediction_quantile * 100)
    return ExperimentSpec(
        key=spec.key,
        name=(
            f"Generation 2 institutional next-open T+{spec.horizon} "
            f"peer ranker with Q{quantile} abstention"
        ),
        hypothesis=(
            "Test whether lagged, normalized TWSE and TPEx institutional flow adds "
            f"after-cost T+{spec.horizon} excess-return lift over the identical "
            "technical peer-ranker control."
        ),
        strategy_family="generation_2_institutional_ablation",
        execution_version=spec.execution_version,
        selector="all_complete_institutional_replay_candidates",
        evaluation_family=GENERATION_2_EVALUATION_FAMILY,
        parameters=(
            ("target", spec.target),
            ("prediction_quantile", spec.prediction_quantile),
            ("feature_mode", spec.feature_mode),
            ("base_features", tuple(MODEL_FEATURES)),
            ("institutional_features", tuple(INSTITUTIONAL_MODEL_FEATURES)),
            ("top_k", spec.top_k),
            ("historical_scope", "development_validation_only"),
            ("holdout_evaluated", False),
            ("formal_ranking_enabled", False),
        ),
    )


def load_institutional_research_frame(dataset_path):
    frame = load_ranking_dataset(dataset_path)
    if frame.empty:
        return frame
    required = {
        "as_of",
        "institutional_known_at",
        "institutional_source_trade_date",
        "institutional_observations_20d",
        "institutional_coverage_status",
        "scenario_version",
        *INSTITUTIONAL_MODEL_FEATURES,
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(
            "Institutional replay dataset missing columns: " + ", ".join(missing)
        )
    for column in INSTITUTIONAL_MODEL_FEATURES:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    known = pd.to_datetime(frame["institutional_known_at"], utc=True, errors="coerce")
    decision = pd.to_datetime(frame["as_of"], utc=True, errors="coerce")
    source_date = pd.to_datetime(
        frame["institutional_source_trade_date"], errors="coerce"
    )
    trade_date = pd.to_datetime(frame["trade_date"], errors="coerce")
    if decision.isna().any() or trade_date.isna().any():
        raise ValueError("Institutional replay dataset contains invalid decision dates.")
    violation = known.notna() & decision.notna() & (known > decision)
    if violation.any():
        row = frame.loc[violation].iloc[0]
        raise ValueError(
            f"Institutional lookahead detected for {row['code']} on {row['trade_date']}."
        )
    source_violation = source_date.notna() & (source_date >= trade_date)
    if source_violation.any():
        row = frame.loc[source_violation].iloc[0]
        raise ValueError(
            f"Same-day institutional flow detected for {row['code']} on {row['trade_date']}."
        )
    complete = frame["institutional_coverage_status"] == "complete"
    if (complete & (known.isna() | source_date.isna())).any():
        raise ValueError("Complete institutional rows require valid source timestamps.")
    frame = frame[
        (frame["institutional_coverage_status"] == "complete")
        & (frame["institutional_observations_20d"] >= 20)
        & known.notna()
        & decision.notna()
    ].copy()
    frame = frame[
        frame["scenario_version"] == EXECUTION_RESEARCH_VERSION
    ].copy()
    return frame.sort_values(["trade_date", "code"], kind="stable")


def _phase_pair(
    frame,
    train_dates,
    evaluation_dates,
    spec,
    gates,
    min_train_rows,
):
    augmented_metrics, augmented_reasons = _evaluate_phase(
        frame,
        train_dates,
        evaluation_dates,
        spec,
        gates,
        min_train_rows,
    )
    baseline_metrics, baseline_reasons = _evaluate_phase(
        frame,
        train_dates,
        evaluation_dates,
        _control_spec(spec),
        gates,
        min_train_rows,
    )
    lift = {
        "mean_net_return": _delta(
            augmented_metrics, baseline_metrics, "mean_net_return"
        ),
        "mean_excess_return": _delta(
            augmented_metrics, baseline_metrics, "mean_excess_return"
        ),
        "mean_daily_net_return": _delta(
            augmented_metrics, baseline_metrics, "mean_daily_net_return"
        ),
        "mean_daily_excess_return": _delta(
            augmented_metrics, baseline_metrics, "mean_daily_excess_return"
        ),
    }
    return {
        "augmented": {
            "metrics": augmented_metrics,
            "rejection_reasons": augmented_reasons,
        },
        "technical_control": {
            "metrics": baseline_metrics,
            "rejection_reasons": baseline_reasons,
        },
        "institutional_lift": lift,
    }


def evaluate_institutional_spec(
    frame,
    spec,
    dataset_fingerprint,
    db_path=DB_PATH,
    gates=None,
    min_train_rows=500,
):
    gates = gates or PromotionGates(
        min_trade_dates=120,
        min_trades=300,
        min_probabilistic_sharpe=GENERATION_2_PSR_GATE,
        max_drawdown=-12.0,
        min_profitable_fold_rate=0.80,
    )
    columns = _label_columns(spec)
    missing = sorted(set(columns.values()) - set(frame.columns))
    if missing:
        raise ValueError(
            f"Institutional experiment {spec.key} is missing labels: "
            + ", ".join(missing)
        )
    scoped = frame.copy()
    for column in (columns["net"], columns["excess"], columns["drawdown"]):
        scoped[column] = pd.to_numeric(scoped[column], errors="coerce")
    partitions = replay_temporal_partitions(
        scoped, embargo_trade_dates=max(spec.horizon, 3)
    )
    development_train, development_eval, development_embargo = _development_dates(
        partitions, max(spec.horizon, 3)
    )
    phase_results = {
        "development": _phase_pair(
            scoped,
            development_train,
            development_eval,
            spec,
            gates,
            min_train_rows,
        ),
        "validation": _phase_pair(
            scoped,
            partitions["development"],
            partitions["validation"],
            spec,
            gates,
            min_train_rows,
        ),
    }
    validation = phase_results["validation"]
    metrics = dict(validation["augmented"]["metrics"])
    lift = validation["institutional_lift"]
    reasons = list(validation["augmented"]["rejection_reasons"])
    if phase_results["development"]["augmented"]["rejection_reasons"]:
        reasons.append("development_gate_failed")
    if lift["mean_daily_net_return"] is None or lift["mean_daily_net_return"] <= 0:
        reasons.append("no_institutional_net_lift")
    if (
        lift["mean_daily_excess_return"] is None
        or lift["mean_daily_excess_return"] <= 0
    ):
        reasons.append("no_institutional_excess_lift")
    reasons.append("prospective_generation_required")
    reasons = list(dict.fromkeys(reasons))
    metrics.update(
        {
            "evaluation_fingerprint": dataset_fingerprint[:10],
            "dataset_fingerprint": dataset_fingerprint,
            "dataset_rows": int(len(scoped)),
            "model_version": GENERATION_2_MODEL_VERSION,
            "institutional_feature_version": INSTITUTIONAL_FEATURE_VERSION,
            "ranking_target": spec.target,
            "prediction_quantile": spec.prediction_quantile,
            "feature_mode": spec.feature_mode,
            "features": list(spec.feature_columns),
            "base_features": list(MODEL_FEATURES),
            "institutional_features": list(INSTITUTIONAL_MODEL_FEATURES),
            "entry_method": spec.method,
            "holding_horizon": spec.horizon,
            "top_k": spec.top_k,
            "multiple_testing_family_size": GENERATION_2_COMPARISONS,
            "multiple_testing_psr_gate": GENERATION_2_PSR_GATE,
            "evaluation_scope": "historical_development_validation_only",
            "holdout_evaluated": False,
            "formal_ranking_enabled": False,
            "prospective_generation_required": True,
            "institutional_net_lift": lift["mean_daily_net_return"],
            "institutional_excess_lift": lift["mean_daily_excess_return"],
            "phase_results": phase_results,
            "temporal_split": {
                "embargo_trade_dates": max(spec.horizon, 3),
                "development_internal_embargo": development_embargo,
                "development_validation_embargo": partitions[
                    "development_validation_embargo"
                ],
                "reserved_holdout_trade_dates": len(partitions["holdout"]),
                "reserved_holdout_start": (
                    partitions["holdout"][0] if partitions["holdout"] else None
                ),
                "reserved_holdout_end": (
                    partitions["holdout"][-1] if partitions["holdout"] else None
                ),
            },
        }
    )
    experiment = _experiment(spec)
    experiment_id = register_experiment(experiment, db_path=db_path)
    version = save_evaluation(
        experiment_id,
        experiment,
        metrics,
        reasons,
        gates=gates,
        db_path=db_path,
    )
    return {
        "experimentKey": spec.key,
        "evaluationVersion": version,
        "qualified": False,
        "rejectionReasons": reasons,
        **metrics,
    }


def run_institutional_ablation(
    dataset_path,
    db_path=DB_PATH,
    specs=GENERATION_2_SPECS,
    gates=None,
    min_train_rows=500,
):
    dataset_path = Path(dataset_path)
    frame = load_institutional_research_frame(dataset_path)
    if frame.empty:
        return []
    fingerprint = _sha256(dataset_path)
    return [
        evaluate_institutional_spec(
            frame,
            spec,
            fingerprint,
            db_path=db_path,
            gates=gates,
            min_train_rows=min_train_rows,
        )
        for spec in specs
    ]


def main():
    parser = argparse.ArgumentParser(
        description="Run locked Generation 2 institutional feature ablations."
    )
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()
    result = run_institutional_ablation(args.dataset, db_path=args.db)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
