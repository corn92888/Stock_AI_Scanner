import json
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_pipeline import (
    FEATURE_VERSION,
    build_feature_snapshots,
    candidate_to_feature,
    save_shadow_predictions,
    train_shadow_model,
    update_prediction_outcomes,
)
from database import get_connection, init_db, save_backtest_result, save_candidate_events


class AiPipelineTests(unittest.TestCase):
    def test_candidate_snapshot_becomes_point_in_time_features(self):
        feature = candidate_to_feature(
            {
                "run_id": 1,
                "signal_id": 2,
                "code": "2330",
                "as_of": "2026-07-10T10:00:00+08:00",
                "score": 72,
                "strategy_count": 2,
                "strategies_json": '["trend", "wave"]',
                "tradable": 1,
                "is_first_eligible_event": 1,
                "stop_distance_pct": 4.5,
                "signal_price": 1000,
                "pct_change": 2.0,
                "turnover_billion": 100,
                "volume_ratio_5": 1.8,
                "intraday_position": 0.7,
                "snapshot_json": json.dumps(
                    {
                        "量比20": 1.4,
                        "RSI": 58,
                        "產業上漲比例": 65,
                        "產業平均漲跌幅": 1.2,
                        "產業熱度分數": 8.5,
                        "市場上漲比例": 55,
                    }
                ),
            }
        )
        self.assertEqual(feature["feature_version"], FEATURE_VERSION)
        self.assertEqual(feature["strategy_trend"], 1)
        self.assertEqual(feature["strategy_reversal"], 0)
        self.assertEqual(feature["strategy_wave"], 1)
        self.assertEqual(feature["market_up_ratio"], 55)

    def test_shadow_training_prediction_and_outcome_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "scanner.db"
            with get_connection(db_path) as conn:
                init_db(conn)
                for index in range(30):
                    run_id = index + 1
                    signal_id = index + 1
                    day = f"2026-05-{index + 1:02d}"
                    conn.execute(
                        """
                        INSERT INTO scan_runs (
                            id, run_at, trade_date, mode, source, strategy_version
                        ) VALUES (?, ?, ?, 'intraday', 'test', 'v1')
                        """,
                        (run_id, f"{day}T10:00:00+08:00", day),
                    )
                    conn.execute(
                        """
                        INSERT INTO stock_signals (
                            id, run_id, trade_date, mode, strategy, code,
                            name, industry, signal_price, created_at
                        ) VALUES (?, ?, ?, 'intraday', 'trend', ?, ?, ?, ?, ?)
                        """,
                        (
                            signal_id,
                            run_id,
                            day,
                            f"{1000 + index}",
                            f"股票{index}",
                            f"產業{index % 4}",
                            50 + index,
                            f"{day}T10:00:00+08:00",
                        ),
                    )
                conn.commit()

            for index in range(30):
                run_id = index + 1
                signal_id = index + 1
                save_candidate_events(
                    run_id,
                    [
                        {
                            "signal_id": signal_id,
                            "code": f"{1000 + index}",
                            "name": f"股票{index}",
                            "as_of": f"2026-05-{index + 1:02d}T10:00:00+08:00",
                            "industry": f"產業{index % 4}",
                            "strategies_json": '["trend"]',
                            "strategy_count": 1,
                            "raw_rank": 1,
                            "score": 40 + index,
                            "signal_price": 50 + index,
                            "pct_change": -2 + index * 0.2,
                            "turnover_billion": 5 + index,
                            "volume_ratio_5": 0.8 + index * 0.05,
                            "intraday_position": 0.3 + index * 0.01,
                            "observation_price": 48 + index,
                            "chase_limit": 52 + index,
                            "stop_distance_pct": 4,
                            "tradable": True,
                            "is_first_eligible_event": True,
                            "is_selected": index % 3 == 0,
                            "selection_status": "selected" if index % 3 == 0 else "daily_limit",
                            "policy_version": "test_v1",
                            "snapshot_json": json.dumps(
                                {
                                    "量比20": 1 + index * 0.02,
                                    "RSI": 45 + index,
                                    "產業上漲比例": 40 + index,
                                    "產業平均漲跌幅": index * 0.05,
                                    "產業熱度分數": index * 0.1,
                                }
                            ),
                        }
                    ],
                    db_path=db_path,
                )
                success = index % 2 == 0
                save_backtest_result(
                    signal_id,
                    {
                        "entry_date": f"2026-05-{index + 1:02d}",
                        "entry_price": 50 + index,
                        "entry_method": "next_open",
                        "net_return_1d": 0.5 if success else -0.5,
                        "net_return_3d": 3 if success else -2,
                        "net_return_5d": 4 if success else -3,
                        "benchmark_return_3d": 0.5,
                        "excess_return_3d": 2.5 if success else -2.5,
                        "max_return_3d": 4 if success else 1,
                        "max_drawdown_3d": -1 if success else -5,
                        "success_t3": success,
                        "matured_horizon": 3,
                        "outcome_status": "partial",
                    },
                    db_path=db_path,
                )

            self.assertEqual(build_feature_snapshots(db_path=db_path), 30)
            training = train_shadow_model(
                db_path=db_path,
                artifact_dir=Path(directory) / "models",
                min_samples=20,
                min_positives=5,
            )
            self.assertEqual(training["status"], "trained")
            predicted_at = dt.datetime(
                2026, 5, 30, 10, 5,
                tzinfo=dt.timezone(dt.timedelta(hours=8)),
            )
            with patch("ai_pipeline.get_taipei_now", return_value=predicted_at):
                predictions = save_shadow_predictions(30, training, db_path=db_path)
                self.assertEqual(len(predictions), 1)
                self.assertTrue(predictions[0]["is_prospective"])
                self.assertEqual(update_prediction_outcomes(db_path=db_path), 1)

            with get_connection(db_path) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM model_versions").fetchone()[0], 1)
                outcome = conn.execute(
                    "SELECT matured_horizon, success_t3 FROM prediction_outcomes"
                ).fetchone()
                self.assertEqual(outcome["matured_horizon"], 3)


if __name__ == "__main__":
    unittest.main()
