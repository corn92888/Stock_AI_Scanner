import json
import tempfile
import unittest
from pathlib import Path

from database import CANDIDATE_EXECUTION_VERSION, get_connection, init_db
from export_dashboard_snapshot import _learning_cycle_snapshot
from learning_cycle import _separate_return_outliers, run_learning_cycle


class LearningCycleTests(unittest.TestCase):
    def _seed_evidence(self, db_path):
        created_at = "2026-03-02T14:30:00+08:00"
        with get_connection(db_path) as conn:
            init_db(conn)
            for index in range(35):
                trade_date = f"2026-03-{index % 28 + 1:02d}"
                run_id = conn.execute(
                    """
                    INSERT INTO scan_runs (
                        run_at, trade_date, mode, source, strategy_version
                    ) VALUES (?, ?, 'eod', 'test', 'test_v1')
                    """,
                    (f"{trade_date}T14:20:00+08:00", trade_date),
                ).lastrowid
                for selected, code, net_return, excess_return in (
                    (1, f"1{index:03d}", -1.5, -1.0),
                    (0, f"2{index:03d}", 0.8, 0.5),
                ):
                    candidate_id = conn.execute(
                        """
                        INSERT INTO candidate_events (
                            run_id, code, name, as_of, industry,
                            strategies_json, strategy_count, score,
                            signal_price, pct_change, turnover_billion,
                            volume_ratio_5, intraday_position,
                            stop_distance_pct, tradable, block_reasons_json,
                            risk_flags_json, is_first_eligible_event,
                            is_selected, selection_rank, selection_status,
                            policy_version, policy_config_json, snapshot_json,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, '測試產業', '["trend"]', 1, ?,
                                  100, 2, 12, 1.5, 0.8, 5, 1, '[]', '[]',
                                  1, ?, ?, ?, 'test_policy', '{}', ?, ?, ?)
                        """,
                        (
                            run_id,
                            code,
                            f"測試{code}",
                            f"{trade_date}T14:20:00+08:00",
                            80 if selected else 45,
                            selected,
                            1 if selected else None,
                            "selected" if selected else "score_below_threshold",
                            json.dumps(
                                {
                                    "市場平均漲跌幅": -0.5,
                                    "市場上漲比例": 35,
                                    "產業上漲比例": 30,
                                },
                                ensure_ascii=False,
                            ),
                            created_at,
                            created_at,
                        ),
                    ).lastrowid
                    conn.execute(
                        """
                        INSERT INTO candidate_outcomes (
                            candidate_id, execution_version, entry_method,
                            net_return_3d, excess_return_3d,
                            max_drawdown_3d, success_t3, matured_horizon,
                            outcome_status, entry_status, evaluated_at, updated_at
                        ) VALUES (?, ?, 'next_open', ?, ?, -2.0, 0, 3,
                                  'complete', 'filled', ?, ?)
                        """,
                        (
                            candidate_id,
                            CANDIDATE_EXECUTION_VERSION,
                            net_return,
                            excess_return,
                            created_at,
                            created_at,
                        ),
                    )
            conn.execute(
                """
                INSERT INTO alpha_forward_snapshots (
                    evaluated_at, validation_version, evidence_start_date,
                    state, allow_new_positions, metrics_json
                ) VALUES (?, 'test_forward_v1', '2026-02-01',
                          'COLLECTING', 1, ?)
                """,
                (
                    created_at,
                    json.dumps(
                        {
                            "decision_days": 20,
                            "closed_trades": 12,
                            "total_return_pct": -2.0,
                            "avg_excess_return_pct": -0.8,
                            "max_drawdown_pct": -7.0,
                            "probabilistic_sharpe": 0.4,
                        }
                    ),
                ),
            )
            conn.execute(
                """
                INSERT INTO paper_accounts (
                    account_key, name, strategy_kind, evidence_mode,
                    policy_version, execution_version, starting_cash, cash,
                    equity, total_return_pct, max_drawdown_pct, closed_trades,
                    winning_trades, open_positions, pending_orders,
                    skipped_orders, status, config_json, created_at, updated_at
                ) VALUES (?, 'Alpha 測試冠軍', 'alpha', 'prospective_only',
                          'test_policy', 'test_execution', 1000000, 980000,
                          980000, -2.0, -7.0, 12, 4, 0, 0, 0,
                          'shadow', '{}', ?, ?)
                """,
                (
                    "alpha_v2_champion_forward_t10_v1",
                    created_at,
                    created_at,
                ),
            )

    def test_cycle_persists_diagnosis_attribution_and_proposals_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "scanner.db"
            report_dir = Path(directory) / "reports"
            self._seed_evidence(db_path)

            first = run_learning_cycle(
                db_path=db_path,
                report_dir=report_dir,
                recent_trade_dates=60,
            )
            second = run_learning_cycle(
                db_path=db_path,
                report_dir=report_dir,
                recent_trade_dates=60,
            )

            self.assertEqual(first["status"], "redesign_required")
            self.assertEqual(first["primaryDiagnosis"], "early_drawdown_breach")
            self.assertEqual(second["cycleDate"], first["cycleDate"])
            self.assertTrue(Path(first["reportPath"]).exists())
            with get_connection(db_path) as conn:
                cycles = conn.execute("SELECT COUNT(*) FROM research_cycles").fetchone()[0]
                attributions = conn.execute(
                    "SELECT COUNT(*) FROM research_failure_attributions"
                ).fetchone()[0]
                hypotheses = conn.execute(
                    "SELECT hypothesis_key, occurrences, status FROM learning_hypotheses"
                ).fetchall()
                snapshot = _learning_cycle_snapshot(conn)

            self.assertEqual(cycles, 1)
            self.assertGreater(attributions, 0)
            self.assertTrue(hypotheses)
            self.assertTrue(all(row["occurrences"] == 1 for row in hypotheses))
            self.assertTrue(all(row["status"] == "proposed" for row in hypotheses))
            self.assertEqual(snapshot["status"], "redesign_required")
            self.assertEqual(snapshot["metrics"]["recentSelected"]["samples"], 35)
            self.assertLess(snapshot["metrics"]["selectionNetLift"], 0)

    def test_implausible_returns_are_excluded_from_learning_evidence(self):
        valid, excluded = _separate_return_outliers(
            [
                {"code": "2330", "net_return_3d": 3.0, "excess_return_3d": 1.0},
                {"code": "9999", "net_return_3d": 106.0, "excess_return_3d": 97.0},
            ]
        )

        self.assertEqual([row["code"] for row in valid], ["2330"])
        self.assertEqual([row["code"] for row in excluded], ["9999"])
        self.assertEqual(excluded[0]["reason"], "implausible_t3_return")


if __name__ == "__main__":
    unittest.main()
