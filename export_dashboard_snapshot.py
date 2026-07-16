import argparse
import datetime as dt
import json
import sqlite3
from pathlib import Path

import pandas as pd

from database import CANDIDATE_EXECUTION_VERSION, INSTITUTIONAL_FEATURE_VERSION


TAIPEI_TZ = dt.timezone(dt.timedelta(hours=8), name="Asia/Taipei")
SCHEMA_VERSION = "dashboard_v2"
CANDIDATE_DETAIL_DAYS = 12
DEFAULT_OUTPUTS = (
    Path("data/dashboard_snapshot.json"),
    Path("web/public/dashboard_snapshot.json"),
)

STRATEGY_LABELS = {
    "trend": "順勢突破",
    "reversal": "低檔爆量",
    "wave": "波段蓄勢",
}

STATUS_LABELS = {
    "selected": "正式入選",
    "blocked": "資料阻擋",
    "score_below_threshold": "分數不足",
    "duplicate_daily_eligible": "當日已評估",
    "daily_limit": "當日名額已滿",
    "industry_limit": "同產業限制",
}


def _table_exists(conn, table_name):
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
    )


def _table_count(conn, table_name):
    if not _table_exists(conn, table_name):
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])


def _read_records(conn, query, params=()):
    frame = pd.read_sql_query(query, conn, params=params)
    if frame.empty:
        return []
    frame = frame.astype(object).where(pd.notna(frame), None)
    return frame.to_dict(orient="records")


