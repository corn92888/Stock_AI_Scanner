import argparse

from database import (
    CANDIDATE_EXECUTION_VERSION,
    DB_PATH,
    get_connection,
    get_taipei_now,
    init_db,
)


def update_prediction_outcomes(db_path=DB_PATH):
    now = get_taipei_now().isoformat(timespec="seconds")
    with get_connection(db_path) as conn:
        init_db(conn)
        candidate_rows = conn.execute(
            """
            WITH canonical_candidate AS (
                SELECT id, run_id, code
                FROM (
                    SELECT ce.id, ce.run_id, ce.code,
                           ROW_NUMBER() OVER (
                               PARTITION BY ce.run_id, ce.code
                               ORDER BY ce.id DESC
                           ) AS candidate_order
                    FROM candidate_events ce
                )
                WHERE candidate_order=1
            )
            SELECT
                p.id AS prediction_id,
                co.entry_at AS entry_date,
                co.entry_price,
                co.entry_method,
                co.fixed_net_return_1d AS net_return_1d,
                co.net_return_3d,
                co.fixed_net_return_5d AS net_return_5d,
                co.benchmark_return_3d,
                co.excess_return_3d,
                co.max_return_3d,
                co.max_drawdown_3d,
                CASE WHEN co.defense_triggered=1 THEN co.exit_at END
                    AS stop_loss_date,
                co.defense_triggered AS stop_loss_hit,
                co.success_t3,
                co.matured_horizon,
                co.outcome_status,
                co.evaluated_at AS tested_at
            FROM predictions p
            JOIN canonical_candidate ce
              ON ce.run_id=p.run_id AND ce.code=p.code
            JOIN candidate_outcomes co
              ON co.candidate_id=ce.id AND co.execution_version=?
            WHERE p.is_prospective=1
            """,
            (CANDIDATE_EXECUTION_VERSION,),
        ).fetchall()
        candidate_prediction_ids = {
            int(row["prediction_id"]) for row in candidate_rows
        }
        legacy_rows = conn.execute(
            """
            SELECT p.id AS prediction_id, br.*
            FROM predictions p
            JOIN backtest_results br ON br.signal_id=p.signal_id
            WHERE p.is_prospective=1
            """
        ).fetchall()
        rows = list(candidate_rows) + [
            row
            for row in legacy_rows
            if int(row["prediction_id"]) not in candidate_prediction_ids
        ]
        conn.execute(
            """
            DELETE FROM prediction_outcomes
            WHERE prediction_id IN (
                SELECT id FROM predictions WHERE is_prospective=0
            )
            """
        )
        for row in rows:
            row = dict(row)
            conn.execute(
                """
                INSERT INTO prediction_outcomes (
                    prediction_id, entry_at, entry_price, entry_method,
                    net_return_1d, net_return_3d, net_return_5d,
                    benchmark_return_3d, excess_return_3d, max_return_3d,
                    max_drawdown_3d, stop_hit_at, first_barrier, success_t3,
                    matured_horizon, outcome_status, evaluated_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(prediction_id) DO UPDATE SET
                    entry_at=excluded.entry_at, entry_price=excluded.entry_price,
                    entry_method=excluded.entry_method,
                    net_return_1d=excluded.net_return_1d,
                    net_return_3d=excluded.net_return_3d,
                    net_return_5d=excluded.net_return_5d,
                    benchmark_return_3d=excluded.benchmark_return_3d,
                    excess_return_3d=excluded.excess_return_3d,
                    max_return_3d=excluded.max_return_3d,
                    max_drawdown_3d=excluded.max_drawdown_3d,
                    stop_hit_at=excluded.stop_hit_at,
                    first_barrier=excluded.first_barrier,
                    success_t3=excluded.success_t3,
                    matured_horizon=excluded.matured_horizon,
                    outcome_status=excluded.outcome_status,
                    evaluated_at=excluded.evaluated_at,
                    updated_at=excluded.updated_at
                """,
                (
                    int(row["prediction_id"]),
                    row.get("entry_date"),
                    row.get("entry_price"),
                    row.get("entry_method"),
                    row.get("net_return_1d"),
                    row.get("net_return_3d"),
                    row.get("net_return_5d"),
                    row.get("benchmark_return_3d"),
                    row.get("excess_return_3d"),
                    row.get("max_return_3d"),
                    row.get("max_drawdown_3d"),
                    row.get("stop_loss_date"),
                    "stop" if row.get("stop_loss_hit") else None,
                    row.get("success_t3"),
                    int(row.get("matured_horizon") or 0),
                    row.get("outcome_status") or "pending",
                    row.get("tested_at"),
                    now,
                ),
            )
        conn.commit()
    return len(rows)


def main():
    parser = argparse.ArgumentParser(
        description="Synchronize mature candidate outcomes to prospective predictions."
    )
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()
    updated = update_prediction_outcomes(args.db)
    print(f"Prediction outcomes synchronized: {updated}")


if __name__ == "__main__":
    main()
