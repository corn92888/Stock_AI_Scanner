"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Activity,
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  ArrowUpRight,
  BarChart3,
  Bot,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  Clock3,
  Code2,
  Database,
  Download,
  ExternalLink,
  Filter,
  Gauge,
  Layers3,
  LoaderCircle,
  Menu,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Target,
  TrendingDown,
  TrendingUp,
  TriangleAlert,
  Workflow,
  X,
  Zap,
} from "lucide-react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { Candidate, DashboardSnapshot, Performance, WorkflowRun } from "@/lib/types";

type ViewId = "decision" | "performance" | "pipeline" | "operations";
type Scope = "selected" | "all" | "rejected";
type CandidateSort = "rank" | "score" | "turnover" | "volume" | "ai" | "excess";
type SortDirection = "asc" | "desc";

const navItems: Array<{ id: ViewId; label: string; hint: string; icon: typeof Gauge }> = [
  { id: "decision", label: "決策工作台", hint: "候選、共識與風險", icon: Gauge },
  { id: "performance", label: "策略驗證", hint: "報酬、回撤與穩定性", icon: BarChart3 },
  { id: "pipeline", label: "AI 與資料", hint: "模型門檻與學習進度", icon: Database },
  { id: "operations", label: "自動化維運", hint: "排程、批次與異常", icon: Workflow },
];

const strategyLabels: Record<string, string> = {
  trend: "順勢突破",
  reversal: "低檔爆量",
  wave: "波段蓄勢",
};

const strategyColors: Record<string, string> = {
  trend: "cyan",
  reversal: "coral",
  wave: "amber",
};

const tooltipStyle = {
  background: "#151719",
  border: "1px solid #363a3d",
  borderRadius: 5,
  color: "#f1f3f4",
  fontSize: 11,
};

function formatNumeric(value: number, minimumDigits: number, maximumDigits: number) {
  const fixed = value.toFixed(maximumDigits);
  const [integer, fraction = ""] = fixed.split(".");
  const grouped = integer.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const trimmed = fraction.replace(/0+$/, "").padEnd(minimumDigits, "0");
  return trimmed ? `${grouped}.${trimmed}` : grouped;
}

const number = { format: (value: number) => formatNumeric(value, 0, 1) };
const decimal = { format: (value: number) => formatNumeric(value, 1, 2) };

