# Staged Live-Capital Governance

`capital_governance.py` converts prospective Alpha evidence into a versioned
capital stage and an auditable next-session order preview. It does not connect
to a broker and cannot transmit a live order.

## Capital ladder

| Stage | Minimum prospective evidence | Drawdown floor | Strategy cap | Position cap | Max positions |
| --- | --- | ---: | ---: | ---: | ---: |
| `SHADOW` | none | -12% hard stop | 0% | 0% | 0 |
| `MICRO` | 20 decision days, 30 closed trades, positive total and average excess return | -4% | 2% | 0.5% | 4 |
| `LIMITED` | 60 decision days, 75 closed trades, two evaluable months, at least 50% profitable months, PSR 0.80 | -6% | 10% | 2% | 6 |
| `PRODUCTION` | 120 decision days, 150 closed trades, three evaluable months, at least 60% profitable months, PSR 0.95 | -12% | 24% | 6% | 12 |

Promotion also requires:

- the latest Alpha run to be `active` or `abstained`;
- no critical Alpha forward-governance state;
- at least 95% latest quote coverage when coverage is available;
- no stale prospective outcomes;
- a complete same-day candidate pool when a pool is expected.

The ladder may automatically downgrade or pause. It never enables broker
transmission.

## Pre-trade policy

The latest model-selected Alpha signals are checked before an intent can become
`manual_approval_required`:

- the capital stage must be `MICRO`, `LIMITED`, or `PRODUCTION`;
- the 20-session market return must be positive;
- the market must be above its 200-session average;
- the market up ratio must be at least 45%;
- 20-session average turnover must be at least NT$1 billion;
- predicted Alpha and the signal price must be positive.

An eligible intent is capped by the current stage's per-position and total
strategy limits. `suggested_quantity` uses the configured reference capital and
the recorded signal price. It is a sizing preview, not a limit order or a
guaranteed fill.

## Safety properties

- `live_transmission_enabled` is always `false`.
- Every eligible intent remains `pending_manual`.
- No API key, broker session, or order-transmission function exists in this
  module.
- Position-ledger connectivity is reported as unavailable until a broker or a
  reconciled external ledger is implemented.
- Every evaluation and intent is persisted in `live_capital_snapshots` and
  `live_order_intents`.

## Automation

Daily and intraday workflows run:

```text
alpha_forward_monitor.py
capital_governance.py
export_dashboard_snapshot.py
```

The optional GitHub Actions variable `LIVE_CAPITAL_REFERENCE` controls the
reference capital used by order previews. It defaults to NT$1,000,000.

Run locally:

```bash
python capital_governance.py \
  --db data/stock_scanner.db \
  --reference-capital 1000000
```

The dashboard shows the current stage, next-stage evidence requirements,
capital limits, market-gate result, and every blocked or approval-required
intent.
