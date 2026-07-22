import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from alpha_strategy_v2 import (
    AlphaSpec,
    _walk_forward_fold_stability,
    fit_alpha_phase,
    load_alpha_dataset,
)
from alpha_universe_dataset import (
    ALPHA_DATASET_VERSION,
    ALPHA_EXECUTION_VERSION,
    ALPHA_FEATURES,
    ALPHA_HORIZONS,
)


def alpha_frame(trade_dates=620, symbols=10):
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2021-01-04", periods=trade_dates).date.astype(str)
    rows = []
    for date_index, trade_date in enumerate(dates):
        regime = 0.4 if date_index % 5 else -0.2
        for symbol in range(symbols):
            signal = (symbol - (symbols - 1) / 2) / symbols + rng.normal(0, 0.04)
            row = {
                "trade_date": trade_date,
                "code": f"{1000 + symbol}",
                "industry": f"Industry {symbol % 5}",
                "execution_version": ALPHA_EXECUTION_VERSION,
            }
            for feature in ALPHA_FEATURES:
                row[feature] = rng.normal(0, 0.2)
            row.update(
                {
                    "return_1d": 0.3 * signal,
                    "return_5d": signal,
                    "return_20d": 2.0 * signal,
                    "return_60d": 3.0 * signal,
                    "momentum_20_ex_5": 1.5 * signal,
                    "momentum_60_ex_5": 2.5 * signal,
                    "relative_return_20d": 2.0 * signal,
                    "relative_return_60d": 3.0 * signal,
                    "volume_ratio_5": 1.2,
                    "distance_ma20_pct": 1.0,
                    "intraday_position": 0.65,
                    "turnover_20d_billion": 8.0 + symbol,
                }
            )
            for horizon in ALPHA_HORIZONS:
                noise = rng.normal(0, 0.08)
                excess = regime + 2.5 * signal + noise
                row[f"net_return_{horizon}d"] = excess + 0.25
                row[f"excess_return_{horizon}d"] = excess
                row[f"max_drawdown_{horizon}d"] = -0.8 - abs(noise)
            rows.append(row)
    return pd.DataFrame(rows)


class AlphaStrategyV2Tests(unittest.TestCase):
    def test_walk_forward_stability_uses_real_model_folds(self):
        selected = pd.DataFrame(
            {
                "trade_date": ["2024-01-02", "2024-02-01", "2024-03-01"],
                "fold_index": [1, 2, 3],
                "net_return_3d": [1.0, -0.2, 0.8],
                "excess_return_3d": [0.5, -0.4, 0.3],
            }
        )
        fold_diagnostics = [{"fold": fold} for fold in range(1, 5)]

        rate, diagnostics = _walk_forward_fold_stability(
            selected, fold_diagnostics
        )

        self.assertEqual(rate, 0.5)
        self.assertEqual([item["profitable"] for item in diagnostics], [True, False, True, False])
        self.assertEqual(diagnostics[-1]["selected_trades"], 0)

    def test_phase_selects_diversified_candidates_and_can_abstain(self):
        frame = alpha_frame()
        dates = sorted(frame["trade_date"].unique())
        selected, diagnostics = fit_alpha_phase(
            frame,
            dates[:480],
            dates[500:600],
            AlphaSpec(horizon=10, target="excess"),
            min_train_rows=500,
        )

        self.assertFalse(selected.empty)
        self.assertLessEqual(selected.groupby("trade_date").size().max(), 3)
        self.assertTrue(
            (selected.groupby("trade_date")["industry"].nunique() == selected.groupby("trade_date").size()).all()
        )
        self.assertGreater(diagnostics["abstained_trade_dates"], 0)
        self.assertGreater(diagnostics["confidence_threshold"], 0)

    def test_loader_rejects_legacy_candidate_dataset_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alpha.csv.gz"
            pd.DataFrame(
                {
                    "trade_date": ["2024-01-02"],
                    "code": ["2330"],
                    "industry": ["Semiconductor"],
                    "execution_version": [ALPHA_EXECUTION_VERSION],
                }
            ).to_csv(path, index=False, compression="gzip")
            with self.assertRaisesRegex(ValueError, "missing columns"):
                load_alpha_dataset(path)


if __name__ == "__main__":
    unittest.main()
