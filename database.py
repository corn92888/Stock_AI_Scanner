import datetime
import os
import sqlite3
import subprocess
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path("data/stock_scanner.db")
STRATEGY_VERSION = "strict_v1"
LEGACY_CANDIDATE_EXECUTION_VERSION = "next_day_open_defense_close_t3_v1"
CANDIDATE_EXECUTION_VERSION = "mode_aligned_after_costs_t3_v2"
PAPER_POLICY_VERSION = "risk_budget_portfolio_v2"
HISTORICAL_REPLAY_VERSION = "point_in_time_eod_replay_v2"
HISTORICAL_REPLAY_EXECUTION_VERSION = "next_open_after_costs_t3_v1"
HISTORICAL_ATTRIBUTION_VERSION = "replay_attribution_v1"


def get_taipei_now():
    tz = datetime.timezone(datetime.timedelta(hours=8), name="Asia/Taipei")
    return datetime.datetime.now(tz)


def get_git_commit():
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except Exception:
        return ""


@contextmanager
def get_connection(db_path=DB_PATH):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
    finally:
        conn.close()


def init_db(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scan_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            mode TEXT NOT NULL,
            source TEXT NOT NULL,
            strategy_version TEXT NOT NULL,
            git_commit TEXT,
            report_path TEXT,
            notes TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            trade_date TEXT NOT NULL,
            mode TEXT NOT NULL,
            strategy TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            industry TEXT,
            signal_price REAL,
            stop_loss REAL,
            pct_change REAL,
            volume_lots INTEGER,
            rsi REAL,
            condition_text TEXT,
            rank_order INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES scan_runs(id),
            UNIQUE (run_id, strategy, code)
        )
        """
    )
    _migrate_signal_uniqueness(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS backtest_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL,
            entry_date TEXT,
            entry_price REAL,
            entry_method TEXT,
            price_basis TEXT,
            exit_1d_price REAL,
            exit_3d_price REAL,
            exit_5d_price REAL,
            exit_10d_price REAL,
            exit_20d_price REAL,
            return_1d REAL,
            return_3d REAL,
            return_5d REAL,
            return_10d REAL,
            return_20d REAL,
            max_return_20d REAL,
            max_drawdown_20d REAL,
            max_return_3d REAL,
            max_drawdown_3d REAL,
            net_return_1d REAL,
            net_return_3d REAL,
            net_return_5d REAL,
            net_return_10d REAL,
            net_return_20d REAL,
            benchmark_code TEXT,
            benchmark_entry_price REAL,
            benchmark_exit_1d_price REAL,
            benchmark_exit_3d_price REAL,
            benchmark_exit_5d_price REAL,
            benchmark_exit_10d_price REAL,
            benchmark_exit_20d_price REAL,
            benchmark_return_1d REAL,
            benchmark_return_3d REAL,
            benchmark_return_5d REAL,
            benchmark_return_10d REAL,
            benchmark_return_20d REAL,
            excess_return_1d REAL,
            excess_return_3d REAL,
            excess_return_5d REAL,
            excess_return_10d REAL,
            excess_return_20d REAL,
            stop_loss_hit INTEGER,
            stop_loss_date TEXT,
            success_t3 INTEGER,
            matured_horizon INTEGER NOT NULL DEFAULT 0,
            outcome_status TEXT NOT NULL DEFAULT 'pending',
            price_data_end TEXT,
            costs_bps REAL,
            config_json TEXT,
            tested_at TEXT NOT NULL,
            updated_at TEXT,
            FOREIGN KEY (signal_id) REFERENCES stock_signals(id),
            UNIQUE (signal_id)
        )
        """
    )
    _migrate_backtest_results(conn)
    _create_quant_tables(conn)
    _create_research_tables(conn)
    _create_historical_replay_tables(conn)
    _create_global_market_tables(conn)
    _migrate_quant_tables(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_trade_date ON stock_signals(trade_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_strategy ON stock_signals(strategy)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_backtest_status ON backtest_results(outcome_status)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_candidate_events_day "
        "ON candidate_events(policy_version, run_id, is_selected)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_candidate_events_code "
        "ON candidate_events(code, policy_version, tradable)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_predictions_run_rank ON predictions(run_id, rank_order)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_outcomes_status ON prediction_outcomes(outcome_status)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_candidate_outcomes_status "
        "ON candidate_outcomes(execution_version, matured_horizon, outcome_status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_candidate_scenarios_status "
        "ON candidate_execution_scenarios(scenario_version, matured_horizon, updated_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_experiment_evaluations_time "
        "ON experiment_evaluations(experiment_id, evaluated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_fundamentals_code_known "
        "ON fundamental_observations(code, known_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_model_validation_version_date "
        "ON model_validation_predictions(model_version, trade_date, fold_index)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_historical_replay_events_day "
        "ON historical_replay_events(replay_run_id, trade_date, is_selected)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_historical_replay_outcomes_status "
        "ON historical_replay_outcomes(execution_version, matured_horizon, outcome_status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_historical_replay_checkpoints_status "
        "ON historical_replay_checkpoints(replay_run_id, status, partition_start)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_historical_replay_attribution_dimension "
        "ON historical_replay_attributions(replay_run_id, attribution_version, "
        "dimension, selection_scope, sort_order)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_historical_replay_summary_run "
        "ON historical_replay_summaries(replay_run_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_research_health_time "
        "ON research_health_snapshots(checked_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_market_observations_key_time "
        "ON market_observations(instrument_key, snapshot_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_market_regime_time "
        "ON market_regime_snapshots(snapshot_at DESC)"
    )
    conn.commit()


def _ensure_columns(conn, table_name, columns):
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})")}
    for column_name, definition in columns.items():
        if column_name not in existing:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def _migrate_backtest_results(conn):
    _ensure_columns(
        conn,
        "backtest_results",
        {
            "entry_method": "TEXT",
            "price_basis": "TEXT",
            "max_return_3d": "REAL",
            "max_drawdown_3d": "REAL",
            "net_return_1d": "REAL",
            "net_return_3d": "REAL",
            "net_return_5d": "REAL",
            "net_return_10d": "REAL",
            "net_return_20d": "REAL",
            "benchmark_code": "TEXT",
            "benchmark_entry_price": "REAL",
            "benchmark_exit_1d_price": "REAL",
            "benchmark_exit_3d_price": "REAL",
            "benchmark_exit_5d_price": "REAL",
            "benchmark_exit_10d_price": "REAL",
            "benchmark_exit_20d_price": "REAL",
            "benchmark_return_1d": "REAL",
            "benchmark_return_3d": "REAL",
            "benchmark_return_5d": "REAL",
            "benchmark_return_10d": "REAL",
            "benchmark_return_20d": "REAL",
            "excess_return_1d": "REAL",
            "excess_return_3d": "REAL",
            "excess_return_5d": "REAL",
            "excess_return_10d": "REAL",
            "excess_return_20d": "REAL",
            "success_t3": "INTEGER",
            "matured_horizon": "INTEGER NOT NULL DEFAULT 0",
            "outcome_status": "TEXT NOT NULL DEFAULT 'pending'",
            "price_data_end": "TEXT",
            "costs_bps": "REAL",
            "config_json": "TEXT",
            "updated_at": "TEXT",
        },
    )


