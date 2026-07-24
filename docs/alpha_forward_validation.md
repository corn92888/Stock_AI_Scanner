# Alpha Forward Validation Runbook

## Purpose

This layer answers one operational question: does Alpha v2 retain a positive,
repeatable edge after it begins making frozen, point-in-time decisions?

Historical holdout results are development evidence. They do not count toward the
forward promotion sample and cannot authorize real-money execution.

## Daily sequence

1. The EOD scanner builds and scores the liquid Taiwan equity universe.
2. `alpha_live.py` stores the complete scored pool and the governed Top 3 decision.
3. `paper_trading.py` rebuilds the champion and four control accounts with identical
   capital and execution rules.
4. `research_monitor.py` verifies outcome maturity.
5. `alpha_forward_monitor.py` evaluates data integrity, evidence gates, account
   performance, and the governance state.
6. `export_dashboard_snapshot.py` publishes the same evidence to the dashboard.

Intraday automation repeats steps 4 through 6 so settlement, quote health, and the
dashboard remain current. It does not create a new EOD Alpha cohort.

## Controls

| Account | Frozen policy |
| --- | --- |
| Alpha v2 forward champion | Highest predicted T+10 excess return |
| Strict anti-chase | Champion ranking with tighter return, volume, MA distance, and gap filters |
| Market regime | Champion ranking only when 20-day market return, MA200 participation, and breadth are supportive |
| Momentum baseline | Highest 20-day industry-relative return |
| Random baseline | Deterministic hash of signal date and stock code |

All accounts select at most three industries per day, allocate 8% per position,
enter at the next session open, and exit at the T+10 close after fees, tax, and
slippage.

## Interpretation

`COLLECTING` is the expected state during the first several months. A positive
short-term return does not accelerate promotion, and a few early losing trades do
not invalidate the model.

`WATCH` means the sample is informative enough to review, but the economic or
stability evidence is incomplete. The system continues paper trading.

`PAUSED` is an operational stop. New paper positions are suppressed until the data
integrity issue clears, or indefinitely when the -12% drawdown stop remains active.
Existing paper positions continue to settle according to their frozen rules.

`HEALTHY` means the quantitative gates passed. It is still not an automatic
real-money approval.

## Recovery checks

When `PAUSED`, inspect these dashboard fields first:

- latest Alpha signal status;
- same-day candidate-pool row count;
- intraday quote coverage;
- drawdown versus the -12% stop;
- governance reason codes and warnings.

Supabase DNS or credential failures should show `DEGRADED`, not `PAUSED`, while the
Git database fallback is active. Repair the external service separately and retain
the local evidence chain.
