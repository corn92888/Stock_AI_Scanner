import { NextRequest, NextResponse } from "next/server";

import { getDashboardSnapshot } from "@/lib/data";
import type { DaytradeQuote } from "@/lib/daytrade";

export const dynamic = "force-dynamic";

const MAX_SYMBOLS = 5;
const FUGLE_QUOTE_URL = "https://api.fugle.tw/marketdata/v1.0/stock/intraday/quote";
const TWSE_MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp";

type UnknownRecord = Record<string, unknown>;

function record(value: unknown): UnknownRecord {
  return value != null && typeof value === "object" && !Array.isArray(value)
    ? value as UnknownRecord
    : {};
}

function numberValue(...values: unknown[]): number | null {
  for (const value of values) {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string" && value.trim() !== "") {
      const parsed = Number(value.replaceAll(",", ""));
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return null;
}

function textValue(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function firstBookPrice(value: unknown): number | null {
  if (!Array.isArray(value)) return null;
  for (const item of value) {
    const row = record(item);
    const price = numberValue(row.price);
    if (price != null && price > 0) return price;
  }
  return null;
}

function listPrice(value: unknown): number | null {
  if (typeof value !== "string") return null;
  return numberValue(...value.split("_").filter(Boolean));
}

function normalizeTimestamp(value: unknown): string {
  const numeric = numberValue(value);
  if (numeric != null) {
    const milliseconds = numeric < 10_000_000_000 ? numeric * 1000 : numeric;
    const date = new Date(milliseconds);
    if (Number.isFinite(date.getTime())) return date.toISOString();
  }
  if (typeof value === "string") {
    const date = new Date(value);
    if (Number.isFinite(date.getTime())) return date.toISOString();
  }
  return new Date().toISOString();
}

function freshMarketTimestamp(at: string) {
  const ageMs = Date.now() - Date.parse(at);
  return Number.isFinite(ageMs) && ageMs >= -30_000 && ageMs <= 120_000;
}

function parseFugleQuote(payload: unknown, fallbackSymbol: string): DaytradeQuote | null {
  const root = record(payload);
  const data = record(root.data ?? root);
  const info = record(data.info);
  const quote = record(data.quote ?? data);
  const total = record(quote.total ?? data.total);
  const lastTrade = record(quote.lastTrade ?? data.lastTrade);
  const order = record(quote.order ?? data.order);
  const last = numberValue(
    lastTrade.price,
    quote.lastPrice,
    quote.closePrice,
    data.lastPrice,
    data.closePrice,
  );
  if (last == null || last <= 0) return null;
  const symbol = textValue(info.symbol, data.symbol, fallbackSymbol);
  const previousClose = numberValue(quote.previousClose, data.previousClose, quote.referencePrice, data.referencePrice);
  const changePct = numberValue(quote.changePercent, data.changePercent)
    ?? (previousClose && previousClose > 0 ? (last / previousClose - 1) * 100 : null);
  const bid = firstBookPrice(order.bids ?? quote.bids ?? data.bids);
  const ask = firstBookPrice(order.asks ?? quote.asks ?? data.asks);

  return {
    symbol,
    name: textValue(info.name, data.name, symbol),
    market: textValue(info.market, data.market, info.exchange, data.exchange),
    source: "fugle",
    at: normalizeTimestamp(lastTrade.time ?? quote.lastUpdated ?? data.lastUpdated ?? Date.now()),
    last,
    open: numberValue(quote.priceOpen, quote.openPrice, data.openPrice),
    high: numberValue(quote.priceHigh, quote.highPrice, data.highPrice),
    low: numberValue(quote.priceLow, quote.lowPrice, data.lowPrice),
    previousClose,
    averagePrice: numberValue(quote.priceAvg, quote.avgPrice, data.avgPrice),
    changePct,
    bid,
    ask,
    tradeVolume: numberValue(total.tradeVolume, quote.totalVolume, data.totalVolume),
    tradeValue: numberValue(total.tradeValue, quote.totalValue, data.totalValue),
    isRealtime: true,
  };
}

async function fetchFugleQuotes(symbols: string[], apiKey: string) {
  const results = await Promise.all(symbols.map(async (symbol) => {
    try {
      const response = await fetch(`${FUGLE_QUOTE_URL}/${symbol}`, {
        cache: "no-store",
        headers: { Accept: "application/json", "X-API-KEY": apiKey },
        signal: AbortSignal.timeout(5_000),
      });
      if (!response.ok) return null;
      return parseFugleQuote(await response.json(), symbol);
    } catch {
      return null;
    }
  }));
  return results.filter((quote): quote is DaytradeQuote => quote != null);
}

function parseMisQuote(value: unknown): DaytradeQuote | null {
  const row = record(value);
  const symbol = textValue(row.c);
  if (!symbol) return null;
  const bid = listPrice(row.b);
  const ask = listPrice(row.a);
  const previousClose = numberValue(row.y);
  const last = numberValue(row.z, bid && ask ? (bid + ask) / 2 : null, ask, bid, row.o, previousClose);
  if (last == null || last <= 0) return null;
  const at = normalizeTimestamp(row.tlong);
  const volumeLots = numberValue(row.v);
  const tradeVolume = volumeLots == null ? null : volumeLots * 1000;

  return {
    symbol,
    name: textValue(row.n, row.nf, symbol),
    market: textValue(row.ex, row.m),
    source: "twse_mis",
    at,
    last,
    open: numberValue(row.o),
    high: numberValue(row.h),
    low: numberValue(row.l),
    previousClose,
    averagePrice: null,
    changePct: previousClose && previousClose > 0 ? (last / previousClose - 1) * 100 : null,
    bid,
    ask,
    tradeVolume,
    tradeValue: tradeVolume == null ? null : tradeVolume * last,
    isRealtime: freshMarketTimestamp(at),
  };
}

async function fetchTwseMisQuotes(symbols: string[]) {
  const channels = symbols.flatMap((symbol) => [`tse_${symbol}.tw`, `otc_${symbol}.tw`]).join("|");
  const url = new URL(TWSE_MIS_URL);
  url.searchParams.set("ex_ch", channels);
  url.searchParams.set("json", "1");
  url.searchParams.set("delay", "0");
  try {
    const response = await fetch(url, {
      cache: "no-store",
      headers: {
        Accept: "application/json",
        Referer: "https://mis.twse.com.tw/stock/fibest.jsp",
        "User-Agent": "Mozilla/5.0 Stock-AI-Control/1.0",
      },
      signal: AbortSignal.timeout(5_000),
    });
    if (!response.ok) return [];
    const payload = record(await response.json());
    const rows = Array.isArray(payload.msgArray) ? payload.msgArray : [];
    const bySymbol = new Map<string, DaytradeQuote>();
    for (const row of rows) {
      const quote = parseMisQuote(row);
      if (quote && !bySymbol.has(quote.symbol)) bySymbol.set(quote.symbol, quote);
    }
    return symbols.map((symbol) => bySymbol.get(symbol)).filter((quote): quote is DaytradeQuote => quote != null);
  } catch {
    return [];
  }
}

async function scannerFallback(symbols: string[]) {
  const snapshot = await getDashboardSnapshot();
  const candidates = [...snapshot.candidates].sort((a, b) => Date.parse(b.runAt) - Date.parse(a.runAt));
  const quotes: Array<DaytradeQuote | null> = symbols.map((symbol) => {
    const row = candidates.find((candidate) => candidate.code === symbol);
    if (!row) return null;
    return {
      symbol,
      name: row.name || symbol,
      market: "snapshot",
      source: "scanner_snapshot",
      at: row.runAt,
      last: row.signalPrice,
      open: null,
      high: null,
      low: null,
      previousClose: row.pctChange > -99 ? row.signalPrice / (1 + row.pctChange / 100) : null,
      averagePrice: null,
      changePct: row.pctChange,
      bid: null,
      ask: null,
      tradeVolume: null,
      tradeValue: row.turnoverBillion * 100_000_000,
      isRealtime: false,
    };
  });
  return quotes.filter((quote): quote is DaytradeQuote => quote != null);
}

export async function GET(request: NextRequest) {
  const symbols = [...new Set((request.nextUrl.searchParams.get("symbols") ?? "")
    .split(",")
    .map((symbol) => symbol.trim())
    .filter((symbol) => /^\d{4,6}$/.test(symbol)))]
    .slice(0, MAX_SYMBOLS);

  if (!symbols.length) {
    return NextResponse.json({ error: "請提供最多五個有效股票代號" }, { status: 400 });
  }

  const apiKey = process.env.FUGLE_MARKETDATA_API_KEY?.trim() ?? "";
  const warnings: string[] = [];
  const fugleQuotes = apiKey ? await fetchFugleQuotes(symbols, apiKey) : [];
  const fugleSymbols = new Set(fugleQuotes.map((quote) => quote.symbol));
  const missingAfterFugle = symbols.filter((symbol) => !fugleSymbols.has(symbol));
  const misQuotes = missingAfterFugle.length ? await fetchTwseMisQuotes(missingAfterFugle) : [];
  const quoteMap = new Map([...fugleQuotes, ...misQuotes].map((quote) => [quote.symbol, quote]));
  const missing = symbols.filter((symbol) => !quoteMap.has(symbol));
  const fallbackQuotes = missing.length ? await scannerFallback(missing) : [];
  for (const quote of fallbackQuotes) quoteMap.set(quote.symbol, quote);

  if (!apiKey) warnings.push("尚未設定 Fugle MarketData API Key，目前使用證交所網站行情備援；僅供模擬研究。" );
  if (apiKey && missingAfterFugle.length) warnings.push("部分 Fugle 行情讀取失敗，已切換備援來源。" );
  if ([...quoteMap.values()].some((quote) => !quote.isRealtime)) warnings.push("部分報價不是即時行情，交易引擎不會據此建立新倉。" );
  if (missing.length > fallbackQuotes.length) warnings.push("仍有股票代號無法取得報價。" );

  const quotes = symbols.map((symbol) => quoteMap.get(symbol)).filter((quote): quote is DaytradeQuote => quote != null);
  const response = NextResponse.json({
    generatedAt: new Date().toISOString(),
    provider: fugleQuotes.length ? (misQuotes.length ? "mixed" : "fugle") : misQuotes.length ? "twse_mis" : "scanner_snapshot",
    configured: Boolean(apiKey),
    realtime: quotes.length > 0 && quotes.every((quote) => quote.isRealtime),
    pollAfterMs: apiKey ? 10_000 : 5_000,
    quotes,
    warnings,
  });
  response.headers.set("Cache-Control", "no-store, no-cache, must-revalidate");
  return response;
}
