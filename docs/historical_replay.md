# Historical Replay and Research Health

## Purpose

`historical_replay.py` reconstructs the existing EOD scanner on each historical
decision date. It creates research evidence without pretending those rows were
live recommendations and without changing the rule or AI paper portfolios.

Replay v2 answers four questions:

1. How often did the frozen technical strategies produce candidates?
2. Did the current ranking and tradability policy add value over rejected candidates?
3. What happened after a realistic next-session open entry with costs and risk rules?
4. Which strategy, score, liquidity, risk, market, and industry slices explain the result?

## Isolation contract

Historical rows are written only to:

- `historical_replay_runs`
- `historical_replay_events`
- `historical_replay_outcomes`
- `historical_replay_checkpoints`
- `historical_replay_attributions`
- `historical_replay_summaries`

They never enter `scan_runs`, `candidate_events`, `predictions`, or
`paper_trades`. AI training consumes replay rows only through the versioned
`replay_training_samples.csv.gz` export. Replay-informed models remain shadow
challengers and cannot affect the formal list unless every governance gate passes.

## Point-in-time contract

For decision date `D`:

- Technical indicators and strategy checks receive bars no later than `D`.
- Market breadth, industry heat, volume ratios, and previous close use bars before
  `D`, plus the completed EOD bar for `D` as the decision snapshot.
- Entry is the next available session open.
- The existing chase limit, gap-below-defense rejection, transaction costs,
  T+3 defense close, benchmark excess return, and drawdown rules are reused.
- Outcome bars after `D` are available only to the execution evaluator, never to
  signal generation or ranking.

## Official point-in-time universe

Build the historical TWSE and TPEx membership intervals from official company,
listing, transfer, and delisting feeds before a multi-year replay:

```bash
venv/bin/python3 historical_universe.py \
  --start 2021-01-01 --end 2025-12-31 \
  --output data/universe_history.csv
```

The compact interval format changes membership on the exact listing or delisting
date and avoids expanding thousands of stocks into duplicated monthly rows:

```csv
code,name,industry,market,listed_on,delisted_on,membership_quality
2330,台積電,半導體業,上市,1994-09-05,,exact
6446,藥華醫藥股份有限公司,未分類,上櫃,2016-07-19,2024-01-25,exact
6446,藥華藥,生技醫療,上市,2024-01-25,,exact
```

`listed_on` is inclusive and `delisted_on` is exclusive. The generated metadata
file records source URLs, retrieval time, coverage dates, a SHA-256 data hash,
market transfers, and the number of `partial_start` intervals. A hash mismatch
stops the replay instead of silently accepting modified membership data.

The snapshot format remains supported. Each `snapshot_date` must describe the
complete eligible universe from that date until the next snapshot. Dates before
the first snapshot intentionally have no eligible stocks, so the loader never
borrows a future membership list.

The legacy static `code,name,industry,market` format remains accepted for scoped
diagnostics, but the replay records a survivorship-bias warning. A missing
official listing date is never guessed: the builder truncates that interval to
the requested coverage start and marks the entire universe `partial`.

## Cache, checkpoints, and resume

Yahoo responses are normalized and cached per ticker under
`data/replay_cache/yahoo`. The directory is ignored by Git and persisted by the
GitHub Actions cache. Use `--refresh-cache` when the requested history must be
downloaded again.

Decision dates are committed in monthly checkpoints. A failed month is cleaned
before being retried, while completed months remain immutable. Resume the same
versioned replay without duplicating events:

```bash
venv/bin/python3 historical_replay.py \
  --start 2022-01-01 --end 2025-12-31 \
  --universe-file data/universe_history.csv \
  --resume
```

Use `--replace` only when intentionally rebuilding the entire replay after a
strategy, policy, universe, or execution assumption changes.

## Factor attribution

After a replay completes, generate the diagnostic matrix:

```bash
venv/bin/python3 replay_attribution.py
```

The attribution engine compares all, selected, and rejected candidates across:

- strategy and policy result;
- score, 5-day and 20-day volume ratio, turnover, and defense distance bands;
- market breadth and industry breadth known on the decision date;
- industry, calendar year, and calendar quarter.

Every slice includes mature sample counts, mean T+1/T+3/T+5 net returns, T+3
benchmark excess, positive and strategy-success rates, mean drawdown, standard
error, and a 95% confidence interval. The dashboard labels a direction as
confirmed only with at least 30 observations and a T+3 interval that does not
cross zero.

## Known limitations

The official builder removes the largest current-list survivorship error, but
some companies delisted after 2021 were listed before the official historical
listing feed begins. Those intervals are marked `partial_start`. Industry labels
for active companies are the latest official classification, not a complete
point-in-time classification history; delisted companies without a historical
classification are marked unclassified. Both limitations are carried into the
replay warnings and dashboard quality state.

Yahoo Finance history can include later corrections and adjustment metadata.
Version 2 also excludes historical point-in-time fundamentals and news. These
limitations are stored on every replay run and must be considered before using
the evidence for model training or promotion.

## Running a replay

```bash
venv/bin/python3 historical_replay.py \
  --start 2025-01-01 \
  --end 2025-12-31
```

Useful scoped runs:

```bash
venv/bin/python3 historical_replay.py \
  --start 2025-01-01 --end 2025-03-31 \
  --codes 2330,2454,2303

venv/bin/python3 historical_replay.py \
  --start 2025-01-01 --end 2025-12-31 \
  --universe-file data/universe_history.csv --resume
```

The GitHub Actions workflow `Historical Point-in-Time Replay` provides the same
operation without requiring a local process. When no custom universe file is
provided, the workflow rebuilds the official interval universe for the requested
dates. It restores the historical price cache, resumes incomplete monthly
checkpoints by default, generates attribution, and persists failed-run
checkpoints so a later dispatch can continue. It is manual because a full-market
multi-year replay is intentionally separate from the latency-sensitive intraday
workflow.

The long replay job operates on an isolated SQLite copy and uses a separate
concurrency group, so it cannot block the 30-minute live scan schedule. A short
final job enters the normal scanner concurrency group, downloads the replay
artifact, and runs `merge_historical_replay.py`. The live database receives only
the matching run summary, monthly checkpoints, factor attributions, and model
governance result; it never copies the large event and outcome tables or
overwrites newer live scans, predictions, or paper trades.

The complete raw SQLite database, official universe, and compact training export
are packaged into a checksum-bearing archive and published under the
`research-replay-data-v1` GitHub Release. This avoids GitHub's 100 MB per-file
limit while retaining event-level evidence for audit and future retraining. The
compact `data/replay_training_samples.csv.gz` file contains only point-in-time
model features and mature T+3 labels, so routine intraday and daily AI runs can
reuse the five-year evidence without downloading the raw database.

Replay-informed AI uses expanding walk-forward validation with a three-session
embargo. The validation window grows adaptively for multi-year samples, and the
challenger remains `shadow` unless it passes minimum trade count, positive net
and excess returns, drawdown, lift, and profitable-fold stability gates.

## Research health monitor

`research_monitor.py` deduplicates prospective predictions to the earliest row
for each `(run_id, code)` cohort. It counts distinct later scan trade dates, so a
prediction becomes expected to mature only after three later trading sessions.
Weekends and multiple intraday runs on the same day do not advance the clock.

Statuses:

- `healthy`: prospective outcomes are current and at least one replay completed.
- `building`: prospective cohorts or replay evidence are still accumulating.
- `warning`: the latest replay did not complete.
- `critical`: one or more prospective cohorts passed T+3 without a mature label.

Both daily and intraday automation run the monitor before exporting the public
dashboard snapshot.