function formatDateTime(value: string | null | undefined) {
  if (!value) return "尚無資料";
  const localTimestamp = value.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::\d{2})?$/);
  if (localTimestamp) return `${localTimestamp[2]}/${localTimestamp[3]} ${localTimestamp[4]}:${localTimestamp[5]}`;
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value.replace("T", " ").slice(0, 16);
  const taipei = new Date(date.valueOf() + 8 * 60 * 60 * 1000);
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${pad(taipei.getUTCMonth() + 1)}/${pad(taipei.getUTCDate())} ${pad(taipei.getUTCHours())}:${pad(taipei.getUTCMinutes())}`;
}

function pct(value: number | null | undefined) {
  if (value == null) return "--";
  return `${value > 0 ? "+" : ""}${decimal.format(value)}%`;
}

function avg(values: Array<number | null | undefined>) {
  const usable = values.filter((value): value is number => value != null && Number.isFinite(value));
  return usable.length ? usable.reduce((sum, value) => sum + value, 0) / usable.length : null;
}

function median(values: Array<number | null | undefined>) {
  const usable = values.filter((value): value is number => value != null && Number.isFinite(value)).sort((a, b) => a - b);
  if (!usable.length) return null;
  const middle = Math.floor(usable.length / 2);
  return usable.length % 2 ? usable[middle] : (usable[middle - 1] + usable[middle]) / 2;
}

function modeLabel(mode: string) {
  return mode === "intraday" ? "盤中" : mode === "eod" ? "盤後" : mode;
}

function statusTone(run: WorkflowRun) {
  if (run.status !== "completed") return "running";
  if (run.conclusion === "success") return "success";
  if (run.conclusion === "skipped" || run.conclusion === "cancelled") return "neutral";
  return "danger";
}

type DirectScanRun = {
  id: number;
  status: string;
  conclusion: string | null;
  createdAt: string;
  updatedAt: string;
  url: string;
};

type DirectScanState = {
  tone: "neutral" | "running" | "success" | "danger";
  message: string;
  tracking: boolean;
  run: DirectScanRun | null;
  requestedAt: string | null;
};

function DirectIntradayControl() {
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [state, setState] = useState<DirectScanState>({
    tone: "neutral",
    message: "正在檢查站內執行權限",
    tracking: false,
    run: null,
    requestedAt: null,
  });
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const initialize = async () => {
      try {
        const response = await fetch("/api/scans/intraday", { cache: "no-store" });
        const payload = await response.json() as {
          configured?: boolean;
          active?: DirectScanRun | null;
        };
        const ready = Boolean(payload.configured);
        setConfigured(ready);
        if (payload.active) {
          setState({
            tone: "running",
            message: payload.active.status === "queued" ? "已排入執行佇列" : "盤中掃描執行中",
            tracking: true,
            run: payload.active,
            requestedAt: payload.active.createdAt,
          });
        } else {
          setState((current) => ({
            ...current,
            tone: ready ? "neutral" : "danger",
            message: ready ? "待命" : "站內執行授權待設定",
          }));
        }
      } catch {
        setConfigured(false);
        setState((current) => ({ ...current, tone: "danger", message: "站內掃描 API 無法連線" }));
      }
    };
    void initialize();
  }, []);

  useEffect(() => {
    if (!state.tracking) return;
    const poll = async () => {
      try {
        const response = await fetch("/api/scans/intraday", { cache: "no-store" });
        const payload = await response.json() as {
          active?: DirectScanRun | null;
          latest?: DirectScanRun | null;
        };
        const run = payload.active ?? payload.latest ?? null;
        if (!run) return;
        const requestedAt = state.requestedAt ? new Date(state.requestedAt).valueOf() : 0;
        const runCreatedAt = new Date(run.createdAt).valueOf();
        if (requestedAt && runCreatedAt < requestedAt - 5_000) {
          setState((current) => ({ ...current, message: "等待 GitHub 建立執行批次" }));
          return;
        }
        if (run.status === "queued") {
          setState((current) => ({ ...current, tone: "running", message: "已排入執行佇列", tracking: true, run }));
        } else if (run.status === "in_progress") {
          setState((current) => ({ ...current, tone: "running", message: "盤中掃描執行中", tracking: true, run }));
        } else if (run.conclusion === "success") {
          setState((current) => ({ ...current, tone: "success", message: "盤中掃描完成", tracking: false, run }));
        } else if (run.conclusion) {
          setState((current) => ({ ...current, tone: "danger", message: `掃描${run.conclusion}`, tracking: false, run }));
        }
      } catch {
        setState((current) => ({ ...current, message: "狀態更新暫時中斷" }));
      }
    };
    void poll();
    const timer = window.setInterval(poll, 10_000);
    return () => window.clearInterval(timer);
  }, [state.requestedAt, state.tracking]);

  const trigger = async () => {
    let secret = window.sessionStorage.getItem("scan-trigger-secret") ?? "";
    if (!secret) {
      secret = window.prompt("請輸入掃描控制碼")?.trim() ?? "";
      if (!secret) return;
      window.sessionStorage.setItem("scan-trigger-secret", secret);
    }

    setSubmitting(true);
    setState({ tone: "running", message: "正在送出掃描要求", tracking: false, run: null, requestedAt: null });
    try {
      const response = await fetch("/api/scans/intraday", {
        method: "POST",
        headers: { "x-scan-trigger-secret": secret },
      });
      const payload = await response.json() as {
        error?: string;
        run?: DirectScanRun | null;
        requestedAt?: string;
      };
      if (response.status === 401) {
        window.sessionStorage.removeItem("scan-trigger-secret");
      }
      if (!response.ok && response.status !== 409) {
        setState({ tone: "danger", message: payload.error ?? "無法啟動掃描", tracking: false, run: null, requestedAt: null });
        return;
      }
      setState({
        tone: "running",
        message: response.status === 409 ? "已有盤中掃描正在執行" : "掃描要求已送出",
        tracking: true,
        run: payload.run ?? null,
        requestedAt: payload.requestedAt ?? payload.run?.createdAt ?? new Date().toISOString(),
      });
    } catch {
      setState({ tone: "danger", message: "站內掃描 API 無法連線", tracking: false, run: null, requestedAt: null });
    } finally {
      setSubmitting(false);
    }
  };

  const busy = submitting || state.tracking;
  const disabled = busy || configured !== true;
  return (
    <>
      <button className="action-link primary-action scan-trigger" type="button" onClick={trigger} disabled={disabled} aria-busy={busy}>
        <span>
          {busy ? <LoaderCircle className="spin-icon" size={19} /> : <RefreshCw size={19} />}
          <span><strong>{busy ? "盤中掃描處理中" : configured === false ? "站內掃描待授權" : "執行盤中掃描"}</strong><small>即時行情、候選政策、AI 影子預測與 Telegram</small></span>
        </span>
        <Zap size={18} />
      </button>
      <div className={`scan-trigger-status ${state.tone}`}>
        <span />
        <strong>{state.message}</strong>
        {state.run && <time>{formatDateTime(state.run.updatedAt || state.run.createdAt)}</time>}
      </div>
    </>
  );
}

function candidateRisk(row: Candidate) {
  return [...row.riskFlags, ...row.blockReasons];
}

function downloadCandidates(rows: Candidate[], date: string) {
  const header = ["日期", "排名", "代號", "名稱", "產業", "策略", "分數", "訊號價", "禁止追價線", "漲跌幅", "量比5", "成交值億", "AI_T3機率", "AI預期超額", "AI預期回撤", "狀態", "風險"];
  const escape = (value: unknown) => `"${String(value ?? "").replaceAll('"', '""')}"`;
  const lines = rows.map((row) => [
    row.tradeDate,
    row.selectionRank ?? row.rawRank,
    row.code,
    row.name,
    row.industry,
    row.strategies.map((item) => strategyLabels[item] ?? item).join(" / "),
    row.score,
    row.signalPrice,
    row.chaseLimit ?? "",
    row.pctChange,
    row.volumeRatio5,
    row.turnoverBillion,
    row.aiProbabilityT3 == null ? "" : row.aiProbabilityT3 * 100,
    row.aiExpectedExcess3d ?? "",
    row.aiExpectedDrawdown3d ?? "",
    row.statusLabel,
    candidateRisk(row).join(" / "),
  ].map(escape).join(","));
  const csv = `\uFEFF${header.map(escape).join(",")}\n${lines.join("\n")}`;
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `candidate-desk-${date || "latest"}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}

function PanelHeader({ eyebrow, title, description, trailing }: { eyebrow: string; title: string; description?: string; trailing?: React.ReactNode }) {
  return (
    <div className="panel-header">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h2>{title}</h2>
        {description && <p>{description}</p>}
      </div>
      {trailing}
    </div>
  );
}

function Metric({ label, value, detail, tone = "default", icon: Icon }: { label: string; value: string; detail: string; tone?: "default" | "positive" | "warning" | "danger" | "info"; icon?: typeof Activity }) {
  return (
    <div className={`metric metric-${tone}`}>
      <div className="metric-label">{Icon && <Icon size={14} />}<span>{label}</span></div>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}

function IconButton({ label, onClick, children, active = false }: { label: string; onClick: () => void; children: React.ReactNode; active?: boolean }) {
  return <button type="button" className={`icon-button ${active ? "active" : ""}`} onClick={onClick} aria-label={label} title={label}>{children}</button>;
}

function SortHeader({ label, field, activeField, direction, onSort }: { label: string; field: CandidateSort; activeField: CandidateSort; direction: SortDirection; onSort: (field: CandidateSort) => void }) {
  const Icon = activeField !== field ? ArrowUpDown : direction === "asc" ? ArrowUp : ArrowDown;
  return <button type="button" className={activeField === field ? "sort-button active" : "sort-button"} onClick={() => onSort(field)}>{label}<Icon size={12} /></button>;
}

function CandidateDrawer({ candidate, onClose }: { candidate: Candidate; onClose: () => void }) {
  const risks = candidateRisk(candidate);
  const yahooUrl = `https://tw.stock.yahoo.com/quote/${candidate.code}`;
  return (
    <>
      <button className="drawer-backdrop" onClick={onClose} aria-label="關閉股票詳情" />
      <aside className="candidate-drawer" aria-label={`${candidate.code} ${candidate.name} 決策詳情`}>
        <div className="drawer-header">
          <div>
            <span className="eyebrow">Decision record</span>
            <div className="drawer-symbol"><strong>{candidate.code}</strong><h2>{candidate.name}</h2></div>
            <p>{candidate.industry || "未分類產業"} · {formatDateTime(candidate.runAt)}</p>
          </div>
          <IconButton label="關閉股票詳情" onClick={onClose}><X size={18} /></IconButton>
        </div>

        <div className="drawer-status-row">
          <span className={`status-pill ${candidate.isSelected ? "selected" : candidate.tradable ? "eligible" : "blocked"}`}>{candidate.statusLabel}</span>
          <span className="policy-code">{candidate.policyVersion}</span>
          {candidate.aiProspective ? <span className="status-pill eligible">AI 前瞻</span> : candidate.aiProbabilityT3 != null ? <span className="status-pill neutral">歷史試跑</span> : null}
        </div>

        <section className="drawer-section">
          <h3>決策矩陣</h3>
          <div className="decision-matrix">
            <div><span>規則分數</span><strong>{decimal.format(candidate.score)}</strong><small>{candidate.isSelected ? "正式入選" : "未正式入選"}</small></div>
            <div><span>AI T+3</span><strong>{candidate.aiProbabilityT3 == null ? "--" : `${decimal.format(candidate.aiProbabilityT3 * 100)}%`}</strong><small>{candidate.aiShadowSelected ? "影子入選" : "影子觀察"}</small></div>
            <div><span>預期超額</span><strong className={(candidate.aiExpectedExcess3d ?? 0) >= 0 ? "positive-text" : "negative-text"}>{pct(candidate.aiExpectedExcess3d)}</strong><small>相對大盤 T+3</small></div>
            <div><span>預期回撤</span><strong className="negative-text">{pct(candidate.aiExpectedDrawdown3d)}</strong><small>AI 估計風險</small></div>
          </div>
        </section>

        <section className="drawer-section">
          <h3>價格與交易條件</h3>
          <dl className="detail-list">
            <div><dt>訊號價格</dt><dd>{decimal.format(candidate.signalPrice)}</dd></div>
            <div><dt>隔日觀察價</dt><dd>{candidate.observationPrice == null ? "--" : decimal.format(candidate.observationPrice)}</dd></div>
            <div><dt>禁止追價線</dt><dd>{candidate.chaseLimit == null ? "--" : decimal.format(candidate.chaseLimit)}</dd></div>
            <div><dt>防守距離</dt><dd>{pct(candidate.stopDistancePct)}</dd></div>
            <div><dt>五日量比</dt><dd>{decimal.format(candidate.volumeRatio5)}x</dd></div>
            <div><dt>成交值</dt><dd>{decimal.format(candidate.turnoverBillion)} 億</dd></div>
            <div><dt>日內位置</dt><dd>{candidate.intradayPosition == null ? "--" : decimal.format(candidate.intradayPosition)}</dd></div>
            <div><dt>當日漲跌</dt><dd className={candidate.pctChange >= 0 ? "positive-text" : "negative-text"}>{pct(candidate.pctChange)}</dd></div>
          </dl>
        </section>

        <section className="drawer-section">
          <h3>策略證據</h3>
          <div className="tag-row drawer-tags">{candidate.strategies.map((item) => <span className={`tag ${strategyColors[item] ?? ""}`} key={item}>{strategyLabels[item] ?? item}</span>)}</div>
          {risks.length ? <ul className="evidence-list risk-list">{risks.map((risk) => <li key={risk}><TriangleAlert size={14} />{risk}</li>)}</ul> : <div className="clean-evidence"><CircleCheck size={15} />目前政策未標記額外風險</div>}
        </section>

        <section className="drawer-section">
          <h3>AI 新聞研究</h3>
          {candidate.aiNewsSummary ? <div className="news-brief"><div><span className={`sentiment ${candidate.aiNewsSentiment ?? "neutral"}`}>{candidate.aiNewsSentiment}</span><small>{candidate.aiNewsEvidenceCount} 則證據 · 信心 {candidate.aiNewsConfidence == null ? "--" : `${decimal.format(candidate.aiNewsConfidence * 100)}%`}</small></div><p>{candidate.aiNewsSummary}</p></div> : <div className="empty-inline">此批次尚無新聞 AI 證據。</div>}
        </section>

        <a className="drawer-link" href={yahooUrl} target="_blank" rel="noreferrer">查看即時行情<ExternalLink size={15} /></a>
      </aside>
    </>
  );
}

