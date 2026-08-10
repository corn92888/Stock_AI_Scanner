"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  CircleAlert,
  CircleStop,
  Clock3,
  Pause,
  Play,
  Plus,
  RadioTower,
  RotateCcw,
  ShieldCheck,
  WalletCards,
  Wifi,
  WifiOff,
  X,
} from "lucide-react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  advanceDaytradeState,
  createDaytradeState,
  daytradeEquity,
  daytradeUnrealizedPnl,
  evaluateDaytradeSignal,
  taipeiSessionDate,
  type DaytradeCandidate,
  type DaytradePaperState,
  type DaytradeQuote,
  type QuotePoint,
} from "@/lib/daytrade";
import type { DashboardSnapshot } from "@/lib/types";

const ACCOUNT_KEY = "stock-ai-daytrade-paper-v1";
const WATCHLIST_KEY = "stock-ai-daytrade-watchlist-v1";
const MAX_WATCHLIST = 5;

type QuotePayload = {
  generatedAt: string;
  provider: "fugle" | "twse_mis" | "mixed" | "scanner_snapshot";
  configured: boolean;
  realtime: boolean;
  pollAfterMs: number;
  quotes: DaytradeQuote[];
  warnings: string[];
};

const moneyFormatter = new Intl.NumberFormat("zh-TW", {
  style: "currency",
  currency: "TWD",
  maximumFractionDigits: 0,
});
const decimalFormatter = new Intl.NumberFormat("zh-TW", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const integerFormatter = new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 0 });

