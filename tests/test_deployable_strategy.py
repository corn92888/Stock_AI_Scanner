import json
import tempfile
import unittest
from pathlib import Path

from alpha_strategy_v2 import ALPHA_MODEL_VERSION
from database import get_connection, init_db
from deployable_strategy import (
    STRATEGY_VERSION,
    build_live_decision,
    run_deployable_strategy,
)
from export_dashboard_snapshot import build_dashboard_snapshot


def evidence():
    return {
        "version": "test",
        "strategyVersion": STRATEGY_VERSION,
        "modelVersion": ALPHA_MODEL_VERSION,
        "qualified": True,
        "manualMicroAllowed": True,
        "automaticBrokerTransmission": False,
        "classification": "historically_validated_manual_micro",
        "holdoutCaveat": "test caveat",
        "holdout": {"year": 2025, "trades": 26, "totalReturnPct": 5.9},
        "years": [],
        "gates": [],
        "rules": {
            "marketUpRatioMinimum": 50.0,
            "positionWeight": 0.08,
            "holdingHorizon": 10,
            "maxPositions": 10,
            "maxIndustryPositions": 2,
            "maxEntryGapPct": 3.0,
        },
    }


def active_run(model_version=ALPHA_MODEL_VERSION):
    return {
        "signal_date": "2026-08-10",
        "model_version": model_version,
        "status": "active",
        "confidence": 2.0,
        "confidence_threshold": 1.0,
    }


def signals():
    return [
        {
            "code": "2330",
            "name": "TSMC",
            "industry": "Semiconductor",
            "rank_order": 1,
            "signal_price": 100.0,
            "predicted_alpha": 2.5,
        },
        {
            "code": "2317",
            "name": "Hon Hai",
            "industry": "Electronics",
            "rank_order": 2,
            "signal_price": 200.0,
            "predicted_alpha": 2.0,
        },
    ]


class DeployableStrategyTests(unittest.TestCase):
    def test_fixed_gates_select_only_the_top_ranked_signal(self):
        decision = build_live_decision(active_run(), signals(), 55.0, evidence())

        self.assertEqual(decision["action"], "BUY_NEXT_OPEN")
        self.assertEqual(decision["selected"]["code"], "2330")
        self.assertAlmostEqual(decision["selected"]["maxEntryPrice"], 103.0)
        self.assertEqual(decision["targetWeight"], 0.08)
        self.assertEqual(decision["holdingHorizon"], 10)

    def test_market_breadth_gate_holds_cash(self):
        decision = build_live_decision(active_run(), signals(), 49.9, evidence())

        self.assertEqual(decision["action"], "CASH")
        self.assertEqual(decision["reasonCodes"], ["market_breadth_below_gate"])
        self.assertIsNone(decision["selected"])

    def test_model_version_mismatch_fails_closed(self):
        decision = build_live_decision(
            active_run("old-model"), signals(), 55.0, evidence()
        )

        self.assertEqual(decision["action"], "REFRESH")
        self.assertEqual(decision["reasonCodes"], ["model_version_mismatch"])

    def test_saved_decision_is_idempotent_and_exported(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "scanner.db"
            evidence_path = Path(directory) / "evidence.json"
            evidence_path.write_text(json.dumps(evidence()), encoding="utf-8")
            with get_connection(db_path) as conn:
                init_db(conn)
                run_id = conn.execute(
                    """
                    INSERT INTO alpha_live_runs (
                        signal_date, generated_at, model_version,
                        artifact_fingerprint, dataset_fingerprint, status,
                        confidence, confidence_threshold, universe_count,
                        eligible_count, selected_count, diagnostics_json
                    ) VALUES (
                        '2026-08-10', '2026-08-10T14:10:00+08:00', ?,
                        'artifact', 'dataset', 'active', 2.0, 1.0,
                        1000, 300, 1, '{}'
                    )
                    """,
                    (ALPHA_MODEL_VERSION,),
                ).lastrowid
                conn.execute(
                    """
                    INSERT INTO alpha_live_signals (
                        run_id, code, name, industry, rank_order, signal_price,
                        predicted_alpha, allocation_weight, holding_horizon,
                        created_at
                    ) VALUES (?, '2330', 'TSMC', 'Semiconductor', 1, 100,
                              2.5, 0.333333, 10,
                              '2026-08-10T14:10:00+08:00')
                    """,
                    (run_id,),
                )
                conn.execute(
                    """
                    INSERT INTO alpha_live_candidates (
                        run_id, code, name, industry, signal_price,
                        predicted_alpha, market_up_ratio, created_at
                    ) VALUES (?, '2330', 'TSMC', 'Semiconductor', 100,
                              2.5, 55, '2026-08-10T14:10:00+08:00')
                    """,
                    (run_id,),
                )

            first = run_deployable_strategy(db_path, evidence_path)
            second = run_deployable_strategy(db_path, evidence_path)
            with get_connection(db_path) as conn:
                count = conn.execute(
                    "SELECT COUNT(*) FROM deployable_strategy_snapshots"
                ).fetchone()[0]
            snapshot = build_dashboard_snapshot(db_path)["deployableStrategy"]

        self.assertEqual(first["action"], "BUY_NEXT_OPEN")
        self.assertEqual(second["action"], "BUY_NEXT_OPEN")
        self.assertEqual(count, 1)
        self.assertEqual(snapshot["selected"]["code"], "2330")
        self.assertTrue(snapshot["evidence"]["manualMicroAllowed"])


if __name__ == "__main__":
    unittest.main()
