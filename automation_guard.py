import argparse
import datetime as dt
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path


TAIPEI_TZ = dt.timezone(dt.timedelta(hours=8), name="Asia/Taipei")


@dataclass(frozen=True)
class ScanSlot:
    name: str
    start: dt.time
    end: dt.time
    cron: str


INTRADAY_SLOTS = (
    ScanSlot("09:00", dt.time(9, 0), dt.time(9, 29, 59), "7 1 * * 1-5"),
    ScanSlot("09:30", dt.time(9, 30), dt.time(9, 59, 59), "37 1 * * 1-5"),
    ScanSlot("10:00", dt.time(10, 0), dt.time(10, 29, 59), "7 2 * * 1-5"),
    ScanSlot("10:30", dt.time(10, 30), dt.time(10, 59, 59), "37 2 * * 1-5"),
    ScanSlot("11:00", dt.time(11, 0), dt.time(11, 29, 59), "7 3 * * 1-5"),
    ScanSlot("11:30", dt.time(11, 30), dt.time(11, 59, 59), "37 3 * * 1-5"),
    ScanSlot("12:00", dt.time(12, 0), dt.time(12, 29, 59), "7 4 * * 1-5"),
    ScanSlot("12:30", dt.time(12, 30), dt.time(12, 59, 59), "37 4 * * 1-5"),
    ScanSlot("13:00", dt.time(13, 0), dt.time(13, 29, 59), "7 5 * * 1-5"),
    ScanSlot("13:30", dt.time(13, 30), dt.time(13, 59, 59), "37 5 * * 1-5"),
)


def get_taipei_now():
    return dt.datetime.now(TAIPEI_TZ)


def find_intraday_slot(now, slots=INTRADAY_SLOTS):
    if now.tzinfo is None:
        now = now.replace(tzinfo=TAIPEI_TZ)
    else:
        now = now.astimezone(TAIPEI_TZ)
    if now.weekday() >= 5:
        return None
    for slot in slots:
        if slot.start <= now.time().replace(tzinfo=None) <= slot.end:
            return slot
    return None


def find_scheduled_slot(cron, slots=INTRADAY_SLOTS):
    normalized = " ".join(str(cron or "").split())
    for slot in slots:
        if slot.cron == normalized:
            return slot
    return None


def _slot_from_notes(notes):
    if not notes:
        return ""
    try:
        payload = json.loads(notes)
        if isinstance(payload, dict):
            return str(payload.get("automation_slot", ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    prefix = "automation_slot="
    for part in str(notes).split(";"):
        part = part.strip()
        if part.startswith(prefix):
            return part[len(prefix) :].strip()
    return ""


def slot_already_completed(db_path, trade_date, slot):
    db_path = Path(db_path)
    if not db_path.exists():
        return False

    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='scan_runs'"
        ).fetchone()
        if not table:
            return False
        rows = conn.execute(
            """
            SELECT run_at, notes
            FROM scan_runs
            WHERE trade_date = ? AND mode = 'intraday'
            ORDER BY id
            """,
            (str(trade_date),),
        ).fetchall()
    finally:
        conn.close()

    for row in rows:
        if _slot_from_notes(row["notes"]) == slot.name:
            return True
        try:
            run_at = dt.datetime.fromisoformat(row["run_at"])
            if run_at.tzinfo is None:
                run_at = run_at.replace(tzinfo=TAIPEI_TZ)
            run_time = run_at.astimezone(TAIPEI_TZ).time().replace(tzinfo=None)
            if slot.start <= run_time <= slot.end:
                return True
        except (TypeError, ValueError):
            continue
    return False


def evaluate_intraday_run(
    now=None,
    db_path="data/stock_scanner.db",
    ignore_existing=False,
    scheduled_cron=None,
):
    now = now or get_taipei_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=TAIPEI_TZ)
    else:
        now = now.astimezone(TAIPEI_TZ)

    if now.weekday() >= 5:
        slot = None
    elif scheduled_cron:
        slot = find_scheduled_slot(scheduled_cron)
        if slot is None:
            return {
                "run": False,
                "slot": "",
                "reason": "unknown_scheduled_cron",
                "now": now.isoformat(timespec="seconds"),
            }
    else:
        slot = find_intraday_slot(now)
    if slot is None:
        return {
            "run": False,
            "slot": "",
            "reason": "outside_intraday_slots",
            "now": now.isoformat(timespec="seconds"),
        }
    if not ignore_existing and slot_already_completed(db_path, now.date().isoformat(), slot):
        return {
            "run": False,
            "slot": slot.name,
            "reason": "slot_already_completed",
            "now": now.isoformat(timespec="seconds"),
        }
    return {
        "run": True,
        "slot": slot.name,
        "reason": "slot_ready",
        "now": now.isoformat(timespec="seconds"),
    }


def write_github_output(path, decision):
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as output:
        for key in ("run", "slot", "reason", "now"):
            value = decision[key]
            if isinstance(value, bool):
                value = str(value).lower()
            output.write(f"{key}={value}\n")


def main():
    parser = argparse.ArgumentParser(description="Decide whether an intraday automation slot should run.")
    parser.add_argument("--db-path", default="data/stock_scanner.db")
    parser.add_argument("--github-output")
    parser.add_argument("--ignore-existing", action="store_true")
    parser.add_argument("--scheduled-cron")
    parser.add_argument("--now", help="ISO timestamp used for deterministic checks")
    args = parser.parse_args()

    now = dt.datetime.fromisoformat(args.now) if args.now else None
    decision = evaluate_intraday_run(
        now=now,
        db_path=args.db_path,
        ignore_existing=args.ignore_existing,
        scheduled_cron=args.scheduled_cron,
    )
    write_github_output(args.github_output, decision)
    print(json.dumps(decision, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