def _create_quant_tables(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS candidate_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            signal_id INTEGER,
            code TEXT NOT NULL,
            name TEXT,
            as_of TEXT NOT NULL,
            industry TEXT,
            strategies_json TEXT NOT NULL,
            strategy_count INTEGER NOT NULL DEFAULT 0,
            raw_rank INTEGER,
            score REAL,
            signal_price REAL,
            pct_change REAL,
            turnover_billion REAL,
            volume_ratio_5 REAL,
            intraday_position REAL,
            observation_price REAL,
            chase_limit REAL,
            stop_distance_pct REAL,
            tradable INTEGER NOT NULL DEFAULT 0,
            block_reasons_json TEXT NOT NULL,
            risk_flags_json TEXT NOT NULL,
            is_first_eligible_event INTEGER NOT NULL DEFAULT 0,
            is_selected INTEGER NOT NULL DEFAULT 0,
            selection_rank INTEGER,
            selection_status TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            policy_config_json TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES scan_runs(id),
            FOREIGN KEY (signal_id) REFERENCES stock_signals(id),
            UNIQUE (run_id, code, policy_version)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS feature_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            signal_id INTEGER,
            code TEXT NOT NULL,
            as_of TEXT NOT NULL,
            decision_at TEXT,
            known_at TEXT,
            point_in_time_valid INTEGER NOT NULL DEFAULT 0,
            feature_version TEXT NOT NULL,
            candidate_score REAL,
            strategy_count INTEGER,
            strategy_trend INTEGER,
            strategy_reversal INTEGER,
            strategy_wave INTEGER,
            tradable INTEGER,
            is_first_eligible_event INTEGER,
            stop_distance_pct REAL,
            price REAL,
            pct_change REAL,
            turnover_billion REAL,
            volume_ratio_5 REAL,
            volume_ratio_20 REAL,
            intraday_position REAL,
            rsi REAL,
            industry_up_ratio REAL,
            industry_avg_return REAL,
            industry_heat REAL,
            market_up_ratio REAL,
            market_avg_return REAL,
            market_median_return REAL,
            pe REAL,
            pb REAL,
            revenue_yoy REAL,
            revenue_mom REAL,
            eps_ttm REAL,
            news_score REAL,
            catalyst_score REAL,
            risk_score REAL,
            feature_lineage_json TEXT NOT NULL DEFAULT '{}',
            quality_flags_json TEXT NOT NULL DEFAULT '[]',
            features_json TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES scan_runs(id),
            FOREIGN KEY (signal_id) REFERENCES stock_signals(id),
            UNIQUE (run_id, code, feature_version)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fundamental_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            period_end TEXT,
            published_at TEXT,
            known_at TEXT NOT NULL,
            source_name TEXT NOT NULL,
            pe REAL,
            pb REAL,
            revenue_yoy REAL,
            revenue_mom REAL,
            eps_ttm REAL,
            raw_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE (code, source_name, period_end, published_at)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS candidate_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id INTEGER NOT NULL,
            execution_version TEXT NOT NULL,
            entry_status TEXT NOT NULL DEFAULT 'filled',
            skip_reason TEXT,
            entry_at TEXT,
            entry_price REAL,
            entry_adjustment_factor REAL,
            entry_method TEXT NOT NULL,
            exit_at TEXT,
            exit_price REAL,
            exit_reason TEXT,
            fixed_net_return_1d REAL,
            fixed_net_return_3d REAL,
            fixed_net_return_5d REAL,
            net_return_3d REAL,
            benchmark_code TEXT,
            benchmark_entry_price REAL,
            benchmark_return_3d REAL,
            excess_return_3d REAL,
            max_return_3d REAL,
            max_drawdown_3d REAL,
            defense_triggered INTEGER NOT NULL DEFAULT 0,
            success_t3 INTEGER,
            matured_horizon INTEGER NOT NULL DEFAULT 0,
            outcome_status TEXT NOT NULL DEFAULT 'pending',
            price_data_end TEXT,
            costs_bps REAL,
            config_json TEXT,
            evaluated_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (candidate_id) REFERENCES candidate_events(id),
            UNIQUE (candidate_id, execution_version)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS candidate_execution_scenarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id INTEGER NOT NULL,
            scenario_version TEXT NOT NULL,
            entry_method TEXT NOT NULL,
            entry_status TEXT NOT NULL,
            skip_reason TEXT,
            entry_at TEXT,
            entry_price REAL,
            benchmark_entry_price REAL,
            matured_horizon INTEGER NOT NULL DEFAULT 0,
            outcome_status TEXT NOT NULL DEFAULT 'pending',
            costs_bps REAL,
            labels_json TEXT NOT NULL DEFAULT '{}',
            price_data_end TEXT,
            evaluated_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (candidate_id) REFERENCES candidate_events(id),
            UNIQUE (candidate_id, scenario_version, entry_method)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_key TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            strategy_kind TEXT NOT NULL,
            evidence_mode TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            execution_version TEXT NOT NULL,
            starting_cash REAL NOT NULL,
            cash REAL NOT NULL,
            equity REAL NOT NULL,
            total_return_pct REAL NOT NULL DEFAULT 0,
            max_drawdown_pct REAL NOT NULL DEFAULT 0,
            closed_trades INTEGER NOT NULL DEFAULT 0,
            winning_trades INTEGER NOT NULL DEFAULT 0,
            open_positions INTEGER NOT NULL DEFAULT 0,
            pending_orders INTEGER NOT NULL DEFAULT 0,
            skipped_orders INTEGER NOT NULL DEFAULT 0,
            first_signal_at TEXT,
            last_equity_at TEXT,
            status TEXT NOT NULL DEFAULT 'shadow',
            config_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            source_type TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            candidate_id INTEGER,
            prediction_id INTEGER,
            signal_date TEXT NOT NULL,
            signal_at TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            industry TEXT,
            rank_order INTEGER,
            model_version TEXT,
            entry_at TEXT,
            entry_price REAL,
            entry_fee REAL,
            quantity INTEGER,
            invested_amount REAL,
            chase_limit REAL,
            stop_price REAL,
            exit_at TEXT,
            exit_price REAL,
            exit_cost REAL,
            exit_proceeds REAL,
            exit_reason TEXT,
            net_return_pct REAL,
            realized_pnl REAL,
            mark_at TEXT,
            mark_price REAL,
            market_value REAL,
            unrealized_pnl REAL,
            max_return_pct REAL,
            max_drawdown_pct REAL,
            status TEXT NOT NULL,
            skip_reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (account_id) REFERENCES paper_accounts(id),
            FOREIGN KEY (candidate_id) REFERENCES candidate_events(id),
            FOREIGN KEY (prediction_id) REFERENCES predictions(id),
            UNIQUE (account_id, source_type, source_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_equity_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            as_of TEXT NOT NULL,
            cash REAL NOT NULL,
            market_value REAL NOT NULL,
            equity REAL NOT NULL,
            total_return_pct REAL NOT NULL,
            peak_equity REAL NOT NULL,
            drawdown_pct REAL NOT NULL,
            open_positions INTEGER NOT NULL,
            closed_trades INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (account_id) REFERENCES paper_accounts(id),
            UNIQUE (account_id, as_of)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS news_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            code TEXT,
            title TEXT NOT NULL,
            source_name TEXT,
            url TEXT NOT NULL,
            published_at TEXT,
            known_at TEXT NOT NULL,
            evidence_type TEXT,
            sentiment TEXT,
            confidence REAL,
            content_hash TEXT,
            extracted_json TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES scan_runs(id),
            UNIQUE (run_id, code, url, published_at)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS model_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_name TEXT NOT NULL,
            version TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'candidate',
            feature_version TEXT NOT NULL,
            training_start TEXT,
            training_end TEXT,
            config_json TEXT,
            metrics_json TEXT,
            artifact_path TEXT,
            created_at TEXT NOT NULL,
            promoted_at TEXT,
            UNIQUE (model_name, version)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS model_validation_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_version TEXT NOT NULL,
            feature_id INTEGER NOT NULL,
            trade_date TEXT NOT NULL,
            code TEXT NOT NULL,
            fold_index INTEGER NOT NULL,
            trained_through TEXT NOT NULL,
            probability_t3 REAL NOT NULL,
            expected_excess_return_3d REAL NOT NULL,
            expected_max_drawdown_3d REAL NOT NULL,
            final_score REAL NOT NULL,
            is_selected INTEGER NOT NULL DEFAULT 0,
            actual_success_t3 INTEGER NOT NULL,
            actual_net_return_3d REAL NOT NULL,
            actual_excess_return_3d REAL NOT NULL,
            actual_max_drawdown_3d REAL NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (feature_id) REFERENCES feature_snapshots(id),
            UNIQUE (model_version, feature_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS model_challenger_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_version TEXT NOT NULL UNIQUE,
            evaluated_at TEXT NOT NULL,
            status TEXT NOT NULL,
            oof_trade_dates INTEGER NOT NULL DEFAULT 0,
            oof_candidates INTEGER NOT NULL DEFAULT 0,
            challenger_trades INTEGER NOT NULL DEFAULT 0,
            champion_trades INTEGER NOT NULL DEFAULT 0,
            challenger_mean_net_return REAL,
            challenger_mean_excess_return REAL,
            champion_mean_net_return REAL,
            champion_mean_excess_return REAL,
            net_return_lift REAL,
            excess_return_lift REAL,
            challenger_max_drawdown REAL,
            profitable_fold_rate REAL,
            qualified INTEGER NOT NULL DEFAULT 0,
            rejection_reasons_json TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            signal_id INTEGER,
            code TEXT NOT NULL,
            predicted_at TEXT NOT NULL,
            model_version TEXT NOT NULL,
            is_prospective INTEGER NOT NULL DEFAULT 1,
            rank_order INTEGER,
            is_selected INTEGER NOT NULL DEFAULT 0,
            final_score REAL,
            probability_t3 REAL,
            expected_excess_return_3d REAL,
            expected_max_drawdown_3d REAL,
            action TEXT,
            entry_low REAL,
            entry_high REAL,
            chase_limit REAL,
            stop_price REAL,
            target_low REAL,
            target_high REAL,
            rationale_json TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES scan_runs(id),
            FOREIGN KEY (signal_id) REFERENCES stock_signals(id),
            UNIQUE (run_id, code, model_version)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS prediction_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prediction_id INTEGER NOT NULL,
            entry_at TEXT,
            entry_price REAL,
            entry_method TEXT,
            net_return_1d REAL,
            net_return_3d REAL,
            net_return_5d REAL,
            benchmark_return_3d REAL,
            excess_return_3d REAL,
            max_return_3d REAL,
            max_drawdown_3d REAL,
            target_hit_at TEXT,
            stop_hit_at TEXT,
            first_barrier TEXT,
            success_t3 INTEGER,
            matured_horizon INTEGER NOT NULL DEFAULT 0,
            outcome_status TEXT NOT NULL DEFAULT 'pending',
            evaluated_at TEXT,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (prediction_id) REFERENCES predictions(id),
            UNIQUE (prediction_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS backtest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            config_json TEXT NOT NULL,
            git_commit TEXT,
            signals_requested INTEGER NOT NULL DEFAULT 0,
            completed_count INTEGER NOT NULL DEFAULT 0,
            partial_count INTEGER NOT NULL DEFAULT 0,
            skipped_count INTEGER NOT NULL DEFAULT 0,
            error_text TEXT
        )
        """
    )


def _create_research_tables(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS research_experiments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            experiment_key TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            hypothesis TEXT NOT NULL,
            strategy_family TEXT NOT NULL,
            execution_version TEXT NOT NULL,
            objective TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'candidate',
            config_json TEXT NOT NULL,
            git_commit TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS experiment_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            experiment_id INTEGER NOT NULL,
            evaluation_version TEXT NOT NULL,
            evaluated_at TEXT NOT NULL,
            sample_start TEXT,
            sample_end TEXT,
            trade_dates INTEGER NOT NULL DEFAULT 0,
            trades INTEGER NOT NULL DEFAULT 0,
            folds INTEGER NOT NULL DEFAULT 0,
            mean_net_return REAL,
            mean_excess_return REAL,
            positive_rate REAL,
            annualized_sharpe REAL,
            probabilistic_sharpe REAL,
            max_drawdown REAL,
            profitable_fold_rate REAL,
            qualified INTEGER NOT NULL DEFAULT 0,
            rejection_reasons_json TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            FOREIGN KEY (experiment_id) REFERENCES research_experiments(id),
            UNIQUE (experiment_id, evaluation_version)
        )
        """
    )


def _create_historical_replay_tables(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS historical_replay_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            replay_key TEXT NOT NULL UNIQUE,
            replay_version TEXT NOT NULL,
            strategy_version TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            execution_version TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            universe_source TEXT NOT NULL,
            universe_size INTEGER NOT NULL DEFAULT 0,
            universe_quality_status TEXT NOT NULL DEFAULT 'unverified',
            universe_partial_memberships INTEGER NOT NULL DEFAULT 0,
            universe_membership_intervals INTEGER NOT NULL DEFAULT 0,
            available_symbols INTEGER NOT NULL DEFAULT 0,
            trading_days INTEGER NOT NULL DEFAULT 0,
            signal_events INTEGER NOT NULL DEFAULT 0,
            candidate_events INTEGER NOT NULL DEFAULT 0,
            selected_events INTEGER NOT NULL DEFAULT 0,
            matured_t3 INTEGER NOT NULL DEFAULT 0,
            config_json TEXT NOT NULL,
            data_warnings_json TEXT NOT NULL DEFAULT '[]',
            git_commit TEXT,
            error_text TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS historical_replay_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            replay_run_id INTEGER NOT NULL,
            trade_date TEXT NOT NULL,
            decision_at TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            industry TEXT,
            strategies_json TEXT NOT NULL,
            strategy_count INTEGER NOT NULL DEFAULT 0,
            raw_rank INTEGER,
            score REAL,
            signal_price REAL,
            pct_change REAL,
            turnover_billion REAL,
            volume_ratio_5 REAL,
            volume_ratio_20 REAL,
            intraday_position REAL,
            observation_price REAL,
            chase_limit REAL,
            stop_distance_pct REAL,
            tradable INTEGER NOT NULL DEFAULT 0,
            block_reasons_json TEXT NOT NULL DEFAULT '[]',
            risk_flags_json TEXT NOT NULL DEFAULT '[]',
            is_selected INTEGER NOT NULL DEFAULT 0,
            selection_rank INTEGER,
            selection_status TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (replay_run_id) REFERENCES historical_replay_runs(id),
            UNIQUE (replay_run_id, trade_date, code)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS historical_replay_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            replay_event_id INTEGER NOT NULL,
            execution_version TEXT NOT NULL,
            entry_status TEXT NOT NULL,
            skip_reason TEXT,
            entry_at TEXT,
            entry_price REAL,
            entry_method TEXT,
            exit_at TEXT,
            exit_price REAL,
            exit_reason TEXT,
            fixed_net_return_1d REAL,
            fixed_net_return_3d REAL,
            fixed_net_return_5d REAL,
            net_return_3d REAL,
            benchmark_code TEXT,
            benchmark_entry_price REAL,
            benchmark_return_3d REAL,
            excess_return_3d REAL,
            max_return_3d REAL,
            max_drawdown_3d REAL,
            defense_triggered INTEGER NOT NULL DEFAULT 0,
            success_t3 INTEGER,
            matured_horizon INTEGER NOT NULL DEFAULT 0,
            outcome_status TEXT NOT NULL DEFAULT 'pending',
            price_data_end TEXT,
            costs_bps REAL,
            config_json TEXT NOT NULL,
            evaluated_at TEXT NOT NULL,
            FOREIGN KEY (replay_event_id) REFERENCES historical_replay_events(id),
            UNIQUE (replay_event_id, execution_version)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS historical_replay_checkpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            replay_run_id INTEGER NOT NULL,
            partition_key TEXT NOT NULL,
            partition_start TEXT NOT NULL,
            partition_end TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            candidate_events INTEGER NOT NULL DEFAULT 0,
            selected_events INTEGER NOT NULL DEFAULT 0,
            matured_t3 INTEGER NOT NULL DEFAULT 0,
            error_text TEXT,
            metrics_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (replay_run_id) REFERENCES historical_replay_runs(id),
            UNIQUE (replay_run_id, partition_key)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS historical_replay_attributions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            replay_run_id INTEGER NOT NULL,
            attribution_version TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            dimension TEXT NOT NULL,
            bucket_key TEXT NOT NULL,
            bucket_label TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            selection_scope TEXT NOT NULL,
            sample_count INTEGER NOT NULL DEFAULT 0,
            selected_count INTEGER NOT NULL DEFAULT 0,
            mean_net_return_1d REAL,
            mean_net_return_3d REAL,
            mean_net_return_5d REAL,
            mean_excess_return_3d REAL,
            positive_rate_3d REAL,
            success_rate_t3 REAL,
            mean_max_drawdown_3d REAL,
            standard_error_3d REAL,
            ci95_low_3d REAL,
            ci95_high_3d REAL,
            metrics_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (replay_run_id) REFERENCES historical_replay_runs(id),
            UNIQUE (
                replay_run_id, attribution_version, dimension,
                bucket_key, selection_scope
            )
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS historical_replay_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            replay_run_id INTEGER NOT NULL UNIQUE,
            generated_at TEXT NOT NULL,
            filled_events INTEGER NOT NULL DEFAULT 0,
            selected_filled INTEGER NOT NULL DEFAULT 0,
            rejected_filled INTEGER NOT NULL DEFAULT 0,
            selected_mean_net_return_3d REAL,
            selected_mean_excess_return_3d REAL,
            selected_success_rate_t3 REAL,
            rejected_mean_net_return_3d REAL,
            rejected_mean_excess_return_3d REAL,
            rejected_success_rate_t3 REAL,
            selection_net_lift_3d REAL,
            selection_excess_lift_3d REAL,
            metrics_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (replay_run_id) REFERENCES historical_replay_runs(id)
        )
        """
    )
    _ensure_columns(
        conn,
        "historical_replay_runs",
        {
            "universe_snapshots": "INTEGER NOT NULL DEFAULT 1",
            "universe_quality_status": "TEXT NOT NULL DEFAULT 'unverified'",
            "universe_partial_memberships": "INTEGER NOT NULL DEFAULT 0",
            "universe_membership_intervals": "INTEGER NOT NULL DEFAULT 0",
            "checkpoint_total": "INTEGER NOT NULL DEFAULT 0",
            "checkpoint_completed": "INTEGER NOT NULL DEFAULT 0",
            "last_checkpoint": "TEXT",
        },
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS research_health_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            checked_at TEXT NOT NULL,
            latest_trade_date TEXT,
            status TEXT NOT NULL,
            prospective_cohorts INTEGER NOT NULL DEFAULT 0,
            pending_cohorts INTEGER NOT NULL DEFAULT 0,
            mature_t3_cohorts INTEGER NOT NULL DEFAULT 0,
            expected_mature_t3 INTEGER NOT NULL DEFAULT 0,
            stale_outcomes INTEGER NOT NULL DEFAULT 0,
            oldest_pending_sessions INTEGER NOT NULL DEFAULT 0,
            replay_runs INTEGER NOT NULL DEFAULT 0,
            completed_replay_runs INTEGER NOT NULL DEFAULT 0,
            latest_replay_at TEXT,
            replay_events INTEGER NOT NULL DEFAULT 0,
            replay_selected INTEGER NOT NULL DEFAULT 0,
            replay_mature_t3 INTEGER NOT NULL DEFAULT 0,
            warnings_json TEXT NOT NULL,
            metrics_json TEXT NOT NULL
        )
        """
    )


def _create_global_market_tables(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_instruments (
            instrument_key TEXT PRIMARY KEY,
            symbol TEXT,
            display_name TEXT NOT NULL,
            group_name TEXT NOT NULL,
            region TEXT NOT NULL,
            asset_class TEXT NOT NULL,
            currency TEXT,
            source_name TEXT NOT NULL,
            source_tier TEXT NOT NULL,
            impact_direction REAL NOT NULL DEFAULT 0,
            model_weight REAL NOT NULL DEFAULT 0,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_at TEXT NOT NULL,
            instrument_key TEXT NOT NULL,
            market_at TEXT,
            price REAL,
            previous_close REAL,
            pct_change REAL,
            return_5d REAL,
            shock_z REAL,
            volume REAL,
            source_name TEXT NOT NULL,
            source_tier TEXT NOT NULL,
            data_status TEXT NOT NULL,
            session_status TEXT NOT NULL,
            latency_minutes REAL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (instrument_key) REFERENCES market_instruments(instrument_key),
            UNIQUE (snapshot_at, instrument_key)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_regime_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_at TEXT NOT NULL UNIQUE,
            score REAL NOT NULL,
            regime_label TEXT NOT NULL,
            taiwan_bias_score REAL NOT NULL,
            taiwan_bias_label TEXT NOT NULL,
            coverage_pct REAL NOT NULL,
            active_fresh_pct REAL NOT NULL,
            components_json TEXT NOT NULL,
            drivers_json TEXT NOT NULL,
            quality_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )


def _migrate_quant_tables(conn):
    _ensure_columns(
        conn,
        "feature_snapshots",
        {
            "candidate_score": "REAL",
            "strategy_count": "INTEGER",
            "strategy_trend": "INTEGER",
            "strategy_reversal": "INTEGER",
            "strategy_wave": "INTEGER",
            "tradable": "INTEGER",
            "is_first_eligible_event": "INTEGER",
            "stop_distance_pct": "REAL",
            "decision_at": "TEXT",
            "known_at": "TEXT",
            "point_in_time_valid": "INTEGER NOT NULL DEFAULT 0",
            "feature_lineage_json": "TEXT NOT NULL DEFAULT '{}'",
            "quality_flags_json": "TEXT NOT NULL DEFAULT '[]'",
        },
    )
    _ensure_columns(
        conn,
        "candidate_outcomes",
        {
            "entry_adjustment_factor": "REAL",
            "entry_status": "TEXT NOT NULL DEFAULT 'filled'",
            "skip_reason": "TEXT",
        },
    )
    _ensure_columns(
        conn,
        "candidate_execution_scenarios",
        {"outcome_status": "TEXT NOT NULL DEFAULT 'pending'"},
    )
    _ensure_columns(
        conn,
        "predictions",
        {"is_prospective": "INTEGER NOT NULL DEFAULT 1"},
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_paper_trades_account_status "
        "ON paper_trades(account_id, status, signal_date)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_paper_equity_account_date "
        "ON paper_equity_snapshots(account_id, as_of)"
    )


def _migrate_signal_uniqueness(conn):
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'stock_signals'"
    ).fetchone()
    if not row or not row[0]:
        return

    current_schema = row[0]
    if "UNIQUE (trade_date, mode, strategy, code)" not in current_schema:
        return

    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        """
        CREATE TABLE stock_signals_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            trade_date TEXT NOT NULL,
            mode TEXT NOT NULL,
            strategy TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            industry TEXT,
            signal_price REAL,
            stop_loss REAL,
            pct_change REAL,
            volume_lots INTEGER,
            rsi REAL,
            condition_text TEXT,
            rank_order INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES scan_runs(id),
            UNIQUE (run_id, strategy, code)
        )
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO stock_signals_new (
            id, run_id, trade_date, mode, strategy, code, name, industry,
            signal_price, stop_loss, pct_change, volume_lots, rsi,
            condition_text, rank_order, created_at
        )
        SELECT
            id, run_id, trade_date, mode, strategy, code, name, industry,
            signal_price, stop_loss, pct_change, volume_lots, rsi,
            condition_text, rank_order, created_at
        FROM stock_signals
        """
    )
    conn.execute("DROP TABLE stock_signals")
    conn.execute("ALTER TABLE stock_signals_new RENAME TO stock_signals")
    conn.execute("PRAGMA foreign_keys=ON")


