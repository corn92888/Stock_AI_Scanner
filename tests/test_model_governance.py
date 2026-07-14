import unittest

import pandas as pd

from model_governance import (
    apply_shadow_selection,
    evaluate_challenger,
    walk_forward_splits,
)


class ModelGovernanceTests(unittest.TestCase):
    def test_walk_forward_splits_are_expanding_and_embargoed(self):
        frame = pd.DataFrame(
            {
                "trade_date": [f"2026-01-{day:02d}" for day in range(1, 31)],
                "value": range(30),
            }
        )
        folds = walk_forward_splits(
            frame, min_train_dates=10, test_dates=5, embargo_trade_dates=3
        )
        self.assertGreaterEqual(len(folds), 3)
        previous_training_dates = 0
        for fold in folds:
            training_dates = fold["train"]["trade_date"].nunique()
            self.assertGreater(training_dates, previous_training_dates)
            self.assertLess(
                fold["trained_through"], fold["validation"]["trade_date"].min()
            )
            self.assertEqual(len(fold["embargo_dates"]), 3)
            previous_training_dates = training_dates

    def test_shadow_selection_applies_daily_and_industry_limits(self):
        frame = pd.DataFrame(
            {
                "trade_date": ["2026-07-10"] * 4,
                "industry": ["半導體", "半導體", "光電", "電腦"],
                "tradable": [1, 1, 1, 1],
                "is_first_eligible_event": [1, 1, 1, 1],
                "probability_t3": [0.8, 0.7, 0.6, 0.5],
                "expected_excess_return_3d": [1.0] * 4,
                "expected_max_drawdown_3d": [-1.0] * 4,
                "final_score": [90, 80, 70, 60],
            }
        )
        selected = apply_shadow_selection(frame)
        self.assertEqual(int(selected["is_selected"].sum()), 3)
        self.assertFalse(bool(selected.loc[1, "is_selected"]))

    def test_challenger_is_compared_to_rule_on_same_oof_window(self):
        rows = []
        for day in range(1, 41):
            for candidate in range(2):
                rows.append(
                    {
                        "trade_date": f"2026-01-{day:02d}",
                        "fold_index": (day - 1) // 10 + 1,
                        "is_selected": True,
                        "rule_selected": candidate == 0,
                        "net_return_3d": 2.0,
                        "excess_return_3d": 1.0,
                    }
                )
        metrics, reasons = evaluate_challenger(pd.DataFrame(rows))
        self.assertEqual(metrics["oof_trade_dates"], 40)
        self.assertEqual(metrics["challenger_trades"], 80)
        self.assertEqual(reasons, ["challenger_does_not_beat_champion"])


if __name__ == "__main__":
    unittest.main()
