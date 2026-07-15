import unittest

import pandas as pd

from execution_research import (
    ENTRY_METHODS,
    HORIZONS,
    calculate_execution_scenarios,
)


def _prices(start="2025-01-02", sessions=26, base=100.0):
    index = pd.bdate_range(start, periods=sessions)
    step = pd.Series(range(sessions), index=index, dtype=float)
    return pd.DataFrame(
        {
            "Open": base + step,
            "High": base + step + 2,
            "Low": base + step - 2,
            "Close": base + step + 1,
            "Volume": 1_000_000,
        },
        index=index,
    )


class ExecutionResearchTests(unittest.TestCase):
    def test_execution_scenarios_cover_locked_methods_and_horizons(self):
        prices = _prices()
        benchmark = _prices(base=20_000)
        candidate = {
            "trade_date": prices.index[0].date().isoformat(),
            "signal_price": 102.0,
        }

        scenarios = calculate_execution_scenarios(candidate, prices, benchmark)

        self.assertEqual(
            [item["entry_method"] for item in scenarios], list(ENTRY_METHODS)
        )
        by_method = {item["entry_method"]: item for item in scenarios}
        self.assertEqual(by_method["next_open"]["entry_price"], 101.0)
        self.assertEqual(by_method["next_ohlc4_proxy"]["entry_price"], 101.25)
        self.assertEqual(by_method["next_close"]["entry_price"], 102.0)
        self.assertEqual(by_method["pullback_2pct_3d"]["entry_price"], 99.96)
        self.assertEqual(
            set(map(int, by_method["next_open"]["labels"])), set(HORIZONS)
        )
        self.assertEqual(
            by_method["next_close"]["labels"]["1"]["exit_at"],
            prices.index[2].date().isoformat(),
        )
        self.assertAlmostEqual(
            by_method["next_open"]["labels"]["1"]["net_return"], 0.1992
        )

    def test_pullback_scenario_is_explicitly_skipped_when_limit_never_trades(self):
        prices = _prices(base=100.0)
        candidate = {
            "trade_date": prices.index[0].date().isoformat(),
            "signal_price": 80.0,
        }

        scenarios = calculate_execution_scenarios(candidate, prices, prices)
        pullback = next(
            item for item in scenarios if item["entry_method"] == "pullback_2pct_3d"
        )

        self.assertEqual(pullback["entry_status"], "skipped")
        self.assertEqual(pullback["skip_reason"], "pullback_not_filled")
        self.assertEqual(pullback["labels"], {})

    def test_scenarios_do_not_emit_unmatured_long_horizons(self):
        prices = _prices(sessions=7)
        candidate = {
            "trade_date": prices.index[0].date().isoformat(),
            "signal_price": 101.0,
        }

        scenarios = calculate_execution_scenarios(candidate, prices, prices)
        next_open = next(
            item for item in scenarios if item["entry_method"] == "next_open"
        )
        next_close = next(
            item for item in scenarios if item["entry_method"] == "next_close"
        )

        self.assertEqual(set(next_open["labels"]), {"1", "3", "5"})
        self.assertEqual(next_open["matured_horizon"], 5)
        self.assertEqual(set(next_close["labels"]), {"1", "3", "5"})
        self.assertEqual(next_close["matured_horizon"], 5)


if __name__ == "__main__":
    unittest.main()
