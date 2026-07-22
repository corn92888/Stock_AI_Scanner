import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import BacktestConfig, _normalize_price_frame
from historical_replay import (
    _download_benchmark,
    _market_suffix,
    download_replay_history,
    load_replay_universe,
    resolve_transfer_history_aliases,
)


ALPHA_DATASET_VERSION = "alpha_liquid_universe_point_in_time_v1"
ALPHA_EXECUTION_VERSION = "alpha_next_open_after_costs_v1"
ALPHA_HORIZONS = (5, 10, 20)
DEFAULT_OUTPUT = Path("data/alpha_universe_dataset.csv.gz")

ALPHA_FEATURES = (
    "return_1d",
    "return_5d",
    "return_20d",
    "return_60d",
    "momentum_20_ex_5",
    "momentum_60_ex_5",
    "distance_ma20_pct",
    "distance_ma60_pct",
    "distance_ma200_pct",
    "distance_high_252_pct",
    "volatility_20_ann_pct",
    "atr_14_pct",
    "rsi_14",
    "volume_ratio_5",
    "volume_ratio_20",
    "turnover_billion",
    "turnover_20d_billion",
    "intraday_position",
    "gap_open_pct",
    "market_return_1d",
    "market_return_20d",
    "market_above_ma200",
    "market_up_ratio",
    "market_avg_return",
    "market_median_return",
    "industry_up_ratio",
    "industry_avg_return",
    "industry_return_20d",
    "relative_return_20d",
    "relative_return_60d",
)


@dataclass(frozen=True)
class AlphaUniverseConfig:
    start_date: str
    end_date: str
    min_price: float = 5.0
    min_turnover_20d_billion: float = 1.0
    min_average_volume_20d: float = 100_000.0
    max_abs_return_1d: float = 9.5
    warmup_calendar_days: int = 500
    future_calendar_days: int = 45
    chunk_size: int = 100

    def validate(self):
        if pd.Timestamp(self.start_date) > pd.Timestamp(self.end_date):
            raise ValueError("start_date must be on or before end_date")
        if self.warmup_calendar_days < 400:
            raise ValueError("warmup_calendar_days must cover at least 400 days")
        if self.future_calendar_days < 35:
            raise ValueError("future_calendar_days must cover T+20 labels")
        if self.min_turnover_20d_billion <= 0:
            raise ValueError("min_turnover_20d_billion must be positive")


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_divide(numerator, denominator):
    return numerator / denominator.replace(0, np.nan)


def _rsi(close, period=14):
    delta = close.diff()
    gains = delta.clip(lower=0).rolling(period, min_periods=period).mean()
    losses = -delta.clip(upper=0).rolling(period, min_periods=period).mean()
    relative_strength = _safe_divide(gains, losses)
    return 100.0 - 100.0 / (1.0 + relative_strength)


def _future_min(series, horizon):
    return pd.concat(
        [series.shift(-offset) for offset in range(1, horizon + 1)], axis=1
    ).min(axis=1, skipna=False)


def _future_dates(index, offset):
    values = pd.Series(pd.DatetimeIndex(index), index=index)
    return values.shift(-offset)


def _mapped_values(series, dates):
    normalized = pd.to_datetime(dates, errors="coerce")
    return pd.Series(series.reindex(normalized).to_numpy(), index=dates.index)


def _net_return(exit_price, entry_price, config):
    entry_cost = entry_price * (1 + config.buy_fee_rate + config.slippage_rate)
    exit_proceeds = exit_price * (
        1 - config.sell_fee_rate - config.sell_tax_rate - config.slippage_rate
    )
    return (exit_proceeds / entry_cost - 1.0) * 100.0


def _history_for_code(universe, histories, code):
    pieces = []
    intervals = tuple(getattr(universe, "intervals", {}).get(code, ()))
    if intervals:
        for membership in intervals:
            ticker = f"{code}.{_market_suffix(membership.stock.market)}"
            history = histories.get(ticker)
            if history is None or history.empty:
                continue
            start = pd.Timestamp(membership.listed_on).normalize()
            end = (
                pd.Timestamp(membership.delisted_on).normalize()
                if membership.delisted_on is not None
                else None
            )
            scoped = history[history.index >= start].copy()
            if end is not None:
                scoped = scoped[scoped.index < end]
            if scoped.empty:
                continue
            scoped["industry"] = membership.stock.industry or "其他"
            scoped["name"] = membership.stock.name
            scoped["market"] = membership.stock.market
            pieces.append(scoped)
    else:
        stock = universe.stock_on(code) if hasattr(universe, "stock_on") else universe.get(code)
        if stock is None:
            return None
        ticker = f"{code}.{_market_suffix(stock.market)}"
        history = histories.get(ticker)
        if history is None or history.empty:
            return None
        scoped = history.copy()
        scoped["industry"] = stock.industry or "其他"
        scoped["name"] = stock.name
        scoped["market"] = stock.market
        pieces.append(scoped)
    if not pieces:
        return None
    return (
        pd.concat(pieces)
        .sort_index(kind="stable")
        .loc[lambda frame: ~frame.index.duplicated(keep="last")]
    )


