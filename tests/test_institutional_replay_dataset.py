import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from database import get_connection, init_db
from institutional_replay_dataset import enrich_replay_training_dataset


TAIPEI_TZ = dt.timezone(dt.timedelta(hours=8))


def _known_at(trade_date):
    value = dt.date.fromisoformat(trade_date) + dt.timedelta(days=1)
    return dt.datetime.combine(
        value, dt.time(8, 30), tzinfo=TAIPEI_TZ
    ).isoformat()


def _seed_shard(path, include_error=False):
    dates = [
        stamp.date().isoformat()
        for stamp in pd.bdate_range(end="2025-02-04", periods=20)
    ]
    dates.append("2025-02-05")
    with get_connection(path) as conn:
        init_db(conn)
        for index, trade_date in enumerate(dates, start=1):
            value = 999 if trade_date == "2025-02-05" else index
            conn.execute(
                """
                INSERT INTO institutional_flow_daily (
                    trade_date, code, name, market,
                    foreign_buy_shares, foreign_sell_shares, foreign_net_shares,
                    trust_buy_shares, trust_sell_shares, trust_net_shares,
                    dealer_buy_shares, dealer_sell_shares, dealer_net_shares,
                    total_net_shares, known_at, source_name, source_url,
                    payload_sha256, fetched_at
                ) VALUES (?, '2330', 'TSMC', '上市', ?, 0, ?, ?, 0, ?, ?, 0, ?, ?, ?,
                          'TWSE T86', 'https://example.test', ?, ?)
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
                    _known_at(trade_date),
                    f"{index:064x}"[-64:],
                    "2025-02-06T10:00:00+08:00",
                ),
            )
            conn.execute(
                """
                INSERT INTO institutional_flow_fetches (
                    trade_date, market, status, report_date, row_count,
                    source_name, source_url, payload_sha256, fetched_at, error_text
                ) VALUES (?, '上市', 'available', ?, 1, 'TWSE T86',
                          'https://example.test', ?, '2025-02-06T10:00:00+08:00', NULL)
                """,
                (trade_date, trade_date, f"{index:064x}"[-64:]),
            )
        if include_error:
            conn.execute(
                """
                INSERT INTO institutional_flow_fetches (
                    trade_date, market, status, row_count, source_name,
                    source_url, fetched_at, error_text
                ) VALUES ('2025-01-02', '上櫃', 'error', 0, 'TPEx',
                          'https://example.test', '2025-01-03T08:00:00+08:00', 'failed')
                """
            )


class InstitutionalReplayDatasetTests(unittest.TestCase):
    def test_enrichment_uses_only_rows_known_before_the_decision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.csv.gz"
            shard = root / "institutional-flow-2025.db"
            output = root / "enriched.csv.gz"
            second = root / "enriched-second.csv.gz"
            pd.DataFrame(
                [
                    {
                        "feature_id": 1,
                        "trade_date": "2025-02-05",
                        "code": "2330",
                        "as_of": "2025-02-05T06:00:00+00:00",
                    }
                ]
            ).to_csv(base, index=False, compression="gzip")
            _seed_shard(shard)

            first_result = enrich_replay_training_dataset(
                base, [shard], output, allow_partial_shards=True
            )
            second_result = enrich_replay_training_dataset(
                base, [shard], second, allow_partial_shards=True
            )
            frame = pd.read_csv(output, dtype={"code": str})
            metadata = json.loads(
                Path(first_result["metadata"]).read_text(encoding="utf-8")
            )

            self.assertEqual(frame.iloc[0]["institutional_source_trade_date"], "2025-02-04")
            self.assertEqual(frame.iloc[0]["institutional_observations_20d"], 20)
            self.assertEqual(frame.iloc[0]["institutional_coverage_status"], "complete")
            self.assertEqual(frame.iloc[0]["foreign_net_shares_1d"], 20)
            self.assertNotEqual(frame.iloc[0]["foreign_net_shares_1d"], 999)
            self.assertEqual(first_result["sha256"], second_result["sha256"])
            self.assertEqual(metadata["complete_coverage_pct"], 100.0)
            self.assertTrue(metadata["same_day_flow_excluded"])
            self.assertFalse(metadata["missing_values_imputed_as_zero"])

    def test_strict_mode_rejects_incomplete_annual_shards(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.csv.gz"
            shard = root / "institutional-flow-2025.db"
            pd.DataFrame(
                [
                    {
                        "trade_date": "2025-02-05",
                        "code": "2330",
                        "as_of": "2025-02-05T06:00:00+00:00",
                    }
                ]
            ).to_csv(base, index=False, compression="gzip")
            _seed_shard(shard, include_error=True)

            with self.assertRaisesRegex(ValueError, "Institutional shards are incomplete"):
                enrich_replay_training_dataset(
                    base, [shard], root / "enriched.csv.gz"
                )


if __name__ == "__main__":
    unittest.main()
