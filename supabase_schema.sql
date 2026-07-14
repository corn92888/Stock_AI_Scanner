-- Supabase SQL Editor: run this once before enabling cloud portfolio storage.
-- The app stores a hashed owner_id instead of the raw email / portfolio key.

CREATE TABLE IF NOT EXISTS public.portfolio_owners (
    owner_id text PRIMARY KEY,
    owner_label text,
    owner_type text,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.portfolio_holdings (
    owner_id text NOT NULL,
    code text NOT NULL,
    stock_name text,
    cost numeric(14, 3) NOT NULL,
    shares numeric(18, 3) NOT NULL,
    stop_price numeric(14, 3),
    target_price numeric(14, 3),
    note text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, code)
);

CREATE TABLE IF NOT EXISTS public.portfolio_snapshots (
    owner_id text NOT NULL,
    trade_date date NOT NULL,
    snapshot_at timestamptz NOT NULL DEFAULT now(),
    code text NOT NULL,
    stock_name text,
    shares numeric(18, 3) NOT NULL,
    cost numeric(14, 3) NOT NULL,
    price numeric(14, 3),
    market_value numeric(18, 3),
    unrealized_pnl numeric(18, 3),
    pnl_pct numeric(10, 4),
    today_pct numeric(10, 4),
    decision_score integer,
    holding_status text,
    action text,
    strategy_status text,
    market_report text,
    scan_report text,
    PRIMARY KEY (owner_id, trade_date, code)
);

CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_owner_date
    ON public.portfolio_snapshots (owner_id, trade_date DESC);

-- Optional cross-market time-series store. SQLite remains the dashboard fallback;
-- the service-role key is required for writes and is never exposed to browsers.
CREATE TABLE IF NOT EXISTS public.market_instruments (
    instrument_key text PRIMARY KEY,
    symbol text,
    display_name text NOT NULL,
    group_name text NOT NULL,
    region text NOT NULL,
    asset_class text NOT NULL,
    currency text,
    source_name text NOT NULL,
    source_tier text NOT NULL,
    impact_direction numeric NOT NULL DEFAULT 0,
    model_weight numeric NOT NULL DEFAULT 0,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.market_observations (
    snapshot_at timestamptz NOT NULL,
    instrument_key text NOT NULL REFERENCES public.market_instruments(instrument_key),
    market_at timestamptz,
    price numeric,
    previous_close numeric,
    pct_change numeric,
    return_5d numeric,
    shock_z numeric,
    volume numeric,
    source_name text NOT NULL,
    source_tier text NOT NULL,
    data_status text NOT NULL,
    session_status text NOT NULL,
    latency_minutes numeric,
    PRIMARY KEY (snapshot_at, instrument_key)
);

CREATE TABLE IF NOT EXISTS public.market_regime_snapshots (
    snapshot_at timestamptz PRIMARY KEY,
    score numeric NOT NULL,
    regime_label text NOT NULL,
    taiwan_bias_score numeric NOT NULL,
    taiwan_bias_label text NOT NULL,
    coverage_pct numeric NOT NULL,
    active_fresh_pct numeric NOT NULL,
    components jsonb NOT NULL DEFAULT '[]'::jsonb,
    drivers jsonb NOT NULL DEFAULT '[]'::jsonb,
    quality jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_market_observations_instrument_time
    ON public.market_observations (instrument_key, snapshot_at DESC);

ALTER TABLE public.market_instruments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_regime_snapshots ENABLE ROW LEVEL SECURITY;
