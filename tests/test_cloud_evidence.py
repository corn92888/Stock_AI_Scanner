import gzip
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cloud_evidence
from database import get_connection, init_db


class FakeSupabaseEvidenceClient:
    objects = {}
    snapshots = {}
    events = []

    def __init__(self, config):
        self.config = config

    @classmethod
    def reset(cls):
        cls.objects = {}
        cls.snapshots = {}
        cls.events = []

    def upload(self, object_path, content):
        self.__class__.objects[object_path] = content

    def download(self, object_path):
        return self.__class__.objects[object_path]

    def upsert(self, table, payload, conflict_column):
        assert table == "scanner_evidence_snapshots"
        assert conflict_column == "snapshot_key"
        self.__class__.snapshots[payload["snapshot_key"]] = dict(payload)

    def insert(self, table, payload):
        assert table == "scanner_evidence_sync_events"
        self.__class__.events.append(dict(payload))

    def get_live_snapshot(self):
        return self.__class__.snapshots.get(cloud_evidence.LIVE_SNAPSHOT_KEY)


class TamperingSupabaseEvidenceClient(FakeSupabaseEvidenceClient):
    def download(self, object_path):
        return super().download(object_path) + b"corrupt"


def create_database(path, run_id, trade_date):
    with get_connection(path) as conn:
        init_db(conn)
        conn.execute(
            """
            INSERT INTO scan_runs (
                id, run_at, trade_date, mode, source, strategy_version
            ) VALUES (?, ?, ?, 'intraday', 'test', 'test-v1')
            """,
            (run_id, f"{trade_date}T10:00:00+08:00", trade_date),
        )


class CloudEvidenceTests(unittest.TestCase):
    def setUp(self):
        FakeSupabaseEvidenceClient.reset()
        self.environment = patch.dict(
            os.environ,
            {
                "SUPABASE_URL": "https://example.supabase.co",
                "SUPABASE_SERVICE_ROLE_KEY": "service-role-test-key",
                "EVIDENCE_WORKFLOW": "unit_test",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_push_uploads_and_verifies_live_and_daily_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "scanner.db"
            create_database(database, 7, "2026-07-17")

            with patch(
                "cloud_evidence.SupabaseEvidenceClient",
                FakeSupabaseEvidenceClient,
            ):
                manifest = cloud_evidence.push(database, archive_daily=True)

            self.assertEqual(manifest["latest_scan_run_id"], 7)
            self.assertEqual(manifest["latest_trade_date"], "2026-07-17")
            self.assertEqual(manifest["sqlite_integrity"], "ok")
            self.assertIn("live/stock_scanner.db.gz", FakeSupabaseEvidenceClient.objects)
            self.assertIn(
                "daily/2026-07-17/stock_scanner.db.gz",
                FakeSupabaseEvidenceClient.objects,
            )
            self.assertEqual(
                len(gzip.decompress(FakeSupabaseEvidenceClient.objects[manifest["object_path"]])),
                manifest["database_bytes"],
            )
            self.assertEqual(len(FakeSupabaseEvidenceClient.events), 2)

            conn = sqlite3.connect(database)
            event = conn.execute(
                """
                SELECT operation, status, latest_scan_run_id, error_code
                FROM cloud_evidence_events ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
            conn.close()
            self.assertEqual(event, ("push", "verified", 7, None))

    def test_restore_replaces_only_an_older_local_database(self):
        with tempfile.TemporaryDirectory() as directory:
            remote_database = Path(directory) / "remote.db"
            local_database = Path(directory) / "local.db"
            create_database(remote_database, 9, "2026-07-17")
            create_database(local_database, 4, "2026-07-16")

            with patch(
                "cloud_evidence.SupabaseEvidenceClient",
                FakeSupabaseEvidenceClient,
            ):
                cloud_evidence.push(remote_database)
                restored = cloud_evidence.restore(local_database, if_newer=True)
                skipped = cloud_evidence.restore(local_database, if_newer=True)

            self.assertTrue(restored["restored"])
            self.assertFalse(skipped["restored"])
            self.assertEqual(
                FakeSupabaseEvidenceClient.events[-1]["operation"], "restore"
            )
            conn = sqlite3.connect(local_database)
            self.assertEqual(conn.execute("SELECT MAX(id) FROM scan_runs").fetchone()[0], 9)
            event = conn.execute(
                "SELECT operation, status FROM cloud_evidence_events ORDER BY id DESC LIMIT 1"
            ).fetchone()
            conn.close()
            self.assertEqual(event, ("restore", "verified"))

    def test_restore_detects_new_cloud_data_when_scan_run_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            remote_database = Path(directory) / "remote.db"
            local_database = Path(directory) / "local.db"
            create_database(remote_database, 5, "2026-07-17")

            with patch(
                "cloud_evidence.SupabaseEvidenceClient",
                FakeSupabaseEvidenceClient,
            ):
                cloud_evidence.push(remote_database)
                cloud_evidence.restore(local_database, if_newer=True)
                with get_connection(remote_database) as conn:
                    conn.execute(
                        """
                        INSERT INTO market_regime_snapshots (
                            snapshot_at, score, regime_label, taiwan_bias_score,
                            taiwan_bias_label, coverage_pct, active_fresh_pct,
                            components_json, drivers_json, quality_json, created_at
                        ) VALUES (
                            '2026-07-17T11:00:00+08:00', 61, 'risk_on', 58,
                            'constructive', 100, 100, '[]', '[]', '{}',
                            '2026-07-17T11:00:00+08:00'
                        )
                        """
                    )
                cloud_evidence.push(remote_database)
                restored = cloud_evidence.restore(local_database, if_newer=True)

            self.assertTrue(restored["restored"])
            conn = sqlite3.connect(local_database)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM market_regime_snapshots").fetchone()[0],
                1,
            )
            conn.close()

    def test_push_rejects_a_tampered_download(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "scanner.db"
            create_database(database, 3, "2026-07-17")

            with patch(
                "cloud_evidence.SupabaseEvidenceClient",
                TamperingSupabaseEvidenceClient,
            ):
                with self.assertRaises(cloud_evidence.EvidenceError) as raised:
                    cloud_evidence.push(database)

            self.assertEqual(raised.exception.code, "compressed_hash_mismatch")

    def test_optional_cli_records_missing_configuration_without_failing(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "scanner.db"
            create_database(database, 2, "2026-07-17")
            with patch.dict(
                os.environ,
                {"SUPABASE_URL": "", "SUPABASE_SERVICE_ROLE_KEY": ""},
            ):
                cloud_evidence.main(["push", "--database", str(database)])

            conn = sqlite3.connect(database)
            event = conn.execute(
                "SELECT status, error_code FROM cloud_evidence_events ORDER BY id DESC LIMIT 1"
            ).fetchone()
            conn.close()
            self.assertEqual(event, ("unconfigured", "not_configured"))


if __name__ == "__main__":
    unittest.main()
