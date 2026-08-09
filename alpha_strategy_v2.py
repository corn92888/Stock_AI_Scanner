import argparse
import hashlib
import json
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

from alpha_universe_dataset import (
    ALPHA_DATASET_VERSION,
    ALPHA_EXECUTION_VERSION,
    ALPHA_FEATURES,
    ALPHA_HORIZONS,
    DEFAULT_OUTPUT,
)
from database import DB_PATH
from research_evaluation import (
    ExperimentSpec,
    PromotionGates,
    evaluate_frame,
    register_experiment,
    replay_temporal_partitions,
    save_evaluation,
)
from strategy_challenger import save_strategy_snapshot


ALPHA_CHALLENGER_VERSION = "alpha_liquid_universe_walk_forward_v2"
ALPHA_EVALUATION_FAMILY = "alpha_liquid_universe_purged_walk_forward_v2"
ALPHA_MODEL_VERSION = "hist_gradient_alpha_cash_gate_v3"
ALPHA_MODEL_ARTIFACT_VERSION = "alpha_model_artifact_v1"
DEFAULT_MODEL_OUTPUT = Path("data/models/alpha_strategy_v2_model.joblib")
LOCKED_COMPARISONS = 6
MULTIPLE_TESTING_PSR_GATE = 1.0 - 0.05 / LOCKED_COMPARISONS
DEFAULT_INITIAL_TRAIN_DATES = 504
DEFAULT_TEST_WINDOW_DATES = 63
DEFAULT_MIN_TRAIN_ROWS = 20_000
CALIBRATION_FRACTION = 0.20
DAILY_CONFIDENCE_QUANTILE = 0.70
DEFAULT_TOP_K = 3
MAX_PER_INDUSTRY = 1
DRAWDOWN_PENALTY = 0.25

CROSS_SECTIONAL_RANK_FEATURES = (
    "return_1d",
    "return_5d",
    "return_20d",
    "return_60d",
    "momentum_20_ex_5",
    "momentum_60_ex_5",
    "distance_ma20_pct",
    "distance_ma60_pct",
    "distance_ma200_pct",
    "distance_high_252_pct",
    "volatility_20_ann_pct",
    "atr_14_pct",
    "rsi_14",
    "volume_ratio_5",
    "volume_ratio_20",
    "turnover_20d_billion",
    "relative_return_20d",
    "relative_return_60d",
)


@dataclass(frozen=True)
class AlphaSpec:
    horizon: int
    target: str
    top_k: int = DEFAULT_TOP_K
    confidence_quantile: float = DAILY_CONFIDENCE_QUANTILE

    def __post_init__(self):
        if self.horizon not in ALPHA_HORIZONS:
            raise ValueError(f"Unsupported alpha horizon: {self.horizon}")
        if self.target not in {"excess", "downside_utility"}:
            raise ValueError(f"Unsupported alpha target: {self.target}")
        if not 0 < self.confidence_quantile < 1:
            raise ValueError("confidence_quantile must be between zero and one")

    @property
    def key(self):
        target = "alpha" if self.target == "excess" else "utility"
        quantile = round(self.confidence_quantile * 100)
        return f"alpha_v2_{target}_next_open_t{self.horizon}_q{quantile}_k{self.top_k}"

    @property
    def experiment(self):
        target_name = (
            "benchmark excess return"
            if self.target == "excess"
            else "benchmark excess return with future drawdown penalty"
        )
        return ExperimentSpec(
            key=self.key,
            name=f"Liquid-universe next-open T+{self.horizon} {self.target}",
            hypothesis=(
                f"A full liquid-universe model of {target_name} can select a "
                f"diversified daily Top {self.top_k} portfolio with positive "
                "after-cost absolute and benchmark-relative returns, while "
                "abstaining when calibrated confidence is weak."
            ),
            strategy_family="alpha_liquid_universe_v2",
            execution_version=f"{ALPHA_EXECUTION_VERSION}:t{self.horizon}",
            selector="full_liquid_universe_cash_gated_top3",
            evaluation_family=ALPHA_EVALUATION_FAMILY,
            parameters=(
                ("target", self.target),
                ("horizon", self.horizon),
                ("top_k", self.top_k),
                ("max_per_industry", MAX_PER_INDUSTRY),
                ("confidence_quantile", self.confidence_quantile),
                ("drawdown_penalty", DRAWDOWN_PENALTY),
                ("legacy_rule_prefilter", False),
                ("holdout_used_for_selection", False),
            ),
        )


