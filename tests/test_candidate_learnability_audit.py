import tempfile
import unittest
from pathlib import Path

import pandas as pd

from candidate_learnability_audit import (
    AUDIT_HORIZONS,
    _fit_validation_predictions,
    build_learnability_audit,
    run_learnability_audit,
)
from cross_sectional_research import (
    ALPHA_PREDICTION_QUANTILE,
    CROSS_SECTIONAL_FEATURE_MODE,
    RankingSpec,
)
from database import get_connection
from execution_research import ENTRY_METHODS
from export_dashboard_snapshot import (
    _learnability_audit_snapshot,
    _research_experiment_snapshot,
)
from research_evaluation import replay_temporal_partitions
from tests.test_cross_sectional_research import _ranking_rows


def _audit_rows(periods=140):
    frame = _ranking_rows(periods=periods)
    candidate = pd.to_numeric(frame["code"]) - 1000
    date_cycle = pd.to_datetime(frame["trade_date"]).dt.dayofyear % 9 / 20
    for method_index, method in enumerate(ENTRY_METHODS):
        frame[f"{method}_entry_status"] = "filled"
        for horizon in AUDIT_HORIZONS:
            frame[f"{method}_net_return_{horizon}d"] = (
                candidate * 0.8 + date_cycle + horizon / 20 - method_index * 0.1
            )
            frame[f"{method}_excess_return_{horizon}d"] = (
                candidate * 0.7 + date_cycle + horizon / 30 - method_index * 0.1
            )
            frame[f"{method}_max_drawdown_{horizon}d"] = -1.0 - candidate / 10
    pullback_skip = (frame.index % 11) == 0
    frame.loc[pullback_skip, "pullback_2pct_3d_entry_status"] = "skipped"
    for horizon in AUDIT_HORIZONS:
        for label in ("net_return", "excess_return", "max_drawdown"):
            frame.loc[
                pullback_skip,
                f"pullback_2pct_3d_{label}_{horizon}d",
            ] = None
    return frame


class CandidateLearnabilityAuditTests(unittest.TestCase):
    def test_audit_builds_eight_diagnostic_cells_without_holdout(self):
        frame = _audit_rows()
        with tempfile.TemporaryDirectory() as directory:
            dataset = Path(directory) / "replay.csv.gz"
            frame.to_csv(dataset, index=False, compression="gzip")
            report = build_learnability_audit(dataset, min_train_rows=30)

        self.assertEqual(len(report["rows"]), len(ENTRY_METHODS) * 2)
        self.assertFalse(report["holdout_evaluated"])
        self.assertFalse(report["formal_ranking_enabled"])
        self.assertGreater(report["reserved_holdout_trade_dates"], 0)
        self.assertEqual(report["primary_spec_key"], "next_open_t5")
        self.assertGreater(report["primary"]["oracle"]["mean_daily_net_return"], 0)
        self.assertGreater(report["primary"]["rankability"]["mean_rank_ic"], 0)

    def test_holdout_label_changes_cannot_change_diagnostic_results(self):
        frame = _audit_rows()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original.csv.gz"
            changed = root / "changed.csv.gz"
            frame.to_csv(original, index=False, compression="gzip")
            report = build_learnability_audit(original, min_train_rows=30)

            future = frame["trade_date"] >= report["reserved_holdout_start"]
            for method in ENTRY_METHODS:
                for horizon in AUDIT_HORIZONS:
                    frame.loc[
                        future, f"{method}_net_return_{horizon}d"
                    ] = 9999.0
                    frame.loc[
                        future, f"{method}_excess_return_{horizon}d"
                    ] = -9999.0
            frame.to_csv(changed, index=False, compression="gzip")
            changed_report = build_learnability_audit(changed, min_train_rows=30)

        self.assertEqual(report["rows"], changed_report["rows"])
        self.assertEqual(report["primary"], changed_report["primary"])
        self.assertEqual(
            report["primary_diagnosis"], changed_report["primary_diagnosis"]
        )

    def test_validation_outcomes_do_not_change_frozen_predictions(self):
        frame = _audit_rows()
        spec = RankingSpec(
            method="next_open",
            horizon=5,
            target="peer_rank",
            prediction_quantile=ALPHA_PREDICTION_QUANTILE,
            feature_mode=CROSS_SECTIONAL_FEATURE_MODE,
        )
        partitions = replay_temporal_partitions(frame, embargo_trade_dates=10)
        original, _, diagnostics = _fit_validation_predictions(
            frame,
            partitions["development"],
            partitions["validation"],
            spec,
            min_train_rows=30,
        )
        changed = frame.copy()
        validation = changed["trade_date"].isin(partitions["validation"])
        changed.loc[validation, "next_open_excess_return_5d"] = 9999.0
        changed_predictions, _, changed_diagnostics = _fit_validation_predictions(
            changed,
            partitions["development"],
            partitions["validation"],
            spec,
            min_train_rows=30,
        )

        self.assertEqual(
            original["prediction"].tolist(),
            changed_predictions["prediction"].tolist(),
        )
        self.assertEqual(
            diagnostics["prediction_threshold"],
            changed_diagnostics["prediction_threshold"],
        )

    def test_persisted_audit_has_dedicated_dashboard_surface(self):
        frame = _audit_rows()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "replay.csv.gz"
            database = root / "scanner.db"
            frame.to_csv(dataset, index=False, compression="gzip")
            run_learnability_audit(
                dataset,
                db_path=database,
                min_train_rows=30,
            )
            with get_connection(database) as conn:
                audit = _learnability_audit_snapshot(conn)
                experiments = _research_experiment_snapshot(conn)

        self.assertEqual(len(audit["rows"]), len(ENTRY_METHODS) * 2)
        self.assertFalse(audit["holdoutEvaluated"])
        self.assertFalse(audit["formalRankingEnabled"])
        self.assertEqual(audit["primarySpecKey"], "next_open_t5")
        self.assertNotIn(
            "candidate_pool_learnability_audit_v1",
            {row["experimentKey"] for row in experiments},
        )


if __name__ == "__main__":
    unittest.main()
