export const DAYTRADE_STATE_VERSION = "paper_daytrade_v1";
export const DAYTRADE_STARTING_CASH = 1_000_000;
export const DAYTRADE_COMMISSION_RATE = 0.001425;
export const DAYTRADE_TAX_RATE = 0.0015;
export const DAYTRADE_SLIPPAGE_RATE = 0.0005;

export type DaytradeQuote = {
  symbol: string;
  name: string;
  market: string;
  source: "fugle" | "twse_mis" | "scanner_snapshot";
  at: string;
  last: number;
  open: number | null;
  high: number | null;
  low: number | null;
  previousClose: number | null;
  averagePrice: number | null;
  changePct: number | null;
  bid: number | null;
  ask: number | null;
  tradeVolume: number | null;
  tradeValue: number | null;
  isRealtime: boolean;
};

export type QuotePoint = {
  at: string;
  price: number;
  volume: number | null;
};

export type DaytradeCandidate = {
  symbol: string;
  name: string;
  score: number;
};

export type DaytradeSignal = {
  symbol: string;
  action: "BUY" | "WAIT" | "BLOCKED";
  score: number;
  momentum1mPct: number | null;
  volumeAcceleration: number | null;
  distanceFromAveragePct: number | null;
  spreadPct: number | null;
  reasons: string[];
};

export type DaytradePosition = {
  symbol: string;
  name: string;
  quantity: number;
  entryAt: string;
  entryPrice: number;
  entryFee: number;
  stopPrice: number;
  targetPrice: number;
  peakPrice: number;
  signalScore: number;
};

export type DaytradeFill = {
  id: string;
  symbol: string;
  name: string;
  side: "BUY" | "SELL";
  quantity: number;
  price: number;
  filledAt: string;
  fee: number;
  tax: number;
  reason: string;
  realizedPnl: number | null;
};

export type DaytradeJournalEntry = {
  id: string;
  symbol: string;
  name: string;
  quantity: number;
  entryAt: string;
  entryPrice: number;
  entryFee: number;
  exitAt: string | null;
  exitPrice: number | null;
  exitFee: number;
  tax: number;
  realizedPnl: number | null;
  exitReason: string | null;
  holdingSeconds: number | null;
  status: "open" | "closed";
};

export type DaytradeLog = {
  at: string;
  level: "info" | "trade" | "risk";
  message: string;
};

export type DaytradePaperState = {
  version: string;
  sessionDate: string;
  status: "paused" | "running" | "halted";
  startingCash: number;
  cash: number;
  realizedPnl: number;
  positions: DaytradePosition[];
  fills: DaytradeFill[];
  cooldownUntil: Record<string, string>;
  lastProcessedAt: Record<string, string>;
  logs: DaytradeLog[];
  updatedAt: string;
};

const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));

export function taipeiSessionDate(value = new Date()) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(value);
}

function taipeiMinutes(iso: string) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Taipei",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(iso));
  const read = (type: Intl.DateTimeFormatPartTypes) => Number(parts.find((part) => part.type === type)?.value ?? 0);
  return read("hour") * 60 + read("minute");
}

export function createDaytradeState(sessionDate = taipeiSessionDate(), initializedAt = new Date().toISOString()): DaytradePaperState {
  const now = initializedAt;
  return {
    version: DAYTRADE_STATE_VERSION,
    sessionDate,
    status: "paused",
    startingCash: DAYTRADE_STARTING_CASH,
    cash: DAYTRADE_STARTING_CASH,
    realizedPnl: 0,
    positions: [],
    fills: [],
    cooldownUntil: {},
    lastProcessedAt: {},
    logs: [{ at: now, level: "info", message: "模擬帳戶已建立，等待啟動。" }],
    updatedAt: now,
  };
}

function commission(notional: number) {
  return Math.max(20, notional * DAYTRADE_COMMISSION_RATE);
}

function quoteMark(quote: DaytradeQuote | undefined) {
  if (!quote) return 0;
  return quote.bid && quote.bid > 0 ? quote.bid : quote.last;
}

export function daytradeEquity(state: DaytradePaperState, quotes: DaytradeQuote[]) {
  const bySymbol = new Map(quotes.map((quote) => [quote.symbol, quote]));
  return state.cash + state.positions.reduce((total, position) => {
    const mark = quoteMark(bySymbol.get(position.symbol)) || position.entryPrice;
    return total + mark * position.quantity;
  }, 0);
}

