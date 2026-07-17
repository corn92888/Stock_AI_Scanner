import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from ai_pipeline import MODEL_FEATURES
from cross_sectional_research import (
    ALPHA_PREDICTION_QUANTILE,
    CROSS_SECTIONAL_FEATURE_MODE,
    RankingSpec,
    _calibration_partition,
    _feature_frame,
    _label_columns,
    _model,
    _peer_rank_target,
    _target_values,
    load_ranking_dataset,
)
from database import DB_PATH
from execution_research import ENTRY_METHODS, EXECUTION_RESEARCH_VERSION, HORIZONS
from research_evaluation import (
    ExperimentSpec,
    register_experiment,
    replay_temporal_partitions,
    save_evaluation,
)


AUDIT_VERSION = "candidate_pool_learnability_audit_v1"
AUDIT_EXPERIMENT_KEY = "candidate_pool_learnability_audit_v1"
AUDIT_EVALUATION_FAMILY = "historical_diagnostic_learnability_v1"
AUDIT_HORIZONS = (5, 10)
AUDIT_TOP_K = 3
AUDIT_MIN_TRAIN_ROWS = 500
AUDIT_MIN_IC_CANDIDATES = 5
AUDIT_EMBARGO_DATES = max(AUDIT_HORIZONS)

AUDIT_EXPERIMENT = ExperimentSpec(
    key=AUDIT_EXPERIMENT_KEY,
    name="Candidate-pool learnability audit",
    hypothesis=(
        "Diagnose whether historical underperformance originates in candidate "
        "opportunity, feature rankability, portfolio construction, or execution."
    ),
    strategy_family="diagnostic_learnability",
    execution_version=EXECUTION_RESEARCH_VERSION,
    selector="all_point_in_time_tradable_candidates",
    evaluation_family=AUDIT_EVALUATION_FAMILY,
    parameters=(
        ("methods", ENTRY_METHODS),
        ("horizons", AUDIT_HORIZONS),
        ("top_k", AUDIT_TOP_K),
        ("training_scope", "development"),
        ("evaluation_scope", "validation"),
        ("holdout_evaluated", False),
        ("formal_ranking_enabled", False),
    ),
)


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_number(value):
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _max_drawdown(daily_returns):
    values = pd.Series(daily_returns, dtype=float).fillna(0.0) / 100.0
    if values.empty:
        return None
    equity = (1.0 + values).cumprod()
    drawdown = (equity / equity.cummax() - 1.0) * 100.0
    return _safe_number(drawdown.min())


def _profitable_fold_rate(daily_returns, fold_count=5):
    values = pd.Series(daily_returns, dtype=float)
    if values.empty:
        return None, 0
    folds = [fold for fold in np.array_split(values, min(fold_count, len(values))) if len(fold)]
    return _safe_number(np.mean([fold.mean() > 0 for fold in folds])), len(folds)


def _portfolio_metrics(frame, evaluation_dates, net_column, excess_column):
    dates = pd.Index([str(value) for value in evaluation_dates], name="trade_date")
    filled = frame[
        frame[net_column].notna() & frame[excess_column].notna()
    ].copy()
    net = (
        filled.groupby("trade_date", sort=True)[net_column]
        .mean()
        .reindex(dates, fill_value=0.0)
    )
    excess = (
        filled.groupby("trade_date", sort=True)[excess_column]
        .mean()
        .reindex(dates, fill_value=0.0)
    )
    profitable_fold_rate, folds = _profitable_fold_rate(net)
    participating = int(filled["trade_date"].nunique())
    return {
        "trades": int(len(filled)),
        "decision_dates": int(len(dates)),
        "participating_dates": participating,
        "participation_rate_pct": (
            _safe_number(participating / len(dates) * 100) if len(dates) else 0.0
        ),
        "mean_daily_net_return": _safe_number(net.mean()),
        "mean_daily_excess_return": _safe_number(excess.mean()),
        "positive_day_rate": _safe_number((net > 0).mean() * 100),
        "max_drawdown": _max_drawdown(net),
        "profitable_fold_rate": profitable_fold_rate,
        "folds": folds,
    }


