import argparse
import hashlib
import json
import math
from pathlib import Path

import pandas as pd

from ai_pipeline import (
    MODEL_FEATURES,
    PIT_FUNDAMENTAL_FEATURE_VERSION,
    build_feature_snapshots,
    latest_candidate_run_id,
)
from database import (
    CANDIDATE_EXECUTION_VERSION,
    CHALLENGER_FACTORY_VERSION,
    DB_PATH,
    get_connection,
    get_git_commit,
    get_taipei_now,
    init_db,
)
from model_governance import (
    ChallengerGates,
    ShadowSelectionPolicy,
    apply_shadow_selection,
    evaluate_challenger,
    score_prediction,
    walk_forward_splits,
)


DEFAULT_APPROVALS_PATH = Path("config/shadow_challenger_approvals.json")
PIT_EXPERIMENT_IMPLEMENTATION = "pit_fundamentals_ablation_v1"
PIT_MODEL_FEATURES = [
    *MODEL_FEATURES,
    "pe",
    "pb",
    "revenue_yoy",
    "revenue_mom",
    "eps_latest",
    "fundamental_age_days",
]


def _decode_object(value):
    try:
        result = json.loads(value or "{}")
        return result if isinstance(result, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _load_approvals(path=DEFAULT_APPROVALS_PATH):
    path = Path(path)
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    approvals = payload.get("approvals", [])
    return {
        str(row["hypothesisKey"]): row
        for row in approvals
        if isinstance(row, dict) and row.get("hypothesisKey")
    }


def _config_fingerprint(config):
    encoded = json.dumps(config, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _experiment_version(hypothesis_key, fingerprint):
    return f"{hypothesis_key}-{fingerprint[:10]}"


def sync_challenger_experiments(
    db_path=DB_PATH,
    approvals_path=DEFAULT_APPROVALS_PATH,
):
    approvals = _load_approvals(approvals_path)
    now = get_taipei_now().isoformat(timespec="seconds")
    synced = []
    with get_connection(db_path) as conn:
        init_db(conn)
        hypotheses = conn.execute(
            """
            SELECT hypothesis_key, target_layer, status, proposed_config_json
            FROM learning_hypotheses
            WHERE status NOT IN ('rejected', 'promoted', 'retired')
            ORDER BY priority DESC, hypothesis_key
            """
        ).fetchall()
        for hypothesis in hypotheses:
            key = hypothesis["hypothesis_key"]
            approval = approvals.get(key)
            proposed = _decode_object(hypothesis["proposed_config_json"])
            implementation = (approval or {}).get("implementation")
            executable = implementation == PIT_EXPERIMENT_IMPLEMENTATION
            config = {
                "hypothesisKey": key,
                "targetLayer": hypothesis["target_layer"],
                "proposedConfig": proposed,
                "implementation": implementation,
                "factoryVersion": CHALLENGER_FACTORY_VERSION,
            }
            if approval:
                config["approval"] = {
                    name: approval.get(name)
                    for name in (
                        "scope",
                        "approvedBy",
                        "approvedAt",
                        "minimumSamples",
                        "minimumTradeDates",
                        "minimumCoveragePct",
                        "notes",
                    )
                }
            fingerprint = _config_fingerprint(config)
            version = _experiment_version(key, fingerprint)
            approval_status = "approved" if approval else "pending"
            status = (
                "collecting_data"
                if approval and executable
                else "implementation_required"
                if approval
                else "draft"
            )
            conn.execute(
                """
                INSERT INTO challenger_experiments (
                    hypothesis_key, experiment_version, factory_version,
                    target_layer, status, approval_status, approved_scope,
                    approved_by, approved_at, config_json, config_fingerprint,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(hypothesis_key, experiment_version) DO UPDATE SET
                    status=CASE
                        WHEN challenger_experiments.status IN (
                            'evaluated', 'promotion_review', 'rejected'
                        ) THEN challenger_experiments.status
                        ELSE excluded.status
                    END,
                    approval_status=excluded.approval_status,
                    approved_scope=excluded.approved_scope,
                    approved_by=excluded.approved_by,
                    approved_at=excluded.approved_at,
                    config_json=excluded.config_json,
                    updated_at=excluded.updated_at
                """,
                (
                    key,
                    version,
                    CHALLENGER_FACTORY_VERSION,
                    hypothesis["target_layer"],
                    status,
                    approval_status,
                    (approval or {}).get("scope"),
                    (approval or {}).get("approvedBy"),
                    (approval or {}).get("approvedAt"),
                    json.dumps(config, ensure_ascii=False, sort_keys=True),
                    fingerprint,
                    now,
                    now,
                ),
            )
            if approval:
                conn.execute(
                    """
                    UPDATE learning_hypotheses
                    SET status='approved_for_shadow', updated_at=?
                    WHERE hypothesis_key=?
                      AND status NOT IN ('rejected', 'promoted', 'retired')
                    """,
                    (now, key),
                )
            synced.append(
                {
                    "hypothesisKey": key,
                    "experimentVersion": version,
                    "approvalStatus": approval_status,
                    "status": status,
                }
            )
    return synced


def _pit_readiness(conn):
    first_known = conn.execute(
        "SELECT MIN(known_at) FROM fundamental_observations"
    ).fetchone()[0]
    if not first_known:
        return {
            "eligibleCandidates": 0,
            "featureRows": 0,
            "fundamentalRows": 0,
            "coveragePct": 0.0,
            "matureSamples": 0,
            "tradeDates": 0,
            "dataStartDate": None,
            "dataEndDate": None,
        }
    coverage = conn.execute(
        """
        WITH canonical AS (
            SELECT ce.run_id, ce.code, ce.as_of, sr.trade_date,
                   ROW_NUMBER() OVER (
                       PARTITION BY sr.trade_date, ce.code
                       ORDER BY ce.as_of ASC, ce.id ASC
                   ) AS row_number
            FROM candidate_events ce
            JOIN scan_runs sr ON sr.id=ce.run_id
            WHERE ce.as_of >= ?
        )
        SELECT COUNT(*) AS eligible_candidates,
               SUM(CASE WHEN fs.id IS NOT NULL THEN 1 ELSE 0 END) AS feature_rows,
               SUM(CASE WHEN fs.fundamental_complete=1 THEN 1 ELSE 0 END) AS fundamental_rows,
               MIN(canonical.trade_date) AS data_start,
               MAX(canonical.trade_date) AS data_end
        FROM canonical
        LEFT JOIN feature_snapshots fs
          ON fs.run_id=canonical.run_id AND fs.code=canonical.code
         AND fs.feature_version=?
        WHERE canonical.row_number=1
        """,
        (first_known, PIT_FUNDAMENTAL_FEATURE_VERSION),
    ).fetchone()
    eligible = int(coverage["eligible_candidates"] or 0)
    fundamental = int(coverage["fundamental_rows"] or 0)
    matured = conn.execute(
        """
        WITH canonical AS (
            SELECT ce.id AS candidate_id, ce.run_id, ce.code, sr.trade_date,
                   ROW_NUMBER() OVER (
                       PARTITION BY sr.trade_date, ce.code
                       ORDER BY ce.as_of ASC, ce.id ASC
                   ) AS row_number
            FROM candidate_events ce
            JOIN scan_runs sr ON sr.id=ce.run_id
            WHERE ce.as_of >= ?
        )
        SELECT COUNT(*) AS samples, COUNT(DISTINCT canonical.trade_date) AS trade_dates
        FROM canonical
        JOIN feature_snapshots fs
          ON fs.run_id=canonical.run_id AND fs.code=canonical.code
         AND fs.feature_version=? AND fs.fundamental_complete=1
        JOIN candidate_outcomes co ON co.candidate_id=canonical.candidate_id
        WHERE canonical.row_number=1
          AND co.execution_version=?
          AND co.entry_status='filled'
          AND co.matured_horizon >= 3
          AND co.success_t3 IS NOT NULL
          AND co.net_return_3d IS NOT NULL
          AND co.excess_return_3d IS NOT NULL
          AND co.max_drawdown_3d IS NOT NULL
        """,
        (first_known, PIT_FUNDAMENTAL_FEATURE_VERSION, CANDIDATE_EXECUTION_VERSION),
    ).fetchone()
    return {
        "eligibleCandidates": eligible,
        "featureRows": int(coverage["feature_rows"] or 0),
        "fundamentalRows": fundamental,
        "coveragePct": round(100.0 * fundamental / eligible, 2) if eligible else 0.0,
        "matureSamples": int(matured["samples"] or 0),
        "tradeDates": int(matured["trade_dates"] or 0),
        "dataStartDate": coverage["data_start"],
        "dataEndDate": coverage["data_end"],
    }


def _load_pit_training_frame(conn):
    feature_columns = ", ".join(f"fs.{name}" for name in PIT_MODEL_FEATURES)
    return pd.read_sql_query(
        f"""
        WITH canonical AS (
            SELECT ce.id AS candidate_id, ce.run_id, ce.code, ce.industry,
                   ce.is_selected, sr.trade_date,
                   ROW_NUMBER() OVER (
                       PARTITION BY sr.trade_date, ce.code
                       ORDER BY ce.as_of ASC, ce.id ASC
                   ) AS row_number
            FROM candidate_events ce
            JOIN scan_runs sr ON sr.id=ce.run_id
        )
        SELECT fs.id AS feature_id, canonical.code, canonical.trade_date,
               canonical.industry, canonical.is_selected AS rule_selected,
               fs.tradable, fs.is_first_eligible_event, {feature_columns},
               co.success_t3, co.net_return_3d, co.excess_return_3d,
               co.max_drawdown_3d
        FROM canonical
        JOIN feature_snapshots fs
          ON fs.run_id=canonical.run_id AND fs.code=canonical.code
        JOIN candidate_outcomes co ON co.candidate_id=canonical.candidate_id
        WHERE canonical.row_number=1
          AND fs.feature_version=?
          AND fs.point_in_time_valid=1
          AND fs.fundamental_complete=1
          AND fs.known_at <= fs.decision_at
          AND co.execution_version=?
          AND co.entry_status='filled'
          AND co.matured_horizon >= 3
          AND co.success_t3 IS NOT NULL
          AND co.net_return_3d IS NOT NULL
          AND co.excess_return_3d IS NOT NULL
          AND co.max_drawdown_3d IS NOT NULL
        ORDER BY canonical.trade_date, fs.id
        """,
        conn,
        params=(PIT_FUNDAMENTAL_FEATURE_VERSION, CANDIDATE_EXECUTION_VERSION),
    )


def _new_models():
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression, Ridge
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    def pipeline(model):
        return Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
                ("scaler", StandardScaler()),
                ("model", model),
            ]
        )

    return (
        pipeline(
            LogisticRegression(
                class_weight="balanced", max_iter=2000, random_state=42, C=0.5
            )
        ),
        pipeline(Ridge(alpha=5.0)),
        pipeline(Ridge(alpha=5.0)),
    )


