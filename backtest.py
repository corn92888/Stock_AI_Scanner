import argparse
import sqlite3

import pandas as pd
import twstock
import yfinance as yf

from database import DB_PATH, get_connection, init_db, save_backtest_result

HORIZONS = (1, 3, 5, 10, 20)


def resolve_yf_ticker(code):
    code = str(code).strip()
    info = twstock.codes.get(code)
    suffix = "TWO" if info and info.market == "上櫃" else "TW"
    return f"{code}.{suffix}"


def load_pending_signals(mode=None, strategy=None, limit=None, db_path=DB_PATH):
    params = []
    where = ["br.id IS NULL"]
    if mode:
        where.append("s.mode = ?")
        params.append(mode)
    if strategy:
        where.append("s.strategy = ?")
        params.append(strategy)

    sql = f"""
        SELECT s.*
        FROM stock_signals s
        LEFT JOIN backtest_results br ON br.signal_id = s.id
        WHERE {" AND ".join(where)}
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
    df = yf.download(
        resolve_yf_ticker(code),
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        progress=False,
        auto_adjust=False,
    )
    if df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] for col in df.columns]
    df = df.loc[:, ~df.columns.duplicated()]
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df.dropna(subset=["Open", "High", "Low", "Close"], inplace=True)
    return df


def _pct_return(exit_price, entry_price):
    if entry_price in (None, 0) or exit_price is None:
        return None
    return round((exit_price - entry_price) / entry_price * 100, 2)


def calculate_signal_result(signal):
    df = fetch_price_window(signal["code"], signal["trade_date"])
    if df is None or df.empty:
        return None

    trade_ts = pd.Timestamp(signal["trade_date"])
    future = df[df.index > trade_ts]
    if future.empty:
        return None

    entry_row = future.iloc[0]
    entry_date = future.index[0].strftime("%Y-%m-%d")
    entry_price = float(entry_row["Open"])
    window = future.head(max(HORIZONS))

    result = {
        "entry_date": entry_date,
        "entry_price": round(entry_price, 2),
        "stop_loss_hit": False,
        "stop_loss_date": None,
    }

    for horizon in HORIZONS:
        key = f"{horizon}d"
        if len(future) >= horizon:
            exit_price = float(future.iloc[horizon - 1]["Close"])
            result[f"exit_{key}_price"] = round(exit_price, 2)
            result[f"return_{key}"] = _pct_return(exit_price, entry_price)
        else:
            result[f"exit_{key}_price"] = None
            result[f"return_{key}"] = None

    if not window.empty:
        result["max_return_20d"] = _pct_return(float(window["High"].max()), entry_price)
        result["max_drawdown_20d"] = _pct_return(float(window["Low"].min()), entry_price)

        stop_loss = signal["stop_loss"]
        if stop_loss is not None:
            hits = window[window["Low"] <= float(stop_loss)]
            if not hits.empty:
                result["stop_loss_hit"] = True
                result["stop_loss_date"] = hits.index[0].strftime("%Y-%m-%d")

    return result


def run_backtest(mode=None, strategy=None, limit=None, db_path=DB_PATH):
    signals = load_pending_signals(mode=mode, strategy=strategy, limit=limit, db_path=db_path)
    if not signals:
        print("ℹ️ 沒有待回測的訊號。")
        return

    completed = 0
    skipped = 0
    rows = []
    for signal in signals:
        print(f"🔎 回測 {signal['trade_date']} {signal['mode']} {signal['strategy']} {signal['code']} {signal['name']}")
        result = calculate_signal_result(signal)
        if result is None:
            skipped += 1
            print("   └ 資料不足，略過")
            continue

        save_backtest_result(signal["id"], result, db_path=db_path)
        completed += 1
        rows.append(
            {
                "date": signal["trade_date"],
                "mode": signal["mode"],
                "strategy": signal["strategy"],
                "code": signal["code"],
                "name": signal["name"],
                "return_5d": result.get("return_5d"),
                "return_20d": result.get("return_20d"),
                "max_return_20d": result.get("max_return_20d"),
                "max_drawdown_20d": result.get("max_drawdown_20d"),
                "stop_loss_hit": result.get("stop_loss_hit"),
            }
        )

    print(f"\n✅ 回測完成: {completed} 筆，略過: {skipped} 筆")
    if rows:
        df = pd.DataFrame(rows)
        print("\n📊 本次回測摘要:")
        print(df.to_string(index=False))


def summarize_results(db_path=DB_PATH):
    with get_connection(db_path) as conn:
        init_db(conn)
        try:
            df = pd.read_sql_query(
                """
                SELECT
                    s.mode,
                    s.strategy,
                    COUNT(*) AS signals,
                    ROUND(AVG(br.return_5d), 2) AS avg_return_5d,
                    ROUND(AVG(br.return_20d), 2) AS avg_return_20d,
                    ROUND(AVG(CASE WHEN br.return_20d > 0 THEN 1.0 ELSE 0.0 END) * 100, 2) AS win_rate_20d,
                    ROUND(AVG(br.max_drawdown_20d), 2) AS avg_max_drawdown_20d,
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
        print("ℹ️ 目前沒有已完成的回測結果。")
    else:
        print(df.to_string(index=False))


def main():
    parser = argparse.ArgumentParser(description="Backtest stored stock scanner signals.")
    parser.add_argument("--mode", choices=["eod", "intraday"], help="只回測指定掃描模式")
    parser.add_argument("--strategy", choices=["trend", "reversal", "wave"], help="只回測指定策略")
    parser.add_argument("--limit", type=int, help="最多回測幾筆待測訊號")
    parser.add_argument("--summary", action="store_true", help="顯示已完成回測統計")
    parser.add_argument("--db", default=str(DB_PATH), help="SQLite 資料庫路徑")
    args = parser.parse_args()

    db_path = args.db
    if args.summary:
        summarize_results(db_path=db_path)
    else:
        run_backtest(mode=args.mode, strategy=args.strategy, limit=args.limit, db_path=db_path)


if __name__ == "__main__":
    main()
