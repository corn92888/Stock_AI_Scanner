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
                strategies_json, selection_status, policy_version,
                snapshot_json, created_at
            ) VALUES (
                ?, '2025-01-02', '2025-01-02T14:00:00+08:00', '2330',
                '台積電', '半導體業', '["trend"]', 'selected', 'policy-v1',
                '{}', '2026-07-15T14:00:00+08:00'
            )
            """,
            (replay_run_id,),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO historical_replay_outcomes (
                replay_event_id, execution_version, entry_status,
                net_return_3d, matured_horizon, outcome_status,
                config_json, evaluated_at
            ) VALUES (?, 'execution-v1', 'filled', 1.25, 3, 'mature', '{}',
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


class MergeHistoricalReplayTests(unittest.TestCase):
    def test_merges_only_replay_tables_and_replaces_the_same_versioned_run(self):
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
                    1,
                )
                outcome = conn.execute(
                    """
                    SELECT hro.net_return_3d, hre.code
                    FROM historical_replay_outcomes hro
                    JOIN historical_replay_events hre ON hre.id=hro.replay_event_id
                    """
                ).fetchone()
                self.assertEqual(outcome["code"], "2330")
                self.assertEqual(outcome["net_return_3d"], 1.25)


if __name__ == "__main__":
    unittest.main()
