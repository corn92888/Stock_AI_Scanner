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

-- Private, server-only evidence storage for the scanner's durable SQLite image.
-- The live object is overwritten after every successful pipeline; one daily
-- object is retained per trading date. PostgreSQL keeps the verified manifest
-- and append-only synchronization audit log.
INSERT INTO storage.buckets (id, name, public, file_size_limit)
VALUES ('scanner-evidence', 'scanner-evidence', false, 104857600)
ON CONFLICT (id) DO UPDATE SET
    public = EXCLUDED.public,
    file_size_limit = EXCLUDED.file_size_limit;

CREATE TABLE IF NOT EXISTS public.scanner_evidence_snapshots (
    snapshot_key text PRIMARY KEY,
    object_path text NOT NULL,
    snapshot_at timestamptz NOT NULL,
    source_workflow text NOT NULL,
    source_run_id text,
    source_commit text,
    schema_version text NOT NULL,
    database_sha256 text NOT NULL,
    compressed_sha256 text NOT NULL,
    database_bytes bigint NOT NULL,
    compressed_bytes bigint NOT NULL,
    latest_scan_run_id bigint,
    latest_trade_date date,
    latest_run_at timestamptz,
    sqlite_integrity text NOT NULL,
    table_counts jsonb NOT NULL DEFAULT '{}'::jsonb,
    dashboard_sha256 text,
    status text NOT NULL CHECK (status IN ('pending', 'verified', 'failed')),
    verified_at timestamptz,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS public.scanner_evidence_sync_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_at timestamptz NOT NULL DEFAULT now(),
    snapshot_key text NOT NULL,
    operation text NOT NULL CHECK (operation IN ('push', 'restore')),
    status text NOT NULL CHECK (status IN ('verified', 'failed', 'skipped')),
    source_workflow text,
    source_run_id text,
    source_commit text,
    database_sha256 text,
    database_bytes bigint,
    compressed_bytes bigint,
    latest_scan_run_id bigint,
    latest_trade_date date,
    details jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_scanner_evidence_snapshots_time
    ON public.scanner_evidence_snapshots (snapshot_at DESC);
CREATE INDEX IF NOT EXISTS idx_scanner_evidence_events_time
    ON public.scanner_evidence_sync_events (event_at DESC);

ALTER TABLE public.scanner_evidence_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scanner_evidence_sync_events ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.scanner_evidence_snapshots FROM anon, authenticated;
REVOKE ALL ON public.scanner_evidence_sync_events FROM anon, authenticated;
GRANT ALL ON public.scanner_evidence_snapshots TO service_role;
GRANT ALL ON public.scanner_evidence_sync_events TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.scanner_evidence_sync_events_id_seq
    TO service_role;