def locked_specs():
    return tuple(
        AlphaSpec(horizon=horizon, target=target)
        for target in ("excess", "downside_utility")
        for horizon in ALPHA_HORIZONS
    )


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_alpha_dataset(path=DEFAULT_OUTPUT):
    path = Path(path)
    if not path.exists():
        return pd.DataFrame()
    metadata_path = path.with_suffix(path.suffix + ".metadata.json")
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        expected_hash = metadata.get("sha256")
        if expected_hash and _sha256(path) != expected_hash:
            raise ValueError("Alpha dataset hash does not match its metadata")
        if metadata.get("dataset_version") != ALPHA_DATASET_VERSION:
            raise ValueError("Alpha dataset version is not supported")
    frame = pd.read_csv(path, dtype={"trade_date": str, "code": str})
    required = {
        "trade_date",
        "code",
        "industry",
        "execution_version",
        *ALPHA_FEATURES,
    }
    for horizon in ALPHA_HORIZONS:
        required.update(
            {
                f"net_return_{horizon}d",
                f"excess_return_{horizon}d",
                f"max_drawdown_{horizon}d",
            }
        )
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError("Alpha dataset missing columns: " + ", ".join(missing))
    frame = frame[frame["execution_version"] == ALPHA_EXECUTION_VERSION].copy()
    frame["trade_date"] = frame["trade_date"].astype(str)
    frame["code"] = frame["code"].astype(str)
    frame["industry"] = frame["industry"].fillna("其他").astype(str)
    numeric = [*ALPHA_FEATURES]
    for horizon in ALPHA_HORIZONS:
        numeric.extend(
            [
                f"net_return_{horizon}d",
                f"excess_return_{horizon}d",
                f"max_drawdown_{horizon}d",
            ]
        )
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.sort_values(["trade_date", "code"], kind="stable")


def _model():
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            (
                "regressor",
                HistGradientBoostingRegressor(
                    learning_rate=0.04,
                    max_iter=140,
                    max_leaf_nodes=15,
                    min_samples_leaf=100,
                    l2_regularization=3.0,
                    random_state=42,
                ),
            ),
        ]
    )


def _feature_frame(frame):
    features = frame[list(ALPHA_FEATURES)].copy()
    ranked = frame[list(CROSS_SECTIONAL_RANK_FEATURES)].groupby(
        frame["trade_date"], sort=False
    ).rank(method="average", pct=True)
    ranked.columns = [f"{column}_cs_rank" for column in ranked.columns]
    return pd.concat([features, ranked], axis=1)


def _target(frame, spec):
    excess = frame[f"excess_return_{spec.horizon}d"]
    if spec.target == "excess":
        return excess
    drawdown = frame[f"max_drawdown_{spec.horizon}d"].clip(upper=0.0)
    return excess + DRAWDOWN_PENALTY * drawdown


def _calibration_partition(train_dates, horizon):
    dates = list(sorted(set(str(value) for value in train_dates)))
    calibration_start = int(len(dates) * (1.0 - CALIBRATION_FRACTION))
    fit_end = max(0, calibration_start - horizon)
    return dates[:fit_end], dates[fit_end:calibration_start], dates[calibration_start:]


