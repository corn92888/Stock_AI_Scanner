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
  buildDaytradeJournal,
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

function dateTimeLabel(value: string) {
  if (!value) return "--";
  return new Intl.DateTimeFormat("zh-TW", {
    timeZone: "Asia/Taipei",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).format(new Date(value));
}

function durationLabel(seconds: number | null) {
  if (seconds == null) return "持倉中";
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `${minutes} 分 ${remainder} 秒`;
}

function marketSessionState(value: string) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Taipei",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(value));
  const read = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? "";
  const weekday = read("weekday");
  const minutes = Number(read("hour")) * 60 + Number(read("minute"));
  if (weekday === "Sat" || weekday === "Sun") return { key: "closed", label: "非交易日", detail: "週末不執行進場" };
  if (minutes < 9 * 60) return { key: "preopen", label: "開盤前", detail: "09:00 開始接收盤中行情" };
  if (minutes < 9 * 60 + 15) return { key: "warmup", label: "開盤暖機", detail: "09:15 後才允許進場" };
  if (minutes <= 12 * 60 + 45) return { key: "entry", label: "進場時段開放", detail: "策略每次行情更新都會重新評估" };
  if (minutes < 13 * 60 + 20) return { key: "exit_only", label: "只出不進", detail: "12:45 後停止建立新倉" };
  if (minutes <= 13 * 60 + 30) return { key: "flatten", label: "強制平倉時段", detail: "13:20 起清空模擬部位" };
  return { key: "closed", label: "今日已收盤", detail: "下一交易日 09:00 恢復" };
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
  const [lastEvaluationAt, setLastEvaluationAt] = useState("");
  const [evaluationCount, setEvaluationCount] = useState(0);
  const [clock, setClock] = useState(() => Date.parse(snapshot.generatedAt));
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
    const timer = window.setInterval(() => setClock(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, []);

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
        setLastEvaluationAt(payload.generatedAt);
        setEvaluationCount((count) => count + 1);

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
  const tradeJournal = useMemo(() => buildDaytradeJournal(state.fills), [state.fills]);
  const chartPoints = (histories[selectedSymbol] ?? []).map((point) => ({
    time: timeLabel(point.at),
    price: point.price,
  }));
  const chartPrices = chartPoints.map((point) => point.price);
  const chartLow = chartPrices.length ? Math.min(...chartPrices) : 0;
  const chartHigh = chartPrices.length ? Math.max(...chartPrices) : 0;
  const chartPadding = Math.max((chartHigh - chartLow) * 0.2, chartHigh * 0.001, 0.01);
  const chartDomain: [number, number] = [
    Number((chartLow - chartPadding).toFixed(2)),
    Number((chartHigh + chartPadding).toFixed(2)),
  ];
  const chartTicks = [...new Set(Array.from({ length: 5 }, (_, index) => Number(
    (chartDomain[0] + (chartDomain[1] - chartDomain[0]) * index / 4).toFixed(2),
  )))];
  const marketSession = marketSessionState(new Date(clock).toISOString());
  const latestQuoteAt = quotes.reduce((latest, quote) => !latest || Date.parse(quote.at) > Date.parse(latest) ? quote.at : latest, "");
  const quoteAgeSeconds = latestQuoteAt ? Math.max(0, Math.floor((clock - Date.parse(latestQuoteAt)) / 1000)) : null;
  const heartbeatAgeSeconds = feed ? Math.max(0, Math.floor((clock - Date.parse(feed.generatedAt)) / 1000)) : null;
  const heartbeatHealthy = Boolean(feed?.realtime && heartbeatAgeSeconds != null && heartbeatAgeSeconds <= 15 && !feedError);
  const buySignals = [...signalBySymbol.values()].filter((signal) => signal?.action === "BUY").length;
  const warmingSignals = [...signalBySymbol.values()].filter((signal) => signal?.reasons.includes("分鐘樣本暖機中")).length;
  let engineActivity = "尚未啟動模擬引擎";
  let engineActivityDetail = "按下啟動模擬後才會評估與撮合";
  if (state.status === "halted") {
    engineActivity = "風控已停止今日交易";
    engineActivityDetail = "重設帳戶前不會再建立部位";
  } else if (state.status === "running" && feedError) {
    engineActivity = "已啟動，但行情連線中斷";
    engineActivityDetail = "行情恢復前不會建立新倉";
  } else if (state.status === "running" && !heartbeatHealthy) {
    engineActivity = "已啟動，等待有效即時行情";
    engineActivityDetail = "收到新報價後會自動恢復評估";
  } else if (state.status === "running" && marketSession.key !== "entry") {
    engineActivity = marketSession.key === "exit_only" || marketSession.key === "flatten" ? "持續監控出場，不再進場" : "引擎在線，現在不允許進場";
    engineActivityDetail = marketSession.detail;
  } else if (state.status === "running" && state.positions.length > 0) {
    engineActivity = `正在監控 ${state.positions.length} 筆持倉`;
    engineActivityDetail = "每次報價都會檢查停損、停利與持有時間";
  } else if (state.status === "running" && buySignals > 0) {
    engineActivity = `${buySignals} 檔條件通過，處理模擬撮合`;
    engineActivityDetail = "成交後會立即出現在完整交易流水帳";
  } else if (state.status === "running" && warmingSignals > 0) {
    engineActivity = `正在暖機 ${warmingSignals} 檔`;
    engineActivityDetail = "需累積兩分鐘報價才能判斷分鐘動能與量能";
  } else if (state.status === "running") {
    engineActivity = "引擎有在掃描，目前沒有進場訊號";
    engineActivityDetail = "下方每檔的阻擋原因會說明未成交條件";
  }

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

      <section className={`engine-observability ${state.status === "running" && heartbeatHealthy ? "active" : "idle"}`}>
        <div className="engine-primary-state"><span className="engine-pulse" /><div><small>策略引擎</small><strong>{state.status === "running" ? heartbeatHealthy ? "有在運作" : "已啟動・等待行情" : state.status === "halted" ? "風控停機" : "沒有啟動"}</strong><p>{engineActivity}</p></div></div>
        <div><small>最後行情</small><strong>{latestQuoteAt ? dateTimeLabel(latestQuoteAt) : "尚未收到"}</strong><p>{quoteAgeSeconds == null ? "等待第一筆行情" : `${quoteAgeSeconds} 秒前 · ${sourceLabel(quotes[0]?.source)}`}</p></div>
        <div><small>最後策略評估</small><strong>{lastEvaluationAt ? dateTimeLabel(lastEvaluationAt) : "尚未評估"}</strong><p>本頁已評估 {integerFormatter.format(evaluationCount)} 次</p></div>
        <div><small>現在能否買進</small><strong>{marketSession.label}</strong><p>{marketSession.detail}</p></div>
        <div><small>今日成交狀態</small><strong>{closedTrades > 0 ? `已完成 ${closedTrades} 趟` : state.positions.length ? `持倉 ${state.positions.length} 筆` : "尚未成交"}</strong><p>{engineActivityDetail}</p></div>
      </section>

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
                <YAxis domain={chartDomain} ticks={chartTicks} stroke="#73797e" tick={{ fontSize: 9 }} width={55} />
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

      <section className="panel trade-journal-panel">
        <div className="panel-header"><div><span className="eyebrow">Paired trade journal</span><h2>完整交易流水帳</h2><p>每一列是一趟完整模擬交易，買進與賣出時間精確到秒，價格、股數、成本與損益並排顯示。</p></div><span className="record-count">{tradeJournal.length} 趟</span></div>
        <div className="table-scroll"><table className="data-table compact-table trade-journal-table"><thead><tr><th>交易</th><th>標的</th><th>狀態</th><th>買進時間</th><th>買進價</th><th>賣出時間</th><th>賣出價</th><th>股數</th><th>持有時間</th><th>交易成本</th><th>已實現損益</th><th>出場原因</th></tr></thead><tbody>{tradeJournal.length ? tradeJournal.map((trade, index) => { const totalCosts = trade.entryFee + trade.exitFee + trade.tax; return <tr key={trade.id}><td className="rank-cell">#{String(tradeJournal.length - index).padStart(3, "0")}</td><td><div className="symbol-cell"><strong>{trade.symbol}</strong><span>{trade.name}</span></div></td><td><span className={`status-pill ${trade.status === "closed" ? "neutral" : "selected"}`}>{trade.status === "closed" ? "已完成" : "持倉中"}</span></td><td className="trade-entry-cell"><strong>{dateTimeLabel(trade.entryAt)}</strong><small>模擬買進</small></td><td className="positive-text">{decimalFormatter.format(trade.entryPrice)}</td><td className="trade-exit-cell"><strong>{trade.exitAt ? dateTimeLabel(trade.exitAt) : "尚未賣出"}</strong><small>{trade.exitAt ? "模擬賣出" : "等待出場條件"}</small></td><td className={trade.exitPrice == null ? "muted-inline" : "negative-text"}>{trade.exitPrice == null ? "--" : decimalFormatter.format(trade.exitPrice)}</td><td>{integerFormatter.format(trade.quantity)}</td><td>{durationLabel(trade.holdingSeconds)}</td><td>{moneyFormatter.format(totalCosts)}</td><td className={(trade.realizedPnl ?? 0) >= 0 ? "positive-text" : "negative-text"}>{trade.realizedPnl == null ? "未實現" : moneyFormatter.format(trade.realizedPnl)}</td><td>{trade.exitReason ?? "持倉監控中"}</td></tr>; }) : <tr><td colSpan={12}><div className="journal-empty"><strong>今天尚未發生任何模擬成交</strong><span>{engineActivity}。{engineActivityDetail}。</span></div></td></tr>}</tbody></table></div>
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
        <div className="panel-header"><div><span className="eyebrow">Raw paper fills</span><h2>逐筆模擬成交原始紀錄</h2><p>每次買進與賣出都獨立留存，時間精確到秒；成交價已加入 5 bps 滑價、手續費與當沖賣出證交稅。</p></div><span className="record-count">{state.fills.length} 筆</span></div>
        <div className="table-scroll"><table className="data-table compact-table daytrade-fill-table"><thead><tr><th>成交時間（秒）</th><th>標的</th><th>動作</th><th>股數</th><th>成交價</th><th>手續費</th><th>稅</th><th>已實現損益</th><th>成交原因</th></tr></thead><tbody>{state.fills.length ? state.fills.map((fill) => <tr key={fill.id}><td><strong>{dateTimeLabel(fill.filledAt)}</strong></td><td><div className="symbol-cell"><strong>{fill.symbol}</strong><span>{fill.name}</span></div></td><td><span className={`status-pill ${fill.side === "BUY" ? "selected" : "blocked"}`}>{fill.side === "BUY" ? "模擬買進" : "模擬賣出"}</span></td><td>{integerFormatter.format(fill.quantity)}</td><td>{decimalFormatter.format(fill.price)}</td><td>{moneyFormatter.format(fill.fee)}</td><td>{moneyFormatter.format(fill.tax)}</td><td className={(fill.realizedPnl ?? 0) >= 0 ? "positive-text" : "negative-text"}>{fill.realizedPnl == null ? "--" : moneyFormatter.format(fill.realizedPnl)}</td><td>{fill.reason}</td></tr>) : <tr><td colSpan={9}>目前沒有原始成交紀錄；上方引擎狀態會顯示正在評估或未成交的原因。</td></tr>}</tbody></table></div>
      </section>
    </div>
  );
}
