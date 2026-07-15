import tempfile
import unittest
from pathlib import Path

from database import get_connection, init_db
from export_dashboard_snapshot import build_dashboard_snapshot
from replay_attribution import generate_replay_attribution


class ReplayAttributionTests(unittest.TestCase):
    def test_generates_selected_and_factor_attribution_with_confidence_interval(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "scanner.db"
            with get_connection(database) as conn:
                init_db(conn)
                replay_id = conn.execute(
                    """
                    INSERT INTO historical_replay_runs (
                        replay_key, replay_version, strategy_version, policy_version,
                        execution_version, started_at, finished_at, status,
                        start_date, end_date, universe_source, universe_size,
                        config_json, data_warnings_json
                    ) VALUES (
                        'test-replay', 'replay-v2', 'strategy-v1', 'policy-v1',
                        'execution-v1', '2026-01-01', '2026-01-31', 'completed',
                        '2026-01-01', '2026-01-31', 'fixture', 4, '{}', '[]'
                    )
                    """
                ).lastrowid
                for index, (selected, score, net_return) in enumerate(
                    [(1, 82, 2.0), (1, 75, -1.0), (0, 65, 1.0), (0, 55, -2.0)],
                    start=1,
                ):
                    event_id = conn.execute(
                        """
                        INSERT INTO historical_replay_events (
                            replay_run_id, trade_date, decision_at, code, name,
                            industry, strategies_json, strategy_count, score,
                            volume_ratio_5, volume_ratio_20, turnover_billion,
                            stop_distance_pct, tradable, block_reasons_json,
                            risk_flags_json, is_selected, selection_status,
                            policy_version, snapshot_json, created_at
                        ) VALUES (
                            ?, ?, ?, ?, '測試股', '半導體', '["順勢突破"]', 1, ?,
                            1.5, 1.2, 8, 5, 1, '[]', '[]', ?, ?, 'policy-v1',
                            '{"市場上漲比例":55,"產業上漲比例":60}', '2026-01-31'
                        )
                        """,
                        (
                            replay_id,
                            f"2026-01-{index:02d}",
                            f"2026-01-{index:02d}T14:00:00+08:00",
                            f"23{index:02d}",
                            score,
                            selected,
                            "selected" if selected else "score_below_threshold",
                        ),
                    ).lastrowid
                    conn.execute(
                        """
                        INSERT INTO historical_replay_outcomes (
                            replay_event_id, execution_version, entry_status,
                            fixed_net_return_1d, fixed_net_return_3d,
                            fixed_net_return_5d, net_return_3d,
                            excess_return_3d, max_drawdown_3d, success_t3,
                            matured_horizon, outcome_status, config_json, evaluated_at
                        ) VALUES (?, 'execution-v1', 'filled', ?, ?, ?, ?, ?, -1, ?,
                                  5, 'complete', '{}', '2026-02-10')
                        """,
                        (
                            event_id,
                            net_return / 2,
                            net_return,
                            net_return * 1.2,
                            net_return,
                            net_return - 0.2,
                            int(net_return > 0),
                        ),
                    )

            result = generate_replay_attribution(database, replay_id)
            self.assertEqual(result["source_rows"], 4)
            self.assertIn("strategy", result["dimensions"])

            with get_connection(database) as conn:
                selected = conn.execute(
                    """
                    SELECT sample_count, mean_net_return_3d, ci95_low_3d,
                           ci95_high_3d
                    FROM historical_replay_attributions
                    WHERE replay_run_id=? AND dimension='selection'
                      AND bucket_key='selected' AND selection_scope='all'
                    """,
                    (replay_id,),
                ).fetchone()
                strategy_scopes = conn.execute(
                    """
                    SELECT COUNT(DISTINCT selection_scope)
                    FROM historical_replay_attributions
                    WHERE replay_run_id=? AND dimension='strategy'
                    """,
                    (replay_id,),
                ).fetchone()[0]

            self.assertEqual(selected["sample_count"], 2)
            self.assertEqual(selected["mean_net_return_3d"], 0.5)
            self.assertIsNotNone(selected["ci95_low_3d"])
            self.assertIsNotNone(selected["ci95_high_3d"])
            self.assertEqual(strategy_scopes, 3)

            snapshot = build_dashboard_snapshot(database)["replayAttribution"]
            self.assertEqual(snapshot["replayRunId"], replay_id)
            self.assertEqual(snapshot["attributionVersion"], "replay_attribution_v1")
            self.assertTrue(snapshot["rows"])


if __name__ == "__main__":
    unittest.main()
