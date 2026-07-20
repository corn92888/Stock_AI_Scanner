import sqlite3
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from ai_pipeline import MODEL_FEATURES
from strategy_challenger import (
    locked_specs,
    run_strategy_challenger,
    select_challenger,
)


class StrategyChallengerTests(unittest.TestCase):
    def test_locked_family_has_six_next_open_comparisons(self):
        specs = locked_specs()
        self.assertEqual(len(specs), 6)
        self.assertEqual({spec.method for spec in specs}, {"next_open"})
        self.assertEqual({spec.horizon for spec in specs}, {5, 10, 20})
        self.assertEqual({spec.target for spec in specs}, {"excess", "peer_rank"})

    def test_selection_stays_in_cash_without_a_qualified_candidate(self):
        result = select_challenger(
            [
                {
                    "experimentKey": "best-diagnostic",
                    "qualified": False,
                    "mean_daily_excess_return": -0.1,
                    "mean_daily_net_return": 0.5,
                    "formal_excess_lift": 0.2,
                    "trades": 500,
                },
                {
                    "experimentKey": "weaker",
                    "qualified": False,
                    "mean_daily_excess_return": -0.5,
                    "mean_daily_net_return": -0.2,
                    "formal_excess_lift": 0.1,
                    "trades": 700,
                },
            ]
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["recommendationMode"], "cash")
        self.assertIsNone(result["selectedExperimentKey"])
        self.assertEqual(result["diagnosticLeaderKey"], "best-diagnostic")

    def test_run_persists_walk_forward_snapshot_without_reading_holdout(self):
        dates = pd.bdate_range("2021-01-04", periods=120)
        rows = []
        for date_index, date in enumerate(dates):
            for code_index in range(5):
                score = 40 + code_index * 10
                net_return = (code_index - 2) * 0.35 + (date_index % 7) * 0.01
                row = {
                    "trade_date": date.strftime("%Y-%m-%d"),
                    "code": f"{1000 + code_index}",
                    "industry": "Electronics" if code_index < 3 else "Other",
                    "rule_selected": int(code_index == 4),
                    "tradable": 1,
                    "scenario_version": "fixed_horizon_execution_scenarios_v1",
                    "next_open_entry_status": "filled",
                    "next_open_net_return_5d": net_return,
                    "next_open_excess_return_5d": net_return - 0.1,
                    "next_open_max_drawdown_5d": -2.0,
                }
                for feature in MODEL_FEATURES:
                    row[feature] = 1.0
                row.update(
                    {
                        "candidate_score": score,
                        "strategy_count": 1,
                        "strategy_trend": 1,
                        "strategy_reversal": 0,
                        "strategy_wave": 0,
                        "pct_change": code_index * 0.2,
                        "turnover_billion": 10 + code_index,
                        "volume_ratio_5": 1 + code_index * 0.1,
                        "volume_ratio_20": 1 + code_index * 0.1,
                        "intraday_position": 0.6,
                        "rsi": 45 + code_index,
                        "industry_up_ratio": 55,
                        "industry_avg_return": 0.5,
                        "industry_heat": 60,
                        "market_up_ratio": 55,
                        "market_avg_return": 0.3,
                        "market_median_return": 0.2,
                        "stop_distance_pct": 4,
                    }
                )
                rows.append(row)

        with tempfile.TemporaryDirectory() as directory:
            dataset = Path(directory) / "training.csv.gz"
            database = Path(directory) / "scanner.db"
            pd.DataFrame(rows).to_csv(dataset, index=False, compression="gzip")
            result = run_strategy_challenger(
                dataset_path=dataset,
                db_path=database,
                specs=(locked_specs()[0],),
                initial_train_dates=30,
                test_window_dates=10,
                min_train_rows=20,
            )

            self.assertEqual(result["candidateCount"], 1)
            self.assertFalse(result["selectionUsesHoldout"])
            self.assertEqual(len(result["executionMatrix"]), 1)
            candidate = result["candidateLeaderboard"][0]
            self.assertFalse(candidate["holdout_evaluated"])
            self.assertGreater(candidate["reserved_holdout_trade_dates"], 0)
            self.assertGreaterEqual(candidate["walk_forward_folds"], 4)

            conn = sqlite3.connect(database)
            saved = conn.execute(
                "SELECT status, recommendation_mode, candidate_count "
                "FROM strategy_challenger_snapshots"
            ).fetchone()
            conn.close()
            self.assertEqual(saved[2], 1)
            self.assertIn(saved[0], {"blocked", "prospective_shadow_ready"})
            self.assertIn(saved[1], {"cash", "shadow"})


if __name__ == "__main__":
    unittest.main()