export function daytradeUnrealizedPnl(position: DaytradePosition, quote?: DaytradeQuote) {
  const mark = quoteMark(quote) || position.entryPrice;
  const exitNotional = mark * position.quantity;
  const exitCosts = commission(exitNotional) + exitNotional * DAYTRADE_TAX_RATE;
  return exitNotional - exitCosts - position.entryPrice * position.quantity - position.entryFee;
}

export function buildDaytradeJournal(fills: DaytradeFill[]): DaytradeJournalEntry[] {
  const chronological = [...fills].sort((left, right) => Date.parse(left.filledAt) - Date.parse(right.filledAt));
  const entries: DaytradeJournalEntry[] = [];
  const openEntries = new Map<string, number[]>();

  for (const fill of chronological) {
    if (fill.side === "BUY") {
      const index = entries.length;
      entries.push({
        id: fill.id,
        symbol: fill.symbol,
        name: fill.name,
        quantity: fill.quantity,
        entryAt: fill.filledAt,
        entryPrice: fill.price,
        entryFee: fill.fee,
        exitAt: null,
        exitPrice: null,
        exitFee: 0,
        tax: 0,
        realizedPnl: null,
        exitReason: null,
        holdingSeconds: null,
        status: "open",
      });
      openEntries.set(fill.symbol, [...(openEntries.get(fill.symbol) ?? []), index]);
      continue;
    }

    const queue = openEntries.get(fill.symbol) ?? [];
    const index = queue.shift();
    if (index == null) continue;
    openEntries.set(fill.symbol, queue);
    const entry = entries[index];
    entry.exitAt = fill.filledAt;
    entry.exitPrice = fill.price;
    entry.exitFee = fill.fee;
    entry.tax = fill.tax;
    entry.realizedPnl = fill.realizedPnl;
    entry.exitReason = fill.reason;
    entry.holdingSeconds = Math.max(0, Math.round((Date.parse(fill.filledAt) - Date.parse(entry.entryAt)) / 1000));
    entry.status = "closed";
  }

  return entries.reverse();
}

function pointBefore(points: QuotePoint[], targetMs: number) {
  for (let index = points.length - 1; index >= 0; index -= 1) {
    if (Date.parse(points[index].at) <= targetMs) return points[index];
  }
  return undefined;
}

export function evaluateDaytradeSignal(
  quote: DaytradeQuote,
  points: QuotePoint[],
  candidate?: DaytradeCandidate,
): DaytradeSignal {
  const nowMs = Date.parse(quote.at);
  const oneMinute = pointBefore(points, nowMs - 55_000);
  const twoMinutes = pointBefore(points, nowMs - 115_000);
  const priorPrices = points.filter((point) => Date.parse(point.at) < nowMs - 10_000).map((point) => point.price);
  const referencePrices = [quote.open, quote.high, quote.low, quote.last]
    .filter((value): value is number => value != null && value > 0);
  const referenceAverage = quote.averagePrice
    ?? (referencePrices.length
      ? referencePrices.reduce((sum, value) => sum + value, 0) / referencePrices.length
      : quote.last);
  const momentum1mPct = oneMinute ? (quote.last / oneMinute.price - 1) * 100 : null;
  const distanceFromAveragePct = referenceAverage > 0 ? (quote.last / referenceAverage - 1) * 100 : null;
  const spreadPct = quote.bid && quote.ask && quote.last > 0
    ? (quote.ask - quote.bid) / quote.last * 100
    : null;
  const recentVolume = oneMinute && quote.tradeVolume != null && oneMinute.volume != null
    ? quote.tradeVolume - oneMinute.volume
    : null;
  const priorVolume = oneMinute && twoMinutes && oneMinute.volume != null && twoMinutes.volume != null
    ? oneMinute.volume - twoMinutes.volume
    : null;
  const volumeAcceleration = recentVolume != null && priorVolume != null && priorVolume > 0
    ? recentVolume / priorVolume
    : null;
  const previousHigh = priorPrices.length ? Math.max(...priorPrices) : null;
  const reasons: string[] = [];

  if (!quote.isRealtime) reasons.push("行情不是即時來源");
  if (!oneMinute || !twoMinutes) reasons.push("分鐘樣本暖機中");
  if ((quote.tradeValue ?? 0) < 50_000_000) reasons.push("成交值低於 5,000 萬");
  if (spreadPct == null || spreadPct > 0.45) reasons.push("買賣價差過大");
  if (distanceFromAveragePct == null || distanceFromAveragePct < 0.1) reasons.push("尚未站穩盤中均價");
  if (momentum1mPct == null || momentum1mPct < 0.25 || momentum1mPct > 1.8) reasons.push("一分鐘動能不在有效區間");
  if (volumeAcceleration == null || volumeAcceleration < 1.2 || volumeAcceleration > 8) reasons.push("分鐘量能未健康加速");
  if (previousHigh == null || quote.last < previousHigh * 1.0005) reasons.push("尚未突破短線高點");
  if ((quote.changePct ?? 0) < 0.3 || (quote.changePct ?? 99) > 6.5) reasons.push("當日漲幅不在交易區間");
  if ((candidate?.score ?? 0) < 50) reasons.push("候選品質分數不足");

  const score = clamp(
    45
      + (candidate?.score ?? 0) * 0.25
      + (momentum1mPct ?? 0) * 8
      + (distanceFromAveragePct ?? 0) * 4
      + Math.min(volumeAcceleration ?? 0, 4) * 4
      - Math.max(0, (spreadPct ?? 0.5) - 0.15) * 18,
    0,
    100,
  );

  return {
    symbol: quote.symbol,
    action: reasons.length ? "WAIT" : "BUY",
    score,
    momentum1mPct,
    volumeAcceleration,
    distanceFromAveragePct,
    spreadPct,
    reasons,
  };
}