def build_stock_feature_frame(code, history, benchmark, config, costs=None):
    costs = costs or BacktestConfig()
    prices = _normalize_price_frame(history)
    benchmark = _normalize_price_frame(benchmark)
    if prices is None or benchmark is None or len(prices) < 220:
        return pd.DataFrame()

    metadata = history[[column for column in ("industry", "name", "market") if column in history]]
    metadata = metadata.reindex(prices.index).ffill().bfill()
    close = prices["Close"]
    volume = pd.to_numeric(prices.get("Volume"), errors="coerce")
    daily_return = close.pct_change() * 100.0
    true_range = pd.concat(
        [
            prices["High"] - prices["Low"],
            (prices["High"] - close.shift(1)).abs(),
            (prices["Low"] - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    ma20 = close.rolling(20, min_periods=20).mean()
    ma60 = close.rolling(60, min_periods=60).mean()
    ma200 = close.rolling(200, min_periods=200).mean()
    volume5 = volume.rolling(5, min_periods=5).mean()
    volume20 = volume.rolling(20, min_periods=20).mean()
    turnover = close * volume / 100_000_000.0
    benchmark_close = benchmark["Close"]
    benchmark_ma200 = benchmark_close.rolling(200, min_periods=200).mean()

    frame = pd.DataFrame(index=prices.index)
    frame["trade_date"] = frame.index.date.astype(str)
    frame["code"] = str(code)
    for column in ("industry", "name", "market"):
        frame[column] = metadata[column] if column in metadata else ""
    frame["signal_price"] = close
    frame["return_1d"] = daily_return
    frame["return_5d"] = close.pct_change(5) * 100.0
    frame["return_20d"] = close.pct_change(20) * 100.0
    frame["return_60d"] = close.pct_change(60) * 100.0
    frame["momentum_20_ex_5"] = (close.shift(5) / close.shift(20) - 1.0) * 100.0
    frame["momentum_60_ex_5"] = (close.shift(5) / close.shift(60) - 1.0) * 100.0
    frame["distance_ma20_pct"] = (close / ma20 - 1.0) * 100.0
    frame["distance_ma60_pct"] = (close / ma60 - 1.0) * 100.0
    frame["distance_ma200_pct"] = (close / ma200 - 1.0) * 100.0
    frame["distance_high_252_pct"] = (
        close / close.rolling(252, min_periods=200).max() - 1.0
    ) * 100.0
    frame["volatility_20_ann_pct"] = (
        close.pct_change().rolling(20, min_periods=20).std() * np.sqrt(252) * 100.0
    )
    frame["atr_14_pct"] = true_range.rolling(14, min_periods=14).mean() / close * 100.0
    frame["rsi_14"] = _rsi(close)
    frame["volume_ratio_5"] = _safe_divide(volume, volume5)
    frame["volume_ratio_20"] = _safe_divide(volume, volume20)
    frame["turnover_billion"] = turnover
    frame["turnover_20d_billion"] = turnover.rolling(20, min_periods=20).mean()
    day_range = prices["High"] - prices["Low"]
    frame["intraday_position"] = _safe_divide(close - prices["Low"], day_range)
    frame["gap_open_pct"] = (prices["Open"] / close.shift(1) - 1.0) * 100.0
    frame["average_volume_20d"] = volume20
    frame["market_return_1d"] = benchmark_close.pct_change().reindex(frame.index) * 100.0
    frame["market_return_20d"] = benchmark_close.pct_change(20).reindex(frame.index) * 100.0
    frame["market_above_ma200"] = (
        benchmark_close.reindex(frame.index) > benchmark_ma200.reindex(frame.index)
    ).astype(float)

    benchmark_open = benchmark["Open"]
    benchmark_close = benchmark["Close"]
    entry_date = _future_dates(prices.index, 1)
    entry_price = prices["Open"].shift(-1)
    benchmark_entry = _mapped_values(benchmark_open, entry_date)
    frame["entry_at"] = entry_date.dt.date.astype(str).where(entry_date.notna())
    frame["entry_price"] = entry_price
    for horizon in ALPHA_HORIZONS:
        exit_date = _future_dates(prices.index, horizon)
        exit_price = close.shift(-horizon)
        benchmark_exit = _mapped_values(benchmark_close, exit_date)
        net = _net_return(exit_price, entry_price, costs)
        benchmark_return = (benchmark_exit / benchmark_entry - 1.0) * 100.0
        frame[f"exit_at_{horizon}d"] = exit_date.dt.date.astype(str).where(
            exit_date.notna()
        )
        frame[f"net_return_{horizon}d"] = net
        frame[f"benchmark_return_{horizon}d"] = benchmark_return
        frame[f"excess_return_{horizon}d"] = net - benchmark_return
        frame[f"max_drawdown_{horizon}d"] = (
            _future_min(prices["Low"], horizon) / entry_price - 1.0
        ) * 100.0
    frame["execution_version"] = ALPHA_EXECUTION_VERSION
    frame["costs_bps"] = costs.costs_bps
    return frame.reset_index(drop=True)


def _cross_sectional_features(panel):
    panel = panel.copy()
    panel["industry"] = panel["industry"].fillna("其他").replace("", "其他")
    by_date = panel.groupby("trade_date", sort=False)
    panel["market_up_ratio"] = by_date["return_1d"].transform(
        lambda values: (values > 0).mean() * 100.0
    )
    panel["market_avg_return"] = by_date["return_1d"].transform("mean")
    panel["market_median_return"] = by_date["return_1d"].transform("median")
    by_industry = panel.groupby(["trade_date", "industry"], sort=False)
    panel["industry_up_ratio"] = by_industry["return_1d"].transform(
        lambda values: (values > 0).mean() * 100.0
    )
    panel["industry_avg_return"] = by_industry["return_1d"].transform("mean")
    panel["industry_return_20d"] = by_industry["return_20d"].transform("median")
    panel["relative_return_20d"] = panel["return_20d"] - panel["industry_return_20d"]
    industry_return_60d = by_industry["return_60d"].transform("median")
    panel["relative_return_60d"] = panel["return_60d"] - industry_return_60d
    return panel


def _eligible_alpha_panel(panel, config, require_entry):
    eligible = (
        (panel["signal_price"] >= config.min_price)
        & (panel["turnover_20d_billion"] >= config.min_turnover_20d_billion)
        & (panel["average_volume_20d"] >= config.min_average_volume_20d)
        & (panel["return_1d"].abs() < config.max_abs_return_1d)
    )
    if require_entry:
        eligible &= panel["entry_price"].notna()
    return panel[eligible].copy()


def finalize_alpha_panel(frames, config):
    panel = pd.concat([frame for frame in frames if not frame.empty], ignore_index=True)
    if panel.empty:
        raise ValueError("No alpha-universe feature rows were generated")
    panel = panel[
        (panel["trade_date"] >= str(config.start_date))
        & (panel["trade_date"] <= str(config.end_date))
    ].copy()
    panel = _cross_sectional_features(panel)
    panel = _eligible_alpha_panel(panel, config, require_entry=True)
    required = list(ALPHA_FEATURES)
    for horizon in ALPHA_HORIZONS:
        required.extend(
            [
                f"net_return_{horizon}d",
                f"excess_return_{horizon}d",
                f"max_drawdown_{horizon}d",
            ]
        )
    panel = panel.dropna(subset=required)
    keep = [
        "trade_date",
        "code",
        "name",
        "industry",
        "market",
        "signal_price",
        "entry_at",
        "entry_price",
        "execution_version",
        "costs_bps",
        *ALPHA_FEATURES,
    ]
    for horizon in ALPHA_HORIZONS:
        keep.extend(
            [
                f"exit_at_{horizon}d",
                f"net_return_{horizon}d",
                f"benchmark_return_{horizon}d",
                f"excess_return_{horizon}d",
                f"max_drawdown_{horizon}d",
            ]
        )
    return panel[keep].sort_values(["trade_date", "code"], kind="stable")


def finalize_alpha_inference_panel(frames, config, trade_date=None):
    panel = pd.concat([frame for frame in frames if not frame.empty], ignore_index=True)
    if panel.empty:
        return pd.DataFrame()
    if trade_date is None:
        trade_date = str(panel["trade_date"].max())
    panel = panel[panel["trade_date"] == str(trade_date)].copy()
    if panel.empty:
        return panel
    panel = _cross_sectional_features(panel)
    panel = _eligible_alpha_panel(panel, config, require_entry=False)
    panel = panel.dropna(subset=list(ALPHA_FEATURES))
    keep = [
        "trade_date",
        "code",
        "name",
        "industry",
        "market",
        "signal_price",
        "execution_version",
        "costs_bps",
        *ALPHA_FEATURES,
    ]
    return panel[keep].sort_values(["trade_date", "code"], kind="stable")


def export_alpha_universe_dataset(
    config,
    output_path=DEFAULT_OUTPUT,
    universe_file="data/universe_history.csv",
    cache_dir="data/replay_cache/yahoo",
    refresh_cache=False,
    max_symbols=None,
    history_loader=download_replay_history,
    benchmark_loader=_download_benchmark,
):
    config.validate()
    universe, universe_source = load_replay_universe(
        universe_file=universe_file, max_symbols=max_symbols
    )
    start = pd.Timestamp(config.start_date) - pd.Timedelta(
        days=config.warmup_calendar_days
    )
    end = pd.Timestamp(config.end_date) + pd.Timedelta(
        days=config.future_calendar_days + 1
    )
    tickers = sorted(
        {
            f"{code}.{_market_suffix(market)}"
            for code in universe
            for market in universe.markets_for(code)
        }
    )
    loader_kwargs = {
        "cache_dir": cache_dir,
        "refresh_cache": refresh_cache,
    }
    if history_loader is download_replay_history:
        histories = history_loader(
            tickers, start, end, config.chunk_size, **loader_kwargs
        )
    else:
        histories = history_loader(tickers, start, end, config.chunk_size)
    histories, transfer_aliases = resolve_transfer_history_aliases(histories, universe)
    if benchmark_loader is _download_benchmark:
        benchmark = benchmark_loader(start, end, **loader_kwargs)
    else:
        benchmark = benchmark_loader(start, end)
    if benchmark is None or benchmark.empty:
        raise RuntimeError("Benchmark history is unavailable")

    frames = []
    for offset, code in enumerate(universe, start=1):
        history = _history_for_code(universe, histories, code)
        if history is None:
            continue
        frame = build_stock_feature_frame(code, history, benchmark, config)
        if not frame.empty:
            frames.append(frame)
        if offset % 100 == 0:
            print(f"Built alpha features for {offset}/{len(universe)} symbols", flush=True)
    panel = finalize_alpha_panel(frames, config)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(
        output_path,
        index=False,
        compression={"method": "gzip", "compresslevel": 9, "mtime": 0},
    )
    metadata = {
        "dataset_version": ALPHA_DATASET_VERSION,
        "execution_version": ALPHA_EXECUTION_VERSION,
        "config": asdict(config),
        "features": list(ALPHA_FEATURES),
        "horizons": list(ALPHA_HORIZONS),
        "rows": int(len(panel)),
        "trade_dates": int(panel["trade_date"].nunique()),
        "symbols": int(panel["code"].nunique()),
        "start_date": str(panel["trade_date"].min()),
        "end_date": str(panel["trade_date"].max()),
        "universe_source": universe_source,
        "universe_quality_status": universe.quality_status,
        "universe_partial_memberships": universe.partial_memberships,
        "available_tickers": int(len(histories)),
        "transfer_aliases": int(len(transfer_aliases)),
        "data_warnings": [
            "Yahoo historical prices may contain later revisions.",
            "The alpha dataset excludes fundamentals and news until point-in-time archives are available.",
            "Liquidity eligibility is calculated using information available at each decision close.",
        ],
        "output": output_path.name,
        "bytes": output_path.stat().st_size,
        "sha256": _sha256(output_path),
    }
    metadata_path = output_path.with_suffix(output_path.suffix + ".metadata.json")
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**metadata, "metadata": str(metadata_path)}


def main():
    parser = argparse.ArgumentParser(
        description="Build a point-in-time liquid-universe dataset independent of legacy rules."
    )
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--universe", default="data/universe_history.csv")
    parser.add_argument("--cache-dir", default="data/replay_cache/yahoo")
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--max-symbols", type=int)
    parser.add_argument("--min-turnover", type=float, default=1.0)
    args = parser.parse_args()
    result = export_alpha_universe_dataset(
        AlphaUniverseConfig(
            start_date=args.start,
            end_date=args.end,
            min_turnover_20d_billion=args.min_turnover,
        ),
        output_path=args.output,
        universe_file=args.universe,
        cache_dir=args.cache_dir,
        refresh_cache=args.refresh_cache,
        max_symbols=args.max_symbols,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
