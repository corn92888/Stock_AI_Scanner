import tempfile
import unittest
from pathlib import Path

import pandas as pd

from backtest import PriceCache
from database import get_connection, init_db
from paper_trading import (
    ACCOUNT_SPECS,
    PaperTradingConfig,
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


if __name__ == "__main__":
    unittest.main()