function CandidateTable({ rows, sort, direction, onSort, onSelect }: { rows: Candidate[]; sort: CandidateSort; direction: SortDirection; onSort: (field: CandidateSort) => void; onSelect: (candidate: Candidate) => void }) {
  if (!rows.length) return <div className="empty-state">目前篩選條件沒有候選標的。</div>;
  return (
    <div className="table-scroll candidate-table-scroll">
      <table className="data-table candidate-table">
        <thead><tr>
          <th><SortHeader label="排名" field="rank" activeField={sort} direction={direction} onSort={onSort} /></th>
          <th>標的</th><th>策略</th>
          <th><SortHeader label="分數" field="score" activeField={sort} direction={direction} onSort={onSort} /></th>
          <th>訊號價</th><th>禁止追價線</th><th>漲跌</th>
          <th><SortHeader label="量比" field="volume" activeField={sort} direction={direction} onSort={onSort} /></th>
          <th><SortHeader label="成交值" field="turnover" activeField={sort} direction={direction} onSort={onSort} /></th>
          <th><SortHeader label="AI T+3" field="ai" activeField={sort} direction={direction} onSort={onSort} /></th>
          <th><SortHeader label="預期超額" field="excess" activeField={sort} direction={direction} onSort={onSort} /></th>
          <th>規則 / AI</th><th>風險</th>
        </tr></thead>
        <tbody>{rows.map((row, index) => {
          const agreement = row.isSelected && row.aiShadowSelected;
          const risks = candidateRisk(row);
          return <tr key={`${row.runAt}-${row.code}-${index}`}>
            <td className="rank-cell">{row.selectionRank ?? row.rawRank ?? index + 1}</td>
            <td><button type="button" className="table-symbol" onClick={() => onSelect(row)}><strong>{row.code}</strong><span>{row.name}</span><small>{row.industry || "未分類"}</small></button></td>
            <td><div className="tag-row">{row.strategies.map((item) => <span className={`tag ${strategyColors[item] ?? ""}`} key={item}>{strategyLabels[item] ?? item}</span>)}</div></td>
            <td><strong>{decimal.format(row.score)}</strong></td>
            <td>{decimal.format(row.signalPrice)}</td>
            <td>{row.chaseLimit == null ? <span className="muted-inline">--</span> : <strong className="price-limit">{decimal.format(row.chaseLimit)}</strong>}</td>
            <td className={row.pctChange >= 0 ? "positive-text" : "negative-text"}>{pct(row.pctChange)}</td>
            <td>{decimal.format(row.volumeRatio5)}x</td>
            <td>{decimal.format(row.turnoverBillion)} 億</td>
            <td>{row.aiProbabilityT3 == null ? <span className="muted-inline">--</span> : <div className="ai-cell"><strong>{decimal.format(row.aiProbabilityT3 * 100)}%</strong><small>{row.aiProspective ? "前瞻" : "試跑"}</small></div>}</td>
            <td className={(row.aiExpectedExcess3d ?? 0) >= 0 ? "positive-text" : "negative-text"}>{pct(row.aiExpectedExcess3d)}</td>
            <td><div className="consensus-cell"><span className={`status-pill ${row.isSelected ? "selected" : row.tradable ? "eligible" : "blocked"}`}>{row.statusLabel}</span>{agreement && <span className="agreement-mark"><Zap size={11} />共識</span>}</div></td>
            <td className="risk-cell" title={risks.join("、") || undefined}>{risks.length ? <span className="risk-count"><TriangleAlert size={12} />{risks.slice(0, 2).join("、")}</span> : <span className="clean-mark"><CheckCircle2 size={12} />清潔</span>}</td>
          </tr>;
        })}</tbody>
      </table>
    </div>
  );
}