def _anti_chase_pool(frame):
    return frame[
        (frame["return_1d"] <= 6.5)
        & (frame["volume_ratio_5"] <= 3.5)
        & (frame["distance_ma20_pct"] <= 12.0)
        & (frame["intraday_position"] >= 0.20)
    ].copy()


def _diversified_top(frame, prediction_column, top_k):
    rows = []
    for _, daily in frame.groupby("trade_date", sort=True):
        industries = set()
        selected = []
        ordered = daily.sort_values(
            [prediction_column, "turnover_20d_billion", "code"],
            ascending=[False, False, True],
            kind="stable",
        )
        for index, row in ordered.iterrows():
            industry = str(row.get("industry") or "其他")
            if industry in industries:
                continue
            industries.add(industry)
            selected.append(index)
            if len(selected) >= top_k:
                break
        if selected:
            rows.append(frame.loc[selected])
    return pd.concat(rows).sort_values(["trade_date", prediction_column]) if rows else frame.iloc[0:0].copy()


def _daily_confidence(frame, prediction_column):
    return frame.groupby("trade_date", sort=True)[prediction_column].mean()


def fit_alpha_phase(
    frame,
    train_dates,
    evaluation_dates,
    spec,
    min_train_rows=DEFAULT_MIN_TRAIN_ROWS,
):
    net_column = f"net_return_{spec.horizon}d"
    excess_column = f"excess_return_{spec.horizon}d"
    drawdown_column = f"max_drawdown_{spec.horizon}d"
    train = frame[
        frame["trade_date"].isin(set(train_dates))
        & frame[net_column].notna()
        & frame[excess_column].notna()
        & frame[drawdown_column].notna()
    ].copy()
    evaluation = _anti_chase_pool(
        frame[frame["trade_date"].isin(set(evaluation_dates))].copy()
    )
    empty = {
        "train_rows": int(len(train)),
        "fit_rows": 0,
        "calibration_rows": 0,
        "evaluation_candidates": int(len(evaluation)),
        "selected_candidates": 0,
        "confidence_threshold": None,
        "participating_trade_dates": 0,
        "abstained_trade_dates": int(len(set(evaluation_dates))),
        "participation_rate_pct": 0.0,
    }
    if len(train) < min_train_rows or evaluation.empty:
        return pd.DataFrame(), empty

    fit_dates, calibration_embargo, calibration_dates = _calibration_partition(
        train_dates, spec.horizon
    )
    fit = train[train["trade_date"].isin(set(fit_dates))].copy()
    calibration = _anti_chase_pool(
        train[train["trade_date"].isin(set(calibration_dates))].copy()
    )
    if len(fit) < min_train_rows or calibration.empty:
        return pd.DataFrame(), {
            **empty,
            "fit_rows": int(len(fit)),
            "calibration_rows": int(len(calibration)),
            "calibration_embargo_trade_dates": len(calibration_embargo),
        }

    target = _target(fit, spec)
    lower, upper = target.quantile([0.01, 0.99])
    model = _model()
    model.fit(_feature_frame(fit), target.clip(lower=lower, upper=upper))
    prediction_column = "predicted_alpha"
    calibration[prediction_column] = model.predict(_feature_frame(calibration))
    calibration_top = _diversified_top(
        calibration, prediction_column, spec.top_k
    )
    calibration_confidence = _daily_confidence(calibration_top, prediction_column)
    if calibration_confidence.empty:
        return pd.DataFrame(), {
            **empty,
            "fit_rows": int(len(fit)),
            "calibration_rows": int(len(calibration)),
            "calibration_embargo_trade_dates": len(calibration_embargo),
        }
    threshold = max(
        0.0,
        float(calibration_confidence.quantile(spec.confidence_quantile)),
    )

    evaluation[prediction_column] = model.predict(_feature_frame(evaluation))
    selected = _diversified_top(evaluation, prediction_column, spec.top_k)
    confidence = _daily_confidence(selected, prediction_column)
    active_dates = set(confidence[confidence > threshold].index.astype(str))
    selected = selected[selected["trade_date"].isin(active_dates)].copy()
    selected["net_return_3d"] = selected[net_column]
    selected["excess_return_3d"] = selected[excess_column]
    selected["max_drawdown_3d"] = selected[drawdown_column]
    selected = selected[
        selected["net_return_3d"].notna()
        & selected["excess_return_3d"].notna()
        & selected["max_drawdown_3d"].notna()
    ].copy()
    decision_dates = int(len(set(evaluation_dates)))
    participating = int(selected["trade_date"].nunique())
    return selected, {
        "train_rows": int(len(train)),
        "fit_rows": int(len(fit)),
        "calibration_rows": int(len(calibration)),
        "calibration_embargo_trade_dates": len(calibration_embargo),
        "evaluation_candidates": int(len(evaluation)),
        "selected_candidates": int(len(selected)),
        "confidence_threshold": threshold,
        "participating_trade_dates": participating,
        "abstained_trade_dates": decision_dates - participating,
        "participation_rate_pct": (
            participating / decision_dates * 100.0 if decision_dates else 0.0
        ),
        "target_winsor_low": float(lower),
        "target_winsor_high": float(upper),
    }


