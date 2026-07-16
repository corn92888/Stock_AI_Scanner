import hashlib
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

from ai_pipeline import MODEL_FEATURES
from database import DB_PATH
from execution_research import ENTRY_METHODS, EXECUTION_RESEARCH_VERSION, HORIZONS
from research_evaluation import (
    ExperimentSpec,
    PromotionGates,
    evaluate_frame,
    register_experiment,
    replay_temporal_partitions,
    save_evaluation,
)


RANKING_EVALUATION_FAMILY = "purged_cross_sectional_holdout_v1"
RANKING_MODEL_VERSION = "hist_gradient_return_ranker_v1"
ALPHA_EVALUATION_FAMILY = "purged_alpha_abstention_holdout_v1"
ALPHA_MODEL_VERSION = "hist_gradient_excess_ranker_q80_v1"
DEFAULT_TOP_K = 3
MULTIPLE_TESTING_PSR_GATE = 0.9975
ALPHA_PREDICTION_QUANTILE = 0.80
CALIBRATION_FRACTION = 0.20

METHOD_NAMES = {
    "next_open": "Next-open",
    "next_ohlc4_proxy": "Next-session OHLC4 proxy",
    "next_close": "Next-close",
    "pullback_2pct_3d": "Three-day 2% pullback",
}


@dataclass(frozen=True)
class RankingSpec:
    method: str
    horizon: int
    top_k: int = DEFAULT_TOP_K
    target: str = "net"
    prediction_quantile: float | None = None

    def __post_init__(self):
        if self.target not in {"net", "excess"}:
            raise ValueError(f"Unsupported ranking target: {self.target}")
        if (
            self.prediction_quantile is not None
            and not 0 < self.prediction_quantile < 1
        ):
            raise ValueError("prediction_quantile must be between zero and one.")

    @property
    def uses_abstention(self):
        return self.prediction_quantile is not None

    @property
    def key(self):
        if self.target == "excess" and self.uses_abstention:
            quantile = round(self.prediction_quantile * 100)
            return f"replay_alpha_rank_{self.method}_t{self.horizon}_q{quantile}_v1"
        return f"replay_rank_{self.method}_t{self.horizon}_v1"

    @property
    def execution_version(self):
        return f"{EXECUTION_RESEARCH_VERSION}:{self.method}:t{self.horizon}"

    @property
    def experiment(self):
        if self.target == "excess" and self.uses_abstention:
            quantile = round(self.prediction_quantile * 100)
            return ExperimentSpec(
                key=self.key,
                name=(
                    f"{METHOD_NAMES[self.method]} T+{self.horizon} alpha ranker "
                    f"with Q{quantile} abstention"
                ),
                hypothesis=(
                    f"Predict benchmark-relative T+{self.horizon} return and hold up "
                    f"to the daily top {self.top_k} only when the score clears a "
                    f"training-only Q{quantile} calibration threshold."
                ),
                strategy_family="cross_sectional_alpha_ranker",
                execution_version=self.execution_version,
                selector="all_replay_candidates_alpha_abstention",
                evaluation_family=ALPHA_EVALUATION_FAMILY,
                parameters=(
                    ("target", self.target),
                    ("prediction_quantile", self.prediction_quantile),
                    ("top_k", self.top_k),
                ),
            )
        return ExperimentSpec(
            key=self.key,
            name=f"{METHOD_NAMES[self.method]} T+{self.horizon} ranker",
            hypothesis=(
                f"Rank the complete tradable candidate pool by predicted after-cost "
                f"T+{self.horizon} return and hold only the daily top {self.top_k}."
            ),
            strategy_family="cross_sectional_return_ranker",
            execution_version=self.execution_version,
            selector="all_replay_candidates",
            evaluation_family=RANKING_EVALUATION_FAMILY,
        )


RANKING_SPECS = tuple(
    RankingSpec(method=method, horizon=horizon)
    for method in ENTRY_METHODS
    for horizon in HORIZONS
)

ALPHA_RANKING_SPECS = tuple(
    RankingSpec(
        method=method,
        horizon=horizon,
        target="excess",
        prediction_quantile=ALPHA_PREDICTION_QUANTILE,
    )
    for method in ENTRY_METHODS
    for horizon in HORIZONS
)

