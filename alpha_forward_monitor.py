import argparse
import json

import pandas as pd

from database import (
    ALPHA_FORWARD_START_DATE,
    ALPHA_FORWARD_VERSION,
    DB_PATH,
    get_connection,
    get_taipei_now,
    init_db,
)
from research_evaluation import _probabilistic_sharpe


CHAMPION_ACCOUNT_KEY = "alpha_v2_champion_forward_t10_v1"
FORWARD_ACCOUNT_KEYS = (
    CHAMPION_ACCOUNT_KEY,
    "alpha_v2_anti_chase_t10_v1",
    "alpha_v2_market_gate_t10_v1",
    "alpha_v2_momentum_t10_v1",
    "alpha_v2_random_t10_v1",
)
MIN_DECISION_DAYS = 120
MIN_CLOSED_TRADES = 150
MIN_PROFITABLE_MONTH_RATE = 0.60
MIN_PROBABILISTIC_SHARPE = 0.95
MIN_UNIVERSE_COVERAGE_PCT = 20.0
MAX_DRAWDOWN_PCT = -12.0
WATCH_DRAWDOWN_PCT = -8.0
MIN_QUOTE_COVERAGE_PCT = 65.0


def _decode(value, default):
    try:
        decoded = json.loads(value or "")
    except (TypeError, ValueError):
        return default
    return decoded if isinstance(decoded, type(default)) else default


def _round(value, digits=6):
    return round(float(value), digits) if value is not None else None


