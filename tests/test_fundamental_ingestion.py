import datetime as dt
import hashlib
import tempfile
import unittest
from pathlib import Path

from database import get_connection, init_db
from fundamental_ingestion import (
    SourcePayload,
    ingest_official_fundamentals,
    normalize_payloads,
)


class FakeProvider:
    def __init__(self, payloads, errors=None):
        self.payloads = payloads
        self.errors = errors or {}

    def fetch_all(self):
        return self.payloads, self.errors


def payload(key, records):
    return SourcePayload(
        key=key,
        url=f"https://official.test/{key}",
        records=tuple(records),
        sha256=hashlib.sha256(key.encode()).hexdigest(),
    )


def official_payloads():
    return {
        "twse_valuation": payload(
            "twse_valuation",
            [
                {
                    "Date": "20260807",
                    "Code": "2330",
                    "Name": "台積電",
                    "PEratio": "25.5",
                    "PBratio": "7.2",
                }
            ],
        ),
        "twse_revenue": payload(
            "twse_revenue",
            [
                {
                    "出表日期": "1150808",
                    "資料年月": "11507",
                    "公司代號": "2330",
                    "公司名稱": "台積電",
                    "營業收入-去年同月增減(%)": "18.4",
                    "營業收入-上月比較增減(%)": "2.1",
                }
            ],
        ),
        "twse_eps": payload(
            "twse_eps",
            [
                {
                    "出表日期": "1150809",
                    "年度": "115",
                    "季別": "2",
                    "公司代號": "2330",
                    "公司名稱": "台積電",
                    "基本每股盈餘(元)": "22.1",
                }
            ],
        ),
        "tpex_valuation": payload(
            "tpex_valuation",
            [
                {
                    "Date": "1150807",
                    "SecuritiesCompanyCode": "6488",
                    "CompanyName": "環球晶",
                    "PriceEarningRatio": "19.8",
                    "PriceBookRatio": "2.4",
                }
            ],
        ),
        "tpex_revenue": payload(
            "tpex_revenue",
            [
                {
                    "出表日期": "1150808",
                    "資料年月": "11507",
                    "公司代號": "6488",
                    "公司名稱": "環球晶",
                    "營業收入-去年同月增減(%)": "7.5",
                    "營業收入-上月比較增減(%)": "-1.2",
                }
            ],
        ),
        "tpex_eps": payload(
            "tpex_eps",
            [
                {
                    "出表日期": "1150809",
                    "年度": "115",
                    "季別": "2",
                    "公司代號": "6488",
                    "公司名稱": "環球晶",
                    "基本每股盈餘(元)": "15.3",
                }
            ],
        ),
    }


class FundamentalIngestionTests(unittest.TestCase):
    def test_official_payloads_are_normalized_with_point_in_time_lineage(self):
        now = dt.datetime(2026, 8, 10, 8, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
        rows = normalize_payloads(official_payloads(), now=now)

        self.assertEqual([row["code"] for row in rows], ["2330", "6488"])
        twse = rows[0]
        self.assertEqual(twse["market"], "TWSE")
        self.assertEqual(twse["valuation_date"], "2026-08-07")
        self.assertEqual(twse["revenue_period"], "2026-07")
        self.assertEqual(twse["eps_period"], "2026Q2")
        self.assertEqual(twse["eps_latest"], 22.1)
        self.assertIsNone(twse["eps_ttm"])
        self.assertEqual(twse["known_at"], "2026-08-10T08:00:00+08:00")
        self.assertEqual(twse["quality_flags"], [])

    def test_ingestion_is_idempotent_and_preserves_earliest_known_at(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "scanner.db"
            first = dt.datetime(2026, 8, 10, 8, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
            second = first + dt.timedelta(hours=2)
            provider = FakeProvider(official_payloads())

            result = ingest_official_fundamentals(db_path, provider=provider, now=first)
            ingest_official_fundamentals(db_path, provider=provider, now=second)

            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["observations"], 2)
            with get_connection(db_path) as conn:
                init_db(conn)
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM fundamental_observations").fetchone()[0],
                    2,
                )
                known_at = conn.execute(
                    "SELECT known_at FROM fundamental_observations WHERE code='2330'"
                ).fetchone()[0]
                runs = conn.execute(
                    "SELECT COUNT(*) FROM fundamental_ingestion_runs"
                ).fetchone()[0]
            self.assertEqual(known_at, "2026-08-10T08:00:00+08:00")
            self.assertEqual(runs, 1)

    def test_partial_sources_are_visible_in_status_and_quality_flags(self):
        now = dt.datetime(2026, 8, 10, 8, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
        limited = {"twse_valuation": official_payloads()["twse_valuation"]}
        with tempfile.TemporaryDirectory() as directory:
            result = ingest_official_fundamentals(
                Path(directory) / "scanner.db",
                provider=FakeProvider(limited, {"twse_revenue": "timeout"}),
                now=now,
            )
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["observations"], 1)
        self.assertIn("twse_revenue: timeout", result["warnings"])


if __name__ == "__main__":
    unittest.main()