def _append_reason(reasons, condition, reason):
    if condition and reason not in reasons:
        reasons.append(reason)


def _walk_forward_fold_stability(selected, fold_diagnostics):
    diagnostics = []
    profitable = []
    for item in fold_diagnostics:
        fold = int(item["fold"])
        if "fold_index" in selected.columns:
            fold_frame = selected[selected["fold_index"] == fold]
        else:
            fold_frame = selected.iloc[0:0]
        mean_net = (
            float(fold_frame["net_return_3d"].mean())
            if not fold_frame.empty
            else 0.0
        )
        mean_excess = (
            float(fold_frame["excess_return_3d"].mean())
            if not fold_frame.empty
            else 0.0
        )
        is_profitable = mean_excess > 0.0
        profitable.append(is_profitable)
        diagnostics.append(
            {
                **item,
                "selected_trades": int(len(fold_frame)),
                "selected_trade_dates": int(fold_frame["trade_date"].nunique()),
                "mean_net_return": mean_net,
                "mean_excess_return": mean_excess,
                "profitable": is_profitable,
            }
        )
    rate = float(np.mean(profitable)) if profitable else None
    return rate, diagnostics


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
        min_trades=180,
        min_probabilistic_sharpe=MULTIPLE_TESTING_PSR_GATE,
        max_drawdown=-12.0,
        min_profitable_fold_rate=0.60,
    )
    partitions = replay_temporal_partitions(
        frame, embargo_trade_dates=spec.horizon
    )
    research_dates = list(partitions["development"]) + list(partitions["validation"])
    selected_folds = []
    fold_diagnostics = []
    evaluation_dates = []
    for fold_index, start in enumerate(
        range(initial_train_dates, len(research_dates), test_window_dates), start=1
    ):
        train_end = max(0, start - spec.horizon)
        train_dates = research_dates[:train_end]
        test_dates = research_dates[start : start + test_window_dates]
        if not test_dates:
            continue
        selected, diagnostics = fit_alpha_phase(
            frame,
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
                "embargo_trade_dates": spec.horizon,
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
            ]
        )
    )
    metrics, reasons = evaluate_frame(
        selected,
        gates=gates,
        decision_horizon=spec.horizon,
        observation_dates=evaluation_dates,
    )
    fold_stability, fold_diagnostics = _walk_forward_fold_stability(
        selected, fold_diagnostics
    )
    metrics["folds"] = len(fold_diagnostics)
    metrics["profitable_fold_rate"] = fold_stability
    reasons = [reason for reason in reasons if reason != "fold_stability_gate_failed"]
    _append_reason(
        reasons,
        fold_stability is None
        or fold_stability < gates.min_profitable_fold_rate,
        "fold_stability_gate_failed",
    )
    _append_reason(
        reasons,
        metrics.get("mean_daily_net_return") is None
        or metrics["mean_daily_net_return"] <= 0,
        "non_positive_daily_net_return",
    )
    _append_reason(
        reasons,
        metrics.get("mean_daily_excess_return") is None
        or metrics["mean_daily_excess_return"] <= 0,
        "non_positive_daily_excess_return",
    )
    _append_reason(reasons, len(fold_diagnostics) < 4, "insufficient_walk_forward_folds")
    metrics.update(
        {
            "sample_start": evaluation_dates[0] if evaluation_dates else None,
            "sample_end": evaluation_dates[-1] if evaluation_dates else None,
            "evaluation_fingerprint": dataset_fingerprint[:10],
            "dataset_fingerprint": dataset_fingerprint,
            "dataset_rows": int(len(frame)),
            "model_version": ALPHA_MODEL_VERSION,
            "entry_method": "next_open",
            "holding_horizon": spec.horizon,
            "ranking_target": spec.target,
            "top_k": spec.top_k,
            "prediction_quantile": spec.confidence_quantile,
            "walk_forward_folds": len(fold_diagnostics),
            "fold_diagnostics": fold_diagnostics,
            "formal_baseline_mean_daily_net_return": 0.0,
            "formal_baseline_mean_daily_excess_return": 0.0,
            "formal_net_lift": metrics.get("mean_daily_net_return"),
            "formal_excess_lift": metrics.get("mean_daily_excess_return"),
            "reserved_holdout_start": (
                partitions["holdout"][0] if partitions["holdout"] else None
            ),
            "reserved_holdout_end": (
                partitions["holdout"][-1] if partitions["holdout"] else None
            ),
            "reserved_holdout_trade_dates": len(partitions["holdout"]),
            "holdout_evaluated": False,
        }
    )
    experiment = spec.experiment
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
        "name": experiment.name,
        "evaluationVersion": version,
        "qualified": not reasons,
        "rejectionReasons": reasons,
        **metrics,
    }


