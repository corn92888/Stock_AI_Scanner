import argparse
import json
from dataclasses import asdict, dataclass

import pandas as pd

from backtest import (
    BacktestConfig,
    PriceCache,
    _net_return,
    _normalize_price_frame,
    _pct_return,
    _row_for_date,
    download_price_data,
)
from database import (
    CANDIDATE_EXECUTION_VERSION,
    DB_PATH,
    finish_backtest_run,
    get_connection,
    init_db,
    save_candidate_outcome,
    start_backtest_run,
)


@dataclass(frozen=True)
class CandidateExecutionConfig(BacktestConfig):
    execution_version: str = CANDIDATE_EXECUTION_VERSION
    decision_horizon: int = 3
    completion_horizon: int = 5
    defense_rule: str = "close_below_observation"


def load_pending_candidates(
    db_path=DB_PATH,
    execution_version=CANDIDATE_EXECUTION_VERSION,
    limit=None,
    refresh=False,
):
    where = []
    params = [execution_version]
    if not refresh:
        where.append("(co.id IS NULL OR COALESCE(co.outcome_status, 'pending') <> 'complete')")
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    sql = f"""
        SELECT ce.*, sr.trade_date, sr.mode, sr.run_at
        FROM candidate_events ce
        JOIN scan_runs sr ON sr.id=ce.run_id
        LEFT JOIN candidate_outcomes co
          ON co.candidate_id=ce.id AND co.execution_version=?
        {where_sql}
        ORDER BY ce.is_selected DESC, ce.is_first_eligible_event DESC,
                 sr.trade_date ASC, sr.run_at ASC, ce.raw_rank ASC, ce.id ASC
    """
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    with get_connection(db_path) as conn:
        init_db(conn)
        return conn.execute(sql, params).fetchall()


def _candidate_fixed_returns(future, entry_price, config):
    results = {}
    for horizon in (1, 3, 5):
        value = None
        if len(future) >= horizon:
            value = _net_return(float(future.iloc[horizon - 1]["Close"]), entry_price, config)
        results[f"fixed_net_return_{horizon}d"] = value
    return results


def calculate_candidate_result(candidate, price_df, benchmark_df=None, config=None):
    config = config or CandidateExecutionConfig()
    price_df = _normalize_price_frame(price_df)
    if price_df is None or price_df.empty:
        return None
    if benchmark_df is not None:
        benchmark_df = _normalize_price_frame(benchmark_df)

    trade_date = pd.Timestamp(candidate["trade_date"]).normalize()
    future = price_df[price_df.index.normalize() > trade_date]
    if future.empty:
        return None

    entry_row = future.iloc[0]
    entry_at = future.index[0]
    entry_price = float(entry_row["Open"])
    entry_factor = float(entry_row.get("AdjustmentFactor", 1.0))
    matured = [horizon for horizon in (1, 3, 5) if len(future) >= horizon]
    result = {
        "entry_at": entry_at.strftime("%Y-%m-%d"),
        "entry_price": round(entry_price, 4),
        "entry_adjustment_factor": round(entry_factor, 8),
        "entry_method": "next_day_open",
        "exit_at": None,
        "exit_price": None,
        "exit_reason": None,
        "net_return_3d": None,
        "benchmark_code": config.benchmark_code,
        "benchmark_entry_price": None,
        "benchmark_return_3d": None,
        "excess_return_3d": None,
        "max_return_3d": None,
        "max_drawdown_3d": None,
        "defense_triggered": False,
        "success_t3": None,
        "matured_horizon": max(matured, default=0),
        "outcome_status": "complete" if len(future) >= config.completion_horizon else "partial",
        "price_data_end": price_df.index[-1].strftime("%Y-%m-%d"),
        "costs_bps": config.costs_bps,
        "config_json": json.dumps(asdict(config), ensure_ascii=True, sort_keys=True),
        **_candidate_fixed_returns(future, entry_price, config),
    }

    benchmark_entry = _row_for_date(benchmark_df, entry_at)
    if benchmark_entry is not None:
        result["benchmark_entry_price"] = round(float(benchmark_entry["Open"]), 4)

    if len(future) < config.decision_horizon:
        return result

    decision_window = future.head(config.decision_horizon)
    defense_price = candidate["observation_price"]
    if defense_price is not None:
        defense_price = float(defense_price) * entry_factor
        defense_hits = decision_window[decision_window["Close"] < defense_price]
    else:
        defense_hits = decision_window.iloc[0:0]

    if not defense_hits.empty:
        exit_at = defense_hits.index[0]
        exit_row = defense_hits.iloc[0]
        exit_reason = "defense_close"
        result["defense_triggered"] = True
    else:
        exit_at = decision_window.index[-1]
        exit_row = decision_window.iloc[-1]
        exit_reason = "time_exit_t3"

    exit_price = float(exit_row["Close"])
    result["exit_at"] = exit_at.strftime("%Y-%m-%d")
    result["exit_price"] = round(exit_price, 4)
    result["exit_reason"] = exit_reason
    result["net_return_3d"] = _net_return(exit_price, entry_price, config)

    experienced = decision_window.loc[:exit_at]
    result["max_return_3d"] = _pct_return(float(experienced["High"].max()), entry_price)
    result["max_drawdown_3d"] = _pct_return(float(experienced["Low"].min()), entry_price)

    benchmark_entry_price = result["benchmark_entry_price"]
    benchmark_exit = _row_for_date(benchmark_df, exit_at)
    if benchmark_entry_price is not None and benchmark_exit is not None:
        benchmark_return = _pct_return(float(benchmark_exit["Close"]), benchmark_entry_price)
        result["benchmark_return_3d"] = benchmark_return
        result["excess_return_3d"] = round(result["net_return_3d"] - benchmark_return, 4)
        result["success_t3"] = (
            result["excess_return_3d"] >= config.success_excess_return_3d
            and result["max_drawdown_3d"] >= config.success_max_drawdown_3d
        )
    return result


