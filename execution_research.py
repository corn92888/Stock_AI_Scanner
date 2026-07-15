import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from backtest import BacktestConfig, _net_return, _normalize_price_frame, _pct_return
from database import get_connection, get_taipei_now, init_db
from historical_replay import (
    _download_benchmark,
    _market_suffix,
    _stock_for_date,
    download_replay_history,
    load_replay_universe,
    resolve_transfer_history_aliases,
)


EXECUTION_RESEARCH_VERSION = "fixed_horizon_execution_scenarios_v1"
HORIZONS = (1, 3, 5, 10, 20)
ENTRY_METHODS = (
    "next_open",
    "next_ohlc4_proxy",
    "next_close",
    "pullback_2pct_3d",
)


@dataclass(frozen=True)
class ExecutionResearchConfig(BacktestConfig):
    version: str = EXECUTION_RESEARCH_VERSION
    pullback_discount: float = 0.02
    pullback_sessions: int = 3


def _ohlc4(row):
    return float(row[["Open", "High", "Low", "Close"]].mean())


def _entry_for_method(future, signal_price, method, config):
    if future.empty:
        return None, None, "no_future_session"
    first = future.iloc[0]
    if method == "next_open":
        return future.index[0], float(first["Open"]), None
    if method == "next_ohlc4_proxy":
        return future.index[0], _ohlc4(first), None
    if method == "next_close":
        return future.index[0], float(first["Close"]), None
    if method == "pullback_2pct_3d":
        if signal_price is None or signal_price <= 0:
            return None, None, "missing_signal_price"
        limit_price = signal_price * (1 - config.pullback_discount)
        for entry_at, row in future.head(config.pullback_sessions).iterrows():
            if float(row["Open"]) <= limit_price:
                return entry_at, float(row["Open"]), None
            if float(row["Low"]) <= limit_price:
                return entry_at, float(limit_price), None
        if len(future) < config.pullback_sessions:
            return None, None, "awaiting_pullback_window"
        return None, None, "pullback_not_filled"
    raise ValueError(f"Unsupported execution research method: {method}")


def _benchmark_entry_price(benchmark_row, method):
    if benchmark_row is None:
        return None
    if method == "next_close":
        return float(benchmark_row["Close"])
    if method == "next_ohlc4_proxy":
        return _ohlc4(benchmark_row)
    return float(benchmark_row["Open"])


def _row_for_normalized_date(frame, value):
    if frame is None or frame.empty:
        return None
    key = pd.Timestamp(value).normalize()
    try:
        row = frame.loc[key]
    except KeyError:
        return None
    return row.iloc[-1] if isinstance(row, pd.DataFrame) else row


