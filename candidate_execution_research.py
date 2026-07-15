import argparse
import json

import pandas as pd

from backtest import PriceCache, download_price_data, resolve_yf_ticker
from database import DB_PATH, get_connection, get_taipei_now, init_db
from execution_research import (
    ENTRY_METHODS,
    EXECUTION_RESEARCH_VERSION,
    calculate_execution_scenarios,
)
from historical_replay import download_replay_history


def load_pending_eod_candidates(db_path=DB_PATH, limit=1000):
    with get_connection(db_path) as conn:
        init_db(conn)
        return conn.execute(
            """
            WITH canonical AS (
                SELECT ce.*, sr.trade_date, sr.mode,
                       ROW_NUMBER() OVER (
                           PARTITION BY sr.trade_date, ce.code
                           ORDER BY ce.as_of ASC, ce.id ASC
                       ) AS day_code_rank
                FROM candidate_events ce
                JOIN scan_runs sr ON sr.id=ce.run_id
                WHERE sr.mode='eod'
            ), scenario_status AS (
                SELECT candidate_id, COUNT(*) AS scenarios,
                       SUM(CASE WHEN outcome_status IN ('complete', 'skipped')
                                THEN 1 ELSE 0 END) AS finalized
                FROM candidate_execution_scenarios
                WHERE scenario_version=?
                GROUP BY candidate_id
            )
            SELECT canonical.*
            FROM canonical
            LEFT JOIN scenario_status ss ON ss.candidate_id=canonical.id
            WHERE canonical.day_code_rank=1
              AND (COALESCE(ss.scenarios, 0) < ?
                   OR COALESCE(ss.finalized, 0) < ?)
            ORDER BY canonical.trade_date ASC, canonical.id ASC
            LIMIT ?
            """,
            (
                EXECUTION_RESEARCH_VERSION,
                len(ENTRY_METHODS),
                len(ENTRY_METHODS),
                int(limit),
            ),
        ).fetchall()


def save_candidate_scenarios(conn, candidate_id, scenarios, price_data_end):
    now = get_taipei_now().isoformat(timespec="seconds")
    for scenario in scenarios:
        conn.execute(
            """
            INSERT INTO candidate_execution_scenarios (
                candidate_id, scenario_version, entry_method, entry_status,
                skip_reason, entry_at, entry_price, benchmark_entry_price,
                matured_horizon, outcome_status, costs_bps, labels_json,
                price_data_end, evaluated_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id, scenario_version, entry_method) DO UPDATE SET
                entry_status=excluded.entry_status,
                skip_reason=excluded.skip_reason,
                entry_at=excluded.entry_at,
                entry_price=excluded.entry_price,
                benchmark_entry_price=excluded.benchmark_entry_price,
                matured_horizon=excluded.matured_horizon,
                outcome_status=excluded.outcome_status,
                costs_bps=excluded.costs_bps,
                labels_json=excluded.labels_json,
                price_data_end=excluded.price_data_end,
                evaluated_at=excluded.evaluated_at,
                updated_at=excluded.updated_at
            """,
            (
                int(candidate_id),
                scenario["scenario_version"],
                scenario["entry_method"],
                scenario["entry_status"],
                scenario.get("skip_reason"),
                scenario.get("entry_at"),
                scenario.get("entry_price"),
                scenario.get("benchmark_entry_price"),
                scenario.get("matured_horizon", 0),
                scenario.get("outcome_status", "pending"),
                scenario.get("costs_bps"),
                json.dumps(scenario.get("labels", {}), sort_keys=True),
                price_data_end,
                now,
                now,
            ),
        )


def run_candidate_execution_research(
    db_path=DB_PATH,
    limit=1000,
    price_loader=download_price_data,
):
    candidates = load_pending_eod_candidates(db_path=db_path, limit=limit)
    if not candidates:
        return {
            "candidates": 0,
            "scenarios": 0,
            "complete": 0,
            "partial": 0,
            "pending": 0,
            "skipped": 0,
        }
    start = min(pd.Timestamp(row["trade_date"]) for row in candidates) - pd.Timedelta(
        days=7
    )
    end = pd.Timestamp.now().normalize() + pd.Timedelta(days=1)
    if price_loader is download_price_data:
        ticker_by_code = {
            str(row["code"]): resolve_yf_ticker(row["code"]) for row in candidates
        }
        histories = download_replay_history(
            sorted(set(ticker_by_code.values()) | {"^TWII"}),
            start,
            end,
            chunk_size=100,
            cache_dir=None,
        )
        benchmark = histories.get("^TWII")

        def stock_prices(code):
            return histories.get(ticker_by_code.get(str(code)))

    else:
        cache = PriceCache(start=start, end=end, loader=price_loader)
        benchmark = cache.get_ticker("^TWII")

        def stock_prices(code):
            return cache.get_stock(code)

    metrics = {
        "candidates": 0,
        "scenarios": 0,
        "complete": 0,
        "partial": 0,
        "pending": 0,
        "skipped": 0,
    }
    with get_connection(db_path) as conn:
        init_db(conn)
        for candidate_row in candidates:
            candidate = dict(candidate_row)
            prices = stock_prices(candidate["code"])
            scenarios = calculate_execution_scenarios(candidate, prices, benchmark)
            if not scenarios:
                continue
            price_data_end = (
                pd.Timestamp(prices.index[-1]).date().isoformat()
                if prices is not None and not prices.empty
                else None
            )
            save_candidate_scenarios(
                conn, candidate["id"], scenarios, price_data_end
            )
            metrics["candidates"] += 1
            metrics["scenarios"] += len(scenarios)
            for scenario in scenarios:
                metrics[scenario["outcome_status"]] += 1
    return metrics


def main():
    parser = argparse.ArgumentParser(
        description="Mature prospective EOD candidates under research executions."
    )
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    print(
        json.dumps(
            run_candidate_execution_research(db_path=args.db, limit=args.limit),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
