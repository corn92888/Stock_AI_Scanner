import argparse
import json
import sqlite3
from dataclasses import asdict, dataclass

import pandas as pd
import twstock
import yfinance as yf

from database import (
    DB_PATH,
    finish_backtest_run,
    get_connection,
    init_db,
    save_backtest_result,
    start_backtest_run,
)

HORIZONS = (1, 3, 5, 10, 20)


@dataclass(frozen=True)
class BacktestConfig:
    buy_fee_rate: float = 0.001425
    sell_fee_rate: float = 0.001425
    sell_tax_rate: float = 0.003
    slippage_rate: float = 0.001
    benchmark_code: str = "^TWII"
    success_excess_return_3d: float = 2.0
    success_max_drawdown_3d: float = -4.0
    price_basis: str = "adjusted_ohlc"

    @property
    def costs_bps(self):
        total_rate = (
            self.buy_fee_rate
            + self.sell_fee_rate
            + self.sell_tax_rate
            + 2 * self.slippage_rate
        )
        return round(total_rate * 10000, 2)

    def to_json(self):
        return json.dumps(asdict(self), ensure_ascii=True, sort_keys=True)


def resolve_yf_ticker(code):
    code = str(code).strip()
    info = twstock.codes.get(code)
    suffix = "TWO" if info and info.market == "上櫃" else "TW"
    return f"{code}.{suffix}"


def _normalize_price_frame(df):
    if df is None or df.empty:
        return None

    data = df.copy()
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = [col[0] for col in data.columns]
    data = data.loc[:, ~data.columns.duplicated()]

    required = ["Open", "High", "Low", "Close"]
    for col in required + ["Adj Close", "Volume"]:
        if col in data.columns:
            data[col] = pd.to_numeric(data[col], errors="coerce")
    if any(col not in data.columns for col in required):
        return None

    already_adjusted = "AdjustmentFactor" in data.columns
    if already_adjusted:
        factor = pd.to_numeric(data["AdjustmentFactor"], errors="coerce").fillna(1.0)
    else:
        factor = pd.Series(1.0, index=data.index)
        if "Adj Close" in data.columns:
            factor = (data["Adj Close"] / data["Close"]).replace(
                [float("inf"), -float("inf")], pd.NA
            )
            factor = factor.fillna(1.0)
        for col in required:
            data[col] = data[col] * factor
    data["AdjustmentFactor"] = factor

    data.index = pd.to_datetime(data.index)
    if getattr(data.index, "tz", None) is not None:
        data.index = data.index.tz_localize(None)
    data.sort_index(inplace=True)
    data = data[~data.index.duplicated(keep="last")]
    data.dropna(subset=required, inplace=True)
    return data


def download_price_data(ticker, start, end):
    df = yf.download(
        ticker,
        start=pd.Timestamp(start).strftime("%Y-%m-%d"),
        end=pd.Timestamp(end).strftime("%Y-%m-%d"),
        progress=False,
        auto_adjust=False,
    )
    return _normalize_price_frame(df)


class PriceCache:
    def __init__(self, start, end, loader=download_price_data):
        self.start = pd.Timestamp(start)
        self.end = pd.Timestamp(end)
        self.loader = loader
        self._frames = {}

    def get_ticker(self, ticker):
        if ticker not in self._frames:
            self._frames[ticker] = self.loader(ticker, self.start, self.end)
        return self._frames[ticker]

    def get_stock(self, code):
        return self.get_ticker(resolve_yf_ticker(code))


def load_pending_signals(
    mode=None,
    strategy=None,
    limit=None,
    refresh=False,
    db_path=DB_PATH,
):
    params = []
    where = []
    if not refresh:
        where.append("(br.id IS NULL OR COALESCE(br.outcome_status, 'pending') <> 'complete')")
    if mode:
        where.append("s.mode = ?")
        params.append(mode)
    if strategy:
        where.append("s.strategy = ?")
        params.append(strategy)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    sql = f"""
        SELECT s.*
        FROM stock_signals s
        LEFT JOIN backtest_results br ON br.signal_id = s.id
        {where_sql}
        ORDER BY s.trade_date ASC, s.mode ASC, s.strategy ASC, s.rank_order ASC
    """
    if limit:
        sql += " LIMIT ?"
        params.append(limit)

    with get_connection(db_path) as conn:
        init_db(conn)
        return conn.execute(sql, params).fetchall()


