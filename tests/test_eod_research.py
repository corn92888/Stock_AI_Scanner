import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from database import get_connection, record_scan_results
from eod_research import save_eod_research_candidates


class EodResearchTests(unittest.TestCase):
    def test_eod_scanner_output_becomes_a_ranked_candidate_event(self):
        signal = pd.DataFrame(
            [
                {
                    "產業族群": "半導體",
                    "代號": "2330",
                    "名稱": "台積電",
                    "現價": 101.0,
                    "防守價": 95.0,
                    "漲跌幅": 1.0,
                    "成交量(張)": 10_000,
                    "條件": "test",
                }
            ]
        )
        market = pd.DataFrame(
            [
                {
                    "產業族群": "半導體",
                    "代號": "2330",
                    "名稱": "台積電",
                    "開盤": 100.0,
                    "最高": 102.0,
                    "最低": 99.0,
                    "現價": 101.0,
                    "漲跌幅": 1.0,
                    "目前成交量(張)": 10_000,
                    "成交值(億)": 100.0,
                    "量比5": 2.0,
                    "量比20": 1.5,
                    "收盤位置": 0.67,
                }
            ]
        )
        industry = pd.DataFrame(
            [
                {
                    "產業族群": "半導體",
                    "熱度分數": 8.0,
                    "上漲比例": 70.0,
                    "平均漲跌幅": 1.2,
                    "成交值合計_億": 500.0,
                }
            ]
        )
        summary = pd.DataFrame(
            [
                {"項目": "更新時間", "數值": "2026-07-14 14:00:00"},
                {"項目": "上漲比例", "數值": "60.00%"},
                {"項目": "平均漲跌幅", "數值": "0.80%"},
                {"項目": "中位數漲跌幅", "數值": "0.50%"},
            ]
        )

        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "scanner.db"
            recorded = record_scan_results(
                mode="eod",
                trade_date="2026-07-14",
                strategy_frames={"trend": signal},
                db_path=db_path,
            )
            with patch(
                "eod_research.build_market_snapshot",
                return_value=(market, industry, summary, None),
            ):
                result = save_eod_research_candidates(
                    recorded["run_id"],
                    {"trend": signal},
                    {},
                    {},
                    {},
                    captured_at=dt.datetime(
                        2026,
                        7,
                        14,
                        14,
                        tzinfo=dt.timezone(dt.timedelta(hours=8)),
                    ),
                    db_path=db_path,
                )
            with get_connection(db_path) as conn:
                candidate = conn.execute(
                    "SELECT code, signal_price, policy_version FROM candidate_events"
                ).fetchone()

        self.assertEqual(result["saved"], 1)
        self.assertEqual(candidate["code"], "2330")
        self.assertEqual(candidate["signal_price"], 101.0)
        self.assertEqual(candidate["policy_version"], "tradability_v1")


if __name__ == "__main__":
    unittest.main()
