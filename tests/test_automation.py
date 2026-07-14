import datetime as dt
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation_guard import (
    INTRADAY_SLOTS,
    TAIPEI_TZ,
    evaluate_intraday_run,
    find_intraday_slot,
    find_scheduled_slot,
    slot_already_completed,
)
from intraday_analysis_report import (
    generate_intraday_analysis_report,
    validate_report_freshness,
)
from intraday_scanner import is_intraday_scan_window, run_intraday_scanner


class AutomationGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "scanner.db"
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            CREATE TABLE scan_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                mode TEXT NOT NULL,
                notes TEXT
            )
            """
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_half_hour_slots_use_taipei_time_and_skip_weekends(self):
        morning = dt.datetime(2026, 7, 10, 10, 5, tzinfo=TAIPEI_TZ)
        weekend = dt.datetime(2026, 7, 11, 10, 0, tzinfo=TAIPEI_TZ)
        outside = dt.datetime(2026, 7, 10, 8, 59, tzinfo=TAIPEI_TZ)

        self.assertEqual(find_intraday_slot(morning).name, "10:00")
        self.assertIsNone(find_intraday_slot(weekend))
        self.assertIsNone(find_intraday_slot(outside))
        self.assertEqual(
            [slot.name for slot in INTRADAY_SLOTS],
            ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00", "13:30"],
        )

    def test_scheduled_cron_preserves_slot_when_github_starts_late(self):
        now = dt.datetime(2026, 7, 10, 9, 22, tzinfo=TAIPEI_TZ)
        decision = evaluate_intraday_run(
            now=now,
            db_path=self.db_path,
            scheduled_cron="7 1 * * 1-5",
        )

        self.assertTrue(decision["run"])
        self.assertEqual(decision["slot"], "09:00")
        self.assertEqual(find_scheduled_slot("37 5 * * 1-5").name, "13:30")

    def test_completed_slot_is_detected_from_structured_notes(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            INSERT INTO scan_runs (run_at, trade_date, mode, notes)
            VALUES (?, ?, ?, ?)
            """,
            (
                "2026-07-10T09:42:00+08:00",
                "2026-07-10",
                "intraday",
                json.dumps({"automation_slot": "09:30"}),
            ),
        )
        conn.commit()
        conn.close()

        now = dt.datetime(2026, 7, 10, 9, 45, tzinfo=TAIPEI_TZ)
        decision = evaluate_intraday_run(now=now, db_path=self.db_path)
        self.assertFalse(decision["run"])
        self.assertEqual(decision["reason"], "slot_already_completed")
        self.assertTrue(
            slot_already_completed(
                self.db_path,
                "2026-07-10",
                find_intraday_slot(now),
            )
        )

    def test_manual_retry_can_ignore_existing_slot(self):
        now = dt.datetime(2026, 7, 10, 11, 30, tzinfo=TAIPEI_TZ)
        decision = evaluate_intraday_run(
            now=now,
            db_path=self.db_path,
            ignore_existing=True,
        )
        self.assertTrue(decision["run"])
        self.assertEqual(decision["slot"], "11:30")


class IntradaySafetyTests(unittest.TestCase):
    def test_scheduled_closing_slot_allows_github_queue_delay(self):
        delayed = dt.datetime(2026, 7, 10, 13, 48, tzinfo=TAIPEI_TZ)
        expired = dt.datetime(2026, 7, 10, 14, 11, tzinfo=TAIPEI_TZ)

        self.assertTrue(is_intraday_scan_window(delayed, "13:30"))
        self.assertFalse(is_intraday_scan_window(expired, "13:30"))

    def test_scanner_skips_before_downloading_outside_market_hours(self):
        outside = dt.datetime(2026, 7, 10, 15, 0, tzinfo=TAIPEI_TZ)
        with patch("intraday_scanner.batch_download") as download:
            result = run_intraday_scanner(send_telegram=False, now=outside)
        download.assert_not_called()
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "outside_market_hours")

    def test_report_pipeline_does_not_run_market_monitor_after_scanner_skip(self):
        scanner_result = {
            "status": "skipped",
            "reason": "outside_market_hours",
            "message": "outside market",
            "report_path": "",
        }
        with patch(
            "intraday_scanner.run_intraday_scanner",
            return_value=scanner_result,
        ), patch("market_monitor.run_market_monitor") as monitor:
            result = generate_intraday_analysis_report(
                run_scanner=True,
                run_market_monitor=True,
                send_telegram=False,
            )
        monitor.assert_not_called()
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["scan_path"], "")

    def test_report_pipeline_passes_scanner_market_context_to_monitor(self):
        market_context = {"realtime": {"2330": {"Close": 100}}}
        scanner_result = {
            "status": "completed",
            "reason": "",
            "message": "completed",
            "report_path": "Reports/盤中日報_2026-07-10_1000.xlsx",
            "market_context": market_context,
        }
        with patch(
            "intraday_scanner.run_intraday_scanner",
            return_value=scanner_result,
        ), patch("market_monitor.run_market_monitor", return_value=None) as monitor:
            with self.assertRaisesRegex(RuntimeError, "全市場監控沒有產生"):
                generate_intraday_analysis_report(
                    run_scanner=True,
                    run_market_monitor=True,
                    send_telegram=False,
                )

        monitor.assert_called_once_with(
            send_telegram=False,
            market_context=market_context,
        )

    def test_report_pair_rejects_different_dates_or_stale_skew(self):
        with self.assertRaisesRegex(RuntimeError, "日期"):
            validate_report_freshness(
                "Reports/盤中日報_2026-07-09_1000.xlsx",
                "Reports/市場監控_2026-07-10_1000.xlsx",
            )
        with self.assertRaisesRegex(RuntimeError, "相差 60 分鐘"):
            validate_report_freshness(
                "Reports/盤中日報_2026-07-10_1000.xlsx",
                "Reports/市場監控_2026-07-10_1100.xlsx",
            )


if __name__ == "__main__":
    unittest.main()