def _walk_forward_model(frame, features):
    parts = []
    dates = int(frame["trade_date"].nunique())
    minimum_train_dates = max(20, dates // 3)
    test_dates = max(5, int(math.ceil(dates / 20)))
    for fold in walk_forward_splits(
        frame,
        min_train_dates=minimum_train_dates,
        test_dates=test_dates,
        embargo_trade_dates=3,
    ):
        train = fold["train"]
        validation = fold["validation"].copy()
        if len(train) < 80 or train["success_t3"].nunique() < 2:
            continue
        classifier, excess_model, drawdown_model = _new_models()
        classifier.fit(train[features], train["success_t3"].astype(int))
        excess_model.fit(train[features], train["excess_return_3d"])
        drawdown_model.fit(train[features], train["max_drawdown_3d"])
        validation["probability_t3"] = classifier.predict_proba(validation[features])[:, 1]
        validation["expected_excess_return_3d"] = excess_model.predict(validation[features])
        validation["expected_max_drawdown_3d"] = drawdown_model.predict(validation[features])
        validation["final_score"] = [
            score_prediction(probability, excess, drawdown)
            for probability, excess, drawdown in zip(
                validation["probability_t3"],
                validation["expected_excess_return_3d"],
                validation["expected_max_drawdown_3d"],
            )
        ]
        validation["fold_index"] = fold["fold_index"]
        validation["trained_through"] = fold["trained_through"]
        parts.append(validation)
    if not parts:
        return pd.DataFrame(), {}, ["insufficient_walk_forward_folds"]
    oof = apply_shadow_selection(pd.concat(parts).sort_index(), ShadowSelectionPolicy())
    metrics, reasons = evaluate_challenger(oof, ChallengerGates())
    return oof, metrics, reasons


def _render_report(experiment, status, readiness, metrics, reasons):
    return "\n".join(
        [
            f"# Challenger {experiment['experiment_version']}",
            "",
            f"- Status: `{status}`",
            f"- Approval: `{experiment['approval_status']}` / `{experiment['approved_scope'] or 'none'}`",
            f"- PIT coverage: {readiness['coveragePct']:.2f}%",
            f"- Mature samples: {readiness['matureSamples']}",
            f"- Mature trade dates: {readiness['tradeDates']}",
            f"- Data window: {readiness['dataStartDate'] or '--'} to {readiness['dataEndDate'] or '--'}",
            f"- Rejection reasons: {', '.join(reasons) if reasons else 'none'}",
            "",
            "This experiment is shadow-only and cannot modify formal ranking or live-capital policy.",
            "",
            "```json",
            json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True),
            "```",
        ]
    )


