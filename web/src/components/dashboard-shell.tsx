"use client";

import { useMemo, useState } from "react";
import {
  Activity,
  ArrowUpRight,
  BarChart3,
  Bot,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  Clock3,
  Code2,
  Database,
  ExternalLink,
  Gauge,
  Menu,
  RefreshCw,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Target,
  Workflow,
  X,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type {
  Candidate,
  DashboardSnapshot,
  Performance,
  WorkflowRun,
} from "@/lib/types";

type ViewId = "decision" | "performance" | "pipeline" | "operations";
type Scope = "selected" | "all" | "rejected";

const navItems: Array<{ id: ViewId; label: string; hint: string; icon: typeof Gauge }> = [
  { id: "decision", label: "決策總覽", hint: "今日候選與風險", icon: Gauge },
  { id: "performance", label: "回測績效", hint: "策略是否真的有效", icon: BarChart3 },
  { id: "pipeline", label: "資料管線", hint: "學習資料完整度", icon: Database },
  { id: "operations", label: "操作中心", hint: "排程與執行狀態", icon: Workflow },
];

const strategyLabels: Record<string, string> = {
  trend: "順勢突破",
  reversal: "低檔爆量",
  wave: "波段蓄勢",
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
  const localTimestamp = value.match(
    /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::\d{2})?$/,
  );
  if (localTimestamp) {
    return `${localTimestamp[2]}/${localTimestamp[3]} ${localTimestamp[4]}:${localTimestamp[5]}`;
  }
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

function modeLabel(mode: string) {
  return mode === "intraday" ? "盤中" : mode === "eod" ? "盤後" : mode;
}

function statusTone(run: WorkflowRun) {
  if (run.status !== "completed") return "running";
  if (run.conclusion === "success") return "success";
  if (run.conclusion === "skipped" || run.conclusion === "cancelled") return "neutral";
  return "danger";
}

function PanelHeader({
  eyebrow,
  title,
  trailing,
}: {
  eyebrow: string;
  title: string;
  trailing?: React.ReactNode;
}) {
  return (
    <div className="panel-header">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h2>{title}</h2>
      </div>
      {trailing}
    </div>
  );
}

function Metric({
  label,
  value,
  detail,
  tone = "default",
}: {
  label: string;
  value: string;
  detail: string;
  tone?: "default" | "positive" | "warning" | "info";
}) {
  return (
    <div className={`metric metric-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}

function CandidateTable({ rows }: { rows: Candidate[] }) {
  if (!rows.length) {
    return <div className="empty-state">這個條件下沒有候選標的。</div>;
  }
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>排名</th><th>標的</th><th>策略</th><th>分數</th><th>訊號價</th>
            <th>漲跌</th><th>五日量比</th><th>成交值</th><th>AI T+3</th><th>AI 新聞</th><th>狀態</th><th>風險</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${row.runAt}-${row.code}-${index}`}>
              <td className="rank-cell">{row.selectionRank ?? row.rawRank ?? index + 1}</td>
              <td>
                <div className="symbol-cell"><strong>{row.code}</strong><span>{row.name}</span></div>
                <small className="muted">{row.industry || "未分類"}</small>
              </td>
              <td><div className="tag-row">{row.strategies.map((item) => <span className="tag" key={item}>{strategyLabels[item] ?? item}</span>)}</div></td>
              <td><strong>{decimal.format(row.score)}</strong></td>
              <td>{decimal.format(row.signalPrice)}</td>
              <td className={row.pctChange >= 0 ? "positive-text" : "negative-text"}>{pct(row.pctChange)}</td>
              <td>{decimal.format(row.volumeRatio5)}x</td>
              <td>{decimal.format(row.turnoverBillion)} 億</td>
              <td>{row.aiProbabilityT3 == null ? "--" : <div className="symbol-cell"><strong>{decimal.format(row.aiProbabilityT3 * 100)}%</strong><span>{row.aiProspective ? (row.aiShadowSelected ? "影子入選" : "影子觀察") : "歷史試跑"}</span></div>}</td>
              <td className="risk-cell" title={row.aiNewsSummary || undefined}>{row.aiNewsSentiment ? `${row.aiNewsSentiment} · ${row.aiNewsEvidenceCount}則` : "--"}</td>
              <td><span className={`status-pill ${row.isSelected ? "selected" : row.tradable ? "eligible" : "blocked"}`}>{row.statusLabel}</span></td>
              <td className="risk-cell">{[...row.riskFlags, ...row.blockReasons].slice(0, 2).join("、") || "無"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DecisionView({ snapshot }: { snapshot: DashboardSnapshot }) {
  const dates = useMemo(() => [...new Set(snapshot.candidates.map((row) => row.tradeDate))].sort().reverse(), [snapshot.candidates]);
  const latestCandidateDate = dates.includes(snapshot.overview.latestTradeDate)
    ? snapshot.overview.latestTradeDate
    : dates[0] || "";
  const [date, setDate] = useState(latestCandidateDate);
  const [scope, setScope] = useState<Scope>("selected");
  const [query, setQuery] = useState("");

  const rows = useMemo(() => snapshot.candidates.filter((row) => {
    if (row.tradeDate !== date) return false;
    if (scope === "selected" && !row.isSelected) return false;
    if (scope === "rejected" && row.isSelected) return false;
    const needle = query.trim().toLowerCase();
    return !needle || row.code.toLowerCase().includes(needle) || row.name.toLowerCase().includes(needle) || row.industry.toLowerCase().includes(needle);
  }).sort((a, b) => (a.selectionRank ?? a.rawRank) - (b.selectionRank ?? b.rawRank)), [snapshot.candidates, date, scope, query]);

  const dayRows = snapshot.candidates.filter((row) => row.tradeDate === date);
  const selected = dayRows.filter((row) => row.isSelected).length;
  const tradable = dayRows.filter((row) => row.tradable).length;
  const chartData = snapshot.dailyCandidates.slice(-36).map((row) => ({ ...row, label: row.tradeDate.slice(5) }));

  return (
    <div className="view-stack">
      <section className="metrics-grid">
        <Metric label="觀察日期" value={date || "--"} detail={`${dayRows.length} 筆候選紀錄`} tone="info" />
        <Metric label="可交易候選" value={number.format(tradable)} detail={`占當日 ${dayRows.length ? Math.round(tradable / dayRows.length * 100) : 0}%`} tone="positive" />
        <Metric label="正式入選" value={number.format(selected)} detail="通過排名與集中度限制" tone="warning" />
        <Metric label="最新執行" value={modeLabel(snapshot.overview.latestMode)} detail={formatDateTime(snapshot.overview.latestRunAt)} />
      </section>

      <section className="panel">
        <PanelHeader eyebrow="Signal desk" title="候選決策表" trailing={<span className="record-count">{rows.length} 筆</span>} />
        <div className="toolbar">
          <label className="select-control"><span>交易日</span><select value={date} onChange={(event) => setDate(event.target.value)}>{dates.map((item) => <option key={item}>{item}</option>)}</select></label>
          <div className="segmented" aria-label="候選範圍">
            {([['selected', '正式入選'], ['all', '全部候選'], ['rejected', '未入選']] as Array<[Scope, string]>).map(([id, label]) => <button key={id} className={scope === id ? "active" : ""} onClick={() => setScope(id)}>{label}</button>)}
          </div>
          <label className="search-control"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="代號、名稱或產業" /></label>
        </div>
        <CandidateTable rows={rows.slice(0, 120)} />
      </section>

      <section className="split-grid">
        <div className="panel chart-panel">
          <PanelHeader eyebrow="Selection history" title="近 36 個交易日候選趨勢" />
          <div className="chart-frame">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={{ top: 12, right: 12, left: -24, bottom: 0 }}>
                <CartesianGrid stroke="#26303a" vertical={false} />
                <XAxis dataKey="label" stroke="#7e8a96" tickLine={false} axisLine={false} minTickGap={28} />
                <YAxis stroke="#7e8a96" tickLine={false} axisLine={false} />
                <Tooltip contentStyle={{ background: "#151c23", border: "1px solid #34404c", borderRadius: 6 }} />
                <Line type="monotone" dataKey="candidates" name="候選" stroke="#49a6ff" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="tradable" name="可交易" stroke="#3bc99d" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="selected" name="正式入選" stroke="#f4b860" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
        <div className="panel funnel-panel">
          <PanelHeader eyebrow="Decision funnel" title="當日篩選漏斗" />
          {[{ label: "程式候選", value: dayRows.length, color: "blue" }, { label: "具交易資格", value: tradable, color: "green" }, { label: "正式入選", value: selected, color: "amber" }].map((item) => (
            <div className="funnel-row" key={item.label}>
              <div><span>{item.label}</span><strong>{item.value}</strong></div>
              <div className="progress"><i className={item.color} style={{ width: `${dayRows.length ? Math.max(4, item.value / dayRows.length * 100) : 0}%` }} /></div>
            </div>
          ))}
          <p className="panel-note"><CircleAlert size={15} />正式入選不等於買進指令，仍須檢查即時價格、流動性與事件風險。</p>
        </div>
      </section>
    </div>
  );
}

function PerformanceView({ snapshot }: { snapshot: DashboardSnapshot }) {
  const [mode, setMode] = useState("all");
  const [sampleScope, setSampleScope] = useState<"formal" | "all">("formal");
  const rows = useMemo(() => snapshot.performance.filter((row) => {
    if (sampleScope === "formal" && !row.isFormalSelection) return false;
    return mode === "all" || row.mode === mode;
  }), [snapshot.performance, mode, sampleScope]);
  const mature = rows.filter((row) => row.maturedHorizon >= 3 && row.netReturn3d != null);
  const grouped = useMemo(() => Object.values(mature.reduce<Record<string, { strategy: string; label: string; values: Performance[] }>>((acc, row) => {
    const key = `${row.mode}-${row.strategy}`;
    acc[key] ??= { strategy: key, label: `${modeLabel(row.mode)} ${row.strategyLabel}`, values: [] };
    acc[key].values.push(row);
    return acc;
  }, {})).map((group) => ({
    strategy: group.label,
    samples: group.values.length,
    netT3: avg(group.values.map((row) => row.netReturn3d)) ?? 0,
    excessT3: avg(group.values.map((row) => row.excessReturn3d)) ?? 0,
    drawdown: avg(group.values.map((row) => row.maxDrawdown3d)) ?? 0,
    success: group.values.length ? group.values.filter((row) => row.successT3).length / group.values.length * 100 : 0,
  })), [mature]);
  const successRate = mature.length ? mature.filter((row) => row.successT3).length / mature.length * 100 : 0;

  return (
    <div className="view-stack">
      <section className="metrics-grid">
        <Metric label="成熟 T+3 樣本" value={number.format(mature.length)} detail={sampleScope === "formal" ? `正式名單已回測 ${snapshot.overview.formalBacktestResults}/${snapshot.overview.formalSelections}` : `全部回測 ${snapshot.overview.backtestResults} 筆`} tone="info" />
        <Metric label="平均淨報酬" value={pct(avg(mature.map((row) => row.netReturn3d)))} detail="已扣模型設定交易成本" tone={(avg(mature.map((row) => row.netReturn3d)) ?? 0) >= 0 ? "positive" : "warning"} />
        <Metric label="平均超額報酬" value={pct(avg(mature.map((row) => row.excessReturn3d)))} detail="相對大盤 T+3" />
        <Metric label="成功率" value={`${decimal.format(successRate)}%`} detail="依策略成功門檻判定" tone="warning" />
      </section>
      <section className="panel chart-panel large-chart">
        <PanelHeader eyebrow="Evidence, not confidence" title="策略三日績效" trailing={<div className="filter-groups"><div className="segmented"><button className={sampleScope === "formal" ? "active" : ""} onClick={() => setSampleScope("formal")}>正式入選</button><button className={sampleScope === "all" ? "active" : ""} onClick={() => setSampleScope("all")}>全部訊號</button></div><div className="segmented">{[["all", "全部"], ["intraday", "盤中"], ["eod", "盤後"]].map(([id, label]) => <button key={id} className={mode === id ? "active" : ""} onClick={() => setMode(id)}>{label}</button>)}</div></div>} />
        <div className="chart-frame tall">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={grouped} margin={{ top: 16, right: 16, left: -12, bottom: 32 }}>
              <CartesianGrid stroke="#26303a" vertical={false} />
              <XAxis dataKey="strategy" stroke="#7e8a96" tickLine={false} axisLine={false} interval={0} angle={-18} textAnchor="end" />
              <YAxis stroke="#7e8a96" tickLine={false} axisLine={false} unit="%" />
              <Tooltip contentStyle={{ background: "#151c23", border: "1px solid #34404c", borderRadius: 6 }} />
              <Bar dataKey="netT3" name="淨報酬" fill="#3bc99d" radius={[3, 3, 0, 0]} />
              <Bar dataKey="excessT3" name="超額報酬" fill="#49a6ff" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </section>
      <section className="panel">
        <PanelHeader eyebrow="Audit trail" title={sampleScope === "formal" ? "正式入選成熟樣本" : "全部成熟樣本"} trailing={<span className="record-count">{mature.length} 筆</span>} />
        <div className="table-scroll">
          <table className="data-table compact-table"><thead><tr><th>日期</th><th>模式 / 策略</th><th>標的</th><th>T+1</th><th>T+3 淨報酬</th><th>T+3 超額</th><th>最大回撤</th><th>判定</th></tr></thead>
            <tbody>{mature.map((row, index) => <tr key={`${row.tradeDate}-${row.code}-${index}`}><td>{row.tradeDate}</td><td>{modeLabel(row.mode)} · {row.strategyLabel}</td><td><strong>{row.code}</strong> {row.name}</td><td>{pct(row.netReturn1d)}</td><td className={(row.netReturn3d ?? 0) >= 0 ? "positive-text" : "negative-text"}>{pct(row.netReturn3d)}</td><td>{pct(row.excessReturn3d)}</td><td className="negative-text">{pct(row.maxDrawdown3d)}</td><td><span className={`status-pill ${row.successT3 ? "selected" : "blocked"}`}>{row.successT3 ? "通過" : "未通過"}</span></td></tr>)}</tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function PipelineView({ snapshot }: { snapshot: DashboardSnapshot }) {
  const stages = [
    { label: "掃描訊號", value: snapshot.overview.signals, icon: Activity, note: "原始策略命中" },
    { label: "候選事件", value: snapshot.overview.candidateEvents, icon: SlidersHorizontal, note: "正規化決策紀錄" },
    { label: "正式入選", value: snapshot.overview.formalSelections, icon: Target, note: "通過政策排序" },
    { label: "特徵快照", value: snapshot.overview.featureSnapshots, icon: Database, note: "模型可用輸入" },
    { label: "前瞻預測", value: snapshot.overview.prospectivePredictions ?? 0, icon: Bot, note: "可追溯即時模型輸出" },
    { label: "成熟結果", value: snapshot.overview.maturePredictionOutcomes ?? 0, icon: CheckCircle2, note: "前瞻預測 T+3 標註" },
  ];
  const max = Math.max(...stages.map((stage) => stage.value), 1);
  const statusData = snapshot.statusCounts.slice(0, 8).map((row) => ({ ...row, short: row.label.slice(0, 6) }));
  const latestModel = (snapshot.aiModels ?? [])[0];
  const loopReady = (snapshot.overview.prospectivePredictions ?? 0) > 0 && (snapshot.overview.maturePredictionOutcomes ?? 0) > 0;
  const modelState = loopReady ? "閉環運作中" : (snapshot.overview.prospectivePredictions ?? 0) > 0 ? "影子預測中" : latestModel ? "模型就緒" : "基礎資料累積中";

  return (
    <div className="view-stack">
      <section className="panel">
        <PanelHeader eyebrow="Learning readiness" title="量化學習資料管線" trailing={<span className={`health-badge ${loopReady ? "healthy" : "building"}`}>{modelState}</span>} />
        <div className="pipeline-grid">
          {stages.map((stage, index) => { const Icon = stage.icon; return <div className="pipeline-stage" key={stage.label}><div className="stage-icon"><Icon size={18} /></div><span>{stage.label}</span><strong>{number.format(stage.value)}</strong><small>{stage.note}</small><div className="progress"><i style={{ width: `${Math.max(stage.value ? 3 : 0, stage.value / max * 100)}%` }} /></div>{index < stages.length - 1 && <ChevronRight className="stage-arrow" size={16} />}</div>; })}
        </div>
        <div className="readiness-callout"><Bot size={21} /><div><strong>{latestModel ? "AI 已進入影子測試，尚未接管正式排名" : "AI 尚未開始自行改權重"}</strong><p>{latestModel ? `最新 ${latestModel.version} 使用 ${latestModel.metrics.samples ?? 0} 筆成熟樣本，先與規則名單平行比較；只有在時序外驗證與前瞻結果都穩定改善後才會升級。` : "先累積可靠標籤，再讓模型進入影子測試，避免用極少樣本追逐雜訊。"}</p></div></div>
      </section>
      {latestModel && <section className="metrics-grid">
        <Metric label="影子模型樣本" value={number.format(latestModel.metrics.samples ?? 0)} detail={`正樣本 ${latestModel.metrics.positive_samples ?? 0}`} tone="info" />
        <Metric label="時序驗證 AUC" value={latestModel.metrics.validation_auc == null ? "NA" : decimal.format(latestModel.metrics.validation_auc)} detail={`${latestModel.metrics.validation_start ?? "--"} 至 ${latestModel.metrics.validation_end ?? "--"}`} tone={(latestModel.metrics.validation_auc ?? 0) >= 0.55 ? "positive" : "warning"} />
        <Metric label="超額預測 MAE" value={latestModel.metrics.validation_excess_mae == null ? "NA" : pct(latestModel.metrics.validation_excess_mae)} detail="驗證區間平均絕對誤差" />
        <Metric label="模型狀態" value={latestModel.status} detail="不影響正式選股政策" tone="warning" />
      </section>}
      <section className="split-grid">
        <div className="panel chart-panel">
          <PanelHeader eyebrow="Policy outcomes" title="候選事件分流" />
          <div className="chart-frame">
            <ResponsiveContainer width="100%" height="100%"><BarChart data={statusData} margin={{ top: 12, right: 8, left: -18, bottom: 30 }}><CartesianGrid stroke="#26303a" vertical={false} /><XAxis dataKey="short" stroke="#7e8a96" tickLine={false} axisLine={false} angle={-15} textAnchor="end" interval={0} /><YAxis stroke="#7e8a96" tickLine={false} axisLine={false} /><Tooltip contentStyle={{ background: "#151c23", border: "1px solid #34404c", borderRadius: 6 }} /><Bar dataKey="count" name="筆數" fill="#49a6ff" radius={[3, 3, 0, 0]} /></BarChart></ResponsiveContainer>
          </div>
        </div>
        <div className="panel run-list-panel">
          <PanelHeader eyebrow="Recent ingestion" title="最近資料寫入" />
          <div className="run-list">{snapshot.scanRuns.slice(0, 8).map((run) => <div className="run-row" key={run.id}><span className={`run-dot ${run.mode}`} /><div><strong>{modeLabel(run.mode)}掃描 · {run.tradeDate}</strong><small>{run.source} · {run.strategyVersion}</small></div><time>{formatDateTime(run.runAt)}</time></div>)}</div>
        </div>
      </section>
    </div>
  );
}

function OperationsView({ snapshot, workflowRuns }: { snapshot: DashboardSnapshot; workflowRuns: WorkflowRun[] }) {
  const intradayUrl = "https://github.com/corn92888/Stock_AI_Scanner/actions/workflows/intraday_scan.yml";
  const dailyUrl = "https://github.com/corn92888/Stock_AI_Scanner/actions/workflows/daily_scan.yml";
  const latestSuccess = workflowRuns.find((run) => run.conclusion === "success");
  const latestFailed = workflowRuns.find((run) => run.conclusion === "failure");
  return (
    <div className="view-stack">
      <section className="metrics-grid">
        <Metric label="GitHub Actions" value={workflowRuns.length ? "已連線" : "無回應"} detail={`${workflowRuns.length} 筆最近執行`} tone={workflowRuns.length ? "positive" : "warning"} />
        <Metric label="最近成功" value={latestSuccess ? modeLabel(latestSuccess.name.includes("Intraday") ? "intraday" : "eod") : "--"} detail={formatDateTime(latestSuccess?.updatedAt)} />
        <Metric label="最近失敗" value={latestFailed ? "需檢查" : "無"} detail={latestFailed ? formatDateTime(latestFailed.updatedAt) : "最近紀錄未見失敗"} tone={latestFailed ? "warning" : "positive"} />
        <Metric label="資料快照" value={snapshot.overview.latestTradeDate || "--"} detail={`產生於 ${formatDateTime(snapshot.generatedAt)}`} tone="info" />
      </section>
      <section className="operations-grid">
        <div className="panel action-panel">
          <PanelHeader eyebrow="Manual control" title="啟動掃描" />
          <a className="action-link primary-action" href={intradayUrl} target="_blank" rel="noreferrer"><span><RefreshCw size={19} /><span><strong>執行盤中掃描</strong><small>前往 GitHub Actions 手動 Run workflow</small></span></span><ArrowUpRight size={18} /></a>
          <a className="action-link" href={dailyUrl} target="_blank" rel="noreferrer"><span><Clock3 size={19} /><span><strong>執行盤後掃描與回測</strong><small>收盤後更新訊號、結果與儀表板快照</small></span></span><ArrowUpRight size={18} /></a>
          <div className="permission-note"><ShieldCheck size={17} /><p>目前控制中心採唯讀部署。啟動權限由 GitHub 登入保護，不在瀏覽器保存管理員憑證。</p></div>
        </div>
        <div className="panel workflow-panel">
          <PanelHeader eyebrow="Live automation" title="最近工作流程" trailing={<a className="icon-link" href="https://github.com/corn92888/Stock_AI_Scanner/actions" target="_blank" rel="noreferrer" aria-label="開啟 GitHub Actions"><Code2 size={17} /></a>} />
          <div className="workflow-list">{workflowRuns.length ? workflowRuns.slice(0, 8).map((run) => <a href={run.url} target="_blank" rel="noreferrer" className="workflow-row" key={run.id}><span className={`run-dot ${statusTone(run)}`} /><div><strong>{run.name}</strong><small>{run.event === "workflow_dispatch" ? "手動執行" : "定時排程"} · {formatDateTime(run.createdAt)}</small></div><span className={`workflow-status ${statusTone(run)}`}>{run.status !== "completed" ? "執行中" : run.conclusion === "success" ? "成功" : run.conclusion ?? "未知"}</span><ExternalLink size={14} /></a>) : <div className="empty-state">GitHub 狀態暫時無法讀取，掃描按鈕仍可使用。</div>}</div>
        </div>
      </section>
      <section className="panel">
        <PanelHeader eyebrow="Backtest jobs" title="最近回測批次" trailing={<span className="record-count">{snapshot.backtestRuns.length} 筆</span>} />
        <div className="table-scroll"><table className="data-table compact-table"><thead><tr><th>開始時間</th><th>狀態</th><th>要求樣本</th><th>完成</th><th>部分成熟</th><th>略過</th><th>錯誤</th></tr></thead><tbody>{snapshot.backtestRuns.length ? snapshot.backtestRuns.map((run) => <tr key={run.id}><td>{formatDateTime(run.startedAt)}</td><td><span className={`status-pill ${run.status === "completed" ? "selected" : "blocked"}`}>{run.status}</span></td><td>{run.signalsRequested}</td><td>{run.completedCount}</td><td>{run.partialCount}</td><td>{run.skippedCount}</td><td className="risk-cell">{run.errorText || "無"}</td></tr>) : <tr><td colSpan={7}>尚無批次回測紀錄。</td></tr>}</tbody></table></div>
      </section>
    </div>
  );
}

export default function DashboardShell({ snapshot, workflowRuns, snapshotFresh }: { snapshot: DashboardSnapshot; workflowRuns: WorkflowRun[]; snapshotFresh: boolean }) {
  const [view, setView] = useState<ViewId>("decision");
  const [mobileMenu, setMobileMenu] = useState(false);
  const active = navItems.find((item) => item.id === view) ?? navItems[0];
  const selectView = (id: ViewId) => { setView(id); setMobileMenu(false); };
  return (
    <main className="app-shell">
      <aside className={`sidebar ${mobileMenu ? "open" : ""}`}>
        <div className="brand"><div className="brand-mark"><Activity size={21} /></div><div><strong>Stock AI Control</strong><span>Quant operations</span></div><button className="mobile-close" onClick={() => setMobileMenu(false)} aria-label="關閉選單"><X size={20} /></button></div>
        <nav>{navItems.map((item) => { const Icon = item.icon; return <button className={view === item.id ? "active" : ""} onClick={() => selectView(item.id)} key={item.id}><Icon size={19} /><span><strong>{item.label}</strong><small>{item.hint}</small></span><ChevronRight size={15} /></button>; })}</nav>
        <div className="sidebar-footer"><div className={`system-indicator ${snapshotFresh ? "online" : "stale"}`}><span /><div><strong>{snapshotFresh ? "資料服務正常" : "資料需要更新"}</strong><small>{formatDateTime(snapshot.generatedAt)}</small></div></div><a href="https://github.com/corn92888/Stock_AI_Scanner" target="_blank" rel="noreferrer"><Code2 size={16} />查看原始碼<ExternalLink size={13} /></a></div>
      </aside>
      {mobileMenu && <button className="backdrop" aria-label="關閉選單" onClick={() => setMobileMenu(false)} />}
      <div className="content-shell">
        <header className="topbar"><button className="menu-button" onClick={() => setMobileMenu(true)} aria-label="開啟選單"><Menu size={20} /></button><div><span className="topbar-eyebrow">{active.hint}</span><h1>{active.label}</h1></div><div className="topbar-status"><span className={snapshotFresh ? "live-dot" : "stale-dot"} /><div><strong>{snapshot.overview.latestTradeDate || "無交易日"}</strong><small>資料更新 {formatDateTime(snapshot.generatedAt)}</small></div></div></header>
        <div className="main-content">
          {view === "decision" && <DecisionView snapshot={snapshot} />}
          {view === "performance" && <PerformanceView snapshot={snapshot} />}
          {view === "pipeline" && <PipelineView snapshot={snapshot} />}
          {view === "operations" && <OperationsView snapshot={snapshot} workflowRuns={workflowRuns} />}
        </div>
      </div>
    </main>
  );
}