def calculate_execution_scenarios(
    candidate,
    price_df,
    benchmark_df=None,
    config=None,
    prices_are_normalized=False,
):
    config = config or ExecutionResearchConfig()
    candidate = dict(candidate)
    prices = price_df if prices_are_normalized else _normalize_price_frame(price_df)
    benchmark = (
        benchmark_df
        if prices_are_normalized
        else _normalize_price_frame(benchmark_df)
    )
    if prices is None or prices.empty:
        return []

    trade_date = pd.Timestamp(candidate["trade_date"]).normalize()
    signal_row = _row_for_normalized_date(prices, trade_date)
    signal_factor = (
        float(signal_row.get("AdjustmentFactor", 1.0))
        if signal_row is not None
        else 1.0
    )
    raw_signal_price = candidate.get("signal_price")
    signal_price = (
        float(raw_signal_price) * signal_factor
        if raw_signal_price is not None and pd.notna(raw_signal_price)
        else None
    )
    future_start = int(prices.index.searchsorted(trade_date, side="right"))
    future = prices.iloc[future_start:]
    scenarios = []
    for method in ENTRY_METHODS:
        entry_at, entry_price, skip_reason = _entry_for_method(
            future, signal_price, method, config
        )
        scenario = {
            "scenario_version": config.version,
            "entry_method": method,
            "entry_status": "skipped" if skip_reason else "filled",
            "skip_reason": skip_reason,
            "entry_at": None,
            "entry_price": None,
            "benchmark_entry_price": None,
            "matured_horizon": 0,
            "outcome_status": "pending" if skip_reason in {
                "no_future_session",
                "awaiting_pullback_window",
            } else "skipped" if skip_reason else "partial",
            "costs_bps": config.costs_bps,
            "labels": {},
        }
        if skip_reason:
            scenarios.append(scenario)
            continue

        scenario["entry_at"] = pd.Timestamp(entry_at).date().isoformat()
        scenario["entry_price"] = round(float(entry_price), 4)
        entry_position = int(future.index.get_loc(entry_at))
        benchmark_entry = _row_for_normalized_date(benchmark, entry_at)
        benchmark_entry_price = _benchmark_entry_price(benchmark_entry, method)
        scenario["benchmark_entry_price"] = (
            round(benchmark_entry_price, 4)
            if benchmark_entry_price is not None
            else None
        )

        # A close entry starts accruing risk on the next session. Other methods
        # are intraday executions and include the fill session as holding day one.
        exit_base = entry_position + (1 if method == "next_close" else 0)
        for horizon in HORIZONS:
            exit_position = exit_base + horizon - 1
            if exit_position >= len(future):
                continue
            exit_at = future.index[exit_position]
            exit_price = float(future.iloc[exit_position]["Close"])
            experienced = future.iloc[exit_base : exit_position + 1]
            benchmark_exit = _row_for_normalized_date(benchmark, exit_at)
            benchmark_return = (
                _pct_return(float(benchmark_exit["Close"]), benchmark_entry_price)
                if benchmark_exit is not None and benchmark_entry_price is not None
                else None
            )
            net_return = _net_return(exit_price, entry_price, config)
            scenario["labels"][str(horizon)] = {
                "exit_at": pd.Timestamp(exit_at).date().isoformat(),
                "exit_price": round(exit_price, 4),
                "net_return": net_return,
                "benchmark_return": benchmark_return,
                "excess_return": (
                    round(net_return - benchmark_return, 4)
                    if net_return is not None and benchmark_return is not None
                    else None
                ),
                "max_return": _pct_return(
                    float(experienced["High"].max()), entry_price
                ),
                "max_drawdown": _pct_return(
                    float(experienced["Low"].min()), entry_price
                ),
            }
            scenario["matured_horizon"] = horizon
        if scenario["matured_horizon"] >= max(HORIZONS):
            scenario["outcome_status"] = "complete"
        scenarios.append(scenario)
    return scenarios


def _canonical_replay_events(database_path, start=None, end=None):
    where = []
    params = []
    if start:
        where.append("hre.trade_date >= ?")
        params.append(str(start))
    if end:
        where.append("hre.trade_date <= ?")
        params.append(str(end))
    filter_sql = " AND " + " AND ".join(where) if where else ""
    with get_connection(database_path) as conn:
        init_db(conn)
        return pd.read_sql_query(
            f"""
            WITH canonical AS (
                SELECT hre.*, hrr.finished_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY hre.trade_date, hre.code
                           ORDER BY COALESCE(hrr.finished_at, hrr.started_at) DESC,
                                    hre.id DESC
                       ) AS day_code_rank
                FROM historical_replay_events hre
                JOIN historical_replay_runs hrr ON hrr.id=hre.replay_run_id
                WHERE hrr.status='completed'
                  AND hrr.universe_quality_status IN ('verified', 'partial')
                  {filter_sql}
            )
            SELECT id AS source_event_id, trade_date, code, signal_price
            FROM canonical
            WHERE day_code_rank=1
            ORDER BY trade_date, code
            """,
            conn,
            params=params,
        )