function DecisionView({ snapshot }: { snapshot: DashboardSnapshot }) {
  const dates = useMemo(() => [...new Set(snapshot.candidates.map((row) => row.tradeDate))].sort().reverse(), [snapshot.candidates]);
  const latestCandidateDate = dates.includes(snapshot.overview.latestTradeDate) ? snapshot.overview.latestTradeDate : dates[0] || "";
  const [date, setDate] = useState(latestCandidateDate);
  const [scope, setScope] = useState<Scope>("selected");
  const [query, setQuery] = useState("");
  const [strategy, setStrategy] = useState("all");
  const [riskFilter, setRiskFilter] = useState("all");
  const [sort, setSort] = useState<CandidateSort>("rank");
  const [direction, setDirection] = useState<SortDirection>("asc");
  const [selectedCandidate, setSelectedCandidate] = useState<Candidate | null>(null);

  const dayRows = useMemo(() => snapshot.candidates.filter((row) => row.tradeDate === date), [snapshot.candidates, date]);
  const rows = useMemo(() => dayRows.filter((row) => {
    if (scope === "selected" && !row.isSelected) return false;
    if (scope === "rejected" && row.isSelected) return false;
    if (strategy !== "all" && !row.strategies.includes(strategy)) return false;
    if (riskFilter === "risk" && !candidateRisk(row).length) return false;
    if (riskFilter === "clean" && candidateRisk(row).length) return false;
    const needle = query.trim().toLowerCase();
    return !needle || row.code.toLowerCase().includes(needle) || row.name.toLowerCase().includes(needle) || row.industry.toLowerCase().includes(needle);
  }).sort((a, b) => {
    const values: Record<CandidateSort, [number, number]> = {
      rank: [a.selectionRank ?? a.rawRank, b.selectionRank ?? b.rawRank],
      score: [a.score, b.score],
      turnover: [a.turnoverBillion, b.turnoverBillion],
      volume: [a.volumeRatio5, b.volumeRatio5],
      ai: [a.aiProbabilityT3 ?? -1, b.aiProbabilityT3 ?? -1],
      excess: [a.aiExpectedExcess3d ?? Number.NEGATIVE_INFINITY, b.aiExpectedExcess3d ?? Number.NEGATIVE_INFINITY],
    };
    const delta = values[sort][0] - values[sort][1];
    return direction === "asc" ? delta : -delta;
  }), [dayRows, scope, strategy, riskFilter, query, sort, direction]);

  const handleSort = (field: CandidateSort) => {
    if (field === sort) setDirection((current) => current === "asc" ? "desc" : "asc");
    else { setSort(field); setDirection(field === "rank" ? "asc" : "desc"); }
  };
  const resetFilters = () => { setScope("selected"); setQuery(""); setStrategy("all"); setRiskFilter("all"); setSort("rank"); setDirection("asc"); };

  const selected = dayRows.filter((row) => row.isSelected);
  const tradable = dayRows.filter((row) => row.tradable);
  const aiEvaluated = dayRows.filter((row) => row.aiProbabilityT3 != null);
  const agreement = selected.filter((row) => row.aiShadowSelected).length;
  const chartData = snapshot.dailyCandidates.slice(-36).map((row) => ({ ...row, label: row.tradeDate.slice(5) }));
  const topIndustries = Object.entries(dayRows.reduce<Record<string, number>>((acc, row) => { const key = row.industry || "未分類"; acc[key] = (acc[key] ?? 0) + 1; return acc; }, {})).sort((a, b) => b[1] - a[1]).slice(0, 5);
  const uniqueRuns = new Set(dayRows.map((row) => row.runAt)).size;

  return (
    <div className="view-stack">
      <section className="metrics-grid metrics-grid-five">
        <Metric label="分析批次" value={number.format(uniqueRuns)} detail={`${date || "--"} 盤中快照`} tone="info" icon={Layers3} />
        <Metric label="可交易候選" value={number.format(tradable.length)} detail={`通過率 ${dayRows.length ? Math.round(tradable.length / dayRows.length * 100) : 0}%`} tone="positive" icon={CheckCircle2} />
        <Metric label="正式入選" value={number.format(selected.length)} detail={`成交值中位 ${median(selected.map((row) => row.turnoverBillion)) == null ? "--" : decimal.format(median(selected.map((row) => row.turnoverBillion)) ?? 0)} 億`} tone="warning" icon={Target} />
        <Metric label="AI 已評估" value={number.format(aiEvaluated.length)} detail={`${agreement} 檔規則與 AI 共識`} tone={aiEvaluated.length ? "info" : "default"} icon={Bot} />
        <Metric label="風險阻擋" value={number.format(dayRows.filter((row) => !row.tradable).length)} detail="未通過硬性交易條件" tone="danger" icon={ShieldCheck} />
      </section>

      <section className="panel decision-panel">
        <PanelHeader eyebrow="Decision workspace" title="候選決策工作台" description="同一交易日的規則訊號、流動性、AI 影子預測與風險證據" trailing={<div className="panel-actions"><span className="record-count">{rows.length} / {dayRows.length} 筆</span><IconButton label="重設篩選" onClick={resetFilters}><RotateCcw size={16} /></IconButton><IconButton label="匯出目前候選 CSV" onClick={() => downloadCandidates(rows, date)}><Download size={16} /></IconButton></div>} />
        <div className="toolbar decision-toolbar">
          <label className="select-control"><span>交易日</span><select value={date} onChange={(event) => setDate(event.target.value)}>{dates.map((item) => <option key={item}>{item}</option>)}</select></label>
          <div className="segmented" aria-label="候選範圍">{([['selected', '正式入選'], ['all', '全部候選'], ['rejected', '未入選']] as Array<[Scope, string]>).map(([id, label]) => <button key={id} className={scope === id ? "active" : ""} onClick={() => setScope(id)}>{label}</button>)}</div>
          <label className="select-control"><span>策略</span><select value={strategy} onChange={(event) => setStrategy(event.target.value)}><option value="all">全部策略</option><option value="trend">順勢突破</option><option value="reversal">低檔爆量</option><option value="wave">波段蓄勢</option></select></label>
          <label className="select-control"><span>風險</span><select value={riskFilter} onChange={(event) => setRiskFilter(event.target.value)}><option value="all">全部狀態</option><option value="clean">僅看清潔</option><option value="risk">僅看有風險</option></select></label>
          <label className="search-control"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜尋代號、名稱或產業" /></label>
        </div>
        <CandidateTable rows={rows.slice(0, 160)} sort={sort} direction={direction} onSort={handleSort} onSelect={setSelectedCandidate} />
      </section>

      <section className="analysis-grid">
        <div className="panel chart-panel">
          <PanelHeader eyebrow="Selection flow" title="近 36 個交易日訊號漏斗" description="觀察候選供給、可交易率與正式名單是否異常漂移" />
          <div className="chart-frame"><ResponsiveContainer width="100%" height="100%"><AreaChart data={chartData} margin={{ top: 14, right: 16, left: -20, bottom: 0 }}><defs><linearGradient id="candidateFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#5fb3d9" stopOpacity={0.22} /><stop offset="100%" stopColor="#5fb3d9" stopOpacity={0} /></linearGradient></defs><CartesianGrid stroke="#2b2e31" vertical={false} /><XAxis dataKey="label" stroke="#777d82" tickLine={false} axisLine={false} minTickGap={26} /><YAxis stroke="#777d82" tickLine={false} axisLine={false} /><Tooltip contentStyle={tooltipStyle} /><Area type="monotone" dataKey="candidates" name="候選" stroke="#5fb3d9" fill="url(#candidateFill)" strokeWidth={2} /><Line type="monotone" dataKey="tradable" name="可交易" stroke="#55c29a" strokeWidth={2} dot={false} /><Line type="monotone" dataKey="selected" name="正式入選" stroke="#e2ae5f" strokeWidth={2} dot={false} /></AreaChart></ResponsiveContainer></div>
        </div>
        <div className="panel exposure-panel">
          <PanelHeader eyebrow="Exposure monitor" title="產業訊號集中度" description="當日候選出現次數，不代表資金配置" />
          <div className="exposure-list">{topIndustries.map(([industry, count]) => <div className="exposure-row" key={industry}><div><span>{industry}</span><strong>{count}</strong></div><div className="progress"><i style={{ width: `${dayRows.length ? Math.max(4, count / dayRows.length * 100) : 0}%` }} /></div></div>)}</div>
          <p className="panel-note"><CircleAlert size={15} />正式入選是研究優先序，不是買進指令。下單前仍須核對最新報價、公告與事件風險。</p>
        </div>
      </section>
      {selectedCandidate && <CandidateDrawer candidate={selectedCandidate} onClose={() => setSelectedCandidate(null)} />}
    </div>
  );
}

