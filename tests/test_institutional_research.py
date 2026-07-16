import tempfile
import unittest
from pathlib import Path

import pandas as pd

from database import get_connection
from export_dashboard_snapshot import _research_experiment_snapshot
from institutional_replay_dataset import INSTITUTIONAL_MODEL_FEATURES
from institutional_research import (
    GENERATION_2_SPECS,
    load_institutional_research_frame,
    run_institutional_ablation,
)
from research_evaluation import PromotionGates
from tests.test_cross_sectional_research import _ranking_rows


def _institutional_ranking_rows(periods=100):
    frame = _ranking_rows(periods=periods)
    for horizon in (5, 10):
        frame[f"next_open_entry_status"] = "filled"
        frame[f"next_open_net_return_{horizon}d"] = (
            frame["next_open_net_return_3d"] + horizon / 10
        )
        frame[f"next_open_excess_return_{horizon}d"] = (
            frame["next_open_excess_return_3d"] + horizon / 20
        )
        frame[f"next_open_max_drawdown_{horizon}d"] = (
            frame["next_open_max_drawdown_3d"] - horizon / 100
        )
    dates = pd.to_datetime(frame["trade_date"])
    frame["as_of"] = dates.dt.strftime("%Y-%m-%dT06:00:00+00:00")
    frame["institutional_known_at"] = (
        dates - pd.Timedelta(days=1)
    ).dt.strftime("%Y-%m-%dT00:30:00+00:00")
    frame["institutional_source_trade_date"] = (
        dates - pd.Timedelta(days=1)
    ).dt.strftime("%Y-%m-%d")
    frame["institutional_observations_20d"] = 20
    frame["institutional_coverage_status"] = "complete"
    code_value = pd.to_numeric(frame["code"], errors="coerce") - 1000
    for feature_index, feature in enumerate(INSTITUTIONAL_MODEL_FEATURES, start=1):
        frame[feature] = code_value * feature_index / 10
    return frame


class InstitutionalResearchTests(unittest.TestCase):
    def test_ablation_reserves_holdout_and_can_never_formally_qualify(self):
        frame = _institutional_ranking_rows()
        gates = PromotionGates(
            min_trade_dates=5,
            min_trades=10,
            min_probabilistic_sharpe=0.0,
            max_drawdown=-100.0,
            min_profitable_fold_rate=0.0,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "institutional.csv.gz"
            database = root / "scanner.db"
            frame.to_csv(dataset, index=False, compression="gzip")

            results = run_institutional_ablation(
                dataset,
                db_path=database,
                specs=(GENERATION_2_SPECS[0],),
                gates=gates,
                min_train_rows=30,
            )
            with get_connection(database) as conn:
                experiment = conn.execute(
                    "SELECT strategy_family FROM research_experiments WHERE experiment_key=?",
                    (GENERATION_2_SPECS[0].key,),
                ).fetchone()
                dashboard_rows = _research_experiment_snapshot(conn)

            self.assertEqual(len(results), 1)
            result = results[0]
            self.assertFalse(result["qualified"])
            self.assertFalse(result["holdout_evaluated"])
            self.assertFalse(result["formal_ranking_enabled"])
            self.assertEqual(
                set(result["phase_results"]), {"development", "validation"}
            )
            self.assertGreater(
                result["temporal_split"]["reserved_holdout_trade_dates"], 0
            )
            self.assertIn("prospective_generation_required", result["rejectionReasons"])
            self.assertEqual(
                experiment["strategy_family"],
                "generation_2_institutional_ablation",
            )
            dashboard_result = next(
                row
                for row in dashboard_rows
                if row["experimentKey"] == GENERATION_2_SPECS[0].key
            )
            self.assertEqual(
                dashboard_result["evaluationScope"],
                "historical_development_validation_only",
            )
            self.assertFalse(dashboard_result["holdoutEvaluated"])
            self.assertFalse(dashboard_result["formalRankingEnabled"])

    def test_loader_rejects_institutional_rows_known_after_the_decision(self):
        frame = _institutional_ranking_rows(periods=20)
        frame.loc[0, "institutional_known_at"] = "2030-01-01T00:00:00+00:00"
        with tempfile.TemporaryDirectory() as directory:
            dataset = Path(directory) / "institutional.csv.gz"
            frame.to_csv(dataset, index=False, compression="gzip")

            with self.assertRaisesRegex(ValueError, "lookahead"):
                load_institutional_research_frame(dataset)


if __name__ == "__main__":
    unittest.main()
