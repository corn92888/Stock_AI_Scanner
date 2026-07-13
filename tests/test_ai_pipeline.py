import json
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_pipeline import (
    FEATURE_VERSION,
    _purged_date_split,
    build_feature_snapshots,
    candidate_to_feature,
    save_shadow_predictions,
    train_shadow_model,
    update_prediction_outcomes,
)
from database import (
    CANDIDATE_EXECUTION_VERSION,
    get_connection,
    init_db,
    save_backtest_result,
    save_candidate_events,
    save_candidate_outcome,
)


class AiPipelineTests(unittest.TestCase):
    def test_time_split_groups_dates_and_embargoes_the_boundary(self):
        import pandas as pd

        frame = pd.DataFrame(
            {
                "trade_date": [f"2026-01-{day:02d}" for day in range(1, 21)],
                "success_t3": [day % 2 for day in range(20)],
            }
        )
        train, validation, embargo = _purged_date_split(frame)
        self.assertTrue(set(train["trade_date"]).isdisjoint(validation["trade_date"]))
        self.assertEqual(len(embargo), 3)
        self.assertLess(train["trade_date"].max(), min(embargo))
        self.assertLess(max(embargo), validation["trade_date"].min())

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

    def test_prediction_outcome_prefers_versioned_candidate_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "scanner.db"
            with get_connection(db_path) as conn:
                init_db(conn)
                conn.execute(
                    """
                    INSERT INTO scan_runs (
                        id, run_at, trade_date, mode, source, strategy_version
                    ) VALUES (1, '2026-07-01T10:00:00+08:00', '2026-07-01',
                              'intraday', 'test', 'v1')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO stock_signals (
                        id, run_id, trade_date, mode, strategy, code,
                        name, signal_price, created_at
                    ) VALUES (1, 1, '2026-07-01', 'intraday', 'trend', '2330',
                              '台積電', 1000, '2026-07-01T10:00:00+08:00')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO predictions (
                        run_id, signal_id, code, predicted_at, model_version,
                        is_prospective, is_selected, created_at
                    ) VALUES (1, 1, '2330', '2026-07-01T10:05:00+08:00',
                              'test-model', 1, 1, '2026-07-01T10:05:00+08:00')
                    """
                )
                conn.commit()

            save_candidate_events(
                1,
                [
                    {
                        "signal_id": 1,
                        "code": "2330",
                        "name": "台積電",
                        "as_of": "2026-07-01T10:00:00+08:00",
                        "strategies_json": '["trend"]',
                        "strategy_count": 1,
                        "tradable": True,
                        "is_first_eligible_event": True,
                        "is_selected": True,
                        "selection_status": "selected",
                        "policy_version": "test-v1",
                    }
                ],
                db_path=db_path,
            )
            with get_connection(db_path) as conn:
                candidate_id = conn.execute(
                    "SELECT id FROM candidate_events"
                ).fetchone()["id"]
            save_candidate_outcome(
                candidate_id,
                CANDIDATE_EXECUTION_VERSION,
                {
                    "entry_at": "2026-07-02",
                    "entry_price": 1010,
                    "entry_method": "next_day_open",
                    "exit_at": "2026-07-03",
                    "exit_price": 990,
                    "exit_reason": "defense_close",
                    "fixed_net_return_1d": -0.5,
                    "fixed_net_return_3d": 2.0,
                    "fixed_net_return_5d": 3.0,
                    "net_return_3d": -2.2,
                    "benchmark_return_3d": -0.4,
                    "excess_return_3d": -1.8,
                    "max_return_3d": 0.8,
                    "max_drawdown_3d": -2.5,
                    "defense_triggered": True,
                    "success_t3": False,
                    "matured_horizon": 5,
                    "outcome_status": "complete",
                },
                db_path=db_path,
            )
            save_backtest_result(
                1,
                {
                    "entry_date": "2026-07-02",
                    "entry_price": 1010,
                    "entry_method": "legacy_next_open",
                    "net_return_3d": 9.9,
                    "matured_horizon": 3,
                    "outcome_status": "partial",
                },
                db_path=db_path,
            )

            self.assertEqual(update_prediction_outcomes(db_path=db_path), 1)
            with get_connection(db_path) as conn:
                outcome = conn.execute(
                    """
                    SELECT entry_method, net_return_3d, first_barrier, stop_hit_at,
                           matured_horizon, outcome_status
                    FROM prediction_outcomes
                    """
                ).fetchone()
            self.assertEqual(outcome["entry_method"], "next_day_open")
            self.assertEqual(outcome["net_return_3d"], -2.2)
            self.assertEqual(outcome["first_barrier"], "stop")
            self.assertEqual(outcome["stop_hit_at"], "2026-07-03")
            self.assertEqual(outcome["matured_horizon"], 5)
            self.assertEqual(outcome["outcome_status"], "complete")


if __name__ == "__main__":
    unittest.main()
