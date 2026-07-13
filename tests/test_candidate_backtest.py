import tempfile
import unittest
from pathlib import Path

import pandas as pd

from candidate_backtest import (
    CandidateExecutionConfig,
    calculate_candidate_result,
    load_pending_candidates,
)
from database import (
    CANDIDATE_EXECUTION_VERSION,
    get_connection,
    init_db,
    save_candidate_outcome,
)


def candidate_prices():
    index = pd.bdate_range("2026-01-02", periods=6)
    closes = [100.0, 98.0, 103.0, 105.0, 107.0, 109.0]
    return pd.DataFrame(
        {
            "Open": [100.0, 100.0, 102.0, 104.0, 106.0, 108.0],
            "High": [value + 1.0 for value in closes],
            "Low": [value - 1.0 for value in closes],
            "Close": closes,
            "Adj Close": closes,
            "Volume": [1_000_000] * len(index),
        },
        index=index,
    )


class CandidateOutcomeCalculationTests(unittest.TestCase):
    def setUp(self):
        self.candidate = {
            "id": 1,
            "trade_date": "2026-01-02",
            "code": "2330",
            "observation_price": 99.0,
        }
        self.config = CandidateExecutionConfig(
            buy_fee_rate=0.0,
            sell_fee_rate=0.0,
            sell_tax_rate=0.0,
            slippage_rate=0.0,
        )
        benchmark = candidate_prices()
        benchmark[["Open", "High", "Low", "Close", "Adj Close"]] = 100.0
        self.benchmark = benchmark

    def test_defense_close_changes_realized_return_without_erasing_fixed_horizon(self):
        result = calculate_candidate_result(
            self.candidate,
            candidate_prices(),
            benchmark_df=self.benchmark,
            config=self.config,
        )
        self.assertTrue(result["defense_triggered"])
        self.assertEqual(result["entry_adjustment_factor"], 1.0)
        self.assertEqual(result["exit_reason"], "defense_close")
        self.assertEqual(result["exit_at"], "2026-01-05")
        self.assertEqual(result["net_return_3d"], -2.0)
        self.assertEqual(result["fixed_net_return_3d"], 5.0)
        self.assertFalse(result["success_t3"])

    def test_candidate_holds_to_t3_when_defense_is_not_breached(self):
        candidate = dict(self.candidate, observation_price=90.0)
        result = calculate_candidate_result(
            candidate,
            candidate_prices(),
            benchmark_df=self.benchmark,
            config=self.config,
        )
        self.assertFalse(result["defense_triggered"])
        self.assertEqual(result["exit_reason"], "time_exit_t3")
        self.assertEqual(result["exit_at"], "2026-01-07")
        self.assertEqual(result["net_return_3d"], 5.0)
        self.assertTrue(result["success_t3"])


class CandidateOutcomeDatabaseTests(unittest.TestCase):
    def test_pending_loader_includes_selected_and_rejected_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "scanner.db"
            with get_connection(db_path) as conn:
                init_db(conn)
                run_id = conn.execute(
                    """
                    INSERT INTO scan_runs (
                        run_at, trade_date, mode, source, strategy_version
                    ) VALUES ('2026-01-02T10:00:00+08:00', '2026-01-02',
                              'intraday', 'test', 'v1')
                    """
                ).lastrowid
                for index, selected in enumerate((1, 0), start=1):
                    conn.execute(
                        """
                        INSERT INTO candidate_events (
                            run_id, code, name, as_of, strategies_json,
                            strategy_count, raw_rank, signal_price,
                            observation_price, tradable, block_reasons_json,
                            risk_flags_json, is_first_eligible_event, is_selected,
                            selection_status, policy_version, policy_config_json,
                            snapshot_json, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, '[]', 1, ?, 100, 95, 1, '[]',
                                  '[]', 1, ?, ?, 'test_v1', '{}', '{}', ?, ?)
                        """,
                        (
                            run_id,
                            f"23{index:02d}",
                            f"Stock {index}",
                            "2026-01-02T10:00:00+08:00",
                            index,
                            selected,
                            "selected" if selected else "daily_limit",
                            "2026-01-02T10:00:00+08:00",
                            "2026-01-02T10:00:00+08:00",
                        ),
                    )

            pending = load_pending_candidates(db_path=db_path)
            self.assertEqual(len(pending), 2)
            self.assertTrue(pending[0]["is_selected"])

            save_candidate_outcome(
                pending[0]["id"],
                CANDIDATE_EXECUTION_VERSION,
                {
                    "entry_method": "next_day_open",
                    "matured_horizon": 5,
                    "outcome_status": "complete",
                    "defense_triggered": False,
                },
                db_path=db_path,
            )
            remaining = load_pending_candidates(db_path=db_path)
            self.assertEqual([row["id"] for row in remaining], [pending[1]["id"]])


if __name__ == "__main__":
    unittest.main()