def _flatten_scenarios(event, scenarios):
    row = {
        "source_event_id": int(event["source_event_id"]),
        "trade_date": str(event["trade_date"]),
        "code": str(event["code"]),
        "scenario_version": EXECUTION_RESEARCH_VERSION,
    }
    for scenario in scenarios:
        prefix = scenario["entry_method"]
        row[f"{prefix}_entry_status"] = scenario["entry_status"]
        row[f"{prefix}_skip_reason"] = scenario["skip_reason"]
        row[f"{prefix}_entry_at"] = scenario["entry_at"]
        row[f"{prefix}_entry_price"] = scenario["entry_price"]
        row[f"{prefix}_benchmark_entry_price"] = scenario[
            "benchmark_entry_price"
        ]
        row[f"{prefix}_matured_horizon"] = scenario["matured_horizon"]
        row[f"{prefix}_outcome_status"] = scenario["outcome_status"]
        for horizon in HORIZONS:
            suffix = f"{horizon}d"
            for key in (
                "exit_at",
                "exit_price",
                "net_return",
                "benchmark_return",
                "excess_return",
                "max_return",
                "max_drawdown",
            ):
                row[f"{prefix}_{key}_{suffix}"] = None
        for horizon, label in scenario["labels"].items():
            suffix = f"{horizon}d"
            for key, value in label.items():
                row[f"{prefix}_{key}_{suffix}"] = value
    return row


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_replay_execution_dataset(
    database_path,
    output_path,
    universe_file,
    cache_dir="data/replay_cache/yahoo",
    start=None,
    end=None,
    refresh_cache=False,
    max_symbols=None,
):
    events = _canonical_replay_events(database_path, start=start, end=end)
    if events.empty:
        raise ValueError("No completed historical replay events are available.")
    if max_symbols:
        codes = sorted(events["code"].astype(str).unique())[: int(max_symbols)]
        events = events[events["code"].astype(str).isin(codes)].copy()

    universe, _ = load_replay_universe(universe_file=universe_file)
    tickers = set()
    for event in events.itertuples(index=False):
        stock = _stock_for_date(universe, str(event.code), event.trade_date)
        if stock is not None:
            tickers.add(f"{event.code}.{_market_suffix(stock.market)}")
    history_start = pd.Timestamp(events["trade_date"].min()) - pd.Timedelta(days=7)
    history_end = pd.Timestamp(events["trade_date"].max()) + pd.Timedelta(days=45)
    histories = download_replay_history(
        sorted(tickers),
        history_start,
        history_end,
        cache_dir=cache_dir,
        refresh_cache=refresh_cache,
    )
    histories, _ = resolve_transfer_history_aliases(histories, universe)
    histories = {
        ticker: normalized
        for ticker, frame in histories.items()
        if (normalized := _normalize_price_frame(frame)) is not None
    }
    benchmark = _download_benchmark(
        history_start,
        history_end,
        cache_dir=cache_dir,
        refresh_cache=refresh_cache,
    )
    benchmark = _normalize_price_frame(benchmark)

    records = []
    missing_prices = 0
    for event in events.to_dict("records"):
        stock = _stock_for_date(universe, str(event["code"]), event["trade_date"])
        ticker = (
            f"{event['code']}.{_market_suffix(stock.market)}" if stock else None
        )
        prices = histories.get(ticker)
        scenarios = calculate_execution_scenarios(
            event, prices, benchmark, prices_are_normalized=True
        )
        if not scenarios:
            missing_prices += 1
            continue
        records.append(_flatten_scenarios(event, scenarios))
    if not records:
        raise ValueError("No execution scenarios could be calculated.")

    frame = pd.DataFrame(records).sort_values(["trade_date", "code"])
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(
        output_path,
        index=False,
        compression={"method": "gzip", "compresslevel": 9, "mtime": 0},
    )
    metadata = {
        "dataset_version": EXECUTION_RESEARCH_VERSION,
        "entry_methods": list(ENTRY_METHODS),
        "horizons": list(HORIZONS),
        "rows": int(len(frame)),
        "source_events": int(len(events)),
        "missing_price_events": int(missing_prices),
        "start_date": str(frame["trade_date"].min()),
        "end_date": str(frame["trade_date"].max()),
        "symbols": int(frame["code"].nunique()),
        "output": output_path.name,
        "bytes": output_path.stat().st_size,
        "sha256": _sha256(output_path),
        "generated_at": get_taipei_now().isoformat(timespec="seconds"),
        "config": asdict(ExecutionResearchConfig()),
    }
    metadata_path = output_path.with_suffix(output_path.suffix + ".metadata.json")
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**metadata, "metadata": str(metadata_path)}


def main():
    parser = argparse.ArgumentParser(
        description="Export fixed-horizon execution labels for replay candidates."
    )
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--universe", required=True)
    parser.add_argument("--cache-dir", default="data/replay_cache/yahoo")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--max-symbols", type=int)
    parser.add_argument("--refresh-cache", action="store_true")
    args = parser.parse_args()
    result = export_replay_execution_dataset(
        args.database,
        args.output,
        args.universe,
        cache_dir=args.cache_dir,
        start=args.start,
        end=args.end,
        refresh_cache=args.refresh_cache,
        max_symbols=args.max_symbols,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
