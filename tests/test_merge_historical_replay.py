import tempfile
import unittest
from pathlib import Path

from database import get_connection, init_db
from merge_historical_replay import merge_historical_replay


def _seed_replay(database):
    with get_connection(database) as conn:
        init_db(conn)
        replay_run_id = conn.execute(
            """
            INSERT INTO historical_replay_runs (
                replay_key, replay_version, strategy_version, policy_version,
                execution_version, started_at, finished_at, status,
                start_date, end_date, universe_source, universe_size,
                universe_quality_status, universe_membership_intervals,
                config_json, data_warnings_json
            ) VALUES (
                'replay-key', 'replay-v2', 'strategy-v1', 'policy-v1',
                'execution-v1', '2026-07-15T14:00:00+08:00',
                '2026-07-15T14:05:00+08:00', 'completed',
                '2025-01-01', '2025-12-31', 'official', 1,
                'verified', 1, '{}', '[]'
            )
            """
        ).lastrowid
        event_id = conn.execute(
            """
            INSERT INTO historical_replay_events (
                replay_run_id, trade_date, decision_at, code, name, industry,
                strategies_json, is_selected, selection_status, policy_version,
                snapshot_json, created_at
            ) VALUES (
                ?, '2025-01-02', '2025-01-02T14:00:00+08:00', '2330',
                '台積電', '半導體業', '["trend"]', 1, 'selected', 'policy-v1',
                '{}', '2026-07-15T14:00:00+08:00'
            )
            """,
            (replay_run_id,),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO historical_replay_outcomes (
                replay_event_id, execution_version, entry_status,
                net_return_3d, excess_return_3d, max_drawdown_3d,
                success_t3, matured_horizon, outcome_status,
                config_json, evaluated_at
            ) VALUES (?, 'execution-v1', 'filled', 1.25, 0.75, -1.5,
                      1, 3, 'mature', '{}',
                      '2026-07-15T14:05:00+08:00')
            """,
            (event_id,),
        )
        conn.execute(
            """
            INSERT INTO historical_replay_checkpoints (
                replay_run_id, partition_key, partition_start, partition_end,
                status, metrics_json
            ) VALUES (?, '2025-01', '2025-01-01', '2025-01-31',
                      'completed', '{}')
            """,
            (replay_run_id,),
        )
        conn.execute(
            """
            INSERT INTO historical_replay_attributions (
                replay_run_id, attribution_version, generated_at, dimension,
                bucket_key, bucket_label, selection_scope, metrics_json
            ) VALUES (?, 'attribution-v1', '2026-07-15T14:06:00+08:00',
                      'strategy', 'trend', '順勢突破', 'all', '{}')
            """,
            (replay_run_id,),
        )
        conn.execute(
            """
            INSERT INTO model_versions (
                model_name, version, status, feature_version,
                training_start, training_end, metrics_json, artifact_path, created_at
            ) VALUES (
                'shadow', 'replay-model-v1', 'shadow', 'features-v1',
                '2025-01-01', '2025-12-31',
                '{"outcome_source":"point_in_time_replay"}',
                '/tmp/models/replay-model-v1.joblib',
                '2026-07-15T14:07:00+08:00'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO model_challenger_evaluations (
                model_version, evaluated_at, status, qualified,
                rejection_reasons_json, metrics_json, created_at
            ) VALUES (
                'replay-model-v1', '2026-07-15T14:08:00+08:00', 'shadow', 0,
                '["non_positive_challenger_net_return"]', '{}',
                '2026-07-15T14:08:00+08:00'
            )
            """
        )


class MergeHistoricalReplayTests(unittest.TestCase):
    def test_compact_merge_preserves_summary_and_replaces_the_same_run(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.db"
            target = Path(directory) / "target.db"
            _seed_replay(source)
            with get_connection(target) as conn:
                init_db(conn)
                conn.execute(
                    """
                    INSERT INTO scan_runs (
                        run_at, trade_date, mode, source, strategy_version
                    ) VALUES ('2026-07-15T12:00:00+08:00', '2026-07-15',
                              'intraday', 'live', 'strategy-v1')
                    """
                )

            first = merge_historical_replay(source, target)
            second = merge_historical_replay(source, target)

            self.assertEqual(first["events"], 1)
            self.assertEqual(first["outcomes"], 1)
            self.assertEqual(first["checkpoints"], 1)
            self.assertEqual(first["attributions"], 1)
            self.assertNotEqual(first["source_replay_run_id"], 0)
            self.assertNotEqual(second["target_replay_run_id"], 0)
            with get_connection(target) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0], 1)
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM historical_replay_runs").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM historical_replay_events").fetchone()[0],
                    0,
                )
                summary = conn.execute(
                    """
                    SELECT filled_events, selected_mean_net_return_3d
                    FROM historical_replay_summaries
                    """
                ).fetchone()
                self.assertEqual(summary["filled_events"], 1)
                self.assertEqual(summary["selected_mean_net_return_3d"], 1.25)
                self.assertEqual(first["raw_events_persisted"], 0)
                model = conn.execute(
                    "SELECT version, artifact_path FROM model_versions"
                ).fetchone()
                self.assertEqual(model["version"], "replay-model-v1")
                self.assertEqual(
                    model["artifact_path"], "data/models/replay-model-v1.joblib"
                )
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM model_challenger_evaluations"
                    ).fetchone()[0],
                    1,
                )

    def test_include_raw_keeps_event_level_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.db"
            target = Path(directory) / "target.db"
            _seed_replay(source)
            result = merge_historical_replay(source, target, include_raw=True)
            with get_connection(target) as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM historical_replay_events").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM historical_replay_outcomes").fetchone()[0],
                    1,
                )
            self.assertEqual(result["raw_events_persisted"], 1)


if __name__ == "__main__":
    unittest.main()
