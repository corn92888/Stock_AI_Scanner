import datetime as dt
import hashlib
import sqlite3
from pathlib import Path

import pandas as pd


LOCAL_PORTFOLIO_DB = Path("data/portfolio_holdings.db")
PORTFOLIO_INPUT_COLUMNS = ["代號", "成本", "股數", "停損價", "目標價", "備註"]
TAIPEI_TZ = dt.timezone(dt.timedelta(hours=8), name="Asia/Taipei")


def normalize_code(value):
    if value is None or value != value:
        return ""
    code = str(value).strip().upper().replace(".TW", "").replace(".TWO", "")
    if code.endswith(".0"):
        code = code[:-2]
    return code.zfill(4) if code.isdigit() and len(code) < 4 else code


def normalize_owner_key(owner_key):
    return str(owner_key or "").strip().lower()


def make_owner_id(owner_key):
    key = normalize_owner_key(owner_key)
    return hashlib.sha256(key.encode("utf-8")).hexdigest() if key else ""


def _now_iso():
    return dt.datetime.now(TAIPEI_TZ).isoformat(timespec="seconds")


def _safe_float(value, default=None):
    try:
        if value is None or value == "" or value != value:
            return default
        return float(value)
    except Exception:
        return default


def _secret_section(secrets, name):
    try:
        section = secrets.get(name, {})
    except Exception:
        return {}
    try:
        return dict(section)
    except Exception:
        return {}


def _nested_secret_section(secrets, first, second):
    outer = _secret_section(secrets, first)
    section = outer.get(second, {}) if isinstance(outer, dict) else {}
    try:
        return dict(section)
    except Exception:
        return {}


def get_supabase_config(secrets):
    direct = _secret_section(secrets, "supabase")
    connection = _nested_secret_section(secrets, "connections", "supabase")

    url = (
        direct.get("url")
        or direct.get("SUPABASE_URL")
        or connection.get("url")
        or connection.get("SUPABASE_URL")
    )
    key = (
        direct.get("service_role_key")
        or direct.get("service_key")
        or direct.get("anon_key")
        or direct.get("key")
        or direct.get("SUPABASE_SERVICE_ROLE_KEY")
        or direct.get("SUPABASE_KEY")
        or connection.get("service_role_key")
        or connection.get("service_key")
        or connection.get("anon_key")
        or connection.get("key")
        or connection.get("SUPABASE_SERVICE_ROLE_KEY")
        or connection.get("SUPABASE_KEY")
    )
    return {"url": url, "key": key}


def _get_supabase_client(secrets):
    config = get_supabase_config(secrets)
    if not config["url"] or not config["key"]:
        return None, "尚未設定 Supabase URL/key"
    try:
        from supabase import create_client
    except ImportError:
        return None, "尚未安裝 supabase 套件"
    try:
        return create_client(config["url"], config["key"]), ""
    except Exception as exc:
        return None, f"Supabase 連線初始化失敗：{exc}"


def get_portfolio_backend_status(secrets):
    client, error = _get_supabase_client(secrets)
    if client:
        return {
            "backend": "supabase",
            "label": "Supabase 雲端資料庫",
            "is_cloud": True,
            "message": "持股會儲存在 Supabase，可跨裝置與多人使用。",
        }
    return {
        "backend": "local",
        "label": "本機 SQLite 暫存",
        "is_cloud": False,
        "message": f"{error}，目前先寫入本機 data/portfolio_holdings.db。",
    }


def normalize_holdings_for_storage(df):
    if df is None or df.empty:
        return pd.DataFrame(columns=PORTFOLIO_INPUT_COLUMNS)

    data = df.copy()
    for col in PORTFOLIO_INPUT_COLUMNS:
        if col not in data.columns:
            data[col] = "" if col in ["代號", "備註"] else 0.0

    data["代號"] = data["代號"].apply(normalize_code)
    for col in ["成本", "股數", "停損價", "目標價"]:
        data[col] = pd.to_numeric(data[col], errors="coerce").fillna(0.0)
    data["備註"] = data["備註"].fillna("").astype(str)
    data = data[(data["代號"] != "") & (data["成本"] > 0) & (data["股數"] > 0)]
    return data[PORTFOLIO_INPUT_COLUMNS].drop_duplicates(subset=["代號"], keep="last").reset_index(drop=True)