def evaluate_reserved_holdout(
    frame,
    spec,
    min_train_rows=DEFAULT_MIN_TRAIN_ROWS,
    gates=None,
):
    gates = gates or PromotionGates(
        min_trade_dates=40,
        min_trades=60,
        min_probabilistic_sharpe=0.95,
        max_drawdown=-12.0,
        min_profitable_fold_rate=0.60,
    )
    partitions = replay_temporal_partitions(frame, embargo_trade_dates=spec.horizon)
    train_dates = list(partitions["development"]) + list(partitions["validation"])
    evaluation_dates = list(partitions["holdout"])
    selected, diagnostics = fit_alpha_phase(
        frame,
        train_dates,
        evaluation_dates,
        spec,
        min_train_rows=min_train_rows,
    )
    metrics, reasons = evaluate_frame(
        selected,
        gates=gates,
        decision_horizon=spec.horizon,
        observation_dates=evaluation_dates,
    )
    _append_reason(
        reasons,
        metrics.get("mean_daily_net_return") is None
        or metrics["mean_daily_net_return"] <= 0,
        "non_positive_daily_net_return",
    )
    _append_reason(
        reasons,
        metrics.get("mean_daily_excess_return") is None
        or metrics["mean_daily_excess_return"] <= 0,
        "non_positive_daily_excess_return",
    )
    return {
        "qualified": not reasons,
        "rejectionReasons": reasons,
        "sampleStart": evaluation_dates[0] if evaluation_dates else None,
        "sampleEnd": evaluation_dates[-1] if evaluation_dates else None,
        **metrics,
        **diagnostics,
    }


