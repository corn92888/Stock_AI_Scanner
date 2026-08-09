# Point-in-Time Fundamentals and Governed Challengers

## Official evidence capture

`fundamental_ingestion.py` captures the latest official TWSE and TPEx valuation,
monthly revenue, and quarterly EPS datasets. Each normalized observation stores:

- the market, valuation date, revenue period, and EPS period;
- the official report publication timestamp and the scanner's first `known_at`;
- source URL, payload SHA-256, raw component records, and quality flags;
- PE, PB, revenue year-over-year, revenue month-over-month, and latest reported EPS.

Latest quarterly EPS is deliberately stored as `eps_latest`, not `eps_ttm`.
Treating a cumulative or single-quarter official value as trailing-twelve-month EPS
would create a false feature definition. A true TTM value may only be populated
after four compatible point-in-time quarters have been archived.

Repeated capture of the same official release preserves the earliest `known_at`.
This prevents a retry from rewriting when the system first knew the observation.
Feature generation accepts a fundamental row only when `known_at <= decision_at`.

## Prospective accumulation

The official APIs expose current releases, not a survivorship-safe historical
archive. The system therefore starts a clean prospective archive instead of
backfilling old candidate dates with today's fundamentals. This is slower but is
the only defensible default without dated source files.

The daily workflow captures official data before the EOD scan. Intraday and future
EOD decisions can use the most recent previously known observation. Failed or
partial endpoints remain visible in `fundamental_ingestion_runs` and on the
dashboard.

## Versioned challenger lifecycle

`challenger_factory.py` converts learning hypotheses into fingerprinted experiment
versions. Approvals live in `config/shadow_challenger_approvals.json` so the exact
scope, owner, date, implementation, and evidence gates are reviewable in Git.

The first approved experiment, `point_in_time_fundamentals_v1`, remains
`collecting_data` until all of the following are available:

- 300 mature T+3 candidate outcomes;
- 30 independent trade dates;
- 60% complete point-in-time coverage. A row is complete only when it has at
  least one valuation field, one monthly-revenue growth field, and an EPS field.

Once ready, the experiment compares the existing feature model and the augmented
fundamental model on the same purged expanding walk-forward folds. The augmented
model must pass all existing economic challenger gates and deliver positive
incremental out-of-fold excess return over the matched baseline.

`promotion_review` is still shadow-only. Neither approval nor evaluation can alter
formal selection, live-capital policy, or broker transmission.
