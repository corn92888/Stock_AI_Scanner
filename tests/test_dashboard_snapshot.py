import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from export_dashboard_snapshot import build_dashboard_snapshot, write_dashboard_snapshot


def _create_fixture(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE scan_runs (
            id INTEGER PRIMARY KEY, run_at TEXT, trade_date TEXT, mode TEXT,
            source TEXT, strategy_version TEXT, git_commit TEXT, report_path TEXT, notes TEXT
        );
        CREATE TABLE stock_signals (
            id INTEGER PRIMARY KEY, run_id INTEGER, trade_date TEXT, mode TEXT,
            strategy TEXT, code TEXT, name TEXT, industry TEXT
        );
        CREATE TABLE candidate_events (
            id INTEGER PRIMARY KEY, run_id INTEGER, signal_id INTEGER, code TEXT,
            name TEXT, industry TEXT, strategies_json TEXT, raw_rank INTEGER,
            score REAL, signal_price REAL, pct_change REAL, turnover_billion REAL,
            volume_ratio_5 REAL, intraday_position REAL, observation_price REAL,
            chase_limit REAL, stop_distance_pct REAL, tradable INTEGER,
            is_selected INTEGER, selection_rank INTEGER, selection_status TEXT,
            risk_flags_json TEXT, block_reasons_json TEXT, policy_version TEXT
        );
        CREATE TABLE backtest_results (
            id INTEGER PRIMARY KEY, signal_id INTEGER, matured_horizon INTEGER,
            outcome_status TEXT, net_return_1d REAL, net_return_3d REAL,
            net_return_5d REAL, excess_return_3d REAL, max_return_3d REAL,
            max_drawdown_3d REAL, success_t3 INTEGER, costs_bps REAL,
            entry_method TEXT, tested_at TEXT
        );
        CREATE TABLE backtest_runs (
            id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, status TEXT,
            config_json TEXT,
            signals_requested INTEGER, completed_count INTEGER, partial_count INTEGER,
            skipped_count INTEGER, error_text TEXT
        );
        INSERT INTO scan_runs VALUES (
            1, '2026-07-11T10:00:00+08:00', '2026-07-11', 'intraday',
            'test', 'v1', 'abc123', 'report.xlsx', '{"automation_slot":"open"}'
        );
        INSERT INTO stock_signals VALUES (
            1, 1, '2026-07-11', 'intraday', 'trend', '2330', '台積電', '半導體'
        );
        INSERT INTO candidate_events VALUES (
            1, 1, 1, '2330', '台積電', '半導體', '["trend"]', 1,
            81.5, 1000, 2.5, 80, 1.8, 0.7, 998, 1010, 2.0, 1, 1, 1,
            'selected', '[]', '[]', 'candidate-v1'
        );
        INSERT INTO backtest_results VALUES (
            1, 1, 3, 'partial', 1.0, 2.0, NULL, 1.2, 3.0, -1.0, 1, 30,
            'next_open', '2026-07-11T15:00:00+08:00'
        );
        """
    )
    conn.commit()
    conn.close()


class DashboardSnapshotTests(unittest.TestCase):
    def test_dashboard_snapshot_is_public_and_structured(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "scanner.db"
            output = Path(directory) / "dashboard.json"
            _create_fixture(database)

            snapshot = build_dashboard_snapshot(database)
            write_dashboard_snapshot(snapshot, [output])
            payload = json.loads(output.read_text(encoding="utf-8"))
            serialized = output.read_text(encoding="utf-8").lower()

            self.assertEqual(payload["schemaVersion"], "dashboard_v2")
            self.assertEqual(payload["overview"]["formalSelections"], 1)
            self.assertEqual(payload["overview"]["formalBacktestResults"], 1)
            self.assertEqual(payload["overview"]["formalMatureT3"], 1)
            self.assertEqual(payload["candidates"][0]["strategies"], ["trend"])
            self.assertTrue(payload["candidates"][0]["isSelected"])
            self.assertEqual(payload["performance"][0]["netReturn3d"], 2.0)
            self.assertTrue(payload["performance"][0]["isFormalSelection"])
            for private_key in ("email", "portfolio", "supabase", "private_code", "chat_id"):
                self.assertNotIn(private_key, serialized)
