import tempfile
import unittest
from pathlib import Path

from database import get_connection, init_db
from paper_settlement import run_opening_settlement


def _seed_pending_trade(db_path):
    with get_connection(db_path) as conn:
        init_db(conn)
        account_id = conn.execute(
            """
            INSERT INTO paper_accounts (
                account_key, name, strategy_kind, evidence_mode,
                policy_version, execution_version, starting_cash, cash, equity,
                total_return_pct, max_drawdown_pct, closed_trades, winning_trades,
                open_positions, pending_orders, skipped_orders, status, config_json,
                created_at, updated_at
            ) VALUES (
                'ai_top3_equal_v1', 'AI Top 3', 'ai_capital',
                'prospective_tournament', 'paper_v1', 'execution_v1',
                1000000, 1000000, 1000000, 0, 0, 0, 0, 0, 1, 0,
                'shadow', '{}', '2026-07-17T14:20:00+08:00',
                '2026-07-17T14:20:00+08:00'
            )
            """
        ).lastrowid
        conn.execute(
            """
            INSERT INTO paper_trades (
                account_id, source_type, source_id, signal_date, signal_at,
                code, name, status, skip_reason, created_at, updated_at
            ) VALUES (?, 'prediction', 10, '2026-07-17',
                      '2026-07-17T14:10:00+08:00', '2330', '台積電',
                      'pending', 'awaiting_next_open',
                      '2026-07-17T14:20:00+08:00',
                      '2026-07-17T14:20:00+08:00')
            """,
            (account_id,),
        )


class PaperSettlementTests(unittest.TestCase):
    def test_settlement_uses_only_prior_eod_candidates_and_audits_transition(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "scanner.db"
            _seed_pending_trade(db_path)
            calls = {}

            def pending_loader(**kwargs):
                calls["pending"] = kwargs
                return [{"id": 1}]

            def backtest_runner(**kwargs):
                calls["backtest"] = kwargs
                return {"saved": 1, "complete": 0, "partial": 1, "skipped": 0}

            def paper_runner(**kwargs):
                calls["paper"] = kwargs
                with get_connection(db_path) as conn:
                    conn.execute(
                        """
                        UPDATE paper_trades
                        SET status='open', skip_reason=NULL,
                            entry_at='2026-07-20', entry_price=100,
                            updated_at='2026-07-20T09:35:00+08:00'
                        """
                    )
                return [{"account_key": "ai_top3_equal_v1"}]

            notifications = []
            result = run_opening_settlement(
                db_path=db_path,
                session_date="2026-07-20",
                source="test",
                send_notification=True,
                pending_loader=pending_loader,
                backtest_runner=backtest_runner,
                paper_runner=paper_runner,
                notifier=lambda payload: notifications.append(payload) or True,
            )

            self.assertEqual(calls["pending"]["modes"], ("eod",))
            self.assertEqual(calls["backtest"]["trade_date_before"], "2026-07-20")
            self.assertTrue(calls["backtest"]["newest_first"])
            self.assertEqual(calls["paper"]["as_of"], "2026-07-20")
            self.assertEqual(result["status"], "completed")
            self.assertTrue(result["lookaheadProtected"])
            self.assertEqual(result["transitions"][0]["from"], "pending")
            self.assertEqual(result["transitions"][0]["to"], "open")
            self.assertEqual(len(notifications), 1)

            with get_connection(db_path) as conn:
                audit = conn.execute(
                    "SELECT * FROM paper_settlement_runs"
                ).fetchone()
            self.assertEqual(audit["status"], "completed")
            self.assertEqual(audit["new_open_positions"], 1)
            self.assertEqual(audit["outcomes_saved"], 1)

    def test_completed_session_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "scanner.db"
            _seed_pending_trade(db_path)

            def paper_runner(**kwargs):
                return []

            run_opening_settlement(
                db_path=db_path,
                session_date="2026-07-20",
                pending_loader=lambda **kwargs: [],
                backtest_runner=lambda **kwargs: {"saved": 0},
                paper_runner=paper_runner,
            )
            second = run_opening_settlement(
                db_path=db_path,
                session_date="2026-07-20",
                pending_loader=lambda **kwargs: self.fail("loader should not run"),
                backtest_runner=lambda **kwargs: self.fail("backtest should not run"),
                paper_runner=lambda **kwargs: self.fail("paper should not run"),
            )
            self.assertEqual(second["status"], "skipped")
            self.assertEqual(second["reason"], "session_already_settled")


if __name__ == "__main__":
    unittest.main()