def evaluate_pit_fundamental_experiment(conn, experiment, approval):
    readiness = _pit_readiness(conn)
    minimum_samples = int(approval.get("minimumSamples") or 300)
    minimum_dates = int(approval.get("minimumTradeDates") or 30)
    minimum_coverage = float(approval.get("minimumCoveragePct") or 60.0)
    reasons = []
    if readiness["matureSamples"] < minimum_samples:
        reasons.append("insufficient_mature_samples")
    if readiness["tradeDates"] < minimum_dates:
        reasons.append("insufficient_trade_dates")
    if readiness["coveragePct"] < minimum_coverage:
        reasons.append("insufficient_point_in_time_coverage")
    metrics = {
        "readiness": readiness,
        "gates": {
            "minimumSamples": minimum_samples,
            "minimumTradeDates": minimum_dates,
            "minimumCoveragePct": minimum_coverage,
        },
        "baselineFeatures": MODEL_FEATURES,
        "challengerFeatures": PIT_MODEL_FEATURES,
        "formalRankingEnabled": False,
        "liveCapitalEnabled": False,
    }
    if reasons:
        return "collecting_data", readiness, metrics, reasons

    frame = _load_pit_training_frame(conn)
    _, baseline_metrics, baseline_reasons = _walk_forward_model(frame, MODEL_FEATURES)
    _, challenger_metrics, challenger_reasons = _walk_forward_model(
        frame, PIT_MODEL_FEATURES
    )
    baseline_excess = baseline_metrics.get("challenger_mean_excess_return")
    challenger_excess = challenger_metrics.get("challenger_mean_excess_return")
    incremental_excess = (
        float(challenger_excess) - float(baseline_excess)
        if challenger_excess is not None and baseline_excess is not None
        else None
    )
    reasons = list(challenger_reasons)
    if incremental_excess is None or incremental_excess <= 0:
        reasons.append("fundamentals_do_not_add_oof_excess_return")
    metrics.update(
        {
            "baseline": baseline_metrics,
            "baselineRejectionReasons": baseline_reasons,
            "challenger": challenger_metrics,
            "challengerRejectionReasons": challenger_reasons,
            "incrementalExcessReturn": incremental_excess,
        }
    )
    return (
        "promotion_review" if not reasons else "evaluated",
        readiness,
        metrics,
        sorted(set(reasons)),
    )