def run_candidate_backtest(
    db_path=DB_PATH,
    config=None,
    limit=None,
    refresh=False,
    price_loader=download_price_data,
):
    config = config or CandidateExecutionConfig()
    candidates = load_pending_candidates(
        db_path=db_path,
        execution_version=config.execution_version,
        limit=limit,
        refresh=refresh,
    )
    if not candidates:
        print("No pending candidate outcomes.")
        return {"saved": 0, "complete": 0, "partial": 0, "skipped": 0}

    start = min(pd.Timestamp(row["trade_date"]) for row in candidates) - pd.Timedelta(days=5)
    end = pd.Timestamp.now().normalize() + pd.Timedelta(days=1)
    cache = PriceCache(start=start, end=end, loader=price_loader)
    benchmark_df = cache.get_ticker(config.benchmark_code)
    audit = asdict(config)
    audit["selection_scope"] = "candidate_all"
    audit_run_id = start_backtest_run(
        json.dumps(audit, ensure_ascii=True, sort_keys=True),
        len(candidates),
        db_path=db_path,
    )

    saved = complete = partial = skipped = 0
    try:
        for candidate in candidates:
            print(
                f"Candidate backtest {candidate['trade_date']} {candidate['code']} "
                f"{candidate['name']} selected={bool(candidate['is_selected'])}"
            )
            result = calculate_candidate_result(
                candidate,
                price_df=cache.get_stock(candidate["code"]),
                benchmark_df=benchmark_df,
                config=config,
            )
            if result is None:
                skipped += 1
                continue
            save_candidate_outcome(
                candidate["id"],
                config.execution_version,
                result,
                db_path=db_path,
            )
            saved += 1
            if result["outcome_status"] == "complete":
                complete += 1
            else:
                partial += 1
        finish_backtest_run(
            audit_run_id,
            status="completed",
            completed_count=complete,
            partial_count=partial,
            skipped_count=skipped,
            db_path=db_path,
        )
    except Exception as exc:
        finish_backtest_run(
            audit_run_id,
            status="failed",
            completed_count=complete,
            partial_count=partial,
            skipped_count=skipped,
            error_text=str(exc),
            db_path=db_path,
        )
        raise

    print(
        f"Candidate outcomes saved: {saved}; complete: {complete}; "
        f"partial: {partial}; skipped: {skipped}"
    )
    return {"saved": saved, "complete": complete, "partial": partial, "skipped": skipped}


def main():
    parser = argparse.ArgumentParser(description="Backtest every ranked candidate with a versioned execution policy.")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    run_candidate_backtest(db_path=args.db, limit=args.limit, refresh=args.refresh)


if __name__ == "__main__":
    main()
