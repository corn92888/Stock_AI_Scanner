import argparse
import datetime as dt
import hashlib
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd
import twstock
import yfinance as yf

from candidate_backtest import CandidateExecutionConfig, calculate_candidate_result
from database import (
    DB_PATH,
    HISTORICAL_REPLAY_EXECUTION_VERSION,
    HISTORICAL_REPLAY_VERSION,
    STRATEGY_VERSION,
    get_connection,
    get_git_commit,
    get_taipei_now,
    init_db,
)
from intraday_analysis_report import build_candidate_ranking_from_data
from logic import (
    calculate_indicators,
    check_reversal_strict,
    check_trend_strict,
    check_wave_strict,
)
from market_monitor import build_market_snapshot
from selection_policy import (
    DEFAULT_SELECTION_POLICY,
    apply_selection_policy,
    candidate_event_records,
)


TAIPEI_TZ = dt.timezone(dt.timedelta(hours=8), name="Asia/Taipei")
STRATEGY_LABELS = {
    "trend": "順勢突破",
    "reversal": "低檔爆量",
    "wave": "波段蓄勢",
}
STRATEGY_CHECKS = {
    "trend": check_trend_strict,
    "reversal": check_reversal_strict,
    "wave": check_wave_strict,
}


@dataclass(frozen=True)
class ReplayStock:
    code: str
    name: str
    industry: str
    market: str

    @property
    def group(self):
        return self.industry


@dataclass(frozen=True)
class HistoricalReplayConfig:
    start_date: str
    end_date: str
    replay_version: str = HISTORICAL_REPLAY_VERSION
    execution_version: str = HISTORICAL_REPLAY_EXECUTION_VERSION
    warmup_calendar_days: int = 500
    future_calendar_days: int = 21
    chunk_size: int = 100
    universe_source: str = "current_twstock_equities"
    price_source: str = "yahoo_finance_revised_history"

    def validate(self):
        start = pd.Timestamp(self.start_date).normalize()
        end = pd.Timestamp(self.end_date).normalize()
        if start > end:
            raise ValueError("Replay start_date must be on or before end_date.")
        if self.warmup_calendar_days < 400:
            raise ValueError("Replay warmup must cover at least 400 calendar days.")
        if self.future_calendar_days < 10:
            raise ValueError("Replay future buffer must cover at least 10 calendar days.")


def _normalize_code(value):
    code = str(value or "").strip().upper().replace(".TW", "").replace(".TWO", "")
    if code.endswith(".0"):
        code = code[:-2]
    return code.zfill(4) if code.isdigit() and len(code) < 4 else code


def _market_suffix(market):
    return "TWO" if str(market).strip() == "上櫃" else "TW"


def load_replay_universe(universe_file=None, codes=None, max_symbols=None):
    requested = {_normalize_code(code) for code in (codes or []) if _normalize_code(code)}
    stocks = {}
    source = "current_twstock_equities"
    if universe_file:
        path = Path(universe_file)
        frame = pd.read_csv(path, dtype=str)
        aliases = {
            "代號": "code",
            "名稱": "name",
            "產業族群": "industry",
            "市場": "market",
        }
        frame = frame.rename(columns=aliases)
        required = {"code", "name", "industry", "market"}
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"Universe file missing columns: {', '.join(sorted(missing))}")
        source = f"csv:{path.name}"
        for row in frame.to_dict(orient="records"):
            code = _normalize_code(row["code"])
            if code:
                stocks[code] = ReplayStock(
                    code=code,
                    name=str(row.get("name") or code),
                    industry=str(row.get("industry") or "其他"),
                    market=str(row.get("market") or "上市"),
                )
    else:
        for code, info in twstock.codes.items():
            if info.type != "股票":
                continue
            stocks[code] = ReplayStock(
                code=code,
                name=info.name,
                industry=info.group or "其他",
                market=info.market,
            )

    if requested:
        stocks = {code: stock for code, stock in stocks.items() if code in requested}
        missing = sorted(requested - set(stocks))
        if missing:
            raise ValueError(f"Unknown replay codes: {', '.join(missing)}")
    ordered = dict(sorted(stocks.items()))
    if max_symbols:
        ordered = dict(list(ordered.items())[: int(max_symbols)])
    if not ordered:
        raise ValueError("Replay universe is empty.")
    return ordered, source


