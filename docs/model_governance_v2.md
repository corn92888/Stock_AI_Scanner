# AI Model Governance v2

## Purpose

The AI layer is a research challenger. It cannot replace the formal rule ranking
unless it demonstrates an economic advantage on data that was unavailable when
the model was trained. Classification accuracy alone is never a promotion gate.

## Point-in-time contract

- Every feature snapshot stores `decision_at`, `known_at`, lineage, and quality flags.
- Naive timestamps are interpreted as Asia/Taipei and normalized to UTC.
- Fundamentals are eligible only when their `known_at` timestamp is no later than
  the decision timestamp.
- News collected after a scan remains in `news_evidence`; it is not written back
  into that scan's model features.
- The model version includes a fingerprint of the exact training outcomes.

## Walk-forward validation

Training uses an expanding window. Each validation block contains five trading
dates and is separated from the training window by a three-date embargo, matching
the T+3 outcome horizon. Every out-of-fold prediction records its feature ID,
fold, last available training date, predicted values, selection result, and actual
outcome in `model_validation_predictions`.

The final model may be fitted on all matured observations only after all OOF
predictions and metrics are generated. This final fit is used for future shadow
predictions, never for the reported OOF score.

## Challenger comparison

The AI challenger and formal rule champion are compared on the same OOF candidate
rows and dates with the same after-cost T+3 outcomes. Promotion requires all of:

- at least 30 OOF trading dates;
- at least 60 AI-selected OOF trades;
- positive mean net and excess return;
- excess return above the rule champion;
- portfolio maximum drawdown no worse than -12%;
- positive excess return in at least 60% of folds.

Passing these gates changes the state only to `promotion_review`. Human review is
still required; no code path automatically changes the formal ranking policy.

## Prospective audit

A prediction is prospective only when first created between zero and 24 hours
after its source scan. Only the earliest prospective prediction across all model
versions for a `(run_id, code)` pair is eligible for the paper account. The unique
`(run_id, code, model_version)` row is immutable: later replays return the original
prediction instead of overwriting its timestamp, selection, or score.

## Current interpretation

The dashboard exposes OOF AUC and regression error for diagnosis, but the primary
decision is the same-window economic comparison. A model that loses less than the
rule champion while still producing negative net and excess return remains
`shadow`; relative improvement is not the same as a tradable edge.