ALL_RANKING_SPECS = RANKING_SPECS + ALPHA_RANKING_SPECS


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _label_columns(spec):
    prefix = spec.method
    suffix = f"{spec.horizon}d"
    return {
        "entry_status": f"{prefix}_entry_status",
        "net": f"{prefix}_net_return_{suffix}",
        "excess": f"{prefix}_excess_return_{suffix}",
        "drawdown": f"{prefix}_max_drawdown_{suffix}",
    }


def load_ranking_dataset(dataset_path):
    dataset_path = Path(dataset_path)
    if not dataset_path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(dataset_path, dtype={"trade_date": str, "code": str})
    required = {"trade_date", "code", "rule_selected", "tradable", *MODEL_FEATURES}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(
            "Cross-sectional replay dataset missing columns: " + ", ".join(missing)
        )
    frame["trade_date"] = frame["trade_date"].astype(str)
    frame["code"] = frame["code"].astype(str)
    for column in {"rule_selected", "tradable", *MODEL_FEATURES}:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.sort_values(["trade_date", "code"], kind="stable")


def available_ranking_specs(frame, specs=ALL_RANKING_SPECS):
    available = []
    for spec in specs:
        columns = _label_columns(spec)
        if set(columns.values()).issubset(frame.columns):
            available.append(spec)
    return tuple(available)


def _model():
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            (
                "regressor",
                HistGradientBoostingRegressor(
                    learning_rate=0.05,
                    max_iter=120,
                    max_leaf_nodes=15,
                    min_samples_leaf=30,
                    l2_regularization=1.0,
                    random_state=42,
                ),
            ),
        ]
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


def _calibration_partition(train, horizon):
    dates = sorted(train["trade_date"].astype(str).unique())
    calibration_start = int(len(dates) * (1.0 - CALIBRATION_FRACTION))
    fit_end = max(0, calibration_start - max(horizon, 3))
    return (
        dates[:fit_end],
        dates[fit_end:calibration_start],
        dates[calibration_start:],
    )