function PerformanceView({ snapshot }: { snapshot: DashboardSnapshot }) {
  const [mode, setMode] = useState("all");
  const [sampleScope, setSampleScope] = useState<"formal" | "all">("formal");
  const [strategy, setStrategy] = useState("all");
  const [windowSize, setWindowSize] = useState("all");
  const rows = useMemo(() => {
    let result = snapshot.performance.filter((row) => (sampleScope !== "formal" || row.isFormalSelection) && (mode === "all" || row.mode === mode) && (strategy === "all" || row.strategy === strategy));
    if (windowSize !== "all") {
      const dates = [...new Set(result.map((row) => row.tradeDate))].sort().slice(-Number(windowSize));
      result = result.filter((row) => dates.includes(row.tradeDate));
    }
    return result;
  }, [snapshot.performance, mode, sampleScope, strategy, windowSize]);
  const mature = rows.filter((row) => row.maturedHorizon >= 3 && row.netReturn3d != null);
  const grouped = useMemo(() => Object.values(mature.reduce<Record<string, { label: string; values: Performance[] }>>((acc, row) => { const key = `${row.mode}-${row.strategy}`; acc[key] ??= { label: `${modeLabel(row.mode)} ${row.strategyLabel}`, values: [] }; acc[key].values.push(row); return acc; }, {})).map((group) => ({ strategy: group.label, samples: group.values.length, netT3: avg(group.values.map((row) => row.netReturn3d)) ?? 0, excessT3: avg(group.values.map((row) => row.excessReturn3d)) ?? 0, drawdown: avg(group.values.map((row) => row.maxDrawdown3d)) ?? 0, winRate: group.values.filter((row) => (row.netReturn3d ?? 0) > 0).length / group.values.length * 100, success: group.values.filter((row) => row.successT3).length / group.values.length * 100 })), [mature]);
  const dailyPerformance = useMemo(() => {
    const daily = Object.values(mature.reduce<Record<string, { date: string; net: number[]; excess: number[] }>>((acc, row) => { acc[row.tradeDate] ??= { date: row.tradeDate, net: [], excess: [] }; if (row.netReturn3d != null) acc[row.tradeDate].net.push(row.netReturn3d); if (row.excessReturn3d != null) acc[row.tradeDate].excess.push(row.excessReturn3d); return acc; }, {})).sort((a, b) => a.date.localeCompare(b.date));
    return daily.reduce<Array<{ date: string; cumulativeNet: number; cumulativeExcess: number; dailyNet: number }>>((result, day) => {
      const previous = result[result.length - 1];
      const dailyNet = avg(day.net) ?? 0;
      return [...result, { date: day.date.slice(5), dailyNet, cumulativeNet: (previous?.cumulativeNet ?? 0) + dailyNet, cumulativeExcess: (previous?.cumulativeExcess ?? 0) + (avg(day.excess) ?? 0) }];
    }, []);
  }, [mature]);
  const wins = mature.map((row) => row.netReturn3d).filter((value): value is number => value != null && value > 0);
  const losses = mature.map((row) => row.netReturn3d).filter((value): value is number => value != null && value <= 0);
  const winRate = mature.length ? wins.length / mature.length * 100 : 0;
  const successRate = mature.length ? mature.filter((row) => row.successT3).length / mature.length * 100 : 0;
  const payoff = wins.length && losses.length ? (avg(wins) ?? 0) / Math.abs(avg(losses) ?? 1) : null;

  return (
    <div className="view-stack">
      <section className="control-strip">
        <div><span className="eyebrow">Validation cohort</span><strong>{sampleScope === "formal" ? "正式入選樣本" : "全部研究訊號"}</strong></div>
        <div className="filter-groups"><div className="segmented"><button className={sampleScope === "formal" ? "active" : ""} onClick={() => setSampleScope("formal")}>正式入選</button><button className={sampleScope === "all" ? "active" : ""} onClick={() => setSampleScope("all")}>全部訊號</button></div><label className="inline-select"><Filter size={14} /><select value={mode} onChange={(event) => setMode(event.target.value)}><option value="all">全部模式</option><option value="intraday">盤中</option><option value="eod">盤後</option></select></label><label className="inline-select"><select value={strategy} onChange={(event) => setStrategy(event.target.value)}><option value="all">全部策略</option><option value="trend">順勢突破</option><option value="reversal">低檔爆量</option><option value="wave">波段蓄勢</option></select></label><label className="inline-select"><Clock3 size={14} /><select value={windowSize} onChange={(event) => setWindowSize(event.target.value)}><option value="all">全部期間</option><option value="20">近 20 日</option><option value="60">近 60 日</option></select></label></div>
      </section>
      <section className="metrics-grid metrics-grid-five">
        <Metric label="成熟樣本" value={number.format(mature.length)} detail={sampleScope === "formal" ? `${snapshot.overview.formalBacktestResults}/${snapshot.overview.formalSelections} 已回測` : `${snapshot.overview.backtestResults} 筆全部結果`} tone="info" icon={Database} />
        <Metric label="平均淨報酬" value={pct(avg(mature.map((row) => row.netReturn3d)))} detail="扣除模型交易成本" tone={(avg(mature.map((row) => row.netReturn3d)) ?? 0) >= 0 ? "positive" : "danger"} icon={TrendingUp} />
        <Metric label="平均超額" value={pct(avg(mature.map((row) => row.excessReturn3d)))} detail="相對大盤 T+3" tone={(avg(mature.map((row) => row.excessReturn3d)) ?? 0) >= 0 ? "positive" : "danger"} icon={Activity} />
        <Metric label="正報酬率" value={`${decimal.format(winRate)}%`} detail={`策略成功率 ${decimal.format(successRate)}%`} tone={winRate >= 50 ? "positive" : "warning"} icon={Target} />
        <Metric label="盈虧比" value={payoff == null ? "--" : `${decimal.format(payoff)}x`} detail={`平均回撤 ${pct(avg(mature.map((row) => row.maxDrawdown3d)))}`} tone={(payoff ?? 0) >= 1 ? "positive" : "warning"} icon={TrendingDown} />
      </section>

      {mature.length < 150 && <div className="validation-banner"><TriangleAlert size={18} /><div><strong>樣本仍不足以升級正式策略</strong><p>目前篩選後只有 {mature.length} 筆成熟樣本；建議至少累積 150 筆前瞻結果並跨越不同盤勢後再調整 AI 權重。</p></div></div>}

      <section className="analysis-grid equal-grid">
        <div className="panel chart-panel">
          <PanelHeader eyebrow="Signal equity proxy" title="逐日累積訊號報酬" description="以每日訊號平均值累加，僅供策略漂移觀察，不是實際資金曲線" />
          <div className="chart-frame tall"><ResponsiveContainer width="100%" height="100%"><LineChart data={dailyPerformance} margin={{ top: 18, right: 18, left: -10, bottom: 0 }}><CartesianGrid stroke="#2b2e31" vertical={false} /><XAxis dataKey="date" stroke="#777d82" tickLine={false} axisLine={false} minTickGap={25} /><YAxis stroke="#777d82" tickLine={false} axisLine={false} unit="%" /><Tooltip contentStyle={tooltipStyle} /><ReferenceLine y={0} stroke="#555b60" /><Line type="monotone" dataKey="cumulativeNet" name="累積淨報酬" stroke="#55c29a" strokeWidth={2} dot={false} /><Line type="monotone" dataKey="cumulativeExcess" name="累積超額" stroke="#5fb3d9" strokeWidth={2} dot={false} /></LineChart></ResponsiveContainer></div>
        </div>
        <div className="panel chart-panel">
          <PanelHeader eyebrow="Strategy matrix" title="策略風險報酬比較" description="同時比較平均淨報酬、超額報酬與最大回撤" />
          <div className="chart-frame tall"><ResponsiveContainer width="100%" height="100%"><BarChart data={grouped} margin={{ top: 18, right: 12, left: -8, bottom: 42 }}><CartesianGrid stroke="#2b2e31" vertical={false} /><XAxis dataKey="strategy" stroke="#777d82" tickLine={false} axisLine={false} interval={0} angle={-16} textAnchor="end" /><YAxis stroke="#777d82" tickLine={false} axisLine={false} unit="%" /><Tooltip contentStyle={tooltipStyle} /><ReferenceLine y={0} stroke="#555b60" /><Bar dataKey="netT3" name="淨報酬" fill="#55c29a" radius={[2, 2, 0, 0]} /><Bar dataKey="excessT3" name="超額" fill="#5fb3d9" radius={[2, 2, 0, 0]} /><Bar dataKey="drawdown" name="回撤" fill="#df756b" radius={[2, 2, 0, 0]} /></BarChart></ResponsiveContainer></div>
        </div>
      </section>

      <section className="panel">
        <PanelHeader eyebrow="Cohort breakdown" title="策略分群統計" description="先看樣本數，再解讀報酬；少量樣本不應直接比較高低" trailing={<span className="record-count">{grouped.length} 組</span>} />
        <div className="table-scroll"><table className="data-table compact-table strategy-table"><thead><tr><th>策略群組</th><th>樣本</th><th>平均淨報酬</th><th>平均超額</th><th>平均回撤</th><th>正報酬率</th><th>成功率</th></tr></thead><tbody>{grouped.map((row) => <tr key={row.strategy}><td><strong>{row.strategy}</strong></td><td>{row.samples}</td><td className={row.netT3 >= 0 ? "positive-text" : "negative-text"}>{pct(row.netT3)}</td><td className={row.excessT3 >= 0 ? "positive-text" : "negative-text"}>{pct(row.excessT3)}</td><td className="negative-text">{pct(row.drawdown)}</td><td>{decimal.format(row.winRate)}%</td><td>{decimal.format(row.success)}%</td></tr>)}</tbody></table></div>
      </section>

      <section className="panel">
        <PanelHeader eyebrow="Audit trail" title="成熟樣本明細" description="每筆結果保留訊號日、交易模式、策略版本與成本後表現" trailing={<span className="record-count">{mature.length} 筆</span>} />
        <div className="table-scroll"><table className="data-table compact-table"><thead><tr><th>日期</th><th>模式 / 策略</th><th>標的</th><th>T+1</th><th>T+3 淨報酬</th><th>T+3 超額</th><th>最大回撤</th><th>判定</th></tr></thead><tbody>{mature.slice(0, 180).map((row, index) => <tr key={`${row.tradeDate}-${row.code}-${index}`}><td>{row.tradeDate}</td><td>{modeLabel(row.mode)} · {row.strategyLabel}</td><td><strong>{row.code}</strong> {row.name}</td><td>{pct(row.netReturn1d)}</td><td className={(row.netReturn3d ?? 0) >= 0 ? "positive-text" : "negative-text"}>{pct(row.netReturn3d)}</td><td className={(row.excessReturn3d ?? 0) >= 0 ? "positive-text" : "negative-text"}>{pct(row.excessReturn3d)}</td><td className="negative-text">{pct(row.maxDrawdown3d)}</td><td><span className={`status-pill ${row.successT3 ? "selected" : "blocked"}`}>{row.successT3 ? "通過" : "未通過"}</span></td></tr>)}</tbody></table></div>
      </section>
    </div>
  );
}

