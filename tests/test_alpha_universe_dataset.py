import unittest

import numpy as np
import pandas as pd

from alpha_universe_dataset import (
    ALPHA_HORIZONS,
    AlphaUniverseConfig,
    build_stock_feature_frame,
    finalize_alpha_inference_panel,
    finalize_alpha_panel,
)
from backtest import BacktestConfig


def price_frame(seed=1, industry="Semiconductor", periods=360):
    rng = np.random.default_rng(seed)
    index = pd.bdate_range("2022-01-03", periods=periods)
    close = 80.0 * np.exp(np.cumsum(rng.normal(0.0005, 0.01, periods)))
    open_price = close * (1.0 + rng.normal(0.0, 0.002, periods))
    high = np.maximum(open_price, close) * (1.0 + rng.uniform(0.001, 0.01, periods))
    low = np.minimum(open_price, close) * (1.0 - rng.uniform(0.001, 0.01, periods))
    return pd.DataFrame(
        {
            "Open": open_price,
            "High": high,
            "Low": low,
            "Close": close,
            "Adj Close": close,
            "Volume": rng.integers(2_000_000, 6_000_000, periods),
            "industry": industry,
            "name": f"Stock {seed}",
            "market": "上市",
        },
        index=index,
    )


class AlphaUniverseDatasetTests(unittest.TestCase):
    def test_next_open_labels_and_costs_are_aligned(self):
        stock = price_frame(seed=4)
        benchmark = price_frame(seed=9).drop(
            columns=["industry", "name", "market"]
        )
        config = AlphaUniverseConfig(start_date="2022-10-01", end_date="2023-04-30")
        frame = build_stock_feature_frame("2330", stock, benchmark, config)

        position = 250
        trade_date = stock.index[position].date().isoformat()
        row = frame.loc[frame["trade_date"] == trade_date].iloc[0]
        costs = BacktestConfig()
        entry = float(stock["Open"].iloc[position + 1])
        exit_price = float(stock["Close"].iloc[position + 5])
        entry_cost = entry * (1 + costs.buy_fee_rate + costs.slippage_rate)
        exit_proceeds = exit_price * (
            1 - costs.sell_fee_rate - costs.sell_tax_rate - costs.slippage_rate
        )
        expected = (exit_proceeds / entry_cost - 1.0) * 100.0

        self.assertEqual(row["entry_at"], stock.index[position + 1].date().isoformat())
        self.assertAlmostEqual(row["entry_price"], entry)
        self.assertAlmostEqual(row["net_return_5d"], expected)
        self.assertAlmostEqual(
            row["return_1d"],
            (stock["Close"].iloc[position] / stock["Close"].iloc[position - 1] - 1)
            * 100,
        )

    def test_final_panel_is_full_universe_and_has_mature_labels(self):
        benchmark = price_frame(seed=11).drop(
            columns=["industry", "name", "market"]
        )
        config = AlphaUniverseConfig(
            start_date="2022-11-01",
            end_date="2023-03-31",
            min_turnover_20d_billion=0.1,
        )
        frames = [
            build_stock_feature_frame(
                "1001", price_frame(seed=1, industry="Semiconductor"), benchmark, config
            ),
            build_stock_feature_frame(
                "1002", price_frame(seed=2, industry="Shipping"), benchmark, config
            ),
        ]
        panel = finalize_alpha_panel(frames, config)

        self.assertEqual(set(panel["code"]), {"1001", "1002"})
        self.assertTrue((panel.groupby("trade_date")["code"].nunique() == 2).all())
        self.assertTrue(panel["market_up_ratio"].between(0, 100).all())
        for horizon in ALPHA_HORIZONS:
            self.assertFalse(panel[f"net_return_{horizon}d"].isna().any())
            self.assertFalse(panel[f"excess_return_{horizon}d"].isna().any())

    def test_inference_panel_keeps_latest_rows_without_future_labels(self):
        benchmark = price_frame(seed=21).drop(
            columns=["industry", "name", "market"]
        )
        latest_date = benchmark.index[-1].date().isoformat()
        config = AlphaUniverseConfig(
            start_date=latest_date,
            end_date=latest_date,
            min_turnover_20d_billion=0.1,
        )
        frames = [
            build_stock_feature_frame(
                "1001", price_frame(seed=1), benchmark, config
            ),
            build_stock_feature_frame(
                "1002", price_frame(seed=2, industry="Shipping"), benchmark, config
            ),
        ]

        panel = finalize_alpha_inference_panel(
            frames, config, trade_date=latest_date
        )

        self.assertEqual(set(panel["code"]), {"1001", "1002"})
        self.assertNotIn("net_return_10d", panel.columns)
        self.assertTrue((panel["trade_date"] == latest_date).all())


if __name__ == "__main__":
    unittest.main()