def fit_rank_phase(frame, train_dates, evaluation_dates, spec, min_train_rows=500):
    columns = _label_columns(spec)
    target_column = columns[spec.target]
    train = frame[
        frame["trade_date"].isin(set(train_dates))
        & (frame["tradable"] == 1)
        & frame[target_column].notna()
    ].copy()
    evaluation_pool = frame[
        frame["trade_date"].isin(set(evaluation_dates))
        & (frame["tradable"] == 1)
    ].copy()
    empty_diagnostics = {
        "train_rows": int(len(train)),
        "fit_rows": 0,
        "calibration_rows": 0,
        "calibration_embargo_trade_dates": 0,
        "evaluation_candidates": int(len(evaluation_pool)),
        "evaluation_trade_dates": int(len(set(evaluation_dates))),
        "eligible_candidates": 0,
        "selected_candidates": 0,
        "participating_trade_dates": 0,
        "abstained_trade_dates": int(len(set(evaluation_dates))),
        "participation_rate_pct": 0.0,
        "filled_trades": 0,
        "fill_rate_pct": None,
        "ranking_target": spec.target,
        "prediction_quantile": spec.prediction_quantile,
        "prediction_threshold": None,
    }
    if len(train) < min_train_rows or evaluation_pool.empty:
        return pd.DataFrame(), {
            **empty_diagnostics,
        }

    fit = train
    calibration = pd.DataFrame()
    calibration_embargo = []
    model = _model()
    prediction_threshold = None
    if spec.uses_abstention:
        fit_dates, calibration_embargo, calibration_dates = _calibration_partition(
            train, spec.horizon
        )
        fit = train[train["trade_date"].isin(set(fit_dates))].copy()
        calibration = train[
            train["trade_date"].isin(set(calibration_dates))
        ].copy()
        if len(fit) < min_train_rows or calibration.empty:
            return pd.DataFrame(), {
                **empty_diagnostics,
                "fit_rows": int(len(fit)),
                "calibration_rows": int(len(calibration)),
                "calibration_embargo_trade_dates": len(calibration_embargo),
            }

    model.fit(fit[MODEL_FEATURES], fit[target_column].astype(float))
    prediction_column = f"predicted_{spec.target}_return"
    if spec.uses_abstention:
        calibration_predictions = pd.Series(
            model.predict(calibration[MODEL_FEATURES]), dtype=float
        )
        prediction_threshold = max(
            0.0,
            float(calibration_predictions.quantile(spec.prediction_quantile)),
        )
    evaluation_pool[prediction_column] = model.predict(
        evaluation_pool[MODEL_FEATURES]
    )
    eligible = evaluation_pool
    if prediction_threshold is not None:
        eligible = evaluation_pool[
            evaluation_pool[prediction_column] > prediction_threshold
        ].copy()
    selected = (
        eligible.sort_values(
            ["trade_date", prediction_column, "code"],
            ascending=[True, False, True],
            kind="stable",
        )
        .groupby("trade_date", sort=False)
        .head(spec.top_k)
        .copy()
    )
    selected["rank_order"] = selected.groupby("trade_date").cumcount() + 1
    selected_returns = _standardized_returns(selected, columns)
    selected_returns[prediction_column] = selected[prediction_column]
    selected_returns["rank_order"] = selected["rank_order"]
    filled = selected_returns[
        selected_returns["net_return_3d"].notna()
        & selected_returns["excess_return_3d"].notna()
    ].copy()
    evaluation_trade_dates = int(len(set(evaluation_dates)))
    participating_trade_dates = int(filled["trade_date"].nunique())
    return filled, {
        "train_rows": int(len(train)),
        "fit_rows": int(len(fit)),
        "calibration_rows": int(len(calibration)),
        "calibration_embargo_trade_dates": len(calibration_embargo),
        "evaluation_candidates": int(len(evaluation_pool)),
        "evaluation_trade_dates": evaluation_trade_dates,
        "eligible_candidates": int(len(eligible)),
        "selected_candidates": int(len(selected)),
        "participating_trade_dates": participating_trade_dates,
        "abstained_trade_dates": evaluation_trade_dates - participating_trade_dates,
        "participation_rate_pct": (
            round(participating_trade_dates / evaluation_trade_dates * 100, 4)
            if evaluation_trade_dates
            else 0.0
        ),
        "filled_trades": int(len(filled)),
        "fill_rate_pct": (
            round(len(filled) / len(selected) * 100, 4) if len(selected) else None
        ),
        "ranking_target": spec.target,
        "prediction_quantile": spec.prediction_quantile,
        "prediction_threshold": prediction_threshold,
    }


def _formal_baseline(frame, evaluation_dates, spec):
    columns = _label_columns(spec)
    baseline = frame[
        frame["trade_date"].isin(set(evaluation_dates))
        & (frame["rule_selected"] == 1)
        & frame[columns["net"]].notna()
        & frame[columns["excess"]].notna()
    ].copy()
    return _standardized_returns(baseline, columns)


def _evaluate_phase(
    frame,
    train_dates,
    evaluation_dates,
    spec,
    gates,
    min_train_rows,
):
    selected, diagnostics = fit_rank_phase(
        frame,
        train_dates,
        evaluation_dates,
        spec,
        min_train_rows=min_train_rows,
    )
    observation_dates = evaluation_dates if spec.uses_abstention else None
    metrics, reasons = evaluate_frame(
        selected,
        gates=gates,
        decision_horizon=spec.horizon,
        observation_dates=observation_dates,
    )
    baseline = _formal_baseline(frame, evaluation_dates, spec)
    baseline_metrics, _ = evaluate_frame(
        baseline,
        gates=gates,
        decision_horizon=spec.horizon,
        observation_dates=observation_dates,
    )
    metrics.update(diagnostics)
    metrics["sample_start"] = (
        str(selected["trade_date"].min()) if not selected.empty else None
    )
    metrics["sample_end"] = (
        str(selected["trade_date"].max()) if not selected.empty else None
    )
    metrics["formal_baseline_trades"] = baseline_metrics["trades"]
    metrics["formal_baseline_mean_net_return"] = baseline_metrics[
        "mean_net_return"
    ]
    metrics["formal_baseline_mean_excess_return"] = baseline_metrics[
        "mean_excess_return"
    ]
    metrics["formal_baseline_mean_daily_net_return"] = baseline_metrics[
        "mean_daily_net_return"
    ]
    metrics["formal_baseline_mean_daily_excess_return"] = baseline_metrics[
        "mean_daily_excess_return"
    ]
    net_metric = "mean_daily_net_return" if spec.uses_abstention else "mean_net_return"
    excess_metric = (
        "mean_daily_excess_return" if spec.uses_abstention else "mean_excess_return"
    )
    if (
        metrics[net_metric] is None
        or baseline_metrics[net_metric] is None
        or metrics[net_metric] <= baseline_metrics[net_metric]
    ):
        reasons.append("no_formal_net_lift")
    if (
        metrics[excess_metric] is None
        or baseline_metrics[excess_metric] is None
        or metrics[excess_metric] <= baseline_metrics[excess_metric]
    ):
        reasons.append("no_formal_excess_lift")
    return metrics, list(dict.fromkeys(reasons))