def _decode_list(value):
    if not value:
        return []
    try:
        result = json.loads(value)
        return result if isinstance(result, list) else []
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def _decode_object(value):
    if not value:
        return {}
    try:
        result = json.loads(value)
        return result if isinstance(result, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _automation_slot(notes):
    if not notes:
        return ""
    try:
        result = json.loads(notes)
        return str(result.get("automation_slot", "")) if isinstance(result, dict) else ""
    except (TypeError, ValueError, json.JSONDecodeError):
        return ""


def _backtest_scope(config_json):
    if not config_json:
        return "legacy"
    try:
        result = json.loads(config_json)
        return str(result.get("selection_scope", "legacy")) if isinstance(result, dict) else "legacy"
    except (TypeError, ValueError, json.JSONDecodeError):
        return "legacy"


def _empty_global_market():
    return {
        "modelVersion": "global_regime_shadow_v1",
        "snapshotAt": "",
        "score": 50,
        "regimeLabel": "資料建立中",
        "taiwanBiasScore": 50,
        "taiwanBiasLabel": "資料建立中",
        "components": [],
        "drivers": [],
        "instruments": [],
        "history": [],
        "quality": {
            "status": "unavailable",
            "coveragePct": 0,
            "activeFreshPct": 0,
            "available": 0,
            "total": 0,
            "missingKeys": [],
            "warnings": ["跨市場資料尚未完成第一次收集。"],
            "formalRankingEnabled": False,
        },
    }


def _empty_institutional_flow():
    return {
        "featureVersion": INSTITUTIONAL_FEATURE_VERSION,
        "researchGeneration": "generation_2_institutional",
        "latestTradeDate": "",
        "fetchedAt": "",
        "rawRows": 0,
        "symbols": 0,
        "candidateTargets": 0,
        "featureSnapshots": 0,
        "completeFeatures": 0,
        "coveragePct": 0,
        "completeCoveragePct": 0,
        "sources": [],
        "candidates": [],
        "quality": {
            "status": "unavailable",
            "formalRankingEnabled": False,
            "historicalUse": "development_only",
            "promotionGate": "prospective_generation_2_evidence",
            "warnings": ["法人籌碼資料尚未完成第一次官方收集。"],
        },
    }


def _institutional_flow_snapshot(conn):
    required = {
        "institutional_flow_daily",
        "institutional_flow_fetches",
        "institutional_feature_snapshots",
    }
    if any(not _table_exists(conn, table) for table in required):
        return _empty_institutional_flow()
    latest = conn.execute(
        "SELECT MAX(trade_date) AS trade_date, MAX(fetched_at) AS fetched_at "
        "FROM institutional_flow_daily"
    ).fetchone()
    latest_date = latest["trade_date"] if latest else None
    if not latest_date:
        return _empty_institutional_flow()
    latest_scan_date = conn.execute(
        "SELECT MAX(trade_date) FROM scan_runs"
    ).fetchone()[0]
    candidate_targets = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT DISTINCT ce.run_id, ce.code
            FROM candidate_events ce
            JOIN scan_runs sr ON sr.id=ce.run_id
            WHERE sr.trade_date=?
        )
        """,
        (latest_scan_date,),
    ).fetchone()[0]
    feature_counts = conn.execute(
        """
        SELECT COUNT(*) AS features,
               SUM(CASE WHEN ifs.coverage_status='complete' THEN 1 ELSE 0 END) AS complete
        FROM institutional_feature_snapshots ifs
        JOIN scan_runs sr ON sr.id=ifs.run_id
        WHERE sr.trade_date=? AND ifs.feature_version=?
        """,
        (latest_scan_date, INSTITUTIONAL_FEATURE_VERSION),
    ).fetchone()
    feature_count = int(feature_counts["features"] or 0)
    complete_count = int(feature_counts["complete"] or 0)
    coverage_pct = feature_count / candidate_targets * 100 if candidate_targets else 0
    complete_pct = complete_count / candidate_targets * 100 if candidate_targets else 0
    source_rows = _read_records(
        conn,
        """
        SELECT market, status, report_date AS reportDate, row_count AS rowCount,
               source_name AS sourceName, source_url AS sourceUrl,
               fetched_at AS fetchedAt, error_text AS errorText
        FROM institutional_flow_fetches
        WHERE trade_date=?
        ORDER BY market
        """,
        (latest_date,),
    )
    candidates = _read_records(
        conn,
        """
        SELECT ce.code, ce.name, ce.industry, ce.score,
               ifs.source_trade_date AS sourceTradeDate,
               ifs.observations_20d AS observations20d,
               ifs.coverage_status AS coverageStatus,
               ifs.foreign_net_z20 AS foreignNetZ20,
               ifs.trust_net_z20 AS trustNetZ20,
               ifs.total_net_z20 AS totalNetZ20,
               ifs.foreign_streak_days AS foreignStreakDays,
               ifs.trust_streak_days AS trustStreakDays,
               ifs.total_streak_days AS totalStreakDays,
               ifs.agreement_score_1d AS agreementScore1d
        FROM candidate_events ce
        LEFT JOIN institutional_feature_snapshots ifs
          ON ifs.run_id=ce.run_id AND ifs.code=ce.code
         AND ifs.feature_version=?
        WHERE ce.run_id=(SELECT MAX(id) FROM scan_runs)
        ORDER BY ce.score DESC, ce.raw_rank, ce.code
        LIMIT 30
        """,
        (INSTITUTIONAL_FEATURE_VERSION,),
    )
    raw = conn.execute(
        "SELECT COUNT(*) AS rows, COUNT(DISTINCT code) AS symbols "
        "FROM institutional_flow_daily"
    ).fetchone()
    available_sources = sum(row.get("status") == "available" for row in source_rows)
    status = (
        "ready"
        if available_sources == 2 and complete_pct >= 90
        else "building"
        if available_sources
        else "warning"
    )
    warnings = [
        "法人資料採隔日上午 08:30 可用的保守時間點，不會影響同日決策。",
        "Generation 2 必須累積新的前瞻樣本，歷史資料只供開發，不可直接升級正式排名。",
    ]
    if available_sources < 2:
        warnings.insert(0, "最新交易日的上市或上櫃官方報表尚未完整。")
    if complete_pct < 90:
        warnings.insert(0, "候選股尚未累積完整 20 日法人特徵。")
    return {
        "featureVersion": INSTITUTIONAL_FEATURE_VERSION,
        "researchGeneration": "generation_2_institutional",
        "latestTradeDate": latest_date,
        "fetchedAt": latest["fetched_at"] or "",
        "rawRows": int(raw["rows"] or 0),
        "symbols": int(raw["symbols"] or 0),
        "candidateTargets": int(candidate_targets or 0),
        "featureSnapshots": feature_count,
        "completeFeatures": complete_count,
        "coveragePct": coverage_pct,
        "completeCoveragePct": complete_pct,
        "sources": source_rows,
        "candidates": candidates,
        "quality": {
            "status": status,
            "formalRankingEnabled": False,
            "historicalUse": "development_only",
            "promotionGate": "prospective_generation_2_evidence",
            "warnings": warnings,
        },
    }


def _global_market_snapshot(conn):
    required = {"market_instruments", "market_observations", "market_regime_snapshots"}
    if any(not _table_exists(conn, table) for table in required):
        return _empty_global_market()
    latest = conn.execute(
        """
        SELECT * FROM market_regime_snapshots
        ORDER BY snapshot_at DESC, id DESC LIMIT 1
        """
    ).fetchone()
    if not latest:
        return _empty_global_market()

    instruments = _read_records(
        conn,
        """
        SELECT
            mi.instrument_key AS key, mi.symbol, mi.display_name AS name,
            mi.group_name AS `group`, mi.region, mi.asset_class AS assetClass,
            mi.currency, mo.market_at AS marketAt, mo.price,
            mo.previous_close AS previousClose, mo.pct_change AS pctChange,
            mo.return_5d AS return5d, mo.shock_z AS shockZ, mo.volume,
            mo.source_name AS sourceName, mo.source_tier AS sourceTier,
            mo.data_status AS dataStatus, mo.session_status AS sessionStatus,
            mo.latency_minutes AS latencyMinutes,
            mi.impact_direction AS impactDirection, mi.model_weight AS modelWeight
        FROM market_observations mo
        JOIN market_instruments mi ON mi.instrument_key=mo.instrument_key
        WHERE mo.snapshot_at=?
        ORDER BY
            CASE mi.group_name
                WHEN '台灣市場' THEN 0 WHEN '美股風險' THEN 1
                WHEN '亞洲科技' THEN 2 WHEN '匯率與利率' THEN 3 ELSE 4
            END,
            mi.model_weight DESC, mi.instrument_key
        """,
        (latest["snapshot_at"],),
    )
    drivers = _decode_list(latest["drivers_json"])
    driver_points = {row.get("key"): row.get("impactPoints", 0) for row in drivers}
    for row in instruments:
        row["impactPoints"] = driver_points.get(row["key"], 0)

    history = _read_records(
        conn,
        """
        SELECT snapshot_at AS snapshotAt, score,
               taiwan_bias_score AS taiwanBiasScore,
               coverage_pct AS coveragePct,
               active_fresh_pct AS activeFreshPct
        FROM market_regime_snapshots
        ORDER BY snapshot_at DESC, id DESC
        LIMIT 192
        """,
    )
    history.reverse()
    quality = _decode_object(latest["quality_json"])
    quality.setdefault("coveragePct", latest["coverage_pct"])
    quality.setdefault("activeFreshPct", latest["active_fresh_pct"])
    quality.setdefault("formalRankingEnabled", False)
    return {
        "modelVersion": "global_regime_shadow_v1",
        "snapshotAt": latest["snapshot_at"],
        "score": latest["score"],
        "regimeLabel": latest["regime_label"],
        "taiwanBiasScore": latest["taiwan_bias_score"],
        "taiwanBiasLabel": latest["taiwan_bias_label"],
        "components": _decode_list(latest["components_json"]),
        "drivers": drivers,
        "instruments": instruments,
        "history": history,
        "quality": quality,
    }


def _research_experiment_snapshot(conn):
    required = {"research_experiments", "experiment_evaluations"}
    if any(not _table_exists(conn, table) for table in required):
        return []
    rows = _read_records(
        conn,
        """
        WITH latest AS (
            SELECT ee.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY ee.experiment_id
                       ORDER BY ee.evaluated_at DESC, ee.id DESC
                   ) AS row_number
            FROM experiment_evaluations ee
        )
        SELECT
            re.experiment_key AS experimentKey,
            re.name,
            re.hypothesis,
            re.strategy_family AS strategyFamily,
            re.execution_version AS executionVersion,
            re.status,
            latest.evaluated_at AS evaluatedAt,
            latest.sample_start AS sampleStart,
            latest.sample_end AS sampleEnd,
            latest.trade_dates AS tradeDates,
            latest.trades,
            latest.folds,
            latest.mean_net_return AS meanNetReturn,
            latest.mean_excess_return AS meanExcessReturn,
            latest.positive_rate AS positiveRate,
            latest.annualized_sharpe AS annualizedSharpe,
            latest.probabilistic_sharpe AS probabilisticSharpe,
            latest.max_drawdown AS maxDrawdown,
            latest.profitable_fold_rate AS profitableFoldRate,
            latest.qualified,
            latest.rejection_reasons_json AS rejectionReasonsJson,
            latest.metrics_json AS metricsJson
        FROM research_experiments re
        LEFT JOIN latest
          ON latest.experiment_id=re.id AND latest.row_number=1
        ORDER BY COALESCE(latest.qualified, 0) DESC,
                 COALESCE(latest.mean_excess_return, -999999) DESC,
                 re.id
        """,
    )
    for row in rows:
        row["qualified"] = bool(row.get("qualified"))
        row["rejectionReasons"] = _decode_list(
            row.pop("rejectionReasonsJson", "")
        )
        metrics = _decode_object(row.pop("metricsJson", ""))
        row["decisionDates"] = metrics.get("decision_dates")
        row["participationRatePct"] = metrics.get("participation_rate_pct")
        row["meanDailyNetReturn"] = metrics.get("mean_daily_net_return")
        row["meanDailyExcessReturn"] = metrics.get("mean_daily_excess_return")
        row["rankingTarget"] = metrics.get("ranking_target")
        row["predictionQuantile"] = metrics.get("prediction_quantile")
        row["predictionThreshold"] = metrics.get("prediction_threshold")
        row["evaluationScope"] = metrics.get("evaluation_scope")
        row["holdoutEvaluated"] = metrics.get("holdout_evaluated")
        row["formalRankingEnabled"] = metrics.get("formal_ranking_enabled")
        row["institutionalNetLift"] = metrics.get("institutional_net_lift")
        row["institutionalExcessLift"] = metrics.get(
            "institutional_excess_lift"
        )
        row["modelVersion"] = metrics.get("model_version")
    return rows


def _model_challenger_snapshot(conn):
    if not _table_exists(conn, "model_challenger_evaluations"):
        return []
    rows = _read_records(
        conn,
        """
        SELECT
            model_version AS modelVersion,
            evaluated_at AS evaluatedAt,
            status,
            oof_trade_dates AS oofTradeDates,
            oof_candidates AS oofCandidates,
            challenger_trades AS challengerTrades,
            champion_trades AS championTrades,
            challenger_mean_net_return AS challengerMeanNetReturn,
            challenger_mean_excess_return AS challengerMeanExcessReturn,
            champion_mean_net_return AS championMeanNetReturn,
            champion_mean_excess_return AS championMeanExcessReturn,
            net_return_lift AS netReturnLift,
            excess_return_lift AS excessReturnLift,
            challenger_max_drawdown AS challengerMaxDrawdown,
            profitable_fold_rate AS profitableFoldRate,
            qualified,
            rejection_reasons_json AS rejectionReasonsJson
        FROM model_challenger_evaluations
        ORDER BY evaluated_at DESC, id DESC
        LIMIT 12
        """,
    )
    for row in rows:
        row["qualified"] = bool(row.get("qualified"))
        row["rejectionReasons"] = _decode_list(
            row.pop("rejectionReasonsJson", "")
        )
    return rows


def _empty_research_health():
    return {
        "status": "building",
        "checkedAt": "",
        "latestTradeDate": "",
        "prospectiveCohorts": 0,
        "pendingCohorts": 0,
        "matureT3Cohorts": 0,
        "expectedMatureT3": 0,
        "staleOutcomes": 0,
        "oldestPendingSessions": 0,
        "maturityCoveragePct": 0,
        "executionScenarioCandidates": 0,
        "executionScenarios": 0,
        "executionScenariosMatureT20": 0,
        "executionScenariosPending": 0,
        "replayRuns": 0,
        "completedReplayRuns": 0,
        "latestReplayAt": None,
        "latestReplayStatus": None,
        "latestReplayStart": None,
        "latestReplayEnd": None,
        "replayEvents": 0,
        "replaySelected": 0,
        "replayMatureT3": 0,
        "replayAvailableSymbols": 0,
        "replayTradingDays": 0,
        "replayUniverseSnapshots": 0,
        "replayUniverseQualityStatus": "unverified",
        "replayUniversePartialMemberships": 0,
        "replayUniverseMembershipIntervals": 0,
        "replayCheckpointTotal": 0,
        "replayCheckpointCompleted": 0,
        "replayAttributionRows": 0,
        "replayAttributionDimensions": 0,
        "replayAttributionAt": None,
        "warnings": ["研究健康監控尚未完成第一次執行。"],
        "replayDataWarnings": [],
        "replaySelectedMeanNetReturn3d": None,
        "replaySelectedMeanExcessReturn3d": None,
        "replayRejectedMeanNetReturn3d": None,
        "replayRejectedMeanExcessReturn3d": None,
        "replaySelectionNetLift3d": None,
        "replaySelectionExcessLift3d": None,
        "replaySelectedSuccessRateT3": None,
        "replayRejectedSuccessRateT3": None,
        "replayEvidenceStorageMode": "none",
        "replayRawEventsPersisted": 0,
    }


def _research_health_snapshot(conn):
    if not _table_exists(conn, "research_health_snapshots"):
        return _empty_research_health()
    row = conn.execute(
        """
        SELECT * FROM research_health_snapshots
        ORDER BY checked_at DESC, id DESC LIMIT 1
        """
    ).fetchone()
    if not row:
        return _empty_research_health()
    metrics = _decode_object(row["metrics_json"])
    return {
        "status": row["status"],
        "checkedAt": row["checked_at"],
        "latestTradeDate": row["latest_trade_date"] or "",
        "prospectiveCohorts": int(row["prospective_cohorts"] or 0),
        "pendingCohorts": int(row["pending_cohorts"] or 0),
        "matureT3Cohorts": int(row["mature_t3_cohorts"] or 0),
        "expectedMatureT3": int(row["expected_mature_t3"] or 0),
        "staleOutcomes": int(row["stale_outcomes"] or 0),
        "oldestPendingSessions": int(row["oldest_pending_sessions"] or 0),
        "maturityCoveragePct": metrics.get("maturity_coverage_pct", 0),
        "executionScenarioCandidates": int(
            metrics.get("execution_scenario_candidates", 0) or 0
        ),
        "executionScenarios": int(metrics.get("execution_scenarios", 0) or 0),
        "executionScenariosMatureT20": int(
            metrics.get("execution_scenarios_mature_t20", 0) or 0
        ),
        "executionScenariosPending": int(
            metrics.get("execution_scenarios_pending", 0) or 0
        ),
        "replayRuns": int(row["replay_runs"] or 0),
        "completedReplayRuns": int(row["completed_replay_runs"] or 0),
        "latestReplayAt": row["latest_replay_at"],
        "latestReplayStatus": metrics.get("latest_replay_status"),
        "latestReplayStart": metrics.get("latest_replay_start"),
        "latestReplayEnd": metrics.get("latest_replay_end"),
        "replayEvents": int(row["replay_events"] or 0),
        "replaySelected": int(row["replay_selected"] or 0),
        "replayMatureT3": int(row["replay_mature_t3"] or 0),
        "replayAvailableSymbols": int(metrics.get("replay_available_symbols", 0) or 0),
        "replayTradingDays": int(metrics.get("replay_trading_days", 0) or 0),
        "replayUniverseSnapshots": int(metrics.get("replay_universe_snapshots", 0) or 0),
        "replayUniverseQualityStatus": str(
            metrics.get("replay_universe_quality_status") or "unverified"
        ),
        "replayUniversePartialMemberships": int(
            metrics.get("replay_universe_partial_memberships", 0) or 0
        ),
        "replayUniverseMembershipIntervals": int(
            metrics.get("replay_universe_membership_intervals", 0) or 0
        ),
        "replayCheckpointTotal": int(metrics.get("replay_checkpoint_total", 0) or 0),
        "replayCheckpointCompleted": int(metrics.get("replay_checkpoint_completed", 0) or 0),
        "replayAttributionRows": int(metrics.get("replay_attribution_rows", 0) or 0),
        "replayAttributionDimensions": int(metrics.get("replay_attribution_dimensions", 0) or 0),
        "replayAttributionAt": metrics.get("replay_attribution_at"),
        "warnings": _decode_list(row["warnings_json"]),
        "replayDataWarnings": metrics.get("replay_data_warnings", []),
        "replaySelectedMeanNetReturn3d": metrics.get("replay_selected_mean_net_return_3d"),
        "replaySelectedMeanExcessReturn3d": metrics.get("replay_selected_mean_excess_return_3d"),
        "replayRejectedMeanNetReturn3d": metrics.get("replay_rejected_mean_net_return_3d"),
        "replayRejectedMeanExcessReturn3d": metrics.get("replay_rejected_mean_excess_return_3d"),
        "replaySelectionNetLift3d": metrics.get("replay_selection_net_lift_3d"),
        "replaySelectionExcessLift3d": metrics.get("replay_selection_excess_lift_3d"),
        "replaySelectedSuccessRateT3": metrics.get("replay_selected_success_rate_t3"),
        "replayRejectedSuccessRateT3": metrics.get("replay_rejected_success_rate_t3"),
        "replayEvidenceStorageMode": metrics.get(
            "replay_evidence_storage_mode", "none"
        ),
        "replayRawEventsPersisted": int(
            metrics.get("replay_raw_events_persisted", 0) or 0
        ),
    }


def _empty_replay_attribution():
    return {
        "replayRunId": None,
        "attributionVersion": "",
        "generatedAt": "",
        "dimensions": [],
        "rows": [],
    }


def _replay_attribution_snapshot(conn):
    if not _table_exists(conn, "historical_replay_attributions"):
        return _empty_replay_attribution()
    latest = conn.execute(
        """
        SELECT hra.replay_run_id, hra.attribution_version,
               MAX(hra.generated_at) AS generated_at
        FROM historical_replay_attributions hra
        JOIN historical_replay_runs hrr ON hrr.id=hra.replay_run_id
        WHERE hrr.status='completed'
        GROUP BY hra.replay_run_id, hra.attribution_version, hrr.finished_at
        ORDER BY COALESCE(hrr.finished_at, hrr.started_at) DESC,
                 hra.replay_run_id DESC
        LIMIT 1
        """
    ).fetchone()
    if not latest:
        return _empty_replay_attribution()
    rows = _read_records(
        conn,
        """
        SELECT
            dimension, bucket_key AS bucketKey, bucket_label AS bucketLabel,
            sort_order AS sortOrder, selection_scope AS selectionScope,
            sample_count AS sampleCount, selected_count AS selectedCount,
            mean_net_return_1d AS meanNetReturn1d,
            mean_net_return_3d AS meanNetReturn3d,
            mean_net_return_5d AS meanNetReturn5d,
            mean_excess_return_3d AS meanExcessReturn3d,
            positive_rate_3d AS positiveRate3d,
            success_rate_t3 AS successRateT3,
            mean_max_drawdown_3d AS meanMaxDrawdown3d,
            standard_error_3d AS standardError3d,
            ci95_low_3d AS ci95Low3d,
            ci95_high_3d AS ci95High3d,
            metrics_json AS metricsJson
        FROM historical_replay_attributions
        WHERE replay_run_id=? AND attribution_version=?
        ORDER BY dimension, selection_scope, sort_order, sample_count DESC,
                 bucket_label
        """,
        (latest["replay_run_id"], latest["attribution_version"]),
    )
    for row in rows:
        metrics = _decode_object(row.pop("metricsJson", ""))
        row["dimensionLabel"] = metrics.get("dimensionLabel", row["dimension"])
        row["selectionLabel"] = metrics.get(
            "selectionLabel", row["selectionScope"]
        )
        row["matureT1"] = int(metrics.get("matureT1", 0) or 0)
        row["matureT3"] = int(metrics.get("matureT3", 0) or 0)
        row["matureT5"] = int(metrics.get("matureT5", 0) or 0)
    dimensions = []
    for row in rows:
        if not any(item["key"] == row["dimension"] for item in dimensions):
            dimensions.append(
                {"key": row["dimension"], "label": row["dimensionLabel"]}
            )
    return {
        "replayRunId": int(latest["replay_run_id"]),
        "attributionVersion": latest["attribution_version"],
        "generatedAt": latest["generated_at"],
        "dimensions": dimensions,
        "rows": rows,
    }


def build_dashboard_snapshot(db_path="data/stock_scanner.db"):
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"Dashboard database not found: {db_path}")

    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        required = {"scan_runs", "stock_signals", "candidate_events", "backtest_results"}
        missing = sorted(table for table in required if not _table_exists(conn, table))
        if missing:
            raise RuntimeError(f"Dashboard database missing tables: {', '.join(missing)}")

        latest = conn.execute(
            f"""
            SELECT trade_date, run_at, mode
            FROM scan_runs
            ORDER BY run_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        overview = {
            "latestTradeDate": latest["trade_date"] if latest else "",
            "latestRunAt": latest["run_at"] if latest else "",
            "latestMode": latest["mode"] if latest else "",
            "scanRuns": _table_count(conn, "scan_runs"),
            "signals": _table_count(conn, "stock_signals"),
            "candidateEvents": _table_count(conn, "candidate_events"),
            "featureSnapshots": (
                int(
                    conn.execute(
                        """
                        SELECT COUNT(*) FROM (
                            SELECT run_id, code FROM feature_snapshots
                            GROUP BY run_id, code
                        )
                        """
                    ).fetchone()[0]
                )
                if _table_exists(conn, "feature_snapshots")
                else 0
            ),
            "predictions": _table_count(conn, "predictions"),
            "predictionOutcomes": _table_count(conn, "prediction_outcomes"),
            "modelVersions": _table_count(conn, "model_versions"),
            "newsEvidence": _table_count(conn, "news_evidence"),
            "backtestResults": _table_count(conn, "backtest_results"),
            "formalSelections": int(
                conn.execute(
                    "SELECT COUNT(*) FROM candidate_events WHERE is_selected=1"
                ).fetchone()[0]
            ),
        }
        has_candidate_outcomes = _table_exists(conn, "candidate_outcomes")
        overview["candidateOutcomes"] = (
            int(
                conn.execute(
                    "SELECT COUNT(*) FROM candidate_outcomes WHERE execution_version=?",
                    (CANDIDATE_EXECUTION_VERSION,),
                ).fetchone()[0]
            )
            if has_candidate_outcomes
            else 0
        )
        if has_candidate_outcomes:
            quality = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN co.matured_horizon >= 3 THEN 1 ELSE 0 END),
                    SUM(CASE WHEN co.matured_horizon >= 3 AND ce.is_selected=0 THEN 1 ELSE 0 END),
                    SUM(CASE WHEN co.matured_horizon >= 3 AND ce.is_selected=1 THEN 1 ELSE 0 END),
                    COUNT(DISTINCT CASE WHEN co.matured_horizon >= 3 THEN sr.trade_date END),
                    AVG(CASE WHEN co.matured_horizon >= 3 THEN co.net_return_3d END),
                    AVG(CASE WHEN co.matured_horizon >= 3 THEN co.excess_return_3d END),
                    AVG(CASE WHEN co.matured_horizon >= 3 THEN
                        CASE WHEN co.net_return_3d > 0 THEN 1.0 ELSE 0.0 END END) * 100,
                    AVG(CASE WHEN co.matured_horizon >= 3 THEN co.success_t3 END) * 100,
                    AVG(CASE WHEN co.matured_horizon >= 3 AND ce.is_selected=1
                        THEN co.net_return_3d END),
                    AVG(CASE WHEN co.matured_horizon >= 3 AND ce.is_selected=1
                        THEN co.excess_return_3d END),
                    AVG(CASE WHEN co.matured_horizon >= 3 AND ce.is_selected=0
                        THEN co.net_return_3d END),
                    AVG(CASE WHEN co.matured_horizon >= 3 AND ce.is_selected=0
                        THEN co.excess_return_3d END)
                FROM candidate_outcomes co
                JOIN candidate_events ce ON ce.id=co.candidate_id
                JOIN scan_runs sr ON sr.id=ce.run_id
                WHERE co.execution_version=?
                """,
                (CANDIDATE_EXECUTION_VERSION,),
            ).fetchone()
        else:
            quality = (0, 0, 0, 0, None, None, None, None, None, None, None, None)
        candidate_events = overview["candidateEvents"]
        selection_net_lift = (
            quality[8] - quality[10]
            if quality[8] is not None and quality[10] is not None
            else None
        )
        selection_excess_lift = (
            quality[9] - quality[11]
            if quality[9] is not None and quality[11] is not None
            else None
        )
        research_quality = {
            "executionVersion": CANDIDATE_EXECUTION_VERSION,
            "outcomeCoveragePct": (
                overview["candidateOutcomes"] / candidate_events * 100
                if candidate_events
                else 0.0
            ),
            "matureCandidateOutcomes": int(quality[0] or 0),
            "matureRejectedOutcomes": int(quality[1] or 0),
            "matureSelectedOutcomes": int(quality[2] or 0),
            "uniqueTradeDates": int(quality[3] or 0),
            "meanNetReturn3d": quality[4],
            "meanExcessReturn3d": quality[5],
            "positiveRate3d": quality[6],
            "successRateT3": quality[7],
            "formalMeanNetReturn3d": quality[8],
            "formalMeanExcessReturn3d": quality[9],
            "rejectedMeanNetReturn3d": quality[10],
            "rejectedMeanExcessReturn3d": quality[11],
            "selectionNetLift3d": selection_net_lift,
            "selectionExcessLift3d": selection_excess_lift,
        }
        overview["candidateMatureT3"] = research_quality["matureCandidateOutcomes"]
        overview["candidateRejectedMatureT3"] = research_quality["matureRejectedOutcomes"]
        overview["maturePredictionOutcomes"] = (
            int(
                conn.execute(
                    "SELECT COUNT(*) FROM prediction_outcomes WHERE matured_horizon >= 3"
                ).fetchone()[0]
            )
            if _table_exists(conn, "prediction_outcomes")
            else 0
        )
        overview["prospectivePredictions"] = (
            int(
                conn.execute(
                    "SELECT COUNT(*) FROM predictions WHERE is_prospective=1"
                ).fetchone()[0]
            )
            if _table_exists(conn, "predictions")
            else 0
        )
        mature = conn.execute(
            """
            SELECT
                SUM(CASE WHEN matured_horizon >= 3 THEN 1 ELSE 0 END),
                SUM(CASE WHEN outcome_status = 'complete' THEN 1 ELSE 0 END)
            FROM backtest_results
            """
        ).fetchone()
        overview["matureT3"] = int(mature[0] or 0)
        overview["completeT20"] = int(mature[1] or 0)
        formal_mature = conn.execute(
            """
            SELECT
                COUNT(br.id),
                SUM(CASE WHEN br.matured_horizon >= 3 THEN 1 ELSE 0 END),
                SUM(CASE WHEN br.outcome_status = 'complete' THEN 1 ELSE 0 END)
            FROM candidate_events ce
            LEFT JOIN backtest_results br ON br.signal_id=ce.signal_id
            WHERE ce.is_selected=1
            """
        ).fetchone()
        overview["formalBacktestResults"] = int(formal_mature[0] or 0)
        overview["formalMatureT3"] = int(formal_mature[1] or 0)
        overview["formalCompleteT20"] = int(formal_mature[2] or 0)

        has_predictions = _table_exists(conn, "predictions")
        prediction_columns = """
                p.model_version AS aiModelVersion,
                p.is_prospective AS aiProspective,
                p.rank_order AS aiRank,
                p.is_selected AS aiShadowSelected,
                p.final_score AS aiScore,
                p.probability_t3 AS aiProbabilityT3,
                p.expected_excess_return_3d AS aiExpectedExcess3d,
                p.expected_max_drawdown_3d AS aiExpectedDrawdown3d,
                p.action AS aiAction,
        """ if has_predictions else ""
        prediction_join = """
            LEFT JOIN predictions p ON p.id=(
                SELECT p2.id FROM predictions p2
                WHERE p2.run_id=ce.run_id AND p2.code=ce.code
                ORDER BY p2.id DESC LIMIT 1
            )
        """ if has_predictions else ""
        if not prediction_columns:
            prediction_columns = """
                NULL AS aiModelVersion, NULL AS aiProspective, NULL AS aiRank,
                NULL AS aiShadowSelected, NULL AS aiScore,
                NULL AS aiProbabilityT3, NULL AS aiExpectedExcess3d,
                NULL AS aiExpectedDrawdown3d, NULL AS aiAction,
            """
        has_news = _table_exists(conn, "news_evidence")
        news_columns = """
                ne.sentiment AS aiNewsSentiment,
                ne.confidence AS aiNewsConfidence,
                ne.extracted_json AS aiNewsJson,
                (SELECT COUNT(*) FROM news_evidence ne2
                 WHERE ne2.run_id=ce.run_id AND ne2.code=ce.code) AS aiNewsEvidenceCount,
        """ if has_news else ""
        news_join = """
            LEFT JOIN news_evidence ne ON ne.id=(
                SELECT ne3.id FROM news_evidence ne3
                WHERE ne3.run_id=ce.run_id AND ne3.code=ce.code
                ORDER BY ne3.id DESC LIMIT 1
            )
        """ if has_news else ""
        if not news_columns:
            news_columns = """
                NULL AS aiNewsSentiment, NULL AS aiNewsConfidence,
                NULL AS aiNewsJson, 0 AS aiNewsEvidenceCount,
            """

        candidates = _read_records(
            conn,
            f"""
            SELECT
                sr.trade_date AS tradeDate,
                sr.run_at AS runAt,
                ce.selection_rank AS selectionRank,
                ce.raw_rank AS rawRank,
                ce.code,
                ce.name,
                ce.industry,
                ce.strategies_json AS strategiesJson,
                ce.score,
                ce.signal_price AS signalPrice,
                ce.pct_change AS pctChange,
                ce.turnover_billion AS turnoverBillion,
                ce.volume_ratio_5 AS volumeRatio5,
                ce.intraday_position AS intradayPosition,
                ce.observation_price AS observationPrice,
                ce.chase_limit AS chaseLimit,
                ce.stop_distance_pct AS stopDistancePct,
                ce.tradable,
                ce.is_selected AS isSelected,
                ce.selection_status AS selectionStatus,
                ce.risk_flags_json AS riskFlagsJson,
                ce.block_reasons_json AS blockReasonsJson,
                {prediction_columns}
                {news_columns}
                ce.policy_version AS policyVersion
            FROM candidate_events ce
            JOIN scan_runs sr ON sr.id=ce.run_id
            {prediction_join}
            {news_join}
            WHERE sr.trade_date IN (
                SELECT DISTINCT sr2.trade_date
                FROM candidate_events ce2
                JOIN scan_runs sr2 ON sr2.id=ce2.run_id
                ORDER BY sr2.trade_date DESC
                LIMIT {CANDIDATE_DETAIL_DAYS}
            )
            ORDER BY sr.trade_date DESC, sr.run_at DESC, ce.raw_rank
            """,
        )
        for row in candidates:
            row["strategies"] = _decode_list(row.pop("strategiesJson", ""))
            row["riskFlags"] = _decode_list(row.pop("riskFlagsJson", ""))
            row["blockReasons"] = _decode_list(row.pop("blockReasonsJson", ""))
            row["statusLabel"] = STATUS_LABELS.get(
                row["selectionStatus"], row["selectionStatus"]
            )
            row["tradable"] = bool(row["tradable"])
            row["isSelected"] = bool(row["isSelected"])
            if row["aiShadowSelected"] is not None:
                row["aiShadowSelected"] = bool(row["aiShadowSelected"])
            if row["aiProspective"] is not None:
                row["aiProspective"] = bool(row["aiProspective"])
            news_analysis = _decode_object(row.pop("aiNewsJson", ""))
            row["aiNewsSummary"] = news_analysis.get("summary", "")

        daily_candidates = _read_records(
            conn,
            """
            SELECT
                sr.trade_date AS tradeDate,
                COUNT(*) AS candidates,
                SUM(ce.tradable) AS tradable,
                SUM(ce.is_selected) AS selected,
                COUNT(DISTINCT ce.run_id) AS analyzedRuns
            FROM candidate_events ce
            JOIN scan_runs sr ON sr.id=ce.run_id
            GROUP BY sr.trade_date
            ORDER BY sr.trade_date
            """,
        )
        status_counts = _read_records(
            conn,
            """
            SELECT selection_status AS status, COUNT(*) AS count
            FROM candidate_events
            GROUP BY selection_status
            ORDER BY count DESC
            """,
        )
        for row in status_counts:
            row["label"] = STATUS_LABELS.get(row["status"], row["status"])

        performance = _read_records(
            conn,
            """
            SELECT
                s.trade_date AS tradeDate,
                s.mode,
                s.strategy,
                s.code,
                s.name,
                br.matured_horizon AS maturedHorizon,
                br.outcome_status AS outcomeStatus,
                br.net_return_1d AS netReturn1d,
                br.net_return_3d AS netReturn3d,
                br.net_return_5d AS netReturn5d,
                br.excess_return_3d AS excessReturn3d,
                br.max_return_3d AS maxReturn3d,
                br.max_drawdown_3d AS maxDrawdown3d,
                br.success_t3 AS successT3,
                br.costs_bps AS costsBps,
                br.entry_method AS entryMethod,
                br.tested_at AS testedAt,
                EXISTS(
                    SELECT 1 FROM candidate_events ce
                    WHERE ce.signal_id=s.id AND ce.is_selected=1
                ) AS isFormalSelection,
                COALESCE((
                    SELECT MAX(ce.policy_version) FROM candidate_events ce
                    WHERE ce.signal_id=s.id AND ce.is_selected=1
                ), '') AS policyVersion
            FROM backtest_results br
            JOIN stock_signals s ON s.id=br.signal_id
            ORDER BY s.trade_date DESC, s.id DESC
            """,
        )
        for row in performance:
            row["strategyLabel"] = STRATEGY_LABELS.get(row["strategy"], row["strategy"])
            if row["successT3"] is not None:
                row["successT3"] = bool(row["successT3"])
            row["isFormalSelection"] = bool(row["isFormalSelection"])

        scan_runs = _read_records(
            conn,
            """
            SELECT id, run_at AS runAt, trade_date AS tradeDate, mode, source,
                   strategy_version AS strategyVersion, git_commit AS gitCommit,
                   report_path AS reportPath, notes
            FROM scan_runs
            ORDER BY run_at DESC, id DESC
            LIMIT 80
            """,
        )
        for row in scan_runs:
            row["automationSlot"] = _automation_slot(row.pop("notes", ""))

        backtest_runs = []
        if _table_exists(conn, "backtest_runs"):
            backtest_runs = _read_records(
                conn,
                """
                SELECT id, started_at AS startedAt, finished_at AS finishedAt,
                       status, signals_requested AS signalsRequested,
                       completed_count AS completedCount,
                       partial_count AS partialCount,
                       skipped_count AS skippedCount,
                       error_text AS errorText, config_json AS configJson
                FROM backtest_runs
                ORDER BY started_at DESC, id DESC
                LIMIT 30
                """,
            )
            for row in backtest_runs:
                row["selectionScope"] = _backtest_scope(row.pop("configJson", ""))

        ai_models = []
        if _table_exists(conn, "model_versions"):
            ai_models = _read_records(
                conn,
                """
                SELECT model_name AS modelName, version, status,
                       feature_version AS featureVersion,
                       training_start AS trainingStart,
                       training_end AS trainingEnd,
                       metrics_json AS metricsJson,
                       created_at AS createdAt
                FROM model_versions
                ORDER BY id DESC
                LIMIT 12
                """,
            )
            for row in ai_models:
                row["metrics"] = _decode_object(row.pop("metricsJson", ""))

        paper_accounts = []
        paper_equity = []
        paper_trades = []
        if _table_exists(conn, "paper_accounts"):
            paper_accounts = _read_records(
                conn,
                """
                SELECT
                    pa.account_key AS accountKey,
                    pa.name,
                    pa.strategy_kind AS strategyKind,
                    pa.evidence_mode AS evidenceMode,
                    pa.policy_version AS policyVersion,
                    pa.execution_version AS executionVersion,
                    pa.starting_cash AS startingCash,
                    pa.cash,
                    pa.equity,
                    pa.total_return_pct AS totalReturnPct,
                    pa.max_drawdown_pct AS maxDrawdownPct,
                    pa.closed_trades AS closedTrades,
                    pa.winning_trades AS winningTrades,
                    CASE WHEN pa.closed_trades > 0
                         THEN pa.winning_trades * 100.0 / pa.closed_trades END AS winRate,
                    pa.open_positions AS openPositions,
                    pa.pending_orders AS pendingOrders,
                    pa.skipped_orders AS skippedOrders,
                    pa.first_signal_at AS firstSignalAt,
                    pa.last_equity_at AS lastEquityAt,
                    pa.status,
                    pa.config_json AS configJson,
                    pa.updated_at AS updatedAt,
                    AVG(CASE WHEN pt.status='closed' THEN pt.net_return_pct END) AS avgReturnPct,
                    SUM(CASE WHEN pt.status='closed' AND pt.realized_pnl > 0
                             THEN pt.realized_pnl ELSE 0 END) AS grossProfit,
                    SUM(CASE WHEN pt.status='closed' AND pt.realized_pnl < 0
                             THEN ABS(pt.realized_pnl) ELSE 0 END) AS grossLoss
                FROM paper_accounts pa
                LEFT JOIN paper_trades pt ON pt.account_id=pa.id
                GROUP BY pa.id
                ORDER BY CASE pa.strategy_kind WHEN 'rule' THEN 0 ELSE 1 END, pa.id
                """,
            )
            for row in paper_accounts:
                row["config"] = _decode_object(row.pop("configJson", ""))
                gross_loss = row.get("grossLoss") or 0
                row["profitFactor"] = (
                    (row.get("grossProfit") or 0) / gross_loss if gross_loss else None
                )
            paper_equity = _read_records(
                conn,
                """
                SELECT pa.account_key AS accountKey, pes.as_of AS asOf,
                       pes.cash, pes.market_value AS marketValue, pes.equity,
                       pes.total_return_pct AS totalReturnPct,
                       pes.drawdown_pct AS drawdownPct,
                       pes.open_positions AS openPositions,
                       pes.closed_trades AS closedTrades
                FROM paper_equity_snapshots pes
                JOIN paper_accounts pa ON pa.id=pes.account_id
                ORDER BY pes.as_of, pa.id
                """,
            )
            ai_account = next(
                (
                    row
                    for row in paper_accounts
                    if row.get("evidenceMode") == "prospective_only"
                ),
                None,
            )
            comparison_start = (
                (ai_account.get("firstSignalAt") or "")[:10]
                if ai_account
                else ""
            )
            for account in paper_accounts:
                account_points = [
                    point
                    for point in paper_equity
                    if point.get("accountKey") == account.get("accountKey")
                ]
                baseline_points = [
                    point
                    for point in account_points
                    if comparison_start and point.get("asOf", "") <= comparison_start
                ]
                baseline = baseline_points[-1] if baseline_points else None
                latest = account_points[-1] if account_points else None
                baseline_equity = (baseline or {}).get("equity")
                account["comparisonStartAt"] = (
                    baseline.get("asOf") if baseline else None
                )
                account["comparisonReturnPct"] = (
                    (latest["equity"] / baseline_equity - 1) * 100
                    if latest and baseline_equity
                    else None
                )
            paper_trades = _read_records(
                conn,
                """
                SELECT pa.account_key AS accountKey,
                       pt.source_type AS sourceType, pt.source_id AS sourceId,
                       pt.signal_date AS signalDate, pt.signal_at AS signalAt,
                       pt.code, pt.name, pt.industry, pt.rank_order AS rankOrder,
                       pt.model_version AS modelVersion,
                       pt.entry_at AS entryAt, pt.entry_price AS entryPrice,
                       pt.quantity, pt.invested_amount AS investedAmount,
                       pt.chase_limit AS chaseLimit, pt.stop_price AS stopPrice,
                       pt.exit_at AS exitAt, pt.exit_price AS exitPrice,
                       pt.exit_reason AS exitReason,
                       pt.net_return_pct AS netReturnPct,
                       pt.realized_pnl AS realizedPnl,
                       pt.mark_at AS markAt, pt.mark_price AS markPrice,
                       pt.market_value AS marketValue,
                       pt.unrealized_pnl AS unrealizedPnl,
                       pt.max_return_pct AS maxReturnPct,
                       pt.max_drawdown_pct AS maxDrawdownPct,
                       pt.status, pt.skip_reason AS skipReason
                FROM paper_trades pt
                JOIN paper_accounts pa ON pa.id=pt.account_id
                ORDER BY pt.signal_at DESC, pt.rank_order, pt.id DESC
                LIMIT 300
                """,
            )

        overview["paperAccounts"] = len(paper_accounts)
        overview["paperClosedTrades"] = sum(
            int(row.get("closedTrades") or 0) for row in paper_accounts
        )
        overview["paperProspectiveClosedTrades"] = sum(
            int(row.get("closedTrades") or 0)
            for row in paper_accounts
            if row.get("evidenceMode") == "prospective_only"
        )
        global_market = _global_market_snapshot(conn)
        institutional_flow = _institutional_flow_snapshot(conn)
        research_experiments = _research_experiment_snapshot(conn)
        model_challengers = _model_challenger_snapshot(conn)
        research_health = _research_health_snapshot(conn)
        replay_attribution = _replay_attribution_snapshot(conn)

        return {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": dt.datetime.now(TAIPEI_TZ).isoformat(timespec="seconds"),
            "candidateDetailDays": CANDIDATE_DETAIL_DAYS,
            "overview": overview,
            "researchQuality": research_quality,
            "researchHealth": research_health,
            "replayAttribution": replay_attribution,
            "researchExperiments": research_experiments,
            "candidates": candidates,
            "dailyCandidates": daily_candidates,
            "statusCounts": status_counts,
            "performance": performance,
            "scanRuns": scan_runs,
            "backtestRuns": backtest_runs,
            "aiModels": ai_models,
            "modelChallengers": model_challengers,
            "paperAccounts": paper_accounts,
            "paperEquity": paper_equity,
            "paperTrades": paper_trades,
            "globalMarket": global_market,
            "institutionalFlow": institutional_flow,
        }
    finally:
        conn.close()


def write_dashboard_snapshot(snapshot, output_paths=DEFAULT_OUTPUTS):
    content = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")) + "\n"
    written = []
    for output_path in output_paths:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
        written.append(str(path))
    return written


def main():
    parser = argparse.ArgumentParser(description="Export a public read-only quant dashboard snapshot.")
    parser.add_argument("--db-path", default="data/stock_scanner.db")
    parser.add_argument("--output", action="append", dest="outputs")
    args = parser.parse_args()

    snapshot = build_dashboard_snapshot(args.db_path)
    outputs = tuple(Path(path) for path in args.outputs) if args.outputs else DEFAULT_OUTPUTS
    written = write_dashboard_snapshot(snapshot, outputs)
    print(
        f"Dashboard snapshot exported: candidates={len(snapshot['candidates'])} "
        f"performance={len(snapshot['performance'])} outputs={', '.join(written)}"
    )


if __name__ == "__main__":
    main()
