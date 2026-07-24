import tempfile
import unittest
from pathlib import Path

from alpha_forward_monitor import (
    build_alpha_forward_metrics,
    run_alpha_forward_monitor,
)
from database import get_connection, init_db, record_cloud_evidence_event


def insert_alpha_run(conn, with_pool=True):
    run_id = conn.execute(
        """
        INSERT INTO alpha_live_runs (
            signal_date, generated_at, model_version, artifact_fingerprint,
            dataset_fingerprint, status, confidence, confidence_threshold,
            universe_count, eligible_count, selected_count, diagnostics_json
        ) VALUES (
            '2026-07-24', '2026-07-24T14:20:00+08:00', 'alpha-v2',
            'artifact', 'dataset', 'active', 2.0, 1.0, 1000, 300, 3, '{}'
        )
        """
    ).lastrowid
    if with_pool:
        conn.execute(
            """
            INSERT INTO alpha_live_candidates (
                run_id, code, name, industry, signal_price, predicted_alpha,
                created_at
            ) VALUES (
                ?, '2330', 'TSMC', 'Semiconductor', 1000, 2.4,
                '2026-07-24T14:20:00+08:00'
            )
            """,
            (run_id,),
        )
    return run_id


class AlphaForwardMonitorTests(unittest.TestCase):
    def test_missing_candidate_pool_pauses_new_positions(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "scanner.db"
            with get_connection(database) as conn:
                init_db(conn)
                insert_alpha_run(conn, with_pool=False)

            metrics = build_alpha_forward_metrics(database)

            self.assertEqual(metrics["state"], "PAUSED")
            self.assertFalse(metrics["allow_new_positions"])
            self.assertIn("candidate_pool_missing", metrics["reason_codes"])

    def test_cloud_fallback_is_degraded_but_does_not_pause_model(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "scanner.db"
            with get_connection(database) as conn:
                init_db(conn)
                insert_alpha_run(conn, with_pool=True)
                record_cloud_evidence_event(
                    conn,
                    event_at="2026-07-24T06:30:00+00:00",
                    backend="supabase_storage",
                    operation="push",
                    status="failed",
                    schema_version="supabase_sqlite_snapshot_v1",
                    snapshot_key="live",
                    object_path="live/stock_scanner.db.gz",
                    database_sha256=None,
                    database_bytes=1024,
                    compressed_bytes=None,
                    latest_scan_run_id=None,
                    latest_trade_date="2026-07-24",
                    source_workflow="daily_scan",
                    migration_mode="dual_write",
                    error_code="dns_resolution_failed",
                    metadata_json="{}",
                )

            metrics = run_alpha_forward_monitor(database)

            self.assertEqual(metrics["state"], "COLLECTING")
            self.assertTrue(metrics["allow_new_positions"])
            self.assertEqual(metrics["data_quality_status"], "degraded")
            with get_connection(database) as conn:
                saved = conn.execute(
                    "SELECT state FROM alpha_forward_snapshots"
                ).fetchone()
            self.assertEqual(saved["state"], "COLLECTING")


if __name__ == "__main__":
    unittest.main()