def _fit_validation_predictions(
    frame,
    train_dates,
    evaluation_dates,
    spec,
    min_train_rows=AUDIT_MIN_TRAIN_ROWS,
):
    columns = _label_columns(spec)
    train = frame[
        frame["trade_date"].isin(set(train_dates))
        & (frame["tradable"] == 1)
        & frame[columns["excess"]].notna()
    ].copy()
    evaluation = frame[
        frame["trade_date"].isin(set(evaluation_dates))
        & (frame["tradable"] == 1)
    ].copy()
    fit_dates, calibration_embargo, calibration_dates = _calibration_partition(
        train, spec.horizon
    )
    fit = train[train["trade_date"].isin(set(fit_dates))].copy()
    calibration = train[
        train["trade_date"].isin(set(calibration_dates))
    ].copy()
    if len(fit) < min_train_rows or calibration.empty or evaluation.empty:
        raise ValueError(
            f"Learnability audit {spec.key} has insufficient fit, calibration, or "
            "validation samples."
        )
    target, target_diagnostics = _target_values(train, spec, columns)
    model = _model()
    model.fit(_feature_frame(fit, spec), target.loc[fit.index].astype(float))
    calibration_predictions = pd.Series(
        model.predict(_feature_frame(calibration, spec)), index=calibration.index
    )
    threshold = max(
        0.0,
        float(calibration_predictions.quantile(spec.prediction_quantile)),
    )
    evaluation["prediction"] = model.predict(_feature_frame(evaluation, spec))
    eligible = evaluation[evaluation["prediction"] > threshold].copy()
    selected = (
        eligible.sort_values(
            ["trade_date", "prediction", "code"],
            ascending=[True, False, True],
            kind="stable",
        )
        .groupby("trade_date", sort=False)
        .head(spec.top_k)
        .copy()
    )
    return evaluation, selected, {
        "train_rows": int(len(train)),
        "fit_rows": int(len(fit)),
        "calibration_rows": int(len(calibration)),
        "calibration_embargo_trade_dates": len(calibration_embargo),
        "validation_candidates": int(len(evaluation)),
        "eligible_candidates": int(len(eligible)),
        "selected_candidates": int(len(selected)),
        "prediction_threshold": _safe_number(threshold),
        **target_diagnostics,
    }


def _rankability_metrics(evaluation, net_column, excess_column):
    filled = evaluation[
        evaluation[net_column].notna() & evaluation[excess_column].notna()
    ].copy()
    if filled.empty:
        return {
            "ic_dates": 0,
            "mean_rank_ic": None,
            "rank_ic_ci95_low": None,
            "rank_ic_ci95_high": None,
            "positive_rank_ic_rate": None,
            "top_bottom_net_spread": None,
            "top_bottom_excess_spread": None,
            "top_bottom_dates": 0,
        }
    filled["realized_peer_rank"], _ = _peer_rank_target(filled, excess_column)
    correlations = []
    net_spreads = []
    excess_spreads = []
    for _, group in filled.groupby("trade_date", sort=True):
        if (
            len(group) >= AUDIT_MIN_IC_CANDIDATES
            and group["prediction"].nunique() > 1
            and group["realized_peer_rank"].nunique() > 1
        ):
            correlation = group["prediction"].corr(
                group["realized_peer_rank"], method="spearman"
            )
            if pd.notna(correlation):
                correlations.append(float(correlation))
        if len(group) >= AUDIT_MIN_IC_CANDIDATES:
            percentile = group["prediction"].rank(method="average", pct=True)
            top = group[percentile > 0.80]
            bottom = group[percentile <= 0.20]
            if not top.empty and not bottom.empty:
                net_spreads.append(float(top[net_column].mean() - bottom[net_column].mean()))
                excess_spreads.append(
                    float(top[excess_column].mean() - bottom[excess_column].mean())
                )
    ic = pd.Series(correlations, dtype=float)
    ic_mean = _safe_number(ic.mean()) if len(ic) else None
    ic_se = _safe_number(ic.std(ddof=1) / math.sqrt(len(ic))) if len(ic) >= 2 else None
    return {
        "ic_dates": int(len(ic)),
        "mean_rank_ic": ic_mean,
        "rank_ic_ci95_low": (
            _safe_number(ic_mean - 1.96 * ic_se)
            if ic_mean is not None and ic_se is not None
            else None
        ),
        "rank_ic_ci95_high": (
            _safe_number(ic_mean + 1.96 * ic_se)
            if ic_mean is not None and ic_se is not None
            else None
        ),
        "positive_rank_ic_rate": (
            _safe_number((ic > 0).mean() * 100) if len(ic) else None
        ),
        "top_bottom_net_spread": _safe_number(pd.Series(net_spreads).mean()),
        "top_bottom_excess_spread": _safe_number(pd.Series(excess_spreads).mean()),
        "top_bottom_dates": int(len(net_spreads)),
    }


