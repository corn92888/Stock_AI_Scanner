import tempfile
import unittest
from pathlib import Path

import pandas as pd

from backtest import PriceCache
from database import get_connection, init_db
from paper_trading import (
    ACCOUNT_SPECS,
    PaperTradingConfig,
    apply_portfolio_policy,
    save_simulation,
    simulate_account,
)


def price_frame():
    index = pd.bdate_range("2026-01-02", periods=6)
    closes = [100.0, 100.0, 105.0, 110.0, 108.0, 111.0]
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [value + 1 for value in closes],
            "Low": [value - 1 for value in closes],
            "Close": closes,
            "Adj Close": closes,
            "Volume": [1_000_000] * len(index),
        },
        index=index,
    )


def signal(source_id, code, chase_limit=105.0):
    return {
        "source_type": "candidate",
        "source_id": source_id,
        "candidate_id": None,
        "prediction_id": None,
        "signal_date": "2026-01-02",
        "signal_at": "2026-01-02T10:00:00+08:00",
        "code": code,
        "name": f"Stock {code}",
        "industry": "Test",
        "rank_order": source_id,
        "model_version": None,
        "entry_status": "filled",
        "outcome_skip_reason": None,
        "raw_chase_limit": chase_limit,
        "raw_stop_price": 95.0,
        "entry_at": "2026-01-05",
        "entry_price": 100.0,
        "entry_adjustment_factor": 1.0,
        "exit_at": "2026-01-07",
        "exit_price": 110.0,
        "exit_reason": "time_exit_t3",
        "max_return_pct": 11.0,
        "max_drawdown_pct": -1.0,
        "matured_horizon": 5,
        "outcome_status": "complete",
    }


def tournament_signal(source_id, code, score, industry="Industry"):
    row = signal(source_id, code)
    row.update(
        source_type="prediction",
        prediction_id=source_id,
        signal_date="2026-07-20",
        signal_at="2026-07-20T14:10:00+08:00",
        rank_order=source_id,
        industry=industry,
        final_score=score,
        probability_t3=0.60,
        expected_excess_return_3d=1.0,
        expected_max_drawdown_3d=-2.0,
        action="shadow_neutral",
        allocation_weight=None,
    )
    return row


