# Automated Learning Cycle

## Purpose

The learning cycle turns mature outcomes into auditable research decisions. It does
not mutate the champion strategy, deploy a model, or authorize live orders.

Each end-of-day run performs the following sequence:

1. Read the latest prospective Alpha governance snapshot and paper accounts.
2. Build a canonical 60-trading-date candidate cohort.
3. Exclude implausible T+3 labels before they can affect attribution or hypotheses.
4. Compare selected candidates with the same-window rejected control group.
5. Attribute negative outcomes across strategy, volume, liquidity, extension,
   intraday position, defense distance, market regime, industry breadth, and
   industry.
6. Record missed positive-excess candidates for opportunity-cost analysis.
7. Register a limited set of evidence-backed challenger hypotheses.
8. Persist the cycle, attribution rows, hypotheses, and Markdown report.

The daily workflow runs `learning_cycle.py` after prospective Alpha governance and
before capital governance. The dashboard therefore shows the same diagnosis that
the capital ladder receives during that end-of-day run.

## Durable evidence

- `research_cycles`: one idempotent record per trade date and cycle version.
- `research_failure_attributions`: immutable per-cycle factor slices with sample
  size, after-cost return, excess return, drawdown, confidence interval, and loss
  contribution.
- `learning_hypotheses`: deduplicated proposals with priority, occurrences,
  configuration, and supporting evidence.
- `Reports/research_cycles/research_cycle_YYYY-MM-DD.md`: human-readable journal
  uploaded with the daily workflow artifact.

Repeated execution on the same date replaces that cycle's calculated evidence and
does not inflate hypothesis occurrence counts.

## Governance contract

Automated hypotheses remain `proposed`. A separate implementation must explicitly
approve and convert a proposal into a version-controlled challenger experiment.
That challenger must still pass purged walk-forward validation, reserved holdout,
and a new prospective paper cohort. No path in this cycle updates formal ranking,
capital weights, or broker transmission.

## Run locally

```bash
venv/bin/python learning_cycle.py \
  --db data/stock_scanner.db \
  --report-dir Reports/research_cycles
```
