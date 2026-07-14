import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from database import get_connection, init_db
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
            self.assertEqual(payload["overview"]["maturePredictionOutcomes"], 0)
            self.assertEqual(payload["overview"]["prospectivePredictions"], 0)
            self.assertEqual(payload["overview"]["candidateOutcomes"], 0)
            self.assertEqual(payload["overview"]["paperAccounts"], 0)
            self.assertEqual(payload["paperAccounts"], [])
            self.assertEqual(payload["researchExperiments"], [])
            self.assertEqual(payload["modelChallengers"], [])
            self.assertEqual(payload["globalMarket"]["quality"]["status"], "unavailable")
            self.assertFalse(payload["globalMarket"]["quality"]["formalRankingEnabled"])
            self.assertEqual(payload["researchQuality"]["matureRejectedOutcomes"], 0)
            self.assertIsNone(payload["researchQuality"]["selectionNetLift3d"])
            self.assertEqual(payload["candidates"][0]["strategies"], ["trend"])
            self.assertTrue(payload["candidates"][0]["isSelected"])
            self.assertEqual(payload["performance"][0]["netReturn3d"], 2.0)
            self.assertTrue(payload["performance"][0]["isFormalSelection"])
            for private_key in ("email", "portfolio", "supabase", "private_code", "chat_id"):
                self.assertNotIn(private_key, serialized)

    def test_snapshot_exports_paper_account_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "scanner.db"
            with get_connection(database) as conn:
                init_db(conn)
                conn.execute(
                    """
                    INSERT INTO scan_runs (
                        run_at, trade_date, mode, source, strategy_version
                    ) VALUES ('2026-07-13T14:00:00+08:00', '2026-07-13',
                              'eod', 'test', 'v1')
                    """
                )
                account_id = conn.execute(
                    """
                    INSERT INTO paper_accounts (
                        account_key, name, strategy_kind, evidence_mode,
                        policy_version, execution_version, starting_cash,
                        cash, equity, total_return_pct, max_drawdown_pct,
                        closed_trades, winning_trades, open_positions,
                        pending_orders, skipped_orders, first_signal_at,
                        status, config_json,
                        created_at, updated_at
                    ) VALUES (
                        'ai_shadow_v1', 'AI Shadow', 'ai', 'prospective_only',
                        'paper_v1', 'execution_v1', 1000000, 990000, 1010000,
                        1, -2, 10, 6, 1, 2, 3,
                        '2026-07-13T14:00:00+08:00', 'shadow', '{}',
                        '2026-07-13T14:00:00+08:00',
                        '2026-07-13T14:00:00+08:00'
                    )
                    """
                ).lastrowid
                conn.execute(
                    """
                    INSERT INTO paper_equity_snapshots (
                        account_id, as_of, cash, market_value, equity,
                        total_return_pct, peak_equity, drawdown_pct,
                        open_positions, closed_trades, created_at
                    ) VALUES (?, '2026-07-13', 990000, 20000, 1010000,
                              1, 1020000, -0.98, 1, 10,
                              '2026-07-13T14:00:00+08:00')
                    """,
                    (account_id,),
                )

            snapshot = build_dashboard_snapshot(database)
            self.assertEqual(snapshot["overview"]["paperAccounts"], 1)
            self.assertEqual(snapshot["overview"]["paperProspectiveClosedTrades"], 10)
            self.assertEqual(snapshot["paperAccounts"][0]["accountKey"], "ai_shadow_v1")
            self.assertEqual(snapshot["paperAccounts"][0]["winRate"], 60)
            self.assertEqual(snapshot["paperAccounts"][0]["comparisonStartAt"], "2026-07-13")
            self.assertEqual(snapshot["paperAccounts"][0]["comparisonReturnPct"], 0)
            self.assertEqual(snapshot["paperEquity"][0]["equity"], 1010000)

    def test_snapshot_exports_latest_strategy_experiment_evaluation(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "scanner.db"
            with get_connection(database) as conn:
                init_db(conn)
                experiment_id = conn.execute(
                    """
                    INSERT INTO research_experiments (
                        experiment_key, name, hypothesis, strategy_family,
                        execution_version, objective, status, config_json,
                        created_at, updated_at
                    ) VALUES (
                        'trend_v2', 'Trend v2', 'test trend', 'trend',
                        'execution_v2', 'after_cost_excess_return', 'candidate',
                        '{}', '2026-07-14T14:00:00+08:00',
                        '2026-07-14T14:00:00+08:00'
                    )
                    """
                ).lastrowid
                conn.execute(
                    """
                    INSERT INTO experiment_evaluations (
                        experiment_id, evaluation_version, evaluated_at,
                        sample_start, sample_end, trade_dates, trades, folds,
                        mean_net_return, mean_excess_return, positive_rate,
                        annualized_sharpe, probabilistic_sharpe, max_drawdown,
                        profitable_fold_rate, qualified,
                        rejection_reasons_json, metrics_json
                    ) VALUES (
                        ?, 'eval_v1', '2026-07-14T14:10:00+08:00',
                        '2026-01-01', '2026-07-14', 120, 300, 5,
                        1.2, 0.7, 58, 1.4, 0.96, -8.5, 0.8, 1, '[]', '{}'
                    )
                    """,
                    (experiment_id,),
                )

            experiment = build_dashboard_snapshot(database)[
                "researchExperiments"
            ][0]
            self.assertEqual(experiment["experimentKey"], "trend_v2")
            self.assertEqual(experiment["meanExcessReturn"], 0.7)
            self.assertTrue(experiment["qualified"])
            self.assertEqual(experiment["rejectionReasons"], [])

    def test_snapshot_exports_model_challenger_governance(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "scanner.db"
            with get_connection(database) as conn:
                init_db(conn)
                conn.execute(
                    """
                    INSERT INTO model_challenger_evaluations (
                        model_version, evaluated_at, status, oof_trade_dates,
                        oof_candidates, challenger_trades, champion_trades,
                        challenger_mean_net_return, challenger_mean_excess_return,
                        champion_mean_net_return, champion_mean_excess_return,
                        net_return_lift, excess_return_lift,
                        challenger_max_drawdown, profitable_fold_rate, qualified,
                        rejection_reasons_json, metrics_json, created_at
                    ) VALUES (
                        'model-v2', '2026-07-14T15:00:00+08:00', 'shadow', 40,
                        200, 60, 45, 0.8, 0.3, 0.5, 0.1, 0.3, 0.2,
                        -4.0, 0.75, 0,
                        '["challenger_does_not_beat_champion"]', '{}',
                        '2026-07-14T15:00:00+08:00'
                    )
                    """
                )
                conn.commit()

            challenger = build_dashboard_snapshot(database)["modelChallengers"][0]
            self.assertEqual(challenger["modelVersion"], "model-v2")
            self.assertEqual(challenger["oofTradeDates"], 40)
            self.assertFalse(challenger["qualified"])
            self.assertEqual(
                challenger["rejectionReasons"],
                ["challenger_does_not_beat_champion"],
            )

    def test_feature_coverage_counts_candidates_not_feature_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "scanner.db"
            with get_connection(database) as conn:
                init_db(conn)
                conn.execute(
                    """
                    INSERT INTO scan_runs (
                        id, run_at, trade_date, mode, source, strategy_version
                    ) VALUES (1, '2026-07-14T10:00:00+08:00', '2026-07-14',
                              'intraday', 'test', 'v1')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO candidate_events (
                        run_id, code, name, as_of, strategies_json,
                        strategy_count, tradable, block_reasons_json,
                        risk_flags_json, is_first_eligible_event, is_selected,
                        selection_status, policy_version, policy_config_json,
                        snapshot_json, created_at, updated_at
                    ) VALUES (
                        1, '2330', '台積電', '2026-07-14T10:00:00+08:00',
                        '["trend"]', 1, 1, '[]', '[]', 1, 1, 'selected',
                        'test-v1', '{}', '{}',
                        '2026-07-14T10:00:00+08:00',
                        '2026-07-14T10:00:00+08:00'
                    )
                    """
                )
                conn.executemany(
                    """
                    INSERT INTO feature_snapshots (
                        run_id, code, as_of, feature_version, created_at
                    ) VALUES (1, '2330', '2026-07-14T10:00:00+08:00', ?,
                              '2026-07-14T10:00:00+08:00')
                    """,
                    [("features-v1",), ("features-v2",)],
                )
                conn.commit()

            overview = build_dashboard_snapshot(database)["overview"]
            self.assertEqual(overview["candidateEvents"], 1)
            self.assertEqual(overview["featureSnapshots"], 1)
