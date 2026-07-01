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
            stop_loss_hit INTEGER,
            stop_loss_date TEXT,
            tested_at TEXT NOT NULL,
            FOREIGN KEY (signal_id) REFERENCES stock_signals(id),
            UNIQUE (signal_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_trade_date ON stock_signals(trade_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_strategy ON stock_signals(strategy)")
    conn.commit()


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
    with get_connection(db_path) as conn:
        init_db(conn)
        conn.execute(
            """
            INSERT INTO backtest_results (
                signal_id, entry_date, entry_price,
                exit_1d_price, exit_3d_price, exit_5d_price, exit_10d_price, exit_20d_price,
                return_1d, return_3d, return_5d, return_10d, return_20d,
                max_return_20d, max_drawdown_20d, stop_loss_hit, stop_loss_date, tested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(signal_id) DO UPDATE SET
                entry_date = excluded.entry_date,
                entry_price = excluded.entry_price,
                exit_1d_price = excluded.exit_1d_price,
                exit_3d_price = excluded.exit_3d_price,
                exit_5d_price = excluded.exit_5d_price,
                exit_10d_price = excluded.exit_10d_price,
                exit_20d_price = excluded.exit_20d_price,
                return_1d = excluded.return_1d,
                return_3d = excluded.return_3d,
                return_5d = excluded.return_5d,
                return_10d = excluded.return_10d,
                return_20d = excluded.return_20d,
                max_return_20d = excluded.max_return_20d,
                max_drawdown_20d = excluded.max_drawdown_20d,
                stop_loss_hit = excluded.stop_loss_hit,
                stop_loss_date = excluded.stop_loss_date,
                tested_at = excluded.tested_at
            """,
            (
                signal_id,
                result.get("entry_date"),
                result.get("entry_price"),
                result.get("exit_1d_price"),
                result.get("exit_3d_price"),
                result.get("exit_5d_price"),
                result.get("exit_10d_price"),
                result.get("exit_20d_price"),
                result.get("return_1d"),
                result.get("return_3d"),
                result.get("return_5d"),
                result.get("return_10d"),
                result.get("return_20d"),
                result.get("max_return_20d"),
                result.get("max_drawdown_20d"),
                1 if result.get("stop_loss_hit") else 0,
                result.get("stop_loss_date"),
                tested_at,
            ),
        )
        conn.commit()