def _records_to_holdings(records):
    rows = []
    for record in records or []:
        rows.append(
            {
                "代號": normalize_code(record.get("code")),
                "成本": _safe_float(record.get("cost"), 0.0),
                "股數": _safe_float(record.get("shares"), 0.0),
                "停損價": _safe_float(record.get("stop_price"), 0.0) or 0.0,
                "目標價": _safe_float(record.get("target_price"), 0.0) or 0.0,
                "備註": record.get("note") or "",
            }
        )
    return pd.DataFrame(rows, columns=PORTFOLIO_INPUT_COLUMNS)


def _get_local_connection(db_path=LOCAL_PORTFOLIO_DB):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _init_local_db(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS portfolio_holdings (
            owner_id TEXT NOT NULL,
            code TEXT NOT NULL,
            stock_name TEXT,
            cost REAL NOT NULL,
            shares REAL NOT NULL,
            stop_price REAL,
            target_price REAL,
            note TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (owner_id, code)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            owner_id TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            snapshot_at TEXT NOT NULL,
            code TEXT NOT NULL,
            stock_name TEXT,
            shares REAL NOT NULL,
            cost REAL NOT NULL,
            price REAL,
            market_value REAL,
            unrealized_pnl REAL,
            pnl_pct REAL,
            today_pct REAL,
            decision_score INTEGER,
            holding_status TEXT,
            action TEXT,
            strategy_status TEXT,
            market_report TEXT,
            scan_report TEXT,
            PRIMARY KEY (owner_id, trade_date, code)
        )
        """
    )
    conn.commit()


def load_holdings(owner_key, secrets=None):
    owner_id = make_owner_id(owner_key)
    if not owner_id:
        return pd.DataFrame(columns=PORTFOLIO_INPUT_COLUMNS), {"backend": "none", "count": 0}

    client, _ = _get_supabase_client(secrets if secrets is not None else {})
    if client:
        try:
            response = (
                client.table("portfolio_holdings")
                .select("code,cost,shares,stop_price,target_price,note")
                .eq("owner_id", owner_id)
                .order("code")
                .execute()
            )
        except Exception as exc:
            return pd.DataFrame(columns=PORTFOLIO_INPUT_COLUMNS), {
                "backend": "supabase",
                "count": 0,
                "error": str(exc),
            }
        data = _records_to_holdings(getattr(response, "data", []) or [])
        return data, {"backend": "supabase", "count": len(data)}

    with _get_local_connection() as conn:
        _init_local_db(conn)
        rows = conn.execute(
            """
            SELECT code, cost, shares, stop_price, target_price, note
            FROM portfolio_holdings
            WHERE owner_id = ?
            ORDER BY code
            """,
            (owner_id,),
        ).fetchall()
    data = _records_to_holdings([dict(row) for row in rows])
    return data, {"backend": "local", "count": len(data)}


def save_holdings(owner_key, holdings_df, secrets=None):
    owner_id = make_owner_id(owner_key)
    if not owner_id:
        raise ValueError("缺少股票倉識別碼")

    holdings = normalize_holdings_for_storage(holdings_df)
    if holdings.empty:
        raise ValueError("沒有可儲存的持股，請至少輸入代號、成本與股數")

    updated_at = _now_iso()
    records = []
    for _, row in holdings.iterrows():
        records.append(
            {
                "owner_id": owner_id,
                "code": row["代號"],
                "stock_name": "",
                "cost": float(row["成本"]),
                "shares": float(row["股數"]),
                "stop_price": _safe_float(row["停損價"]),
                "target_price": _safe_float(row["目標價"]),
                "note": str(row["備註"]).strip(),
                "updated_at": updated_at,
            }
        )

    client, _ = _get_supabase_client(secrets if secrets is not None else {})
    if client:
        client.table("portfolio_holdings").delete().eq("owner_id", owner_id).execute()
        client.table("portfolio_holdings").insert(records).execute()
        return {"backend": "supabase", "count": len(records), "updated_at": updated_at}

    with _get_local_connection() as conn:
        _init_local_db(conn)
        conn.execute("DELETE FROM portfolio_holdings WHERE owner_id = ?", (owner_id,))
        conn.executemany(
            """
            INSERT INTO portfolio_holdings (
                owner_id, code, stock_name, cost, shares, stop_price, target_price, note, updated_at
            ) VALUES (
                :owner_id, :code, :stock_name, :cost, :shares, :stop_price, :target_price, :note, :updated_at
            )
            """,
            records,
        )
        conn.commit()
    return {"backend": "local", "count": len(records), "updated_at": updated_at}


def save_portfolio_snapshot(owner_key, analysis_df, market_report="", scan_report="", secrets=None):
    owner_id = make_owner_id(owner_key)
    if not owner_id:
        raise ValueError("缺少股票倉識別碼")
    if analysis_df is None or analysis_df.empty:
        raise ValueError("沒有可寫入快照的持股分析")

    snapshot_at = _now_iso()
    trade_date = dt.datetime.now(TAIPEI_TZ).date().isoformat()
    records = []
    for _, row in analysis_df.iterrows():
        records.append(
            {
                "owner_id": owner_id,
                "trade_date": trade_date,
                "snapshot_at": snapshot_at,
                "code": normalize_code(row.get("代號")),
                "stock_name": str(row.get("名稱", "")),
                "shares": _safe_float(row.get("股數"), 0.0),
                "cost": _safe_float(row.get("成本"), 0.0),
                "price": _safe_float(row.get("現價")),
                "market_value": _safe_float(row.get("市值")),
                "unrealized_pnl": _safe_float(row.get("未實現損益")),
                "pnl_pct": _safe_float(row.get("損益率(%)")),
                "today_pct": _safe_float(row.get("今日漲跌幅(%)")),
                "decision_score": int(row.get("續抱分數")) if row.get("續抱分數") == row.get("續抱分數") else None,
                "holding_status": str(row.get("持股狀態", "")),
                "action": str(row.get("行動建議", "")),
                "strategy_status": str(row.get("策略命中", "")),
                "market_report": Path(str(market_report)).name if market_report else "",
                "scan_report": Path(str(scan_report)).name if scan_report else "",
            }
        )

    client, _ = _get_supabase_client(secrets if secrets is not None else {})
    if client:
        client.table("portfolio_snapshots").upsert(records, on_conflict="owner_id,trade_date,code").execute()
        return {"backend": "supabase", "count": len(records), "snapshot_at": snapshot_at}

    with _get_local_connection() as conn:
        _init_local_db(conn)
        conn.executemany(
            """
            INSERT INTO portfolio_snapshots (
                owner_id, trade_date, snapshot_at, code, stock_name, shares, cost, price,
                market_value, unrealized_pnl, pnl_pct, today_pct, decision_score,
                holding_status, action, strategy_status, market_report, scan_report
            ) VALUES (
                :owner_id, :trade_date, :snapshot_at, :code, :stock_name, :shares, :cost, :price,
                :market_value, :unrealized_pnl, :pnl_pct, :today_pct, :decision_score,
                :holding_status, :action, :strategy_status, :market_report, :scan_report
            )
            ON CONFLICT(owner_id, trade_date, code) DO UPDATE SET
                snapshot_at = excluded.snapshot_at,
                stock_name = excluded.stock_name,
                shares = excluded.shares,
                cost = excluded.cost,
                price = excluded.price,
                market_value = excluded.market_value,
                unrealized_pnl = excluded.unrealized_pnl,
                pnl_pct = excluded.pnl_pct,
                today_pct = excluded.today_pct,
                decision_score = excluded.decision_score,
                holding_status = excluded.holding_status,
                action = excluded.action,
                strategy_status = excluded.strategy_status,
                market_report = excluded.market_report,
                scan_report = excluded.scan_report
            """,
            records,
        )
        conn.commit()
    return {"backend": "local", "count": len(records), "snapshot_at": snapshot_at}