function GateRow({ label, value, target, display, passed, lowerIsBetter = false }: { label: string; value: number; target: number; display: string; passed: boolean; lowerIsBetter?: boolean }) {
  const progress = lowerIsBetter ? target / Math.max(value, target) * 100 : value / target * 100;
  return <div className="gate-row"><div><span>{passed ? <CircleCheck size={15} /> : <Clock3 size={15} />}{label}</span><strong className={passed ? "positive-text" : ""}>{display}</strong></div><div className="progress"><i className={passed ? "green" : "amber"} style={{ width: `${Math.min(100, Math.max(3, progress))}%` }} /></div><small>上線門檻 {lowerIsBetter ? "≤" : "≥"} {target}</small></div>;
}

function PipelineView({ snapshot }: { snapshot: DashboardSnapshot }) {
  const stages = [
    { label: "掃描訊號", value: snapshot.overview.signals, icon: Activity, note: "原始策略命中" },
    { label: "候選事件", value: snapshot.overview.candidateEvents, icon: SlidersHorizontal, note: "正規化決策紀錄" },
    { label: "正式入選", value: snapshot.overview.formalSelections, icon: Target, note: "通過政策排序" },
    { label: "特徵快照", value: snapshot.overview.featureSnapshots, icon: Database, note: "模型可用輸入" },
    { label: "前瞻預測", value: snapshot.overview.prospectivePredictions ?? 0, icon: Bot, note: "即時模型輸出" },
    { label: "成熟結果", value: snapshot.overview.maturePredictionOutcomes ?? 0, icon: CheckCircle2, note: "前瞻 T+3 標註" },
  ];
  const max = Math.max(...stages.map((stage) => stage.value), 1);
  const statusData = snapshot.statusCounts.slice(0, 7).map((row) => ({ ...row, short: row.label.slice(0, 6) }));
  const latestModel = (snapshot.aiModels ?? [])[0];
  const samples = latestModel?.metrics.samples ?? 0;
  const positives = latestModel?.metrics.positive_samples ?? 0;
  const auc = latestModel?.metrics.validation_auc ?? 0;
  const mae = latestModel?.metrics.validation_excess_mae ?? 99;
  const prospective = snapshot.overview.prospectivePredictions ?? 0;
  const matureProspective = snapshot.overview.maturePredictionOutcomes ?? 0;
  const allGatesPassed = samples >= 150 && positives >= 30 && auc >= 0.6 && mae <= 3 && matureProspective >= 150;
  const featureCoverage = snapshot.overview.candidateEvents ? snapshot.overview.featureSnapshots / snapshot.overview.candidateEvents * 100 : 0;

  return (
    <div className="view-stack">
      <section className="model-hero-band">
        <div><span className="eyebrow">Model governance</span><h2>{allGatesPassed ? "模型符合候選升級門檻" : latestModel ? "影子模型運作中，尚未允許接管排名" : "模型資料仍在建立"}</h2><p>{latestModel ? `${latestModel.version} · 訓練區間 ${latestModel.trainingStart} 至 ${latestModel.trainingEnd}` : "尚無可用模型版本"}</p></div>
        <div className={`governance-state ${allGatesPassed ? "ready" : "shadow"}`}><Bot size={20} /><span>{allGatesPassed ? "PROMOTION REVIEW" : "SHADOW ONLY"}</span></div>
      </section>

      <section className="pipeline-board panel">
        <PanelHeader eyebrow="Data lineage" title="量化學習資料鏈" description={`特徵覆蓋率 ${decimal.format(featureCoverage)}%，所有階段保留版本與時間點`} trailing={<span className={`health-badge ${allGatesPassed ? "healthy" : "building"}`}>{allGatesPassed ? "可審查升級" : latestModel ? "影子測試" : "累積中"}</span>} />
        <div className="pipeline-grid">{stages.map((stage, index) => { const Icon = stage.icon; return <div className="pipeline-stage" key={stage.label}><div className="stage-icon"><Icon size={18} /></div><span>{stage.label}</span><strong>{number.format(stage.value)}</strong><small>{stage.note}</small><div className="progress"><i style={{ width: `${Math.max(stage.value ? 3 : 0, stage.value / max * 100)}%` }} /></div>{index < stages.length - 1 && <ChevronRight className="stage-arrow" size={16} />}</div>; })}</div>
      </section>

      <section className="analysis-grid equal-grid">
        <div className="panel gate-panel">
          <PanelHeader eyebrow="Promotion gates" title="模型升級門檻" description="全部通過後仍需人工審查，不會自動接管正式策略" />
          <div className="gate-list">
            <GateRow label="成熟訓練樣本" value={samples} target={150} display={`${samples} 筆`} passed={samples >= 150} />
            <GateRow label="成功正樣本" value={positives} target={30} display={`${positives} 筆`} passed={positives >= 30} />
            <GateRow label="時序驗證 AUC" value={auc} target={0.6} display={auc ? decimal.format(auc) : "NA"} passed={auc >= 0.6} />
            <GateRow label="超額報酬 MAE" value={mae} target={3} display={mae < 99 ? pct(mae) : "NA"} passed={mae <= 3} lowerIsBetter />
            <GateRow label="成熟前瞻預測" value={matureProspective} target={150} display={`${matureProspective} 筆`} passed={matureProspective >= 150} />
          </div>
        </div>
        <div className="panel model-panel">
          <PanelHeader eyebrow="Latest challenger" title="目前影子模型" description="模型只提供平行排名，正式名單仍由版本化規則控制" />
          {latestModel ? <div className="model-spec"><div className="model-version"><Bot size={22} /><div><strong>{latestModel.modelName}</strong><span>{latestModel.version}</span></div></div><dl className="detail-list"><div><dt>狀態</dt><dd>{latestModel.status}</dd></div><div><dt>特徵版本</dt><dd>{latestModel.featureVersion}</dd></div><div><dt>訓練樣本</dt><dd>{samples}</dd></div><div><dt>驗證樣本</dt><dd>{latestModel.metrics.validation_samples ?? "--"}</dd></div><div><dt>Brier Score</dt><dd>{latestModel.metrics.validation_brier == null ? "--" : decimal.format(latestModel.metrics.validation_brier)}</dd></div><div><dt>回撤 MAE</dt><dd>{pct(latestModel.metrics.validation_drawdown_mae)}</dd></div><div><dt>新聞證據</dt><dd>{snapshot.overview.newsEvidence}</dd></div><div><dt>前瞻預測</dt><dd>{prospective}</dd></div></dl></div> : <div className="empty-state">尚未建立模型版本。</div>}
        </div>
      </section>

      <section className="analysis-grid">
        <div className="panel chart-panel">
          <PanelHeader eyebrow="Policy outcomes" title="候選事件分流" description="觀察主要阻擋原因是否因資料品質或政策門檻異常增加" />
          <div className="chart-frame"><ResponsiveContainer width="100%" height="100%"><BarChart data={statusData} margin={{ top: 14, right: 10, left: -16, bottom: 34 }}><CartesianGrid stroke="#2b2e31" vertical={false} /><XAxis dataKey="short" stroke="#777d82" tickLine={false} axisLine={false} angle={-14} textAnchor="end" interval={0} /><YAxis stroke="#777d82" tickLine={false} axisLine={false} /><Tooltip contentStyle={tooltipStyle} /><Bar dataKey="count" name="筆數" fill="#5fb3d9" radius={[2, 2, 0, 0]} /></BarChart></ResponsiveContainer></div>
        </div>
        <div className="panel run-list-panel">
          <PanelHeader eyebrow="Recent ingestion" title="最近資料寫入" description="盤中與盤後掃描的資料版本與來源" />
          <div className="run-list">{snapshot.scanRuns.slice(0, 8).map((run) => <div className="run-row" key={run.id}><span className={`run-dot ${run.mode}`} /><div><strong>{modeLabel(run.mode)}掃描 · {run.tradeDate}</strong><small>{run.source} · {run.strategyVersion}</small></div><time>{formatDateTime(run.runAt)}</time></div>)}</div>
        </div>
      </section>
    </div>
  );
}

