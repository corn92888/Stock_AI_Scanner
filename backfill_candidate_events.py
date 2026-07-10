import argparse
import re
from pathlib import Path

from database import DB_PATH, find_scan_run
from intraday_analysis_report import generate_intraday_analysis_report


TIMESTAMP_PATTERN = re.compile(r"盤中日報_(\d{4}-\d{2}-\d{2})_(\d{4})\.xlsx$")


def discover_report_pairs(report_dir="Reports", start_date=None, end_date=None):
    report_dir = Path(report_dir)
    pairs = []
    for scan_path in report_dir.glob("盤中日報_*.xlsx"):
        match = TIMESTAMP_PATTERN.search(scan_path.name)
        if not match:
            continue
        trade_date, clock = match.groups()
        if start_date and trade_date < start_date:
            continue
        if end_date and trade_date > end_date:
            continue
        market_path = report_dir / f"市場監控_{trade_date}_{clock}.xlsx"
        if market_path.exists():
            pairs.append((trade_date, clock, scan_path, market_path))
    return sorted(pairs, key=lambda item: (item[0], item[1]))


def backfill_candidate_events(
    report_dir="Reports",
    start_date=None,
    end_date=None,
    limit=None,
    db_path=DB_PATH,
):
    pairs = discover_report_pairs(report_dir, start_date=start_date, end_date=end_date)
    if limit is not None:
        pairs = pairs[: max(0, int(limit))]

    summary = {"pairs": len(pairs), "processed": 0, "skipped": 0, "events": 0, "selected": 0}
    for trade_date, clock, scan_path, market_path in pairs:
        if not find_scan_run(scan_path, db_path=db_path):
            summary["skipped"] += 1
            print(f"SKIP {trade_date} {clock}: database scan run not found")
            continue

        result = generate_intraday_analysis_report(
            scan_path=scan_path,
            market_path=market_path,
            save_report=False,
            send_telegram=False,
            db_path=db_path,
        )
        summary["processed"] += 1
        summary["events"] += result["candidate_events_saved"]
        summary["selected"] += result["selected_count"]
        print(
            f"OK   {trade_date} {clock}: run={result['scan_run_id']} "
            f"events={result['candidate_events_saved']} selected={result['selected_count']}"
        )
    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Backfill canonical candidate events from matching intraday reports."
    )
    parser.add_argument("--report-dir", default="Reports")
    parser.add_argument("--start-date", help="YYYY-MM-DD")
    parser.add_argument("--end-date", help="YYYY-MM-DD")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--db-path", default=str(DB_PATH))
    args = parser.parse_args()

    summary = backfill_candidate_events(
        report_dir=args.report_dir,
        start_date=args.start_date,
        end_date=args.end_date,
        limit=args.limit,
        db_path=args.db_path,
    )
    print(
        "Backfill complete: "
        f"pairs={summary['pairs']} processed={summary['processed']} "
        f"skipped={summary['skipped']} events={summary['events']} "
        f"selected={summary['selected']}"
    )


if __name__ == "__main__":
    main()