def _development_dates(partitions, horizon):
    dates = list(partitions["development"])
    evaluation_start = int(len(dates) * 0.70)
    training_end = max(0, evaluation_start - horizon)
    return dates[:training_end], dates[evaluation_start:], dates[
        training_end:evaluation_start
    ]


def evaluate_ranking_spec(
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
        min_probabilistic_sharpe=MULTIPLE_TESTING_PSR_GATE,
        max_drawdown=-12.0,
        min_profitable_fold_rate=0.80,
    )
    columns = _label_columns(spec)
    numeric = [columns["net"], columns["excess"], columns["drawdown"]]
    scoped = frame.copy()
    for column in numeric:
        scoped[column] = pd.to_numeric(scoped[column], errors="coerce")
    scoped = scoped[scoped["scenario_version"] == EXECUTION_RESEARCH_VERSION].copy()
    partitions = replay_temporal_partitions(
        scoped, embargo_trade_dates=max(spec.horizon, 3)
    )
    development_train, development_eval, development_embargo = _development_dates(
        partitions, max(spec.horizon, 3)
    )
    phase_definitions = {
        "development": (development_train, development_eval),
        "validation": (partitions["development"], partitions["validation"]),
        "holdout": (
            list(partitions["development"]) + list(partitions["validation"]),
            partitions["holdout"],
        ),
    }
    phase_results = {}
    for phase_name, (train_dates, evaluation_dates) in phase_definitions.items():
        metrics, reasons = _evaluate_phase(
            scoped,
            train_dates,
            evaluation_dates,
            spec,
            gates,
            min_train_rows,
        )
        phase_results[phase_name] = {
            "metrics": metrics,
            "rejection_reasons": reasons,
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
            "dataset_rows": int(len(scoped)),
            "model_version": (
                ALPHA_MODEL_VERSION if spec.uses_abstention else RANKING_MODEL_VERSION
            ),
            "ranking_target": spec.target,
            "prediction_quantile": spec.prediction_quantile,
            "calibration_fraction": CALIBRATION_FRACTION,
            "entry_method": spec.method,
            "holding_horizon": spec.horizon,
            "top_k": spec.top_k,
            "features": list(MODEL_FEATURES),
            "multiple_testing_psr_gate": MULTIPLE_TESTING_PSR_GATE,
            "multiple_testing_family_size": len(ALPHA_RANKING_SPECS)
            if spec.uses_abstention
            else len(RANKING_SPECS),
            "temporal_split": {
                "embargo_trade_dates": max(spec.horizon, 3),
                "development_internal_embargo": development_embargo,
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
    experiment = spec.experiment
    experiment_id = register_experiment(experiment, db_path=db_path)
    version = save_evaluation(
        experiment_id,
        experiment,
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


def run_cross_sectional_evaluations(
    dataset_path,
    db_path=DB_PATH,
    specs=ALL_RANKING_SPECS,
    gates=None,
    min_train_rows=500,
):
    frame = load_ranking_dataset(dataset_path)
    if frame.empty or "scenario_version" not in frame.columns:
        return []
    available = available_ranking_specs(frame, specs=specs)
    fingerprint = _sha256(dataset_path)
    return [
        evaluate_ranking_spec(
            frame,
            spec,
            fingerprint,
            db_path=db_path,
            gates=gates,
            min_train_rows=min_train_rows,
        )
        for spec in available
    ]