function appendLog(state: DaytradePaperState, log: DaytradeLog) {
  state.logs = [log, ...state.logs].slice(0, 80);
}

function sellPosition(
  state: DaytradePaperState,
  position: DaytradePosition,
  quote: DaytradeQuote,
  reason: string,
  now: string,
) {
  const reference = quote.bid && quote.bid > 0 ? quote.bid : quote.last;
  const price = reference * (1 - DAYTRADE_SLIPPAGE_RATE);
  const notional = price * position.quantity;
  const fee = commission(notional);
  const tax = notional * DAYTRADE_TAX_RATE;
  const realizedPnl = notional - fee - tax - position.entryPrice * position.quantity - position.entryFee;
  state.cash += notional - fee - tax;
  state.realizedPnl += realizedPnl;
  state.positions = state.positions.filter((item) => item.symbol !== position.symbol);
  state.cooldownUntil[position.symbol] = new Date(Date.parse(now) + 10 * 60_000).toISOString();
  state.fills.unshift({
    id: `${now}-${position.symbol}-SELL`,
    symbol: position.symbol,
    name: position.name,
    side: "SELL",
    quantity: position.quantity,
    price,
    filledAt: now,
    fee,
    tax,
    reason,
    realizedPnl,
  });
  state.fills = state.fills.slice(0, 100);
  appendLog(state, {
    at: now,
    level: realizedPnl >= 0 ? "trade" : "risk",
    message: `${position.symbol} ${reason}，模擬賣出 ${position.quantity} 股，已實現 ${Math.round(realizedPnl).toLocaleString("zh-TW")} 元。`,
  });
}

function buyPosition(
  state: DaytradePaperState,
  quote: DaytradeQuote,
  signal: DaytradeSignal,
  equity: number,
  now: string,
) {
  const reference = quote.ask && quote.ask > 0 ? quote.ask : quote.last;
  const price = reference * (1 + DAYTRADE_SLIPPAGE_RATE);
  const capitalBudget = Math.min(state.cash * 0.3, equity * 0.2);
  const riskBudget = equity * 0.0035;
  const quantity = Math.max(0, Math.floor(Math.min(
    capitalBudget / (price * (1 + DAYTRADE_COMMISSION_RATE)),
    riskBudget / (price * 0.01),
  )));
  if (quantity < 1) {
    appendLog(state, { at: now, level: "risk", message: `${quote.symbol} 訊號通過，但模擬資金不足以建立部位。` });
    return;
  }
  const notional = price * quantity;
  const fee = commission(notional);
  if (notional + fee > state.cash) return;
  state.cash -= notional + fee;
  state.positions.push({
    symbol: quote.symbol,
    name: quote.name,
    quantity,
    entryAt: now,
    entryPrice: price,
    entryFee: fee,
    stopPrice: price * 0.99,
    targetPrice: price * 1.018,
    peakPrice: price,
    signalScore: signal.score,
  });
  state.fills.unshift({
    id: `${now}-${quote.symbol}-BUY`,
    symbol: quote.symbol,
    name: quote.name,
    side: "BUY",
    quantity,
    price,
    filledAt: now,
    fee,
    tax: 0,
    reason: "動能、量能、均價與流動性閘門通過",
    realizedPnl: null,
  });
  state.fills = state.fills.slice(0, 100);
  appendLog(state, {
    at: now,
    level: "trade",
    message: `${quote.symbol} 模擬買進 ${quantity} 股，成交 ${price.toFixed(2)}，停損 ${(price * 0.99).toFixed(2)}。`,
  });
}

