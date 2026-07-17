# Prospective Capital Tournament

## Purpose

The capital tournament tests whether portfolio construction can convert the AI
ranker's weak cross-sectional signal into a tradable result. It does not retrain
the model and does not change formal stock selection.

Version `prospective_capital_tournament_v1` starts on `2026-07-20`. Predictions,
signals, or outcomes before that date are excluded. Historical validation cannot
be used to choose the winning account.

## Shared evidence cohort

All tournament accounts consume the first immutable prospective prediction from
the first EOD run for each stock and trade date. Intraday predictions are excluded
so every account receives one comparable next-open cohort per day.

The shared eligibility gate is frozen at:

- tradable under the recorded risk policy;
- `probability_t3 >= 0.35`;
- `expected_excess_return_3d >= 0`;
- `expected_max_drawdown_3d >= -4%`.

Orders use the recorded next-open execution result, chase limit, defense price,
fees, tax, and slippage. A missing next-open result remains pending rather than
being filled from later knowledge.

## Accounts

| Account | Daily cohort | Weighting | Open positions | Industry controls |
| --- | --- | --- | ---: | --- |
| `ai_top3_equal_v1` | Top 3 | Equal, 20% target per stock | 5 | One stock per industry per cohort; 40% open exposure cap |
| `ai_top5_diversified_v1` | Top 5 | Equal, 10% target per stock | 10 | One stock per industry per cohort; 20% open exposure cap |
| `ai_top10_weighted_v1` | Top 10 | Score-proportional, 50% daily budget, 7.5% stock cap | 20 | Two stocks per industry per cohort; 15% open exposure cap |

Every account starts with TWD 1,000,000, reserves 5% cash, limits risk at 1% of
equity per position, and rejects orders below the minimum trade value. Position
targets are ceilings: stop-distance risk sizing, available cash, and industry
exposure can reduce the actual quantity.

## Promotion governance

Top 3 is the frozen tournament benchmark. A challenger can only enter manual
review after all of these prospective gates pass:

1. At least 120 new EOD decision dates.
2. At least 100 closed simulated trades in that account.
3. Positive after-cost account return.
4. Positive mean benchmark excess return among closed trades.
5. Higher account return than Top 3 over the common evidence period.
6. Better return-to-drawdown efficiency than Top 3.
7. Maximum drawdown no worse than -12%.

Passing the gates never promotes an account automatically. The dashboard reports
`manual_review_required`; a human must inspect execution quality, missing-data
rates, concentration, and regime dependence before any policy change.

## Operations

`python paper_trading.py` updates the legacy paper accounts and all three
tournament accounts during the daily EOD workflow. `export_dashboard_snapshot.py`
publishes the evidence days, account metrics, provisional leader, relative lifts,
and explicit rejection reasons to the Capital Tournament section of the
dashboard.
