import argparse
import json
import sqlite3
from pathlib import Path

from database import DB_PATH, get_connection, init_db


def _columns(conn, table_name):
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})")]


def _insert_row(conn, table_name, row, overrides=None, columns=None):
    data = dict(row)
    data.pop("id", None)
    data.update(overrides or {})
    insert_columns = [
        column for column in (columns or _columns(conn, table_name)) if column in data
    ]
    placeholders = ", ".join("?" for _ in insert_columns)
    cursor = conn.execute(
        f"INSERT INTO {table_name} ({', '.join(insert_columns)}) "
        f"VALUES ({placeholders})",
        [data[column] for column in insert_columns],
    )
    return cursor.lastrowid


def _delete_existing_run(conn, replay_run_id):
    conn.execute(
        "DELETE FROM historical_replay_attributions WHERE replay_run_id=?",
        (replay_run_id,),
    )
    conn.execute(
        "DELETE FROM historical_replay_checkpoints WHERE replay_run_id=?",
        (replay_run_id,),
    )
    conn.execute(
        "DELETE FROM historical_replay_outcomes WHERE replay_event_id IN "
        "(SELECT id FROM historical_replay_events WHERE replay_run_id=?)",
        (replay_run_id,),
    )
    conn.execute(
        "DELETE FROM historical_replay_events WHERE replay_run_id=?",
        (replay_run_id,),
    )
    conn.execute("DELETE FROM historical_replay_runs WHERE id=?", (replay_run_id,))


def merge_historical_replay(
    source_path,
    target_path=DB_PATH,
    replay_key=None,
    start_date=None,
    end_date=None,
):
    source_path = Path(source_path)
    if not source_path.exists():
        raise FileNotFoundError(f"Replay source database not found: {source_path}")
    source = sqlite3.connect(source_path)
    source.row_factory = sqlite3.Row
    try:
        query = "SELECT * FROM historical_replay_runs"
        filters = []
        parameters = []
        if replay_key:
            filters.append("replay_key=?")
            parameters.append(replay_key)
        if start_date:
            filters.append("start_date=?")
            parameters.append(start_date)
        if end_date:
            filters.append("end_date=?")
            parameters.append(end_date)
        if filters:
            query += " WHERE " + " AND ".join(filters)
        query += " ORDER BY started_at DESC, id DESC LIMIT 1"
        replay_run = source.execute(query, parameters).fetchone()
        if replay_run is None:
            raise ValueError("Source database contains no historical replay run.")
        source_run_id = replay_run["id"]
        source_events = source.execute(
            "SELECT * FROM historical_replay_events "
            "WHERE replay_run_id=? ORDER BY id",
            (source_run_id,),
        ).fetchall()
        outcomes = source.execute(
            "SELECT hro.* FROM historical_replay_outcomes hro "
            "JOIN historical_replay_events hre ON hre.id=hro.replay_event_id "
            "WHERE hre.replay_run_id=? ORDER BY hro.id",
            (source_run_id,),
        ).fetchall()
        checkpoints = source.execute(
            "SELECT * FROM historical_replay_checkpoints "
            "WHERE replay_run_id=? ORDER BY id",
            (source_run_id,),
        ).fetchall()
        attributions = source.execute(
            "SELECT * FROM historical_replay_attributions "
            "WHERE replay_run_id=? ORDER BY id",
            (source_run_id,),
        ).fetchall()

        with get_connection(target_path) as target:
            init_db(target)
            column_cache = {
                table_name: _columns(target, table_name)
                for table_name in (
                    "historical_replay_runs",
                    "historical_replay_events",
                    "historical_replay_outcomes",
                    "historical_replay_checkpoints",
                    "historical_replay_attributions",
                )
            }
            existing = target.execute(
                "SELECT id FROM historical_replay_runs WHERE replay_key=?",
                (replay_run["replay_key"],),
            ).fetchone()
            if existing:
                _delete_existing_run(target, existing["id"])
            target_run_id = _insert_row(
                target,
                "historical_replay_runs",
                replay_run,
                columns=column_cache["historical_replay_runs"],
            )
            event_id_map = {}
            for event in source_events:
                event_id_map[event["id"]] = _insert_row(
                    target,
                    "historical_replay_events",
                    event,
                    {"replay_run_id": target_run_id},
                    column_cache["historical_replay_events"],
                )
            for outcome in outcomes:
                _insert_row(
                    target,
                    "historical_replay_outcomes",
                    outcome,
                    {"replay_event_id": event_id_map[outcome["replay_event_id"]]},
                    column_cache["historical_replay_outcomes"],
                )
            for checkpoint in checkpoints:
                _insert_row(
                    target,
                    "historical_replay_checkpoints",
                    checkpoint,
                    {"replay_run_id": target_run_id},
                    column_cache["historical_replay_checkpoints"],
                )
            for attribution in attributions:
                _insert_row(
                    target,
                    "historical_replay_attributions",
                    attribution,
                    {"replay_run_id": target_run_id},
                    column_cache["historical_replay_attributions"],
                )
        return {
            "replay_key": replay_run["replay_key"],
            "status": replay_run["status"],
            "source_replay_run_id": source_run_id,
            "target_replay_run_id": target_run_id,
            "events": len(source_events),
            "outcomes": len(outcomes),
            "checkpoints": len(checkpoints),
            "attributions": len(attributions),
        }
    finally:
        source.close()


def main():
    parser = argparse.ArgumentParser(
        description="Merge isolated historical replay evidence into the live research DB."
    )
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", default=str(DB_PATH))
    parser.add_argument("--replay-key")
    parser.add_argument("--start")
    parser.add_argument("--end")
    args = parser.parse_args()
    result = merge_historical_replay(
        args.source,
        target_path=args.target,
        replay_key=args.replay_key,
        start_date=args.start,
        end_date=args.end,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
