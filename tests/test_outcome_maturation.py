import unittest

from outcome_maturation import drain_overdue_outcomes


class OutcomeMaturationTests(unittest.TestCase):
    def test_drains_multiple_batches_until_stale_queue_is_empty(self):
        stale_values = iter((900, 400, 0))
        backtest_limits = []
        sync_calls = []

        def health_builder(_db_path):
            stale = next(stale_values)
            return {
                "stale_outcomes": stale,
                "maturity_coverage_pct": 100.0 if stale == 0 else 50.0,
            }

        def backtest_runner(**kwargs):
            backtest_limits.append(kwargs["limit"])
            return {"saved": kwargs["limit"]}

        def sync_runner(**kwargs):
            sync_calls.append(kwargs["db_path"])
            return 10

        result = drain_overdue_outcomes(
            db_path="test.db",
            batch_size=500,
            max_batches=4,
            backtest_runner=backtest_runner,
            sync_runner=sync_runner,
            health_builder=health_builder,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["resolved"], 900)
        self.assertEqual(result["final_stale"], 0)
        self.assertEqual(backtest_limits, [500, 400])
        self.assertEqual(sync_calls, ["test.db", "test.db"])

    def test_stops_when_a_batch_cannot_mature_any_outcomes(self):
        health_calls = iter(
            (
                {"stale_outcomes": 20, "maturity_coverage_pct": 50.0},
                {"stale_outcomes": 20, "maturity_coverage_pct": 50.0},
            )
        )
        result = drain_overdue_outcomes(
            db_path="test.db",
            batch_size=500,
            max_batches=4,
            backtest_runner=lambda **_kwargs: {"saved": 0},
            sync_runner=lambda **_kwargs: 0,
            health_builder=lambda _db_path: next(health_calls),
        )
        self.assertEqual(result["status"], "stalled")
        self.assertEqual(result["stop_reason"], "no_maturity_progress")
        self.assertEqual(result["final_stale"], 20)

    def test_skips_backtest_when_no_outcomes_are_stale(self):
        result = drain_overdue_outcomes(
            db_path="test.db",
            backtest_runner=lambda **_kwargs: self.fail("backtest should not run"),
            sync_runner=lambda **_kwargs: self.fail("sync should not run"),
            health_builder=lambda _db_path: {
                "stale_outcomes": 0,
                "maturity_coverage_pct": 100.0,
            },
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["stop_reason"], "already_current")


if __name__ == "__main__":
    unittest.main()
