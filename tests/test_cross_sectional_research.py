import tempfile
import unittest
from pathlib import Path

import pandas as pd

from ai_pipeline import MODEL_FEATURES
from cross_sectional_research import (
    RankingSpec,
    available_ranking_specs,
    evaluate_ranking_spec,
    fit_rank_phase,
    load_ranking_dataset,
)
from execution_research import EXECUTION_RESEARCH_VERSION
from research_evaluation import PromotionGates, replay_temporal_partitions


def _ranking_rows(periods=120):
    rows = []
    for date_index, trade_date in enumerate(
        pd.bdate_range("2022-01-03", periods=periods)
    ):
        market_cycle = (date_index % 7) * 0.05
        for candidate_index in range(6):
            row = {
                "trade_date": trade_date.date().isoformat(),
                "code": str(1000 + candidate_index),
                "rule_selected": int(candidate_index == 0),
                "tradable": 1,
                "scenario_version": EXECUTION_RESEARCH_VERSION,
                "next_open_entry_status": "filled",
                "next_open_net_return_3d": candidate_index + market_cycle + 0.5,
                "next_open_excess_return_3d": candidate_index + market_cycle + 0.25,
                "next_open_max_drawdown_3d": -1.0 - candidate_index * 0.1,
            }
            for feature in MODEL_FEATURES:
                row[feature] = 0.0
            row["candidate_score"] = float(candidate_index)
            row["market_avg_return"] = market_cycle
            rows.append(row)
    return pd.DataFrame(rows)


class CrossSectionalResearchTests(unittest.TestCase):
    def test_ranking_uses_all_tradable_candidates_and_selects_daily_top_k(self):
        frame = _ranking_rows(periods=40)
        dates = sorted(frame["trade_date"].unique())
        selected, diagnostics = fit_rank_phase(
            frame,
            dates[:25],
            dates[25:],
            RankingSpec("next_open", 3, top_k=2),
            min_train_rows=30,
        )

        self.assertEqual(diagnostics["selected_candidates"], 30)
        self.assertEqual(diagnostics["filled_trades"], 30)
        self.assertEqual(selected.groupby("trade_date").size().max(), 2)
        self.assertGreater(selected["net_return_3d"].mean(), 4.0)

    def test_ranking_evaluation_uses_horizon_sized_embargo_and_persists_phases(self):
        frame = _ranking_rows()
        spec = RankingSpec("next_open", 3, top_k=2)
        gates = PromotionGates(
            min_trade_dates=5,
            min_trades=10,
            min_probabilistic_sharpe=0.0,
            max_drawdown=-100.0,
            min_profitable_fold_rate=0.0,
        )
        with tempfile.TemporaryDirectory() as directory:
            result = evaluate_ranking_spec(
                frame,
                spec,
                "a" * 64,
                db_path=Path(directory) / "scanner.db",
                gates=gates,
                min_train_rows=30,
            )

        self.assertTrue(result["qualified"])
        self.assertEqual(result["temporal_split"]["embargo_trade_dates"], 3)
        self.assertEqual(
            set(result["phase_results"]), {"development", "validation", "holdout"}
        )
        self.assertGreater(
            result["mean_net_return"], result["formal_baseline_mean_net_return"]
        )

    def test_dataset_loader_only_enables_specs_with_complete_label_columns(self):
        frame = _ranking_rows(periods=10)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "replay.csv.gz"
            frame.to_csv(path, index=False, compression="gzip")
            loaded = load_ranking_dataset(path)

        available = available_ranking_specs(
            loaded,
            specs=(
                RankingSpec("next_open", 3),
                RankingSpec("next_open", 5),
            ),
        )
        self.assertEqual(available, (RankingSpec("next_open", 3),))

    def test_temporal_partition_accepts_long_horizon_embargo(self):
        frame = _ranking_rows(periods=120)
        partitions = replay_temporal_partitions(frame, embargo_trade_dates=20)

        self.assertEqual(len(partitions["development_validation_embargo"]), 20)
        self.assertEqual(len(partitions["validation_holdout_embargo"]), 20)


if __name__ == "__main__":
    unittest.main()