def _normalize_history(frame):
    if frame is None or frame.empty:
        return None
    data = frame.copy()
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    data = data.loc[:, ~data.columns.duplicated()]
    required = ["Open", "High", "Low", "Close", "Volume"]
    if any(column not in data.columns for column in required):
        return None
    for column in required + ["Adj Close"]:
        if column in data.columns:
            data[column] = pd.to_numeric(data[column], errors="coerce")
    data.index = pd.to_datetime(data.index)
    if getattr(data.index, "tz", None) is not None:
        data.index = data.index.tz_localize(None)
    data.index = data.index.normalize()
    data = data[~data.index.duplicated(keep="last")].sort_index()
    data.dropna(subset=required, inplace=True)
    return data if not data.empty else None


def _ticker_level(columns, tickers):
    if not isinstance(columns, pd.MultiIndex):
        return None
    ticker_set = set(tickers)
    for level in range(columns.nlevels):
        if ticker_set.intersection(map(str, columns.get_level_values(level).unique())):
            return level
    return columns.nlevels - 1


def download_replay_history(tickers, start, end, chunk_size=100):
    histories = {}
    for offset in range(0, len(tickers), chunk_size):
        chunk = tickers[offset : offset + chunk_size]
        raw = yf.download(
            chunk,
            start=pd.Timestamp(start).strftime("%Y-%m-%d"),
            end=pd.Timestamp(end).strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=False,
            threads=True,
        )
        if raw is None or raw.empty:
            continue
        if len(chunk) == 1:
            normalized = _normalize_history(raw)
            if normalized is not None:
                histories[chunk[0]] = normalized
            continue
        level = _ticker_level(raw.columns, chunk)
        for ticker in chunk:
            try:
                ticker_frame = raw.xs(ticker, axis=1, level=level, drop_level=True)
            except (KeyError, TypeError, ValueError):
                continue
            normalized = _normalize_history(ticker_frame)
            if normalized is not None:
                histories[ticker] = normalized
    return histories


def _download_benchmark(start, end):
    raw = yf.download(
        "^TWII",
        start=pd.Timestamp(start).strftime("%Y-%m-%d"),
        end=pd.Timestamp(end).strftime("%Y-%m-%d"),
        progress=False,
        auto_adjust=False,
    )
    return _normalize_history(raw)


def collect_strategy_signals(histories, ticker_to_code, universe, start_date, end_date):
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    signals_by_date = defaultdict(list)
    for ticker, raw in histories.items():
        code = ticker_to_code.get(ticker)
        stock = universe.get(code)
        if stock is None:
            continue
        indicators = calculate_indicators(raw)
        if indicators is None or indicators.empty:
            continue
        for position, decision_date in enumerate(indicators.index):
            decision_date = pd.Timestamp(decision_date).normalize()
            if decision_date < start or decision_date > end:
                continue
            known = indicators.iloc[: position + 1]
            if known.index.max().normalize() > decision_date:
                raise RuntimeError("Replay indicator window contains future data.")
            last = known.iloc[-1]
            for strategy, checker in STRATEGY_CHECKS.items():
                matched, note, pct_change, stop_loss = checker(known)
                if not matched:
                    continue
                signals_by_date[decision_date].append(
                    {
                        "產業族群": stock.industry,
                        "代號": code,
                        "名稱": stock.name,
                        "現價": round(float(last["Close"]), 4),
                        "防守價": round(float(stop_loss), 4),
                        "漲跌幅": round(float(pct_change), 4),
                        "成交量(張)": int(float(last["Volume"]) / 1000),
                        "RSI": round(float(last.get("RSI", 0)), 4),
                        "條件": note,
                        "策略": STRATEGY_LABELS[strategy],
                    }
                )
    return signals_by_date


