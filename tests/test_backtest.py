import sqlite3
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from backtest import (
    BacktestConfig,
    _net_return,
    _normalize_price_frame,
    calculate_signal_result,
    load_pending_signals,
)
from database import get_connection, init_db, save_backtest_result


def make_prices(periods, start="2026-01-02", close_step=2.0, low_offset=-1.0):
    index = pd.bdate_range(start, periods=periods)
    closes = [100.0 + i * close_step for i in range(periods)]
    return pd.DataFrame(
        {
            "Open": [100.0 + i * close_step for i in range(periods)],
            "High": [value + 1.0 for value in closes],
            "Low": [value + low_offset for value in closes],
            "Close": closes,
            "Adj Close": closes,
            "Volume": [1_000_000] * periods,
        },
        index=index,
    )


class BacktestCalculationTests(unittest.TestCase):
    def setUp(self):
        self.signal = {
            "id": 1,
            "trade_date": "2026-01-02",
            "mode": "intraday",
            "strategy": "trend",
            "code": "2330",
            "name": "TSMC",
            "stop_loss": 95.0,
        }
        self.zero_cost_config = BacktestConfig(
            buy_fee_rate=0.0,
            sell_fee_rate=0.0,
            sell_tax_rate=0.0,
            slippage_rate=0.0,
        )

    def test_cost_model_deducts_round_trip_costs(self):
        config = BacktestConfig()
        actual = _net_return(110.0, 100.0, config)
        entry_cost = 100.0 * (1 + config.buy_fee_rate + config.slippage_rate)
        exit_proceeds = 110.0 * (
            1 - config.sell_fee_rate - config.sell_tax_rate - config.slippage_rate
        )
        expected = round((exit_proceeds - entry_cost) / entry_cost * 100, 4)
        self.assertEqual(actual, expected)
        self.assertLess(actual, 10.0)

    def test_t3_success_uses_excess_return_and_drawdown(self):
        prices = make_prices(22)
        benchmark = make_prices(22, close_step=0.0, low_offset=0.0)
        result = calculate_signal_result(
            self.signal,
            price_df=prices,
            benchmark_df=benchmark,
            config=self.zero_cost_config,
        )
        self.assertEqual(result["outcome_status"], "complete")
        self.assertEqual(result["matured_horizon"], 20)
        self.assertTrue(result["success_t3"])
        self.assertGreaterEqual(result["excess_return_3d"], 2.0)
        self.assertGreaterEqual(result["max_drawdown_3d"], -4.0)
        self.assertEqual(result["entry_method"], "legacy_next_day_open_intraday")

    def test_partial_result_does_not_claim_three_day_or_twenty_day_metrics(self):
        prices = make_prices(3)
        benchmark = make_prices(3, close_step=0.0, low_offset=0.0)
        result = calculate_signal_result(
            self.signal,
            price_df=prices,
            benchmark_df=benchmark,
            config=self.zero_cost_config,
        )
        self.assertEqual(result["outcome_status"], "partial")
        self.assertEqual(result["matured_horizon"], 1)
        self.assertIsNone(result["return_3d"])
        self.assertIsNone(result["max_drawdown_3d"])
        self.assertIsNone(result["max_return_20d"])
        self.assertIsNone(result["success_t3"])

    def test_adjusted_ohlc_preserves_corporate_action_factor(self):
        frame = make_prices(2)
        frame["Adj Close"] = frame["Close"] * 0.5
        adjusted = _normalize_price_frame(frame)
        self.assertAlmostEqual(adjusted.iloc[0]["Open"], 50.0)
        self.assertAlmostEqual(adjusted.iloc[0]["AdjustmentFactor"], 0.5)
        adjusted_again = _normalize_price_frame(adjusted)
        self.assertAlmostEqual(adjusted_again.iloc[0]["Open"], 50.0)
        self.assertAlmostEqual(adjusted_again.iloc[0]["AdjustmentFactor"], 0.5)


class BacktestDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "scanner.db"
        with get_connection(self.db_path) as conn:
            init_db(conn)
            cursor = conn.execute(
                """
                INSERT INTO scan_runs (
                    run_at, trade_date, mode, source, strategy_version
                ) VALUES (?, ?, ?, ?, ?)
                """,
                ("2026-01-02T10:00:00+08:00", "2026-01-02", "intraday", "test", "v1"),
            )
            run_id = cursor.lastrowid
            cursor = conn.execute(
                """
                INSERT INTO stock_signals (
                    run_id, trade_date, mode, strategy, code, name, stop_loss,
                    rank_order, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    "2026-01-02",
                    "intraday",
                    "trend",
                    "2330",
                    "TSMC",
                    95.0,
                    1,
                    "2026-01-02T10:00:00+08:00",
                ),
            )
            self.signal_id = cursor.lastrowid
            conn.commit()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_schema_migration_is_idempotent_and_creates_quant_tables(self):
        with get_connection(self.db_path) as conn:
            init_db(conn)
            init_db(conn)
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            backtest_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(backtest_results)")
            }
        self.assertIn("feature_snapshots", tables)
        self.assertIn("candidate_events", tables)
        self.assertIn("news_evidence", tables)
        self.assertIn("predictions", tables)
        self.assertIn("prediction_outcomes", tables)
        self.assertIn("model_versions", tables)
        self.assertIn("backtest_runs", tables)
        self.assertIn("excess_return_3d", backtest_columns)
        self.assertIn("outcome_status", backtest_columns)

    def test_partial_backtest_remains_pending_and_complete_result_is_removed(self):
        partial = {
            "entry_date": "2026-01-05",
            "entry_price": 100.0,
            "entry_method": "legacy_next_day_open_intraday",
            "price_basis": "adjusted_ohlc",
            "matured_horizon": 1,
            "outcome_status": "partial",
            "stop_loss_hit": False,
            "success_t3": None,
        }
        save_backtest_result(self.signal_id, partial, db_path=self.db_path)
        pending = load_pending_signals(db_path=self.db_path)
        self.assertEqual([row["id"] for row in pending], [self.signal_id])

        complete = dict(partial)
        complete.update(
            {
                "matured_horizon": 20,
                "outcome_status": "complete",
                "success_t3": True,
            }
        )
        save_backtest_result(self.signal_id, complete, db_path=self.db_path)
        pending = load_pending_signals(db_path=self.db_path)
        self.assertEqual(pending, [])

    def test_formal_selections_are_prioritized_and_can_be_scoped(self):
        with get_connection(self.db_path) as conn:
            raw_cursor = conn.execute(
                """
                INSERT INTO stock_signals (
                    run_id, trade_date, mode, strategy, code, name, stop_loss,
                    rank_order, created_at
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "2025-12-31",
                    "eod",
                    "wave",
                    "2317",
                    "Hon Hai",
                    90.0,
                    1,
                    "2025-12-31T14:00:00+08:00",
                ),
            )
            raw_signal_id = raw_cursor.lastrowid
            conn.execute(
                """
                INSERT INTO candidate_events (
                    run_id, signal_id, code, name, as_of, strategies_json,
                    strategy_count, raw_rank, tradable, block_reasons_json,
                    risk_flags_json, is_first_eligible_event, is_selected,
                    selection_rank, selection_status, policy_version,
                    policy_config_json, snapshot_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    1,
                    self.signal_id,
                    "2330",
                    "TSMC",
                    "2026-01-02T10:00:00+08:00",
                    '["trend"]',
                    1,
                    1,
                    1,
                    "[]",
                    "[]",
                    1,
                    1,
                    1,
                    "selected",
                    "candidate-v1",
                    "{}",
                    "{}",
                    "2026-01-02T10:00:00+08:00",
                    "2026-01-02T10:00:00+08:00",
                ),
            )

        all_pending = load_pending_signals(db_path=self.db_path)
        formal_pending = load_pending_signals(
            selection_scope="formal", db_path=self.db_path
        )
        nonformal_pending = load_pending_signals(
            selection_scope="nonformal", db_path=self.db_path
        )

        self.assertEqual(
            [row["id"] for row in all_pending],
            [self.signal_id, raw_signal_id],
        )
        self.assertEqual([row["id"] for row in formal_pending], [self.signal_id])
        self.assertEqual([row["id"] for row in nonformal_pending], [raw_signal_id])


if __name__ == "__main__":
    unittest.main()
