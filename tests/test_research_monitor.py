import tempfile
import unittest
from pathlib import Path

from database import get_connection, init_db
from research_monitor import (
    build_research_health,
    build_research_integrity_gate,
    run_research_health_monitor,
)


class ResearchMonitorTests(unittest.TestCase):
    def test_integrity_gate_blocks_negative_or_incomplete_evidence(self):
        gate = build_research_integrity_gate(
            {
                "expected_mature_t3": 100,
                "mature_t3_cohorts": 50,
                "stale_outcomes": 50,
            },
            {
                "latest_replay_status": "completed",
                "replay_universe_quality_status": "partial",
                "replay_selected_mean_net_return_3d": -1.0,
                "replay_selected_mean_excess_return_3d": -1.2,
                "replay_selection_net_lift_3d": -0.2,
                "replay_selection_excess_lift_3d": -0.3,
            },
            {
                "formal_mature_selected": 120,
                "formal_selected_trade_dates": 30,
                "formal_selected_mean_net_return_3d": -0.5,
                "formal_selected_mean_excess_return_3d": -0.7,
                "formal_selection_net_lift_3d": -0.1,
                "formal_selection_excess_lift_3d": -0.2,
            },
            approved=True,
        )
        self.assertEqual(gate["status"], "blocked")
        self.assertEqual(gate["recommendation_mode"], "research_only")
        self.assertFalse(gate["formal_recommendations_allowed"])
        self.assertLess(gate["passed_checks"], gate["total_checks"])

    def test_integrity_gate_requires_manual_approval_after_evidence_passes(self):
        prospective = {
            "expected_mature_t3": 100,
            "mature_t3_cohorts": 100,
            "stale_outcomes": 0,
        }
        replay = {
            "latest_replay_status": "completed",
            "replay_universe_quality_status": "verified",
            "replay_selected_mean_net_return_3d": 1.0,
            "replay_selected_mean_excess_return_3d": 0.8,
            "replay_selection_net_lift_3d": 0.3,
            "replay_selection_excess_lift_3d": 0.2,
        }
        formal = {
            "formal_mature_selected": 120,
            "formal_selected_trade_dates": 30,
            "formal_selected_mean_net_return_3d": 1.0,
            "formal_selected_mean_excess_return_3d": 0.8,
            "formal_selection_net_lift_3d": 0.3,
            "formal_selection_excess_lift_3d": 0.2,
        }
        review = build_research_integrity_gate(
            prospective, replay, formal, approved=False
        )
        self.assertTrue(review["evidence_ready"])
        self.assertEqual(review["status"], "review_required")
        self.assertFalse(review["formal_recommendations_allowed"])

        approved = build_research_integrity_gate(
            prospective, replay, formal, approved=True
        )
        self.assertEqual(approved["status"], "approved")
        self.assertTrue(approved["formal_recommendations_allowed"])

    def test_flags_only_predictions_past_three_later_trading_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "scanner.db"
            with get_connection(database) as conn:
                init_db(conn)
                run_ids = []
                for day in ("2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09"):
                    run_ids.append(
                        conn.execute(
                            """
                            INSERT INTO scan_runs (
                                run_at, trade_date, mode, source, strategy_version
                            ) VALUES (?, ?, 'eod', 'test', 'v1')
                            """,
                            (f"{day}T14:00:00+08:00", day),
                        ).lastrowid
                    )
                prediction_id = conn.execute(
                    """
                    INSERT INTO predictions (
                        run_id, code, predicted_at, model_version,
                        is_prospective, created_at
                    ) VALUES (?, '2330', '2026-07-06T14:01:00+08:00',
                              'model-a', 1, '2026-07-06T14:01:00+08:00')
                    """,
                    (run_ids[0],),
                ).lastrowid
                conn.execute(
                    """
                    INSERT INTO predictions (
                        run_id, code, predicted_at, model_version,
                        is_prospective, created_at
                    ) VALUES (?, '2330', '2026-07-06T14:02:00+08:00',
                              'model-b', 1, '2026-07-06T14:02:00+08:00')
                    """,
                    (run_ids[0],),
                )

            stale = build_research_health(database)
            self.assertEqual(stale["prospective_cohorts"], 1)
            self.assertEqual(stale["expected_mature_t3"], 1)
            self.assertEqual(stale["stale_outcomes"], 1)
            self.assertEqual(stale["status"], "critical")
            self.assertEqual(stale["integrity_gate"]["status"], "blocked")

            with get_connection(database) as conn:
                conn.execute(
                    """
                    INSERT INTO prediction_outcomes (
                        prediction_id, matured_horizon, outcome_status, updated_at
                    ) VALUES (?, 3, 'partial', '2026-07-09T18:00:00+08:00')
                    """,
                    (prediction_id,),
                )
            current = run_research_health_monitor(database)
            self.assertEqual(current["mature_t3_cohorts"], 1)
            self.assertEqual(current["stale_outcomes"], 0)
            self.assertEqual(current["status"], "building")
            self.assertGreater(current["snapshot_id"], 0)


if __name__ == "__main__":
    unittest.main()
