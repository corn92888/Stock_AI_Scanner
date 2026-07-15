import json
import tempfile
import unittest
from pathlib import Path

from historical_replay import load_replay_universe
from historical_universe import (
    build_universe_history,
    parse_official_date,
    write_universe_artifacts,
)


def _official_payloads():
    return {
        "twse_current": [
            {
                "公司代號": "2330",
                "公司簡稱": "台積電",
                "產業別": "24",
                "上市日期": "19940905",
            },
            {
                "公司代號": "5678",
                "公司簡稱": "轉板公司",
                "產業別": "28",
                "上市日期": "20230103",
            },
        ],
        "twse_listings": {
            "fields": ["公司代號", "公司簡稱", "股票上市買賣日期"],
            "data": [
                ["9999", "已下市", "110.02.01"],
                ["5678", "轉板公司", "112.01.03"],
            ],
        },
        "twse_delisted": {
            "data": [
                ["110/03/01", "已下市", "9999"],
                ["111/03/01", "早期掛牌", "8888"],
            ]
        },
        "tpex_current": [
            {
                "SecuritiesCompanyCode": "1234",
                "CompanyAbbreviation": "上櫃公司",
                "SecuritiesIndustryCode": "33",
                "DateOfListing": "20180808",
            }
        ],
        "tpex_listings": {
            "tables": [
                {
                    "fields": ["股票代號", "公司名稱", "上櫃日期"],
                    "data": [["5678", "轉板公司", "109/01/02"]],
                }
            ]
        },
        "tpex_delisted": {
            "tables": [
                {
                    "fields": ["股票代號", "公司名稱", "終止上櫃日期", "終止上櫃原因"],
                    "data": [["5678", "轉板公司", "112-01-03", "轉上市"]],
                }
            ]
        },
    }


class HistoricalUniverseTests(unittest.TestCase):
    def test_parses_roc_and_gregorian_official_dates(self):
        self.assertEqual(parse_official_date("1150714").isoformat(), "2026-07-14")
        self.assertEqual(parse_official_date("0990101").isoformat(), "2010-01-01")
        self.assertEqual(parse_official_date("110.02.01").isoformat(), "2021-02-01")
        self.assertEqual(parse_official_date("2023-01-03").isoformat(), "2023-01-03")

    def test_builds_transfer_and_marks_unknown_historical_start_partial(self):
        frame, metadata = build_universe_history(
            "2021-01-01", "2025-12-31", _official_payloads()
        )

        self.assertEqual(len(frame), 6)
        self.assertEqual(metadata["unique_symbols"], 5)
        self.assertEqual(metadata["transfer_codes"], ["5678"])
        self.assertEqual(metadata["membership_quality"]["status"], "partial")
        self.assertEqual(
            metadata["membership_quality"]["partial_start_intervals"], 1
        )
        partial = frame[frame["code"] == "8888"].iloc[0]
        self.assertEqual(partial["listed_on"], "2021-01-01")
        self.assertEqual(partial["membership_quality"], "partial_start")

    def test_rejects_an_empty_official_source_instead_of_building_partial_data(self):
        payloads = _official_payloads()
        payloads["tpex_current"] = []

        with self.assertRaisesRegex(RuntimeError, "tpex_current"):
            build_universe_history("2021-01-01", "2025-12-31", payloads)

    def test_merges_current_and_scheduled_delisting_for_same_listing(self):
        payloads = _official_payloads()
        payloads["tpex_current"].append(
            {
                "SecuritiesCompanyCode": "5236",
                "CompanyAbbreviation": "凌陽創新",
                "SecuritiesIndustryCode": "24",
                "DateOfListing": "20210729",
            }
        )
        payloads["tpex_listings"]["tables"][0]["data"].append(
            ["5236", "凌陽創新", "110/07/29"]
        )
        payloads["tpex_delisted"]["tables"][0]["data"].append(
            ["5236", "凌陽創新科技股份有限公司", "115-07-16", "合併終止上櫃"]
        )

        frame, metadata = build_universe_history(
            "2021-01-01", "2026-07-15", payloads
        )

        membership = frame[frame["code"] == "5236"].iloc[0]
        self.assertEqual(len(frame[frame["code"] == "5236"]), 1)
        self.assertEqual(membership["name"], "凌陽創新")
        self.assertEqual(membership["industry"], "半導體業")
        self.assertEqual(membership["delisted_on"], "2026-07-16")
        self.assertEqual(metadata["normalization"]["adjustment_count"], 1)
        self.assertEqual(
            metadata["normalization"]["adjustments"][0]["code"], "5236"
        )

    def test_replay_loader_switches_market_at_the_official_transfer_date(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "universe_history.csv"
            frame, metadata = build_universe_history(
                "2021-01-01", "2025-12-31", _official_payloads()
            )
            write_universe_artifacts(frame, metadata, output)

            universe, source = load_replay_universe(universe_file=output)

            self.assertEqual(
                source, "point_in_time_intervals_csv:universe_history.csv"
            )
            self.assertEqual(universe.quality_status, "partial")
            self.assertEqual(universe.partial_memberships, 1)
            self.assertEqual(universe.stock_on("5678", "2023-01-02").market, "上櫃")
            self.assertEqual(universe.stock_on("5678", "2023-01-03").market, "上市")
            self.assertIsNone(universe.stock_on("9999", "2021-03-01"))

            scoped, _ = load_replay_universe(
                universe_file=output, codes=["5678"]
            )
            self.assertEqual(scoped.quality_status, "verified")
            self.assertEqual(scoped.partial_memberships, 0)

    def test_replay_loader_rejects_a_tampered_official_universe(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "universe_history.csv"
            frame, metadata = build_universe_history(
                "2021-01-01", "2025-12-31", _official_payloads()
            )
            write_universe_artifacts(frame, metadata, output)
            output.write_text(output.read_text(encoding="utf-8") + "\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "hash does not match"):
                load_replay_universe(universe_file=output)

    def test_metadata_is_valid_json_and_records_the_data_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "universe_history.csv"
            frame, metadata = build_universe_history(
                "2021-01-01", "2025-12-31", _official_payloads()
            )
            document = write_universe_artifacts(frame, metadata, output)
            saved = json.loads(
                output.with_suffix(".metadata.json").read_text(encoding="utf-8")
            )

            self.assertEqual(saved["data_sha256"], document["data_sha256"])
            self.assertEqual(saved["schema_version"], "official_point_in_time_universe_v1")


if __name__ == "__main__":
    unittest.main()
