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

They never enter `scan_runs`, `candidate_events`, `predictions`, or
`paper_trades`. The current AI training loader therefore cannot consume replay
rows accidentally. A later model experiment may opt into them only after replay
quality and out-of-sample behavior are reviewed.

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

## Point-in-time universe snapshots

The preferred universe CSV is a sequence of complete snapshots. Every row needs:

```csv
snapshot_date,code,name,industry,market
2023-01-01,2330,台積電,半導體業,上市
2023-01-01,2454,聯發科,半導體業,上市
2023-02-01,2330,台積電,半導體業,上市
```

Each `snapshot_date` must describe the complete eligible universe from that date
until the next snapshot. A stock absent from a snapshot is not eligible on those
decision dates. Dates before the first snapshot intentionally have no eligible
stocks, so the loader never borrows a future membership list.

The legacy static `code,name,industry,market` format remains accepted for scoped
diagnostics, but the replay records a survivorship-bias warning. A credible
multi-year study should reconstruct monthly snapshots from official TWSE and TPEx
listing, transfer, and delisting records before interpreting results.

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
  --universe-file data/universe_snapshots.csv \
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

The default universe comes from the current `twstock` listing. Replaying older
dates with it has survivorship bias because delisted securities are absent and
later IPOs may be overrepresented in the universe definition. Use a versioned
CSV with `code,name,industry,market` columns when a historically accurate
universe becomes available.

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
  --universe-file data/universe_snapshots.csv --resume
```

The GitHub Actions workflow `Historical Point-in-Time Replay` provides the same
operation without requiring a local process. It restores the historical price
cache, resumes incomplete monthly checkpoints by default, generates attribution,
and persists failed-run checkpoints so a later dispatch can continue. It is
manual because a full-market multi-year replay is intentionally separate from
the latency-sensitive intraday workflow.

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