def build_deployment_artifact(frame, spec, dataset_fingerprint):
    dates = sorted(frame["trade_date"].dropna().astype(str).unique())
    fit_dates, calibration_embargo, calibration_dates = _calibration_partition(
        dates, spec.horizon
    )
    net_column = f"net_return_{spec.horizon}d"
    excess_column = f"excess_return_{spec.horizon}d"
    drawdown_column = f"max_drawdown_{spec.horizon}d"
    labelled = frame[
        frame[net_column].notna()
        & frame[excess_column].notna()
        & frame[drawdown_column].notna()
    ].copy()
    fit = labelled[labelled["trade_date"].isin(set(fit_dates))].copy()
    calibration = _anti_chase_pool(
        labelled[labelled["trade_date"].isin(set(calibration_dates))].copy()
    )
    if len(fit) < DEFAULT_MIN_TRAIN_ROWS or calibration.empty:
        raise ValueError("Insufficient labelled rows for the Alpha v2 model artifact")

    target = _target(fit, spec)
    lower, upper = target.quantile([0.01, 0.99])
    model = _model()
    model.fit(_feature_frame(fit), target.clip(lower=lower, upper=upper))
    prediction_column = "predicted_alpha"
    calibration[prediction_column] = model.predict(_feature_frame(calibration))
    calibration_top = _diversified_top(
        calibration, prediction_column, spec.top_k
    )
    confidence = _daily_confidence(calibration_top, prediction_column)
    if confidence.empty:
        raise ValueError("Alpha v2 deployment calibration produced no daily scores")
    threshold = max(
        0.0,
        float(confidence.quantile(spec.confidence_quantile)),
    )
    return {
        "artifact_version": ALPHA_MODEL_ARTIFACT_VERSION,
        "model_version": ALPHA_MODEL_VERSION,
        "dataset_version": ALPHA_DATASET_VERSION,
        "execution_version": ALPHA_EXECUTION_VERSION,
        "dataset_fingerprint": dataset_fingerprint,
        "spec": asdict(spec),
        "feature_columns": list(_feature_frame(fit.iloc[:1]).columns),
        "confidence_threshold": threshold,
        "fit_start": fit_dates[0],
        "fit_end": fit_dates[-1],
        "fit_rows": int(len(fit)),
        "calibration_start": calibration_dates[0],
        "calibration_end": calibration_dates[-1],
        "calibration_rows": int(len(calibration)),
        "calibration_embargo_trade_dates": len(calibration_embargo),
        "target_winsor_low": float(lower),
        "target_winsor_high": float(upper),
        "sklearn_version": sklearn.__version__,
        "dependency_versions": {
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scikit_learn": sklearn.__version__,
            "joblib": joblib.__version__,
        },
        "model": model,
    }


def save_deployment_artifact(artifact, output_path=DEFAULT_MODEL_OUTPUT):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, output_path, compress=3)
    return {
        key: value
        for key, value in artifact.items()
        if key != "model"
    } | {
        "path": str(output_path),
        "sha256": _sha256(output_path),
        "bytes": output_path.stat().st_size,
    }


def _rank_results(results):
    return sorted(
        results,
        key=lambda row: (
            float(row.get("mean_daily_excess_return") or -999.0),
            float(row.get("mean_daily_net_return") or -999.0),
            float(row.get("probabilistic_sharpe") or -999.0),
        ),
        reverse=True,
    )


