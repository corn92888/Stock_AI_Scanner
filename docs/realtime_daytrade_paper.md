# Realtime Day-Trading Paper Terminal

## Purpose

The `即時當沖` workspace is a simulation-only intraday execution terminal. It polls current quotes, evaluates a separate day-trading signal, records paper fills, marks open positions, and enforces intraday risk controls. It never sends orders to a broker.

## Market Data

The server uses providers in this order:

1. Fugle MarketData REST quote API when `FUGLE_MARKETDATA_API_KEY` is configured.
2. TWSE MIS web quotes as a best-effort paper-only fallback.
3. The latest scanner snapshot for display only. Snapshot quotes are never eligible for entry.

Add `FUGLE_MARKETDATA_API_KEY` to Vercel Project Settings > Environment Variables for Production, Preview, and Development, then redeploy. The key remains server-side and is never returned to the browser.

The first version polls at 10 seconds with Fugle or 5 seconds with the TWSE fallback and monitors at most five symbols. A persistent WebSocket worker is not hosted inside Vercel serverless functions; a later always-on version should run Fugle WebSocket or Shioaji on a persistent worker service.

## Paper Strategy

- Long-only entries from 09:15 through 12:45 Asia/Taipei.
- Two simultaneous positions and six entries per session at most.
- Candidate quality score at least 50.
- Current trade value at least TWD 50 million.
- Bid/ask spread no wider than 0.45%.
- Price above the intraday reference average by at least 0.1%.
- One-minute momentum between 0.25% and 1.8%.
- One-minute volume acceleration between 1.2x and 8x.
- A fresh short-horizon breakout and daily change between 0.3% and 6.5%.

The browser needs two minutes of quote history before a symbol can pass the signal gate.

## Risk And Fill Model

- Starting paper cash: TWD 1,000,000.
- Risk budget: 0.35% of current equity per entry.
- Maximum position value: 20% of current equity.
- Fixed stop: 1.0% below entry.
- Fixed target: 1.8% above entry.
- Trailing exit: activated after +1.2%, then 0.6% below the peak.
- Maximum holding time: 45 minutes.
- Forced flat time: 13:20.
- Daily loss circuit breaker: -1.5%.
- Simulated slippage: 5 bps per side.
- Commission: 0.1425% with a TWD 20 minimum.
- Day-trading sell tax: 0.15%.

State and fills are stored in browser local storage for the current trading date. The engine runs only while the workspace remains open. This is an execution and data-quality experiment, not evidence of profitability and not an instruction to place a real trade.
