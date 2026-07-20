import argparse
import json

from candidate_backtest import run_candidate_backtest
from database import DB_PATH
from prediction_outcomes import update_prediction_outcomes
from research_monitor import build_research_health


def drain_overdue_outcomes(
    db_path=DB_PATH,
    batch_size=500,
    max_batches=4,
    backtest_runner=run_candidate_backtest,
    sync_runner=update_prediction_outcomes,
    health_builder=build_research_health,
):
    health = health_builder(db_path)
    initial_stale = int(health.get("stale_outcomes") or 0)
    batches = []
    status = "completed" if initial_stale == 0 else "running"
    stop_reason = "already_current" if initial_stale == 0 else ""

    if initial_stale == 0:
        return {
            "status": status,
            "stop_reason": stop_reason,
            "initial_stale": 0,
            "final_stale": 0,
            "resolved": 0,
            "maturity_coverage_pct": float(
                health.get("maturity_coverage_pct") or 0.0
            ),
            "batches": batches,
        }

    for batch_number in range(1, int(max_batches) + 1):
        stale_before = int(health.get("stale_outcomes") or 0)
        if stale_before == 0:
            status = "completed"
            stop_reason = "queue_drained"
            break

        backtest = backtest_runner(
            db_path=db_path,
            limit=max(1, min(int(batch_size), stale_before)),
        )
        synchronized = int(sync_runner(db_path=db_path) or 0)
        health = health_builder(db_path)
        stale_after = int(health.get("stale_outcomes") or 0)
        batches.append(
            {
                "batch": batch_number,
                "stale_before": stale_before,
                "stale_after": stale_after,
                "synchronized_predictions": synchronized,
                "backtest": backtest,
            }
        )

        if stale_after == 0:
            status = "completed"
            stop_reason = "queue_drained"
            break
        if stale_after >= stale_before:
            status = "stalled"
            stop_reason = "no_maturity_progress"
            break
    else:
        status = "max_batches_reached"
        stop_reason = "batch_budget_exhausted"

    final_stale = int(health.get("stale_outcomes") or 0)
    return {
        "status": status,
        "stop_reason": stop_reason,
        "initial_stale": initial_stale,
        "final_stale": final_stale,
        "resolved": initial_stale - final_stale,
        "maturity_coverage_pct": float(
            health.get("maturity_coverage_pct") or 0.0
        ),
        "batches": batches,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Drain overdue prospective outcome cohorts in retryable batches."
    )
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--max-batches", type=int, default=4)
    parser.add_argument("--fail-on-stale", action="store_true")
    args = parser.parse_args()
    result = drain_overdue_outcomes(
        db_path=args.db,
        batch_size=args.batch_size,
        max_batches=args.max_batches,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.fail_on_stale and result["final_stale"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