def run_alpha_strategy_v2(
    dataset_path=DEFAULT_OUTPUT,
    db_path=DB_PATH,
    specs=None,
    initial_train_dates=DEFAULT_INITIAL_TRAIN_DATES,
    test_window_dates=DEFAULT_TEST_WINDOW_DATES,
    min_train_rows=DEFAULT_MIN_TRAIN_ROWS,
    model_output=None,
):
    dataset_path = Path(dataset_path)
    frame = load_alpha_dataset(dataset_path)
    if frame.empty:
        raise ValueError(f"Alpha dataset is empty: {dataset_path}")
    fingerprint = _sha256(dataset_path)
    active_specs = tuple(specs or locked_specs())
    candidates = [
        evaluate_walk_forward_spec(
            frame,
            spec,
            fingerprint,
            db_path=db_path,
            initial_train_dates=initial_train_dates,
            test_window_dates=test_window_dates,
            min_train_rows=min_train_rows,
        )
        for spec in active_specs
    ]
    ranked = _rank_results(candidates)
    prequalified = [row for row in ranked if row.get("qualified")]
    selected = prequalified[0] if prequalified else None
    holdout = None
    if selected is not None:
        selected_spec = next(
            spec for spec in active_specs if spec.key == selected["experimentKey"]
        )
        holdout = evaluate_reserved_holdout(
            frame,
            selected_spec,
            min_train_rows=min_train_rows,
        )
        selected["holdout_evaluated"] = True
        selected["holdout"] = holdout
        if not holdout["qualified"]:
            selected["qualified"] = False
            selected["rejectionReasons"] = list(
                dict.fromkeys(
                    [*selected["rejectionReasons"], "reserved_holdout_gate_failed"]
                )
            )

    for candidate in prequalified:
        candidate["prequalified"] = True
        if candidate is not selected:
            candidate["qualified"] = False
            candidate["rejectionReasons"] = list(
                dict.fromkeys(
                    [
                        *candidate["rejectionReasons"],
                        "reserved_holdout_not_selected",
                    ]
                )
            )

    promoted = selected if selected is not None and selected.get("qualified") else None
    model_artifact = None
    if promoted and model_output:
        selected_spec = next(
            spec for spec in active_specs if spec.key == promoted["experimentKey"]
        )
        model_artifact = save_deployment_artifact(
            build_deployment_artifact(frame, selected_spec, fingerprint),
            model_output,
        )
    diagnostic = ranked[0] if ranked else None
    payload = {
        "version": ALPHA_CHALLENGER_VERSION,
        "datasetFingerprint": fingerprint,
        "datasetRows": int(len(frame)),
        "datasetStart": str(frame["trade_date"].min()),
        "datasetEnd": str(frame["trade_date"].max()),
        "lockedComparisons": LOCKED_COMPARISONS,
        "multipleTestingPsrGate": MULTIPLE_TESTING_PSR_GATE,
        "selectionUsesHoldout": False,
        "formalRankingEnabled": False,
        "legacyRulePrefilter": False,
        "candidateUniverse": "point_in_time_liquid_equities",
        "status": "prospective_shadow_ready" if promoted else "blocked",
        "recommendationMode": "shadow" if promoted else "cash",
        "selectedExperimentKey": promoted.get("experimentKey") if promoted else None,
        "diagnosticLeaderKey": diagnostic.get("experimentKey") if diagnostic else None,
        "qualifiedCandidates": 1 if promoted else 0,
        "prequalifiedCandidates": len(prequalified),
        "candidateCount": len(ranked),
        "candidateLeaderboard": ranked,
        "holdout": holdout,
        "modelArtifact": model_artifact,
        "executionMatrix": [],
    }
    payload["evaluatedAt"] = save_strategy_snapshot(
        payload,
        fingerprint,
        db_path=db_path,
        challenger_version=ALPHA_CHALLENGER_VERSION,
    )
    return payload


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate Alpha v2 on the full liquid universe with a reserved holdout."
    )
    parser.add_argument("--dataset", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--initial-train-dates", type=int, default=DEFAULT_INITIAL_TRAIN_DATES)
    parser.add_argument("--test-window-dates", type=int, default=DEFAULT_TEST_WINDOW_DATES)
    parser.add_argument("--min-train-rows", type=int, default=DEFAULT_MIN_TRAIN_ROWS)
    parser.add_argument("--output")
    parser.add_argument("--model-output")
    args = parser.parse_args()
    result = run_alpha_strategy_v2(
        dataset_path=args.dataset,
        db_path=args.db,
        initial_train_dates=args.initial_train_dates,
        test_window_dates=args.test_window_dates,
        min_train_rows=args.min_train_rows,
        model_output=args.model_output,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
