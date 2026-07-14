# Global Market Intelligence

The global-market layer records cross-asset context as point-in-time research data. It is a shadow input and does not change formal stock rankings until its incremental predictive value is validated out of sample.

## Data flow

1. `global_market.py` downloads daily and 15-minute fallback bars in two batches.
2. Each observation is normalized with market time, collection time, session state, latency, source tier, price change, five-day return, and a 60-session shock score.
3. SQLite stores instruments, observations, and regime snapshots. Supabase receives the same records when its service-role environment variables and tables are available.
4. `export_dashboard_snapshot.py` publishes the latest matrix and up to 192 regime observations to the Next.js dashboard.
5. Intraday scans refresh the context every 30 minutes during the Taiwan session. The lightweight global workflow refreshes it hourly on weekdays outside the Taiwan scan cycle as well.

## Current sources and limitations

- Yahoo Finance is the first-stage delayed fallback for US futures, US equities and indices, Asian markets, FX, and commodities.
- Taiwan futures night-session data is deliberately marked `not_connected`. A licensed feed must replace this gap; no unrelated proxy is presented as the Taiwan futures contract.
- TWSE and TAIFEX official open data remain appropriate for closing and historical validation, but public OpenAPI endpoints are not treated as licensed real-time feeds.
- The dashboard displays source tier and freshness independently. A fresh collection timestamp does not turn a delayed quote into real-time data.

## Regime model

`global_regime_shadow_v1` groups the available shocks into US risk, Asian technology, macro, and commodity components. Directional weights encode an explicit prior about their relationship to Taiwan risk appetite. Missing instruments are excluded from the denominator, coverage is reported, and the resulting score is explanatory context only.

Promotion into the selection model requires:

- stable collection coverage across sessions;
- no look-ahead timestamps;
- a walk-forward comparison with the current policy;
- measurable improvement in excess return or drawdown without unstable turnover;
- a documented rollback threshold.

## Operations

Run locally:

```bash
python global_market.py
python export_dashboard_snapshot.py
```

For optional Supabase persistence, run the market-table section of `supabase_schema.sql`, then provide `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` as GitHub Actions secrets. The browser never receives the service-role key.
