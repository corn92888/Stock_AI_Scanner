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


def _upsert_row(conn, table_name, row, conflict_columns, overrides=None, columns=None):
    data = dict(row)
    data.pop("id", None)
    data.update(overrides or {})
    insert_columns = [
        column for column in (columns or _columns(conn, table_name)) if column in data
    ]
    update_columns = [
        column for column in insert_columns if column not in set(conflict_columns)
    ]
    placeholders = ", ".join("?" for _ in insert_columns)
    updates = ", ".join(f"{column}=excluded.{column}" for column in update_columns)
    conn.execute(
        f"INSERT INTO {table_name} ({', '.join(insert_columns)}) "
        f"VALUES ({placeholders}) ON CONFLICT ({', '.join(conflict_columns)}) "
        f"DO UPDATE SET {updates}",
        [data[column] for column in insert_columns],
    )


def _delete_existing_run(conn, replay_run_id):
    conn.execute(
        "DELETE FROM historical_replay_summaries WHERE replay_run_id=?",
        (replay_run_id,),
    )
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


def _build_replay_summary(conn, replay_run_id, generated_at):
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS filled_events,
            SUM(CASE WHEN hre.is_selected=1 THEN 1 ELSE 0 END) AS selected_filled,
            SUM(CASE WHEN hre.is_selected=0 THEN 1 ELSE 0 END) AS rejected_filled,
            AVG(CASE WHEN hre.is_selected=1 THEN hro.net_return_3d END)
                AS selected_net,
            AVG(CASE WHEN hre.is_selected=1 THEN hro.excess_return_3d END)
                AS selected_excess,
            AVG(CASE WHEN hre.is_selected=1 THEN hro.success_t3 END) * 100
                AS selected_success,
            AVG(CASE WHEN hre.is_selected=0 THEN hro.net_return_3d END)
                AS rejected_net,
            AVG(CASE WHEN hre.is_selected=0 THEN hro.excess_return_3d END)
                AS rejected_excess,
            AVG(CASE WHEN hre.is_selected=0 THEN hro.success_t3 END) * 100
                AS rejected_success,
            AVG(CASE WHEN hre.is_selected=1 THEN hro.fixed_net_return_1d END)
                AS selected_fixed_1d,
            AVG(CASE WHEN hre.is_selected=1 THEN hro.fixed_net_return_5d END)
                AS selected_fixed_5d,
            AVG(CASE WHEN hre.is_selected=0 THEN hro.fixed_net_return_1d END)
                AS rejected_fixed_1d,
            AVG(CASE WHEN hre.is_selected=0 THEN hro.fixed_net_return_5d END)
                AS rejected_fixed_5d,
            AVG(CASE WHEN hre.is_selected=1 THEN hro.max_drawdown_3d END)
                AS selected_drawdown,
            AVG(CASE WHEN hre.is_selected=0 THEN hro.max_drawdown_3d END)
                AS rejected_drawdown
        FROM historical_replay_outcomes hro
        JOIN historical_replay_events hre ON hre.id=hro.replay_event_id
        WHERE hre.replay_run_id=?
          AND hro.entry_status='filled'
          AND hro.matured_horizon >= 3
        """,
        (replay_run_id,),
    ).fetchone()
    selected_net = row["selected_net"]
    rejected_net = row["rejected_net"]
    selected_excess = row["selected_excess"]
    rejected_excess = row["rejected_excess"]
    metrics = {
        "selected_mean_fixed_net_return_1d": row["selected_fixed_1d"],
        "selected_mean_fixed_net_return_5d": row["selected_fixed_5d"],
        "rejected_mean_fixed_net_return_1d": row["rejected_fixed_1d"],
        "rejected_mean_fixed_net_return_5d": row["rejected_fixed_5d"],
        "selected_mean_max_drawdown_3d": row["selected_drawdown"],
        "rejected_mean_max_drawdown_3d": row["rejected_drawdown"],
    }
    return {
        "generated_at": generated_at,
        "filled_events": int(row["filled_events"] or 0),
        "selected_filled": int(row["selected_filled"] or 0),
        "rejected_filled": int(row["rejected_filled"] or 0),
        "selected_mean_net_return_3d": selected_net,
        "selected_mean_excess_return_3d": selected_excess,
        "selected_success_rate_t3": row["selected_success"],
        "rejected_mean_net_return_3d": rejected_net,
        "rejected_mean_excess_return_3d": rejected_excess,
        "rejected_success_rate_t3": row["rejected_success"],
        "selection_net_lift_3d": (
            selected_net - rejected_net
            if selected_net is not None and rejected_net is not None
            else None
        ),
        "selection_excess_lift_3d": (
            selected_excess - rejected_excess
            if selected_excess is not None and rejected_excess is not None
            else None
        ),
        "metrics_json": json.dumps(metrics, sort_keys=True),
    }


def merge_historical_replay(
    source_path,
    target_path=DB_PATH,
    replay_key=None,
    start_date=None,
    end_date=None,
    include_raw=False,
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
        event_count = int(
            source.execute(
                "SELECT COUNT(*) FROM historical_replay_events WHERE replay_run_id=?",
                (source_run_id,),
            ).fetchone()[0]
        )
        outcome_count = int(
            source.execute(
                "SELECT COUNT(*) FROM historical_replay_outcomes hro "
                "JOIN historical_replay_events hre ON hre.id=hro.replay_event_id "
                "WHERE hre.replay_run_id=?",
                (source_run_id,),
            ).fetchone()[0]
        )
        source_events = (
            source.execute(
                "SELECT * FROM historical_replay_events "
                "WHERE replay_run_id=? ORDER BY id",
                (source_run_id,),
            ).fetchall()
            if include_raw
            else []
        )
        outcomes = (
            source.execute(
                "SELECT hro.* FROM historical_replay_outcomes hro "
                "JOIN historical_replay_events hre ON hre.id=hro.replay_event_id "
                "WHERE hre.replay_run_id=? ORDER BY hro.id",
                (source_run_id,),
            ).fetchall()
            if include_raw
            else []
        )
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
        attribution_at = max(
            (row["generated_at"] for row in attributions),
            default=replay_run["finished_at"] or replay_run["started_at"],
        )
        summary = _build_replay_summary(source, source_run_id, attribution_at)
        replay_model = source.execute(
            """
            SELECT * FROM model_versions
            WHERE metrics_json LIKE '%point_in_time_replay%'
            ORDER BY created_at DESC, id DESC LIMIT 1
            """
        ).fetchone()
        challenger = (
            source.execute(
                "SELECT * FROM model_challenger_evaluations WHERE model_version=?",
                (replay_model["version"],),
            ).fetchone()
            if replay_model
            else None
        )

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
                    "historical_replay_summaries",
                    "model_versions",
                    "model_challenger_evaluations",
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
            _insert_row(
                target,
                "historical_replay_summaries",
                summary,
                {"replay_run_id": target_run_id},
                column_cache["historical_replay_summaries"],
            )
            if replay_model:
                artifact_name = Path(replay_model["artifact_path"] or "").name
                _upsert_row(
                    target,
                    "model_versions",
                    replay_model,
                    ("model_name", "version"),
                    {
                        "artifact_path": (
                            f"data/models/{artifact_name}" if artifact_name else None
                        )
                    },
                    column_cache["model_versions"],
                )
            if challenger:
                _upsert_row(
                    target,
                    "model_challenger_evaluations",
                    challenger,
                    ("model_version",),
                    columns=column_cache["model_challenger_evaluations"],
                )
        return {
            "replay_key": replay_run["replay_key"],
            "status": replay_run["status"],
            "source_replay_run_id": source_run_id,
            "target_replay_run_id": target_run_id,
            "events": event_count,
            "outcomes": outcome_count,
            "raw_events_persisted": len(source_events),
            "raw_outcomes_persisted": len(outcomes),
            "checkpoints": len(checkpoints),
            "attributions": len(attributions),
            "summary": summary,
            "model_version": replay_model["version"] if replay_model else None,
            "model_governance": dict(challenger) if challenger else None,
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
    parser.add_argument(
        "--include-raw",
        action="store_true",
        help="Copy event-level rows into the target database instead of summary-only mode.",
    )
    args = parser.parse_args()
    result = merge_historical_replay(
        args.source,
        target_path=args.target,
        replay_key=args.replay_key,
        start_date=args.start,
        end_date=args.end,
        include_raw=args.include_raw,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
