import json
import tempfile
import unittest
from pathlib import Path

from challenger_factory import run_governed_challengers, sync_challenger_experiments
from database import get_connection, init_db
from export_dashboard_snapshot import _learning_cycle_snapshot


class ChallengerFactoryTests(unittest.TestCase):
    def _seed(self, db_path):
        created_at = "2026-08-10T08:00:00+08:00"
        with get_connection(db_path) as conn:
            init_db(conn)
            cycle_id = conn.execute(
                """
                INSERT INTO research_cycles (
                    cycle_date, generated_at, cycle_version, status,
                    primary_diagnosis, metrics_json, report_markdown
                ) VALUES ('2026-08-10', ?, 'test_cycle', 'collecting',
                          'prospective_evidence_thin', '{}', '')
                """,
                (created_at,),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO learning_hypotheses (
                    hypothesis_key, title, rationale, target_layer, status,
                    priority, first_cycle_id, latest_cycle_id, occurrences,
                    proposed_config_json, evidence_json, created_at, updated_at
                ) VALUES (
                    'point_in_time_fundamentals_v1', 'PIT fundamentals',
                    'test', 'data', 'proposed', 100, ?, ?, 1,
                    '{"features":["pe","pb"]}', '{}', ?, ?
                )
                """,
                (cycle_id, cycle_id, created_at, created_at),
            )
            conn.execute(
                """
                INSERT INTO fundamental_observations (
                    code, period_end, published_at, known_at, source_name,
                    pe, pb, revenue_yoy, revenue_mom, raw_json, created_at,
                    eps_latest
                ) VALUES ('2330', '2026-08-09', '2026-08-09T18:00:00+08:00',
                          '2026-08-10T08:30:00+08:00', 'official_test',
                          25.0, 7.0, 15.0, 2.0, '{}', ?, 20.0)
                """,
                (created_at,),
            )
            run_id = conn.execute(
                """
                INSERT INTO scan_runs (
                    run_at, trade_date, mode, source, strategy_version
                ) VALUES ('2026-08-10T10:00:00+08:00', '2026-08-10',
                          'intraday', 'test', 'test_v1')
                """
            ).lastrowid
            conn.execute(
                """
                INSERT INTO candidate_events (
                    run_id, code, name, as_of, industry, strategies_json,
                    strategy_count, raw_rank, score, signal_price, tradable,
                    block_reasons_json, risk_flags_json,
                    is_first_eligible_event, is_selected, selection_status,
                    policy_version, policy_config_json, snapshot_json,
                    created_at, updated_at
                ) VALUES (?, '2330', '台積電', '2026-08-10T10:00:00+08:00',
                          '半導體', '["trend"]', 1, 1, 80, 1000, 1,
                          '[]', '[]', 1, 1, 'selected', 'test_policy', '{}',
                          '{}', ?, ?)
                """,
                (run_id, created_at, created_at),
            )

    def _approvals(self, directory):
        path = Path(directory) / "approvals.json"
        path.write_text(
            json.dumps(
                {
                    "approvals": [
                        {
                            "hypothesisKey": "point_in_time_fundamentals_v1",
                            "scope": "shadow_research_only",
                            "approvedBy": "test_owner",
                            "approvedAt": "2026-08-10",
                            "implementation": "pit_fundamentals_ablation_v1",
                            "minimumSamples": 300,
                            "minimumTradeDates": 30,
                            "minimumCoveragePct": 60,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_approved_hypothesis_becomes_versioned_shadow_experiment(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "scanner.db"
            self._seed(db_path)
            approvals = self._approvals(directory)

            first = sync_challenger_experiments(db_path, approvals)
            second = sync_challenger_experiments(db_path, approvals)

            self.assertEqual(first[0]["approvalStatus"], "approved")
            self.assertEqual(first[0]["experimentVersion"], second[0]["experimentVersion"])
            with get_connection(db_path) as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM challenger_experiments").fetchone()[0],
                    1,
                )
                status = conn.execute(
                    "SELECT status FROM learning_hypotheses"
                ).fetchone()[0]
            self.assertEqual(status, "approved_for_shadow")

    def test_challenger_collects_data_without_mutating_formal_strategy(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "scanner.db"
            self._seed(db_path)
            result = run_governed_challengers(
                db_path=db_path,
                approvals_path=self._approvals(directory),
            )

            experiment = result["experiments"][0]
            self.assertEqual(experiment["status"], "collecting_data")
            self.assertEqual(experiment["featureCoveragePct"], 100.0)
            self.assertIn("insufficient_mature_samples", experiment["rejectionReasons"])
            with get_connection(db_path) as conn:
                model_versions = conn.execute(
                    "SELECT COUNT(*) FROM model_versions"
                ).fetchone()[0]
                stored = conn.execute(
                    "SELECT metrics_json FROM challenger_experiments"
                ).fetchone()[0]
                snapshot = _learning_cycle_snapshot(conn)
            self.assertEqual(model_versions, 0)
            metrics = json.loads(stored)
            self.assertFalse(metrics["formalRankingEnabled"])
            self.assertFalse(metrics["liveCapitalEnabled"])
            self.assertEqual(snapshot["challengers"][0]["status"], "collecting_data")
            self.assertEqual(snapshot["challengers"][0]["approvalStatus"], "approved")
            self.assertEqual(snapshot["fundamentalData"]["codes"], 1)


if __name__ == "__main__":
    unittest.main()