function timeLabel(value: string) {
  if (!value) return "--";
  return new Intl.DateTimeFormat("zh-TW", {
    timeZone: "Asia/Taipei",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).format(new Date(value));
}

function pct(value: number | null, digits = 2) {
  return value == null || !Number.isFinite(value) ? "--" : `${value >= 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

function sourceLabel(source?: DaytradeQuote["source"]) {
  if (source === "fugle") return "Fugle 即時";
  if (source === "twse_mis") return "TWSE 備援";
  if (source === "scanner_snapshot") return "掃描快照";
  return "等待行情";
}

function candidatePool(snapshot: DashboardSnapshot): DaytradeCandidate[] {
  const sorted = [...snapshot.candidates].sort((left, right) => {
    const priority = Number(right.isSelected) - Number(left.isSelected)
      || Number(right.tradable) - Number(left.tradable)
      || right.score - left.score
      || Date.parse(right.runAt) - Date.parse(left.runAt);
    return priority;
  });
  const unique = new Map<string, DaytradeCandidate>();
  for (const row of sorted) {
    if (!unique.has(row.code)) unique.set(row.code, { symbol: row.code, name: row.name, score: row.score });
  }
  return [...unique.values()].slice(0, MAX_WATCHLIST);
}

function stateStatusLabel(state: DaytradePaperState) {
  if (state.status === "running") return "執行中";
  if (state.status === "halted") return "風控停機";
  return "已暫停";
}

export default function DaytradeTerminal({ snapshot }: { snapshot: DashboardSnapshot }) {
  const initialCandidates = useMemo(() => candidatePool(snapshot), [snapshot]);
  const [watchSymbols, setWatchSymbols] = useState(() => initialCandidates.map((row) => row.symbol));
  const [manualSymbol, setManualSymbol] = useState("");
  const [quotes, setQuotes] = useState<DaytradeQuote[]>([]);
  const [feed, setFeed] = useState<QuotePayload | null>(null);
  const [feedError, setFeedError] = useState("");
  const [histories, setHistories] = useState<Record<string, QuotePoint[]>>({});
  const [selectedSymbol, setSelectedSymbol] = useState(() => initialCandidates[0]?.symbol ?? "");
  const [state, setState] = useState<DaytradePaperState>(() => createDaytradeState(
    taipeiSessionDate(new Date(snapshot.generatedAt)),
    snapshot.generatedAt,
  ));
  const [hydrated, setHydrated] = useState(false);
  const historiesRef = useRef<Record<string, QuotePoint[]>>({});
  const symbolsKey = watchSymbols.join(",");

  const candidates = useMemo(() => {
    const bySymbol = new Map(initialCandidates.map((row) => [row.symbol, row]));
    return watchSymbols.map((symbol) => bySymbol.get(symbol) ?? {
      symbol,
      name: symbol,
      score: 50,
    });
  }, [initialCandidates, watchSymbols]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      try {
        const storedWatchlist = JSON.parse(window.localStorage.getItem(WATCHLIST_KEY) ?? "null") as unknown;
        if (Array.isArray(storedWatchlist)) {
          const valid = storedWatchlist.filter((value): value is string => typeof value === "string" && /^\d{4,6}$/.test(value)).slice(0, MAX_WATCHLIST);
          if (valid.length) {
            setWatchSymbols(valid);
            setSelectedSymbol(valid[0]);
          }
        }
        const storedState = JSON.parse(window.localStorage.getItem(ACCOUNT_KEY) ?? "null") as DaytradePaperState | null;
        if (storedState?.version && storedState.sessionDate === taipeiSessionDate()) setState(storedState);
      } catch {
        window.localStorage.removeItem(ACCOUNT_KEY);
      }
      setHydrated(true);
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    if (!hydrated) return;
    window.localStorage.setItem(ACCOUNT_KEY, JSON.stringify(state));
  }, [hydrated, state]);

  useEffect(() => {
    if (!hydrated || !watchSymbols.length) return;
    window.localStorage.setItem(WATCHLIST_KEY, JSON.stringify(watchSymbols));
  }, [hydrated, watchSymbols]);

  useEffect(() => {
    if (!symbolsKey) return;
    let cancelled = false;
    let timer = 0;

    const poll = async () => {
      let nextPoll = 5_000;
      try {
        const response = await fetch(`/api/daytrade/quotes?symbols=${encodeURIComponent(symbolsKey)}`, { cache: "no-store" });
        if (!response.ok) throw new Error(`行情服務回應 ${response.status}`);
        const payload = await response.json() as QuotePayload;
        if (cancelled) return;
        nextPoll = Math.max(3_000, payload.pollAfterMs || 5_000);
        setFeed(payload);
        setFeedError("");
        setQuotes(payload.quotes);

        const nextHistories = { ...historiesRef.current };
        for (const quote of payload.quotes) {
          const points = nextHistories[quote.symbol] ?? [];
          if (!points.some((point) => point.at === quote.at)) {
            nextHistories[quote.symbol] = [...points, {
              at: quote.at,
              price: quote.last,
              volume: quote.tradeVolume,
            }].filter((point) => Date.parse(point.at) >= Date.parse(quote.at) - 30 * 60_000).slice(-400);
          }
        }
        historiesRef.current = nextHistories;
        setHistories(nextHistories);
        setState((current) => advanceDaytradeState(current, payload.quotes, nextHistories, candidates));
      } catch (error) {
        if (!cancelled) setFeedError(error instanceof Error ? error.message : "行情服務暫時無法連線");
      } finally {
        if (!cancelled) timer = window.setTimeout(poll, nextPoll);
      }
    };

    void poll();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [candidates, symbolsKey]);

  const quoteBySymbol = useMemo(() => new Map(quotes.map((quote) => [quote.symbol, quote])), [quotes]);
  const signalBySymbol = useMemo(() => new Map(watchSymbols.map((symbol) => {
    const quote = quoteBySymbol.get(symbol);
    const candidate = candidates.find((row) => row.symbol === symbol);
    return [symbol, quote ? evaluateDaytradeSignal(quote, histories[symbol] ?? [], candidate) : null] as const;
  })), [candidates, histories, quoteBySymbol, watchSymbols]);
  const equity = daytradeEquity(state, quotes);
  const totalPnl = equity - state.startingCash;
  const unrealizedPnl = state.positions.reduce((sum, position) => sum + daytradeUnrealizedPnl(position, quoteBySymbol.get(position.symbol)), 0);
  const closedTrades = state.fills.filter((fill) => fill.side === "SELL").length;
  const chartPoints = (histories[selectedSymbol] ?? []).map((point) => ({
    time: timeLabel(point.at),
    price: point.price,
  }));

  const changeStatus = (status: DaytradePaperState["status"]) => {
    setState((current) => ({
      ...current,
      status,
      updatedAt: new Date().toISOString(),
      logs: [{
        at: new Date().toISOString(),
        level: status === "running" ? "info" as const : "risk" as const,
        message: status === "running" ? "模擬當沖引擎已啟動。" : "模擬當沖引擎已由使用者暫停。",
      }, ...current.logs].slice(0, 80),
    }));
  };

  const resetAccount = () => {
    if (!window.confirm("確定重設今日模擬帳戶、部位與成交紀錄？")) return;
    historiesRef.current = {};
    setHistories({});
    setState(createDaytradeState());
  };

  const addSymbol = (event: FormEvent) => {
    event.preventDefault();
    const symbol = manualSymbol.trim();
    if (!/^\d{4,6}$/.test(symbol) || watchSymbols.includes(symbol) || watchSymbols.length >= MAX_WATCHLIST) return;
    setWatchSymbols((current) => [...current, symbol]);
    setSelectedSymbol(symbol);
    setManualSymbol("");
  };

  const removeSymbol = (symbol: string) => {
    if (state.positions.some((position) => position.symbol === symbol)) return;
    setWatchSymbols((current) => current.filter((item) => item !== symbol));
    if (selectedSymbol === symbol) setSelectedSymbol(watchSymbols.find((item) => item !== symbol) ?? "");
  };

  return (
    <div className="view-stack daytrade-terminal">
      <section className="daytrade-hero">
        <div>
          <span className="eyebrow">Realtime paper execution</span>
          <h2>即時當沖模擬機器人</h2>
          <p>五秒級行情輪詢、規則訊號、模擬撮合與日內風控。只使用虛擬資金，不連接券商真實委託。</p>
        </div>
        <div className="daytrade-controls">
          <span className={`feed-badge ${feed?.realtime ? "online" : "stale"}`}>
            {feed?.realtime ? <Wifi size={15} /> : <WifiOff size={15} />}
            {sourceLabel(quotes[0]?.source)}
          </span>
          {state.status === "running" ? (
            <button className="terminal-button pause" type="button" onClick={() => changeStatus("paused")}><Pause size={16} />暫停</button>
          ) : (
            <button className="terminal-button start" type="button" onClick={() => changeStatus("running")} disabled={state.status === "halted"}><Play size={16} />啟動模擬</button>
          )}
          <button className="terminal-icon-button" type="button" onClick={resetAccount} title="重設模擬帳戶" aria-label="重設模擬帳戶"><RotateCcw size={16} /></button>
        </div>
      </section>

      {(feedError || feed?.warnings.length) ? (
        <div className="validation-banner">
          <CircleAlert size={18} />
          <div><strong>{feedError || feed?.warnings[0]}</strong><p>{feedError ? "引擎不會在行情中斷時建立新倉。" : feed?.warnings.slice(1).join(" ") || "正式部署建議設定 FUGLE_MARKETDATA_API_KEY。"}</p></div>
        </div>
      ) : null}

      <section className="metrics-grid metrics-grid-five daytrade-metrics">
        <div className={`metric ${state.status === "running" ? "metric-positive" : state.status === "halted" ? "metric-danger" : "metric-warning"}`}><span className="metric-label"><Activity size={14} />機器人</span><strong>{stateStatusLabel(state)}</strong><small>頁面開啟時持續運作</small></div>
        <div className="metric metric-info"><span className="metric-label"><WalletCards size={14} />模擬權益</span><strong>{moneyFormatter.format(equity)}</strong><small>可用現金 {moneyFormatter.format(state.cash)}</small></div>
        <div className={`metric ${totalPnl >= 0 ? "metric-positive" : "metric-danger"}`}><span className="metric-label"><Activity size={14} />本日損益</span><strong>{moneyFormatter.format(totalPnl)}</strong><small>{pct(totalPnl / state.startingCash * 100)} · 未實現 {moneyFormatter.format(unrealizedPnl)}</small></div>
        <div className="metric"><span className="metric-label"><ShieldCheck size={14} />持倉</span><strong>{state.positions.length} / 2</strong><small>單筆風險 0.35% · 日損 1.5%</small></div>
        <div className="metric"><span className="metric-label"><Clock3 size={14} />完成交易</span><strong>{closedTrades}</strong><small>今日進場 {state.fills.filter((fill) => fill.side === "BUY").length} / 6</small></div>
      </section>

      <section className="panel daytrade-chart-panel">
        <div className="panel-header">
          <div><span className="eyebrow">Live tape</span><h2>{selectedSymbol || "--"} 盤中價格</h2><p>{feed ? `行情更新 ${timeLabel(feed.generatedAt)} · ${Math.round(feed.pollAfterMs / 1000)} 秒輪詢` : "正在連線行情服務"}</p></div>
          <form className="symbol-add-form" onSubmit={addSymbol}>
            <input value={manualSymbol} onChange={(event) => setManualSymbol(event.target.value.replace(/\D/g, "").slice(0, 6))} placeholder="股票代號" aria-label="新增股票代號" />
            <button type="submit" disabled={watchSymbols.length >= MAX_WATCHLIST || !/^\d{4,6}$/.test(manualSymbol)} title="加入觀察" aria-label="加入觀察"><Plus size={16} /></button>
          </form>
        </div>
        <div className="daytrade-chart-frame">
          {chartPoints.length > 1 ? (
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartPoints} margin={{ top: 16, right: 22, bottom: 4, left: 6 }}>
                <CartesianGrid stroke="#292d30" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="time" stroke="#73797e" tick={{ fontSize: 9 }} minTickGap={34} />
                <YAxis domain={["auto", "auto"]} stroke="#73797e" tick={{ fontSize: 9 }} width={55} />
                <Tooltip contentStyle={{ background: "#151718", border: "1px solid #3b4044", borderRadius: 5, fontSize: 11 }} formatter={(value) => [decimalFormatter.format(Number(value)), "價格"]} />
                <Line type="monotone" dataKey="price" stroke="#55c29a" strokeWidth={2} dot={false} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          ) : <div className="empty-state">持續收集盤中報價，取得第二筆資料後顯示走勢。</div>}
        </div>
      </section>

      <section className="panel">
        <div className="panel-header"><div><span className="eyebrow">Signal monitor</span><h2>即時觀察與交易閘門</h2><p>09:15 至 12:45 允許進場；量能、動能、價差、均價與流動性必須同時通過。</p></div><span className="record-count">{watchSymbols.length} / {MAX_WATCHLIST} 檔</span></div>
        <div className="table-scroll">
          <table className="data-table compact-table daytrade-watch-table">
            <thead><tr><th>標的</th><th>即時價</th><th>漲跌</th><th>一分鐘動能</th><th>量能加速</th><th>價差</th><th>候選分數</th><th>訊號</th><th>阻擋原因</th><th /></tr></thead>
            <tbody>{watchSymbols.length ? watchSymbols.map((symbol) => {
              const quote = quoteBySymbol.get(symbol);
              const signal = signalBySymbol.get(symbol);
              const candidate = candidates.find((row) => row.symbol === symbol);
              return <tr key={symbol} className={selectedSymbol === symbol ? "selected-row" : ""} onClick={() => setSelectedSymbol(symbol)}><td><div className="symbol-cell"><strong>{symbol}</strong><span>{quote?.name ?? candidate?.name ?? "讀取中"}</span></div><small className={`quote-source ${quote?.isRealtime ? "online" : "stale"}`}>{sourceLabel(quote?.source)} · {quote ? timeLabel(quote.at) : "--"}</small></td><td>{quote ? decimalFormatter.format(quote.last) : "--"}</td><td className={(quote?.changePct ?? 0) >= 0 ? "positive-text" : "negative-text"}>{pct(quote?.changePct ?? null)}</td><td>{pct(signal?.momentum1mPct ?? null)}</td><td>{signal?.volumeAcceleration == null ? "--" : `${decimalFormatter.format(signal.volumeAcceleration)}x`}</td><td>{pct(signal?.spreadPct ?? null)}</td><td>{decimalFormatter.format(candidate?.score ?? 50)}</td><td><span className={`status-pill ${signal?.action === "BUY" ? "selected" : quote?.isRealtime ? "neutral" : "blocked"}`}>{signal?.action === "BUY" ? "模擬買進" : signal?.action === "WAIT" ? "等待" : "無行情"}</span></td><td className="risk-cell" title={signal?.reasons.join("、")}>{signal?.reasons.slice(0, 2).join("、") || "全部條件通過"}</td><td><button className="row-icon-button" type="button" onClick={(event) => { event.stopPropagation(); removeSymbol(symbol); }} disabled={state.positions.some((position) => position.symbol === symbol)} title="移除觀察" aria-label={`移除 ${symbol}`}><X size={14} /></button></td></tr>;
            }) : <tr><td colSpan={10}>請輸入股票代號加入觀察。</td></tr>}</tbody>
          </table>
        </div>
      </section>

      <div className="daytrade-lower-grid">
        <section className="panel">
          <div className="panel-header"><div><span className="eyebrow">Open risk</span><h2>即時模擬部位</h2></div><span className="record-count">{state.positions.length} 筆</span></div>
          <div className="table-scroll"><table className="data-table compact-table daytrade-position-table"><thead><tr><th>標的</th><th>股數</th><th>成本 / 現價</th><th>停損 / 目標</th><th>未實現損益</th><th>進場時間</th></tr></thead><tbody>{state.positions.length ? state.positions.map((position) => { const quote = quoteBySymbol.get(position.symbol); const pnl = daytradeUnrealizedPnl(position, quote); return <tr key={position.symbol}><td><div className="symbol-cell"><strong>{position.symbol}</strong><span>{position.name}</span></div></td><td>{integerFormatter.format(position.quantity)}</td><td>{decimalFormatter.format(position.entryPrice)} / {quote ? decimalFormatter.format(quote.last) : "--"}</td><td>{decimalFormatter.format(position.stopPrice)} / {decimalFormatter.format(position.targetPrice)}</td><td className={pnl >= 0 ? "positive-text" : "negative-text"}>{moneyFormatter.format(pnl)}</td><td>{timeLabel(position.entryAt)}</td></tr>; }) : <tr><td colSpan={6}>目前沒有模擬持倉。</td></tr>}</tbody></table></div>
        </section>

        <section className="panel daytrade-log-panel">
          <div className="panel-header"><div><span className="eyebrow">Execution log</span><h2>引擎事件</h2></div><span className="record-count">{state.logs.length} 筆</span></div>
          <div className="terminal-log">{state.logs.slice(0, 14).map((log, index) => <div key={`${log.at}-${index}`} className={log.level}><span>{log.level === "trade" ? <RadioTower size={14} /> : log.level === "risk" ? <CircleStop size={14} /> : <Activity size={14} />}</span><time>{timeLabel(log.at)}</time><p>{log.message}</p></div>)}</div>
        </section>
      </div>

      <section className="panel">
        <div className="panel-header"><div><span className="eyebrow">Paper fills</span><h2>今日模擬成交明細</h2><p>成交價已加入 5 bps 滑價、最高牌告手續費與當沖賣出證交稅。</p></div><span className="record-count">{state.fills.length} 筆</span></div>
        <div className="table-scroll"><table className="data-table compact-table daytrade-fill-table"><thead><tr><th>時間</th><th>標的</th><th>方向</th><th>股數</th><th>成交價</th><th>手續費</th><th>稅</th><th>已實現損益</th><th>原因</th></tr></thead><tbody>{state.fills.length ? state.fills.map((fill) => <tr key={fill.id}><td>{timeLabel(fill.filledAt)}</td><td><div className="symbol-cell"><strong>{fill.symbol}</strong><span>{fill.name}</span></div></td><td><span className={`status-pill ${fill.side === "BUY" ? "selected" : "blocked"}`}>{fill.side === "BUY" ? "買進" : "賣出"}</span></td><td>{integerFormatter.format(fill.quantity)}</td><td>{decimalFormatter.format(fill.price)}</td><td>{moneyFormatter.format(fill.fee)}</td><td>{moneyFormatter.format(fill.tax)}</td><td className={(fill.realizedPnl ?? 0) >= 0 ? "positive-text" : "negative-text"}>{fill.realizedPnl == null ? "--" : moneyFormatter.format(fill.realizedPnl)}</td><td>{fill.reason}</td></tr>) : <tr><td colSpan={9}>啟動後，引擎只會在所有交易與風控條件通過時模擬成交。</td></tr>}</tbody></table></div>
      </section>
    </div>
  );
}