def run_governed_challengers(
    db_path=DB_PATH,
    approvals_path=DEFAULT_APPROVALS_PATH,
    build_latest_features=True,
):
    synced = sync_challenger_experiments(db_path, approvals_path)
    approvals = _load_approvals(approvals_path)
    if build_latest_features:
        run_id = latest_candidate_run_id(db_path)
        if run_id is not None:
            build_feature_snapshots(
                db_path=db_path,
                run_id=run_id,
                missing_only=True,
                feature_version=PIT_FUNDAMENTAL_FEATURE_VERSION,
            )
    now = get_taipei_now().isoformat(timespec="seconds")
    results = []
    with get_connection(db_path) as conn:
        init_db(conn)
        experiments = conn.execute(
            """
            SELECT * FROM challenger_experiments
            WHERE approval_status='approved'
            ORDER BY updated_at DESC, id DESC
            """
        ).fetchall()
        latest_by_hypothesis = {}
        for row in experiments:
            latest_by_hypothesis.setdefault(row["hypothesis_key"], dict(row))
        for key, experiment in latest_by_hypothesis.items():
            approval = approvals.get(key, {})
            started_at = get_taipei_now().isoformat(timespec="seconds")
            if approval.get("implementation") == PIT_EXPERIMENT_IMPLEMENTATION:
                status, readiness, metrics, reasons = evaluate_pit_fundamental_experiment(
                    conn, experiment, approval
                )
            else:
                readiness = _pit_readiness(conn)
                status = "implementation_required"
                metrics = {"formalRankingEnabled": False, "liveCapitalEnabled": False}
                reasons = ["challenger_executor_not_implemented"]
            report = _render_report(experiment, status, readiness, metrics, reasons)
            finished_at = get_taipei_now().isoformat(timespec="seconds")
            conn.execute(
                """
                INSERT INTO challenger_experiment_runs (
                    experiment_id, started_at, finished_at, status,
                    sample_count, trade_dates, feature_coverage_pct,
                    metrics_json, rejection_reasons_json, report_markdown,
                    git_commit
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    experiment["id"],
                    started_at,
                    finished_at,
                    status,
                    readiness["matureSamples"],
                    readiness["tradeDates"],
                    readiness["coveragePct"],
                    json.dumps(metrics, ensure_ascii=False, sort_keys=True),
                    json.dumps(reasons, ensure_ascii=False),
                    report,
                    get_git_commit(),
                ),
            )
            conn.execute(
                """
                UPDATE challenger_experiments
                SET status=?, data_start_date=?, data_end_date=?,
                    sample_count=?, trade_dates=?, feature_coverage_pct=?,
                    metrics_json=?, rejection_reasons_json=?, updated_at=?
                WHERE id=?
                """,
                (
                    status,
                    readiness["dataStartDate"],
                    readiness["dataEndDate"],
                    readiness["matureSamples"],
                    readiness["tradeDates"],
                    readiness["coveragePct"],
                    json.dumps(metrics, ensure_ascii=False, sort_keys=True),
                    json.dumps(reasons, ensure_ascii=False),
                    finished_at,
                    experiment["id"],
                ),
            )
            results.append(
                {
                    "hypothesisKey": key,
                    "experimentVersion": experiment["experiment_version"],
                    "status": status,
                    "sampleCount": readiness["matureSamples"],
                    "tradeDates": readiness["tradeDates"],
                    "featureCoveragePct": readiness["coveragePct"],
                    "rejectionReasons": reasons,
                }
            )
    return {
        "status": "completed",
        "factoryVersion": CHALLENGER_FACTORY_VERSION,
        "synced": len(synced),
        "evaluated": len(results),
        "experiments": results,
        "generatedAt": now,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Materialize approved learning hypotheses as governed shadow challengers."
    )
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--approvals", default=str(DEFAULT_APPROVALS_PATH))
    parser.add_argument("--sync-only", action="store_true")
    parser.add_argument("--no-build-features", action="store_true")
    args = parser.parse_args()
    if args.sync_only:
        result = {
            "status": "completed",
            "experiments": sync_challenger_experiments(args.db, args.approvals),
        }
    else:
        result = run_governed_challengers(
            db_path=args.db,
            approvals_path=args.approvals,
            build_latest_features=not args.no_build_features,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
