import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from historical_replay import (
    HistoricalReplayConfig,
    build_historical_market_context,
    load_replay_universe,
    resolve_transfer_history_aliases,
    run_historical_replay,
)


def _price_frame():
    dates = pd.bdate_range("2024-01-02", periods=340)
    close = pd.Series(100.0, index=dates)
    decision_position = 330
    close.iloc[decision_position - 1] = 100.0
    close.iloc[decision_position] = 102.0
    close.iloc[decision_position + 1 : decision_position + 7] = [103, 104, 105, 106, 107, 108]
    frame = pd.DataFrame(
        {
            "Open": close - 0.5,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Adj Close": close,
            "Volume": 5_000_000.0,
        },
        index=dates,
    )
    frame.iloc[decision_position, frame.columns.get_loc("Volume")] = 10_000_000
    frame.iloc[decision_position + 1, frame.columns.get_loc("Open")] = 102.5
    return frame, dates[decision_position]


class HistoricalReplayTests(unittest.TestCase):
    def test_point_in_time_universe_never_uses_a_future_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            universe_file = Path(directory) / "universe.csv"
            universe_file.write_text(
                "snapshot_date,code,name,industry,market\n"
                "2025-01-01,2330,台積電,半導體,上市\n"
                "2025-02-01,2330,台積電,半導體,上市\n"
                "2025-02-01,2454,聯發科,半導體,上市\n",
                encoding="utf-8",
            )

            universe, source = load_replay_universe(universe_file=universe_file)

            self.assertEqual(source, "point_in_time_csv:universe.csv")
            self.assertEqual(universe.snapshot_count, 2)
            self.assertIsNone(universe.stock_on("2330", "2024-12-31"))
            self.assertIsNone(universe.stock_on("2454", "2025-01-15"))
            self.assertEqual(universe.stock_on("2454", "2025-02-15").name, "聯發科")

    def test_market_context_contains_only_information_known_before_decision(self):
        frame, decision_date = _price_frame()
        universe = {
            "2330": type(
                "Stock", (), {"industry": "半導體", "group": "半導體", "market": "上市"}
            )()
        }
        context = build_historical_market_context(
            decision_date,
            {"2330.TW": frame},
            {"2330.TW": "2330"},
            universe,
        )

        self.assertEqual(context["captured_at"].date(), decision_date.date())
        self.assertLess(context["history"]["2330.TW"].index.max(), decision_date)
        self.assertLessEqual(len(context["history"]["2330.TW"]), 20)
        self.assertEqual(context["realtime"]["2330"]["Close"], 102.0)

    def test_market_transfer_uses_the_ticker_suffix_active_on_the_decision_date(self):
        with tempfile.TemporaryDirectory() as directory:
            universe_file = Path(directory) / "universe.csv"
            universe_file.write_text(
                "snapshot_date,code,name,industry,market\n"
                "2025-01-01,1234,測試股,電子,上櫃\n"
                "2025-02-01,1234,測試股,電子,上市\n",
                encoding="utf-8",
            )
            universe, _ = load_replay_universe(universe_file=universe_file)
            dates = pd.to_datetime(["2025-01-02", "2025-01-15", "2025-02-15"])
            frame = pd.DataFrame(
                {
                    "Open": [10, 11, 12],
                    "High": [11, 12, 13],
                    "Low": [9, 10, 11],
                    "Close": [10, 11, 12],
                    "Volume": [1_000_000, 1_000_000, 1_000_000],
                },
                index=dates,
            )
            histories = {"1234.TWO": frame, "1234.TW": frame}
            ticker_to_code = {"1234.TWO": "1234", "1234.TW": "1234"}

            january = build_historical_market_context(
                "2025-01-15", histories, ticker_to_code, universe
            )
            february = build_historical_market_context(
                "2025-02-15", histories, ticker_to_code, universe
            )

            self.assertEqual(january["code_to_yf"]["1234"], "1234.TWO")
            self.assertEqual(february["code_to_yf"]["1234"], "1234.TW")
            self.assertEqual(len(january["realtime"]), 1)
            self.assertEqual(len(february["realtime"]), 1)

    def test_market_transfer_can_reuse_yahoo_history_remapped_to_new_suffix(self):
        with tempfile.TemporaryDirectory() as directory:
            universe_file = Path(directory) / "universe.csv"
            universe_file.write_text(
                "snapshot_date,code,name,industry,market\n"
                "2025-01-01,1234,測試股,電子,上櫃\n"
                "2025-02-01,1234,測試股,電子,上市\n",
                encoding="utf-8",
            )
            universe, _ = load_replay_universe(universe_file=universe_file)
            dates = pd.to_datetime(["2025-01-02", "2025-01-15", "2025-02-15"])
            frame = pd.DataFrame(
                {
                    "Open": [10, 11, 12],
                    "High": [11, 12, 13],
                    "Low": [9, 10, 11],
                    "Close": [10, 11, 12],
                    "Volume": [1_000_000, 1_000_000, 1_000_000],
                },
                index=dates,
            )

            histories, aliases = resolve_transfer_history_aliases(
                {"1234.TW": frame}, universe
            )
            january = build_historical_market_context(
                "2025-01-15",
                histories,
                {"1234.TWO": "1234", "1234.TW": "1234"},
                universe,
            )

            self.assertEqual(aliases, {"1234.TWO": "1234.TW"})
            self.assertEqual(january["code_to_yf"]["1234"], "1234.TWO")
            self.assertEqual(january["realtime"]["1234"]["Close"], 11.0)

    def test_replay_isolated_from_live_tables_and_saves_mature_outcome(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "scanner.db"
            universe_file = root / "universe.csv"
            universe_file.write_text(
                "code,name,industry,market\n2330,台積電,半導體,上市\n",
                encoding="utf-8",
            )
            frame, decision_date = _price_frame()
            benchmark = frame.copy()
            benchmark[["Open", "High", "Low", "Close", "Adj Close"]] = 100.0

            def history_loader(tickers, start, end, chunk_size):
                self.assertEqual(tickers, ["2330.TW"])
                return {"2330.TW": frame}

            def benchmark_loader(start, end):
                return benchmark

            signal = {
                "產業族群": "半導體",
                "代號": "2330",
                "名稱": "台積電",
                "現價": 102.0,
                "防守價": 96.0,
                "漲跌幅": 2.0,
                "成交量(張)": 10_000,
                "RSI": 60.0,
                "條件": "test",
                "策略": "順勢突破",
            }
            config = HistoricalReplayConfig(
                start_date=decision_date.date().isoformat(),
                end_date=decision_date.date().isoformat(),
            )
            with patch(
                "historical_replay.collect_strategy_signals",
                return_value={decision_date.normalize(): [signal]},
            ):
                result = run_historical_replay(
                    config,
                    db_path=database,
                    universe_file=universe_file,
                    replace=True,
                    history_loader=history_loader,
                    benchmark_loader=benchmark_loader,
                )

            import sqlite3

            conn = sqlite3.connect(database)
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM candidate_events").fetchone()[0], 0)
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM historical_replay_events").fetchone()[0],
                    1,
                )
                checkpoint = conn.execute(
                    "SELECT status, candidate_events FROM historical_replay_checkpoints"
                ).fetchone()
                self.assertEqual(checkpoint, ("completed", 1))
                outcome = conn.execute(
                    "SELECT entry_status, matured_horizon, outcome_status "
                    "FROM historical_replay_outcomes"
                ).fetchone()
                self.assertEqual(outcome, ("filled", 5, "complete"))
            finally:
                conn.close()
            self.assertEqual(result["matured_t3"], 1)
            self.assertEqual(result["selected_events"], 1)

            resumed = run_historical_replay(
                config,
                db_path=database,
                universe_file=universe_file,
                resume=True,
                history_loader=history_loader,
                benchmark_loader=benchmark_loader,
            )
            self.assertTrue(resumed["already_completed"])

            import sqlite3

            conn = sqlite3.connect(database)
            try:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM historical_replay_events").fetchone()[0],
                    1,
                )
                conn.execute("DELETE FROM historical_replay_outcomes")
                conn.execute("DELETE FROM historical_replay_events")
                conn.commit()
            finally:
                conn.close()

            with patch(
                "historical_replay.collect_strategy_signals",
                return_value={decision_date.normalize(): [signal]},
            ):
                rebuilt = run_historical_replay(
                    config,
                    db_path=database,
                    universe_file=universe_file,
                    resume=True,
                    history_loader=history_loader,
                    benchmark_loader=benchmark_loader,
                )
            self.assertFalse(rebuilt.get("already_completed", False))
            conn = sqlite3.connect(database)
            try:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM historical_replay_events").fetchone()[0],
                    1,
                )
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
