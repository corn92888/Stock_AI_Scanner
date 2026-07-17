import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from database import (
    CLOUD_EVIDENCE_SCHEMA_VERSION,
    DB_PATH,
    get_connection,
    record_cloud_evidence_event,
)


BUCKET = "scanner-evidence"
LIVE_SNAPSHOT_KEY = "live"
LIVE_OBJECT_PATH = "live/stock_scanner.db.gz"
BACKEND = "supabase_storage"
HTTP_TIMEOUT_SECONDS = 120


class EvidenceError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SupabaseConfig:
    url: str
    service_role_key: str

    @classmethod
    def from_environment(cls):
        url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not url or not key:
            return None
        return cls(url=url, service_role_key=key)


class SupabaseEvidenceClient:
    def __init__(self, config):
        self.config = config

    def _request(self, method, path, *, body=None, headers=None):
        request_headers = {
            "apikey": self.config.service_role_key,
            "Authorization": f"Bearer {self.config.service_role_key}",
            **(headers or {}),
        }
        request = urllib.request.Request(
            f"{self.config.url}{path}",
            data=body,
            headers=request_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(
                request, timeout=HTTP_TIMEOUT_SECONDS
            ) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise EvidenceError(
                f"http_{exc.code}",
                f"Supabase request failed with HTTP {exc.code}.",
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise EvidenceError(
                "network_error", "Supabase could not be reached."
            ) from exc

    def upload(self, object_path, content):
        encoded = urllib.parse.quote(object_path, safe="/")
        return self._request(
            "POST",
            f"/storage/v1/object/{BUCKET}/{encoded}",
            body=content,
            headers={
                "Content-Type": "application/gzip",
                "x-upsert": "true",
            },
        )

    def download(self, object_path):
        encoded = urllib.parse.quote(object_path, safe="/")
        return self._request("GET", f"/storage/v1/object/{BUCKET}/{encoded}")

    def upsert(self, table, payload, conflict_column):
        query = urllib.parse.urlencode({"on_conflict": conflict_column})
        return self._request(
            "POST",
            f"/rest/v1/{table}?{query}",
            body=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
        )

    def insert(self, table, payload):
        return self._request(
            "POST",
            f"/rest/v1/{table}",
            body=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
        )

    def get_live_snapshot(self):
        query = urllib.parse.urlencode(
            {
                "snapshot_key": f"eq.{LIVE_SNAPSHOT_KEY}",
                "select": (
                    "snapshot_key,object_path,snapshot_at,database_sha256,"
                    "compressed_sha256,database_bytes,compressed_bytes,"
                    "latest_scan_run_id,latest_trade_date,status"
                ),
                "limit": 1,
            }
        )
        body = self._request(
            "GET", f"/rest/v1/scanner_evidence_snapshots?{query}"
        )
        try:
            records = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EvidenceError(
                "invalid_manifest", "Supabase returned an invalid evidence manifest."
            ) from exc
        return records[0] if records else None


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def bytes_sha256(content):
    return hashlib.sha256(content).hexdigest()


def git_commit():
    configured = os.getenv("GITHUB_SHA", "").strip()
    if configured:
        return configured
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def source_workflow():
    return (
        os.getenv("EVIDENCE_WORKFLOW", "").strip()
        or os.getenv("GITHUB_WORKFLOW", "").strip()
        or "local"
    )


def create_consistent_backup(source_path, destination_path):
    source_path = Path(source_path)
    if not source_path.exists():
        raise EvidenceError("database_missing", f"Database not found: {source_path}")
    source = sqlite3.connect(f"file:{source_path.resolve()}?mode=ro", uri=True)
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
        result = destination.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise EvidenceError(
                "sqlite_integrity_failed", "SQLite integrity verification failed."
            )
    finally:
        destination.close()
        source.close()


def compress_database(source_path, destination_path):
    with Path(source_path).open("rb") as source, Path(destination_path).open(
        "wb"
    ) as output:
        with gzip.GzipFile(fileobj=output, mode="wb", compresslevel=6, mtime=0) as archive:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                archive.write(block)


def database_metadata(database_path):
    conn = sqlite3.connect(f"file:{Path(database_path).resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        table_names = [
            row[0]
            for row in conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        ]
        counts = {
            table: int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in table_names
        }
        latest = None
        if "scan_runs" in counts:
            latest = conn.execute(
                """
                SELECT id, trade_date, run_at
                FROM scan_runs
                ORDER BY run_at DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
        return {
            "table_counts": counts,
            "latest_scan_run_id": int(latest["id"]) if latest else None,
            "latest_trade_date": latest["trade_date"] if latest else None,
            "latest_run_at": latest["run_at"] if latest else None,
        }
    finally:
        conn.close()


def local_latest_scan_run_id(database_path):
    path = Path(database_path)
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='scan_runs'"
        ).fetchone()
        if not exists:
            return None
        row = conn.execute("SELECT MAX(id) FROM scan_runs").fetchone()
        return int(row[0]) if row and row[0] is not None else None
    finally:
        conn.close()


def local_evidence_hash(database_path):
    path = Path(database_path)
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        exists = conn.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='cloud_evidence_events'
            """
        ).fetchone()
        if not exists:
            return None
        row = conn.execute(
            """
            SELECT database_sha256
            FROM cloud_evidence_events
            WHERE status='verified' AND database_sha256 IS NOT NULL
            ORDER BY event_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def record_local_event(database_path, *, operation, status, **values):
    path = Path(database_path)
    if not path.exists():
        return
    metadata = values.pop("metadata", {})
    with get_connection(path) as conn:
        record_cloud_evidence_event(
            conn,
            event_at=utc_now(),
            backend=BACKEND,
            operation=operation,
            status=status,
            schema_version=CLOUD_EVIDENCE_SCHEMA_VERSION,
            metadata_json=json.dumps(metadata, separators=(",", ":")),
            **values,
        )


def snapshot_manifest(snapshot_key, object_path, metadata, database_path, archive_path):
    return {
        "snapshot_key": snapshot_key,
        "object_path": object_path,
        "snapshot_at": utc_now(),
        "source_workflow": source_workflow(),
        "source_run_id": os.getenv("GITHUB_RUN_ID", "").strip() or None,
        "source_commit": git_commit() or None,
        "schema_version": CLOUD_EVIDENCE_SCHEMA_VERSION,
        "database_sha256": file_sha256(database_path),
        "compressed_sha256": file_sha256(archive_path),
        "database_bytes": Path(database_path).stat().st_size,
        "compressed_bytes": Path(archive_path).stat().st_size,
        "latest_scan_run_id": metadata["latest_scan_run_id"],
        "latest_trade_date": metadata["latest_trade_date"],
        "latest_run_at": metadata["latest_run_at"],
        "sqlite_integrity": "ok",
        "table_counts": metadata["table_counts"],
        "dashboard_sha256": None,
        "status": "verified",
        "verified_at": utc_now(),
        "metadata": {
            "compression": "gzip",
            "migration_mode": "dual_write",
        },
    }


def verify_download(content, manifest):
    if bytes_sha256(content) != manifest["compressed_sha256"]:
        raise EvidenceError(
            "compressed_hash_mismatch", "Downloaded cloud archive hash did not match."
        )
    try:
        database_content = gzip.decompress(content)
    except (gzip.BadGzipFile, OSError) as exc:
        raise EvidenceError("invalid_gzip", "Downloaded cloud archive is invalid.") from exc
    if bytes_sha256(database_content) != manifest["database_sha256"]:
        raise EvidenceError(
            "database_hash_mismatch", "Downloaded database hash did not match."
        )
    return database_content


def publish_manifest(client, manifest):
    client.upsert(
        "scanner_evidence_snapshots", manifest, conflict_column="snapshot_key"
    )
    client.insert(
        "scanner_evidence_sync_events",
        sync_event_payload(manifest, operation="push"),
    )


def sync_event_payload(manifest, *, operation):
    return {
        "event_at": utc_now(),
        "snapshot_key": manifest["snapshot_key"],
        "operation": operation,
        "status": "verified",
        "source_workflow": source_workflow(),
        "source_run_id": os.getenv("GITHUB_RUN_ID", "").strip() or None,
        "source_commit": git_commit() or None,
        "database_sha256": manifest["database_sha256"],
        "database_bytes": manifest.get("database_bytes"),
        "compressed_bytes": manifest.get("compressed_bytes"),
        "latest_scan_run_id": manifest.get("latest_scan_run_id"),
        "latest_trade_date": manifest.get("latest_trade_date"),
        "details": {"object_path": manifest["object_path"]},
    }


def push(database_path, *, archive_daily=False):
    config = SupabaseConfig.from_environment()
    if config is None:
        raise EvidenceError(
            "not_configured", "Supabase cloud evidence credentials are not configured."
        )
    client = SupabaseEvidenceClient(config)
    with tempfile.TemporaryDirectory(prefix="scanner-evidence-") as directory:
        backup_path = Path(directory) / "stock_scanner.db"
        archive_path = Path(directory) / "stock_scanner.db.gz"
        create_consistent_backup(database_path, backup_path)
        metadata = database_metadata(backup_path)
        compress_database(backup_path, archive_path)
        content = archive_path.read_bytes()

        live_manifest = snapshot_manifest(
            LIVE_SNAPSHOT_KEY,
            LIVE_OBJECT_PATH,
            metadata,
            backup_path,
            archive_path,
        )
        client.upload(LIVE_OBJECT_PATH, content)
        verify_download(client.download(LIVE_OBJECT_PATH), live_manifest)
        publish_manifest(client, live_manifest)

        if archive_daily and metadata["latest_trade_date"]:
            trade_date = metadata["latest_trade_date"]
            archive_object_path = f"daily/{trade_date}/stock_scanner.db.gz"
            daily_manifest = snapshot_manifest(
                f"daily:{trade_date}",
                archive_object_path,
                metadata,
                backup_path,
                archive_path,
            )
            client.upload(archive_object_path, content)
            verify_download(client.download(archive_object_path), daily_manifest)
            publish_manifest(client, daily_manifest)

    record_local_event(
        database_path,
        operation="push",
        status="verified",
        snapshot_key=LIVE_SNAPSHOT_KEY,
        object_path=LIVE_OBJECT_PATH,
        database_sha256=live_manifest["database_sha256"],
        database_bytes=live_manifest["database_bytes"],
        compressed_bytes=live_manifest["compressed_bytes"],
        latest_scan_run_id=live_manifest["latest_scan_run_id"],
        latest_trade_date=live_manifest["latest_trade_date"],
        source_workflow=live_manifest["source_workflow"],
        error_code=None,
        metadata={"archive_daily": archive_daily, "verified_download": True},
    )
    return live_manifest


def restore(database_path, *, if_newer=False):
    config = SupabaseConfig.from_environment()
    if config is None:
        raise EvidenceError(
            "not_configured", "Supabase cloud evidence credentials are not configured."
        )
    client = SupabaseEvidenceClient(config)
    manifest = client.get_live_snapshot()
    if not manifest or manifest.get("status") != "verified":
        raise EvidenceError(
            "snapshot_unavailable", "No verified live cloud snapshot is available."
        )
    cloud_run_id = manifest.get("latest_scan_run_id")
    local_run_id = local_latest_scan_run_id(database_path)
    if if_newer and local_run_id is not None:
        if cloud_run_id is None or int(cloud_run_id) < local_run_id:
            return {"restored": False, "reason": "local_run_is_newer", **manifest}
        if (
            int(cloud_run_id) == local_run_id
            and local_evidence_hash(database_path) == manifest["database_sha256"]
        ):
            return {"restored": False, "reason": "same_verified_snapshot", **manifest}

    content = client.download(manifest["object_path"])
    database_content = verify_download(content, manifest)
    destination = Path(database_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=destination.parent, prefix=f".{destination.name}.", delete=False
    ) as temporary:
        temporary.write(database_content)
        temporary_path = Path(temporary.name)
    try:
        conn = sqlite3.connect(f"file:{temporary_path.resolve()}?mode=ro", uri=True)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
        if integrity != "ok":
            raise EvidenceError(
                "sqlite_integrity_failed", "Restored SQLite snapshot failed integrity check."
            )
        temporary_path.replace(destination)
    finally:
        temporary_path.unlink(missing_ok=True)

    client.insert(
        "scanner_evidence_sync_events",
        sync_event_payload(manifest, operation="restore"),
    )
    record_local_event(
        database_path,
        operation="restore",
        status="verified",
        snapshot_key=manifest["snapshot_key"],
        object_path=manifest["object_path"],
        database_sha256=manifest["database_sha256"],
        database_bytes=manifest.get("database_bytes"),
        compressed_bytes=manifest.get("compressed_bytes"),
        latest_scan_run_id=manifest.get("latest_scan_run_id"),
        latest_trade_date=manifest.get("latest_trade_date"),
        source_workflow=source_workflow(),
        error_code=None,
        metadata={"verified_download": True},
    )
    return {"restored": True, **manifest}


def handle_failure(database_path, operation, error, *, required):
    status = "unconfigured" if error.code == "not_configured" else "failed"
    record_local_event(
        database_path,
        operation=operation,
        status=status,
        snapshot_key=LIVE_SNAPSHOT_KEY,
        object_path=LIVE_OBJECT_PATH,
        database_sha256=None,
        database_bytes=Path(database_path).stat().st_size
        if Path(database_path).exists()
        else None,
        compressed_bytes=None,
        latest_scan_run_id=local_latest_scan_run_id(database_path),
        latest_trade_date=None,
        source_workflow=source_workflow(),
        error_code=error.code,
        metadata={"required": required},
    )
    print(f"Cloud evidence {operation}: {error}")
    if required:
        raise error


def build_parser():
    parser = argparse.ArgumentParser(
        description="Store and restore verified scanner database snapshots in Supabase."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    push_parser = subparsers.add_parser("push")
    push_parser.add_argument("--database", default=str(DB_PATH))
    push_parser.add_argument("--archive-daily", action="store_true")
    push_parser.add_argument("--required", action="store_true")

    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--database", default=str(DB_PATH))
    restore_parser.add_argument("--if-newer", action="store_true")
    restore_parser.add_argument("--required", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "push":
            result = push(args.database, archive_daily=args.archive_daily)
            print(
                "Cloud evidence verified: "
                f"run={result['latest_scan_run_id']} "
                f"sha256={result['database_sha256'][:12]} "
                f"compressed_bytes={result['compressed_bytes']}"
            )
        else:
            result = restore(args.database, if_newer=args.if_newer)
            if result["restored"]:
                print(
                    "Cloud evidence restored: "
                    f"run={result.get('latest_scan_run_id')} "
                    f"sha256={result['database_sha256'][:12]}"
                )
            else:
                print("Cloud evidence restore skipped: local database is not older.")
    except EvidenceError as error:
        handle_failure(
            args.database, args.command, error, required=args.required
        )


if __name__ == "__main__":
    main()
