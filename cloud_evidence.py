import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from database import (
    CLOUD_CUTOVER_AUDIT_VERSION,
    CLOUD_EVIDENCE_SCHEMA_VERSION,
    DB_PATH,
    get_connection,
    record_cloud_evidence_audit,
    record_cloud_evidence_event,
)


BUCKET = "scanner-evidence"
LIVE_SNAPSHOT_KEY = "live"
LIVE_OBJECT_PATH = "live/stock_scanner.db.gz"
BACKEND = "supabase_storage"
HTTP_TIMEOUT_SECONDS = 120
DEFAULT_RETENTION_DAYS = 45
DEFAULT_MINIMUM_DAILY_SNAPSHOTS = 2
DEFAULT_MINIMUM_VERIFIED_PUSHES = 4
DEFAULT_MINIMUM_WORKFLOWS = 2
DEFAULT_MAX_LIVE_AGE_HOURS = 36
VALID_MIGRATION_MODES = {"dual_write", "cloud_primary"}
LOCAL_ONLY_TABLES = {"cloud_evidence_events", "cloud_evidence_audits"}


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


def migration_mode():
    mode = os.getenv("CLOUD_EVIDENCE_MODE", "dual_write").strip().lower()
    if mode not in VALID_MIGRATION_MODES:
        raise EvidenceError(
            "invalid_migration_mode",
            "CLOUD_EVIDENCE_MODE must be dual_write or cloud_primary.",
        )
    return mode


