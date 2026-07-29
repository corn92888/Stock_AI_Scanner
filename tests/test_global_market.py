import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

from export_dashboard_snapshot import build_dashboard_snapshot
from global_market import (
    TwseOfficialCloseProvider,
    build_market_snapshot,
    persist_global_market,
)


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

    def test_twse_provider_parses_the_latest_official_close(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            {
                "stat": "OK",
                "fields": [
                    "日期",
                    "成交股數",
                    "成交金額",
                    "成交筆數",
                    "發行量加權股價指數",
                    "漲跌點數",
                ],
                "data": [
                    [
                        "115/07/13",
                        "1",
                        "1",
                        "1",
                        "43,634.19",
                        "-20.65",
                    ],
                    [
                        "115/07/14",
                        "1",
                        "1",
                        "1",
                        "41,603.36",
                        "-2,030.83",
                    ],
                ],
            }
        ).encode("utf-8")

        with patch("global_market.urllib.request.urlopen", return_value=response):
            result = TwseOfficialCloseProvider().fetch(dt.date(2026, 7, 14))

        taiex = result["taiex"]
        self.assertEqual(taiex["price"], 41603.36)
        self.assertAlmostEqual(taiex["previousClose"], 43634.19)
        self.assertAlmostEqual(taiex["pctChange"], -4.654218, places=5)
        self.assertEqual(taiex["sourceTier"], "official_close")

    def test_official_close_overrides_same_day_fallback_and_local_bias(self):
        now = dt.datetime(
            2026,
            7,
            14,
            15,
            0,
            tzinfo=dt.timezone(dt.timedelta(hours=8)),
        )
        snapshot = build_market_snapshot(
            self.daily,
            self.intraday,
            now=now,
            official_closes={
                "taiex": {
                    "marketAt": "2026-07-14T13:30:00+08:00",
                    "price": 41603.36,
                    "previousClose": 43634.19,
                    "pctChange": -4.654218,
                    "shockZ": -3.102812,
                    "sourceName": "TWSE official close",
                    "sourceTier": "official_close",
                    "dataStatus": "closed",
                    "sessionStatus": "closed",
                }
            },
        )
        instruments = {row["key"]: row for row in snapshot["instruments"]}

        self.assertEqual(instruments["taiex"]["price"], 41603.36)
        self.assertEqual(instruments["taiex"]["sourceTier"], "official_close")
        self.assertEqual(snapshot["quality"]["status"], "official_close_plus_fallback")
        self.assertEqual(snapshot["quality"]["taiwanLocalScore"], 26.0)
        self.assertLess(snapshot["taiwanBiasScore"], snapshot["score"])

    def test_older_official_close_does_not_replace_current_intraday_quote(self):
        snapshot = build_market_snapshot(
            self.daily,
            self.intraday,
            now=self.now,
            official_closes={
                "taiex": {
                    "marketAt": "2026-07-13T13:30:00+08:00",
                    "price": 40000,
                    "previousClose": 41000,
                    "pctChange": -2.44,
                    "shockZ": -1.63,
                    "sourceName": "TWSE official close",
                    "sourceTier": "official_close",
                    "dataStatus": "closed",
                    "sessionStatus": "closed",
                }
            },
        )
        instruments = {row["key"]: row for row in snapshot["instruments"]}

        self.assertNotEqual(instruments["taiex"]["price"], 40000)
        self.assertEqual(instruments["taiex"]["sourceTier"], "fallback_delayed")

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
