import argparse
import datetime as dt
import json
import sqlite3
from pathlib import Path

import pandas as pd


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
            "featureSnapshots": _table_count(conn, "feature_snapshots"),
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

        return {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": dt.datetime.now(TAIPEI_TZ).isoformat(timespec="seconds"),
            "candidateDetailDays": CANDIDATE_DETAIL_DAYS,
            "overview": overview,
            "candidates": candidates,
            "dailyCandidates": daily_candidates,
            "statusCounts": status_counts,
            "performance": performance,
            "scanRuns": scan_runs,
            "backtestRuns": backtest_runs,
            "aiModels": ai_models,
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
