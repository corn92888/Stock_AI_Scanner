import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from candidate_execution_research import (
    load_pending_eod_candidates,
    run_candidate_execution_research,
)
from database import get_connection, init_db


def _seed_candidate(database, mode="eod"):
    with get_connection(database) as conn:
        init_db(conn)
        run_id = conn.execute(
            """
            INSERT INTO scan_runs (
                run_at, trade_date, mode, source, strategy_version
            ) VALUES ('2026-01-02T14:00:00+08:00', '2026-01-02', ?, 'test', 'v1')
            """,
            (mode,),
        ).lastrowid
        return conn.execute(
            """
            INSERT INTO candidate_events (
                run_id, code, name, as_of, strategies_json, strategy_count,
                raw_rank, signal_price, tradable, block_reasons_json,
                risk_flags_json, is_first_eligible_event, is_selected,
                selection_status, policy_version, policy_config_json,
                snapshot_json, created_at, updated_at
            ) VALUES (?, '2330', 'TSMC', '2026-01-02T14:00:00+08:00',
                      '["trend"]', 1, 1, 102, 1, '[]', '[]', 1, 1,
                      'selected', 'test_v1', '{}', '{}',
                      '2026-01-02T14:00:00+08:00',
                      '2026-01-02T14:00:00+08:00')
            """,
            (run_id,),
        ).lastrowid


def _prices(*_args, **_kwargs):
    index = pd.bdate_range("2026-01-02", periods=26)
    steps = pd.Series(range(26), index=index, dtype=float)
    return pd.DataFrame(
        {
            "Open": 100 + steps,
            "High": 102 + steps,
            "Low": 98 + steps,
            "Close": 101 + steps,
            "Adj Close": 101 + steps,
            "Volume": 1_000_000,
        },
        index=index,
    )


class CandidateExecutionResearchTests(unittest.TestCase):
    def test_daily_research_matures_and_persists_all_execution_scenarios(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "scanner.db"
            candidate_id = _seed_candidate(database)

            metrics = run_candidate_execution_research(
                db_path=database, price_loader=_prices
            )
            with get_connection(database) as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM candidate_execution_scenarios
                    WHERE candidate_id=? ORDER BY entry_method
                    """,
                    (candidate_id,),
                ).fetchall()

            self.assertEqual(metrics["candidates"], 1)
            self.assertEqual(metrics["scenarios"], 4)
            self.assertEqual(len(rows), 4)
            self.assertTrue(all(row["outcome_status"] == "complete" for row in rows))
            self.assertTrue(all(row["matured_horizon"] == 20 for row in rows))
            self.assertIn("20", json.loads(rows[0]["labels_json"]))
            self.assertEqual(load_pending_eod_candidates(database), [])

    def test_intraday_candidates_are_not_mixed_into_eod_research(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "scanner.db"
            _seed_candidate(database, mode="intraday")

            self.assertEqual(load_pending_eod_candidates(database), [])

    def test_default_loader_batches_stock_and_benchmark_history(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "scanner.db"
            _seed_candidate(database)
            with patch(
                "candidate_execution_research.download_replay_history",
                return_value={"2330.TW": _prices(), "^TWII": _prices()},
            ) as downloader:
                metrics = run_candidate_execution_research(db_path=database)

            self.assertEqual(downloader.call_count, 1)
            requested = set(downloader.call_args.args[0])
            self.assertEqual(requested, {"2330.TW", "^TWII"})
            self.assertEqual(metrics["scenarios"], 4)


if __name__ == "__main__":
    unittest.main()
