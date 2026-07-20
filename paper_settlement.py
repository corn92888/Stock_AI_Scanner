import argparse
import datetime as dt
import json
import os

import requests

from candidate_backtest import load_pending_candidates, run_candidate_backtest
from database import DB_PATH, get_connection, get_taipei_now, init_db
from paper_trading import run_paper_trading


REASON_LABELS = {
    "above_chase_limit": "開盤超過禁止追價線",
    "gap_below_defense": "開盤跌破防守價",
    "duplicate_open_position": "已有同一標的持倉",
    "max_positions_reached": "已達持倉上限",
    "insufficient_cash": "可用資金不足",
    "industry_exposure_limit": "已達產業曝險上限",
    "invalid_stop_at_entry": "進場價未高於防守價",
}


def _session_date(value=None):
    if value is None:
        return get_taipei_now().date()
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value))


def _trade_state(db_path):
    with get_connection(db_path) as conn:
        init_db(conn)
        rows = conn.execute(
            """
            SELECT pa.account_key, pt.source_type, pt.source_id,
                   pt.code, pt.name, pt.status, pt.skip_reason,
                   pt.entry_at, pt.entry_price
            FROM paper_trades pt
            JOIN paper_accounts pa ON pa.id=pt.account_id
            """
        ).fetchall()
    return {
        (row["account_key"], row["source_type"], int(row["source_id"])): dict(row)
        for row in rows
    }


def _completed_settlement(db_path, session_date):
    with get_connection(db_path) as conn:
        init_db(conn)
        row = conn.execute(
            """
            SELECT settlement_at
            FROM paper_settlement_runs
            WHERE session_date=? AND status='completed'
            ORDER BY settlement_at DESC, id DESC
            LIMIT 1
            """,
            (str(session_date),),
        ).fetchone()
    return row["settlement_at"] if row else None


def _transitions(before, after):
    changes = []
    for key, current in after.items():
        previous = before.get(key)
        previous_status = previous.get("status") if previous else None
        if previous_status == current.get("status"):
            continue
        changes.append(
            {
                "accountKey": current.get("account_key"),
                "code": current.get("code"),
                "name": current.get("name"),
                "from": previous_status,
                "to": current.get("status"),
                "reason": current.get("skip_reason"),
                "entryAt": current.get("entry_at"),
                "entryPrice": current.get("entry_price"),
            }
        )
    return changes


