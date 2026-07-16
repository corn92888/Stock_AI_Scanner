import sqlite3
import tempfile
import unittest
from pathlib import Path

from database import INSTITUTIONAL_FEATURE_VERSION, get_connection, init_db
from export_dashboard_snapshot import build_dashboard_snapshot
from institutional_flow import (
    InstitutionalDataError,
    build_institutional_feature_snapshots,
    institutional_features,
    parse_tpex_payload,
    parse_twse_payload,
)


FETCHED_AT = "2025-07-15T19:30:00+08:00"
SOURCE_URL = "https://example.test/report"
PAYLOAD_HASH = "a" * 64


class InstitutionalFlowTests(unittest.TestCase):
    def test_twse_parser_maps_official_columns_and_ignores_non_four_digit_codes(self):
        stock = [
            "2330",
            "台積電",
            "1,000",
            "400",
            "600",
            "10",
            "5",
            "5",
            "300",
            "100",
            "200",
            "75",
            "50",
            "20",
            "30",
            "80",
            "35",
            "45",
            "875",
        ]
        payload = {
            "stat": "OK",
            "date": "20250715",
            "fields": [str(index) for index in range(19)],
            "data": [stock, ["006208", "ETF", *(["0"] * 17)]],
        }

        report = parse_twse_payload(
            payload, "2025-07-15", SOURCE_URL, PAYLOAD_HASH, FETCHED_AT
        )

        self.assertEqual(report.market, "上市")
        self.assertEqual(len(report.records), 1)
        row = report.records[0]
        self.assertEqual(row["foreign_net_shares"], 600)
        self.assertEqual(row["trust_net_shares"], 200)
        self.assertEqual(row["dealer_buy_shares"], 130)
        self.assertEqual(row["dealer_sell_shares"], 55)
        self.assertEqual(row["dealer_net_shares"], 75)
        self.assertEqual(row["total_net_shares"], 875)
        self.assertEqual(row["known_at"], "2025-07-16T08:30:00+08:00")

    def test_tpex_parser_maps_aggregate_dealer_columns(self):
        stock = [
            "6488",
            "環球晶",
            "1,000",
            "500",
            "500",
            "0",
            "0",
            "0",
            "1,000",
            "500",
            "500",
            "200",
            "100",
            "100",
            "50",
            "25",
            "25",
            "70",
            "30",
            "40",
            "120",
            "55",
            "65",
            "665",
        ]
        payload = {
            "tables": [
                {
                    "date": "114/07/15",
                    "fields": [str(index) for index in range(24)],
                    "data": [stock],
                }
            ]
        }

        report = parse_tpex_payload(
            payload, "2025-07-15", SOURCE_URL, PAYLOAD_HASH, FETCHED_AT
        )

        row = report.records[0]
        self.assertEqual(row["foreign_net_shares"], 500)
        self.assertEqual(row["trust_net_shares"], 100)
        self.assertEqual(row["dealer_buy_shares"], 120)
        self.assertEqual(row["dealer_sell_shares"], 55)
        self.assertEqual(row["dealer_net_shares"], 65)
        self.assertEqual(row["total_net_shares"], 665)

    def test_parser_rejects_a_report_for_a_different_date(self):
        with self.assertRaises(InstitutionalDataError):
            parse_twse_payload(
                {"stat": "OK", "date": "20250714", "data": []},
                "2025-07-15",
                SOURCE_URL,
                PAYLOAD_HASH,
                FETCHED_AT,
            )

    def test_features_use_only_rows_known_before_the_decision(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "scanner.db"
            with get_connection(database) as conn:
                init_db(conn)
                run_id = conn.execute(
                    """
                    INSERT INTO scan_runs (
                        run_at, trade_date, mode, source, strategy_version
                    ) VALUES (
                        '2025-07-16T10:00:00+08:00', '2025-07-16',
                        'intraday', 'test', 'v1'
                    )
                    """
                ).lastrowid
                conn.execute(
                    """
                    INSERT INTO candidate_events (
                        run_id, code, as_of, strategies_json,
                        block_reasons_json, risk_flags_json, selection_status,
                        policy_version, policy_config_json, snapshot_json,
                        created_at, updated_at
                    ) VALUES (
                        ?, '2330', '2025-07-16T10:00:00+08:00', '[]',
                        '[]', '[]', 'selected', 'test_v1', '{}', '{}',
                        '2025-07-16T10:00:00+08:00',
                        '2025-07-16T10:00:00+08:00'
                    )
                    """,
                    (run_id,),
                )
                for trade_date, known_at, value in (
                    ("2025-07-14", "2025-07-15T08:30:00+08:00", 10),
                    ("2025-07-15", "2025-07-16T08:30:00+08:00", 20),
                    ("2025-07-16", "2025-07-17T08:30:00+08:00", 999),
                ):
                    conn.execute(
                        """
                        INSERT INTO institutional_flow_daily (
                            trade_date, code, name, market,
                            foreign_buy_shares, foreign_sell_shares,
                            foreign_net_shares, trust_buy_shares,
                            trust_sell_shares, trust_net_shares,
                            dealer_buy_shares, dealer_sell_shares,
                            dealer_net_shares, total_net_shares, known_at,
                            source_name, source_url, payload_sha256, fetched_at
                        ) VALUES (
                            ?, '2330', '台積電', '上市', ?, 0, ?, ?, 0, ?,
                            ?, 0, ?, ?, ?, 'TWSE T86', ?, ?, ?
                        )
                        """,
                        (
                            trade_date,
                            value,
                            value,
                            value,
                            value,
                            value,
                            value,
                            value * 3,
                            known_at,
                            SOURCE_URL,
                            PAYLOAD_HASH,
                            FETCHED_AT,
                        ),
                    )

            result = build_institutional_feature_snapshots(
                database, scope="latest-run"
            )
            self.assertEqual(result["built"], 1)
            conn = sqlite3.connect(database)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM institutional_feature_snapshots"
            ).fetchone()
            conn.close()

            self.assertEqual(row["feature_version"], INSTITUTIONAL_FEATURE_VERSION)
            self.assertEqual(row["source_trade_date"], "2025-07-15")
            self.assertEqual(row["observations_20d"], 2)
            self.assertEqual(row["foreign_net_shares_1d"], 20)
            self.assertEqual(row["foreign_net_shares_5d"], 30)
            self.assertNotEqual(row["foreign_net_shares_1d"], 999)

            snapshot = build_dashboard_snapshot(database)["institutionalFlow"]
            self.assertEqual(snapshot["latestTradeDate"], "2025-07-16")
            self.assertEqual(snapshot["featureSnapshots"], 1)
            self.assertEqual(snapshot["candidateTargets"], 1)
            self.assertFalse(snapshot["quality"]["formalRankingEnabled"])

    def test_signed_streak_preserves_buy_and_sell_direction(self):
        rows = []
        for value in (-3, -2, 1, 1, 1):
            rows.append(
                {
                    "trade_date": "2025-07-15",
                    "known_at": "2025-07-16T08:30:00+08:00",
                    "foreign_net_shares": value,
                    "trust_net_shares": value,
                    "dealer_net_shares": value,
                    "total_net_shares": value * 3,
                    "payload_sha256": PAYLOAD_HASH,
                    "source_name": "test",
                }
            )
        features = institutional_features(rows)
        self.assertEqual(features["foreign_streak_days"], -2)
        self.assertEqual(features["total_streak_days"], -2)


if __name__ == "__main__":
    unittest.main()