export function advanceDaytradeState(
  current: DaytradePaperState,
  quotes: DaytradeQuote[],
  histories: Record<string, QuotePoint[]>,
  candidates: DaytradeCandidate[],
): DaytradePaperState {
  if (!quotes.length) return current;
  const newestQuote = quotes.reduce((latest, quote) => Date.parse(quote.at) > Date.parse(latest.at) ? quote : latest);
  const now = newestQuote.at;
  const sessionDate = taipeiSessionDate(new Date(now));
  if (current.sessionDate !== sessionDate && current.positions.length === 0) {
    return createDaytradeState(sessionDate);
  }
  const state: DaytradePaperState = {
    ...current,
    positions: current.positions.map((position) => ({ ...position })),
    fills: [...current.fills],
    cooldownUntil: { ...current.cooldownUntil },
    lastProcessedAt: { ...current.lastProcessedAt },
    logs: [...current.logs],
    updatedAt: now,
  };
  const bySymbol = new Map(quotes.map((quote) => [quote.symbol, quote]));
  const minute = taipeiMinutes(now);

  for (const position of [...state.positions]) {
    const quote = bySymbol.get(position.symbol);
    if (!quote || !quote.isRealtime || state.lastProcessedAt[position.symbol] === quote.at) continue;
    position.peakPrice = Math.max(position.peakPrice, quote.last);
    const heldMinutes = (Date.parse(now) - Date.parse(position.entryAt)) / 60_000;
    const trailingActive = position.peakPrice >= position.entryPrice * 1.012;
    if (minute >= 13 * 60 + 20) sellPosition(state, position, quote, "13:20 強制平倉", now);
    else if (quote.last <= position.stopPrice) sellPosition(state, position, quote, "固定停損", now);
    else if (quote.last >= position.targetPrice) sellPosition(state, position, quote, "固定停利", now);
    else if (trailingActive && quote.last <= position.peakPrice * 0.994) sellPosition(state, position, quote, "移動停利", now);
    else if (heldMinutes >= 45) sellPosition(state, position, quote, "持倉逾 45 分鐘", now);
  }

  const equity = daytradeEquity(state, quotes);
  if (equity <= state.startingCash * 0.985 && state.status !== "halted") {
    for (const position of [...state.positions]) {
      const quote = bySymbol.get(position.symbol);
      if (quote?.isRealtime) sellPosition(state, position, quote, "每日虧損達 1.5%", now);
    }
    state.status = "halted";
    appendLog(state, { at: now, level: "risk", message: "每日虧損達 1.5%，當日模擬交易已停機。" });
  }

  const buyCount = state.fills.filter((fill) => fill.side === "BUY").length;
  if (state.status === "running" && minute >= 9 * 60 + 15 && minute <= 12 * 60 + 45 && state.positions.length < 2 && buyCount < 6) {
    const candidateBySymbol = new Map(candidates.map((candidate) => [candidate.symbol, candidate]));
    const assessments = quotes
      .filter((quote) => quote.isRealtime && !state.positions.some((position) => position.symbol === quote.symbol))
      .filter((quote) => !state.cooldownUntil[quote.symbol] || Date.parse(state.cooldownUntil[quote.symbol]) <= Date.parse(now))
      .map((quote) => ({ quote, signal: evaluateDaytradeSignal(quote, histories[quote.symbol] ?? [], candidateBySymbol.get(quote.symbol)) }))
      .filter((item) => item.signal.action === "BUY")
      .sort((a, b) => b.signal.score - a.signal.score);
    if (assessments[0]) buyPosition(state, assessments[0].quote, assessments[0].signal, equity, now);
  }

  for (const quote of quotes) state.lastProcessedAt[quote.symbol] = quote.at;
  return state;
}