def build_historical_market_context(decision_date, histories, ticker_to_code, universe):
    date = pd.Timestamp(decision_date).normalize()
    historical = {}
    realtime = {}
    active_map = {}
    active_codes = {}
    for ticker, frame in histories.items():
        code = ticker_to_code.get(ticker)
        stock = universe.get(code)
        if stock is None or date not in frame.index:
            continue
        past = frame[frame.index < date].copy()
        if past.empty:
            continue
        row = frame.loc[date]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[-1]
        close = float(row["Close"])
        if close <= 0:
            continue
        historical[ticker] = past
        active_map[ticker] = code
        active_codes[code] = stock
        realtime[code] = {
            "Open": float(row["Open"]),
            "High": float(row["High"]),
            "Low": float(row["Low"]),
            "Close": close,
            "Volume": float(row["Volume"]) / 1000,
        }
    captured_at = dt.datetime.combine(
        date.date(), dt.time(hour=14), tzinfo=TAIPEI_TZ
    )
    return {
        "captured_at": captured_at,
        "codes": active_codes,
        "yf_to_code": active_map,
        "history": historical,
        "realtime": realtime,
    }


def _replay_key(config, universe):
    universe_hash = hashlib.sha256(
        ",".join(sorted(universe)).encode("ascii")
    ).hexdigest()[:10]
    return (
        f"{config.replay_version}:{config.start_date}:{config.end_date}:"
        f"{config.universe_source}:{universe_hash}"
    )


def _start_replay_run(config, replay_key, universe_size, warnings, replace, db_path):
    now = get_taipei_now().isoformat(timespec="seconds")
    with get_connection(db_path) as conn:
        init_db(conn)
        existing = conn.execute(
            "SELECT id FROM historical_replay_runs WHERE replay_key=?", (replay_key,)
        ).fetchone()
        if existing and not replace:
            raise ValueError(
                "Replay already exists. Pass --replace to rebuild the same versioned range."
            )
        if existing:
            conn.execute(
                "DELETE FROM historical_replay_outcomes WHERE replay_event_id IN "
                "(SELECT id FROM historical_replay_events WHERE replay_run_id=?)",
                (existing["id"],),
            )
            conn.execute(
                "DELETE FROM historical_replay_events WHERE replay_run_id=?",
                (existing["id"],),
            )
            conn.execute("DELETE FROM historical_replay_runs WHERE id=?", (existing["id"],))
        cursor = conn.execute(
            """
            INSERT INTO historical_replay_runs (
                replay_key, replay_version, strategy_version, policy_version,
                execution_version, started_at, status, start_date, end_date,
                universe_source, universe_size, config_json, data_warnings_json,
                git_commit
            ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                replay_key,
                config.replay_version,
                STRATEGY_VERSION,
                DEFAULT_SELECTION_POLICY.version,
                config.execution_version,
                now,
                config.start_date,
                config.end_date,
                config.universe_source,
                universe_size,
                json.dumps(asdict(config), sort_keys=True),
                json.dumps(warnings, ensure_ascii=False),
                get_git_commit(),
            ),
        )
        return cursor.lastrowid


def _save_replay_event(conn, replay_run_id, trade_date, decision_at, record, volume_ratio_20):
    cursor = conn.execute(
        """
        INSERT INTO historical_replay_events (
            replay_run_id, trade_date, decision_at, code, name, industry,
            strategies_json, strategy_count, raw_rank, score, signal_price,
            pct_change, turnover_billion, volume_ratio_5, volume_ratio_20,
            intraday_position, observation_price, chase_limit, stop_distance_pct,
            tradable, block_reasons_json, risk_flags_json, is_selected,
            selection_rank, selection_status, policy_version, snapshot_json,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                  ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            replay_run_id,
            trade_date,
            decision_at,
            record["code"],
            record.get("name"),
            record.get("industry"),
            record["strategies_json"],
            record["strategy_count"],
            record.get("raw_rank"),
            record.get("score"),
            record.get("signal_price"),
            record.get("pct_change"),
            record.get("turnover_billion"),
            record.get("volume_ratio_5"),
            volume_ratio_20,
            record.get("intraday_position"),
            record.get("observation_price"),
            record.get("chase_limit"),
            record.get("stop_distance_pct"),
            int(record.get("tradable", False)),
            record["block_reasons_json"],
            record["risk_flags_json"],
            int(record.get("is_selected", False)),
            record.get("selection_rank"),
            record["selection_status"],
            record["policy_version"],
            record["snapshot_json"],
            get_taipei_now().isoformat(timespec="seconds"),
        ),
    )
    return cursor.lastrowid