def _save_run(db_path, payload, status, error_text=None):
    now = get_taipei_now().isoformat(timespec="seconds")
    transitions = payload.get("transitions", [])
    with get_connection(db_path) as conn:
        init_db(conn)
        conn.execute(
            """
            INSERT INTO paper_settlement_runs (
                settlement_at, session_date, source, status,
                eligible_candidates, outcomes_saved, accounts_updated,
                new_open_positions, new_skipped_orders, new_closed_positions,
                pending_orders, open_positions, error_text, metrics_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["settlementAt"],
                payload["sessionDate"],
                payload["source"],
                status,
                int(payload.get("eligibleCandidates", 0)),
                int(payload.get("outcomes", {}).get("saved", 0)),
                int(payload.get("accountsUpdated", 0)),
                sum(change.get("to") == "open" for change in transitions),
                sum(change.get("to") == "skipped" for change in transitions),
                sum(change.get("to") == "closed" for change in transitions),
                int(payload.get("pendingOrders", 0)),
                int(payload.get("openPositions", 0)),
                error_text,
                json.dumps(payload, ensure_ascii=True, sort_keys=True),
                now,
            ),
        )


def _telegram_message(payload):
    transitions = payload.get("transitions", [])
    opened = [row for row in transitions if row.get("to") == "open"]
    skipped = [row for row in transitions if row.get("to") == "skipped"]
    closed = [row for row in transitions if row.get("to") == "closed"]
    lines = [
        f"模擬資金開盤結算｜{payload['sessionDate']}",
        "模式：PAPER ONLY｜正式資金維持 CASH",
        (
            f"新成交 {len(opened)}｜新未成交 {len(skipped)}｜"
            f"新結案 {len(closed)}"
        ),
        (
            f"目前持有 {payload.get('openPositions', 0)}｜"
            f"等待成交 {payload.get('pendingOrders', 0)}"
        ),
    ]
    for row in (opened + skipped + closed)[:8]:
        status = {"open": "成交", "skipped": "未成交", "closed": "結案"}.get(
            row.get("to"), row.get("to")
        )
        detail = REASON_LABELS.get(row.get("reason"), row.get("reason"))
        if row.get("to") == "open" and row.get("entryPrice") is not None:
            detail = f"{float(row['entryPrice']):.2f}"
        suffix = f"｜{detail}" if detail else ""
        lines.append(f"{row.get('code')} {row.get('name') or ''}｜{status}{suffix}")
    if not transitions:
        lines.append("本次沒有新的狀態變更。")
    return "\n".join(lines)


def send_telegram(payload):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={
            "chat_id": chat_id,
            "text": _telegram_message(payload),
            "disable_web_page_preview": True,
        },
        timeout=20,
    )
    response.raise_for_status()
    return True


def run_opening_settlement(
    db_path=DB_PATH,
    session_date=None,
    source=None,
    outcome_limit=500,
    send_notification=False,
    pending_loader=load_pending_candidates,
    backtest_runner=run_candidate_backtest,
    paper_runner=run_paper_trading,
    notifier=send_telegram,
    force=False,
):
    session = _session_date(session_date)
    session_text = session.isoformat()
    source = source or ("github_action" if os.getenv("GITHUB_ACTIONS") else "local")
    settlement_at = get_taipei_now().isoformat(timespec="seconds")
    payload = {
        "version": "opening_paper_settlement_v1",
        "settlementAt": settlement_at,
        "sessionDate": session_text,
        "source": source,
        "entryPolicy": "prior_eod_signal_next_session_open",
        "lookaheadProtected": True,
    }

    completed_at = _completed_settlement(db_path, session_text)
    if completed_at and not force:
        return {
            **payload,
            "status": "skipped",
            "reason": "session_already_settled",
            "completedAt": completed_at,
        }

    before = _trade_state(db_path)
    try:
        eligible = pending_loader(
            db_path=db_path,
            modes=("eod",),
            trade_date_before=session_text,
            newest_first=True,
            limit=outcome_limit,
        )
        payload["eligibleCandidates"] = len(eligible)
        payload["outcomes"] = backtest_runner(
            db_path=db_path,
            modes=("eod",),
            trade_date_before=session_text,
            newest_first=True,
            limit=outcome_limit,
        )
        summaries = paper_runner(db_path=db_path, as_of=session_text)
        after = _trade_state(db_path)
        payload["accountsUpdated"] = len(summaries)
        payload["transitions"] = _transitions(before, after)
        payload["pendingOrders"] = sum(
            row.get("status") == "pending" for row in after.values()
        )
        payload["openPositions"] = sum(
            row.get("status") == "open" for row in after.values()
        )
        payload["status"] = (
            "waiting_market_data"
            if payload["eligibleCandidates"] > 0
            and int(payload["outcomes"].get("saved", 0)) == 0
            else "completed"
        )
        if send_notification:
            try:
                payload["telegramSent"] = bool(notifier(payload))
            except Exception as exc:
                payload["telegramSent"] = False
                payload["telegramError"] = str(exc)
        _save_run(db_path, payload, payload["status"])
        return payload
    except Exception as exc:
        payload.update(status="failed", error=str(exc))
        _save_run(db_path, payload, "failed", error_text=str(exc))
        raise


def main():
    parser = argparse.ArgumentParser(
        description="Settle prior-session EOD paper orders after the market opens."
    )
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--session-date")
    parser.add_argument("--source")
    parser.add_argument("--outcome-limit", type=int, default=500)
    parser.add_argument("--send-telegram", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    result = run_opening_settlement(
        db_path=args.db,
        session_date=args.session_date,
        source=args.source,
        outcome_limit=args.outcome_limit,
        send_notification=args.send_telegram,
        force=args.force,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