def fetch_price_window(code, trade_date):
    start = pd.Timestamp(trade_date) - pd.Timedelta(days=5)
    end = pd.Timestamp(trade_date) + pd.Timedelta(days=60)
    return download_price_data(resolve_yf_ticker(code), start, end)


def _signal_value(signal, key, default=None):
    try:
        value = signal[key]
    except (KeyError, TypeError, IndexError):
        value = default
    return default if value is None else value


def _pct_return(exit_price, entry_price):
    if entry_price in (None, 0) or exit_price is None:
        return None
    return round((exit_price - entry_price) / entry_price * 100, 4)


def _net_return(exit_price, entry_price, config):
    if entry_price in (None, 0) or exit_price is None:
        return None
    entry_cost = entry_price * (1 + config.buy_fee_rate + config.slippage_rate)
    exit_proceeds = exit_price * (
        1 - config.sell_fee_rate - config.sell_tax_rate - config.slippage_rate
    )
    return round((exit_proceeds - entry_cost) / entry_cost * 100, 4)


def _row_for_date(df, date):
    if df is None or df.empty:
        return None
    matches = df[df.index.normalize() == pd.Timestamp(date).normalize()]
    return None if matches.empty else matches.iloc[0]


def _entry_method(signal):
    if _signal_value(signal, "mode") == "intraday":
        return "legacy_next_day_open_intraday"
    return "next_day_open_eod"


def calculate_signal_result(signal, price_df=None, benchmark_df=None, config=None):
    config = config or BacktestConfig()
    if price_df is None:
        price_df = fetch_price_window(signal["code"], signal["trade_date"])
    else:
        price_df = _normalize_price_frame(price_df)
    if price_df is None or price_df.empty:
        return None

    if benchmark_df is not None:
        benchmark_df = _normalize_price_frame(benchmark_df)

    trade_ts = pd.Timestamp(_signal_value(signal, "trade_date")).normalize()
    future = price_df[price_df.index.normalize() > trade_ts]
    if future.empty:
        return None

    entry_row = future.iloc[0]
    entry_date = future.index[0].strftime("%Y-%m-%d")
    entry_price = float(entry_row["Open"])
    entry_factor = float(entry_row.get("AdjustmentFactor", 1.0))
    benchmark_entry_row = _row_for_date(benchmark_df, entry_date)
    benchmark_entry_price = (
        float(benchmark_entry_row["Open"]) if benchmark_entry_row is not None else None
    )

    result = {
        "entry_date": entry_date,
        "entry_price": round(entry_price, 4),
        "entry_method": _entry_method(signal),
        "price_basis": config.price_basis,
        "benchmark_code": config.benchmark_code,
        "benchmark_entry_price": (
            round(benchmark_entry_price, 4) if benchmark_entry_price is not None else None
        ),
        "stop_loss_hit": False,
        "stop_loss_date": None,
        "success_t3": None,
        "costs_bps": config.costs_bps,
        "config_json": config.to_json(),
        "price_data_end": price_df.index[-1].strftime("%Y-%m-%d"),
    }

    matured = [horizon for horizon in HORIZONS if len(future) >= horizon]
    result["matured_horizon"] = max(matured, default=0)
    result["outcome_status"] = "complete" if len(future) >= max(HORIZONS) else "partial"

    for horizon in HORIZONS:
        key = f"{horizon}d"
        result[f"exit_{key}_price"] = None
        result[f"return_{key}"] = None
        result[f"net_return_{key}"] = None
        result[f"benchmark_exit_{key}_price"] = None
        result[f"benchmark_return_{key}"] = None
        result[f"excess_return_{key}"] = None
        if len(future) < horizon:
            continue

        exit_row = future.iloc[horizon - 1]
        exit_date = future.index[horizon - 1]
        exit_price = float(exit_row["Close"])
        gross_return = _pct_return(exit_price, entry_price)
        net_return = _net_return(exit_price, entry_price, config)
        result[f"exit_{key}_price"] = round(exit_price, 4)
        result[f"return_{key}"] = gross_return
        result[f"net_return_{key}"] = net_return

        benchmark_exit_row = _row_for_date(benchmark_df, exit_date)
        if benchmark_entry_price is not None and benchmark_exit_row is not None:
            benchmark_exit_price = float(benchmark_exit_row["Close"])
            benchmark_return = _pct_return(benchmark_exit_price, benchmark_entry_price)
            result[f"benchmark_exit_{key}_price"] = round(benchmark_exit_price, 4)
            result[f"benchmark_return_{key}"] = benchmark_return
            result[f"excess_return_{key}"] = round(net_return - benchmark_return, 4)

    if len(future) >= 3:
        window_3d = future.head(3)
        result["max_return_3d"] = _pct_return(float(window_3d["High"].max()), entry_price)
        result["max_drawdown_3d"] = _pct_return(float(window_3d["Low"].min()), entry_price)
    else:
        result["max_return_3d"] = None
        result["max_drawdown_3d"] = None

    if len(future) >= 20:
        window_20d = future.head(20)
        result["max_return_20d"] = _pct_return(float(window_20d["High"].max()), entry_price)
        result["max_drawdown_20d"] = _pct_return(float(window_20d["Low"].min()), entry_price)
    else:
        result["max_return_20d"] = None
        result["max_drawdown_20d"] = None

    stop_loss = _signal_value(signal, "stop_loss")
    if stop_loss is not None:
        adjusted_stop = float(stop_loss) * entry_factor
        available_window = future.head(min(len(future), max(HORIZONS)))
        hits = available_window[available_window["Low"] <= adjusted_stop]
        if not hits.empty:
            result["stop_loss_hit"] = True
            result["stop_loss_date"] = hits.index[0].strftime("%Y-%m-%d")

    excess_return_3d = result.get("excess_return_3d")
    max_drawdown_3d = result.get("max_drawdown_3d")
    if excess_return_3d is not None and max_drawdown_3d is not None:
        result["success_t3"] = (
            excess_return_3d >= config.success_excess_return_3d
            and max_drawdown_3d >= config.success_max_drawdown_3d
        )

    return result


