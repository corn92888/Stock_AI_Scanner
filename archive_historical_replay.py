import argparse
import hashlib
import io
import json
import sqlite3
import tarfile
from datetime import datetime, timezone
from pathlib import Path


ARCHIVE_SCHEMA_VERSION = "historical_replay_archive_v1"


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _selected_replay(conn, start_date=None, end_date=None):
    filters = []
    parameters = []
    if start_date:
        filters.append("start_date=?")
        parameters.append(start_date)
    if end_date:
        filters.append("end_date=?")
        parameters.append(end_date)
    query = "SELECT * FROM historical_replay_runs"
    if filters:
        query += " WHERE " + " AND ".join(filters)
    query += " ORDER BY COALESCE(finished_at, started_at) DESC, id DESC LIMIT 1"
    row = conn.execute(query, parameters).fetchone()
    if row is None:
        raise ValueError("Replay database contains no matching replay run.")
    return row


def build_replay_archive(
    database_path,
    output_path,
    start_date=None,
    end_date=None,
    universe_files=(),
    extra_files=(),
):
    database_path = Path(database_path).resolve()
    output_path = Path(output_path).resolve()
    if not database_path.exists():
        raise FileNotFoundError(f"Replay database not found: {database_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        replay = _selected_replay(conn, start_date=start_date, end_date=end_date)
        replay_run_id = int(replay["id"])
        counts = {
            "events": int(
                conn.execute(
                    "SELECT COUNT(*) FROM historical_replay_events "
                    "WHERE replay_run_id=?",
                    (replay_run_id,),
                ).fetchone()[0]
            ),
            "outcomes": int(
                conn.execute(
                    "SELECT COUNT(*) FROM historical_replay_outcomes hro "
                    "JOIN historical_replay_events hre "
                    "ON hre.id=hro.replay_event_id WHERE hre.replay_run_id=?",
                    (replay_run_id,),
                ).fetchone()[0]
            ),
            "checkpoints": int(
                conn.execute(
                    "SELECT COUNT(*) FROM historical_replay_checkpoints "
                    "WHERE replay_run_id=?",
                    (replay_run_id,),
                ).fetchone()[0]
            ),
            "attributions": int(
                conn.execute(
                    "SELECT COUNT(*) FROM historical_replay_attributions "
                    "WHERE replay_run_id=?",
                    (replay_run_id,),
                ).fetchone()[0]
            ),
        }
        manifest = {
            "schema_version": ARCHIVE_SCHEMA_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "database_file": "historical_replay.db",
            "database_bytes": database_path.stat().st_size,
            "database_sha256": _sha256(database_path),
            "replay": {
                "replay_key": replay["replay_key"],
                "replay_version": replay["replay_version"],
                "status": replay["status"],
                "start_date": replay["start_date"],
                "end_date": replay["end_date"],
                "universe_source": replay["universe_source"],
                "universe_quality_status": replay["universe_quality_status"],
                "git_commit": replay["git_commit"],
                **counts,
            },
        }
    finally:
        conn.close()

    included_universe = []
    included_files = []
    with tarfile.open(output_path, "w:gz", compresslevel=9) as archive:
        archive.add(database_path, arcname="historical_replay.db")
        for file_path in universe_files:
            file_path = Path(file_path).resolve()
            if not file_path.exists() or file_path == database_path:
                continue
            archive.add(file_path, arcname=file_path.name)
            included_universe.append(file_path.name)
        for file_path in extra_files:
            file_path = Path(file_path).resolve()
            if not file_path.exists() or file_path == database_path:
                continue
            archive.add(file_path, arcname=file_path.name)
            included_files.append(file_path.name)
        manifest["universe_files"] = sorted(included_universe)
        manifest["included_files"] = sorted(included_files)
        payload = json.dumps(
            manifest, ensure_ascii=False, indent=2, sort_keys=True
        ).encode("utf-8")
        info = tarfile.TarInfo("manifest.json")
        info.size = len(payload)
        info.mtime = int(datetime.now(timezone.utc).timestamp())
        archive.addfile(info, io.BytesIO(payload))

    result = {
        **manifest,
        "archive": str(output_path),
        "archive_bytes": output_path.stat().st_size,
        "archive_sha256": _sha256(output_path),
    }
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Package raw point-in-time replay evidence for durable storage."
    )
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--universe", action="append", default=[])
    parser.add_argument("--include", action="append", default=[])
    args = parser.parse_args()
    result = build_replay_archive(
        args.database,
        args.output,
        start_date=args.start,
        end_date=args.end,
        universe_files=args.universe,
        extra_files=args.include,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
