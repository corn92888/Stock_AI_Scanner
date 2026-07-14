# Research Execution v2

## Purpose

This milestone turns scanner output into an auditable research loop. It does not authorize live trading. Every strategy remains a research candidate until it passes the same after-cost evaluation gates and a human promotion review.

## Frozen Baseline

`next_day_open_defense_close_t3_v1` remains immutable as the legacy baseline. New outcomes use `mode_aligned_after_costs_t3_v2`, so changing execution assumptions never rewrites the historical comparison series.

## Execution Contract

### Intraday candidates

- Decision time: the stored candidate `as_of` timestamp.
- Entry: the stored signal snapshot price.
- Benchmark proxy: the previous session close, which is known at decision time; the signal-day close is never used because it would introduce look-ahead.
- Rejection: no fill when the signal price is above the stored chase limit.
- Defense: the first following session whose close is below the stored observation price.
- Time exit: the third following trading-session close.

### End-of-day candidates

- Decision time: after the signal session has closed.
- Entry: next trading-session open.
- Rejection: no fill above the chase limit or at/below the observation price.
- Defense: the first entered session or later whose close is below the observation price.
- Time exit: the third following trading-session close.

Both modes record skipped fills and their reason. They also preserve fixed T+1, T+3 and T+5 returns separately from the executable defense exit.

## Costs And Position Sizing

Candidate outcomes use the shared `BacktestConfig` transaction-cost assumptions. Paper accounts additionally size each order with both constraints:

- Position market value cannot exceed 20% of current sizing equity.
- Planned loss from entry to the recorded stop cannot exceed 1% of sizing equity.

The smaller allocation wins. Invalid stops at or above entry are rejected instead of creating a misleading position.

## Strategy Registry

`research_experiments` stores the hypothesis, family, execution version, objective, configuration and Git commit. `experiment_evaluations` stores immutable evaluation versions and the latest result is exported to the dashboard.

For the portfolio path, each signal-date cohort's T+3 return is divided across the three-session holding horizon before compounding. This approximates three overlapping, equally funded sleeves and avoids treating a three-day return as a one-day return.

The initial tournament contains:

- Legacy formal rule baseline.
- Mode-aligned formal rule baseline.
- Mode-aligned trend sleeve.
- Mode-aligned reversal sleeve.
- Mode-aligned consolidation sleeve.

## Promotion Gates

A strategy is not qualified unless all default gates pass:

- At least 120 independent trade dates.
- At least 300 filled trades.
- Positive mean T+3 excess return after costs.
- Probabilistic Sharpe of at least 95% against a zero-Sharpe benchmark.
- Portfolio maximum drawdown no worse than -12%.
- Positive mean excess return in at least 60% of chronological folds.

Passing these gates only changes the strategy to a promotion-review candidate. It does not replace the production ranking automatically.

## Daily Automation

The end-of-day worker now performs this order:

1. Run the scanner and persist raw stock signals.
2. Convert end-of-day scanner output into versioned candidate events.
3. Backfill mode-aligned candidate outcomes.
4. Train the shadow model and create prospective predictions for the new run.
5. Evaluate every registered strategy experiment.
6. Settle constrained rule and AI paper accounts.
7. Export the dashboard snapshot and persist generated data.

## Known Research Limits

- The v2 tournament only evaluates signals already emitted by the scanner. It does not yet search a broad parameter grid.
- Existing fundamental and news features are not treated as point-in-time safe until their publication or known-at timestamps are verified end to end.
- Daily bars cannot reproduce intraday spread or queue position. The signal snapshot fill is an explicit approximation and is versioned as such.
- The dashboard is an evidence and governance surface, not a buy instruction.