def run_backtest(
    mode=None,
    strategy=None,
    limit=None,
    db_path=DB_PATH,
    config=None,
    refresh=False,
    price_loader=download_price_data,
):
    config = config or BacktestConfig()
    signals = load_pending_signals(
        mode=mode,
        strategy=strategy,
        limit=limit,
        refresh=refresh,
        db_path=db_path,
    )
    if not signals:
        print("No pending backtest signals.")
        return {"saved": 0, "complete": 0, "partial": 0, "skipped": 0}

    min_trade_date = min(pd.Timestamp(signal["trade_date"]) for signal in signals)
    start = min_trade_date - pd.Timedelta(days=5)
    end = pd.Timestamp.now().normalize() + pd.Timedelta(days=1)
    cache = PriceCache(start=start, end=end, loader=price_loader)
    benchmark_df = cache.get_ticker(config.benchmark_code)
    config_json = config.to_json()
    audit_run_id = start_backtest_run(config_json, len(signals), db_path=db_path)

    saved = 0
    complete = 0
    partial = 0
    skipped = 0
    rows = []
    try:
        for signal in signals:
            print(
                f"Backtest {signal['trade_date']} {signal['mode']} "
                f"{signal['strategy']} {signal['code']} {signal['name']}"
            )
            price_df = cache.get_stock(signal["code"])
            result = calculate_signal_result(
                signal,
                price_df=price_df,
                benchmark_df=benchmark_df,
                config=config,
            )
            if result is None:
                skipped += 1
                print("  No mature price data; skipped.")
                continue

            save_backtest_result(signal["id"], result, db_path=db_path)
            saved += 1
            if result["outcome_status"] == "complete":
                complete += 1
            else:
                partial += 1
            rows.append(
                {
                    "date": signal["trade_date"],
                    "mode": signal["mode"],
                    "strategy": signal["strategy"],
                    "code": signal["code"],
                    "name": signal["name"],
                    "status": result["outcome_status"],
                    "matured": result["matured_horizon"],
                    "net_return_3d": result.get("net_return_3d"),
                    "excess_return_3d": result.get("excess_return_3d"),
                    "max_drawdown_3d": result.get("max_drawdown_3d"),
                    "success_t3": result.get("success_t3"),
                }
            )

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
        f"Backtest saved: {saved}; complete: {complete}; "
        f"partial: {partial}; skipped: {skipped}"
    )
    if rows:
        print(pd.DataFrame(rows).to_string(index=False))
    return {"saved": saved, "complete": complete, "partial": partial, "skipped": skipped}


