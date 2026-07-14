import datetime as dt
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from export_dashboard_snapshot import build_dashboard_snapshot
from global_market import build_market_snapshot, persist_global_market


def _daily(prices):
    index = pd.date_range(end="2026-07-13", periods=len(prices), freq="B", tz="UTC")
    return pd.DataFrame({"Close": prices, "Volume": [1000] * len(prices)}, index=index)


def _intraday(prices):
    index = pd.date_range("2026-07-14 01:00", periods=len(prices), freq="15min", tz="UTC")
    return pd.DataFrame({"Close": prices, "Volume": [100] * len(prices)}, index=index)


class GlobalMarketTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 7, 14, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
        base = [100 + index * 0.05 for index in range(70)]
        self.daily = {
            "NQ=F": _daily(base),
            "^VIX": _daily(base),
            "^TWII": _daily(base),
        }
        self.intraday = {
            "NQ=F": _intraday([base[-1] * 1.02, base[-1] * 1.03]),
            "^VIX": _intraday([base[-1] * 0.92, base[-1] * 0.90]),
            "^TWII": _intraday([base[-1], base[-1] * 1.005]),
        }

    def test_builds_directional_regime_and_labels_data_gaps(self):
        snapshot = build_market_snapshot(self.daily, self.intraday, now=self.now)
        instruments = {row["key"]: row for row in snapshot["instruments"]}

        self.assertGreater(snapshot["score"], 50)
        self.assertEqual(instruments["nasdaq_futures"]["dataStatus"], "fresh")
        self.assertEqual(instruments["taifex_night"]["dataStatus"], "not_connected")
        self.assertIn("taifex_night", snapshot["quality"]["missingKeys"])
        self.assertFalse(snapshot["quality"]["formalRankingEnabled"])
        self.assertGreater(len(snapshot["drivers"]), 0)

    def test_persists_point_in_time_history_for_dashboard_export(self):
        snapshot = build_market_snapshot(self.daily, self.intraday, now=self.now)
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "scanner.db"
            persist_global_market(snapshot, database)

            exported = build_dashboard_snapshot(database)
            market = exported["globalMarket"]

            self.assertEqual(market["snapshotAt"], snapshot["snapshotAt"])
            self.assertEqual(len(market["history"]), 1)
            self.assertEqual(len(market["instruments"]), len(snapshot["instruments"]))
            self.assertEqual(market["quality"]["status"], "fallback_delayed")


if __name__ == "__main__":
    unittest.main()
