import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from alpha_strategy_v2 import (
    ALPHA_MODEL_VERSION,
    AlphaSpec,
    _anti_chase_pool,
    _calibration_partition,
    _diversified_top,
    _feature_frame,
    _model,
    _sha256,
    load_alpha_dataset,
)
from database import DB_PATH, get_connection, get_taipei_now, init_db


STRATEGY_VERSION = "alpha_t10_breadth_top1_v1"
EVIDENCE_VERSION = "alpha_t10_breadth_top1_history_2025_v1"
DEFAULT_EVIDENCE_PATH = Path("data/models/deployable_strategy_v1.json")
HOLDING_HORIZON = 10
CONFIDENCE_QUANTILE = 0.70
MIN_MARKET_UP_RATIO = 50.0
MAX_ENTRY_GAP_PCT = 3.0
POSITION_WEIGHT = 0.08
MAX_POSITIONS = 10
MAX_INDUSTRY_POSITIONS = 2
HISTORICAL_YEARS = (2022, 2023, 2024, 2025)
HOLDOUT_YEAR = 2025


def load_evidence(path=DEFAULT_EVIDENCE_PATH):
    path = Path(path)
    if not path.exists():
        return {
            "version": EVIDENCE_VERSION,
            "strategyVersion": STRATEGY_VERSION,
            "qualified": False,
            "manualMicroAllowed": False,
            "reasonCodes": ["historical_evidence_missing"],
            "years": [],
            "holdout": None,
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("strategyVersion") != STRATEGY_VERSION:
        raise ValueError("Deployable strategy evidence version mismatch")
    if payload.get("modelVersion") != ALPHA_MODEL_VERSION:
        raise ValueError("Deployable strategy evidence model version mismatch")
    return payload


def _portfolio_metrics(selected, observation_dates):
    daily = (
        selected.groupby("trade_date")["net_return_10d"]
        .mean()
        .reindex(observation_dates, fill_value=0.0)
        * POSITION_WEIGHT
    )
    equity = (1.0 + daily / 100.0).cumprod()
    drawdown = (equity / equity.cummax() - 1.0) * 100.0
    standard_deviation = float(daily.std(ddof=1)) if len(daily) > 1 else 0.0
    sharpe = (
        float(daily.mean() / standard_deviation * math.sqrt(252))
        if standard_deviation > 0
        else None
    )
    return {
        "activeDays": int(selected["trade_date"].nunique()),
        "trades": int(len(selected)),
        "totalReturnPct": float((equity.iloc[-1] - 1.0) * 100.0),
        "maxDrawdownPct": float(drawdown.min()),
        "annualizedSharpe": sharpe,
        "meanTradeNetReturnPct": float(selected["net_return_10d"].mean()),
        "meanTradeExcessReturnPct": float(selected["excess_return_10d"].mean()),
        "winRatePct": float((selected["net_return_10d"] > 0).mean() * 100.0),
    }


def _evaluate_year(frame, features, dates, year):
    training_dates = [date for date in dates if date < f"{year}-01-01"]
    evaluation_dates = [
        date for date in dates if f"{year}-01-01" <= date <= f"{year}-12-31"
    ]
    fit_dates, _, calibration_dates = _calibration_partition(
        training_dates, HOLDING_HORIZON
    )
    fit = frame[frame["trade_date"].isin(fit_dates)].copy()
    calibration = _anti_chase_pool(
        frame[frame["trade_date"].isin(calibration_dates)].copy()
    )
    evaluation = _anti_chase_pool(
        frame[frame["trade_date"].isin(evaluation_dates)].copy()
    )
    target = fit[f"excess_return_{HOLDING_HORIZON}d"]
    lower, upper = target.quantile([0.01, 0.99])
    model = _model()
    model.fit(features.loc[fit.index], target.clip(lower=lower, upper=upper))

    calibration["predicted_alpha"] = model.predict(features.loc[calibration.index])
    evaluation["predicted_alpha"] = model.predict(features.loc[evaluation.index])
    calibration_top = _diversified_top(calibration, "predicted_alpha", 3)
    evaluation_top = _diversified_top(evaluation, "predicted_alpha", 3)
    calibration_confidence = calibration_top.groupby("trade_date")[
        "predicted_alpha"
    ].mean()
    threshold = max(
        0.0,
        float(calibration_confidence.quantile(CONFIDENCE_QUANTILE)),
    )
    evaluation_confidence = evaluation_top.groupby("trade_date")[
        "predicted_alpha"
    ].mean()
    active_dates = set(
        evaluation_confidence[evaluation_confidence > threshold].index.astype(str)
    )
    selected = evaluation_top[
        evaluation_top["trade_date"].isin(active_dates)
        & (evaluation_top["market_up_ratio"] >= MIN_MARKET_UP_RATIO)
    ].copy()
    selected = (
        selected.sort_values(
            ["trade_date", "predicted_alpha", "turnover_20d_billion", "code"],
            ascending=[True, False, False, True],
            kind="stable",
        )
        .groupby("trade_date", sort=True)
        .head(1)
        .copy()
    )
    selected["entry_gap_pct"] = (
        selected["entry_price"] / selected["signal_price"] - 1.0
    ) * 100.0
    selected = selected[selected["entry_gap_pct"] <= MAX_ENTRY_GAP_PCT].copy()
    return {
        "year": int(year),
        "role": "holdout_audit" if year == HOLDOUT_YEAR else "walk_forward_validation",
        "confidenceThreshold": threshold,
        **_portfolio_metrics(selected, evaluation_dates),
    }


def audit_historical_strategy(dataset_path, output_path=DEFAULT_EVIDENCE_PATH):
    frame = load_alpha_dataset(dataset_path)
    if frame.empty:
        raise ValueError("Deployable strategy audit requires the Alpha universe dataset")
    dates = sorted(frame["trade_date"].dropna().astype(str).unique())
    features = _feature_frame(frame)
    years = [_evaluate_year(frame, features, dates, year) for year in HISTORICAL_YEARS]
    development = [row for row in years if row["year"] != HOLDOUT_YEAR]
    holdout = next(row for row in years if row["year"] == HOLDOUT_YEAR)
    gates = [
        {
            "key": "development_positive_years",
            "passed": all(row["totalReturnPct"] > 0 for row in development),
            "requirement": "2022-2024 each after-cost year > 0%",
        },
        {
            "key": "development_positive_excess",
            "passed": all(
                row["meanTradeExcessReturnPct"] > 0 for row in development
            ),
            "requirement": "2022-2024 each mean trade excess > 0%",
        },
        {
            "key": "development_sample",
            "passed": sum(row["trades"] for row in development) >= 60,
            "requirement": "at least 60 pre-holdout trades",
        },
        {
            "key": "development_drawdown",
            "passed": min(row["maxDrawdownPct"] for row in development) >= -10.0,
            "requirement": "each pre-holdout drawdown no worse than -10%",
        },
        {
            "key": "holdout_return",
            "passed": holdout["totalReturnPct"] > 0,
            "requirement": "2025 after-cost total return > 0%",
        },
        {
            "key": "holdout_excess",
            "passed": holdout["meanTradeExcessReturnPct"] > 0,
            "requirement": "2025 mean trade excess > 0%",
        },
        {
            "key": "holdout_sample",
            "passed": holdout["trades"] >= 20,
            "requirement": "at least 20 holdout trades",
        },
        {
            "key": "holdout_drawdown",
            "passed": holdout["maxDrawdownPct"] >= -10.0,
            "requirement": "2025 drawdown no worse than -10%",
        },
    ]
    qualified = all(gate["passed"] for gate in gates)
    payload = {
        "version": EVIDENCE_VERSION,
        "strategyVersion": STRATEGY_VERSION,
        "modelVersion": ALPHA_MODEL_VERSION,
        "generatedAt": get_taipei_now().isoformat(timespec="seconds"),
        "datasetFingerprint": _sha256(dataset_path),
        "datasetStart": str(frame["trade_date"].min()),
        "datasetEnd": str(frame["trade_date"].max()),
        "executionVersion": str(frame["execution_version"].iloc[0]),
        "costsBps": float(frame["costs_bps"].iloc[0]),
        "classification": "historically_validated_manual_micro",
        "qualified": qualified,
        "manualMicroAllowed": qualified,
        "automaticBrokerTransmission": False,
        "pristineHoldout": False,
        "holdoutCaveat": (
            "2025 is chronologically out of sample for this fixed model fit, but it "
            "was visible to earlier project research and is not a never-observed holdout."
        ),
        "rules": {
            "ranking": "regularized predicted T+10 benchmark excess",
            "confidenceQuantile": CONFIDENCE_QUANTILE,
            "marketUpRatioMinimum": MIN_MARKET_UP_RATIO,
            "dailySelections": 1,
            "positionWeight": POSITION_WEIGHT,
            "holdingHorizon": HOLDING_HORIZON,
            "maxPositions": MAX_POSITIONS,
            "maxIndustryPositions": MAX_INDUSTRY_POSITIONS,
            "entry": "next_session_open",
            "maxEntryGapPct": MAX_ENTRY_GAP_PCT,
            "exit": "t10_close",
            "sameDayExit": False,
        },
        "gates": gates,
        "years": years,
        "holdout": holdout,
        "reasonCodes": [] if qualified else [
            gate["key"] for gate in gates if not gate["passed"]
        ],
    }
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


def build_live_decision(run, signals, market_up_ratio, evidence):
    rules = evidence.get("rules") or {}
    base = {
        "strategyVersion": STRATEGY_VERSION,
        "modelVersion": ALPHA_MODEL_VERSION,
        "signalDate": run.get("signal_date") if run else None,
        "evaluatedAt": get_taipei_now().isoformat(timespec="seconds"),
        "status": "cash",
        "action": "CASH",
        "reasonCodes": [],
        "marketUpRatio": market_up_ratio,
        "marketGateMinimum": float(
            rules.get("marketUpRatioMinimum", MIN_MARKET_UP_RATIO)
        ),
        "confidence": run.get("confidence") if run else None,
        "confidenceThreshold": run.get("confidence_threshold") if run else None,
        "targetWeight": float(rules.get("positionWeight", POSITION_WEIGHT)),
        "holdingHorizon": int(rules.get("holdingHorizon", HOLDING_HORIZON)),
        "maxPositions": int(rules.get("maxPositions", MAX_POSITIONS)),
        "maxIndustryPositions": int(
            rules.get("maxIndustryPositions", MAX_INDUSTRY_POSITIONS)
        ),
        "maxEntryGapPct": float(rules.get("maxEntryGapPct", MAX_ENTRY_GAP_PCT)),
        "selected": None,
    }
    if not evidence.get("qualified"):
        return {**base, "status": "blocked", "action": "BLOCKED", "reasonCodes": ["historical_evidence_failed"]}
    if not run:
        return {**base, "status": "refresh_required", "action": "REFRESH", "reasonCodes": ["alpha_live_run_missing"]}
    if run.get("model_version") != ALPHA_MODEL_VERSION:
        return {**base, "status": "refresh_required", "action": "REFRESH", "reasonCodes": ["model_version_mismatch"]}
    if run.get("status") != "active":
        return {**base, "reasonCodes": ["alpha_signal_not_active"]}
    confidence = run.get("confidence")
    threshold = run.get("confidence_threshold")
    if confidence is None or threshold is None or float(confidence) <= float(threshold):
        return {**base, "reasonCodes": ["confidence_below_threshold"]}
    if market_up_ratio is None or float(market_up_ratio) < base["marketGateMinimum"]:
        return {**base, "reasonCodes": ["market_breadth_below_gate"]}
    if not signals:
        return {**base, "reasonCodes": ["ranked_signal_missing"]}
    selected = sorted(
        signals,
        key=lambda row: (
            int(row.get("rank_order") or 999),
            -float(row.get("predicted_alpha") or -math.inf),
            str(row.get("code") or ""),
        ),
    )[0]
    signal_price = float(selected["signal_price"])
    return {
        **base,
        "status": "enter_next_open",
        "action": "BUY_NEXT_OPEN",
        "reasonCodes": ["all_fixed_gates_passed"],
        "selected": {
            "code": str(selected["code"]),
            "name": selected.get("name") or "",
            "industry": selected.get("industry") or "其他",
            "signalPrice": signal_price,
            "predictedAlpha": float(selected["predicted_alpha"]),
            "maxEntryPrice": signal_price * (1.0 + base["maxEntryGapPct"] / 100.0),
        },
    }


def run_deployable_strategy(db_path=DB_PATH, evidence_path=DEFAULT_EVIDENCE_PATH):
    evidence = load_evidence(evidence_path)
    with get_connection(db_path) as conn:
        init_db(conn)
        run_row = conn.execute(
            """
            SELECT * FROM alpha_live_runs
            ORDER BY signal_date DESC, generated_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        run = dict(run_row) if run_row else None
        signals = []
        market_up_ratio = None
        alpha_run_id = None
        if run:
            alpha_run_id = int(run["id"])
            signals = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT code, name, industry, rank_order, signal_price,
                           predicted_alpha
                    FROM alpha_live_signals
                    WHERE run_id=?
                    ORDER BY rank_order, id
                    """,
                    (alpha_run_id,),
                ).fetchall()
            ]
            market_row = conn.execute(
                "SELECT AVG(market_up_ratio) AS market_up_ratio "
                "FROM alpha_live_candidates WHERE run_id=?",
                (alpha_run_id,),
            ).fetchone()
            market_up_ratio = (
                float(market_row["market_up_ratio"])
                if market_row and market_row["market_up_ratio"] is not None
                else None
            )
        decision = build_live_decision(run, signals, market_up_ratio, evidence)
        conn.execute(
            """
            INSERT INTO deployable_strategy_snapshots (
                signal_date, evaluated_at, strategy_version, alpha_run_id,
                decision_status, action, code, name, industry, signal_price,
                predicted_alpha, market_up_ratio, confidence,
                confidence_threshold, target_weight, holding_horizon,
                reason_codes_json, decision_json, evidence_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(strategy_version, signal_date) DO UPDATE SET
                evaluated_at=excluded.evaluated_at,
                alpha_run_id=excluded.alpha_run_id,
                decision_status=excluded.decision_status,
                action=excluded.action,
                code=excluded.code,
                name=excluded.name,
                industry=excluded.industry,
                signal_price=excluded.signal_price,
                predicted_alpha=excluded.predicted_alpha,
                market_up_ratio=excluded.market_up_ratio,
                confidence=excluded.confidence,
                confidence_threshold=excluded.confidence_threshold,
                target_weight=excluded.target_weight,
                holding_horizon=excluded.holding_horizon,
                reason_codes_json=excluded.reason_codes_json,
                decision_json=excluded.decision_json,
                evidence_json=excluded.evidence_json
            """,
            (
                decision.get("signalDate") or "",
                decision["evaluatedAt"],
                STRATEGY_VERSION,
                alpha_run_id,
                decision["status"],
                decision["action"],
                (decision.get("selected") or {}).get("code"),
                (decision.get("selected") or {}).get("name"),
                (decision.get("selected") or {}).get("industry"),
                (decision.get("selected") or {}).get("signalPrice"),
                (decision.get("selected") or {}).get("predictedAlpha"),
                decision.get("marketUpRatio"),
                decision.get("confidence"),
                decision.get("confidenceThreshold"),
                decision["targetWeight"],
                decision["holdingHorizon"],
                json.dumps(decision["reasonCodes"], ensure_ascii=True),
                json.dumps(decision, ensure_ascii=True, sort_keys=True),
                json.dumps(evidence, ensure_ascii=True, sort_keys=True),
            ),
        )
    return {**decision, "evidence": evidence}


def main():
    parser = argparse.ArgumentParser(
        description="Audit and materialize the fixed Alpha execution strategy."
    )
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--evidence", default=str(DEFAULT_EVIDENCE_PATH))
    parser.add_argument("--audit-dataset")
    args = parser.parse_args()
    if args.audit_dataset:
        result = audit_historical_strategy(args.audit_dataset, args.evidence)
    else:
        result = run_deployable_strategy(args.db, args.evidence)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