function OperationsView({ snapshot, workflowRuns, snapshotFresh }: { snapshot: DashboardSnapshot; workflowRuns: WorkflowRun[]; snapshotFresh: boolean }) {
  const dailyUrl = "https://github.com/corn92888/Stock_AI_Scanner/actions/workflows/daily_scan.yml";
  const latestSuccess = workflowRuns.find((run) => run.conclusion === "success");
  const latestFailed = workflowRuns.find((run) => run.conclusion === "failure");
  const completed = workflowRuns.filter((run) => run.status === "completed");
  const workflowSuccessRate = completed.length ? completed.filter((run) => run.conclusion === "success" || run.conclusion === "skipped").length / completed.length * 100 : 0;
  const lastBacktest = snapshot.backtestRuns[0];
  const healthChecks = [
    { label: "公開資料快照", ok: snapshotFresh, detail: formatDateTime(snapshot.generatedAt) },
    { label: "GitHub Actions API", ok: workflowRuns.length > 0, detail: `${workflowRuns.length} 筆執行紀錄` },
    { label: "特徵資料完整", ok: snapshot.overview.featureSnapshots >= snapshot.overview.candidateEvents, detail: `${snapshot.overview.featureSnapshots}/${snapshot.overview.candidateEvents}` },
    { label: "回測批次可追溯", ok: Boolean(lastBacktest), detail: lastBacktest ? formatDateTime(lastBacktest.startedAt) : "尚無紀錄" },
  ];
  return (
    <div className="view-stack">
      <section className="metrics-grid metrics-grid-five">
        <Metric label="自動化服務" value={workflowRuns.length ? "ONLINE" : "DEGRADED"} detail={`${workflowRuns.length} 筆最近執行`} tone={workflowRuns.length ? "positive" : "danger"} icon={Workflow} />
        <Metric label="執行成功率" value={`${decimal.format(workflowSuccessRate)}%`} detail={`最近 ${completed.length} 筆已完成`} tone={workflowSuccessRate >= 80 ? "positive" : "warning"} icon={CheckCircle2} />
        <Metric label="最近成功" value={latestSuccess ? modeLabel(latestSuccess.name.includes("Intraday") ? "intraday" : "eod") : "--"} detail={formatDateTime(latestSuccess?.updatedAt)} icon={Clock3} />
        <Metric label="最近失敗" value={latestFailed ? "需檢查" : "無"} detail={latestFailed ? formatDateTime(latestFailed.updatedAt) : "最近紀錄未見失敗"} tone={latestFailed ? "danger" : "positive"} icon={TriangleAlert} />
        <Metric label="資料快照" value={snapshot.overview.latestTradeDate || "--"} detail={formatDateTime(snapshot.generatedAt)} tone={snapshotFresh ? "info" : "warning"} icon={Database} />
      </section>

      <section className="operations-grid">
        <div className="panel action-panel">
          <PanelHeader eyebrow="Manual control" title="受控執行入口" description="站內控制碼授權；GitHub 憑證只保留在伺服器端" />
          <DirectIntradayControl />
          <a className="action-link" href={dailyUrl} target="_blank" rel="noreferrer"><span><Clock3 size={19} /><span><strong>執行盤後結算</strong><small>盤後訊號、T+3/T+20 回測與模型結果更新</small></span></span><ArrowUpRight size={18} /></a>
          <div className="permission-note"><ShieldCheck size={17} /><p>站內觸發由伺服器端授權，仍會套用交易時段、防重複與資料覆蓋率檢查。</p></div>
        </div>
        <div className="panel health-panel">
          <PanelHeader eyebrow="System checks" title="服務健康檢查" description="部署、資料、特徵與回測四個必要面向" />
          <div className="health-list">{healthChecks.map((check) => <div className="health-row" key={check.label}><span className={check.ok ? "ok" : "warn"}>{check.ok ? <CircleCheck size={16} /> : <TriangleAlert size={16} />}</span><div><strong>{check.label}</strong><small>{check.detail}</small></div><span>{check.ok ? "正常" : "注意"}</span></div>)}</div>
        </div>
      </section>

      <section className="panel workflow-panel">
        <PanelHeader eyebrow="Live automation" title="最近工作流程" description="可直接開啟 GitHub 查看步驟、耗時與錯誤紀錄" trailing={<a className="icon-link" href="https://github.com/corn92888/Stock_AI_Scanner/actions" target="_blank" rel="noreferrer" aria-label="開啟 GitHub Actions" title="開啟 GitHub Actions"><Code2 size={17} /></a>} />
        <div className="workflow-grid">{workflowRuns.length ? workflowRuns.slice(0, 10).map((run) => <a href={run.url} target="_blank" rel="noreferrer" className="workflow-row" key={run.id}><span className={`run-dot ${statusTone(run)}`} /><div><strong>{run.name}</strong><small>{run.event === "workflow_dispatch" ? "手動執行" : "定時排程"} · {formatDateTime(run.createdAt)}</small></div><span className={`workflow-status ${statusTone(run)}`}>{run.status !== "completed" ? "執行中" : run.conclusion === "success" ? "成功" : run.conclusion === "skipped" ? "略過" : run.conclusion ?? "未知"}</span><ExternalLink size={14} /></a>) : <div className="empty-state">GitHub 狀態暫時無法讀取，掃描入口仍可使用。</div>}</div>
      </section>

      <section className="panel">
        <PanelHeader eyebrow="Backtest ledger" title="最近回測批次" description="正式與研究訊號分開記錄，部分成熟結果會在後續交易日更新" trailing={<span className="record-count">{snapshot.backtestRuns.length} 筆</span>} />
        <div className="table-scroll"><table className="data-table compact-table"><thead><tr><th>開始時間</th><th>樣本範圍</th><th>狀態</th><th>要求</th><th>完成</th><th>部分成熟</th><th>略過</th><th>錯誤</th></tr></thead><tbody>{snapshot.backtestRuns.length ? snapshot.backtestRuns.map((run) => <tr key={run.id}><td>{formatDateTime(run.startedAt)}</td><td>{run.selectionScope}</td><td><span className={`status-pill ${run.status === "completed" ? "selected" : "blocked"}`}>{run.status}</span></td><td>{run.signalsRequested}</td><td>{run.completedCount}</td><td>{run.partialCount}</td><td>{run.skippedCount}</td><td className="risk-cell">{run.errorText || "無"}</td></tr>) : <tr><td colSpan={8}>尚無批次回測紀錄。</td></tr>}</tbody></table></div>
      </section>
    </div>
  );
}

