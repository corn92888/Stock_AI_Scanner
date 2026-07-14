import json
import math
from dataclasses import asdict, dataclass

import pandas as pd

from database import DB_PATH, get_connection, get_taipei_now, init_db


DECISION_HORIZON = 3


@dataclass(frozen=True)
class ShadowSelectionPolicy:
    min_probability: float = 0.35
    min_expected_excess: float = 0.0
    min_expected_drawdown: float = -4.0
    max_daily_selections: int = 3
    max_per_industry: int = 1


@dataclass(frozen=True)
class ChallengerGates:
    min_oof_trade_dates: int = 30
    min_challenger_trades: int = 60
    min_mean_net_return: float = 0.0
    min_mean_excess_return: float = 0.0
    min_excess_return_lift: float = 0.0
    max_drawdown: float = -12.0
    min_profitable_fold_rate: float = 0.60


def walk_forward_splits(
    frame,
    min_train_dates=20,
    test_dates=5,
    embargo_trade_dates=3,
):
    dates = sorted(str(value) for value in frame["trade_date"].dropna().unique())
    first_validation = int(min_train_dates) + int(embargo_trade_dates)
    folds = []
    for validation_start in range(first_validation, len(dates), int(test_dates)):
        train_end = validation_start - int(embargo_trade_dates)
        validation_end = min(len(dates), validation_start + int(test_dates))
        training_dates = set(dates[:train_end])
        embargo_dates = dates[train_end:validation_start]
        validation_dates = set(dates[validation_start:validation_end])
        train = frame[frame["trade_date"].astype(str).isin(training_dates)].copy()
        validation = frame[
            frame["trade_date"].astype(str).isin(validation_dates)
        ].copy()
        if train.empty or validation.empty:
            continue
        folds.append(
            {
                "fold_index": len(folds) + 1,
                "train": train,
                "validation": validation,
                "embargo_dates": embargo_dates,
                "trained_through": dates[train_end - 1],
            }
        )
    return folds


def score_prediction(probability, expected_excess, expected_drawdown):
    def sigmoid(value, scale):
        bounded = max(-30.0, min(30.0, float(value) / scale))
        return 1.0 / (1.0 + math.exp(-bounded))

    return 100.0 * (
        0.70 * float(probability)
        + 0.20 * sigmoid(expected_excess, 3.0)
        + 0.10 * sigmoid(float(expected_drawdown) + 4.0, 2.0)
    )


def apply_shadow_selection(frame, policy=None):
    policy = policy or ShadowSelectionPolicy()
    result = frame.copy()
    result["is_selected"] = False
    for _, daily in result.groupby("trade_date", sort=True):
        eligible = daily[
            (daily["tradable"].fillna(0).astype(int) == 1)
            & (daily["is_first_eligible_event"].fillna(0).astype(int) == 1)
            & (daily["probability_t3"] >= policy.min_probability)
            & (daily["expected_excess_return_3d"] >= policy.min_expected_excess)
            & (
                daily["expected_max_drawdown_3d"]
                >= policy.min_expected_drawdown
            )
        ].sort_values("final_score", ascending=False)
        industry_counts = {}
        selected = 0
        for index, row in eligible.iterrows():
            industry = str(row.get("industry") or "")
            if (
                industry
                and industry_counts.get(industry, 0) >= policy.max_per_industry
            ):
                continue
            result.at[index, "is_selected"] = True
            selected += 1
            if industry:
                industry_counts[industry] = industry_counts.get(industry, 0) + 1
            if selected >= policy.max_daily_selections:
                break
    return result


def _portfolio_max_drawdown(frame):
    if frame.empty:
        return None
    daily = frame.groupby("trade_date")["net_return_3d"].mean()
    daily = daily / DECISION_HORIZON / 100.0
    equity = (1.0 + daily).cumprod()
    return float(((equity / equity.cummax()) - 1.0).min() * 100.0)


