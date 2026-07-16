import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from ai_pipeline import MODEL_FEATURES
from cross_sectional_research import (
    ALPHA_MODEL_VERSION,
    CROSS_SECTIONAL_FEATURE_MODE,
    PEER_RANK_MODEL_VERSION,
    PEER_RANK_PSR_GATE,
    RankingSpec,
    _feature_frame,
    _peer_rank_target,
    available_ranking_specs,
    evaluate_ranking_spec,
    fit_rank_phase,
    load_ranking_dataset,
)
from database import get_connection
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
                "industry": "Industry A" if candidate_index < 3 else "Industry B",
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

    def test_alpha_ranker_calibrates_on_training_data_and_can_abstain(self):
        frame = _ranking_rows(periods=55)
        dates = sorted(frame["trade_date"].unique())
        evaluation_dates = dates[40:]
        low_signal_dates = evaluation_dates[:5]
        frame.loc[
            frame["trade_date"].isin(low_signal_dates), "candidate_score"
        ] = -100.0
        spec = RankingSpec(
            "next_open",
            3,
            top_k=2,
            target="excess",
            prediction_quantile=0.80,
        )

        selected, diagnostics = fit_rank_phase(
            frame,
            dates[:40],
            evaluation_dates,
            spec,
            min_train_rows=30,
        )
        changed_evaluation = frame.copy()
        changed_evaluation.loc[
            changed_evaluation["trade_date"].isin(evaluation_dates),
            "next_open_excess_return_3d",
        ] = 999.0
        _, changed_diagnostics = fit_rank_phase(
            changed_evaluation,
            dates[:40],
            evaluation_dates,
            spec,
            min_train_rows=30,
        )

        self.assertGreater(diagnostics["prediction_threshold"], 0.0)
        self.assertEqual(
            diagnostics["prediction_threshold"],
            changed_diagnostics["prediction_threshold"],
        )
        self.assertEqual(diagnostics["calibration_embargo_trade_dates"], 3)
        self.assertGreater(diagnostics["abstained_trade_dates"], 0)
        self.assertLess(
            diagnostics["participating_trade_dates"],
            diagnostics["evaluation_trade_dates"],
        )
        self.assertLessEqual(selected.groupby("trade_date").size().max(), 2)

    def test_alpha_evaluation_persists_locked_target_and_threshold_policy(self):
        frame = _ranking_rows()
        spec = RankingSpec(
            "next_open",
            3,
            top_k=2,
            target="excess",
            prediction_quantile=0.80,
        )
        gates = PromotionGates(
            min_trade_dates=5,
            min_trades=10,
            min_probabilistic_sharpe=0.0,
            max_drawdown=-100.0,
            min_profitable_fold_rate=0.0,
        )
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "scanner.db"
            result = evaluate_ranking_spec(
                frame,
                spec,
                "b" * 64,
                db_path=db_path,
                gates=gates,
                min_train_rows=30,
            )
            with get_connection(db_path) as conn:
                row = conn.execute(
                    """
                    SELECT re.config_json, ee.metrics_json
                    FROM research_experiments re
                    JOIN experiment_evaluations ee ON ee.experiment_id=re.id
                    WHERE re.experiment_key=?
                    """,
                    (spec.key,),
                ).fetchone()

        config = json.loads(row["config_json"])
        metrics = json.loads(row["metrics_json"])
        self.assertEqual(config["parameters"]["target"], "excess")
        self.assertEqual(config["parameters"]["prediction_quantile"], 0.80)
        self.assertEqual(result["model_version"], ALPHA_MODEL_VERSION)
        self.assertEqual(metrics["ranking_target"], "excess")
        self.assertIn("prediction_threshold", metrics)
        self.assertIn("participation_rate_pct", metrics)

    def test_peer_rank_target_uses_industry_then_date_fallback(self):
        frame = pd.DataFrame(
            {
                "trade_date": ["2026-01-02"] * 5,
                "industry": ["A", "A", "A", "B", "B"],
                "excess": [1.0, 2.0, 3.0, 10.0, 20.0],
            }
        )

        target, diagnostics = _peer_rank_target(frame, "excess")

        self.assertEqual(target.tolist(), [-1.0, -0.5, 0.0, 0.5, 1.0])
        self.assertAlmostEqual(target.mean(), 0.0)
        self.assertEqual(diagnostics["target_industry_peer_rows"], 3)
        self.assertEqual(diagnostics["target_date_fallback_rows"], 2)
        self.assertEqual(diagnostics["target_industry_peer_rate_pct"], 60.0)

    def test_cross_sectional_rank_features_ignore_absolute_scale(self):
        frame = _ranking_rows(periods=1)
        spec = RankingSpec(
            "next_open",
            3,
            target="peer_rank",
            prediction_quantile=0.80,
            feature_mode=CROSS_SECTIONAL_FEATURE_MODE,
        )
        original = _feature_frame(frame, spec)
        rescaled = frame.copy()
        rescaled["candidate_score"] = rescaled["candidate_score"] * 100 + 7
        transformed = _feature_frame(rescaled, spec)

        self.assertEqual(
            original["candidate_score_cs_rank"].tolist(),
            transformed["candidate_score_cs_rank"].tolist(),
        )
        self.assertNotEqual(
            original["candidate_score"].tolist(),
            transformed["candidate_score"].tolist(),
        )

    def test_peer_rank_evaluation_persists_cumulative_research_gate(self):
        frame = _ranking_rows()
        spec = RankingSpec(
            "next_open",
            3,
            top_k=2,
            target="peer_rank",
            prediction_quantile=0.80,
            feature_mode=CROSS_SECTIONAL_FEATURE_MODE,
        )
        with tempfile.TemporaryDirectory() as directory:
            result = evaluate_ranking_spec(
                frame,
                spec,
                "c" * 64,
                db_path=Path(directory) / "scanner.db",
                min_train_rows=30,
            )

        self.assertEqual(result["model_version"], PEER_RANK_MODEL_VERSION)
        self.assertEqual(result["ranking_target"], "peer_rank")
        self.assertEqual(result["feature_mode"], CROSS_SECTIONAL_FEATURE_MODE)
        self.assertEqual(result["multiple_testing_family_size"], 60)
        self.assertAlmostEqual(
            result["multiple_testing_psr_gate"], PEER_RANK_PSR_GATE
        )
        self.assertEqual(result["phase_results"]["holdout"]["metrics"]["feature_count"], 36)


if __name__ == "__main__":
    unittest.main()