def _latest_alpha_run(conn):
    row = conn.execute(
        """
        SELECT id, signal_date, generated_at, status, universe_count,
               eligible_count, selected_count
        FROM alpha_live_runs
        ORDER BY signal_date DESC, generated_at DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["candidate_pool_rows"] = int(
        conn.execute(
            "SELECT COUNT(*) FROM alpha_live_candidates WHERE run_id=?",
            (row["id"],),
        ).fetchone()[0]
    )
    return result


def _latest_quote_health(conn):
    row = conn.execute(
        """
        SELECT trade_date, run_at, notes
        FROM scan_runs
        WHERE LOWER(mode)='intraday'
        ORDER BY trade_date DESC, run_at DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        return {
            "trade_date": None,
            "run_at": None,
            "coverage_pct": None,
            "attempts": None,
        }
    notes = _decode(row["notes"], {})
    coverage = notes.get("realtime_coverage")
    return {
        "trade_date": row["trade_date"],
        "run_at": row["run_at"],
        "coverage_pct": _round(float(coverage) * 100.0, 2)
        if coverage is not None
        else None,
        "attempts": notes.get("quote_attempts"),
    }


def _latest_research_health(conn):
    row = conn.execute(
        """
        SELECT status, checked_at, stale_outcomes, metrics_json
        FROM research_health_snapshots
        ORDER BY checked_at DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        return {
            "status": "not_run",
            "checked_at": None,
            "stale_outcomes": 0,
        }
    return {
        "status": row["status"],
        "checked_at": row["checked_at"],
        "stale_outcomes": int(row["stale_outcomes"] or 0),
    }


def _latest_cloud_health(conn):
    row = conn.execute(
        """
        SELECT status, event_at, error_code, migration_mode
        FROM cloud_evidence_events
        ORDER BY event_at DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        return {
            "status": "not_run",
            "event_at": None,
            "error_code": "",
            "git_fallback": True,
        }
    return {
        "status": row["status"],
        "event_at": row["event_at"],
        "error_code": row["error_code"] or "",
        "git_fallback": (row["migration_mode"] or "dual_write") != "cloud_primary",
    }


def _account_metrics(conn):
    placeholders = ", ".join("?" for _ in FORWARD_ACCOUNT_KEYS)
    accounts = conn.execute(
        f"""
        SELECT *
        FROM paper_accounts
        WHERE account_key IN ({placeholders})
        """,
        FORWARD_ACCOUNT_KEYS,
    ).fetchall()
    result = []
    for account in accounts:
        trades = conn.execute(
            """
            SELECT status, signal_date, net_return_pct, excess_return_pct
            FROM paper_trades
            WHERE account_id=? AND signal_date >= ?
            """,
            (account["id"], ALPHA_FORWARD_START_DATE),
        ).fetchall()
        closed = [row for row in trades if row["status"] == "closed"]
        returns = [
            float(row["net_return_pct"])
            for row in closed
            if row["net_return_pct"] is not None
        ]
        excess = [
            float(row["excess_return_pct"])
            for row in closed
            if row["excess_return_pct"] is not None
        ]
        config = _decode(account["config_json"], {})
        policy = config.get("capital_policy") or {}
        result.append(
            {
                "account_key": account["account_key"],
                "name": account["name"],
                "role": policy.get("role", "challenger"),
                "selection_policy": policy.get("selection_policy"),
                "total_return_pct": _round(account["total_return_pct"]),
                "max_drawdown_pct": _round(account["max_drawdown_pct"]),
                "closed_trades": len(closed),
                "open_positions": sum(row["status"] == "open" for row in trades),
                "pending_orders": sum(row["status"] == "pending" for row in trades),
                "signal_dates": len({row["signal_date"] for row in trades}),
                "win_rate_pct": _round(
                    sum(value > 0 for value in returns) / len(returns) * 100.0
                    if returns
                    else None
                ),
                "avg_net_return_pct": _round(
                    sum(returns) / len(returns) if returns else None
                ),
                "avg_excess_return_pct": _round(
                    sum(excess) / len(excess) if excess else None
                ),
            }
        )
    by_key = {row["account_key"]: row for row in result}
    return [by_key[key] for key in FORWARD_ACCOUNT_KEYS if key in by_key]


def _champion_equity_metrics(conn):
    account = conn.execute(
        "SELECT id, total_return_pct, max_drawdown_pct FROM paper_accounts "
        "WHERE account_key=?",
        (CHAMPION_ACCOUNT_KEY,),
    ).fetchone()
    if not account:
        return {
            "total_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "probabilistic_sharpe": None,
            "profitable_month_rate_pct": None,
            "month_count": 0,
        }
    rows = conn.execute(
        """
        SELECT as_of, equity
        FROM paper_equity_snapshots
        WHERE account_id=? AND as_of >= ?
        ORDER BY as_of
        """,
        (account["id"], ALPHA_FORWARD_START_DATE),
    ).fetchall()
    frame = pd.DataFrame([dict(row) for row in rows])
    daily_returns = []
    profitable_month_rate = None
    month_count = 0
    if not frame.empty:
        frame["as_of"] = pd.to_datetime(frame["as_of"])
        frame["equity"] = pd.to_numeric(frame["equity"], errors="coerce")
        daily_returns = frame["equity"].pct_change().dropna() * 100.0
        monthly = frame.set_index("as_of")["equity"].resample("ME").last().pct_change()
        monthly = monthly.dropna()
        month_count = int(len(monthly))
        if month_count:
            profitable_month_rate = float((monthly > 0).mean() * 100.0)
    return {
        "total_return_pct": _round(account["total_return_pct"]),
        "max_drawdown_pct": _round(account["max_drawdown_pct"]),
        "probabilistic_sharpe": _round(_probabilistic_sharpe(daily_returns)),
        "profitable_month_rate_pct": _round(profitable_month_rate),
        "month_count": month_count,
    }


def _cohort_metrics(conn):
    placeholders = ", ".join("?" for _ in FORWARD_ACCOUNT_KEYS)
    rows = conn.execute(
        f"""
        SELECT pa.account_key, pa.name, pt.signal_date,
               COUNT(*) AS trades,
               SUM(CASE WHEN pt.status='closed' THEN 1 ELSE 0 END) AS closed,
               SUM(CASE WHEN pt.status='open' THEN 1 ELSE 0 END) AS open,
               AVG(CASE WHEN pt.status='closed' THEN pt.net_return_pct END)
                   AS avg_net_return_pct,
               AVG(CASE WHEN pt.status='closed' THEN pt.excess_return_pct END)
                   AS avg_excess_return_pct
        FROM paper_trades pt
        JOIN paper_accounts pa ON pa.id=pt.account_id
        WHERE pa.account_key IN ({placeholders})
          AND pt.signal_date >= ?
        GROUP BY pa.account_key, pa.name, pt.signal_date
        ORDER BY pt.signal_date DESC, pa.account_key
        LIMIT 150
        """,
        (*FORWARD_ACCOUNT_KEYS, ALPHA_FORWARD_START_DATE),
    ).fetchall()
    return [
        {
            "account_key": row["account_key"],
            "name": row["name"],
            "signal_date": row["signal_date"],
            "trades": int(row["trades"] or 0),
            "closed": int(row["closed"] or 0),
            "open": int(row["open"] or 0),
            "avg_net_return_pct": _round(row["avg_net_return_pct"]),
            "avg_excess_return_pct": _round(row["avg_excess_return_pct"]),
        }
        for row in rows
    ]


def build_alpha_forward_metrics(db_path=DB_PATH):
    with get_connection(db_path) as conn:
        init_db(conn)
        latest_run = _latest_alpha_run(conn)
        quote_health = _latest_quote_health(conn)
        research_health = _latest_research_health(conn)
        cloud_health = _latest_cloud_health(conn)
        accounts = _account_metrics(conn)
        champion = next(
            (
                account
                for account in accounts
                if account["account_key"] == CHAMPION_ACCOUNT_KEY
            ),
            None,
        )
        equity = _champion_equity_metrics(conn)
        decision_days = int(
            conn.execute(
                """
                SELECT COUNT(DISTINCT signal_date)
                FROM alpha_live_runs
                WHERE signal_date >= ?
                  AND status IN ('active', 'abstained', 'paused')
                """,
                (ALPHA_FORWARD_START_DATE,),
            ).fetchone()[0]
        )
        cohorts = _cohort_metrics(conn)

    universe_coverage_pct = (
        float(latest_run["eligible_count"]) / float(latest_run["universe_count"]) * 100
        if latest_run and latest_run["universe_count"]
        else 0.0
    )
    candidate_pool_rows = int(
        latest_run["candidate_pool_rows"] if latest_run else 0
    )
    latest_is_forward = bool(
        latest_run and latest_run["signal_date"] >= ALPHA_FORWARD_START_DATE
    )
    pool_expected = bool(
        latest_is_forward
        and latest_run["eligible_count"] > 0
        and latest_run["status"] in {"active", "abstained", "paused"}
    )
    candidate_pool_healthy = not pool_expected or candidate_pool_rows > 0
    quote_healthy = (
        quote_health["coverage_pct"] is None
        or quote_health["coverage_pct"] >= MIN_QUOTE_COVERAGE_PCT
    )
    alpha_run_healthy = not latest_is_forward or latest_run["status"] != "blocked"
    data_integrity_healthy = (
        candidate_pool_healthy and quote_healthy and alpha_run_healthy
    )
    warnings = []
    reason_codes = []
    if not candidate_pool_healthy:
        reason_codes.append("candidate_pool_missing")
        warnings.append("最新 Alpha 評分有合格標的，但完整候選池未寫入。")
    if not quote_healthy:
        reason_codes.append("intraday_quote_coverage_low")
        warnings.append("最新盤中報價覆蓋低於 65%，不得依該輪建立決策。")
    if not alpha_run_healthy:
        reason_codes.append("alpha_live_run_blocked")
        warnings.append("最新 Alpha 全市場評分因資料完整性被阻擋。")
    if research_health["stale_outcomes"] > 0:
        warnings.append(
            f"舊研究管線仍有 {research_health['stale_outcomes']} 筆逾期結果待處理。"
        )
    if cloud_health["error_code"]:
        warnings.append(
            "雲端證據庫連線降級，Git 資料庫備援仍在運作。"
            if cloud_health["git_fallback"]
            else "雲端證據庫異常，且目前沒有 Git 備援。"
        )

    closed_trades = int(champion["closed_trades"] if champion else 0)
    avg_net = champion["avg_net_return_pct"] if champion else None
    avg_excess = champion["avg_excess_return_pct"] if champion else None
    total_return = equity["total_return_pct"]
    max_drawdown = equity["max_drawdown_pct"]
    psr = equity["probabilistic_sharpe"]
    profitable_month_rate = equity["profitable_month_rate_pct"]
    gates = [
        {
            "key": "decision_days",
            "label": "獨立前瞻決策日",
            "value": decision_days,
            "requirement": f">= {MIN_DECISION_DAYS}",
            "passed": decision_days >= MIN_DECISION_DAYS,
        },
        {
            "key": "closed_trades",
            "label": "冠軍帳戶結案交易",
            "value": closed_trades,
            "requirement": f">= {MIN_CLOSED_TRADES}",
            "passed": closed_trades >= MIN_CLOSED_TRADES,
        },
        {
            "key": "total_return",
            "label": "冠軍帳戶總報酬",
            "value": total_return,
            "requirement": "> 0%",
            "passed": total_return > 0,
        },
        {
            "key": "average_excess_return",
            "label": "平均成本後超額",
            "value": avg_excess,
            "requirement": "> 0%",
            "passed": avg_excess is not None and avg_excess > 0,
        },
        {
            "key": "max_drawdown",
            "label": "最大回撤",
            "value": max_drawdown,
            "requirement": f"> {MAX_DRAWDOWN_PCT:.0f}%",
            "passed": max_drawdown > MAX_DRAWDOWN_PCT,
        },
        {
            "key": "profitable_month_rate",
            "label": "獲利月份比例",
            "value": profitable_month_rate,
            "requirement": f">= {MIN_PROFITABLE_MONTH_RATE * 100:.0f}%",
            "passed": (
                equity["month_count"] >= 3
                and profitable_month_rate is not None
                and profitable_month_rate >= MIN_PROFITABLE_MONTH_RATE * 100
            ),
        },
        {
            "key": "probabilistic_sharpe",
            "label": "機率夏普比率",
            "value": psr,
            "requirement": f">= {MIN_PROBABILISTIC_SHARPE:.2f}",
            "passed": psr is not None and psr >= MIN_PROBABILISTIC_SHARPE,
        },
        {
            "key": "universe_coverage",
            "label": "Alpha 合格股票池覆蓋",
            "value": _round(universe_coverage_pct, 2),
            "requirement": f">= {MIN_UNIVERSE_COVERAGE_PCT:.0f}%",
            "passed": universe_coverage_pct >= MIN_UNIVERSE_COVERAGE_PCT,
        },
        {
            "key": "candidate_pool_integrity",
            "label": "同日完整候選池",
            "value": candidate_pool_rows,
            "requirement": "> 0 when eligible",
            "passed": candidate_pool_healthy,
        },
    ]
    enough_evidence = (
        decision_days >= MIN_DECISION_DAYS
        and closed_trades >= MIN_CLOSED_TRADES
        and equity["month_count"] >= 3
    )
    if not data_integrity_healthy or max_drawdown <= MAX_DRAWDOWN_PCT:
        state = "PAUSED"
        if max_drawdown <= MAX_DRAWDOWN_PCT:
            reason_codes.append("drawdown_stop_triggered")
    elif enough_evidence and all(gate["passed"] for gate in gates):
        state = "HEALTHY"
    elif enough_evidence or max_drawdown <= WATCH_DRAWDOWN_PCT:
        state = "WATCH"
        if max_drawdown <= WATCH_DRAWDOWN_PCT:
            reason_codes.append("drawdown_watch_triggered")
    else:
        state = "COLLECTING"
        reason_codes.append("minimum_evidence_not_reached")

    if not latest_run:
        data_quality_status = "waiting"
    elif not data_integrity_healthy:
        data_quality_status = "critical"
    elif warnings:
        data_quality_status = "degraded"
    else:
        data_quality_status = "healthy"
    return {
        "version": ALPHA_FORWARD_VERSION,
        "evidence_start_date": ALPHA_FORWARD_START_DATE,
        "state": state,
        "allow_new_positions": state != "PAUSED",
        "minimum_decision_days": MIN_DECISION_DAYS,
        "minimum_closed_trades": MIN_CLOSED_TRADES,
        "decision_days": decision_days,
        "closed_trades": closed_trades,
        "open_positions": int(champion["open_positions"] if champion else 0),
        "total_return_pct": total_return,
        "max_drawdown_pct": max_drawdown,
        "avg_net_return_pct": avg_net,
        "avg_excess_return_pct": avg_excess,
        "positive_rate_pct": champion["win_rate_pct"] if champion else None,
        "profitable_month_rate_pct": profitable_month_rate,
        "profitable_month_count": equity["month_count"],
        "probabilistic_sharpe": psr,
        "latest_signal_date": latest_run["signal_date"] if latest_run else None,
        "latest_signal_status": latest_run["status"] if latest_run else "not_run",
        "universe_coverage_pct": _round(universe_coverage_pct, 2),
        "candidate_pool_rows": candidate_pool_rows,
        "data_quality_status": data_quality_status,
        "quote_health": quote_health,
        "research_health": research_health,
        "cloud_health": cloud_health,
        "warnings": warnings,
        "reason_codes": list(dict.fromkeys(reason_codes)),
        "gates": gates,
        "accounts": accounts,
        "cohorts": cohorts,
    }


def save_alpha_forward_metrics(metrics, db_path=DB_PATH):
    evaluated_at = get_taipei_now().isoformat(timespec="seconds")
    with get_connection(db_path) as conn:
        init_db(conn)
        conn.execute(
            """
            INSERT INTO alpha_forward_snapshots (
                evaluated_at, validation_version, evidence_start_date,
                state, allow_new_positions, metrics_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(validation_version, evaluated_at) DO UPDATE SET
                evidence_start_date=excluded.evidence_start_date,
                state=excluded.state,
                allow_new_positions=excluded.allow_new_positions,
                metrics_json=excluded.metrics_json
            """,
            (
                evaluated_at,
                metrics["version"],
                metrics["evidence_start_date"],
                metrics["state"],
                int(metrics["allow_new_positions"]),
                json.dumps(metrics, ensure_ascii=True, sort_keys=True),
            ),
        )
    return evaluated_at


def run_alpha_forward_monitor(db_path=DB_PATH, save=True):
    metrics = build_alpha_forward_metrics(db_path)
    if save:
        metrics["evaluated_at"] = save_alpha_forward_metrics(metrics, db_path)
    return metrics


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate prospective Alpha v2 evidence and trading governance."
    )
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()
    metrics = run_alpha_forward_monitor(args.db, save=not args.no_save)
    print(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