def cloud_is_required():
    legacy_required = os.getenv("CLOUD_EVIDENCE_REQUIRED", "").strip().lower()
    return migration_mode() == "cloud_primary" or legacy_required == "true"


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
                    "latest_scan_run_id,latest_trade_date,latest_run_at,"
                    "table_counts,status,verified_at,source_workflow"
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

    def list_snapshots(self, limit=200):
        query = urllib.parse.urlencode(
            {
                "select": (
                    "snapshot_key,object_path,snapshot_at,database_sha256,"
                    "compressed_sha256,database_bytes,compressed_bytes,"
                    "latest_scan_run_id,latest_trade_date,latest_run_at,"
                    "table_counts,status,verified_at,source_workflow"
                ),
                "order": "snapshot_at.desc",
                "limit": limit,
            }
        )
        body = self._request(
            "GET", f"/rest/v1/scanner_evidence_snapshots?{query}"
        )
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EvidenceError(
                "invalid_manifest", "Supabase returned invalid snapshot records."
            ) from exc

    def list_sync_events(self, limit=200):
        query = urllib.parse.urlencode(
            {
                "select": (
                    "event_at,snapshot_key,operation,status,source_workflow,"
                    "latest_scan_run_id,latest_trade_date"
                ),
                "order": "event_at.desc",
                "limit": limit,
            }
        )
        body = self._request(
            "GET", f"/rest/v1/scanner_evidence_sync_events?{query}"
        )
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EvidenceError(
                "invalid_sync_events", "Supabase returned invalid sync events."
            ) from exc

    def delete_objects(self, object_paths):
        if not object_paths:
            return b""
        return self._request(
            "DELETE",
            f"/storage/v1/object/{BUCKET}",
            body=json.dumps({"prefixes": object_paths}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

    def delete_snapshot(self, snapshot_key):
        query = urllib.parse.urlencode({"snapshot_key": f"eq.{snapshot_key}"})
        return self._request(
            "DELETE",
            f"/rest/v1/scanner_evidence_snapshots?{query}",
            headers={"Prefer": "return=minimal"},
        )


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
    values.setdefault("migration_mode", migration_mode())
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
            "migration_mode": migration_mode(),
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


def parse_timestamp(value):
    if not value:
        return None
    normalized = str(value).replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise EvidenceError(
            "invalid_snapshot_timestamp",
            "Cloud evidence manifest contains an invalid snapshot timestamp.",
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def write_json(path, payload):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)


def audit_check(key, label, passed, detail, requirement):
    return {
        "key": key,
        "label": label,
        "passed": bool(passed),
        "detail": str(detail),
        "requirement": str(requirement),
    }


def count_differences(local_counts, cloud_counts):
    differences = {}
    table_names = sorted(set(local_counts) | set(cloud_counts))
    for table in table_names:
        if table in LOCAL_ONLY_TABLES:
            continue
        local_count = int(local_counts.get(table, 0) or 0)
        cloud_count = int(cloud_counts.get(table, 0) or 0)
        if local_count != cloud_count:
            differences[table] = {"local": local_count, "cloud": cloud_count}
    return differences


def build_audit_report(
    *,
    checks,
    live_manifest=None,
    daily_snapshots=0,
    verified_pushes=0,
    workflow_count=0,
    error_code=None,
):
    passed_checks = sum(1 for check in checks if check["passed"])
    ready = bool(checks) and passed_checks == len(checks)
    live_manifest = live_manifest or {}
    return {
        "auditVersion": CLOUD_CUTOVER_AUDIT_VERSION,
        "auditedAt": utc_now(),
        "migrationMode": migration_mode(),
        "status": "ready" if ready else "blocked",
        "ready": ready,
        "passedChecks": passed_checks,
        "totalChecks": len(checks),
        "dailySnapshots": int(daily_snapshots),
        "verifiedPushes": int(verified_pushes),
        "workflowCount": int(workflow_count),
        "errorCode": error_code,
        "live": {
            "snapshotAt": live_manifest.get("snapshot_at"),
            "verifiedAt": live_manifest.get("verified_at"),
            "latestScanRunId": live_manifest.get("latest_scan_run_id"),
            "latestTradeDate": live_manifest.get("latest_trade_date"),
            "sourceWorkflow": live_manifest.get("source_workflow"),
            "databaseSha256": live_manifest.get("database_sha256"),
            "databaseBytes": live_manifest.get("database_bytes"),
            "compressedBytes": live_manifest.get("compressed_bytes"),
        },
        "checks": checks,
    }


def record_local_audit(database_path, report):
    if not Path(database_path).exists():
        return
    with get_connection(database_path) as conn:
        record_cloud_evidence_audit(conn, report)


def audit_cutover(
    database_path,
    *,
    minimum_daily_snapshots=DEFAULT_MINIMUM_DAILY_SNAPSHOTS,
    minimum_verified_pushes=DEFAULT_MINIMUM_VERIFIED_PUSHES,
    minimum_workflows=DEFAULT_MINIMUM_WORKFLOWS,
    max_live_age_hours=DEFAULT_MAX_LIVE_AGE_HOURS,
):
    config = SupabaseConfig.from_environment()
    if config is None:
        raise EvidenceError(
            "not_configured", "Supabase cloud evidence credentials are not configured."
        )
    client = SupabaseEvidenceClient(config)
    snapshots = client.list_snapshots()
    live_manifest = next(
        (row for row in snapshots if row.get("snapshot_key") == LIVE_SNAPSHOT_KEY),
        None,
    )
    if not live_manifest:
        raise EvidenceError(
            "snapshot_unavailable", "No verified live cloud snapshot is available."
        )
    events = client.list_sync_events()
    daily = [
        row
        for row in snapshots
        if str(row.get("snapshot_key", "")).startswith("daily:")
        and row.get("status") == "verified"
    ]
    verified_push_events = [
        row
        for row in events
        if row.get("operation") == "push"
        and row.get("status") == "verified"
        and row.get("snapshot_key") == LIVE_SNAPSHOT_KEY
    ]
    workflows = {
        row.get("source_workflow")
        for row in verified_push_events
        if row.get("source_workflow")
    }
    checks = [
        audit_check(
            "live_manifest",
            "Live manifest 已驗證",
            live_manifest.get("status") == "verified",
            live_manifest.get("status") or "missing",
            "verified",
        ),
        audit_check(
            "daily_snapshots",
            "每日快照覆蓋",
            len(daily) >= minimum_daily_snapshots,
            len(daily),
            f">={minimum_daily_snapshots}",
        ),
        audit_check(
            "verified_pushes",
            "連續雲端寫入證據",
            len(verified_push_events) >= minimum_verified_pushes,
            len(verified_push_events),
            f">={minimum_verified_pushes}",
        ),
        audit_check(
            "workflow_coverage",
            "來源工作流覆蓋",
            len(workflows) >= minimum_workflows,
            ", ".join(sorted(workflows)) or "none",
            f">={minimum_workflows} workflows",
        ),
    ]
    snapshot_at = parse_timestamp(live_manifest.get("snapshot_at"))
    age_hours = (
        max(0.0, (dt.datetime.now(dt.timezone.utc) - snapshot_at).total_seconds() / 3600)
        if snapshot_at
        else None
    )
    checks.append(
        audit_check(
            "live_freshness",
            "Live 快照時效",
            age_hours is not None and age_hours <= max_live_age_hours,
            f"{age_hours:.2f}h" if age_hours is not None else "missing",
            f"<={max_live_age_hours}h",
        )
    )

    content = client.download(live_manifest["object_path"])
    database_content = verify_download(content, live_manifest)
    checks.append(
        audit_check(
            "download_hashes",
            "下載後雙重雜湊",
            True,
            "compressed and database SHA-256 matched",
            "both hashes match",
        )
    )
    with tempfile.TemporaryDirectory(prefix="scanner-cutover-audit-") as directory:
        restored_database = Path(directory) / "restored.db"
        restored_database.write_bytes(database_content)
        restored_metadata = database_metadata(restored_database)
        conn = sqlite3.connect(
            f"file:{restored_database.resolve()}?mode=ro", uri=True
        )
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
    checks.append(
        audit_check(
            "sqlite_integrity",
            "SQLite 復原完整性",
            integrity == "ok",
            integrity,
            "ok",
        )
    )

    local_metadata = database_metadata(database_path)
    local_run_id = local_metadata["latest_scan_run_id"]
    cloud_run_id = live_manifest.get("latest_scan_run_id")
    checks.append(
        audit_check(
            "latest_scan_run",
            "最新掃描批次一致",
            local_run_id == cloud_run_id,
            f"local={local_run_id}, cloud={cloud_run_id}",
            "equal",
        )
    )
    cloud_counts = live_manifest.get("table_counts") or restored_metadata["table_counts"]
    differences = count_differences(
        local_metadata["table_counts"], cloud_counts
    )
    checks.append(
        audit_check(
            "table_counts",
            "資料表筆數一致",
            not differences,
            json.dumps(differences, ensure_ascii=False, separators=(",", ":"))
            if differences
            else "all durable tables matched",
            "no differences",
        )
    )
    checks.append(
        audit_check(
            "remote_audit_ledger",
            "PostgreSQL 驗收帳本",
            True,
            "audit row persisted",
            "write succeeds",
        )
    )
    report = build_audit_report(
        checks=checks,
        live_manifest=live_manifest,
        daily_snapshots=len(daily),
        verified_pushes=len(verified_push_events),
        workflow_count=len(workflows),
    )
    try:
        client.upsert(
            "scanner_evidence_cutover_audits",
            {
                "audited_at": report["auditedAt"],
                "audit_version": report["auditVersion"],
                "migration_mode": report["migrationMode"],
                "status": report["status"],
                "ready": report["ready"],
                "latest_scan_run_id": report["live"]["latestScanRunId"],
                "latest_trade_date": report["live"]["latestTradeDate"],
                "daily_snapshot_count": report["dailySnapshots"],
                "verified_push_count": report["verifiedPushes"],
                "workflow_count": report["workflowCount"],
                "passed_checks": report["passedChecks"],
                "total_checks": report["totalChecks"],
                "checks": report["checks"],
                "report": report,
            },
            conflict_column="audited_at",
        )
    except EvidenceError as error:
        checks[-1] = audit_check(
            "remote_audit_ledger",
            "PostgreSQL 驗收帳本",
            False,
            error.code,
            "write succeeds",
        )
        report = build_audit_report(
            checks=checks,
            live_manifest=live_manifest,
            daily_snapshots=len(daily),
            verified_pushes=len(verified_push_events),
            workflow_count=len(workflows),
            error_code=error.code,
        )
    record_local_audit(database_path, report)
    return report


def failed_audit(database_path, error):
    report = build_audit_report(
        checks=[
            audit_check(
                "cloud_connection",
                "Supabase 連線與 schema",
                False,
                error.code,
                "reachable and initialized",
            )
        ],
        error_code=error.code,
    )
    record_local_audit(database_path, report)
    return report


def prune_daily_snapshots(*, retention_days=DEFAULT_RETENTION_DAYS, apply=False):
    if retention_days < 7:
        raise EvidenceError(
            "unsafe_retention", "Cloud evidence retention must be at least 7 days."
        )
    config = SupabaseConfig.from_environment()
    if config is None:
        raise EvidenceError(
            "not_configured", "Supabase cloud evidence credentials are not configured."
        )
    client = SupabaseEvidenceClient(config)
    cutoff = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(
        days=retention_days
    )
    candidates = []
    for snapshot in client.list_snapshots(limit=500):
        snapshot_key = str(snapshot.get("snapshot_key", ""))
        if not snapshot_key.startswith("daily:"):
            continue
        try:
            trade_date = dt.date.fromisoformat(snapshot_key.split(":", 1)[1])
        except ValueError:
            continue
        if trade_date < cutoff:
            candidates.append(snapshot)
    if apply and candidates:
        client.delete_objects([row["object_path"] for row in candidates])
        for row in candidates:
            client.delete_snapshot(row["snapshot_key"])
    return {
        "evaluatedAt": utc_now(),
        "retentionDays": retention_days,
        "cutoffDate": cutoff.isoformat(),
        "apply": bool(apply),
        "candidateCount": len(candidates),
        "deletedCount": len(candidates) if apply else 0,
        "snapshotKeys": [row["snapshot_key"] for row in candidates],
    }


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

    audit_parser = subparsers.add_parser("audit")
    audit_parser.add_argument("--database", default=str(DB_PATH))
    audit_parser.add_argument("--output")
    audit_parser.add_argument("--github-output")
    audit_parser.add_argument(
        "--minimum-daily-snapshots",
        type=int,
        default=DEFAULT_MINIMUM_DAILY_SNAPSHOTS,
    )
    audit_parser.add_argument(
        "--minimum-verified-pushes",
        type=int,
        default=DEFAULT_MINIMUM_VERIFIED_PUSHES,
    )
    audit_parser.add_argument(
        "--minimum-workflows", type=int, default=DEFAULT_MINIMUM_WORKFLOWS
    )
    audit_parser.add_argument(
        "--max-live-age-hours", type=float, default=DEFAULT_MAX_LIVE_AGE_HOURS
    )
    audit_parser.add_argument("--require-ready", action="store_true")

    prune_parser = subparsers.add_parser("prune")
    prune_parser.add_argument(
        "--retention-days", type=int, default=DEFAULT_RETENTION_DAYS
    )
    prune_parser.add_argument("--apply", action="store_true")
    prune_parser.add_argument("--required", action="store_true")
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
        elif args.command == "restore":
            result = restore(args.database, if_newer=args.if_newer)
            if result["restored"]:
                print(
                    "Cloud evidence restored: "
                    f"run={result.get('latest_scan_run_id')} "
                    f"sha256={result['database_sha256'][:12]}"
                )
            else:
                print("Cloud evidence restore skipped: local database is not older.")
        elif args.command == "audit":
            try:
                result = audit_cutover(
                    args.database,
                    minimum_daily_snapshots=args.minimum_daily_snapshots,
                    minimum_verified_pushes=args.minimum_verified_pushes,
                    minimum_workflows=args.minimum_workflows,
                    max_live_age_hours=args.max_live_age_hours,
                )
            except EvidenceError as error:
                result = failed_audit(args.database, error)
            if args.output:
                write_json(args.output, result)
            if args.github_output:
                with Path(args.github_output).open("a", encoding="utf-8") as handle:
                    handle.write(f"ready={str(result['ready']).lower()}\n")
                    handle.write(f"status={result['status']}\n")
                    handle.write(f"passed_checks={result['passedChecks']}\n")
                    handle.write(f"total_checks={result['totalChecks']}\n")
            print(
                "Cloud cutover audit: "
                f"status={result['status']} "
                f"checks={result['passedChecks']}/{result['totalChecks']} "
                f"daily_snapshots={result['dailySnapshots']}"
            )
            if args.require_ready and not result["ready"]:
                return 1
        else:
            result = prune_daily_snapshots(
                retention_days=args.retention_days, apply=args.apply
            )
            print(
                "Cloud evidence retention: "
                f"candidates={result['candidateCount']} "
                f"deleted={result['deletedCount']} "
                f"apply={str(result['apply']).lower()}"
            )
    except EvidenceError as error:
        required = getattr(args, "required", False) or cloud_is_required()
        handle_failure(
            getattr(args, "database", DB_PATH),
            args.command,
            error,
            required=required,
        )
        return 1 if required else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
