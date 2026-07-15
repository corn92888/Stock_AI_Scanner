import argparse
import json

from database import DB_PATH, get_connection, get_taipei_now, init_db


def _prospective_metrics(conn):
    row = conn.execute(
        """
        WITH ranked AS (
            SELECT
                p.id AS prediction_id,
                sr.trade_date,
                po.matured_horizon,
                po.outcome_status,
                ROW_NUMBER() OVER (
                    PARTITION BY p.run_id, p.code
                    ORDER BY p.predicted_at, p.id
                ) AS cohort_order
            FROM predictions p
            JOIN scan_runs sr ON sr.id=p.run_id
            LEFT JOIN prediction_outcomes po ON po.prediction_id=p.id
            WHERE p.is_prospective=1
        ),
        cohorts AS (
            SELECT
                ranked.*,
                (
                    SELECT COUNT(DISTINCT later.trade_date)
                    FROM scan_runs later
                    WHERE later.trade_date > ranked.trade_date
                ) AS later_sessions
            FROM ranked
            WHERE cohort_order=1
        )
        SELECT
            COUNT(*) AS prospective_cohorts,
            SUM(CASE WHEN COALESCE(matured_horizon, 0) < 3 THEN 1 ELSE 0 END)
                AS pending_cohorts,
            SUM(CASE WHEN COALESCE(matured_horizon, 0) >= 3 THEN 1 ELSE 0 END)
                AS mature_t3_cohorts,
            SUM(CASE WHEN later_sessions >= 3 THEN 1 ELSE 0 END)
                AS expected_mature_t3,
            SUM(CASE WHEN later_sessions >= 3
                          AND COALESCE(matured_horizon, 0) < 3
                     THEN 1 ELSE 0 END) AS stale_outcomes,
            MAX(CASE WHEN COALESCE(matured_horizon, 0) < 3
                     THEN later_sessions ELSE 0 END) AS oldest_pending_sessions
        FROM cohorts
        """
    ).fetchone()
    return {
        "prospective_cohorts": int(row["prospective_cohorts"] or 0),
        "pending_cohorts": int(row["pending_cohorts"] or 0),
        "mature_t3_cohorts": int(row["mature_t3_cohorts"] or 0),
        "expected_mature_t3": int(row["expected_mature_t3"] or 0),
        "stale_outcomes": int(row["stale_outcomes"] or 0),
        "oldest_pending_sessions": int(row["oldest_pending_sessions"] or 0),
    }


