import datetime
import os
import sqlite3
import subprocess
from pathlib import Path

DB_PATH = Path("data/stock_scanner.db")
STRATEGY_VERSION = "strict_v1"


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


def get_connection(db_path=DB_PATH):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_trade_date ON stock_signals(trade_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_strategy ON stock_signals(strategy)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_backtest_status ON backtest_results(outcome_status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_predictions_run_rank ON predictions(run_id, rank_order)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_outcomes_status ON prediction_outcomes(outcome_status)")
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
        CREATE TABLE IF NOT EXISTS feature_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            signal_id INTEGER,
            code TEXT NOT NULL,
            as_of TEXT NOT NULL,
            feature_version TEXT NOT NULL,
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
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            signal_id INTEGER,
            code TEXT NOT NULL,
            predicted_at TEXT NOT NULL,
            model_version TEXT NOT NULL,
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
