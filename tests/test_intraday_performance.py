import datetime as dt
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from intraday_scanner import (
    TAIPEI_TZ,
    append_realtime_bars,
    build_liquid_coverage_universe,
    collect_realtime_prices,
    fetch_realtime_prices,
    fetch_yfinance_current_bars,
    get_projected_volume,
)
from market_monitor import build_market_snapshot


class IntradayPerformanceTests(unittest.TestCase):
    def test_opening_volume_projection_uses_a_fifteen_minute_floor(self):
        now = dt.datetime(2026, 7, 22, 9, 1, tzinfo=TAIPEI_TZ)

        projected = get_projected_volume(
            10,
            now=now,
            min_elapsed_minutes=15,
        )

        self.assertEqual(projected, 180)

    def test_liquid_coverage_universe_uses_previous_session_turnover(self):
        index = pd.bdate_range(end="2026-07-21", periods=20)
        liquid = pd.DataFrame(
            {
                "Close": [100.0] * 20,
                "Volume": [1_000_000] * 20,
            },
            index=index,
        )
        illiquid = pd.DataFrame(
            {
                "Close": [10.0] * 20,
                "Volume": [10_000] * 20,
            },
            index=index,
        )

        result = build_liquid_coverage_universe(
            {"1001.TW": liquid, "1002.TW": illiquid},
            min_turnover_twd=50_000_000,
            min_volume_shares=200_000,
            min_symbols=1,
        )

        self.assertEqual(result, ["1001.TW"])

    def test_liquid_coverage_universe_falls_back_to_top_turnover(self):
        index = pd.bdate_range(end="2026-07-21", periods=20)
        history = {
            "1001.TW": pd.DataFrame(
                {"Close": [20.0] * 20, "Volume": [100_000] * 20},
                index=index,
            ),
            "1002.TW": pd.DataFrame(
                {"Close": [40.0] * 20, "Volume": [100_000] * 20},
                index=index,
            ),
        }

        result = build_liquid_coverage_universe(
            history,
            min_turnover_twd=1_000_000_000,
            min_volume_shares=1_000_000,
            min_symbols=1,
        )

        self.assertEqual(result, ["1002.TW"])

    def test_realtime_bars_exclude_symbols_without_a_current_quote(self):
        index = pd.bdate_range(end="2026-07-21", periods=20)
        history = {
            ticker: pd.DataFrame(
                {
                    "Open": [99.0] * 20,
                    "High": [101.0] * 20,
                    "Low": [98.0] * 20,
                    "Close": [100.0] * 20,
                    "Volume": [1_000_000] * 20,
                },
                index=index,
            )
            for ticker in ("1001.TW", "1002.TW")
        }
        now = dt.datetime(2026, 7, 22, 9, 5, tzinfo=TAIPEI_TZ)

        fresh, fresh_codes = append_realtime_bars(
            history,
            {
                "1001": {
                    "Open": 101,
                    "High": 104,
                    "Low": 100,
                    "Close": 103,
                    "Volume": 500,
                }
            },
            {"1001.TW": "1001", "1002.TW": "1002"},
            now=now,
        )

        self.assertEqual(set(fresh), {"1001.TW"})
        self.assertEqual(fresh_codes, {"1001"})
        self.assertEqual(fresh["1001.TW"].index[-1], pd.Timestamp("2026-07-22"))
        self.assertEqual(fresh["1001.TW"]["Close"].iloc[-1], 103)

    def test_realtime_batches_run_with_bounded_concurrency(self):
        active = 0
        max_active = 0
        lock = threading.Lock()

        def fake_fetch(chunk, timeout=8):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return {
                "success": True,
                "realtime": {
                    "latest_trade_price": "100",
                    "open": "98",
                    "high": "101",
                    "low": "97",
                    "accumulate_trade_volume": "1200",
                },
            }

        with patch("intraday_scanner.twstock_realtime_get_with_timeout", side_effect=fake_fetch):
            result = fetch_realtime_prices(
                ["1001", "1002", "1003", "1004", "1005", "1006"],
                chunk_size=1,
                max_workers=3,
            )

        self.assertEqual(len(result), 6)
        self.assertGreaterEqual(max_active, 2)
        self.assertLessEqual(max_active, 3)

    def test_sparse_quotes_retry_only_missing_symbols(self):
        history_tickers = ["1001.TW", "1002.TW", "1003.TW"]
        yf_to_code = {
            "1001.TW": "1001",
            "1002.TW": "1002",
            "1003.TW": "1003",
        }
        first_quote = {
            "1001": {"Close": 100, "Open": 99, "High": 101, "Low": 98, "Volume": 1}
        }
        second_quote = {
            "1002": {"Close": 50, "Open": 49, "High": 51, "Low": 48, "Volume": 2}
        }

        with patch(
            "intraday_scanner.fetch_realtime_prices",
            side_effect=(first_quote, second_quote),
        ) as realtime, patch(
            "intraday_scanner.fetch_yfinance_current_bars",
            return_value={},
        ) as fallback, patch("intraday_scanner.time.sleep") as sleep:
            quotes, coverage, attempts = collect_realtime_prices(
                history_tickers,
                yf_to_code,
                max_attempts=3,
                retry_delay_seconds=5,
                sleep_fn=sleep,
            )

        self.assertEqual(set(quotes), {"1001", "1002"})
        self.assertAlmostEqual(coverage, 2 / 3)
        self.assertEqual(attempts, 2)
        self.assertEqual(realtime.call_args_list[0].args[0], ["1001", "1002", "1003"])
        self.assertEqual(realtime.call_args_list[1].args[0], ["1002", "1003"])
        fallback.assert_called_once()
        sleep.assert_called_once_with(5)

    def test_quote_coverage_can_use_the_liquid_subset(self):
        history_tickers = ["1001.TW", "1002.TW", "1003.TW"]
        yf_to_code = {
            "1001.TW": "1001",
            "1002.TW": "1002",
            "1003.TW": "1003",
        }
        quotes = {
            "1001": {"Close": 100, "Open": 99, "High": 101, "Low": 98, "Volume": 1},
            "1002": {"Close": 50, "Open": 49, "High": 51, "Low": 48, "Volume": 2},
        }

        with patch(
            "intraday_scanner.fetch_realtime_prices",
            return_value=quotes,
        ) as realtime, patch(
            "intraday_scanner.fetch_yfinance_current_bars",
            return_value={},
        ) as fallback:
            result, coverage, attempts = collect_realtime_prices(
                history_tickers,
                yf_to_code,
                coverage_tickers=["1001.TW", "1002.TW"],
                max_attempts=3,
                retry_delay_seconds=0,
            )

        self.assertEqual(set(result), {"1001", "1002"})
        self.assertEqual(coverage, 1.0)
        self.assertEqual(attempts, 1)
        realtime.assert_called_once()
        fallback.assert_not_called()

    def test_yfinance_fallback_aggregates_today_minute_bars(self):
        index = pd.to_datetime(
            ["2026-07-22T01:01:00Z", "2026-07-22T01:02:00Z"]
        )
        columns = pd.MultiIndex.from_product(
            [["Open", "High", "Low", "Close", "Volume"], ["6245.TWO"]]
        )
        raw = pd.DataFrame(
            [
                [80.0, 81.0, 79.5, 80.5, 100_000],
                [80.5, 82.0, 80.0, 81.5, 150_000],
            ],
            index=index,
            columns=columns,
        )
        now = dt.datetime(2026, 7, 22, 9, 5, tzinfo=TAIPEI_TZ)

        with patch("intraday_scanner.yf.download", return_value=raw) as download:
            result = fetch_yfinance_current_bars(
                ["6245.TWO"],
                {"6245.TWO": "6245"},
                now=now,
            )

        self.assertEqual(
            result["6245"],
            {
                "Open": 80.0,
                "High": 82.0,
                "Low": 79.5,
                "Close": 81.5,
                "Volume": 250.0,
            },
        )
        self.assertEqual(download.call_args.kwargs["period"], "1d")
        self.assertEqual(download.call_args.kwargs["interval"], "1m")

    def test_market_monitor_reuses_scanner_context_without_downloading(self):
        now = dt.datetime(2026, 7, 10, 10, 0, tzinfo=TAIPEI_TZ)
        index = pd.bdate_range(end="2026-07-09", periods=25)
        history = pd.DataFrame(
            {
                "Open": range(75, 100),
                "High": range(77, 102),
                "Low": range(73, 98),
                "Close": range(76, 101),
                "Volume": [1_000_000] * 25,
            },
            index=index,
        )
        context = {
            "captured_at": now,
            "codes": {
                "1234": SimpleNamespace(
                    group="半導體業",
                    market="上市",
                    name="測試公司",
                )
            },
            "yf_to_code": {"1234.TW": "1234"},
            "history": {"1234.TW": history},
            "realtime": {
                "1234": {
                    "Open": 101,
                    "High": 105,
                    "Low": 100,
                    "Close": 104,
                    "Volume": 1500,
                }
            },
        }

        with patch("market_monitor.batch_download_recent") as download, patch(
            "market_monitor.fetch_realtime_prices"
        ) as realtime:
            frame, industry, summary, captured_at = build_market_snapshot(
                market_context=context
            )

        download.assert_not_called()
        realtime.assert_not_called()
        self.assertEqual(captured_at, now)
        self.assertEqual(frame.iloc[0]["代號"], "1234")
        self.assertEqual(frame.iloc[0]["昨收"], 100)
        self.assertEqual(frame.iloc[0]["現價"], 104)
        self.assertFalse(industry.empty)
        self.assertIn("報價覆蓋率", summary["項目"].tolist())


if __name__ == "__main__":
    unittest.main()