export default function DashboardShell({ snapshot, workflowRuns, snapshotFresh }: { snapshot: DashboardSnapshot; workflowRuns: WorkflowRun[]; snapshotFresh: boolean }) {
  const router = useRouter();
  const [view, setView] = useState<ViewId>("decision");
  const [mobileMenu, setMobileMenu] = useState(false);
  const active = navItems.find((item) => item.id === view) ?? navItems[0];
  const latestModel = (snapshot.aiModels ?? [])[0];
  const aiState = (snapshot.overview.prospectivePredictions ?? 0) > 0 ? "AI 前瞻運作" : latestModel ? "AI 影子就緒" : "AI 尚未就緒";
  const selectView = (next: ViewId) => { setView(next); setMobileMenu(false); };

  useEffect(() => {
    const refresh = () => router.refresh();
    const timer = window.setInterval(refresh, 60_000);
    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") refresh();
    };
    document.addEventListener("visibilitychange", refreshWhenVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, [router]);

  return (
    <main className="app-shell">
      <aside className={`sidebar ${mobileMenu ? "open" : ""}`}>
        <div className="brand"><div className="brand-mark"><Activity size={21} /></div><div><strong>Stock AI Control</strong><span>Quant research OS</span></div><button className="mobile-close" onClick={() => setMobileMenu(false)} aria-label="關閉選單"><X size={18} /></button></div>
        <div className="workspace-label"><span>Workspace</span><strong>TAIWAN EQUITY</strong></div>
        <nav>{navItems.map((item) => { const Icon = item.icon; return <button className={view === item.id ? "active" : ""} onClick={() => selectView(item.id)} key={item.id}><Icon size={19} /><span><strong>{item.label}</strong><small>{item.hint}</small></span><ChevronRight size={15} /></button>; })}</nav>
        <div className="sidebar-footer"><div className={`system-indicator ${snapshotFresh ? "online" : "stale"}`}><span /><div><strong>{snapshotFresh ? "資料服務正常" : "資料需要更新"}</strong><small>{formatDateTime(snapshot.generatedAt)}</small></div></div><div className="sidebar-model"><Bot size={15} /><div><strong>{aiState}</strong><small>{latestModel?.version ?? "尚無模型版本"}</small></div></div><a href="https://github.com/corn92888/Stock_AI_Scanner" target="_blank" rel="noreferrer"><Code2 size={16} />查看原始碼<ExternalLink size={13} /></a></div>
      </aside>
      {mobileMenu && <button className="backdrop" onClick={() => setMobileMenu(false)} aria-label="關閉選單遮罩" />}
      <div className="content-shell">
        <header className="topbar">
          <button className="menu-button" onClick={() => setMobileMenu(true)} aria-label="開啟選單"><Menu size={18} /></button>
          <div className="page-identity"><span className="topbar-eyebrow">TAIWAN EQUITY / {active.id.toUpperCase()}</span><h1>{active.label}</h1><p>{active.hint}</p></div>
          <div className="topbar-context"><div className="context-item"><span>最新交易日</span><strong>{snapshot.overview.latestTradeDate || "--"}</strong></div><div className="context-item"><span>最新模式</span><strong>{modeLabel(snapshot.overview.latestMode)}</strong></div><div className={`topbar-health ${snapshotFresh ? "online" : "stale"}`}><span /><div><strong>{snapshotFresh ? "LIVE DATA" : "STALE DATA"}</strong><small>{formatDateTime(snapshot.generatedAt)}</small></div></div></div>
        </header>
        <div className="main-content">
          {view === "decision" && <DecisionView snapshot={snapshot} />}
          {view === "performance" && <PerformanceView snapshot={snapshot} />}
          {view === "pipeline" && <PipelineView snapshot={snapshot} />}
          {view === "operations" && <OperationsView snapshot={snapshot} workflowRuns={workflowRuns} snapshotFresh={snapshotFresh} />}
        </div>
      </div>
    </main>
  );
}
