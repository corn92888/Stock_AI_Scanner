-- Supabase SQL Editor: run this once before enabling cloud portfolio storage.
-- The app stores a hashed owner_id instead of the raw email / portfolio key.

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
