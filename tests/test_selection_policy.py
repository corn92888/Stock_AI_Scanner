import tempfile
import unittest
from pathlib import Path

import pandas as pd

from database import (
    find_scan_run,
    get_connection,
    get_daily_candidate_state,
    init_db,
    save_candidate_events,
)
from selection_policy import (
    DEFAULT_SELECTION_POLICY,
    apply_selection_policy,
    candidate_event_records,
)


def candidate(
    code,
    score,
    industry,
    strategy="順勢突破",
    turnover=20.0,
    pct_change=2.0,
    volume_ratio=2.0,
    position=0.7,
    price=100.0,
    observation=95.0,
):
    return {
        "代號": code,
        "名稱": f"Stock {code}",
        "產業族群": industry,
        "策略": strategy,
        "策略數": 1,
        "分數": score,
        "現價": price,
        "漲跌幅": pct_change,
        "成交值(億)": turnover,
        "量比5": volume_ratio,
        "收盤位置": position,
        "隔日觀察價": observation,
        "追價上限": price * 1.02,
        "報價時間": "2026-07-10 10:00:00+08:00",
        "續漲型態": "隔日延續",
        "條件": strategy,
    }


class SelectionPolicyTests(unittest.TestCase):
    def test_duplicate_codes_merge_strategies_before_selection(self):
        rows = [
            candidate("2330", 100, "半導體業", "順勢突破"),
            candidate("2330", 90, "半導體業", "波段蓄勢"),
        ]
        result = apply_selection_policy(pd.DataFrame(rows))

        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["策略數"], 2)
        self.assertIn("順勢突破", result.iloc[0]["策略"])
        self.assertIn("波段蓄勢", result.iloc[0]["策略"])
        self.assertTrue(result.iloc[0]["政策入選"])

    def test_tradability_gates_record_all_block_reasons(self):
        row = candidate(
            "1234",
            100,
            "電子業",
            turnover=2.0,
            pct_change=9.7,
            volume_ratio=11.0,
            position=0.1,
            observation=80.0,
        )
        result = apply_selection_policy(pd.DataFrame([row]))
        reasons = result.iloc[0]["阻擋原因"]

        self.assertFalse(result.iloc[0]["可交易"])
        self.assertFalse(result.iloc[0]["政策入選"])
        self.assertIn("成交值低於5億", reasons)
        self.assertIn("接近漲跌停", reasons)
        self.assertIn("量比過熱", reasons)
        self.assertIn("日內回落過深", reasons)
        self.assertIn("觀察價距離過遠", reasons)

    def test_daily_top_three_and_one_per_industry(self):
        rows = [
            candidate("1001", 100, "半導體業"),
            candidate("1002", 95, "半導體業"),
            candidate("1003", 90, "通信網路業"),
            candidate("1004", 85, "金融保險業"),
            candidate("1005", 80, "電腦及週邊設備業"),
        ]
        result = apply_selection_policy(pd.DataFrame(rows))

        selected = result[result["政策入選"]]
        self.assertEqual(selected["代號"].tolist(), ["1001", "1003", "1004"])
        self.assertEqual(result.loc[result["代號"] == "1002", "政策狀態"].iloc[0], "industry_limit")
        self.assertEqual(result.loc[result["代號"] == "1005", "政策狀態"].iloc[0], "daily_limit")

    def test_prior_eligible_code_and_slots_are_not_reused(self):
        state = {
            "eligible_codes": {"1001"},
            "selected_count": 1,
            "selected_industry_counts": {"半導體業": 1},
        }
        rows = [
            candidate("1001", 100, "半導體業"),
            candidate("1002", 95, "半導體業"),
            candidate("1003", 90, "通信網路業"),
            candidate("1004", 85, "金融保險業"),
            candidate("1005", 80, "電腦及週邊設備業"),
        ]
        result = apply_selection_policy(pd.DataFrame(rows), daily_state=state)

        statuses = dict(zip(result["代號"], result["政策狀態"]))
        self.assertEqual(statuses["1001"], "duplicate_daily_eligible")
        self.assertEqual(statuses["1002"], "industry_limit")
        self.assertEqual(statuses["1003"], "selected")
        self.assertEqual(statuses["1004"], "selected")
        self.assertEqual(statuses["1005"], "daily_limit")

    def test_low_score_is_tradable_but_system_can_abstain(self):
        result = apply_selection_policy(
            pd.DataFrame([candidate("1001", 49, "半導體業")])
        )

        self.assertTrue(result.iloc[0]["可交易"])
        self.assertFalse(result.iloc[0]["每日首次合格"])
        self.assertFalse(result.iloc[0]["政策入選"])
        self.assertEqual(result.iloc[0]["政策狀態"], "score_below_threshold")


class CandidateEventDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "scanner.db"
        with get_connection(self.db_path) as conn:
            init_db(conn)
            cursor = conn.execute(
                """
                INSERT INTO scan_runs (
                    run_at, trade_date, mode, source, strategy_version, report_path
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "2026-07-10T10:00:00+08:00",
                    "2026-07-10",
                    "intraday",
                    "test",
                    "v1",
                    "Reports/盤中日報_2026-07-10_1000.xlsx",
                ),
            )
            self.run_id = cursor.lastrowid
            conn.execute(
                """
                INSERT INTO stock_signals (
                    run_id, trade_date, mode, strategy, code, name,
                    rank_order, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.run_id,
                    "2026-07-10",
                    "intraday",
                    "順勢突破",
                    "2330",
                    "台積電",
                    1,
                    "2026-07-10T10:00:00+08:00",
                ),
            )
            conn.commit()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_candidate_event_upsert_and_daily_state_are_idempotent(self):
        ranked = apply_selection_policy(
            pd.DataFrame([candidate("2330", 100, "半導體業")])
        )
        events = candidate_event_records(ranked)
        save_candidate_events(self.run_id, events, db_path=self.db_path)
        save_candidate_events(self.run_id, events, db_path=self.db_path)

        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count, signal_id FROM candidate_events"
            ).fetchone()
        self.assertEqual(row["count"], 1)
        self.assertIsNotNone(row["signal_id"])

        with get_connection(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO scan_runs (
                    run_at, trade_date, mode, source, strategy_version, report_path
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "2026-07-10T11:30:00+08:00",
                    "2026-07-10",
                    "intraday",
                    "test",
                    "v1",
                    "Reports/盤中日報_2026-07-10_1130.xlsx",
                ),
            )
            later_run_id = cursor.lastrowid
            conn.commit()

        state = get_daily_candidate_state(
            later_run_id,
            DEFAULT_SELECTION_POLICY.version,
            db_path=self.db_path,
        )
        self.assertEqual(state["eligible_codes"], {"2330"})
        self.assertEqual(state["selected_count"], 1)
        self.assertEqual(state["selected_industry_counts"], {"半導體業": 1})

    def test_scan_run_can_be_resolved_from_absolute_report_path(self):
        report = Path.cwd() / "Reports/盤中日報_2026-07-10_1000.xlsx"
        run = find_scan_run(report, db_path=self.db_path)
        self.assertEqual(run["id"], self.run_id)


if __name__ == "__main__":
    unittest.main()