def _save_replay_outcome(conn, event_id, execution_version, result):
    conn.execute(
        """
        INSERT INTO historical_replay_outcomes (
            replay_event_id, execution_version, entry_status, skip_reason,
            entry_at, entry_price, entry_method, exit_at, exit_price, exit_reason,
            fixed_net_return_1d, fixed_net_return_3d, fixed_net_return_5d,
            net_return_3d, benchmark_code, benchmark_entry_price,
            benchmark_return_3d, excess_return_3d, max_return_3d,
            max_drawdown_3d, defense_triggered, success_t3, matured_horizon,
            outcome_status, price_data_end, costs_bps, config_json, evaluated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                  ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            execution_version,
            result["entry_status"],
            result.get("skip_reason"),
            result.get("entry_at"),
            result.get("entry_price"),
            result.get("entry_method"),
            result.get("exit_at"),
            result.get("exit_price"),
            result.get("exit_reason"),
            result.get("fixed_net_return_1d"),
            result.get("fixed_net_return_3d"),
            result.get("fixed_net_return_5d"),
            result.get("net_return_3d"),
            result.get("benchmark_code"),
            result.get("benchmark_entry_price"),
            result.get("benchmark_return_3d"),
            result.get("excess_return_3d"),
            result.get("max_return_3d"),
            result.get("max_drawdown_3d"),
            int(result.get("defense_triggered", False)),
            None if result.get("success_t3") is None else int(result["success_t3"]),
            result.get("matured_horizon", 0),
            result.get("outcome_status", "pending"),
            result.get("price_data_end"),
            result.get("costs_bps"),
            result.get("config_json") or "{}",
            get_taipei_now().isoformat(timespec="seconds"),
        ),
    )


def _finish_replay_run(replay_run_id, metrics, db_path, error_text=None):
    status = "failed" if error_text else "completed"
    with get_connection(db_path) as conn:
        conn.execute(
            """
            UPDATE historical_replay_runs
            SET finished_at=?, status=?, available_symbols=?, trading_days=?,
                signal_events=?, candidate_events=?, selected_events=?,
                matured_t3=?, error_text=?
            WHERE id=?
            """,
            (
                get_taipei_now().isoformat(timespec="seconds"),
                status,
                metrics.get("available_symbols", 0),
                metrics.get("trading_days", 0),
                metrics.get("signal_events", 0),
                metrics.get("candidate_events", 0),
                metrics.get("selected_events", 0),
                metrics.get("matured_t3", 0),
                error_text,
                replay_run_id,
            ),
        )


def run_historical_replay(
    config,
    db_path=DB_PATH,
    universe_file=None,
    codes=None,
    max_symbols=None,
    replace=False,
    history_loader=download_replay_history,
    benchmark_loader=_download_benchmark,
):
    config.validate()
    universe, universe_source = load_replay_universe(
        universe_file=universe_file,
        codes=codes,
        max_symbols=max_symbols,
    )
    config = HistoricalReplayConfig(
        **{**asdict(config), "universe_source": universe_source}
    )
    warnings = [
        "Replay rows are isolated from live scan and prediction tables.",
        "Yahoo historical prices may contain later revisions and adjustment metadata.",
        "Historical fundamentals and news are not included in replay_v1.",
    ]
    if universe_source == "current_twstock_equities":
        warnings.append(
            "The current listed universe creates survivorship bias for older replay dates."
        )
    key = _replay_key(config, universe)
    replay_run_id = _start_replay_run(
        config, key, len(universe), warnings, replace, db_path
    )
    metrics = {
        "available_symbols": 0,
        "trading_days": 0,
        "signal_events": 0,
        "candidate_events": 0,
        "selected_events": 0,
        "matured_t3": 0,
    }
    try:
        start = pd.Timestamp(config.start_date) - pd.Timedelta(
            days=config.warmup_calendar_days
        )
        end = pd.Timestamp(config.end_date) + pd.Timedelta(
            days=config.future_calendar_days + 1
        )
        ticker_to_code = {
            f"{code}.{_market_suffix(stock.market)}": code
            for code, stock in universe.items()
        }
        histories = history_loader(
            list(ticker_to_code), start, end, config.chunk_size
        )
        histories = {
            ticker: normalized
            for ticker, frame in histories.items()
            if (normalized := _normalize_history(frame)) is not None
        }
        metrics["available_symbols"] = len(histories)
        if not histories:
            raise RuntimeError("No historical stock prices were downloaded.")
        benchmark = benchmark_loader(start, end)
        benchmark = _normalize_history(benchmark)
        if benchmark is None:
            raise RuntimeError("Benchmark ^TWII history is unavailable.")

        signals_by_date = collect_strategy_signals(
            histories,
            ticker_to_code,
            universe,
            config.start_date,
            config.end_date,
        )
        metrics["signal_events"] = sum(len(rows) for rows in signals_by_date.values())
        calendar = benchmark[
            (benchmark.index >= pd.Timestamp(config.start_date))
            & (benchmark.index <= pd.Timestamp(config.end_date))
        ].index
        metrics["trading_days"] = len(calendar)
        execution = CandidateExecutionConfig(
            execution_version=config.execution_version
        )
        code_to_ticker = {code: ticker for ticker, code in ticker_to_code.items()}

        for decision_date in sorted(signals_by_date):
            signals = pd.DataFrame(signals_by_date[decision_date])
            context = build_historical_market_context(
                decision_date, histories, ticker_to_code, universe
            )
            if not context["realtime"]:
                continue
            market, industry, summary, _ = build_market_snapshot(
                market_context=context
            )
            ranked, *_ = build_candidate_ranking_from_data(
                signals, market, industry, summary, pd.DataFrame()
            )
            ranked = apply_selection_policy(
                ranked, daily_state={}, policy=DEFAULT_SELECTION_POLICY
            )
            records = candidate_event_records(
                ranked, policy=DEFAULT_SELECTION_POLICY
            )
            decision_at = context["captured_at"].isoformat(timespec="seconds")
            trade_date = pd.Timestamp(decision_date).date().isoformat()
            with get_connection(db_path) as conn:
                for (_, ranked_row), record in zip(ranked.iterrows(), records):
                    event_id = _save_replay_event(
                        conn,
                        replay_run_id,
                        trade_date,
                        decision_at,
                        record,
                        ranked_row.get("量比20"),
                    )
                    metrics["candidate_events"] += 1
                    metrics["selected_events"] += int(record.get("is_selected", False))
                    ticker = code_to_ticker.get(record["code"])
                    result = calculate_candidate_result(
                        {
                            **record,
                            "trade_date": trade_date,
                            "mode": "eod",
                        },
                        histories.get(ticker),
                        benchmark_df=benchmark,
                        config=execution,
                    )
                    if result is None:
                        continue
                    _save_replay_outcome(
                        conn, event_id, config.execution_version, result
                    )
                    metrics["matured_t3"] += int(
                        result.get("entry_status") == "filled"
                        and result.get("matured_horizon", 0) >= 3
                    )
        _finish_replay_run(replay_run_id, metrics, db_path)
        return {
            "replay_run_id": replay_run_id,
            "replay_key": key,
            "warnings": warnings,
            **metrics,
        }
    except Exception as exc:
        _finish_replay_run(replay_run_id, metrics, db_path, error_text=str(exc))
        raise


def main():
    parser = argparse.ArgumentParser(
        description="Run an isolated point-in-time historical EOD scanner replay."
    )
    parser.add_argument("--start", required=True, dest="start_date")
    parser.add_argument("--end", required=True, dest="end_date")
    parser.add_argument("--db-path", default=str(DB_PATH))
    parser.add_argument("--universe-file")
    parser.add_argument("--codes", help="Comma-separated stock codes")
    parser.add_argument("--max-symbols", type=int)
    parser.add_argument("--chunk-size", type=int, default=100)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    config = HistoricalReplayConfig(
        start_date=args.start_date,
        end_date=args.end_date,
        chunk_size=args.chunk_size,
    )
    result = run_historical_replay(
        config,
        db_path=args.db_path,
        universe_file=args.universe_file,
        codes=[code.strip() for code in (args.codes or "").split(",") if code.strip()],
        max_symbols=args.max_symbols,
        replace=args.replace,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
