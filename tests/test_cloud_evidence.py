import gzip
import json
import os
import socket
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

import cloud_evidence
from database import get_connection, init_db


class FakeSupabaseEvidenceClient:
    objects = {}
    snapshots = {}
    events = []
    audits = []

    def __init__(self, config):
        self.config = config

    @classmethod
    def reset(cls):
        cls.objects = {}
        cls.snapshots = {}
        cls.events = []
        cls.audits = []

    def upload(self, object_path, content):
        self.__class__.objects[object_path] = content

    def download(self, object_path):
        return self.__class__.objects[object_path]

    def upsert(self, table, payload, conflict_column):
        if table == "scanner_evidence_snapshots":
            assert conflict_column == "snapshot_key"
            self.__class__.snapshots[payload["snapshot_key"]] = dict(payload)
            return
        assert table == "scanner_evidence_cutover_audits"
        assert conflict_column == "audited_at"
        self.__class__.audits.append(dict(payload))

    def insert(self, table, payload):
        assert table == "scanner_evidence_sync_events"
        self.__class__.events.append(dict(payload))

    def get_live_snapshot(self):
        return self.__class__.snapshots.get(cloud_evidence.LIVE_SNAPSHOT_KEY)

    def list_snapshots(self, limit=200):
        return list(self.__class__.snapshots.values())[:limit]

    def list_sync_events(self, limit=200):
        return list(reversed(self.__class__.events))[:limit]

    def delete_objects(self, object_paths):
        for object_path in object_paths:
            self.__class__.objects.pop(object_path, None)

    def delete_snapshot(self, snapshot_key):
        self.__class__.snapshots.pop(snapshot_key, None)


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

    def test_client_classifies_dns_resolution_failure(self):
        config = cloud_evidence.SupabaseConfig(
            url="https://missing.supabase.co",
            service_role_key="service-role-test-key",
        )
        client = cloud_evidence.SupabaseEvidenceClient(config)
        failure = URLError(socket.gaierror(-2, "Name or service not known"))

        with patch("urllib.request.urlopen", side_effect=failure):
            with self.assertRaises(cloud_evidence.EvidenceError) as context:
                client.list_snapshots()

        self.assertEqual(context.exception.code, "dns_resolution_failed")

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

    def test_cutover_audit_verifies_restore_counts_and_remote_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            older_database = Path(directory) / "older.db"
            current_database = Path(directory) / "current.db"
            create_database(older_database, 8, "2026-07-16")
            create_database(current_database, 9, "2026-07-17")

            with patch(
                "cloud_evidence.SupabaseEvidenceClient",
                FakeSupabaseEvidenceClient,
            ):
                cloud_evidence.push(older_database, archive_daily=True)
                cloud_evidence.push(current_database, archive_daily=True)
                report = cloud_evidence.audit_cutover(
                    current_database,
                    minimum_daily_snapshots=2,
                    minimum_verified_pushes=2,
                    minimum_workflows=1,
                )

            self.assertTrue(report["ready"])
            self.assertEqual(report["passedChecks"], report["totalChecks"])
            self.assertEqual(report["dailySnapshots"], 2)
            self.assertEqual(len(FakeSupabaseEvidenceClient.audits), 1)
            conn = sqlite3.connect(current_database)
            audit = conn.execute(
                """
                SELECT status, ready, passed_checks, total_checks
                FROM cloud_evidence_audits ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
            conn.close()
            self.assertEqual(audit, ("ready", 1, 10, 10))

    def test_cutover_audit_blocks_when_daily_history_is_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "scanner.db"
            create_database(database, 9, "2026-07-17")

            with patch(
                "cloud_evidence.SupabaseEvidenceClient",
                FakeSupabaseEvidenceClient,
            ):
                cloud_evidence.push(database, archive_daily=True)
                report = cloud_evidence.audit_cutover(
                    database,
                    minimum_daily_snapshots=2,
                    minimum_verified_pushes=1,
                    minimum_workflows=1,
                )

            self.assertFalse(report["ready"])
            daily_check = next(
                check for check in report["checks"] if check["key"] == "daily_snapshots"
            )
            self.assertFalse(daily_check["passed"])

    def test_cutover_audit_records_an_invalid_manifest_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "scanner.db"
            create_database(database, 9, "2026-07-17")

            with patch(
                "cloud_evidence.SupabaseEvidenceClient",
                FakeSupabaseEvidenceClient,
            ):
                cloud_evidence.push(database, archive_daily=True)
                FakeSupabaseEvidenceClient.snapshots[
                    cloud_evidence.LIVE_SNAPSHOT_KEY
                ]["snapshot_at"] = "not-a-timestamp"
                result = cloud_evidence.main(
                    ["audit", "--database", str(database)]
                )

            self.assertEqual(result, 0)
            conn = sqlite3.connect(database)
            audit = conn.execute(
                """
                SELECT status, ready, report_json
                FROM cloud_evidence_audits ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
            conn.close()
            self.assertEqual(audit[0:2], ("blocked", 0))
            self.assertEqual(
                json.loads(audit[2])["errorCode"],
                "invalid_snapshot_timestamp",
            )

    def test_retention_prunes_only_expired_daily_snapshots(self):
        today = cloud_evidence.dt.datetime.now(cloud_evidence.dt.timezone.utc).date()
        current_key = f"daily:{today.isoformat()}"
        old_key = "daily:2000-01-01"
        FakeSupabaseEvidenceClient.snapshots = {
            cloud_evidence.LIVE_SNAPSHOT_KEY: {
                "snapshot_key": cloud_evidence.LIVE_SNAPSHOT_KEY,
                "object_path": cloud_evidence.LIVE_OBJECT_PATH,
            },
            current_key: {
                "snapshot_key": current_key,
                "object_path": f"daily/{today.isoformat()}/stock_scanner.db.gz",
            },
            old_key: {
                "snapshot_key": old_key,
                "object_path": "daily/2000-01-01/stock_scanner.db.gz",
            },
        }
        FakeSupabaseEvidenceClient.objects = {
            row["object_path"]: b"snapshot"
            for row in FakeSupabaseEvidenceClient.snapshots.values()
        }

        with patch(
            "cloud_evidence.SupabaseEvidenceClient",
            FakeSupabaseEvidenceClient,
        ):
            preview = cloud_evidence.prune_daily_snapshots(
                retention_days=45, apply=False
            )
            applied = cloud_evidence.prune_daily_snapshots(
                retention_days=45, apply=True
            )

        self.assertEqual(preview["snapshotKeys"], [old_key])
        self.assertEqual(applied["deletedCount"], 1)
        self.assertIn(current_key, FakeSupabaseEvidenceClient.snapshots)
        self.assertIn(cloud_evidence.LIVE_SNAPSHOT_KEY, FakeSupabaseEvidenceClient.snapshots)
        self.assertNotIn(old_key, FakeSupabaseEvidenceClient.snapshots)

    def test_cloud_primary_makes_missing_configuration_fatal(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "scanner.db"
            create_database(database, 2, "2026-07-17")
            with patch.dict(
                os.environ,
                {
                    "SUPABASE_URL": "",
                    "SUPABASE_SERVICE_ROLE_KEY": "",
                    "CLOUD_EVIDENCE_MODE": "cloud_primary",
                },
            ):
                result = cloud_evidence.main(["push", "--database", str(database)])

            self.assertEqual(result, 1)
            conn = sqlite3.connect(database)
            event = conn.execute(
                """
                SELECT status, migration_mode, error_code
                FROM cloud_evidence_events ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
            conn.close()
            self.assertEqual(
                event, ("unconfigured", "cloud_primary", "not_configured")
            )


if __name__ == "__main__":
    unittest.main()