def detect_source():
    if os.getenv("GITHUB_ACTIONS") == "true":
        return "github_action"
    return "local"


def _first_present(row, names, default=None):
    for name in names:
        if name in row and row[name] == row[name]:
            return row[name]
    return default


def _safe_float(value):
    if value is None or value != value:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _safe_int(value):
    if value is None or value != value:
        return None
    try:
        return int(value)
    except Exception:
        return None


def record_scan_results(mode, trade_date, strategy_frames, report_path=None, notes=None, db_path=DB_PATH):
    now = get_taipei_now()
    run_at = now.isoformat(timespec="seconds")
    trade_date = str(trade_date)

    with get_connection(db_path) as conn:
        init_db(conn)
        cursor = conn.execute(
            """
            INSERT INTO scan_runs (
                run_at, trade_date, mode, source, strategy_version, git_commit, report_path, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_at,
                trade_date,
                mode,
                detect_source(),
                STRATEGY_VERSION,
                get_git_commit(),
                report_path,
                notes,
            ),
        )
        run_id = cursor.lastrowid

        inserted = 0
        for strategy, df in strategy_frames.items():
            if df is None or df.empty:
                continue

            for rank_order, (_, row) in enumerate(df.iterrows(), start=1):
                code = str(_first_present(row, ["代號"], "")).strip()
                if not code:
                    continue

                conn.execute(
                    """
                    INSERT INTO stock_signals (
                        run_id, trade_date, mode, strategy, code, name, industry,
                        signal_price, stop_loss, pct_change, volume_lots, rsi,
                        condition_text, rank_order, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id, strategy, code) DO UPDATE SET
                        name = excluded.name,
                        industry = excluded.industry,
                        signal_price = excluded.signal_price,
                        stop_loss = excluded.stop_loss,
                        pct_change = excluded.pct_change,
                        volume_lots = excluded.volume_lots,
                        rsi = excluded.rsi,
                        condition_text = excluded.condition_text,
                        rank_order = excluded.rank_order,
                        created_at = excluded.created_at
                    """,
                    (
                        run_id,
                        trade_date,
                        mode,
                        strategy,
                        code,
                        _first_present(row, ["名稱"], ""),
                        _first_present(row, ["產業族群"], ""),
                        _safe_float(_first_present(row, ["現價"])),
                        _safe_float(_first_present(row, ["防守價"])),
                        _safe_float(_first_present(row, ["漲跌幅"])),
                        _safe_int(_first_present(row, ["成交量(張)", "成交(張)(含預估)"])),
                        _safe_float(_first_present(row, ["RSI"])),
                        _first_present(row, ["條件"], ""),
                        rank_order,
                        run_at,
                    ),
                )
                inserted += 1

        conn.commit()

    return {"run_id": run_id, "signals": inserted, "db_path": str(Path(db_path))}


def save_backtest_result(signal_id, result, db_path=DB_PATH):
    tested_at = get_taipei_now().isoformat(timespec="seconds")
    columns = [
        "signal_id",
        "entry_date",
        "entry_price",
        "entry_method",
        "price_basis",
        "exit_1d_price",
        "exit_3d_price",
        "exit_5d_price",
        "exit_10d_price",
        "exit_20d_price",
        "return_1d",
        "return_3d",
        "return_5d",
        "return_10d",
        "return_20d",
        "max_return_3d",
        "max_drawdown_3d",
        "max_return_20d",
        "max_drawdown_20d",
        "net_return_1d",
        "net_return_3d",
        "net_return_5d",
        "net_return_10d",
        "net_return_20d",
        "benchmark_code",
        "benchmark_entry_price",
        "benchmark_exit_1d_price",
        "benchmark_exit_3d_price",
        "benchmark_exit_5d_price",
        "benchmark_exit_10d_price",
        "benchmark_exit_20d_price",
        "benchmark_return_1d",
        "benchmark_return_3d",
        "benchmark_return_5d",
        "benchmark_return_10d",
        "benchmark_return_20d",
        "excess_return_1d",
        "excess_return_3d",
        "excess_return_5d",
        "excess_return_10d",
        "excess_return_20d",
        "stop_loss_hit",
        "stop_loss_date",
        "success_t3",
        "matured_horizon",
        "outcome_status",
        "price_data_end",
        "costs_bps",
        "config_json",
        "tested_at",
        "updated_at",
    ]
    values = []
    for column in columns:
        if column == "signal_id":
            value = signal_id
        elif column in {"tested_at", "updated_at"}:
            value = tested_at
        elif column in {"stop_loss_hit", "success_t3"}:
            raw = result.get(column)
            value = None if raw is None else int(bool(raw))
        else:
            value = result.get(column)
        values.append(value)

    placeholders = ", ".join("?" for _ in columns)
    updates = ",\n                ".join(
        f"{column} = excluded.{column}" for column in columns if column != "signal_id"
    )
    with get_connection(db_path) as conn:
        init_db(conn)
        conn.execute(
            f"""
            INSERT INTO backtest_results ({", ".join(columns)})
            VALUES ({placeholders})
            ON CONFLICT(signal_id) DO UPDATE SET
                {updates}
            """,
            values,
        )
        conn.commit()


def save_candidate_outcome(candidate_id, execution_version, result, db_path=DB_PATH):
    evaluated_at = get_taipei_now().isoformat(timespec="seconds")
    columns = [
        "candidate_id",
        "execution_version",
        "entry_status",
        "skip_reason",
        "entry_at",
        "entry_price",
        "entry_adjustment_factor",
        "entry_method",
        "exit_at",
        "exit_price",
        "exit_reason",
        "fixed_net_return_1d",
        "fixed_net_return_3d",
        "fixed_net_return_5d",
        "net_return_3d",
        "benchmark_code",
        "benchmark_entry_price",
        "benchmark_return_3d",
        "excess_return_3d",
        "max_return_3d",
        "max_drawdown_3d",
        "defense_triggered",
        "success_t3",
        "matured_horizon",
        "outcome_status",
        "price_data_end",
        "costs_bps",
        "config_json",
        "evaluated_at",
        "updated_at",
    ]
    values = []
    for column in columns:
        if column == "candidate_id":
            value = int(candidate_id)
        elif column == "execution_version":
            value = execution_version
        elif column == "entry_status":
            value = result.get(column) or "filled"
        elif column in {"evaluated_at", "updated_at"}:
            value = evaluated_at
        elif column in {"defense_triggered", "success_t3"}:
            raw = result.get(column)
            value = None if raw is None else int(bool(raw))
        else:
            value = result.get(column)
        values.append(value)

    placeholders = ", ".join("?" for _ in columns)
    updates = ",\n                ".join(
        f"{column} = excluded.{column}"
        for column in columns
        if column not in {"candidate_id", "execution_version"}
    )
    with get_connection(db_path) as conn:
        init_db(conn)
        conn.execute(
            f"""
            INSERT INTO candidate_outcomes ({", ".join(columns)})
            VALUES ({placeholders})
            ON CONFLICT(candidate_id, execution_version) DO UPDATE SET
                {updates}
            """,
            values,
        )
        conn.commit()


def start_backtest_run(config_json, signals_requested, db_path=DB_PATH):
    started_at = get_taipei_now().isoformat(timespec="seconds")
    with get_connection(db_path) as conn:
        init_db(conn)
        cursor = conn.execute(
            """
            INSERT INTO backtest_runs (
                started_at, status, config_json, git_commit, signals_requested
            ) VALUES (?, 'running', ?, ?, ?)
            """,
            (started_at, config_json, get_git_commit(), int(signals_requested)),
        )
        conn.commit()
        return cursor.lastrowid


def finish_backtest_run(
    run_id,
    status,
    completed_count=0,
    partial_count=0,
    skipped_count=0,
    error_text=None,
    db_path=DB_PATH,
):
    finished_at = get_taipei_now().isoformat(timespec="seconds")
    with get_connection(db_path) as conn:
        init_db(conn)
        conn.execute(
            """
            UPDATE backtest_runs
            SET finished_at = ?, status = ?, completed_count = ?, partial_count = ?,
                skipped_count = ?, error_text = ?
            WHERE id = ?
            """,
            (
                finished_at,
                status,
                int(completed_count),
                int(partial_count),
                int(skipped_count),
                error_text,
                int(run_id),
            ),
        )
        conn.commit()


def find_scan_run(report_path, db_path=DB_PATH):
    if not report_path:
        return None

    path = Path(report_path)
    candidates = {str(path), path.as_posix()}
    try:
        candidates.add(str(path.resolve().relative_to(Path.cwd().resolve())))
    except ValueError:
        pass

    with get_connection(db_path) as conn:
        init_db(conn)
        placeholders = ", ".join("?" for _ in candidates)
        row = conn.execute(
            f"""
            SELECT * FROM scan_runs
            WHERE report_path IN ({placeholders})
            ORDER BY id DESC
            LIMIT 1
            """,
            tuple(candidates),
        ).fetchone()
        if row:
            return dict(row)

        rows = conn.execute(
            """
            SELECT * FROM scan_runs
            WHERE report_path IS NOT NULL
            ORDER BY id DESC
            """
        ).fetchall()
        for candidate in rows:
            if Path(candidate["report_path"]).name == path.name:
                return dict(candidate)
    return None


def get_daily_candidate_state(run_id, policy_version, db_path=DB_PATH):
    with get_connection(db_path) as conn:
        init_db(conn)
        run = conn.execute(
            "SELECT id, run_at, trade_date, mode FROM scan_runs WHERE id = ?",
            (int(run_id),),
        ).fetchone()
        if not run:
            raise ValueError(f"scan run {run_id} does not exist")

        rows = conn.execute(
            """
            SELECT ce.code, ce.industry, ce.tradable,
                   ce.is_first_eligible_event, ce.is_selected
            FROM candidate_events ce
            JOIN scan_runs sr ON sr.id = ce.run_id
            WHERE sr.trade_date = ?
              AND sr.mode = ?
              AND ce.policy_version = ?
              AND (
                    sr.run_at < ?
                    OR (sr.run_at = ? AND sr.id < ?)
              )
            """,
            (
                run["trade_date"],
                run["mode"],
                policy_version,
                run["run_at"],
                run["run_at"],
                int(run_id),
            ),
        ).fetchall()

    selected_industry_counts = {}
    for row in rows:
        if row["is_selected"] and row["industry"]:
            selected_industry_counts[row["industry"]] = (
                selected_industry_counts.get(row["industry"], 0) + 1
            )

    return {
        "eligible_codes": {
            row["code"] for row in rows if row["is_first_eligible_event"]
        },
        "selected_count": sum(int(row["is_selected"] or 0) for row in rows),
        "selected_industries": set(selected_industry_counts),
        "selected_industry_counts": selected_industry_counts,
    }


def save_candidate_events(run_id, events, db_path=DB_PATH):
    events = list(events)
    now = get_taipei_now().isoformat(timespec="seconds")

    with get_connection(db_path) as conn:
        init_db(conn)
        run = conn.execute(
            "SELECT id, run_at FROM scan_runs WHERE id = ?", (int(run_id),)
        ).fetchone()
        if not run:
            raise ValueError(f"scan run {run_id} does not exist")

        policies = {str(event["policy_version"]) for event in events}
        for event in events:
            code = str(event["code"])
            signal_id = event.get("signal_id")
            if signal_id is None:
                signal = conn.execute(
                    """
                    SELECT id FROM stock_signals
                    WHERE run_id = ? AND code = ?
                    ORDER BY rank_order, id
                    LIMIT 1
                    """,
                    (int(run_id), code),
                ).fetchone()
                signal_id = signal["id"] if signal else None

            conn.execute(
                """
                INSERT INTO candidate_events (
                    run_id, signal_id, code, name, as_of, industry,
                    strategies_json, strategy_count, raw_rank, score,
                    signal_price, pct_change, turnover_billion, volume_ratio_5,
                    intraday_position, observation_price, chase_limit,
                    stop_distance_pct, tradable, block_reasons_json,
                    risk_flags_json, is_first_eligible_event, is_selected,
                    selection_rank, selection_status, policy_version,
                    policy_config_json, snapshot_json, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(run_id, code, policy_version) DO UPDATE SET
                    signal_id = excluded.signal_id,
                    name = excluded.name,
                    as_of = excluded.as_of,
                    industry = excluded.industry,
                    strategies_json = excluded.strategies_json,
                    strategy_count = excluded.strategy_count,
                    raw_rank = excluded.raw_rank,
                    score = excluded.score,
                    signal_price = excluded.signal_price,
                    pct_change = excluded.pct_change,
                    turnover_billion = excluded.turnover_billion,
                    volume_ratio_5 = excluded.volume_ratio_5,
                    intraday_position = excluded.intraday_position,
                    observation_price = excluded.observation_price,
                    chase_limit = excluded.chase_limit,
                    stop_distance_pct = excluded.stop_distance_pct,
                    tradable = excluded.tradable,
                    block_reasons_json = excluded.block_reasons_json,
                    risk_flags_json = excluded.risk_flags_json,
                    is_first_eligible_event = excluded.is_first_eligible_event,
                    is_selected = excluded.is_selected,
                    selection_rank = excluded.selection_rank,
                    selection_status = excluded.selection_status,
                    policy_config_json = excluded.policy_config_json,
                    snapshot_json = excluded.snapshot_json,
                    updated_at = excluded.updated_at
                """,
                (
                    int(run_id),
                    signal_id,
                    code,
                    event.get("name"),
                    event.get("as_of") or run["run_at"],
                    event.get("industry"),
                    event.get("strategies_json", "[]"),
                    int(event.get("strategy_count") or 0),
                    event.get("raw_rank"),
                    event.get("score"),
                    event.get("signal_price"),
                    event.get("pct_change"),
                    event.get("turnover_billion"),
                    event.get("volume_ratio_5"),
                    event.get("intraday_position"),
                    event.get("observation_price"),
                    event.get("chase_limit"),
                    event.get("stop_distance_pct"),
                    int(bool(event.get("tradable"))),
                    event.get("block_reasons_json", "[]"),
                    event.get("risk_flags_json", "[]"),
                    int(bool(event.get("is_first_eligible_event"))),
                    int(bool(event.get("is_selected"))),
                    event.get("selection_rank"),
                    event.get("selection_status", "blocked"),
                    event["policy_version"],
                    event.get("policy_config_json", "{}"),
                    event.get("snapshot_json", "{}"),
                    now,
                    now,
                ),
            )

        for policy_version in policies:
            current_codes = [
                str(event["code"])
                for event in events
                if str(event["policy_version"]) == policy_version
            ]
            if current_codes:
                placeholders = ", ".join("?" for _ in current_codes)
                conn.execute(
                    f"""
                    DELETE FROM candidate_events
                    WHERE run_id = ? AND policy_version = ?
                      AND code NOT IN ({placeholders})
                    """,
                    (int(run_id), policy_version, *current_codes),
                )
        conn.commit()

    return len(events)
