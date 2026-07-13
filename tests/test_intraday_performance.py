import datetime as dt
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from intraday_scanner import TAIPEI_TZ, fetch_realtime_prices
from market_monitor import build_market_snapshot


class IntradayPerformanceTests(unittest.TestCase):
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