def summarize_results(db_path=DB_PATH):
    with get_connection(db_path) as conn:
        init_db(conn)
        try:
            df = pd.read_sql_query(
                """
                SELECT
                    s.mode,
                    s.strategy,
                    COUNT(*) AS evaluated_signals,
                    SUM(CASE WHEN br.outcome_status = 'complete' THEN 1 ELSE 0 END) AS complete_signals,
                    ROUND(AVG(br.net_return_3d), 2) AS avg_net_return_3d,
                    ROUND(AVG(br.excess_return_3d), 2) AS avg_excess_return_3d,
                    ROUND(AVG(CASE WHEN br.success_t3 IS NOT NULL THEN br.success_t3 * 1.0 END) * 100, 2)
                        AS success_rate_t3,
                    ROUND(AVG(br.max_drawdown_3d), 2) AS avg_max_drawdown_3d,
                    ROUND(AVG(br.net_return_5d), 2) AS avg_net_return_5d,
                    ROUND(AVG(br.stop_loss_hit) * 100, 2) AS stop_loss_hit_rate
                FROM stock_signals s
                JOIN backtest_results br ON br.signal_id = s.id
                GROUP BY s.mode, s.strategy
                ORDER BY s.mode, s.strategy
                """,
                conn,
            )
        except sqlite3.OperationalError:
            df = pd.DataFrame()

    if df.empty:
        print("No completed backtest results.")
    else:
        print(df.to_string(index=False))


def main():
    parser = argparse.ArgumentParser(description="Backtest stored stock scanner signals.")
    parser.add_argument("--mode", choices=["eod", "intraday"], help="Only backtest one scan mode")
    parser.add_argument(
        "--strategy",
        choices=["trend", "reversal", "wave"],
        help="Only backtest one strategy",
    )
    parser.add_argument("--limit", type=int, help="Maximum signals to update")
    parser.add_argument("--summary", action="store_true", help="Show backtest statistics")
    parser.add_argument("--refresh", action="store_true", help="Recalculate completed outcomes")
    parser.add_argument("--db", default=str(DB_PATH), help="SQLite database path")
    parser.add_argument("--benchmark", default="^TWII", help="Yahoo Finance benchmark ticker")
    parser.add_argument("--buy-fee-rate", type=float, default=0.001425)
    parser.add_argument("--sell-fee-rate", type=float, default=0.001425)
    parser.add_argument("--sell-tax-rate", type=float, default=0.003)
    parser.add_argument("--slippage-rate", type=float, default=0.001)
    parser.add_argument("--success-excess-return", type=float, default=2.0)
    parser.add_argument("--success-max-drawdown", type=float, default=-4.0)
    args = parser.parse_args()

    if args.summary:
        summarize_results(db_path=args.db)
        return

    config = BacktestConfig(
        buy_fee_rate=args.buy_fee_rate,
        sell_fee_rate=args.sell_fee_rate,
        sell_tax_rate=args.sell_tax_rate,
        slippage_rate=args.slippage_rate,
        benchmark_code=args.benchmark,
        success_excess_return_3d=args.success_excess_return,
        success_max_drawdown_3d=args.success_max_drawdown,
    )
    run_backtest(
        mode=args.mode,
        strategy=args.strategy,
        limit=args.limit,
        db_path=args.db,
        config=config,
        refresh=args.refresh,
    )


if __name__ == "__main__":
    main()
