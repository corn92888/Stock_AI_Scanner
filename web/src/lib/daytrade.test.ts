import assert from "node:assert/strict";
import test from "node:test";

import {
  advanceDaytradeState,
  createDaytradeState,
  evaluateDaytradeSignal,
  type DaytradeQuote,
  type QuotePoint,
} from "./daytrade.ts";

const quote: DaytradeQuote = {
  symbol: "2330",
  name: "Test Stock",
  market: "tse",
  source: "fugle",
  at: "2026-08-10T02:00:00.000Z",
  last: 100.7,
  open: 100,
  high: 100.7,
  low: 99.8,
  previousClose: 98.7,
  averagePrice: 100.2,
  changePct: 2.03,
  bid: 100.65,
  ask: 100.7,
  tradeVolume: 103_000,
  tradeValue: 100_000_000,
  isRealtime: true,
};

const history: QuotePoint[] = [
  { at: "2026-08-10T01:58:00.000Z", price: 100, volume: 100_000 },
  { at: "2026-08-10T01:59:00.000Z", price: 100.2, volume: 101_000 },
  { at: "2026-08-10T01:59:45.000Z", price: 100.3, volume: 101_800 },
  { at: quote.at, price: quote.last, volume: quote.tradeVolume },
];

test("blocks entries until two minutes of realtime history exist", () => {
  const signal = evaluateDaytradeSignal(quote, history.slice(-2), { symbol: "2330", name: "Test Stock", score: 80 });
  assert.equal(signal.action, "WAIT");
  assert(signal.reasons.includes("分鐘樣本暖機中"));
});

test("accepts a liquid breakout with healthy momentum and volume acceleration", () => {
  const signal = evaluateDaytradeSignal(quote, history, { symbol: "2330", name: "Test Stock", score: 80 });
  assert.equal(signal.action, "BUY");
  assert.equal(signal.reasons.length, 0);
  assert(signal.score >= 50);
});

test("paper broker enters, charges costs, and exits at the fixed stop", () => {
  const running = { ...createDaytradeState("2026-08-10"), status: "running" as const };
  const entered = advanceDaytradeState(
    running,
    [quote],
    { "2330": history },
    [{ symbol: "2330", name: "Test Stock", score: 80 }],
  );
  assert.equal(entered.positions.length, 1);
  assert.equal(entered.fills[0].side, "BUY");
  assert(entered.cash < entered.startingCash);

  const stoppedQuote: DaytradeQuote = {
    ...quote,
    at: "2026-08-10T02:01:00.000Z",
    last: 99,
    bid: 98.95,
    ask: 99,
    changePct: 0.3,
  };
  const exited = advanceDaytradeState(
    entered,
    [stoppedQuote],
    { "2330": [...history, { at: stoppedQuote.at, price: stoppedQuote.last, volume: 104_000 }] },
    [{ symbol: "2330", name: "Test Stock", score: 80 }],
  );
  assert.equal(exited.positions.length, 0);
  assert.equal(exited.fills[0].side, "SELL");
  assert.equal(exited.fills[0].reason, "固定停損");
  assert((exited.fills[0].realizedPnl ?? 0) < 0);
});