def _overlap_rate(selected, oracle):
    selected_keys = set(zip(selected["trade_date"], selected["code"]))
    oracle_keys = set(zip(oracle["trade_date"], oracle["code"]))
    return (
        _safe_number(len(selected_keys & oracle_keys) / len(selected_keys) * 100)
        if selected_keys
        else None
    )


def _diagnosis(oracle, model, rankability, fill_rate_pct):
    if (oracle["mean_daily_net_return"] or 0.0) <= 0:
        return "candidate_opportunity_gap"
    if (
        rankability["mean_rank_ic"] is None
        or rankability["mean_rank_ic"] <= 0
        or (rankability["top_bottom_excess_spread"] or 0.0) <= 0
    ):
        return "feature_rankability_gap"
    if fill_rate_pct < 50.0:
        return "execution_fill_gap"
    if (
        (model["mean_daily_net_return"] or 0.0) <= 0
        or (model["mean_daily_excess_return"] or 0.0) <= 0
    ):
        return "portfolio_construction_gap"
    return "historical_edge_not_promotable"


def _audit_spec(frame, partitions, method, horizon, min_train_rows):
    spec = RankingSpec(
        method=method,
        horizon=horizon,
        top_k=AUDIT_TOP_K,
        target="peer_rank",
        prediction_quantile=ALPHA_PREDICTION_QUANTILE,
        feature_mode=CROSS_SECTIONAL_FEATURE_MODE,
        feature_columns=tuple(MODEL_FEATURES),
        experiment_key=f"audit_{method}_t{horizon}",
    )
    columns = _label_columns(spec)
    evaluation, selected, training = _fit_validation_predictions(
        frame,
        partitions["development"],
        partitions["validation"],
        spec,
        min_train_rows=min_train_rows,
    )
    filled_pool = evaluation[
        evaluation[columns["net"]].notna()
        & evaluation[columns["excess"]].notna()
    ].copy()
    oracle = (
        filled_pool.sort_values(
            ["trade_date", columns["net"], "code"],
            ascending=[True, False, True],
            kind="stable",
        )
        .groupby("trade_date", sort=False)
        .head(AUDIT_TOP_K)
        .copy()
    )
    formal = filled_pool[filled_pool["rule_selected"] == 1].copy()
    pool_metrics = _portfolio_metrics(
        filled_pool, partitions["validation"], columns["net"], columns["excess"]
    )
    oracle_metrics = _portfolio_metrics(
        oracle, partitions["validation"], columns["net"], columns["excess"]
    )
    formal_metrics = _portfolio_metrics(
        formal, partitions["validation"], columns["net"], columns["excess"]
    )
    model_metrics = _portfolio_metrics(
        selected, partitions["validation"], columns["net"], columns["excess"]
    )
    rankability = _rankability_metrics(evaluation, columns["net"], columns["excess"])
    selected_filled = selected[
        selected[columns["net"]].notna() & selected[columns["excess"]].notna()
    ]
    fill_rate_pct = (
        _safe_number(len(selected_filled) / len(selected) * 100) if len(selected) else 0.0
    )
    positive_candidates = filled_pool[filled_pool[columns["net"]] > 0]
    profitable_by_date = positive_candidates.groupby("trade_date").size()
    profitable_capacity_rate = _safe_number(
        profitable_by_date.reindex(partitions["validation"], fill_value=0)
        .ge(AUDIT_TOP_K)
        .mean()
        * 100
    )
    diagnosis = _diagnosis(oracle_metrics, model_metrics, rankability, fill_rate_pct)
    return {
        "key": f"{method}_t{horizon}",
        "entry_method": method,
        "holding_horizon": horizon,
        "training": training,
        "pool": {
            **pool_metrics,
            "filled_candidates": int(len(filled_pool)),
            "fill_rate_pct": _safe_number(len(filled_pool) / len(evaluation) * 100),
            "positive_candidate_rate": _safe_number(
                (filled_pool[columns["net"]] > 0).mean() * 100
            ),
            "profitable_top_k_capacity_rate_pct": profitable_capacity_rate,
        },
        "oracle": oracle_metrics,
        "formal_rule": formal_metrics,
        "model": {
            **model_metrics,
            "selected_fill_rate_pct": fill_rate_pct,
            "oracle_overlap_rate_pct": _overlap_rate(selected_filled, oracle),
            "opportunity_capture_pct": (
                _safe_number(
                    model_metrics["mean_daily_net_return"]
                    / oracle_metrics["mean_daily_net_return"]
                    * 100
                )
                if oracle_metrics["mean_daily_net_return"]
                else None
            ),
            "oracle_headroom": _safe_number(
                oracle_metrics["mean_daily_net_return"]
                - model_metrics["mean_daily_net_return"]
            ),
        },
        "rankability": rankability,
        "diagnosis": diagnosis,
    }


