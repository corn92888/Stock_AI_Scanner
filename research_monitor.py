import argparse
import json
import os

from database import (
    CANDIDATE_EXECUTION_VERSION,
    DB_PATH,
    get_connection,
    get_taipei_now,
    init_db,
)


RESEARCH_INTEGRITY_GATE_VERSION = "research_integrity_gate_v1"
MIN_MATURITY_COVERAGE_PCT = 95.0
MIN_FORMAL_MATURE_SELECTED = 100
MIN_FORMAL_TRADE_DATES = 20


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
                      AND LOWER(later.mode)='eod'
                ) AS later_sessions
            FROM ranked
            WHERE cohort_order=1
        )
        SELECT
            COUNT(*) AS prospective_cohorts,
            SUM(CASE WHEN COALESCE(outcome_status, 'pending') <> 'skipped'
                          AND COALESCE(matured_horizon, 0) < 3
                     THEN 1 ELSE 0 END)
                AS pending_cohorts,
            SUM(CASE WHEN COALESCE(outcome_status, 'pending') <> 'skipped'
                          AND COALESCE(matured_horizon, 0) >= 3
                     THEN 1 ELSE 0 END)
                AS mature_t3_cohorts,
            SUM(CASE WHEN COALESCE(outcome_status, 'pending') = 'skipped'
                     THEN 1 ELSE 0 END) AS terminal_skipped_cohorts,
            SUM(CASE WHEN later_sessions >= 3
                          AND COALESCE(outcome_status, 'pending') <> 'skipped'
                     THEN 1 ELSE 0 END)
                AS expected_mature_t3,
            SUM(CASE WHEN later_sessions >= 3
                          AND COALESCE(outcome_status, 'pending') <> 'skipped'
                          AND COALESCE(matured_horizon, 0) < 3
                     THEN 1 ELSE 0 END) AS stale_outcomes,
            MAX(CASE WHEN COALESCE(outcome_status, 'pending') <> 'skipped'
                          AND COALESCE(matured_horizon, 0) < 3
                     THEN later_sessions ELSE 0 END) AS oldest_pending_sessions
        FROM cohorts
        """
    ).fetchone()
    return {
        "prospective_cohorts": int(row["prospective_cohorts"] or 0),
        "pending_cohorts": int(row["pending_cohorts"] or 0),
        "mature_t3_cohorts": int(row["mature_t3_cohorts"] or 0),
        "terminal_skipped_cohorts": int(row["terminal_skipped_cohorts"] or 0),
        "expected_mature_t3": int(row["expected_mature_t3"] or 0),
        "stale_outcomes": int(row["stale_outcomes"] or 0),
        "oldest_pending_sessions": int(row["oldest_pending_sessions"] or 0),
    }


def _execution_scenario_metrics(conn):
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT candidate_id) AS candidates,
               COUNT(*) AS scenarios,
               SUM(CASE WHEN matured_horizon >= 20 THEN 1 ELSE 0 END)
                   AS mature_t20,
               SUM(CASE WHEN outcome_status IN ('pending', 'partial')
                        THEN 1 ELSE 0 END) AS pending
        FROM candidate_execution_scenarios
        """
    ).fetchone()
    return {
        "execution_scenario_candidates": int(row["candidates"] or 0),
        "execution_scenarios": int(row["scenarios"] or 0),
        "execution_scenarios_mature_t20": int(row["mature_t20"] or 0),
        "execution_scenarios_pending": int(row["pending"] or 0),
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


def _formal_performance_metrics(conn):
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS mature_candidates,
            SUM(CASE WHEN ce.is_selected=1 THEN 1 ELSE 0 END)
                AS mature_selected,
            SUM(CASE WHEN ce.is_selected=0 THEN 1 ELSE 0 END)
                AS mature_rejected,
            COUNT(DISTINCT CASE WHEN ce.is_selected=1 THEN sr.trade_date END)
                AS selected_trade_dates,
            AVG(CASE WHEN ce.is_selected=1 THEN co.net_return_3d END)
                AS selected_net,
            AVG(CASE WHEN ce.is_selected=1 THEN co.excess_return_3d END)
                AS selected_excess,
            AVG(CASE WHEN ce.is_selected=0 THEN co.net_return_3d END)
                AS rejected_net,
            AVG(CASE WHEN ce.is_selected=0 THEN co.excess_return_3d END)
                AS rejected_excess
        FROM candidate_outcomes co
        JOIN candidate_events ce ON ce.id=co.candidate_id
        JOIN scan_runs sr ON sr.id=ce.run_id
        WHERE co.execution_version=?
          AND co.entry_status='filled'
          AND co.matured_horizon >= 3
        """,
        (CANDIDATE_EXECUTION_VERSION,),
    ).fetchone()
    selected_net = row["selected_net"]
    selected_excess = row["selected_excess"]
    rejected_net = row["rejected_net"]
    rejected_excess = row["rejected_excess"]
    return {
        "formal_mature_candidates": int(row["mature_candidates"] or 0),
        "formal_mature_selected": int(row["mature_selected"] or 0),
        "formal_mature_rejected": int(row["mature_rejected"] or 0),
        "formal_selected_trade_dates": int(row["selected_trade_dates"] or 0),
        "formal_selected_mean_net_return_3d": selected_net,
        "formal_selected_mean_excess_return_3d": selected_excess,
        "formal_rejected_mean_net_return_3d": rejected_net,
        "formal_rejected_mean_excess_return_3d": rejected_excess,
        "formal_selection_net_lift_3d": (
            selected_net - rejected_net
            if selected_net is not None and rejected_net is not None
            else None
        ),
        "formal_selection_excess_lift_3d": (
            selected_excess - rejected_excess
            if selected_excess is not None and rejected_excess is not None
            else None
        ),
    }


def _strategy_challenger_metrics(conn):
    row = conn.execute(
        """
        SELECT evaluated_at, challenger_version, status,
               selected_experiment_key, recommendation_mode,
               qualified_candidates, candidate_count
        FROM strategy_challenger_snapshots
        ORDER BY
            CASE
                WHEN challenger_version='alpha_liquid_universe_walk_forward_v2'
                THEN 0 ELSE 1
            END,
            evaluated_at DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        return {
            "strategy_challenger_evaluated_at": None,
            "strategy_challenger_version": None,
            "strategy_challenger_status": "not_evaluated",
            "strategy_challenger_selected_key": None,
            "strategy_recommendation_mode": "cash",
            "strategy_qualified_candidates": 0,
            "strategy_candidate_count": 0,
        }
    return {
        "strategy_challenger_evaluated_at": row["evaluated_at"],
        "strategy_challenger_version": row["challenger_version"],
        "strategy_challenger_status": row["status"],
        "strategy_challenger_selected_key": row["selected_experiment_key"],
        "strategy_recommendation_mode": row["recommendation_mode"],
        "strategy_qualified_candidates": int(row["qualified_candidates"] or 0),
        "strategy_candidate_count": int(row["candidate_count"] or 0),
    }


def _positive(value):
    return value is not None and float(value) > 0


def build_research_integrity_gate(
    prospective, replay, formal, approved=None, strategy=None
):
    strategy = strategy or {}
    expected = int(prospective.get("expected_mature_t3") or 0)
    maturity_coverage = (
        int(prospective.get("mature_t3_cohorts") or 0) / expected * 100
        if expected
        else 0.0
    )
    checks = [
        {
            "key": "outcomes_current",
            "label": "前瞻 T+3 標註無逾期",
            "passed": int(prospective.get("stale_outcomes") or 0) == 0,
            "detail": f"逾期 {int(prospective.get('stale_outcomes') or 0)} 筆",
            "requirement": "stale outcomes = 0",
        },
        {
            "key": "maturity_coverage",
            "label": "前瞻成熟覆蓋完整",
            "passed": expected > 0 and maturity_coverage >= MIN_MATURITY_COVERAGE_PCT,
            "detail": f"{maturity_coverage:.1f}% ({int(prospective.get('mature_t3_cohorts') or 0)}/{expected})",
            "requirement": f">= {MIN_MATURITY_COVERAGE_PCT:.0f}%",
        },
        {
            "key": "formal_sample_size",
            "label": "正式規則成熟樣本足夠",
            "passed": int(formal.get("formal_mature_selected") or 0)
            >= MIN_FORMAL_MATURE_SELECTED,
            "detail": f"{int(formal.get('formal_mature_selected') or 0)} 筆",
            "requirement": f">= {MIN_FORMAL_MATURE_SELECTED}",
        },
        {
            "key": "formal_trade_dates",
            "label": "正式規則跨足夠交易日",
            "passed": int(formal.get("formal_selected_trade_dates") or 0)
            >= MIN_FORMAL_TRADE_DATES,
            "detail": f"{int(formal.get('formal_selected_trade_dates') or 0)} 日",
            "requirement": f">= {MIN_FORMAL_TRADE_DATES}",
        },
        {
            "key": "formal_net_return",
            "label": "正式規則成本後淨報酬為正",
            "passed": _positive(formal.get("formal_selected_mean_net_return_3d")),
            "detail": f"{float(formal.get('formal_selected_mean_net_return_3d') or 0):.3f}%",
            "requirement": "> 0%",
        },
        {
            "key": "formal_excess_return",
            "label": "正式規則相對大盤超額為正",
            "passed": _positive(formal.get("formal_selected_mean_excess_return_3d")),
            "detail": f"{float(formal.get('formal_selected_mean_excess_return_3d') or 0):.3f}%",
            "requirement": "> 0%",
        },
        {
            "key": "formal_selection_lift",
            "label": "正式規則勝過落選對照組",
            "passed": _positive(formal.get("formal_selection_net_lift_3d"))
            and _positive(formal.get("formal_selection_excess_lift_3d")),
            "detail": (
                f"淨增值 {float(formal.get('formal_selection_net_lift_3d') or 0):.3f}% / "
                f"超額增值 {float(formal.get('formal_selection_excess_lift_3d') or 0):.3f}%"
            ),
            "requirement": "net and excess lift > 0%",
        },
        {
            "key": "replay_complete",
            "label": "歷史重播完成且股票池已驗證",
            "passed": replay.get("latest_replay_status") == "completed"
            and replay.get("replay_universe_quality_status") == "verified",
            "detail": (
                f"{replay.get('latest_replay_status') or 'not_run'} / "
                f"{replay.get('replay_universe_quality_status') or 'unverified'}"
            ),
            "requirement": "completed / verified",
        },
        {
            "key": "replay_net_return",
            "label": "歷史重播入選淨報酬為正",
            "passed": _positive(replay.get("replay_selected_mean_net_return_3d")),
            "detail": f"{float(replay.get('replay_selected_mean_net_return_3d') or 0):.3f}%",
            "requirement": "> 0%",
        },
        {
            "key": "replay_excess_return",
            "label": "歷史重播入選超額為正",
            "passed": _positive(replay.get("replay_selected_mean_excess_return_3d")),
            "detail": f"{float(replay.get('replay_selected_mean_excess_return_3d') or 0):.3f}%",
            "requirement": "> 0%",
        },
        {
            "key": "replay_selection_lift",
            "label": "歷史重播入選勝過落選組",
            "passed": _positive(replay.get("replay_selection_net_lift_3d"))
            and _positive(replay.get("replay_selection_excess_lift_3d")),
            "detail": (
                f"淨增值 {float(replay.get('replay_selection_net_lift_3d') or 0):.3f}% / "
                f"超額增值 {float(replay.get('replay_selection_excess_lift_3d') or 0):.3f}%"
            ),
            "requirement": "net and excess lift > 0%",
        },
        {
            "key": "walk_forward_challenger",
            "label": "擴展視窗挑戰策略已通過",
            "passed": strategy.get("strategy_challenger_status")
            == "prospective_shadow_ready"
            and int(strategy.get("strategy_qualified_candidates") or 0) > 0,
            "detail": (
                f"{int(strategy.get('strategy_qualified_candidates') or 0)} / "
                f"{int(strategy.get('strategy_candidate_count') or 0)} 個候選"
            ),
            "requirement": "at least one purged walk-forward candidate",
        },
    ]
    passed_checks = sum(1 for check in checks if check["passed"])
    evidence_ready = passed_checks == len(checks)
    if approved is None:
        approved = os.getenv("FORMAL_RECOMMENDATIONS_APPROVED", "").lower() in {
            "1",
            "true",
            "yes",
        }
    formal_allowed = evidence_ready and bool(approved)
    status = "approved" if formal_allowed else "review_required" if evidence_ready else "blocked"
    return {
        "version": RESEARCH_INTEGRITY_GATE_VERSION,
        "status": status,
        "recommendation_mode": "formal" if formal_allowed else "research_only",
        "evidence_ready": evidence_ready,
        "manual_approval": bool(approved),
        "formal_recommendations_allowed": formal_allowed,
        "passed_checks": passed_checks,
        "total_checks": len(checks),
        "checks": checks,
    }


def build_research_health(db_path=DB_PATH):
    with get_connection(db_path) as conn:
        init_db(conn)
        prospective = _prospective_metrics(conn)
        execution_scenarios = _execution_scenario_metrics(conn)
        replay = _replay_metrics(conn)
        formal = _formal_performance_metrics(conn)
        strategy = _strategy_challenger_metrics(conn)
        latest_trade_date = conn.execute(
            "SELECT MAX(trade_date) FROM scan_runs"
        ).fetchone()[0]

    integrity_gate = build_research_integrity_gate(
        prospective, replay, formal, strategy=strategy
    )

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
    if not integrity_gate["evidence_ready"]:
        warnings.append(
            "研究完整性閘門未通過；所有入選結果僅能顯示為研究候選，"
            "不得視為正式買進建議。"
        )
    elif not integrity_gate["manual_approval"]:
        warnings.append(
            "量化證據已通過研究完整性閘門，但尚未取得人工核准，"
            "正式推薦仍維持關閉。"
        )
    if strategy["strategy_challenger_status"] == "not_evaluated":
        warnings.append("擴展視窗策略挑戰者尚未執行。")
    elif strategy["strategy_challenger_status"] != "prospective_shadow_ready":
        warnings.append(
            "目前沒有策略同時通過成本後淨報酬、超額報酬、回撤與穩定度門檻；"
            "資金模式維持 CASH。"
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
        **execution_scenarios,
        **replay,
        **formal,
        **strategy,
        "latest_trade_date": latest_trade_date,
        "status": status,
        "maturity_coverage_pct": (
            prospective["mature_t3_cohorts"] / expected * 100 if expected else 0.0
        ),
        "integrity_gate": integrity_gate,
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
