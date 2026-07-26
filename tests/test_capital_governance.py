import json
import tempfile
import unittest
from pathlib import Path

from capital_governance import (
    build_order_intents,
    evaluate_capital_ladder,
    run_capital_governance,
)
from database import (
    ALPHA_FORWARD_START_DATE,
    ALPHA_FORWARD_VERSION,
    get_connection,
    init_db,
)


def forward_metrics(**overrides):
    metrics = {
        "version": ALPHA_FORWARD_VERSION,
        "evidence_start_date": ALPHA_FORWARD_START_DATE,
        "state": "COLLECTING",
        "data_quality_status": "healthy",
        "latest_signal_status": "active",
        "decision_days": 1,
        "closed_trades": 0,
        "total_return_pct": 0.0,
        "max_drawdown_pct": 0.0,
        "avg_excess_return_pct": None,
        "profitable_month_count": 0,
        "profitable_month_rate_pct": None,
        "probabilistic_sharpe": None,
        "candidate_pool_rows": 200,
        "quote_health": {"coverage_pct": 99.0},
        "research_health": {"stale_outcomes": 0},
    }
    metrics.update(overrides)
    return metrics


def alpha_signal(**overrides):
    signal = {
        "signal_date": "2026-07-24",
        "code": "2330",
        "name": "TSMC",
        "industry": "Semiconductor",
        "signal_price": 100.0,
        "predicted_alpha": 2.5,
        "turnover_20d_billion": 10.0,
        "market_return_20d": 3.0,
        "market_above_ma200": 1.0,
        "market_up_ratio": 55.0,
    }
    signal.update(overrides)
    return signal


class CapitalGovernanceTests(unittest.TestCase):
    def test_new_strategy_remains_shadow_until_micro_evidence(self):
        governance = evaluate_capital_ladder(forward_metrics())

        self.assertEqual(governance["stage"], "SHADOW")
        self.assertFalse(governance["order_preview_enabled"])
        self.assertEqual(governance["max_strategy_weight"], 0.0)
        self.assertEqual(governance["next_stage"], "MICRO")

    def test_micro_stage_generates_manual_approval_preview(self):
        governance = evaluate_capital_ladder(
            forward_metrics(
                decision_days=20,
                closed_trades=30,
                total_return_pct=1.0,
                avg_excess_return_pct=0.2,
                max_drawdown_pct=-2.0,
            ),
            reference_capital=1_000_000,
        )
        intents = build_order_intents(governance, [alpha_signal()])

        self.assertEqual(governance["stage"], "MICRO")
        self.assertTrue(governance["order_preview_enabled"])
        self.assertFalse(governance["live_transmission_enabled"])
        self.assertEqual(intents[0]["decision_status"], "manual_approval_required")
        self.assertEqual(intents[0]["target_weight"], 0.005)
        self.assertEqual(intents[0]["max_notional"], 5_000)
        self.assertEqual(intents[0]["suggested_quantity"], 50)

    def test_market_regime_blocks_an_otherwise_eligible_order(self):
        governance = evaluate_capital_ladder(
            forward_metrics(
                decision_days=20,
                closed_trades=30,
                total_return_pct=1.0,
                avg_excess_return_pct=0.2,
                max_drawdown_pct=-2.0,
            )
        )
        intents = build_order_intents(
            governance,
            [alpha_signal(market_return_20d=-5.0)],
        )

        self.assertEqual(
            intents[0]["decision_status"],
            "blocked_by_pretrade_policy",
        )
        self.assertIn("market_regime_blocked", intents[0]["reason_codes"])
        self.assertEqual(intents[0]["target_weight"], 0.0)

    def test_forward_pause_disables_all_capital(self):
        governance = evaluate_capital_ladder(
            forward_metrics(
                state="PAUSED",
                data_quality_status="critical",
                max_drawdown_pct=-13.0,
            )
        )

        self.assertEqual(governance["stage"], "PAUSED")
        self.assertEqual(governance["max_positions"], 0)
        self.assertFalse(governance["order_preview_enabled"])

    def test_run_persists_governance_and_auditable_intents(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "scanner.db"
            with get_connection(database) as conn:
                init_db(conn)
                metrics = forward_metrics(
                    decision_days=20,
                    closed_trades=30,
                    total_return_pct=1.0,
                    avg_excess_return_pct=0.2,
                    max_drawdown_pct=-2.0,
                )
                conn.execute(
                    """
                    INSERT INTO alpha_forward_snapshots (
                        evaluated_at, validation_version, evidence_start_date,
                        state, allow_new_positions, metrics_json
                    ) VALUES (?, ?, ?, 'COLLECTING', 1, ?)
                    """,
                    (
                        "2026-07-24T14:30:00+08:00",
                        ALPHA_FORWARD_VERSION,
                        ALPHA_FORWARD_START_DATE,
                        json.dumps(metrics),
                    ),
                )
                run_id = conn.execute(
                    """
                    INSERT INTO alpha_live_runs (
                        signal_date, generated_at, model_version,
                        artifact_fingerprint, dataset_fingerprint, status,
                        confidence, confidence_threshold, universe_count,
                        eligible_count, selected_count, diagnostics_json
                    ) VALUES (
                        '2026-07-24', '2026-07-24T14:20:00+08:00',
                        'alpha-v2', 'artifact', 'dataset', 'active',
                        2.0, 1.0, 1000, 300, 1, '{}'
                    )
                    """
                ).lastrowid
                conn.execute(
                    """
                    INSERT INTO alpha_live_signals (
                        run_id, code, name, industry, rank_order, signal_price,
                        predicted_alpha, allocation_weight, holding_horizon,
                        created_at
                    ) VALUES (
                        ?, '2330', 'TSMC', 'Semiconductor', 1, 100,
                        2.5, 1.0, 10, '2026-07-24T14:20:00+08:00'
                    )
                    """,
                    (run_id,),
                )
                conn.execute(
                    """
                    INSERT INTO alpha_live_candidates (
                        run_id, code, name, industry, signal_price,
                        predicted_alpha, turnover_20d_billion,
                        market_return_20d, market_above_ma200, market_up_ratio,
                        created_at
                    ) VALUES (
                        ?, '2330', 'TSMC', 'Semiconductor', 100,
                        2.5, 10, 3, 1, 55,
                        '2026-07-24T14:20:00+08:00'
                    )
                    """,
                    (run_id,),
                )

            result = run_capital_governance(
                database,
                reference_capital=1_000_000,
            )

            self.assertEqual(result["governance"]["stage"], "MICRO")
            with get_connection(database) as conn:
                snapshot = conn.execute(
                    "SELECT stage FROM live_capital_snapshots"
                ).fetchone()
                intent = conn.execute(
                    """
                    SELECT decision_status, approval_status, suggested_quantity
                    FROM live_order_intents
                    """
                ).fetchone()
            self.assertEqual(snapshot["stage"], "MICRO")
            self.assertEqual(intent["decision_status"], "manual_approval_required")
            self.assertEqual(intent["approval_status"], "pending_manual")
            self.assertEqual(intent["suggested_quantity"], 50)


if __name__ == "__main__":
    unittest.main()