def _replay_metrics(conn):
    counts = conn.execute(
        """
        SELECT COUNT(*) AS replay_runs,
               SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END)
                   AS completed_replay_runs
        FROM historical_replay_runs
        """
    ).fetchone()
    latest = conn.execute(
        """
        SELECT * FROM historical_replay_runs
        ORDER BY COALESCE(finished_at, started_at) DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    result = {
        "replay_runs": int(counts["replay_runs"] or 0),
        "completed_replay_runs": int(counts["completed_replay_runs"] or 0),
        "latest_replay_at": None,
        "latest_replay_status": None,
        "latest_replay_start": None,
        "latest_replay_end": None,
        "replay_events": 0,
        "replay_selected": 0,
        "replay_mature_t3": 0,
        "replay_available_symbols": 0,
        "replay_trading_days": 0,
        "replay_universe_snapshots": 0,
        "replay_universe_quality_status": "unverified",
        "replay_universe_partial_memberships": 0,
        "replay_universe_membership_intervals": 0,
        "replay_checkpoint_total": 0,
        "replay_checkpoint_completed": 0,
        "replay_attribution_rows": 0,
        "replay_attribution_dimensions": 0,
        "replay_attribution_at": None,
        "replay_data_warnings": [],
        "replay_selected_mean_net_return_3d": None,
        "replay_selected_mean_excess_return_3d": None,
        "replay_rejected_mean_net_return_3d": None,
        "replay_rejected_mean_excess_return_3d": None,
        "replay_selection_net_lift_3d": None,
        "replay_selection_excess_lift_3d": None,
        "replay_selected_success_rate_t3": None,
        "replay_rejected_success_rate_t3": None,
        "replay_evidence_storage_mode": "none",
        "replay_raw_events_persisted": 0,
    }
    if latest:
        attribution = conn.execute(
            """
            SELECT COUNT(*) AS rows,
                   COUNT(DISTINCT dimension) AS dimensions,
                   MAX(generated_at) AS generated_at
            FROM historical_replay_attributions
            WHERE replay_run_id=?
            """,
            (latest["id"],),
        ).fetchone()
        summary = conn.execute(
            "SELECT * FROM historical_replay_summaries WHERE replay_run_id=?",
            (latest["id"],),
        ).fetchone()
        raw_event_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM historical_replay_events WHERE replay_run_id=?",
                (latest["id"],),
            ).fetchone()[0]
        )
        if summary:
            performance = {
                "selected_net": summary["selected_mean_net_return_3d"],
                "selected_excess": summary["selected_mean_excess_return_3d"],
                "rejected_net": summary["rejected_mean_net_return_3d"],
                "rejected_excess": summary["rejected_mean_excess_return_3d"],
                "selected_success": summary["selected_success_rate_t3"],
                "rejected_success": summary["rejected_success_rate_t3"],
            }
        else:
            performance = conn.execute(
                """
                SELECT
                    AVG(CASE WHEN hre.is_selected=1 THEN hro.net_return_3d END)
                        AS selected_net,
                    AVG(CASE WHEN hre.is_selected=1 THEN hro.excess_return_3d END)
                        AS selected_excess,
                    AVG(CASE WHEN hre.is_selected=0 THEN hro.net_return_3d END)
                        AS rejected_net,
                    AVG(CASE WHEN hre.is_selected=0 THEN hro.excess_return_3d END)
                        AS rejected_excess,
                    AVG(CASE WHEN hre.is_selected=1 THEN hro.success_t3 END) * 100
                        AS selected_success,
                    AVG(CASE WHEN hre.is_selected=0 THEN hro.success_t3 END) * 100
                        AS rejected_success
                FROM historical_replay_outcomes hro
                JOIN historical_replay_events hre ON hre.id=hro.replay_event_id
                WHERE hre.replay_run_id=?
                  AND hro.entry_status='filled'
                  AND hro.matured_horizon >= 3
                """,
                (latest["id"],),
            ).fetchone()
        selected_net = performance["selected_net"]
        selected_excess = performance["selected_excess"]
        rejected_net = performance["rejected_net"]
        rejected_excess = performance["rejected_excess"]
        result.update(
            {
                "latest_replay_at": latest["finished_at"] or latest["started_at"],
                "latest_replay_status": latest["status"],
                "latest_replay_start": latest["start_date"],
                "latest_replay_end": latest["end_date"],
                "replay_events": int(latest["candidate_events"] or 0),
                "replay_selected": int(latest["selected_events"] or 0),
                "replay_mature_t3": int(latest["matured_t3"] or 0),
                "replay_available_symbols": int(latest["available_symbols"] or 0),
                "replay_trading_days": int(latest["trading_days"] or 0),
                "replay_universe_snapshots": int(latest["universe_snapshots"] or 0),
                "replay_universe_quality_status": str(
                    latest["universe_quality_status"] or "unverified"
                ),
                "replay_universe_partial_memberships": int(
                    latest["universe_partial_memberships"] or 0
                ),
                "replay_universe_membership_intervals": int(
                    latest["universe_membership_intervals"] or 0
                ),
                "replay_checkpoint_total": int(latest["checkpoint_total"] or 0),
                "replay_checkpoint_completed": int(latest["checkpoint_completed"] or 0),
                "replay_attribution_rows": int(attribution["rows"] or 0),
                "replay_attribution_dimensions": int(attribution["dimensions"] or 0),
                "replay_attribution_at": attribution["generated_at"],
                "replay_data_warnings": json.loads(
                    latest["data_warnings_json"] or "[]"
                ),
                "replay_selected_mean_net_return_3d": selected_net,
                "replay_selected_mean_excess_return_3d": selected_excess,
                "replay_rejected_mean_net_return_3d": rejected_net,
                "replay_rejected_mean_excess_return_3d": rejected_excess,
                "replay_selection_net_lift_3d": (
                    selected_net - rejected_net
                    if selected_net is not None and rejected_net is not None
                    else None
                ),
                "replay_selection_excess_lift_3d": (
                    selected_excess - rejected_excess
                    if selected_excess is not None and rejected_excess is not None
                    else None
                ),
                "replay_selected_success_rate_t3": performance["selected_success"],
                "replay_rejected_success_rate_t3": performance["rejected_success"],
                "replay_evidence_storage_mode": (
                    "summary_only" if summary and raw_event_count == 0 else "raw"
                ),
                "replay_raw_events_persisted": raw_event_count,
            }
        )
    return result


def build_research_health(db_path=DB_PATH):
    with get_connection(db_path) as conn:
        init_db(conn)
        prospective = _prospective_metrics(conn)
        replay = _replay_metrics(conn)
        latest_trade_date = conn.execute(
            "SELECT MAX(trade_date) FROM scan_runs"
        ).fetchone()[0]

    warnings = []
    if prospective["prospective_cohorts"] == 0:
        warnings.append("尚未累積真正前瞻預測 cohort。")
    elif prospective["mature_t3_cohorts"] == 0:
        warnings.append("前瞻 cohort 尚未產生第一批成熟 T+3 結果。")
    if prospective["stale_outcomes"]:
        warnings.append(
            f"{prospective['stale_outcomes']} 個前瞻 cohort 已超過 T+3 但尚未完成標註。"
        )
    if replay["replay_runs"] == 0:
        warnings.append("歷史重播尚未執行，模型仍主要依賴近期正式資料。")
    elif replay["latest_replay_status"] != "completed":
        warnings.append("最近一次歷史重播未完成，請檢查批次錯誤。")
    if replay["completed_replay_runs"] and replay["replay_mature_t3"] == 0:
        warnings.append("歷史重播已完成，但尚無可用的成熟 T+3 成交結果。")
    if (
        replay["replay_checkpoint_total"]
        and replay["replay_checkpoint_completed"] < replay["replay_checkpoint_total"]
    ):
        warnings.append(
            "歷史重播月份檢查點尚未全部完成，可使用 --resume 接續執行。"
        )
    if replay["completed_replay_runs"] and replay["replay_attribution_rows"] == 0:
        warnings.append("歷史重播尚未建立因子歸因矩陣。")
    if (
        replay["completed_replay_runs"]
        and replay["replay_universe_quality_status"] == "unverified"
    ):
        warnings.append("最近一次歷史重播尚未附上官方股票池來源與完整性資料。")
    elif (
        replay["completed_replay_runs"]
        and replay["replay_universe_quality_status"] == "partial"
    ):
        warnings.append(
            "最近一次歷史重播的股票池成員資格尚未完全驗證"
            f"（{replay['replay_universe_partial_memberships']} 個部分區間）。"
        )

    if prospective["stale_outcomes"]:
        status = "critical"
    elif (
        prospective["prospective_cohorts"] == 0
        or prospective["mature_t3_cohorts"] == 0
        or replay["completed_replay_runs"] == 0
    ):
        status = "building"
    elif (
        replay["latest_replay_status"] != "completed"
        or replay["replay_universe_quality_status"] != "verified"
    ):
        status = "warning"
    else:
        status = "healthy"

    expected = prospective["expected_mature_t3"]
    metrics = {
        **prospective,
        **replay,
        "latest_trade_date": latest_trade_date,
        "status": status,
        "maturity_coverage_pct": (
            prospective["mature_t3_cohorts"] / expected * 100 if expected else 0.0
        ),
        "warnings": warnings,
    }
    return metrics


def save_research_health(metrics, db_path=DB_PATH):
    checked_at = get_taipei_now().isoformat(timespec="seconds")
    with get_connection(db_path) as conn:
        init_db(conn)
        cursor = conn.execute(
            """
            INSERT INTO research_health_snapshots (
                checked_at, latest_trade_date, status, prospective_cohorts,
                pending_cohorts, mature_t3_cohorts, expected_mature_t3,
                stale_outcomes, oldest_pending_sessions, replay_runs,
                completed_replay_runs, latest_replay_at, replay_events,
                replay_selected, replay_mature_t3, warnings_json, metrics_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                checked_at,
                metrics.get("latest_trade_date"),
                metrics["status"],
                metrics["prospective_cohorts"],
                metrics["pending_cohorts"],
                metrics["mature_t3_cohorts"],
                metrics["expected_mature_t3"],
                metrics["stale_outcomes"],
                metrics["oldest_pending_sessions"],
                metrics["replay_runs"],
                metrics["completed_replay_runs"],
                metrics.get("latest_replay_at"),
                metrics["replay_events"],
                metrics["replay_selected"],
                metrics["replay_mature_t3"],
                json.dumps(metrics["warnings"], ensure_ascii=False),
                json.dumps(metrics, ensure_ascii=False, sort_keys=True),
            ),
        )
        return cursor.lastrowid, checked_at


def run_research_health_monitor(db_path=DB_PATH, save=True):
    metrics = build_research_health(db_path)
    if save:
        snapshot_id, checked_at = save_research_health(metrics, db_path)
        metrics = {**metrics, "snapshot_id": snapshot_id, "checked_at": checked_at}
    return metrics


def main():
    parser = argparse.ArgumentParser(
        description="Audit prospective outcome maturity and historical replay coverage."
    )
    parser.add_argument("--db-path", default=str(DB_PATH))
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument("--fail-on-critical", action="store_true")
    args = parser.parse_args()
    result = run_research_health_monitor(args.db_path, save=not args.no_save)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.fail_on_critical and result["status"] == "critical":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
