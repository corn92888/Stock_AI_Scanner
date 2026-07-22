import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from alpha_live import save_alpha_live_run, score_alpha_panel
from alpha_strategy_v2 import ALPHA_MODEL_VERSION
from alpha_universe_dataset import (
    ALPHA_DATASET_VERSION,
    ALPHA_EXECUTION_VERSION,
    ALPHA_FEATURES,
)
from database import get_connection, init_db


class SignalModel:
    def predict(self, features):
        return features["return_20d"].to_numpy()


def inference_panel():
    rows = []
    for index in range(6):
        row = {
            "trade_date": "2026-07-22",
            "code": str(1000 + index),
            "name": f"Stock {index}",
            "industry": f"Industry {index % 4}",
            "signal_price": 50.0 + index,
        }
        for feature in ALPHA_FEATURES:
            row[feature] = 1.0
        row.update(
            return_1d=1.0,
            return_20d=float(index),
            volume_ratio_5=1.2,
            distance_ma20_pct=2.0,
            intraday_position=0.7,
            turnover_20d_billion=5.0 + index,
        )
        rows.append(row)
    return pd.DataFrame(rows)


def artifact(threshold=2.5):
    return {
        "artifact_version": "alpha_model_artifact_v1",
        "model_version": ALPHA_MODEL_VERSION,
        "dataset_version": ALPHA_DATASET_VERSION,
        "execution_version": ALPHA_EXECUTION_VERSION,
        "dataset_fingerprint": "dataset-sha",
        "spec": {
            "horizon": 10,
            "target": "excess",
            "top_k": 3,
            "confidence_quantile": 0.7,
        },
        "confidence_threshold": threshold,
        "model": SignalModel(),
    }


class AlphaLiveTests(unittest.TestCase):
    def test_score_selects_top_three_from_distinct_industries(self):
        selected, diagnostics = score_alpha_panel(inference_panel(), artifact())

        self.assertEqual(diagnostics["status"], "active")
        self.assertEqual(list(selected["code"]), ["1005", "1004", "1003"])
        self.assertEqual(selected["industry"].nunique(), 3)
        self.assertTrue(np.allclose(selected["allocation_weight"], 1 / 3))

    def test_score_holds_cash_below_calibrated_confidence(self):
        selected, diagnostics = score_alpha_panel(
            inference_panel(), artifact(threshold=10.0)
        )

        self.assertTrue(selected.empty)
        self.assertEqual(diagnostics["status"], "abstained")

    def test_live_run_is_idempotent_for_same_artifact_and_date(self):
        selected, diagnostics = score_alpha_panel(inference_panel(), artifact())
        diagnostics.update(
            signal_date="2026-07-22",
            history_symbols=6,
            eligible_symbols=6,
        )
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "scanner.db"
            first = save_alpha_live_run(
                selected, diagnostics, artifact(), "artifact-sha", db_path=db_path
            )
            second = save_alpha_live_run(
                selected, diagnostics, artifact(), "artifact-sha", db_path=db_path
            )
            with get_connection(db_path) as conn:
                init_db(conn)
                runs = conn.execute("SELECT COUNT(*) FROM alpha_live_runs").fetchone()[0]
                signals = conn.execute(
                    "SELECT COUNT(*) FROM alpha_live_signals"
                ).fetchone()[0]

        self.assertEqual(first, second)
        self.assertEqual(runs, 1)
        self.assertEqual(signals, 3)


if __name__ == "__main__":
    unittest.main()