def build_learnability_audit(dataset_path, min_train_rows=AUDIT_MIN_TRAIN_ROWS):
    dataset_path = Path(dataset_path)
    frame = load_ranking_dataset(dataset_path)
    if frame.empty:
        raise ValueError("No replay training samples are available for audit.")
    if "scenario_version" not in frame.columns:
        raise ValueError("Learnability audit requires multi-horizon execution labels.")
    frame = frame[frame["scenario_version"] == EXECUTION_RESEARCH_VERSION].copy()
    available_horizons = set(HORIZONS)
    if not set(AUDIT_HORIZONS).issubset(available_horizons):
        raise ValueError("Execution research does not provide the locked audit horizons.")
    partitions = replay_temporal_partitions(
        frame, embargo_trade_dates=AUDIT_EMBARGO_DATES
    )
    rows = [
        _audit_spec(frame, partitions, method, horizon, min_train_rows)
        for method in ENTRY_METHODS
        for horizon in AUDIT_HORIZONS
    ]
    primary = next(row for row in rows if row["key"] == "next_open_t5")
    best = max(
        rows,
        key=lambda row: row["model"]["mean_daily_net_return"]
        if row["model"]["mean_daily_net_return"] is not None
        else -math.inf,
    )
    return {
        "audit_version": AUDIT_VERSION,
        "dataset_fingerprint": _sha256(dataset_path),
        "evaluation_scope": "historical_development_validation_diagnostic",
        "training_start": min(partitions["development"]),
        "training_end": max(partitions["development"]),
        "validation_start": min(partitions["validation"]),
        "validation_end": max(partitions["validation"]),
        "training_trade_dates": len(partitions["development"]),
        "validation_trade_dates": len(partitions["validation"]),
        "holdout_evaluated": False,
        "formal_ranking_enabled": False,
        "reserved_holdout_trade_dates": len(partitions["holdout"]),
        "reserved_holdout_start": min(partitions["holdout"]),
        "reserved_holdout_end": max(partitions["holdout"]),
        "primary_spec_key": primary["key"],
        "primary_diagnosis": primary["diagnosis"],
        "best_diagnostic_spec_key": best["key"],
        "primary": primary,
        "rows": rows,
    }


def persist_learnability_audit(report, db_path=DB_PATH):
    primary = report["primary"]
    model = primary["model"]
    metrics = {
        **report,
        "evaluation_fingerprint": report["dataset_fingerprint"][:10],
        "sample_start": report["validation_start"],
        "sample_end": report["validation_end"],
        "trade_dates": model["participating_dates"],
        "trades": model["trades"],
        "folds": model["folds"],
        "mean_net_return": model["mean_daily_net_return"],
        "mean_excess_return": model["mean_daily_excess_return"],
        "positive_rate": model["positive_day_rate"],
        "annualized_sharpe": None,
        "probabilistic_sharpe": None,
        "max_drawdown": model["max_drawdown"],
        "profitable_fold_rate": model["profitable_fold_rate"],
    }
    reasons = [
        "diagnostic_only",
        "holdout_not_evaluated",
        "formal_ranking_disabled",
    ]
    experiment_id = register_experiment(AUDIT_EXPERIMENT, db_path=db_path)
    evaluation_version = save_evaluation(
        experiment_id,
        AUDIT_EXPERIMENT,
        metrics,
        reasons,
        db_path=db_path,
    )
    return evaluation_version


def run_learnability_audit(
    dataset_path,
    db_path=DB_PATH,
    output_path=None,
    min_train_rows=AUDIT_MIN_TRAIN_ROWS,
):
    report = build_learnability_audit(dataset_path, min_train_rows=min_train_rows)
    report["evaluation_version"] = persist_learnability_audit(report, db_path=db_path)
    if output_path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return report


def main():
    parser = argparse.ArgumentParser(
        description="Audit candidate opportunity, rankability, and execution erosion."
    )
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--output")
    args = parser.parse_args()
    result = run_learnability_audit(
        args.dataset,
        db_path=args.db,
        output_path=args.output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