def evaluate_challenger(oof_frame, gates=None):
    gates = gates or ChallengerGates()
    challenger = oof_frame[oof_frame["is_selected"].astype(bool)].copy()
    champion = oof_frame[oof_frame["rule_selected"].fillna(0).astype(int) == 1].copy()
    fold_results = []
    for fold_index in sorted(oof_frame["fold_index"].unique()):
        fold = challenger[challenger["fold_index"] == fold_index]
        fold_results.append(
            bool(not fold.empty and float(fold["excess_return_3d"].mean()) > 0)
        )

    def mean(frame, column):
        return float(frame[column].mean()) if not frame.empty else None

    challenger_net = mean(challenger, "net_return_3d")
    challenger_excess = mean(challenger, "excess_return_3d")
    champion_net = mean(champion, "net_return_3d")
    champion_excess = mean(champion, "excess_return_3d")
    net_lift = (
        challenger_net - champion_net
        if challenger_net is not None and champion_net is not None
        else None
    )
    excess_lift = (
        challenger_excess - champion_excess
        if challenger_excess is not None and champion_excess is not None
        else None
    )
    metrics = {
        "oof_trade_dates": int(oof_frame["trade_date"].nunique()),
        "oof_candidates": int(len(oof_frame)),
        "challenger_trades": int(len(challenger)),
        "champion_trades": int(len(champion)),
        "challenger_mean_net_return": challenger_net,
        "challenger_mean_excess_return": challenger_excess,
        "champion_mean_net_return": champion_net,
        "champion_mean_excess_return": champion_excess,
        "net_return_lift": net_lift,
        "excess_return_lift": excess_lift,
        "challenger_max_drawdown": _portfolio_max_drawdown(challenger),
        "profitable_fold_rate": (
            float(sum(fold_results) / len(fold_results)) if fold_results else None
        ),
    }
    reasons = []
    if metrics["oof_trade_dates"] < gates.min_oof_trade_dates:
        reasons.append("insufficient_oof_trade_dates")
    if metrics["challenger_trades"] < gates.min_challenger_trades:
        reasons.append("insufficient_challenger_trades")
    if challenger_net is None or challenger_net <= gates.min_mean_net_return:
        reasons.append("non_positive_challenger_net_return")
    if challenger_excess is None or challenger_excess <= gates.min_mean_excess_return:
        reasons.append("non_positive_challenger_excess_return")
    if excess_lift is None or excess_lift <= gates.min_excess_return_lift:
        reasons.append("challenger_does_not_beat_champion")
    if (
        metrics["challenger_max_drawdown"] is None
        or metrics["challenger_max_drawdown"] < gates.max_drawdown
    ):
        reasons.append("challenger_drawdown_gate_failed")
    if (
        metrics["profitable_fold_rate"] is None
        or metrics["profitable_fold_rate"] < gates.min_profitable_fold_rate
    ):
        reasons.append("challenger_fold_stability_failed")
    return metrics, reasons


def save_model_governance(
    model_version,
    oof_frame,
    metrics,
    reasons,
    selection_policy=None,
    gates=None,
    db_path=DB_PATH,
):
    selection_policy = selection_policy or ShadowSelectionPolicy()
    gates = gates or ChallengerGates()
    now = get_taipei_now().isoformat(timespec="seconds")
    status = "promotion_review" if not reasons else "shadow"
    with get_connection(db_path) as conn:
        init_db(conn)
        for _, row in oof_frame.iterrows():
            conn.execute(
                """
                INSERT INTO model_validation_predictions (
                    model_version, feature_id, trade_date, code, fold_index,
                    trained_through, probability_t3,
                    expected_excess_return_3d,
                    expected_max_drawdown_3d, final_score, is_selected,
                    actual_success_t3, actual_net_return_3d,
                    actual_excess_return_3d, actual_max_drawdown_3d,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(model_version, feature_id) DO NOTHING
                """,
                (
                    model_version,
                    int(row["feature_id"]),
                    str(row["trade_date"]),
                    str(row["code"]),
                    int(row["fold_index"]),
                    str(row["trained_through"]),
                    float(row["probability_t3"]),
                    float(row["expected_excess_return_3d"]),
                    float(row["expected_max_drawdown_3d"]),
                    float(row["final_score"]),
                    int(bool(row["is_selected"])),
                    int(bool(row["success_t3"])),
                    float(row["net_return_3d"]),
                    float(row["excess_return_3d"]),
                    float(row["max_drawdown_3d"]),
                    now,
                ),
            )
        payload = {
            **metrics,
            "selection_policy": asdict(selection_policy),
            "gates": asdict(gates),
        }
        conn.execute(
            """
            INSERT INTO model_challenger_evaluations (
                model_version, evaluated_at, status, oof_trade_dates,
                oof_candidates, challenger_trades, champion_trades,
                challenger_mean_net_return, challenger_mean_excess_return,
                champion_mean_net_return, champion_mean_excess_return,
                net_return_lift, excess_return_lift,
                challenger_max_drawdown, profitable_fold_rate, qualified,
                rejection_reasons_json, metrics_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(model_version) DO NOTHING
            """,
            (
                model_version,
                now,
                status,
                metrics["oof_trade_dates"],
                metrics["oof_candidates"],
                metrics["challenger_trades"],
                metrics["champion_trades"],
                metrics["challenger_mean_net_return"],
                metrics["challenger_mean_excess_return"],
                metrics["champion_mean_net_return"],
                metrics["champion_mean_excess_return"],
                metrics["net_return_lift"],
                metrics["excess_return_lift"],
                metrics["challenger_max_drawdown"],
                metrics["profitable_fold_rate"],
                int(not reasons),
                json.dumps(reasons, sort_keys=True),
                json.dumps(payload, sort_keys=True),
                now,
            ),
        )
        conn.execute(
            "UPDATE model_versions SET status=? WHERE version=?",
            (status, model_version),
        )
        conn.commit()
    return {"status": status, "qualified": not reasons, **metrics}