class PaperTradingTests(unittest.TestCase):
    def setUp(self):
        self.config = PaperTradingConfig(
            starting_cash=100_000.0,
            buy_fee_rate=0.0,
            sell_fee_rate=0.0,
            sell_tax_rate=0.0,
            slippage_rate=0.0,
            min_trade_value=1_000.0,
        )
        self.cache = PriceCache(
            start="2026-01-01",
            end="2026-01-10",
            loader=lambda ticker, start, end: price_frame(),
        )

    def test_replay_respects_capital_and_chase_limit(self):
        result = simulate_account(
            ACCOUNT_SPECS[0],
            [signal(1, "2330"), signal(2, "2317", chase_limit=90.0)],
            config=self.config,
            price_cache=self.cache,
            as_of="2026-01-09",
        )
        trades = {trade["source_id"]: trade for trade in result["trades"]}
        self.assertEqual(trades[1]["status"], "closed")
        self.assertEqual(trades[1]["quantity"], 200)
        self.assertEqual(trades[1]["realized_pnl"], 2_000.0)
        self.assertEqual(trades[2]["status"], "skipped")
        self.assertEqual(trades[2]["skip_reason"], "above_chase_limit")
        self.assertEqual(result["account"]["equity"], 102_000.0)
        self.assertAlmostEqual(result["account"]["total_return_pct"], 2.0)
        self.assertEqual(result["account"]["closed_trades"], 1)

    def test_signal_without_next_open_stays_pending(self):
        pending = signal(3, "2454")
        pending.update(entry_at=None, entry_price=None, exit_at=None, exit_price=None)
        result = simulate_account(
            ACCOUNT_SPECS[1],
            [pending],
            config=self.config,
            price_cache=self.cache,
            as_of="2026-01-02",
        )
        self.assertEqual(result["trades"][0]["status"], "pending")
        self.assertEqual(result["trades"][0]["skip_reason"], "awaiting_next_open")
        self.assertEqual(result["account"]["pending_orders"], 1)
        self.assertEqual(result["account"]["equity"], 100_000.0)

    def test_risk_budget_reduces_position_size_for_a_distant_stop(self):
        distant_stop = signal(4, "3008")
        distant_stop["raw_stop_price"] = 80.0
        result = simulate_account(
            ACCOUNT_SPECS[0],
            [distant_stop],
            config=self.config,
            price_cache=self.cache,
            as_of="2026-01-09",
        )
        trade = result["trades"][0]
        self.assertEqual(trade["quantity"], 50)
        self.assertEqual(trade["realized_pnl"], 500.0)

    def test_rejected_execution_outcome_never_opens_a_position(self):
        rejected = signal(5, "2303")
        rejected.update(
            entry_status="skipped",
            outcome_skip_reason="gap_below_defense",
        )
        result = simulate_account(
            ACCOUNT_SPECS[0],
            [rejected],
            config=self.config,
            price_cache=self.cache,
            as_of="2026-01-09",
        )
        self.assertEqual(result["trades"][0]["status"], "skipped")
        self.assertEqual(
            result["trades"][0]["skip_reason"], "gap_below_defense"
        )
        self.assertEqual(result["account"]["equity"], 100_000.0)

    def test_simulation_persists_account_trades_and_equity_curve(self):
        result = simulate_account(
            ACCOUNT_SPECS[0],
            [signal(1, "2330")],
            config=self.config,
            price_cache=self.cache,
            as_of="2026-01-09",
        )
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "scanner.db"
            account_id = save_simulation(result, db_path=db_path)
            with get_connection(db_path) as conn:
                init_db(conn)
                account = conn.execute(
                    "SELECT equity, closed_trades FROM paper_accounts WHERE id=?",
                    (account_id,),
                ).fetchone()
                trade_count = conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]
                snapshot_count = conn.execute(
                    "SELECT COUNT(*) FROM paper_equity_snapshots"
                ).fetchone()[0]
            self.assertEqual(account["equity"], 102_000.0)
            self.assertEqual(account["closed_trades"], 1)
            self.assertEqual(trade_count, 1)
            self.assertGreater(snapshot_count, 1)

    def test_top5_policy_starts_prospectively_and_diversifies_industries(self):
        spec = next(
            item for item in ACCOUNT_SPECS if item.account_key == "ai_top5_diversified_v1"
        )
        old = tournament_signal(1, "1101", 99.0, "Cement")
        old["signal_date"] = "2026-07-17"
        rows = [
            old,
            tournament_signal(2, "2330", 90.0, "Semiconductor"),
            tournament_signal(3, "2454", 89.0, "Semiconductor"),
            tournament_signal(4, "2317", 88.0, "Electronics"),
            tournament_signal(5, "1301", 87.0, "Plastics"),
        ]
        selected = apply_portfolio_policy(rows, spec)
        self.assertEqual([row["code"] for row in selected], ["2330", "2317", "1301"])
        self.assertTrue(all(row["signal_date"] >= "2026-07-20" for row in selected))
        self.assertTrue(all(row["allocation_weight"] == 0.1 for row in selected))

    def test_top10_policy_uses_a_bounded_score_weighted_budget(self):
        spec = next(
            item for item in ACCOUNT_SPECS if item.account_key == "ai_top10_weighted_v1"
        )
        rows = [
            tournament_signal(index, str(6000 + index), 50.0 + index, f"Industry {index}")
            for index in range(1, 11)
        ]
        selected = apply_portfolio_policy(rows, spec)
        weights = [row["allocation_weight"] for row in selected]
        self.assertEqual(len(selected), 10)
        self.assertAlmostEqual(sum(weights), 0.5)
        self.assertGreater(weights[0], weights[-1])
        self.assertTrue(all(weight <= 0.075 for weight in weights))

    def test_industry_exposure_cap_blocks_a_second_concentrated_position(self):
        first = tournament_signal(20, "2330", 90.0, "Semiconductor")
        second = tournament_signal(21, "2454", 89.0, "Semiconductor")
        first["allocation_weight"] = 0.2
        second["allocation_weight"] = 0.2
        config = PaperTradingConfig(
            starting_cash=100_000.0,
            buy_fee_rate=0.0,
            sell_fee_rate=0.0,
            sell_tax_rate=0.0,
            slippage_rate=0.0,
            min_trade_value=1_000.0,
            max_industry_exposure_pct=0.2,
        )
        result = simulate_account(
            ACCOUNT_SPECS[2],
            [first, second],
            config=config,
            price_cache=self.cache,
            as_of="2026-01-09",
        )
        trades = result["trades"]
        self.assertEqual(trades[0]["status"], "closed")
        self.assertEqual(trades[1]["status"], "skipped")
        self.assertEqual(trades[1]["skip_reason"], "industry_exposure_limit")


if __name__ == "__main__":
    unittest.main()
