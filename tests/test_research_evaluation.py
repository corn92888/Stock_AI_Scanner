import tempfile
import unittest
from pathlib import Path

import pandas as pd

from database import get_connection, init_db
from research_evaluation import (
    BASELINE_EXPERIMENTS,
    PromotionGates,
    evaluate_frame,
    run_baseline_evaluations,
    strategy_matches,
)


class ResearchEvaluationTests(unittest.TestCase):
    def test_strategy_selector_accepts_persisted_chinese_labels(self):
        self.assertTrue(strategy_matches('["順勢突破"]', "trend"))
        self.assertTrue(strategy_matches('["低檔爆量"]', "reversal"))
        self.assertTrue(strategy_matches('["波段蓄勢"]', "wave"))
        self.assertFalse(strategy_matches('["順勢突破"]', "wave"))

    def test_profitable_chronological_folds_can_pass_relaxed_test_gates(self):
        rows = []
        for index, trade_date in enumerate(pd.bdate_range("2026-01-02", periods=12)):
            rows.append(
                {
                    "trade_date": trade_date.date().isoformat(),
                    "net_return_3d": 0.8 + (index % 4) * 0.25,
                    "excess_return_3d": 0.3 + (index % 3) * 0.1,
                }
            )
        metrics, reasons = evaluate_frame(
            pd.DataFrame(rows),
            gates=PromotionGates(
                min_trade_dates=10,
                min_trades=10,
                min_probabilistic_sharpe=0.5,
                max_drawdown=-20.0,
                min_profitable_fold_rate=0.6,
            ),
        )
        self.assertEqual(reasons, [])
        self.assertEqual(metrics["trade_dates"], 12)
        self.assertEqual(metrics["folds"], 5)
        self.assertGreater(metrics["mean_excess_return"], 0)
        self.assertEqual(metrics["profitable_fold_rate"], 1.0)

    def test_empty_experiment_is_rejected_with_auditable_reasons(self):
        metrics, reasons = evaluate_frame(pd.DataFrame())
        self.assertEqual(metrics["trades"], 0)
        self.assertIn("insufficient_trade_dates", reasons)
        self.assertIn("insufficient_trades", reasons)
        self.assertIn("non_positive_excess_return", reasons)

    def test_baseline_registry_and_evaluations_are_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "scanner.db"
            results = run_baseline_evaluations(db_path=db_path)
            with get_connection(db_path) as conn:
                init_db(conn)
                experiments = conn.execute(
                    "SELECT COUNT(*) FROM research_experiments"
                ).fetchone()[0]
                evaluations = conn.execute(
                    "SELECT COUNT(*) FROM experiment_evaluations"
                ).fetchone()[0]
            self.assertEqual(len(results), len(BASELINE_EXPERIMENTS))
            self.assertEqual(experiments, len(BASELINE_EXPERIMENTS))
            self.assertEqual(evaluations, len(BASELINE_EXPERIMENTS))
            self.assertTrue(all(not result["qualified"] for result in results))


if __name__ == "__main__":
    unittest.main()
