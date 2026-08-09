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
  Globe2,
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
  WalletCards,
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

import type { Candidate, DashboardSnapshot, GlobalMarketInstrument, PaperTrade, Performance, WorkflowRun } from "@/lib/types";

type ViewId = "decision" | "market" | "performance" | "paper" | "pipeline" | "operations";
type Scope = "latest" | "selected" | "all" | "rejected";
type CandidateSort = "rank" | "score" | "turnover" | "volume" | "ai" | "excess";
type SortDirection = "asc" | "desc";

const viewIds: ViewId[] = ["decision", "market", "performance", "paper", "pipeline", "operations"];

function isViewId(value: string | undefined): value is ViewId {
  return Boolean(value && viewIds.includes(value as ViewId));
}

const navItems: Array<{ id: ViewId; label: string; hint: string; icon: typeof Gauge }> = [
  { id: "decision", label: "決策工作台", hint: "候選、共識與風險", icon: Gauge },
  { id: "market", label: "全球市場", hint: "跨市場脈動與傳導", icon: Globe2 },
  { id: "performance", label: "策略驗證", hint: "報酬、回撤與穩定性", icon: BarChart3 },
  { id: "paper", label: "模擬資金", hint: "規則與 AI 資金競賽", icon: WalletCards },
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

const experimentFamilyLabels: Record<string, string> = {
  rule_baseline: "正式規則",
  trend: "順勢突破",
  reversal: "低檔爆量",
  wave: "波段蓄勢",
  replay_baseline: "五年正式基準",
  market_regime: "市場確認",
  industry_confirmation: "產業確認",
  volume_confirmation: "量能放大",
  volume_quality: "平衡量能",
  extension_control: "延伸控制",
  breadth_consensus: "廣度共識",
  quality_stack: "綜合品質層",
  cross_sectional_return_ranker: "橫斷面 AI 排名",
  cross_sectional_alpha_ranker: "Alpha 棄權排名",
  cross_sectional_peer_ranker: "同儕相對排名",
  walk_forward_cross_sectional_challenger: "擴展視窗挑戰者",
  generation_2_institutional_ablation: "法人增量消融",
  generation_2_institutional_interaction: "法人條件互動",
};

const experimentReasonLabels: Record<string, string> = {
  insufficient_trade_dates: "交易日不足",
  insufficient_trades: "交易筆數不足",
  non_positive_net_return: "成本後報酬未轉正",
  non_positive_excess_return: "超額報酬未轉正",
  probabilistic_sharpe_below_gate: "PSR 未達標",
  drawdown_gate_failed: "回撤超標",
  fold_stability_gate_failed: "分折穩定度不足",
  development_gate_failed: "開發期未通過",
  validation_gate_failed: "驗證期未通過",
  no_formal_net_lift: "成本後報酬未勝過正式規則",
  no_formal_excess_lift: "超額報酬未勝過正式規則",
  no_institutional_net_lift: "法人特徵未增加成本後報酬",
  no_institutional_excess_lift: "法人特徵未增加超額報酬",
  prospective_generation_required: "仍需全新前瞻世代驗證",
};

const challengerReasonLabels: Record<string, string> = {
  insufficient_oof_trade_dates: "樣本外交易日不足",
  insufficient_challenger_trades: "AI 入選筆數不足",
  non_positive_challenger_net_return: "AI 成本後報酬未轉正",
  non_positive_challenger_excess_return: "AI 超額報酬未轉正",
  challenger_does_not_beat_champion: "AI 未勝過規則冠軍",
  challenger_drawdown_gate_failed: "AI 回撤超標",
  challenger_fold_stability_failed: "AI 分折穩定度不足",
};

const strategyChallengerReasonLabels: Record<string, string> = {
  insufficient_trade_dates: "參與交易日不足",
  insufficient_trades: "成交樣本不足",
  non_positive_net_return: "成本後交易報酬未轉正",
  non_positive_excess_return: "交易超額未轉正",
  non_positive_daily_net_return: "含空手日淨報酬未轉正",
  non_positive_daily_excess_return: "含空手日超額未轉正",
  probabilistic_sharpe_below_gate: "多重檢定後 PSR 未達標",
  drawdown_gate_failed: "最大回撤超標",
  fold_stability_gate_failed: "跨期穩定度不足",
  no_formal_net_lift: "淨報酬未勝過正式規則",
  no_formal_excess_lift: "超額未勝過正式規則",
  insufficient_walk_forward_folds: "擴展視窗折數不足",
  reserved_holdout_gate_failed: "保留區間驗證失敗",
  reserved_holdout_not_selected: "未獲選進入保留區間",
};

const executionMethodLabels: Record<string, string> = {
  next_open: "隔日開盤",
  next_ohlc4_proxy: "隔日 OHLC4",
  next_close: "隔日收盤",
  pullback_2pct_3d: "三日回檔 2%",
};

const learnabilityDiagnosisLabels: Record<string, string> = {
  candidate_opportunity_gap: "候選池缺乏成本後機會",
  feature_rankability_gap: "現有特徵無法穩定排序",
  execution_fill_gap: "進場成交率不足",
  portfolio_construction_gap: "門檻與組合建構未捕捉機會",
  historical_edge_not_promotable: "歷史診斷有優勢，仍不可升級",
  not_available: "尚未完成稽核",
};

const learningDiagnosisLabels: Record<string, string> = {
  hard_drawdown_stop: "前瞻策略已觸發硬性回撤停止線",
  early_drawdown_breach: "早期前瞻回撤已超過微型實盤容忍值",
  prospective_evidence_thin: "前瞻結案樣本仍不足",
  prospective_edge_negative: "前瞻成本後與超額優勢均為負",
  selection_policy_not_adding_value: "正式入選沒有勝過落選對照組",
  evidence_building: "策略優勢仍在累積驗證",
  not_available: "尚未完成第一次自動研究週期",
};

const learningDimensionLabels: Record<string, string> = {
  strategy: "策略來源",
  volume: "五日量比",
  turnover: "成交值",
  score: "規則分數",
  extension: "當日漲幅",
  intraday_position: "日內收位",
  defense_distance: "防守距離",
  market_regime: "市場狀態",
  industry_breadth: "產業廣度",
  industry: "產業",
};

const learningLayerLabels: Record<string, string> = {
  data: "資料層",
  model: "模型層",
  selection: "選股層",
  portfolio: "組合層",
};

const governedChallengerStatusLabels: Record<string, string> = {
  draft: "待審查",
  collecting_data: "累積前瞻資料",
  implementation_required: "等待實作",
  evaluated: "未通過樣本外門檻",
  promotion_review: "等待人工升級審查",
  rejected: "已拒絕",
};

const governedChallengerReasonLabels: Record<string, string> = {
  insufficient_mature_samples: "成熟樣本不足",
  insufficient_trade_dates: "獨立交易日不足",
  insufficient_point_in_time_coverage: "時間點基本面覆蓋不足",
  insufficient_walk_forward_folds: "樣本外分折不足",
  fundamentals_do_not_add_oof_excess_return: "基本面尚未增加樣本外超額報酬",
};

const capitalIntentReasonLabels: Record<string, string> = {
  governance_paused: "治理停止線已觸發",
  capital_stage_shadow: "仍在影子觀察階段",
  market_regime_blocked: "市場狀態不允許新增曝險",
  liquidity_below_minimum: "流動性未達最低要求",
  non_positive_predicted_alpha: "預測超額報酬不為正",
  invalid_signal_price: "訊號價格無效",
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

function rate(value: number | null | undefined) {
  if (value == null) return "--";
  return `${decimal.format(value)}%`;
}

function money(value: number | null | undefined) {
  if (value == null) return "--";
  return `NT$${formatNumeric(value, 0, 0)}`;
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

function marketStatusLabel(status: GlobalMarketInstrument["dataStatus"]) {
  return {
    fresh: "更新中",
    delayed: "延遲",
    closed: "休市",
    stale: "過期",
    unavailable: "暫無資料",
    not_connected: "待接來源",
  }[status];
}

function marketStatusTone(status: GlobalMarketInstrument["dataStatus"]) {
  if (status === "fresh") return "selected";
  if (status === "delayed" || status === "closed") return "neutral";
  return "blocked";
}

function marketPrice(row: GlobalMarketInstrument) {
  if (row.price == null) return "--";
  const digits = row.assetClass === "fx" ? 3 : row.price < 100 ? 2 : 1;
  return formatNumeric(row.price, digits, digits);
}

function statusTone(run: WorkflowRun) {
  if (run.status !== "completed") return "running";
  if (run.conclusion === "success") return "success";
  if (run.conclusion === "skipped" || run.conclusion === "cancelled") return "neutral";
  return "danger";
}

function workflowTriggerLabel(run: WorkflowRun) {
  if (run.displayTitle.includes("vercel-cron")) return "Vercel 排程";
  if (run.event === "workflow_dispatch") return "手動執行";
  return "定時排程";
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

function CandidateDrawer({ candidate, onClose, researchOnly }: { candidate: Candidate; onClose: () => void; researchOnly: boolean }) {
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
          <span className={`status-pill ${candidate.isSelected ? "selected" : candidate.tradable ? "eligible" : "blocked"}`}>{candidate.isSelected && researchOnly ? "研究候選" : candidate.statusLabel}</span>
          <span className="policy-code">{candidate.policyVersion}</span>
          {candidate.aiProspective ? <span className="status-pill eligible">AI 前瞻</span> : candidate.aiProbabilityT3 != null ? <span className="status-pill neutral">歷史試跑</span> : null}
        </div>

        <section className="drawer-section">
          <h3>決策矩陣</h3>
          <div className="decision-matrix">
            <div><span>規則分數</span><strong>{decimal.format(candidate.score)}</strong><small>{candidate.isSelected ? researchOnly ? "研究候選" : "正式入選" : "未入選"}</small></div>
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

function CandidateTable({ rows, sort, direction, onSort, onSelect, researchOnly }: { rows: Candidate[]; sort: CandidateSort; direction: SortDirection; onSort: (field: CandidateSort) => void; onSelect: (candidate: Candidate) => void; researchOnly: boolean }) {
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
            <td><div className="consensus-cell"><span className={`status-pill ${row.isSelected ? "selected" : row.tradable ? "eligible" : "blocked"}`}>{row.isSelected && researchOnly ? "研究候選" : row.statusLabel}</span>{agreement && <span className="agreement-mark"><Zap size={11} />共識</span>}</div></td>
            <td className="risk-cell" title={risks.join("、") || undefined}>{risks.length ? <span className="risk-count"><TriangleAlert size={12} />{risks.slice(0, 2).join("、")}</span> : <span className="clean-mark"><CheckCircle2 size={12} />清潔</span>}</td>
          </tr>;
        })}</tbody>
      </table>
    </div>
  );
}

function DecisionView({ snapshot }: { snapshot: DashboardSnapshot }) {
  const integrityGate = snapshot.researchHealth.integrityGate;
  const researchOnly = !integrityGate.formalRecommendationsAllowed;
  const dates = useMemo(() => [...new Set(snapshot.candidates.map((row) => row.tradeDate))].sort().reverse(), [snapshot.candidates]);
  const latestCandidateDate = dates.includes(snapshot.overview.latestTradeDate) ? snapshot.overview.latestTradeDate : dates[0] || "";
  const [date, setDate] = useState(latestCandidateDate);
  const [scope, setScope] = useState<Scope>("latest");
  const [query, setQuery] = useState("");
  const [strategy, setStrategy] = useState("all");
  const [riskFilter, setRiskFilter] = useState("all");
  const [sort, setSort] = useState<CandidateSort>("rank");
  const [direction, setDirection] = useState<SortDirection>("asc");
  const [selectedCandidate, setSelectedCandidate] = useState<Candidate | null>(null);

  const dayRows = useMemo(() => snapshot.candidates.filter((row) => row.tradeDate === date), [snapshot.candidates, date]);
  const latestRunAt = useMemo(() => dayRows.reduce((latest, row) => row.runAt > latest ? row.runAt : latest, ""), [dayRows]);
  const rows = useMemo(() => dayRows.filter((row) => {
    if (scope === "latest" && row.runAt !== latestRunAt) return false;
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
  }), [dayRows, latestRunAt, scope, strategy, riskFilter, query, sort, direction]);

  const handleSort = (field: CandidateSort) => {
    if (field === sort) setDirection((current) => current === "asc" ? "desc" : "asc");
    else { setSort(field); setDirection(field === "rank" ? "asc" : "desc"); }
  };
  const resetFilters = () => { setScope("latest"); setQuery(""); setStrategy("all"); setRiskFilter("all"); setSort("rank"); setDirection("asc"); };

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
        <Metric label={researchOnly ? "研究候選" : "正式入選"} value={number.format(selected.length)} detail={`成交值中位 ${median(selected.map((row) => row.turnoverBillion)) == null ? "--" : decimal.format(median(selected.map((row) => row.turnoverBillion)) ?? 0)} 億`} tone="warning" icon={Target} />
        <Metric label="AI 已評估" value={number.format(aiEvaluated.length)} detail={`${agreement} 檔規則與 AI 共識`} tone={aiEvaluated.length ? "info" : "default"} icon={Bot} />
        <Metric label="風險阻擋" value={number.format(dayRows.filter((row) => !row.tradable).length)} detail="未通過硬性交易條件" tone="danger" icon={ShieldCheck} />
      </section>

      <div className="validation-banner"><TriangleAlert size={18} /><div><strong>{integrityGate.status === "blocked" ? "研究完整性閘門阻擋正式推薦" : integrityGate.status === "review_required" ? "量化證據待人工核准" : "研究完整性閘門已核准"}</strong><p>{researchOnly ? `目前僅通過 ${integrityGate.passedChecks}/${integrityGate.totalChecks} 項完整性檢查；下方入選結果只代表研究排序，不是買進指令。` : `已通過 ${integrityGate.passedChecks}/${integrityGate.totalChecks} 項檢查與人工核准；仍須依即時價格及個人風險承受度決策。`}</p></div></div>

      <section className="panel decision-panel">
        <PanelHeader eyebrow="Decision workspace" title="候選決策工作台" description="同一交易日的規則訊號、流動性、AI 影子預測與風險證據" trailing={<div className="panel-actions"><span className="record-count">{scope === "latest" && latestRunAt ? `${formatDateTime(latestRunAt)} · ` : ""}{rows.length} / {dayRows.length} 筆</span><IconButton label="重設篩選" onClick={resetFilters}><RotateCcw size={16} /></IconButton><IconButton label="匯出目前候選 CSV" onClick={() => downloadCandidates(rows, date)}><Download size={16} /></IconButton></div>} />
        <div className="toolbar decision-toolbar">
          <label className="select-control"><span>交易日</span><select value={date} onChange={(event) => setDate(event.target.value)}>{dates.map((item) => <option key={item}>{item}</option>)}</select></label>
          <div className="segmented" aria-label="候選範圍">{([['latest', '最新批次'], ['selected', researchOnly ? '每日研究' : '每日正式'], ['all', '全日候選'], ['rejected', '未入選']] as Array<[Scope, string]>).map(([id, label]) => <button key={id} className={scope === id ? "active" : ""} onClick={() => setScope(id)}>{label}</button>)}</div>
          <label className="select-control"><span>策略</span><select value={strategy} onChange={(event) => setStrategy(event.target.value)}><option value="all">全部策略</option><option value="trend">順勢突破</option><option value="reversal">低檔爆量</option><option value="wave">波段蓄勢</option></select></label>
          <label className="select-control"><span>風險</span><select value={riskFilter} onChange={(event) => setRiskFilter(event.target.value)}><option value="all">全部狀態</option><option value="clean">僅看清潔</option><option value="risk">僅看有風險</option></select></label>
          <label className="search-control"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜尋代號、名稱或產業" /></label>
        </div>
        <CandidateTable rows={rows.slice(0, 160)} sort={sort} direction={direction} onSort={handleSort} onSelect={setSelectedCandidate} researchOnly={researchOnly} />
      </section>

      <section className="analysis-grid">
        <div className="panel chart-panel">
          <PanelHeader eyebrow="Selection flow" title="近 36 個交易日訊號漏斗" description="觀察候選供給、可交易率與正式名單是否異常漂移" />
          <div className="chart-frame"><ResponsiveContainer width="100%" height="100%"><AreaChart data={chartData} margin={{ top: 14, right: 16, left: -20, bottom: 0 }}><defs><linearGradient id="candidateFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#5fb3d9" stopOpacity={0.22} /><stop offset="100%" stopColor="#5fb3d9" stopOpacity={0} /></linearGradient></defs><CartesianGrid stroke="#2b2e31" vertical={false} /><XAxis dataKey="label" stroke="#777d82" tickLine={false} axisLine={false} minTickGap={26} /><YAxis stroke="#777d82" tickLine={false} axisLine={false} /><Tooltip contentStyle={tooltipStyle} /><Area type="monotone" dataKey="candidates" name="候選" stroke="#5fb3d9" fill="url(#candidateFill)" strokeWidth={2} /><Line type="monotone" dataKey="tradable" name="可交易" stroke="#55c29a" strokeWidth={2} dot={false} /><Line type="monotone" dataKey="selected" name={researchOnly ? "研究候選" : "正式入選"} stroke="#e2ae5f" strokeWidth={2} dot={false} /></AreaChart></ResponsiveContainer></div>
        </div>
        <div className="panel exposure-panel">
          <PanelHeader eyebrow="Exposure monitor" title="產業訊號集中度" description="當日候選出現次數，不代表資金配置" />
          <div className="exposure-list">{topIndustries.map(([industry, count]) => <div className="exposure-row" key={industry}><div><span>{industry}</span><strong>{count}</strong></div><div className="progress"><i style={{ width: `${dayRows.length ? Math.max(4, count / dayRows.length * 100) : 0}%` }} /></div></div>)}</div>
          <p className="panel-note"><CircleAlert size={15} />{researchOnly ? "完整性閘門尚未通過，研究候選不得視為買進指令。" : "入選結果仍須核對最新報價、公告與事件風險。"}</p>
        </div>
      </section>
      {selectedCandidate && <CandidateDrawer candidate={selectedCandidate} onClose={() => setSelectedCandidate(null)} researchOnly={researchOnly} />}
    </div>
  );
}

function GlobalMarketView({ snapshot }: { snapshot: DashboardSnapshot }) {
  const market = snapshot.globalMarket;
  const groups = ["全部", ...new Set(market.instruments.map((row) => row.group))];
  const [group, setGroup] = useState("全部");
  const rows = group === "全部" ? market.instruments : market.instruments.filter((row) => row.group === group);
  const chartData = market.history.map((point) => ({
    ...point,
    label: formatDateTime(point.snapshotAt),
  }));
  const riskTone = market.score >= 62 ? "positive" : market.score <= 38 ? "danger" : "warning";
  const nightMarket = market.instruments.find((row) => row.key === "taifex_night");
  const connectedNight = Boolean(nightMarket?.price != null);

  return (
    <div className="view-stack">
      <section className="metrics-grid metrics-grid-five">
        <Metric label="全球風險分數" value={decimal.format(market.score)} detail={market.regimeLabel} tone={riskTone} icon={Globe2} />
        <Metric label="台股傳導偏向" value={market.taiwanBiasLabel} detail={`影子分數 ${decimal.format(market.taiwanBiasScore)}`} tone={riskTone} icon={Activity} />
        <Metric label="資料覆蓋" value={`${decimal.format(market.quality.coveragePct)}%`} detail={`${market.quality.available} / ${market.quality.total} 個市場`} tone={market.quality.coveragePct >= 85 ? "positive" : "warning"} icon={Database} />
        <Metric label="開市資料可用" value={`${decimal.format(market.quality.activeFreshPct)}%`} detail="更新中或可接受延遲" tone={market.quality.activeFreshPct >= 80 ? "positive" : "danger"} icon={RefreshCw} />
        <Metric label="台指期夜盤" value={connectedNight ? "CONNECTED" : "PENDING"} detail={connectedNight ? "已納入風險分數" : "等待授權行情來源"} tone={connectedNight ? "positive" : "warning"} icon={Clock3} />
      </section>

      <section className="market-policy-band">
        <div><ShieldCheck size={18} /><div><strong>跨市場脈動目前以影子模式運作</strong><span>先驗證資料穩定性與預測增益，再決定是否加入正式排名，避免用未驗證關聯改寫選股。</span></div></div>
        <span className="policy-code">{market.modelVersion}</span>
      </section>

      <section className="analysis-grid market-analysis-grid">
        <div className="panel chart-panel">
          <PanelHeader eyebrow="Regime tape" title="全球風險與台股傳導軌跡" description="每次收集保留點時資料；50 為中性，不代表買賣指令" trailing={<span className="record-count">{chartData.length} 筆</span>} />
          <div className="chart-frame"><ResponsiveContainer width="100%" height="100%"><LineChart data={chartData} margin={{ top: 15, right: 18, left: -17, bottom: 0 }}><CartesianGrid stroke="#2b2e31" vertical={false} /><XAxis dataKey="label" stroke="#777d82" tickLine={false} axisLine={false} minTickGap={38} /><YAxis domain={[0, 100]} stroke="#777d82" tickLine={false} axisLine={false} /><Tooltip contentStyle={tooltipStyle} /><ReferenceLine y={50} stroke="#72787d" strokeDasharray="4 4" /><Line type="monotone" dataKey="score" name="全球風險" stroke="#5fb3d9" strokeWidth={2} dot={chartData.length < 12} isAnimationActive={false} /><Line type="monotone" dataKey="taiwanBiasScore" name="台股傳導" stroke="#e2ae5f" strokeWidth={2} dot={false} isAnimationActive={false} /></LineChart></ResponsiveContainer></div>
        </div>
        <div className="panel component-panel">
          <PanelHeader eyebrow="Transmission map" title="風險構面" description="各群組以自身有效資料標準化，觀察方向與覆蓋" />
          <div className="component-list">{market.components.length ? market.components.map((component) => <div className="component-row" key={component.key}><div><span>{component.name}</span><strong className={component.score >= 50 ? "positive-text" : "negative-text"}>{decimal.format(component.score)}</strong></div><div className="regime-track"><i className={component.score >= 50 ? "positive" : "negative"} style={{ width: `${Math.max(2, component.score)}%` }} /></div><small>{component.coverage} / {component.total} 個有效市場</small></div>) : <div className="empty-state">完成第一次跨市場收集後，這裡會顯示風險構面。</div>}</div>
        </div>
      </section>

      <section className="panel market-board">
        <PanelHeader eyebrow="Cross-asset monitor" title="跨市場行情矩陣" description="價格時間與來源品質分開顯示，避免把休市收盤價誤認為即時行情" trailing={<span className="record-count">{rows.length} 個市場</span>} />
        <div className="toolbar market-toolbar"><div className="segmented market-groups" aria-label="市場分類">{groups.map((item) => <button key={item} className={group === item ? "active" : ""} onClick={() => setGroup(item)}>{item}</button>)}</div></div>
        <div className="table-scroll"><table className="data-table compact-table market-table"><thead><tr><th>市場</th><th>區域</th><th>狀態</th><th>最新價格</th><th>當期漲跌</th><th>5 日</th><th>衝擊 Z</th><th>台股貢獻</th><th>行情時間</th><th>資料來源</th></tr></thead><tbody>{rows.length ? rows.map((row) => <tr key={row.key}><td><div className="symbol-cell"><strong>{row.name}</strong><span>{row.symbol ?? row.key}</span></div></td><td>{row.region}</td><td><span className={`status-pill ${marketStatusTone(row.dataStatus)}`}>{marketStatusLabel(row.dataStatus)}</span></td><td className="mono-cell">{marketPrice(row)}</td><td className={(row.pctChange ?? 0) >= 0 ? "positive-text" : "negative-text"}>{pct(row.pctChange)}</td><td className={(row.return5d ?? 0) >= 0 ? "positive-text" : "negative-text"}>{pct(row.return5d)}</td><td>{row.shockZ == null ? "--" : decimal.format(row.shockZ)}</td><td className={row.impactPoints >= 0 ? "positive-text" : "negative-text"}>{row.modelWeight ? `${row.impactPoints > 0 ? "+" : ""}${decimal.format(row.impactPoints)}` : "觀察"}</td><td><div className="time-cell"><strong>{formatDateTime(row.marketAt)}</strong><span>{row.latencyMinutes == null ? "--" : `${number.format(row.latencyMinutes)} 分鐘差`}</span></div></td><td><div className="source-cell"><strong>{row.sourceName}</strong><span>{row.sourceTier}</span></div></td></tr>) : <tr><td colSpan={10}>尚無跨市場觀測。</td></tr>}</tbody></table></div>
      </section>

      <section className="analysis-grid equal-grid">
        <div className="panel driver-panel">
          <PanelHeader eyebrow="Top drivers" title="主要傳導因子" description="只解釋目前風險分數，不等於單一因果證明" />
          <div className="driver-list">{market.drivers.length ? market.drivers.map((driver, index) => <div className="driver-row" key={driver.key}><span>{String(index + 1).padStart(2, "0")}</span><div><strong>{driver.name}</strong><small>{driver.reason}</small></div><div><strong className={driver.tone === "positive" ? "positive-text" : "negative-text"}>{driver.impactPoints > 0 ? "+" : ""}{decimal.format(driver.impactPoints)}</strong><small>{pct(driver.pctChange)}</small></div></div>) : <div className="empty-state">目前沒有足夠資料產生驅動因子。</div>}</div>
        </div>
        <div className="panel quality-panel">
          <PanelHeader eyebrow="Data governance" title="資料品質與缺口" description="正式決策前必須知道哪些資料延遲、休市或尚未授權" />
          <div className="quality-summary"><div><span>資料等級</span><strong>{market.quality.status}</strong></div><div><span>正式排名</span><strong className={market.quality.formalRankingEnabled ? "positive-text" : "warning-text"}>{market.quality.formalRankingEnabled ? "已啟用" : "未啟用"}</strong></div></div>
          <ul className="quality-warnings">{market.quality.warnings.map((warning) => <li key={warning}><CircleAlert size={15} />{warning}</li>)}</ul>
          <p className="market-asof">觀測建立：{formatDateTime(market.snapshotAt)}</p>
        </div>
      </section>
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
  const progress = target === 0
    ? (passed ? 100 : 3)
    : lowerIsBetter ? target / Math.max(value, target) * 100 : value / target * 100;
  return <div className="gate-row"><div><span>{passed ? <CircleCheck size={15} /> : <Clock3 size={15} />}{label}</span><strong className={passed ? "positive-text" : ""}>{display}</strong></div><div className="progress"><i className={passed ? "green" : "amber"} style={{ width: `${Math.min(100, Math.max(3, progress))}%` }} /></div><small>上線門檻 {lowerIsBetter ? "≤" : "≥"} {target}</small></div>;
}

function PipelineView({ snapshot }: { snapshot: DashboardSnapshot }) {
  const research = snapshot.researchQuality;
  const health = snapshot.researchHealth;
  const integrityGate = health.integrityGate;
  const institutional = snapshot.institutionalFlow;
  const experiments = snapshot.researchExperiments ?? [];
  const learnability = snapshot.learnabilityAudit;
  const learnabilityPrimary = learnability.primary;
  const strategyLab = snapshot.strategyChallenger;
  const alphaLive = snapshot.alphaLive;
  const alphaForward = snapshot.alphaForward;
  const capital = snapshot.capitalGovernance;
  const learning = snapshot.learningCycle;
  const learningForward = learning.metrics.alphaForward ?? {};
  const learningSelected = learning.metrics.recentSelected;
  const learningRejected = learning.metrics.recentRejected;
  const fundamentalData = learning.fundamentalData;
  const governedChallengers = learning.challengers.slice(0, 8);
  const activeHypotheses = learning.hypotheses
    .filter((row) => row.seenThisCycle)
    .slice(0, 8);
  const negativeAttributions = [...learning.attributions]
    .filter((row) => row.scope === "formal_selected" && row.sampleCount >= 10 && (row.meanNetReturn ?? 0) < 0)
    .sort((left, right) => (left.meanNetReturn ?? 0) - (right.meanNetReturn ?? 0) || right.sampleCount - left.sampleCount)
    .slice(0, 10);
  const nextCapitalStage = capital.stages.find((stage) => stage.key === capital.nextStage);
  const isAlphaV2 = strategyLab.version === "alpha_liquid_universe_walk_forward_v2";
  const strategyLeader = strategyLab.candidateLeaderboard[0];
  const executionMatrix = useMemo(() => (
    [...strategyLab.executionMatrix]
      .sort((left, right) => right.validation.meanDailyExcessReturn - left.validation.meanDailyExcessReturn)
  ), [strategyLab.executionMatrix]);
  const attribution = snapshot.replayAttribution;
  const [attributionDimension, setAttributionDimension] = useState("strategy");
  const [attributionScope, setAttributionScope] = useState<"all" | "selected" | "rejected">("all");
  const attributionRows = useMemo(() => (
    attribution.rows
      .filter((row) => row.dimension === attributionDimension && row.selectionScope === attributionScope)
      .sort((left, right) => left.sortOrder - right.sortOrder || right.sampleCount - left.sampleCount)
      .slice(0, attributionDimension === "industry" ? 20 : 40)
  ), [attribution.rows, attributionDimension, attributionScope]);
  const stages = [
    { label: "掃描訊號", value: snapshot.overview.signals, icon: Activity, note: "原始策略命中" },
    { label: "候選事件", value: snapshot.overview.candidateEvents, icon: SlidersHorizontal, note: "正規化決策紀錄" },
    { label: "候選回測", value: snapshot.overview.candidateOutcomes, icon: Target, note: "含落選對照組" },
    { label: "特徵快照", value: snapshot.overview.featureSnapshots, icon: Database, note: "模型可用輸入" },
    { label: "前瞻預測", value: snapshot.overview.prospectivePredictions ?? 0, icon: Bot, note: "即時模型輸出" },
    { label: "成熟結果", value: snapshot.overview.maturePredictionOutcomes ?? 0, icon: CheckCircle2, note: "前瞻 T+3 標註" },
  ];
  const max = Math.max(...stages.map((stage) => stage.value), 1);
  const statusData = snapshot.statusCounts.slice(0, 7).map((row) => ({ ...row, short: row.label.slice(0, 6) }));
  const latestModel = (snapshot.aiModels ?? [])[0];
  const latestChallenger = (snapshot.modelChallengers ?? [])[0];
  const universeQualityLabel = {
    verified: "官方成員資格完整",
    partial: "官方資料部分缺漏",
    unverified: "股票池尚未驗證",
  }[health.replayUniverseQualityStatus] ?? "股票池尚未驗證";
  const samples = latestModel?.metrics.samples ?? 0;
  const positives = latestModel?.metrics.positive_samples ?? 0;
  const auc = latestModel?.metrics.validation_auc ?? 0;
  const mae = latestModel?.metrics.validation_excess_mae ?? 99;
  const prospective = snapshot.overview.prospectivePredictions ?? 0;
  const matureProspective = snapshot.overview.maturePredictionOutcomes ?? 0;
  const rulePaper = snapshot.paperAccounts.find((account) => account.strategyKind === "rule");
  const aiPaper = snapshot.paperAccounts.find((account) => account.strategyKind === "ai");
  const paperClosed = aiPaper?.closedTrades ?? 0;
  const paperEdge = paperClosed >= 100
    && aiPaper?.comparisonReturnPct != null
    && rulePaper?.comparisonReturnPct != null
    ? aiPaper.comparisonReturnPct - rulePaper.comparisonReturnPct
    : -Infinity;
  const selectionLift = research.selectionNetLift3d ?? -Infinity;
  const economicEdge = (research.formalMeanNetReturn3d ?? -Infinity) > 0
    && (research.formalMeanExcessReturn3d ?? -Infinity) > 0
    && selectionLift > 0;
  const allGatesPassed = Boolean(latestChallenger?.qualified)
    && samples >= 500 && positives >= 50 && auc >= 0.6 && mae <= 3
    && research.matureCandidateOutcomes >= 500 && research.matureRejectedOutcomes >= 250
    && research.uniqueTradeDates >= 120 && matureProspective >= 150 && economicEdge
    && paperClosed >= 100 && paperEdge > 0;
  const featureCoverage = snapshot.overview.candidateEvents ? snapshot.overview.featureSnapshots / snapshot.overview.candidateEvents * 100 : 0;

  return (
    <div className="view-stack">
      <section className="model-hero-band">
        <div><span className="eyebrow">Model governance</span><h2>{allGatesPassed ? "模型符合候選升級門檻" : latestModel ? "影子模型運作中，尚未允許接管排名" : "模型資料仍在建立"}</h2><p>{latestModel ? `${latestModel.version} · 訓練區間 ${latestModel.trainingStart} 至 ${latestModel.trainingEnd}` : "尚無可用模型版本"}</p></div>
        <div className={`governance-state ${allGatesPassed ? "ready" : "shadow"}`}><Bot size={20} /><span>{allGatesPassed ? "PROMOTION REVIEW" : "SHADOW ONLY"}</span></div>
      </section>

      <section className="panel">
        <PanelHeader eyebrow="Automated research cycle" title="策略自我修正閉環" description="每天盤後把成熟結果轉成失敗歸因與預先登記的 challenger 假設；不會直接改正式規則" trailing={<span className={`status-pill ${learning.status === "evidence_review" ? "selected" : learning.status === "redesign_required" || learning.status === "paused" ? "blocked" : "neutral"}`}>{learning.status === "redesign_required" ? "REDESIGN REQUIRED" : learning.status === "paused" ? "PAUSED" : learning.status === "collecting" ? "COLLECTING" : learning.status === "evidence_review" ? "EVIDENCE REVIEW" : "NOT RUN"}</span>} />
        <div className="readiness-callout"><Code2 size={19} /><div><strong>{learningDiagnosisLabels[learning.primaryDiagnosis] ?? learning.primaryDiagnosis}</strong><p>{learning.cycleDate ? `研究週期 ${learning.cycleDate} · 證據 ${learning.evidenceStartDate ?? "--"} 至 ${learning.evidenceEndDate ?? "--"} · 新成熟 ${number.format(learning.newMaturedOutcomes)} 筆。系統只建立研究提案；任何變更仍須固定版本、樣本外驗證與全新前瞻模擬。` : "盤後流程完成後會建立第一份自動研究日誌。"}</p></div></div>
      </section>

      <section className="metrics-grid metrics-grid-five">
        <Metric label="前瞻 Alpha" value={pct(learningForward.total_return_pct)} detail={`${number.format(learningForward.closed_trades ?? 0)} 筆結案 · 超額 ${pct(learningForward.avg_excess_return_pct)}`} tone={(learningForward.total_return_pct ?? 0) > 0 && (learningForward.avg_excess_return_pct ?? 0) > 0 ? "positive" : "danger"} icon={TrendingUp} />
        <Metric label="前瞻最大回撤" value={pct(learningForward.max_drawdown_pct)} detail={`PSR ${learningForward.probabilistic_sharpe == null ? "--" : decimal.format(learningForward.probabilistic_sharpe)}`} tone={(learningForward.max_drawdown_pct ?? -99) > -6 ? "positive" : "danger"} icon={TrendingDown} />
        <Metric label="近期選股增值" value={pct(learning.metrics.selectionNetLift)} detail={`入選 ${learningSelected?.samples ?? 0} / 落選 ${learningRejected?.samples ?? 0} 筆`} tone={(learning.metrics.selectionNetLift ?? 0) > 0 ? "positive" : "danger"} icon={Target} />
        <Metric label="本輪研究假設" value={number.format(activeHypotheses.length)} detail="只建立 challenger 提案" tone={activeHypotheses.length ? "warning" : "info"} icon={Bot} />
        <Metric label="官方基本面觀測" value={number.format(fundamentalData.observations)} detail={`${number.format(fundamentalData.codes)} 檔 · 最新 ${fundamentalData.snapshotDate ?? "--"}`} tone={fundamentalData.status === "completed" ? "positive" : fundamentalData.status === "partial" ? "warning" : "danger"} icon={Database} />
      </section>

      <section className="analysis-grid equal-grid">
        <div className="panel">
          <PanelHeader eyebrow="Point-in-time fundamentals" title="官方基本面資料閘門" description="TWSE／TPEx 估值、月營收與季度 EPS；只允許決策當下已知的版本進入模型" trailing={<span className={`status-pill ${fundamentalData.status === "completed" ? "selected" : fundamentalData.status === "partial" ? "neutral" : "blocked"}`}>{fundamentalData.status === "completed" ? "OFFICIAL READY" : fundamentalData.status === "partial" ? "PARTIAL" : "NOT READY"}</span>} />
          <div className="table-scroll"><table className="data-table compact-table"><tbody><tr><th>估值資料日</th><td><strong>{fundamentalData.valuationDate ?? "--"}</strong></td><th>月營收期間</th><td><strong>{fundamentalData.revenuePeriod ?? "--"}</strong></td></tr><tr><th>EPS 期間</th><td><strong>{fundamentalData.epsPeriod ?? "--"}</strong></td><th>最後得知時間</th><td>{fundamentalData.latestKnownAt ?? "--"}</td></tr><tr><th>官方觀測</th><td>{number.format(fundamentalData.observations)} 筆</td><th>股票覆蓋</th><td>{number.format(fundamentalData.codes)} 檔</td></tr></tbody></table></div>
          {fundamentalData.warnings.length ? <div className="readiness-callout"><CircleAlert size={18} /><div><strong>資料來源部分缺漏</strong><p>{fundamentalData.warnings.slice(0, 3).join("；")}</p></div></div> : null}
        </div>
        <div className="panel">
          <PanelHeader eyebrow="Versioned challenger lab" title="已治理 Challenger 實驗" description="版本、核准範圍、前瞻覆蓋與失敗原因均固定保存；不會直接改正式排名" trailing={<span className="record-count">{governedChallengers.length} 組</span>} />
          {governedChallengers.length ? <div className="table-scroll"><table className="data-table compact-table"><thead><tr><th>實驗</th><th>核准</th><th>成熟證據</th><th>狀態</th></tr></thead><tbody>{governedChallengers.map((row) => <tr key={row.experimentVersion}><td><strong>{row.hypothesisKey}</strong><br /><small>{row.experimentVersion.slice(-10)}</small></td><td><span className={`status-pill ${row.approvalStatus === "approved" ? "selected" : "neutral"}`}>{row.approvalStatus === "approved" ? "SHADOW" : "待審查"}</span><br /><small>{row.approvedScope ?? "未核准"}</small></td><td><strong>{number.format(row.sampleCount)} 筆 / {number.format(row.tradeDates)} 日</strong><br /><small>PIT 覆蓋 {row.featureCoveragePct == null ? "--" : `${decimal.format(row.featureCoveragePct)}%`}</small></td><td><span className={`status-pill ${row.status === "promotion_review" ? "selected" : row.status === "evaluated" || row.status === "rejected" ? "blocked" : "neutral"}`}>{governedChallengerStatusLabels[row.status] ?? row.status}</span><br /><small>{row.rejectionReasons.slice(0, 2).map((reason) => governedChallengerReasonLabels[reason] ?? reason).join("、") || "尚無拒絕原因"}</small></td></tr>)}</tbody></table></div> : <div className="empty-state">研究提案尚未轉成版本化 Challenger。</div>}
        </div>
      </section>

      <section className="analysis-grid equal-grid">
        <div className="panel">
          <PanelHeader eyebrow="Pre-registered hypotheses" title="下一輪 Challenger 假設" description="優先級來自資料缺口、前瞻風險與成熟樣本；提案不等於啟用" trailing={<span className="record-count">{activeHypotheses.length} 項</span>} />
          {activeHypotheses.length ? <div className="table-scroll"><table className="data-table compact-table"><thead><tr><th>優先</th><th>研究層</th><th>假設</th><th>累積出現</th><th>狀態</th></tr></thead><tbody>{activeHypotheses.map((row) => <tr key={row.hypothesisKey}><td><strong>{row.priority}</strong></td><td>{learningLayerLabels[row.targetLayer] ?? row.targetLayer}</td><td><strong>{row.title}</strong><br /><small>{row.rationale}</small></td><td>{row.occurrences}</td><td><span className={`status-pill ${row.status === "approved_for_shadow" ? "selected" : "neutral"}`}>{row.status === "proposed" ? "待審查" : row.status === "approved_for_shadow" ? "已核准 Shadow 實驗" : row.status}</span></td></tr>)}</tbody></table></div> : <div className="empty-state">目前沒有達到最低證據要求的新假設。</div>}
        </div>
        <div className="panel">
          <PanelHeader eyebrow="Failure attribution" title="近期主要負向切片" description="只顯示至少 10 筆成熟正式入選；小樣本不會觸發自動改版" trailing={<span className="record-count">{negativeAttributions.length} 組</span>} />
          {negativeAttributions.length ? <div className="table-scroll"><table className="data-table compact-table"><thead><tr><th>維度</th><th>切片</th><th>樣本</th><th>淨報酬</th><th>超額</th><th>回撤</th></tr></thead><tbody>{negativeAttributions.map((row) => <tr key={`${row.dimension}-${row.bucketKey}`}><td>{learningDimensionLabels[row.dimension] ?? row.dimension}</td><td><strong>{row.bucketLabel}</strong></td><td>{row.sampleCount}</td><td className="negative-text">{pct(row.meanNetReturn)}</td><td className={(row.meanExcessReturn ?? 0) >= 0 ? "positive-text" : "negative-text"}>{pct(row.meanExcessReturn)}</td><td className="negative-text">{pct(row.meanDrawdown)}</td></tr>)}</tbody></table></div> : <div className="empty-state">近期尚無足量負向切片。</div>}
        </div>
      </section>

      <section className="panel">
        <PanelHeader eyebrow="Research integrity gate" title="正式推薦完整性閘門" description="先驗證資料成熟度、成本後績效、落選對照組與歷史重播，再決定是否允許使用正式推薦語意" trailing={<span className={`status-pill ${integrityGate.formalRecommendationsAllowed ? "selected" : "blocked"}`}>{integrityGate.status === "approved" ? "FORMAL APPROVED" : integrityGate.status === "review_required" ? "REVIEW REQUIRED" : "RESEARCH ONLY"}</span>} />
        <div className="readiness-callout"><ShieldCheck size={19} /><div><strong>{integrityGate.passedChecks}/{integrityGate.totalChecks} 項證據檢查通過</strong><p>{integrityGate.formalRecommendationsAllowed ? "量化證據與人工核准均已完成；系統可顯示正式推薦，但不會自動下單。" : integrityGate.evidenceReady ? "量化證據已通過，仍需人工設定 FORMAL_RECOMMENDATIONS_APPROVED 才能解除研究模式。" : "證據尚未支持可交易優勢；決策頁、報告與 Telegram 一律把入選結果降級為研究候選。"}</p></div></div>
        <div className="table-scroll"><table className="data-table compact-table"><thead><tr><th>完整性檢查</th><th>目前證據</th><th>門檻</th><th>判定</th></tr></thead><tbody>{integrityGate.checks.map((check) => <tr key={check.key}><td><strong>{check.label}</strong></td><td>{check.detail}</td><td>{check.requirement}</td><td><span className={`status-pill ${check.passed ? "selected" : "blocked"}`}>{check.passed ? "通過" : "阻擋"}</span></td></tr>)}</tbody></table></div>
      </section>

      <section className="metrics-grid metrics-grid-five">
        <Metric label="候選回測覆蓋" value={`${decimal.format(research.outcomeCoveragePct)}%`} detail={`${snapshot.overview.candidateOutcomes}/${snapshot.overview.candidateEvents} 筆`} tone={research.outcomeCoveragePct >= 90 ? "positive" : "warning"} icon={Database} />
        <Metric label="落選對照組" value={number.format(research.matureRejectedOutcomes)} detail="驗證篩選是否增值" tone={research.matureRejectedOutcomes >= 250 ? "positive" : "warning"} icon={SlidersHorizontal} />
        <Metric label="舊規則 T+3 淨報酬" value={pct(research.formalMeanNetReturn3d)} detail={`超額 ${pct(research.formalMeanExcessReturn3d)}`} tone={economicEdge ? "positive" : "danger"} icon={TrendingUp} />
        <Metric label="選股增值" value={pct(research.selectionNetLift3d)} detail={`落選組 ${pct(research.rejectedMeanNetReturn3d)}`} tone={selectionLift > 0 ? "positive" : "danger"} icon={Target} />
        <Metric label="獨立交易日" value={number.format(research.uniqueTradeDates)} detail={research.executionVersion} tone={research.uniqueTradeDates >= 120 ? "positive" : "warning"} icon={Clock3} />
      </section>

      {learnabilityPrimary ? <>
        <section className="metrics-grid metrics-grid-five">
          <Metric label="候選池 Oracle 上限" value={pct(learnabilityPrimary.oracle.meanDailyNetReturn)} detail={`T+${learnabilityPrimary.holdingHorizon} · 事後每日 Top 3`} tone={(learnabilityPrimary.oracle.meanDailyNetReturn ?? 0) > 0 ? "positive" : "danger"} icon={Target} />
          <Metric label="AI 樣本外淨報酬" value={pct(learnabilityPrimary.model.meanDailyNetReturn)} detail={`超額 ${pct(learnabilityPrimary.model.meanDailyExcessReturn)}`} tone={(learnabilityPrimary.model.meanDailyNetReturn ?? 0) > 0 && (learnabilityPrimary.model.meanDailyExcessReturn ?? 0) > 0 ? "positive" : "danger"} icon={Bot} />
          <Metric label="樣本外 Rank IC" value={learnabilityPrimary.rankability.meanRankIc == null ? "--" : decimal.format(learnabilityPrimary.rankability.meanRankIc)} detail={`${number.format(learnabilityPrimary.rankability.icDates)} 個橫斷面交易日`} tone={(learnabilityPrimary.rankability.rankIcCi95Low ?? -1) > 0 ? "positive" : "warning"} icon={BarChart3} />
          <Metric label="預測前後分位差" value={pct(learnabilityPrimary.rankability.topBottomExcessSpread)} detail="前 20% 減後 20% 超額" tone={(learnabilityPrimary.rankability.topBottomExcessSpread ?? 0) > 0 ? "positive" : "danger"} icon={Layers3} />
          <Metric label="Oracle 捕捉率" value={rate(learnabilityPrimary.model.opportunityCapturePct)} detail={`重疊 ${rate(learnabilityPrimary.model.oracleOverlapRatePct)}`} tone={(learnabilityPrimary.model.opportunityCapturePct ?? 0) > 0 ? "positive" : "danger"} icon={Gauge} />
        </section>

        <section className="panel">
          <PanelHeader eyebrow="Candidate learnability audit" title="候選池可學習性稽核" description={`訓練 ${learnability.trainingStart ?? "--"} 至 ${learnability.trainingEnd ?? "--"} · 驗證 ${learnability.validationStart ?? "--"} 至 ${learnability.validationEnd ?? "--"} · 保留 ${number.format(learnability.reservedHoldoutTradeDates)} 個 holdout 交易日`} trailing={<span className="record-count">{learnability.auditVersion}</span>} />
          <div className="readiness-callout"><CircleAlert size={19} /><div><strong>主要診斷：{learnabilityDiagnosisLabels[learnability.primaryDiagnosis] ?? learnability.primaryDiagnosis}</strong><p>Oracle 是不可交易的事後上限，只用來確認候選池是否存在機會。所有 AI 指標均由 development 訓練、validation 評估；正式排名維持關閉，既有 holdout 未被讀取。</p></div></div>
          <div className="table-scroll"><table className="data-table compact-table strategy-table"><thead><tr><th>進場 / 持有</th><th>候選池</th><th>Oracle 上限</th><th>AI 淨報酬 / 超額</th><th>Rank IC</th><th>前後分位超額差</th><th>成交 / 捕捉</th><th>診斷</th></tr></thead><tbody>{learnability.rows.map((row) => <tr key={row.key}><td><strong>{executionMethodLabels[row.entryMethod] ?? row.entryMethod}</strong><br /><small>T+{row.holdingHorizon} · Q80</small></td><td><strong>{number.format(row.pool.filledCandidates)} 筆</strong><br /><small>正報酬 {rate(row.pool.positiveCandidateRate)} · 可湊 Top 3 {rate(row.pool.profitableTopKCapacityRatePct)}</small></td><td className={(row.oracle.meanDailyNetReturn ?? 0) >= 0 ? "positive-text" : "negative-text"}>{pct(row.oracle.meanDailyNetReturn)}<br /><small>超額 {pct(row.oracle.meanDailyExcessReturn)}</small></td><td className={(row.model.meanDailyNetReturn ?? 0) >= 0 && (row.model.meanDailyExcessReturn ?? 0) >= 0 ? "positive-text" : "negative-text"}>{pct(row.model.meanDailyNetReturn)} / {pct(row.model.meanDailyExcessReturn)}<br /><small>{number.format(row.model.trades)} 筆 · 參與 {rate(row.model.participationRatePct)}</small></td><td>{row.rankability.meanRankIc == null ? "--" : decimal.format(row.rankability.meanRankIc)}<br /><small>{row.rankability.rankIcCi95Low == null || row.rankability.rankIcCi95High == null ? "區間不足" : `${decimal.format(row.rankability.rankIcCi95Low)} ～ ${decimal.format(row.rankability.rankIcCi95High)}`}</small></td><td className={(row.rankability.topBottomExcessSpread ?? 0) >= 0 ? "positive-text" : "negative-text"}>{pct(row.rankability.topBottomExcessSpread)}<br /><small>{number.format(row.rankability.topBottomDates)} 日</small></td><td>成交 {rate(row.model.selectedFillRatePct)}<br /><small>捕捉 {rate(row.model.opportunityCapturePct)} · 重疊 {rate(row.model.oracleOverlapRatePct)}</small></td><td><span className={`status-pill ${row.diagnosis === "historical_edge_not_promotable" ? "selected" : "blocked"}`}>{learnabilityDiagnosisLabels[row.diagnosis] ?? row.diagnosis}</span></td></tr>)}</tbody></table></div>
        </section>
      </> : null}

      <section className="panel">
        <PanelHeader eyebrow="Purged walk-forward lab" title="策略挑戰者治理" description={isAlphaV2 ? "新版模型直接從歷史流動性股票池選股，不再先經過舊三策略；只有預先保留區間也通過才可進入影子資金" : "六個鎖定模型逐季擴展訓練；選擇階段不讀最終 holdout，通過後也只能進入全新前瞻影子測試"} trailing={<span className={`status-pill ${strategyLab.status === "prospective_shadow_ready" ? "selected" : "blocked"}`}>{strategyLab.status === "prospective_shadow_ready" ? "SHADOW READY" : strategyLab.status === "not_evaluated" ? "NOT EVALUATED" : "CASH / NO DEPLOYMENT"}</span>} />
        <div className="readiness-callout"><ShieldCheck size={19} /><div><strong>{strategyLab.status === "prospective_shadow_ready" ? `挑戰者 ${strategyLab.selectedExperimentKey} 可進入前瞻影子測試` : "目前沒有可部署的選股優勢，資金決策維持 CASH"}</strong><p>{strategyLab.status === "not_evaluated" ? "等待策略挑戰流程第一次完成。" : `${isAlphaV2 ? "全市場流動性股票池" : "舊規則候選池"} · 前段通過 ${strategyLab.prequalifiedCandidates} 組 · 最終通過 ${strategyLab.qualifiedCandidates}/${strategyLab.candidateCount} 組。最佳診斷候選 ${strategyLab.diagnosticLeaderKey ?? "--"} 只代表最接近門檻，不等於可買進；研究資料 ${strategyLab.datasetStart ?? "--"} 至 ${strategyLab.datasetEnd ?? "--"}，${number.format(strategyLab.datasetRows)} 筆。`}</p></div></div>
      </section>

      <section className="metrics-grid metrics-grid-five">
        <Metric label="資金模式" value={strategyLab.recommendationMode === "cash" ? "CASH" : "SHADOW"} detail="未通過時禁止硬選股票" tone={strategyLab.recommendationMode === "cash" ? "warning" : "positive"} icon={WalletCards} />
        <Metric label="合格挑戰者" value={`${strategyLab.qualifiedCandidates}/${strategyLab.candidateCount}`} detail={`鎖定比較 ${strategyLab.lockedComparisons} 組`} tone={strategyLab.qualifiedCandidates ? "positive" : "danger"} icon={Bot} />
        <Metric label="領先者每日淨報酬" value={pct(strategyLeader?.meanDailyNetReturn)} detail={`超額 ${pct(strategyLeader?.meanDailyExcessReturn)}`} tone={(strategyLeader?.meanDailyNetReturn ?? 0) > 0 && (strategyLeader?.meanDailyExcessReturn ?? 0) > 0 ? "positive" : "danger"} icon={TrendingUp} />
        <Metric label={isAlphaV2 ? "相對現金基準" : "相對正式規則"} value={pct(strategyLeader?.formalExcessLift)} detail={`淨增值 ${pct(strategyLeader?.formalNetLift)}`} tone={(strategyLeader?.formalExcessLift ?? 0) > 0 && (strategyLeader?.formalNetLift ?? 0) > 0 ? "positive" : "danger"} icon={Target} />
        <Metric label="保留 Holdout" value={strategyLeader?.holdoutEvaluated ? strategyLeader.holdoutQualified ? "通過" : "失敗" : number.format(strategyLeader?.reservedHoldoutTradeDates ?? 0)} detail={strategyLeader?.holdoutEvaluated ? `淨 ${pct(strategyLeader.holdoutMeanDailyNetReturn)} · 超額 ${pct(strategyLeader.holdoutMeanDailyExcessReturn)}` : `${strategyLeader?.reservedHoldoutStart ?? "--"} 起尚未開封`} tone={strategyLeader?.holdoutQualified ? "positive" : strategyLeader?.holdoutEvaluated ? "danger" : "warning"} icon={Database} />
      </section>

      <section className="panel">
        <PanelHeader eyebrow="Prospective Alpha v2" title="最新全市場模擬訊號" description="每日盤後以同一個通過 holdout 的模型掃描流動性股票池；隔日開盤模擬成交並於 T+10 收盤結算" trailing={<span className={`status-pill ${alphaLive.status === "active" ? "selected" : alphaLive.status === "abstained" || alphaLive.status === "not_run" ? "neutral" : "blocked"}`}>{alphaLive.status === "active" ? "PAPER SIGNAL" : alphaLive.status === "abstained" ? "CASH TODAY" : alphaLive.status === "not_run" ? "NOT RUN" : alphaLive.status === "paused" ? "GOVERNANCE PAUSED" : "BLOCKED"}</span>} />
        <div className="readiness-callout"><Bot size={19} /><div><strong>{alphaLive.status === "active" ? `${alphaLive.signalDate} 產生 ${alphaLive.selectedCount} 筆 Alpha v2 模擬訊號` : alphaLive.status === "abstained" ? `${alphaLive.signalDate} 模型信心不足，模擬資金維持現金` : alphaLive.status === "paused" ? "前瞻治理已暫停建立新的模擬部位" : "等待第一輪 Alpha v2 盤後全市場評分"}</strong><p>信心 {alphaLive.confidence == null ? "--" : decimal.format(alphaLive.confidence)} / 門檻 {alphaLive.confidenceThreshold == null ? "--" : decimal.format(alphaLive.confidenceThreshold)} · 原始股票 {number.format(alphaLive.universeCount)} 檔 · 流動性與資料合格 {number.format(alphaLive.eligibleCount)} 檔。這裡只顯示前瞻模擬，不代表真錢買進核准。</p></div></div>
        {alphaLive.signals.length ? <div className="table-scroll"><table className="data-table compact-table strategy-table"><thead><tr><th>順位</th><th>標的</th><th>產業</th><th>訊號價</th><th>預測 T+10 超額</th><th>模擬配置</th><th>狀態</th></tr></thead><tbody>{alphaLive.signals.map((row) => <tr key={`${alphaLive.signalDate}-${row.code}`}><td>#{row.rankOrder}</td><td><strong>{row.code}</strong> {row.name}</td><td>{row.industry}</td><td>{decimal.format(row.signalPrice)}</td><td className={row.predictedAlpha >= 0 ? "positive-text" : "negative-text"}>{pct(row.predictedAlpha)}</td><td>{rate(row.allocationWeight * 100)}</td><td><span className="status-pill selected">隔日開盤模擬</span><br /><small>固定持有 T+{row.holdingHorizon}</small></td></tr>)}</tbody></table></div> : <div className="empty-state">本日沒有可建立的 Alpha v2 模擬部位。</div>}
      </section>

      <section className="panel">
        <PanelHeader eyebrow="Forward capital governance" title="Alpha 前瞻驗證控制台" description={`凍結起始日 ${alphaForward.evidenceStartDate} · 同股票池、同 T+10、同資金限制比較模型與四個對照政策`} trailing={<span className={`status-pill ${alphaForward.state === "HEALTHY" ? "selected" : alphaForward.state === "PAUSED" ? "blocked" : "neutral"}`}>{alphaForward.state}</span>} />
        <div className="readiness-callout"><ShieldCheck size={19} /><div><strong>{alphaForward.state === "HEALTHY" ? "前瞻證據已通過全部量化門檻" : alphaForward.state === "PAUSED" ? "資料完整性或回撤停止線已觸發，新模擬倉暫停" : alphaForward.state === "WATCH" ? "前瞻績效進入觀察，維持影子資金" : "正在累積獨立前瞻證據，尚未開放真實資金"}</strong><p>最新訊號 {alphaForward.latestSignalDate ?? "--"} · 資料品質 {alphaForward.dataQualityStatus.toUpperCase()} · 報價覆蓋 {alphaForward.quoteHealth.coveragePct == null ? "--" : `${decimal.format(alphaForward.quoteHealth.coveragePct)}%`} · 完整候選池 {number.format(alphaForward.candidatePoolRows)} 筆。{alphaForward.warnings.join(" ")}</p></div></div>
        <div className="metrics-grid metrics-grid-five">
          <Metric label="前瞻總報酬" value={pct(alphaForward.totalReturnPct)} detail={`平均超額 ${pct(alphaForward.avgExcessReturnPct)}`} tone={alphaForward.totalReturnPct > 0 && (alphaForward.avgExcessReturnPct ?? 0) > 0 ? "positive" : "warning"} icon={TrendingUp} />
          <Metric label="最大回撤" value={pct(alphaForward.maxDrawdownPct)} detail="停止線 -12%" tone={alphaForward.maxDrawdownPct <= -12 ? "danger" : alphaForward.maxDrawdownPct <= -8 ? "warning" : "positive"} icon={ShieldCheck} />
          <Metric label="決策日 / 結案" value={`${alphaForward.decisionDays} / ${alphaForward.closedTrades}`} detail={`門檻 ${alphaForward.minimumDecisionDays} 日 / ${alphaForward.minimumClosedTrades} 筆`} tone={alphaForward.decisionDays >= alphaForward.minimumDecisionDays && alphaForward.closedTrades >= alphaForward.minimumClosedTrades ? "positive" : "warning"} icon={Clock3} />
          <Metric label="PSR" value={alphaForward.probabilisticSharpe == null ? "--" : `${decimal.format(alphaForward.probabilisticSharpe * 100)}%`} detail="門檻 95%" tone={(alphaForward.probabilisticSharpe ?? 0) >= 0.95 ? "positive" : "warning"} icon={Target} />
          <Metric label="合格股票池覆蓋" value={`${decimal.format(alphaForward.universeCoveragePct)}%`} detail={`${number.format(alphaForward.candidatePoolRows)} 筆同日候選`} tone={alphaForward.universeCoveragePct >= 20 && alphaForward.dataQualityStatus !== "critical" ? "positive" : "warning"} icon={Database} />
        </div>
        <div className="table-scroll"><table className="data-table compact-table strategy-table"><thead><tr><th>治理門檻</th><th>目前值</th><th>要求</th><th>判定</th></tr></thead><tbody>{alphaForward.gates.length ? alphaForward.gates.map((gate) => <tr key={gate.key}><td><strong>{gate.label}</strong></td><td>{gate.value == null ? "--" : decimal.format(gate.value)}</td><td>{gate.requirement}</td><td><span className={`status-pill ${gate.passed ? "selected" : "neutral"}`}>{gate.passed ? "通過" : "累積中"}</span></td></tr>) : <tr><td colSpan={4}>下一次自動監控會建立治理門檻快照。</td></tr>}</tbody></table></div>
      </section>

      <section className="panel">
        <PanelHeader
          eyebrow="Staged capital control"
          title="實盤資金階梯"
          description="階段可以自動降級，但真單傳送永遠不會因模型分數自動開啟；每筆訂單仍需人工核准與持倉核對"
          trailing={<span className={`status-pill ${capital.stage === "PRODUCTION" ? "selected" : capital.stage === "PAUSED" ? "blocked" : "neutral"}`}>{capital.stage}</span>}
        />
        <div className="readiness-callout">
          <ShieldCheck size={19} />
          <div>
            <strong>{capital.stage === "PAUSED" ? "資金停止線已觸發，所有新單歸零" : capital.stage === "SHADOW" ? `目前只允許影子觀察；下一階段為 ${capital.nextStageLabel ?? "微型實盤"}` : `${capital.stageLabel}額度已通過，但仍只產生人工核准預覽`}</strong>
            <p>參考資金 {money(capital.referenceCapital)} · 執行鏈 {capital.operationalReady ? "完整" : "尚未通過升級檢查"} · 持倉同步 {capital.positionLedgerConnected ? "已連線" : "未連線"} · 真單傳送固定為 {capital.liveTransmissionEnabled ? "開啟" : "關閉"}。{nextCapitalStage ? `下一階段需要 ${nextCapitalStage.minDecisionDays} 個決策日與 ${nextCapitalStage.minClosedTrades} 筆結案。` : "目前已位於最高資金階段。"}</p>
          </div>
        </div>
        <div className="metrics-grid metrics-grid-five">
          <Metric label="目前階段" value={capital.stageLabel} detail={capital.policyVersion} tone={capital.stage === "PAUSED" ? "danger" : capital.stage === "SHADOW" ? "warning" : "positive"} icon={Layers3} />
          <Metric label="策略總曝險上限" value={rate(capital.maxStrategyWeight * 100)} detail={money(capital.referenceCapital * capital.maxStrategyWeight)} tone={capital.maxStrategyWeight > 0 ? "positive" : "warning"} icon={WalletCards} />
          <Metric label="單檔上限" value={rate(capital.maxPositionWeight * 100)} detail={money(capital.referenceCapital * capital.maxPositionWeight)} tone={capital.maxPositionWeight > 0 ? "positive" : "warning"} icon={Target} />
          <Metric label="最大同時持倉" value={`${capital.maxPositions} 檔`} detail="同產業另受既有曝險限制" tone={capital.maxPositions > 0 ? "positive" : "warning"} icon={SlidersHorizontal} />
          <Metric label="可送真單" value={capital.liveTransmissionEnabled ? "YES" : "NO"} detail="尚未接券商與人工簽核" tone={capital.liveTransmissionEnabled ? "positive" : "warning"} icon={ShieldCheck} />
        </div>
        <div className="table-scroll">
          <table className="data-table compact-table strategy-table">
            <thead><tr><th>階段</th><th>決策日 / 結案</th><th>總曝險</th><th>單檔</th><th>持倉數</th><th>門檻進度</th><th>判定</th></tr></thead>
            <tbody>{capital.stages.length ? capital.stages.map((stage) => <tr key={stage.key}><td><strong>{stage.label}</strong><br /><small>{stage.key}</small></td><td>{stage.minDecisionDays} / {stage.minClosedTrades}</td><td>{rate(stage.maxStrategyWeight * 100)}</td><td>{rate(stage.maxPositionWeight * 100)}</td><td>{stage.maxPositions}</td><td>{decimal.format(stage.progressPct)}%</td><td><span className={`status-pill ${stage.passed ? "selected" : "neutral"}`}>{stage.passed ? "通過" : "尚未通過"}</span></td></tr>) : <tr><td colSpan={7}>下一次自動流程會建立資金階梯快照。</td></tr>}</tbody>
          </table>
        </div>
        <div className="table-scroll">
          <table className="data-table compact-table strategy-table">
            <thead><tr><th>訊號日</th><th>標的</th><th>訊號價</th><th>模型 T+10 超額</th><th>市場閘門</th><th>階段預覽權重</th><th>實際允許額度</th><th>股數</th><th>決策</th></tr></thead>
            <tbody>{capital.orderIntents.length ? capital.orderIntents.map((intent) => <tr key={`${intent.signalDate}-${intent.code}`}><td>{intent.signalDate}</td><td><strong>{intent.code}</strong> {intent.name}<br /><small>{intent.industry}</small></td><td>{decimal.format(intent.signalPrice)}</td><td className={(intent.predictedAlpha ?? 0) > 0 ? "positive-text" : "negative-text"}>{pct(intent.predictedAlpha)}</td><td><span className={`status-pill ${intent.marketGatePassed ? "selected" : "blocked"}`}>{intent.marketGatePassed ? "通過" : "阻擋"}</span><br /><small>大盤 20 日 {pct(intent.marketContext.market_return_20d)}</small></td><td>{rate(intent.proposedWeight * 100)}</td><td>{money(intent.maxNotional)}<br /><small>{rate(intent.targetWeight * 100)}</small></td><td>{intent.suggestedQuantity || "--"}</td><td><span className={`status-pill ${intent.decisionStatus === "manual_approval_required" ? "selected" : "blocked"}`}>{intent.decisionStatus === "manual_approval_required" ? "等待人工核准" : "禁止建立真單"}</span><br /><small>{intent.reasonCodes.map((reason) => capitalIntentReasonLabels[reason] ?? reason).join("、") || "全部預交易檢查通過"}</small></td></tr>) : <tr><td colSpan={9}>目前沒有可建立的訂單意圖。</td></tr>}</tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <PanelHeader eyebrow="Prospective control groups" title="Alpha 帳戶公平比較" description="模型冠軍、嚴格反追高、市場狀態、純動能與固定隨機基準只使用當日可得候選池" trailing={<span className="record-count">{alphaForward.accounts.length} 組</span>} />
        <div className="table-scroll"><table className="data-table compact-table strategy-table"><thead><tr><th>帳戶</th><th>政策</th><th>決策日 / 結案</th><th>持有 / 等待</th><th>總報酬</th><th>平均淨報酬</th><th>平均超額</th><th>勝率</th><th>最大回撤</th></tr></thead><tbody>{alphaForward.accounts.length ? alphaForward.accounts.map((account) => <tr key={account.accountKey}><td><strong>{account.name}</strong><br /><small>{account.role === "benchmark" ? "凍結基準" : "挑戰者"}</small></td><td>{account.selectionPolicy ?? "--"}</td><td>{account.signalDates} / {account.closedTrades}</td><td>{account.openPositions} / {account.pendingOrders}</td><td className={account.totalReturnPct >= 0 ? "positive-text" : "negative-text"}>{pct(account.totalReturnPct)}</td><td className={(account.avgNetReturnPct ?? 0) >= 0 ? "positive-text" : "negative-text"}>{pct(account.avgNetReturnPct)}</td><td className={(account.avgExcessReturnPct ?? 0) >= 0 ? "positive-text" : "negative-text"}>{pct(account.avgExcessReturnPct)}</td><td>{rate(account.winRatePct)}</td><td className="negative-text">{pct(account.maxDrawdownPct)}</td></tr>) : <tr><td colSpan={9}>下一次盤後模擬會初始化五個公平比較帳戶。</td></tr>}</tbody></table></div>
      </section>

      <section className="panel">
        <PanelHeader eyebrow="Locked candidate family" title="擴展視窗候選排名" description="排名只用真正樣本外折；最佳診斷候選若仍有任一拒絕原因，就不建立模擬持倉" trailing={<span className="record-count">{strategyLab.candidateLeaderboard.length} 組</span>} />
        {strategyLab.candidateLeaderboard.length ? <div className="table-scroll"><table className="data-table compact-table strategy-table"><thead><tr><th>擴展視窗候選</th><th>樣本 / 折數</th><th>每日淨報酬</th><th>每日超額</th><th>{isAlphaV2 ? "相對現金" : "相對正式規則"}</th><th>PSR</th><th>最大回撤</th><th>跨期獲利</th><th>判定</th></tr></thead><tbody>{strategyLab.candidateLeaderboard.map((row) => <tr key={row.experimentKey}><td><strong>{row.rankingTarget === "peer_rank" ? "同產業相對排序" : row.rankingTarget === "downside_utility" ? "回撤懲罰超額排序" : "大盤超額排序"}</strong><br /><small>{executionMethodLabels[row.entryMethod] ?? row.entryMethod} · T+{row.holdingHorizon} · Q{Math.round((row.predictionQuantile ?? 0.8) * 100)}</small></td><td><strong>{number.format(row.trades)} 筆</strong><br /><small>{number.format(row.decisionDates)} 日 · {row.walkForwardFolds} 折 · 參與 {rate(row.participationRatePct)}</small></td><td className={(row.meanDailyNetReturn ?? 0) >= 0 ? "positive-text" : "negative-text"}>{pct(row.meanDailyNetReturn)}<br /><small>{isAlphaV2 ? "現金" : "規則"} {pct(row.formalBaselineMeanDailyNetReturn)}</small></td><td className={(row.meanDailyExcessReturn ?? 0) >= 0 ? "positive-text" : "negative-text"}>{pct(row.meanDailyExcessReturn)}<br /><small>{isAlphaV2 ? "現金" : "規則"} {pct(row.formalBaselineMeanDailyExcessReturn)}</small></td><td className={(row.formalExcessLift ?? 0) >= 0 ? "positive-text" : "negative-text"}>超額 {pct(row.formalExcessLift)}<br /><small>淨報酬 {pct(row.formalNetLift)}</small></td><td>{row.probabilisticSharpe == null ? "--" : `${decimal.format(row.probabilisticSharpe * 100)}%`}</td><td className="negative-text">{pct(row.maxDrawdown)}</td><td>{rate((row.profitableFoldRate ?? 0) * 100)}</td><td><span className={`status-pill ${row.qualified ? "selected" : "blocked"}`}>{row.qualified ? "可進前瞻影子" : row.prequalified ? "前段通過 / 最終未過" : "不可部署"}</span><br /><small>{row.rejectionReasons.map((reason) => strategyChallengerReasonLabels[reason] ?? reason).join("、")}</small></td></tr>)}</tbody></table></div> : <div className="empty-state">尚未建立擴展視窗策略評估。</div>}
      </section>

      <section className="panel">
        <PanelHeader eyebrow="Execution and horizon audit" title="進場方式與持有期矩陣" description="正式規則在相同訊號上的成本後表現；驗證期用於比較，holdout 欄僅顯示既有稽核結果，不參與挑戰者選擇" trailing={<span className="record-count">{executionMatrix.length} 組</span>} />
        <div className="table-scroll"><table className="data-table compact-table strategy-table"><thead><tr><th>進場 / 持有</th><th>開發期淨 / 超額</th><th>驗證期淨 / 超額</th><th>驗證參與</th><th>Holdout 稽核淨 / 超額</th><th>穩定性</th></tr></thead><tbody>{executionMatrix.map((row) => { const stable = row.development.meanDailyNetReturn > 0 && row.development.meanDailyExcessReturn > 0 && row.validation.meanDailyNetReturn > 0 && row.validation.meanDailyExcessReturn > 0; return <tr key={row.key}><td><strong>{executionMethodLabels[row.entryMethod] ?? row.entryMethod}</strong><br /><small>T+{row.holdingHorizon}</small></td><td className={row.development.meanDailyNetReturn > 0 && row.development.meanDailyExcessReturn > 0 ? "positive-text" : "negative-text"}>{pct(row.development.meanDailyNetReturn)} / {pct(row.development.meanDailyExcessReturn)}</td><td className={row.validation.meanDailyNetReturn > 0 && row.validation.meanDailyExcessReturn > 0 ? "positive-text" : "negative-text"}>{pct(row.validation.meanDailyNetReturn)} / {pct(row.validation.meanDailyExcessReturn)}</td><td>{number.format(row.validation.trades)} 筆<br /><small>{rate(row.validation.participationRatePct)}</small></td><td className={row.holdoutAudit.meanDailyNetReturn > 0 && row.holdoutAudit.meanDailyExcessReturn > 0 ? "positive-text" : "negative-text"}>{pct(row.holdoutAudit.meanDailyNetReturn)} / {pct(row.holdoutAudit.meanDailyExcessReturn)}</td><td><span className={`status-pill ${stable ? "selected" : "blocked"}`}>{stable ? "雙期為正" : "跨期失效"}</span></td></tr>; })}</tbody></table></div>
      </section>

      <section className="panel">
        <PanelHeader eyebrow="Strategy tournament" title="策略競賽與升級判定" description="正式實驗顯示隔離 holdout；法人研究只顯示開發與驗證期消融，保留全新前瞻世代作最終確認" trailing={<span className="record-count">{experiments.length} 組</span>} />
        <div className="table-scroll"><table className="data-table compact-table strategy-table"><thead><tr><th>策略實驗</th><th>樣本 / 參與</th><th>成本後淨報酬</th><th>超額報酬</th><th>PSR</th><th>最大回撤</th><th>分折獲利率</th><th>判定</th></tr></thead><tbody>{experiments.length ? experiments.map((row) => <tr key={row.experimentKey} title={row.hypothesis}><td><div className="symbol-cell"><strong>{experimentFamilyLabels[row.strategyFamily] ?? row.strategyFamily}</strong><span>{row.name}</span>{row.institutionalCondition ? <small>{row.institutionalCondition}</small> : null}{row.evaluationScope === "historical_development_validation_only" ? <small>歷史診斷 · 未查看保留 holdout</small> : null}{row.predictionThreshold != null ? <small>{row.rankingTarget === "peer_rank" ? `校準分數 ${decimal.format(row.predictionThreshold)}` : `校準門檻 ${pct(row.predictionThreshold)}`}</small> : null}</div></td><td><strong>{number.format(row.trades ?? 0)} 筆</strong><br /><small>{number.format(row.tradeDates ?? 0)} / {number.format(row.decisionDates ?? row.tradeDates ?? 0)} 日 · 參與 {row.participationRatePct == null ? "--" : `${decimal.format(row.participationRatePct)}%`}</small><br /><small>{row.sampleStart ?? "--"} 至 {row.sampleEnd ?? "--"}</small></td><td className={(row.meanNetReturn ?? 0) >= 0 ? "positive-text" : "negative-text"}>{pct(row.meanNetReturn)}{row.meanDailyNetReturn != null && row.decisionDates !== row.tradeDates ? <><br /><small>含空手日 {pct(row.meanDailyNetReturn)}</small></> : null}{row.institutionalNetLift != null ? <><br /><small>法人增量 {pct(row.institutionalNetLift)}</small></> : null}</td><td className={(row.meanExcessReturn ?? 0) >= 0 ? "positive-text" : "negative-text"}>{pct(row.meanExcessReturn)}{row.meanDailyExcessReturn != null && row.decisionDates !== row.tradeDates ? <><br /><small>含空手日 {pct(row.meanDailyExcessReturn)}</small></> : null}{row.institutionalExcessLift != null ? <><br /><small>法人增量 {pct(row.institutionalExcessLift)}</small></> : null}</td><td>{row.probabilisticSharpe == null ? "--" : `${decimal.format(row.probabilisticSharpe * 100)}%`}</td><td className="negative-text">{pct(row.maxDrawdown)}</td><td>{row.profitableFoldRate == null ? "--" : `${decimal.format(row.profitableFoldRate * 100)}%`}</td><td><span className={`status-pill ${row.qualified ? "selected" : "blocked"}`}>{row.qualified ? "符合升級門檻" : row.evaluationScope === "historical_development_validation_only" ? "歷史診斷" : "維持研究"}</span><br /><small>{row.qualified ? "等待人工審查" : row.rejectionReasons.map((reason) => experimentReasonLabels[reason] ?? reason).join("、") || "尚無評估"}</small></td></tr>) : <tr><td colSpan={8}>每日盤後流程完成後會建立第一批策略評估。</td></tr>}</tbody></table></div>
      </section>

      <section className="panel">
        <PanelHeader eyebrow="Out-of-fold challenge" title="AI 挑戰者與規則冠軍同窗比較" description="每一折只使用該日期以前的資料訓練，隔離 T+3 標籤重疊後，再以相同候選、成本與交易日比較" trailing={<span className={`health-badge ${latestChallenger?.qualified ? "healthy" : "building"}`}>{latestChallenger?.qualified ? "通過量化門檻" : "維持影子"}</span>} />
        {latestChallenger ? <div className="table-scroll"><table className="data-table compact-table strategy-table"><thead><tr><th>驗證範圍</th><th>AI 挑戰者</th><th>規則冠軍</th><th>AI 淨報酬</th><th>規則淨報酬</th><th>AI 超額</th><th>規則超額</th><th>AI 相對增值</th><th>最大回撤</th><th>判定</th></tr></thead><tbody><tr><td><strong>{number.format(latestChallenger.oofTradeDates)} 日</strong><br /><small>{number.format(latestChallenger.oofCandidates)} 個樣本外候選</small></td><td>{number.format(latestChallenger.challengerTrades)} 筆</td><td>{number.format(latestChallenger.championTrades)} 筆</td><td className={(latestChallenger.challengerMeanNetReturn ?? 0) >= 0 ? "positive-text" : "negative-text"}>{pct(latestChallenger.challengerMeanNetReturn)}</td><td className={(latestChallenger.championMeanNetReturn ?? 0) >= 0 ? "positive-text" : "negative-text"}>{pct(latestChallenger.championMeanNetReturn)}</td><td className={(latestChallenger.challengerMeanExcessReturn ?? 0) >= 0 ? "positive-text" : "negative-text"}>{pct(latestChallenger.challengerMeanExcessReturn)}</td><td className={(latestChallenger.championMeanExcessReturn ?? 0) >= 0 ? "positive-text" : "negative-text"}>{pct(latestChallenger.championMeanExcessReturn)}</td><td className={(latestChallenger.excessReturnLift ?? 0) >= 0 ? "positive-text" : "negative-text"}>{pct(latestChallenger.excessReturnLift)}</td><td className="negative-text">{pct(latestChallenger.challengerMaxDrawdown)}</td><td><span className={`status-pill ${latestChallenger.qualified ? "selected" : "blocked"}`}>{latestChallenger.qualified ? "可進人工審查" : "不可升級"}</span><br /><small>{latestChallenger.qualified ? `分折獲利率 ${pct((latestChallenger.profitableFoldRate ?? 0) * 100)}` : latestChallenger.rejectionReasons.map((reason) => challengerReasonLabels[reason] ?? reason).join("、")}</small></td></tr></tbody></table></div> : <div className="empty-state">下一次 AI 訓練後會建立第一份嚴格樣本外挑戰報告。</div>}
      </section>

      <section className="pipeline-board panel">
        <PanelHeader eyebrow="Data lineage" title="量化學習資料鏈" description={`特徵覆蓋率 ${decimal.format(featureCoverage)}%，所有階段保留版本與時間點`} trailing={<span className={`health-badge ${health.status === "healthy" ? "healthy" : "building"}`}>{health.status === "critical" ? "標註逾期" : health.status === "healthy" ? "資料健康" : "證據累積中"}</span>} />
        <div className="pipeline-grid">{stages.map((stage, index) => { const Icon = stage.icon; return <div className="pipeline-stage" key={stage.label}><div className="stage-icon"><Icon size={18} /></div><span>{stage.label}</span><strong>{number.format(stage.value)}</strong><small>{stage.note}</small><div className="progress"><i style={{ width: `${Math.max(stage.value ? 3 : 0, stage.value / max * 100)}%` }} /></div>{index < stages.length - 1 && <ChevronRight className="stage-arrow" size={16} />}</div>; })}</div>
        <div className="readiness-callout"><Database size={19} /><div><strong>歷史重播：{health.latestReplayStatus === "completed" ? `${number.format(health.replayTradingDays)} 個交易日、${number.format(health.replayMatureT3)} 筆成熟 T+3` : "尚未完成第一輪"}</strong><p>{health.latestReplayStart && health.latestReplayEnd ? `${health.latestReplayStart} 至 ${health.latestReplayEnd} · ${number.format(health.replayAvailableSymbols)} 檔可用股票 · ${number.format(health.replayUniverseMembershipIntervals)} 個成員區間 · ${universeQualityLabel}${health.replayUniversePartialMemberships ? `（${number.format(health.replayUniversePartialMemberships)} 個起始日缺漏）` : ""}${health.replayCheckpointTotal ? ` · 檢查點 ${health.replayCheckpointCompleted}/${health.replayCheckpointTotal}` : ""} · 入選淨報酬 ${pct(health.replaySelectedMeanNetReturn3d)} · 相對落選增值 ${pct(health.replaySelectionNetLift3d)} · ${health.replayEvidenceStorageMode === "summary_only" ? "主庫保留摘要，完整明細已封存" : `主庫明細 ${number.format(health.replayRawEventsPersisted)} 筆`}。` : "重播資料與正式即時預測完全隔離，完成後才作為研究證據。"} {health.warnings.join(" ")} {health.replayDataWarnings.slice(0, 2).join(" ")}</p></div></div>
      </section>

      <section className="panel">
        <PanelHeader eyebrow="Point-in-time institutional flow" title="法人籌碼研究資料層" description={`${institutional.researchGeneration} · ${institutional.featureVersion}`} trailing={<span className={`health-badge ${institutional.quality.status === "ready" ? "healthy" : "building"}`}>{institutional.quality.status === "ready" ? "資料完整" : "前瞻累積中"}</span>} />
        <div className="table-scroll"><table className="data-table compact-table strategy-table"><thead><tr><th>最新資料日</th><th>官方來源</th><th>保留列數 / 標的</th><th>候選特徵覆蓋</th><th>完整 20 日特徵</th><th>正式排名</th></tr></thead><tbody><tr><td><strong>{institutional.latestTradeDate || "尚未收集"}</strong><br /><small>{formatDateTime(institutional.fetchedAt)}</small></td><td>{institutional.sources.length ? institutional.sources.map((source) => `${source.market} ${source.status === "available" ? "完成" : source.status}`).join(" · ") : "等待官方資料"}</td><td><strong>{number.format(institutional.rawRows)}</strong> 列<br /><small>{number.format(institutional.symbols)} 檔</small></td><td><strong>{decimal.format(institutional.coveragePct)}%</strong><br /><small>{number.format(institutional.featureSnapshots)}/{number.format(institutional.candidateTargets)}</small></td><td><strong>{decimal.format(institutional.completeCoveragePct)}%</strong><br /><small>{number.format(institutional.completeFeatures)} 筆</small></td><td><span className="status-pill blocked">影子研究</span><br /><small>等待新世代前瞻證據</small></td></tr></tbody></table></div>
        <div className="readiness-callout"><ShieldCheck size={19} /><div><strong>時間點防護：交易日資料隔日上午 08:30 才可進入特徵</strong><p>{institutional.quality.warnings.join(" ")}</p></div></div>
        {institutional.candidates.length ? <div className="table-scroll"><table className="data-table compact-table"><thead><tr><th>候選</th><th>資料截至</th><th>樣本</th><th>外資 Z20</th><th>投信 Z20</th><th>三法人 Z20</th><th>外資連續</th><th>投信連續</th><th>方向共識</th></tr></thead><tbody>{institutional.candidates.slice(0, 20).map((row) => <tr key={row.code}><td><strong>{row.code}</strong> {row.name}<br /><small>{row.industry}</small></td><td>{row.sourceTradeDate ?? "--"}</td><td><span className={`status-pill ${row.coverageStatus === "complete" ? "selected" : "neutral"}`}>{row.observations20d ?? 0}/20</span></td><td>{row.foreignNetZ20 == null ? "--" : decimal.format(row.foreignNetZ20)}</td><td>{row.trustNetZ20 == null ? "--" : decimal.format(row.trustNetZ20)}</td><td>{row.totalNetZ20 == null ? "--" : decimal.format(row.totalNetZ20)}</td><td>{row.foreignStreakDays ?? "--"}</td><td>{row.trustStreakDays ?? "--"}</td><td>{row.agreementScore1d == null ? "--" : `${row.agreementScore1d > 0 ? "+" : ""}${row.agreementScore1d}`}</td></tr>)}</tbody></table></div> : null}
      </section>

      <section className="panel replay-attribution-panel">
        <PanelHeader eyebrow="Historical factor attribution" title="歷史重播因子歸因" description="同一批點時事件比較持有期、超額、回撤與統計不確定性" trailing={<span className="record-count">{attribution.attributionVersion || "尚未建立"}</span>} />
        <div className="toolbar attribution-toolbar">
          <label className="inline-select"><SlidersHorizontal size={14} /><select value={attributionDimension} onChange={(event) => setAttributionDimension(event.target.value)}>{attribution.dimensions.map((dimension) => <option value={dimension.key} key={dimension.key}>{dimension.label}</option>)}</select></label>
          <div className="segmented" aria-label="歸因候選範圍"><button className={attributionScope === "all" ? "active" : ""} onClick={() => setAttributionScope("all")}>全部候選</button><button className={attributionScope === "selected" ? "active" : ""} onClick={() => setAttributionScope("selected")}>正式入選</button><button className={attributionScope === "rejected" ? "active" : ""} onClick={() => setAttributionScope("rejected")}>未入選</button></div>
          <span className="attribution-asof">更新 {formatDateTime(attribution.generatedAt)} · Replay #{attribution.replayRunId ?? "--"}</span>
        </div>
        {attributionRows.length ? <>
          <div className="chart-frame attribution-chart"><ResponsiveContainer width="100%" height="100%"><BarChart data={attributionRows.slice(0, 12)} margin={{ top: 16, right: 18, left: -12, bottom: 48 }}><CartesianGrid stroke="#2b2e31" vertical={false} /><XAxis dataKey="bucketLabel" stroke="#777d82" tickLine={false} axisLine={false} interval={0} angle={-17} textAnchor="end" /><YAxis stroke="#777d82" tickLine={false} axisLine={false} unit="%" /><Tooltip contentStyle={tooltipStyle} formatter={(value) => pct(Number(value))} /><ReferenceLine y={0} stroke="#666c71" /><Bar dataKey="meanNetReturn1d" name="T+1 淨報酬" fill="#72787d" radius={[2, 2, 0, 0]} /><Bar dataKey="meanNetReturn3d" name="T+3 淨報酬" fill="#5fb3d9" radius={[2, 2, 0, 0]} /><Bar dataKey="meanNetReturn5d" name="T+5 淨報酬" fill="#55c29a" radius={[2, 2, 0, 0]} /></BarChart></ResponsiveContainer></div>
          <div className="table-scroll"><table className="data-table compact-table attribution-table"><thead><tr><th>切片</th><th>成熟樣本</th><th>T+1</th><th>T+3</th><th>T+5</th><th>T+3 超額</th><th>正報酬率</th><th>策略成功率</th><th>平均回撤</th><th>95% 信賴區間</th><th>證據判定</th></tr></thead><tbody>{attributionRows.map((row) => { const directionConfirmed = row.sampleCount >= 30 && row.ci95Low3d != null && row.ci95High3d != null && (row.ci95Low3d > 0 || row.ci95High3d < 0); const positive = directionConfirmed && (row.ci95Low3d ?? 0) > 0; return <tr key={`${row.dimension}-${row.bucketKey}-${row.selectionScope}`}><td><strong>{row.bucketLabel}</strong><br /><small>{row.selectionLabel}</small></td><td><strong>{number.format(row.sampleCount)}</strong><br /><small>T+5 {number.format(row.matureT5)}</small></td><td className={(row.meanNetReturn1d ?? 0) >= 0 ? "positive-text" : "negative-text"}>{pct(row.meanNetReturn1d)}</td><td className={(row.meanNetReturn3d ?? 0) >= 0 ? "positive-text" : "negative-text"}>{pct(row.meanNetReturn3d)}</td><td className={(row.meanNetReturn5d ?? 0) >= 0 ? "positive-text" : "negative-text"}>{pct(row.meanNetReturn5d)}</td><td className={(row.meanExcessReturn3d ?? 0) >= 0 ? "positive-text" : "negative-text"}>{pct(row.meanExcessReturn3d)}</td><td>{rate(row.positiveRate3d)}</td><td>{rate(row.successRateT3)}</td><td className="negative-text">{pct(row.meanMaxDrawdown3d)}</td><td>{row.ci95Low3d == null || row.ci95High3d == null ? "--" : `${pct(row.ci95Low3d)} ～ ${pct(row.ci95High3d)}`}</td><td><span className={`status-pill ${directionConfirmed ? positive ? "selected" : "blocked" : "neutral"}`}>{directionConfirmed ? positive ? "正向確認" : "負向確認" : "尚未確認"}</span></td></tr>; })}</tbody></table></div>
        </> : <div className="empty-state">完成重播並執行歸因後，這裡會顯示各因子的歷史效果。</div>}
      </section>

      <section className="metrics-grid metrics-grid-five">
        <Metric label="前瞻 Cohort" value={number.format(health.prospectiveCohorts)} detail={`多週期 ${number.format(health.executionScenarioCandidates ?? 0)} 檔 · T+20 ${number.format(health.executionScenariosMatureT20 ?? 0)} 情境`} tone="info" icon={Bot} />
        <Metric label="理應成熟 T+3" value={number.format(health.expectedMatureT3)} detail={`最久等待 ${health.oldestPendingSessions} 個交易日`} tone="info" icon={Clock3} />
        <Metric label="實際成熟 T+3" value={number.format(health.matureT3Cohorts)} detail={`覆蓋 ${decimal.format(health.maturityCoveragePct)}%`} tone={health.staleOutcomes === 0 ? "positive" : "warning"} icon={CheckCircle2} />
        <Metric label="逾期未標註" value={number.format(health.staleOutcomes)} detail={health.staleOutcomes ? "資料管線需要處理" : "沒有成熟度缺口"} tone={health.staleOutcomes ? "danger" : "positive"} icon={TriangleAlert} />
        <Metric label="歷史重播樣本" value={number.format(health.replayEvents)} detail={`入選超額 ${pct(health.replaySelectedMeanExcessReturn3d)} · 增值 ${pct(health.replaySelectionExcessLift3d)}`} tone={(health.replaySelectionExcessLift3d ?? -Infinity) > 0 ? "positive" : health.completedReplayRuns ? "danger" : "warning"} icon={Database} />
      </section>

      <section className="analysis-grid equal-grid">
        <div className="panel gate-panel">
          <PanelHeader eyebrow="Promotion gates" title="模型升級門檻" description="全部通過後仍需人工審查，不會自動接管正式策略" />
          <div className="gate-list">
            <GateRow label="成熟全候選結果" value={research.matureCandidateOutcomes} target={500} display={`${research.matureCandidateOutcomes} 筆`} passed={research.matureCandidateOutcomes >= 500} />
            <GateRow label="成熟落選對照組" value={research.matureRejectedOutcomes} target={250} display={`${research.matureRejectedOutcomes} 筆`} passed={research.matureRejectedOutcomes >= 250} />
            <GateRow label="獨立交易日" value={research.uniqueTradeDates} target={120} display={`${research.uniqueTradeDates} 日`} passed={research.uniqueTradeDates >= 120} />
            <GateRow label="模型訓練樣本" value={samples} target={500} display={`${samples} 筆`} passed={samples >= 500} />
            <GateRow label="成功正樣本" value={positives} target={50} display={`${positives} 筆`} passed={positives >= 50} />
            <GateRow label="擴展視窗 OOF AUC" value={auc} target={0.6} display={auc ? decimal.format(auc) : "NA"} passed={auc >= 0.6} />
            <GateRow label="AI 勝過規則冠軍" value={latestChallenger?.qualified ? 1 : 0} target={1} display={latestChallenger?.qualified ? "通過" : "尚未通過"} passed={Boolean(latestChallenger?.qualified)} />
            <GateRow label="超額報酬 MAE" value={mae} target={3} display={mae < 99 ? pct(mae) : "NA"} passed={mae <= 3} lowerIsBetter />
            <GateRow label="成熟前瞻預測" value={matureProspective} target={150} display={`${matureProspective} 筆`} passed={matureProspective >= 150} />
            <GateRow label="AI 模擬結案交易" value={paperClosed} target={100} display={`${paperClosed} 筆`} passed={paperClosed >= 100} />
            <GateRow label="AI 模擬相對規則" value={paperEdge} target={0} display={Number.isFinite(paperEdge) ? pct(paperEdge) : "待滿 100 筆"} passed={paperEdge > 0} />
            <GateRow label="選股相對落選組增值" value={selectionLift} target={0} display={Number.isFinite(selectionLift) ? pct(selectionLift) : "NA"} passed={selectionLift > 0} />
            <GateRow label="樣本外經濟優勢" value={economicEdge ? 1 : 0} target={1} display={economicEdge ? "淨報酬與超額為正" : "尚未通過"} passed={economicEdge} />
          </div>
        </div>
        <div className="panel model-panel">
          <PanelHeader eyebrow="Latest challenger" title="目前影子模型" description="模型只提供平行排名，正式名單仍由版本化規則控制" />
          {latestModel ? <div className="model-spec"><div className="model-version"><Bot size={22} /><div><strong>{latestModel.modelName}</strong><span>{latestModel.version}</span></div></div><dl className="detail-list"><div><dt>狀態</dt><dd>{latestModel.status}</dd></div><div><dt>結果來源</dt><dd>{latestModel.metrics.outcome_source ?? "legacy"}</dd></div><div><dt>特徵版本</dt><dd>{latestModel.featureVersion}</dd></div><div><dt>訓練樣本</dt><dd>{samples}</dd></div><div><dt>OOF 驗證樣本</dt><dd>{latestModel.metrics.validation_samples ?? "--"}</dd></div><div><dt>擴展視窗分折</dt><dd>{latestModel.metrics.walk_forward_folds ?? "--"}</dd></div><div><dt>OOF 交易日</dt><dd>{latestModel.metrics.oof_trade_dates ?? "--"}</dd></div><div><dt>Brier Score</dt><dd>{latestModel.metrics.validation_brier == null ? "--" : decimal.format(latestModel.metrics.validation_brier)}</dd></div><div><dt>回撤 MAE</dt><dd>{pct(latestModel.metrics.validation_drawdown_mae)}</dd></div><div><dt>新聞證據</dt><dd>{snapshot.overview.newsEvidence}</dd></div><div><dt>前瞻預測</dt><dd>{prospective}</dd></div></dl></div> : <div className="empty-state">尚未建立模型版本。</div>}
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

const paperStatusLabels: Record<PaperTrade["status"], string> = {
  pending: "等待成交",
  open: "持有中",
  closed: "已結案",
  skipped: "未成交",
};

const paperReasonLabels: Record<string, string> = {
  awaiting_next_open: "等待下一交易日開盤",
  outcome_backfill_pending: "等待行情回填",
  awaiting_exit_data: "等待退出資料",
  above_chase_limit: "開盤超過禁止追價線",
  gap_below_defense: "開盤跌破防守價",
  invalid_stop_at_entry: "進場價未高於防守價",
  duplicate_open_position: "已有同一標的持倉",
  max_positions_reached: "已達五檔持倉上限",
  insufficient_cash: "可用資金不足",
  industry_exposure_limit: "已達產業曝險上限",
  defense_close: "防守價出場",
  time_exit_t3: "T+3 到期出場",
};

const capitalTournamentReasonLabels: Record<string, string> = {
  insufficient_prospective_dates: "未滿 120 個新決策日",
  insufficient_closed_trades: "未滿 100 筆結案",
  nonpositive_after_cost_return: "成本後總報酬未轉正",
  nonpositive_benchmark_excess: "平均大盤超額未轉正",
  does_not_outperform_top3: "尚未勝過 Top 3 基準",
  does_not_improve_drawdown_efficiency: "回撤效率未勝過 Top 3",
  drawdown_below_floor: "最大回撤超過 12%",
};

function paperStatusTone(status: PaperTrade["status"]) {
  if (status === "open") return "selected";
  if (status === "pending") return "eligible";
  if (status === "skipped") return "blocked";
  return "neutral";
}

function PaperTradingView({ snapshot }: { snapshot: DashboardSnapshot }) {
  const rule = snapshot.paperAccounts.find((account) => account.strategyKind === "rule");
  const legacyAi = snapshot.paperAccounts.find((account) => account.strategyKind === "ai");
  const tournament = snapshot.capitalTournament;
  const tournamentAccounts = tournament.accounts.map((row) => snapshot.paperAccounts.find((account) => account.accountKey === row.accountKey)).filter((account) => account != null);
  const benchmark = snapshot.paperAccounts.find((account) => account.accountKey === tournament.benchmarkAccountKey);
  const provisionalLeader = tournament.accounts.find((account) => account.accountKey === tournament.provisionalLeaderAccountKey);
  const reviewCandidate = tournament.accounts.find((account) => account.accountKey === tournament.reviewCandidateAccountKey);
  const closedTrades = tournament.accounts.reduce((sum, account) => sum + account.closedTrades, 0);
  const promotionReady = tournament.status === "manual_review_required";
  const tournamentAccountKeys = new Set(tournament.accounts.map((account) => account.accountKey));
  const settlement = snapshot.paperSettlement;
  const settlementIsCurrent = settlement.sessionDate === snapshot.overview.latestTradeDate;
  const settlementLabel = settlement.status === "completed" && settlementIsCurrent
    ? "今日開盤結算完成"
    : settlement.status === "waiting_market_data" && settlementIsCurrent
      ? "等待開盤行情"
      : settlement.status === "failed" && settlementIsCurrent
        ? "今日結算失敗"
        : "今日尚未完成開盤結算";
  const curve = Array.from(
    snapshot.paperEquity.reduce((map, point) => {
      if (!tournamentAccountKeys.has(point.accountKey) || point.asOf < tournament.evidenceStartDate) return map;
      const current = map.get(point.asOf) ?? { asOf: point.asOf };
      if (point.accountKey === "ai_top3_equal_v1") current.top3 = point.equity;
      if (point.accountKey === "ai_top5_diversified_v1") current.top5 = point.equity;
      if (point.accountKey === "ai_top10_weighted_v1") current.top10 = point.equity;
      map.set(point.asOf, current);
      return map;
    }, new Map<string, { asOf: string; top3?: number; top5?: number; top10?: number }>()).values(),
  ).sort((a, b) => a.asOf.localeCompare(b.asOf));
  const active = snapshot.paperTrades.filter((trade) => trade.status === "open" || trade.status === "pending");
  const recent = snapshot.paperTrades.filter((trade) => trade.status === "closed" || trade.status === "skipped").slice(0, 30);
  const accountNames = new Map(snapshot.paperAccounts.map((account) => [account.accountKey, account.name]));
  const config = benchmark?.config ?? legacyAi?.config ?? rule?.config ?? {};

  return (
    <div className="view-stack">
      <section className="model-hero-band">
        <div><span className="eyebrow">Prospective capital tournament</span><h2>{promotionReady ? `${reviewCandidate?.name ?? "挑戰者"}進入人工升級審查` : "三種 AI 組合正在累積全新前瞻證據"}</h2><p>同一批每日盤後預測，同日起跑比較 Top 3 等權、Top 5 分散與 Top 10 分數加權；歷史結果不補考</p></div>
        <div className={`governance-state ${promotionReady ? "ready" : "shadow"}`}><WalletCards size={20} /><span>{promotionReady ? "MANUAL REVIEW" : "PAPER ONLY"}</span></div>
      </section>

      <div className="readiness-callout"><Clock3 size={19} /><div><strong>正式資金 CASH｜{settlementLabel}</strong><p>影子帳戶仍會累積可驗證交易。開盤結算只讀取前一交易日 EOD 訊號，使用次一交易日開盤價；目前持有 {active.filter((trade) => trade.status === "open").length} 筆、等待成交 {active.filter((trade) => trade.status === "pending").length} 筆。{settlement.settlementAt ? `最近結算 ${formatDateTime(settlement.settlementAt)}，本次新成交 ${settlement.newOpenPositions}、未成交 ${settlement.newSkippedOrders}、結案 ${settlement.newClosedPositions}。` : "首次結算會在開盤後第一個可用排程執行。"}</p></div></div>

      <section className="metrics-grid metrics-grid-five">
        <Metric label="前瞻決策日" value={number.format(tournament.evidenceDays)} detail={`門檻 ${tournament.minimumEvidenceDays} 日 · 自 ${tournament.evidenceStartDate}`} tone={tournament.evidenceDays >= tournament.minimumEvidenceDays ? "positive" : "warning"} icon={Clock3} />
        <Metric label="競賽結案交易" value={number.format(closedTrades)} detail="三帳戶合計，升級逐戶判斷" tone={closedTrades > 0 ? "info" : "warning"} icon={CheckCircle2} />
        <Metric label="暫時領先組合" value={provisionalLeader?.name ?? "等待首批交易"} detail={`報酬 ${pct(provisionalLeader?.totalReturnPct)}`} tone={(provisionalLeader?.totalReturnPct ?? 0) > 0 ? "positive" : "warning"} icon={Target} />
        <Metric label="Top 3 基準報酬" value={pct(benchmark?.totalReturnPct)} detail={`回撤 ${pct(benchmark?.maxDrawdownPct)}`} tone={(benchmark?.totalReturnPct ?? 0) > 0 ? "positive" : "info"} icon={Activity} />
        <Metric label="升級狀態" value={promotionReady ? "人工審查" : "累積證據"} detail="永不自動切換正式策略" tone={promotionReady ? "positive" : "warning"} icon={ShieldCheck} />
      </section>

      <section className="analysis-grid equal-grid">
        <div className="panel chart-panel paper-chart-panel">
          <PanelHeader eyebrow="Equity curve" title="前瞻組合資產曲線" description="三帳戶同本金、同預測、同交易成本；只改變選取廣度與配置方法" />
          <div className="chart-frame">{curve.length ? <ResponsiveContainer width="100%" height="100%"><LineChart data={curve} margin={{ top: 16, right: 18, left: 8, bottom: 4 }}><CartesianGrid stroke="#2b2e31" vertical={false} /><XAxis dataKey="asOf" stroke="#777d82" tickLine={false} axisLine={false} minTickGap={30} /><YAxis stroke="#777d82" tickLine={false} axisLine={false} tickFormatter={(value) => `${Math.round(value / 1000)}k`} /><Tooltip contentStyle={tooltipStyle} formatter={(value) => money(Number(value))} /><Line type="monotone" dataKey="top3" name="Top 3 等權" stroke="#5fb3d9" strokeWidth={2} dot={false} connectNulls /><Line type="monotone" dataKey="top5" name="Top 5 分散" stroke="#55c29a" strokeWidth={2} dot={false} connectNulls /><Line type="monotone" dataKey="top10" name="Top 10 加權" stroke="#e2ae5f" strokeWidth={2} dot={false} connectNulls /></LineChart></ResponsiveContainer> : <div className="empty-state">競賽將於 {tournament.evidenceStartDate} 的盤後預測完成後開始畫線。</div>}</div>
        </div>
        <div className="panel paper-account-panel">
          <PanelHeader eyebrow="Account comparison" title="三帳戶即時比較" description="暫時領先只代表目前帳面結果，不等於通過升級門檻" />
          <div className="paper-account-list">{tournamentAccounts.length ? tournamentAccounts.map((account) => { const policy = account.config.capital_policy; return <div className="paper-account-row" key={account.accountKey}><div><span className={`run-dot ${account.accountKey === tournament.provisionalLeaderAccountKey ? "success" : "intraday"}`} /><div><strong>{account.name}</strong><small>每日 Top {policy?.max_daily_selections ?? "--"} · {policy?.weighting === "score_proportional" ? "分數加權" : "等權"} · 產業 {policy?.max_per_industry ?? "--"} 檔</small></div></div><dl><div><dt>總報酬</dt><dd className={account.totalReturnPct >= 0 ? "positive-text" : "negative-text"}>{pct(account.totalReturnPct)}</dd></div><div><dt>平均超額</dt><dd className={(account.avgExcessReturnPct ?? 0) >= 0 ? "positive-text" : "negative-text"}>{pct(account.avgExcessReturnPct)}</dd></div><div><dt>回撤</dt><dd>{pct(account.maxDrawdownPct)}</dd></div><div><dt>結案 / 勝率</dt><dd>{account.closedTrades} / {rate(account.winRate)}</dd></div></dl></div>; }) : <div className="empty-state">首次盤後流程執行後會建立三個競賽帳戶。</div>}</div>
        </div>
      </section>

      <section className="panel">
        <PanelHeader eyebrow="Promotion gates" title="組合升級判定" description="挑戰者須同時滿足樣本、成本後報酬、大盤超額、Top 3 相對增值、回撤效率與風險底線" trailing={<span className="record-count">{tournament.version}</span>} />
        <div className="table-scroll"><table className="data-table compact-table strategy-table"><thead><tr><th>組合</th><th>角色</th><th>決策日 / 結案</th><th>總報酬</th><th>平均超額</th><th>相對 Top 3</th><th>最大回撤</th><th>判定</th></tr></thead><tbody>{tournament.accounts.length ? tournament.accounts.map((account) => <tr key={account.accountKey}><td><strong>{account.name}</strong><br /><small>Top {account.policy.max_daily_selections} · {account.policy.weighting === "score_proportional" ? "分數加權" : "等權"}</small></td><td>{account.role === "benchmark" ? "凍結基準" : "挑戰者"}</td><td>{tournament.evidenceDays} / {account.closedTrades}</td><td className={account.totalReturnPct >= 0 ? "positive-text" : "negative-text"}>{pct(account.totalReturnPct)}</td><td className={(account.avgExcessReturnPct ?? 0) >= 0 ? "positive-text" : "negative-text"}>{pct(account.avgExcessReturnPct)}</td><td className={(account.returnLiftVsTop3Pct ?? 0) > 0 ? "positive-text" : "negative-text"}>{account.role === "benchmark" ? "基準" : pct(account.returnLiftVsTop3Pct)}</td><td>{pct(account.maxDrawdownPct)}</td><td><span className={`status-pill ${account.qualifiedForReview ? "selected" : account.role === "benchmark" ? "neutral" : "blocked"}`}>{account.qualifiedForReview ? "等待人工審查" : account.role === "benchmark" ? "凍結比較" : "累積證據"}</span><br /><small>{account.role === "benchmark" ? "不參與升級" : account.rejectionReasons.map((reason) => capitalTournamentReasonLabels[reason] ?? reason).join("、")}</small></td></tr>) : <tr><td colSpan={8}>尚未建立競賽帳戶，下一次每日盤後自動流程會完成初始化。</td></tr>}</tbody></table></div>
      </section>

      <div className="validation-banner"><TriangleAlert size={18} /><div><strong>這是資金配置實驗，不是買進建議</strong><p>資料只從 {tournament.evidenceStartDate} 起向前累積；至少 120 個新決策日、每個帳戶 100 筆結案且通過所有風險閘門後，也只會送交人工審查，不會自動實盤。</p></div></div>

      <section className="panel">
        <PanelHeader eyebrow="Order lifecycle" title="所有模擬委託與持倉" description="涵蓋規則基準、舊 AI 與新資金競賽；等待成交不會預先占用資金" trailing={<span className="record-count">{active.length} 筆</span>} />
        <div className="table-scroll"><table className="data-table compact-table paper-table"><thead><tr><th>帳戶</th><th>訊號日</th><th>標的</th><th>狀態</th><th>進場</th><th>配置</th><th>股數</th><th>禁止追價</th><th>防守價</th><th>目前價值</th><th>原因</th></tr></thead><tbody>{active.length ? active.map((trade) => <tr key={`${trade.accountKey}-${trade.sourceType}-${trade.sourceId}`}><td>{accountNames.get(trade.accountKey) ?? trade.accountKey}</td><td>{trade.signalDate}</td><td><div className="symbol-cell"><strong>{trade.code}</strong><span>{trade.name}</span></div></td><td><span className={`status-pill ${paperStatusTone(trade.status)}`}>{paperStatusLabels[trade.status]}</span></td><td>{trade.entryAt ? `${trade.entryAt} · ${formatNumeric(trade.entryPrice ?? 0, 0, 2)}` : "--"}</td><td>{trade.allocationWeight == null ? "--" : `${decimal.format(trade.allocationWeight * 100)}%`}</td><td>{trade.quantity ?? "--"}</td><td>{trade.chaseLimit == null ? "--" : formatNumeric(trade.chaseLimit, 0, 2)}</td><td>{trade.stopPrice == null ? "--" : formatNumeric(trade.stopPrice, 0, 2)}</td><td>{money(trade.marketValue)}</td><td>{paperReasonLabels[trade.skipReason ?? ""] ?? "正常持有"}</td></tr>) : <tr><td colSpan={11}>目前沒有等待成交或持有中的模擬部位。</td></tr>}</tbody></table></div>
      </section>

      <section className="panel">
        <PanelHeader eyebrow="Execution ledger" title="所有帳戶最近模擬紀錄" description="成交、未成交與出場都保留原因，避免只統計成功進場造成倖存者偏誤" trailing={<span className="record-count">{recent.length} 筆</span>} />
        <div className="table-scroll"><table className="data-table compact-table paper-table"><thead><tr><th>帳戶</th><th>訊號日</th><th>標的</th><th>AI 分數</th><th>狀態</th><th>進場 / 出場</th><th>股數</th><th>報酬 / 超額</th><th>損益</th><th>最大漲幅</th><th>最大回撤</th><th>結果</th></tr></thead><tbody>{recent.length ? recent.map((trade) => <tr key={`${trade.accountKey}-${trade.sourceType}-${trade.sourceId}`}><td>{accountNames.get(trade.accountKey) ?? trade.accountKey}</td><td>{trade.signalDate}</td><td><div className="symbol-cell"><strong>{trade.code}</strong><span>{trade.name}</span></div></td><td>{trade.finalScore == null ? "--" : decimal.format(trade.finalScore)}</td><td><span className={`status-pill ${paperStatusTone(trade.status)}`}>{paperStatusLabels[trade.status]}</span></td><td>{trade.entryAt ?? "--"} / {trade.exitAt ?? "--"}</td><td>{trade.quantity ?? "--"}</td><td className={(trade.netReturnPct ?? 0) >= 0 ? "positive-text" : "negative-text"}>{pct(trade.netReturnPct)} / {pct(trade.excessReturnPct)}</td><td className={(trade.realizedPnl ?? 0) >= 0 ? "positive-text" : "negative-text"}>{money(trade.realizedPnl)}</td><td>{pct(trade.maxReturnPct)}</td><td>{pct(trade.maxDrawdownPct)}</td><td>{paperReasonLabels[trade.skipReason ?? ""] ?? paperReasonLabels[trade.exitReason ?? ""] ?? "--"}</td></tr>) : <tr><td colSpan={12}>尚無已結案或未成交紀錄。</td></tr>}</tbody></table></div>
      </section>

      <section className="panel paper-policy-panel">
        <PanelHeader eyebrow="Capital policy" title="凍結競賽規則" description="版本發布後不依結果回改；任何新配置法必須另開帳戶與全新證據期" />
        <dl className="paper-policy-grid"><div><dt>共同起始資金</dt><dd>{money(config.starting_cash ?? 1_000_000)}</dd></div><div><dt>證據起算日</dt><dd>{tournament.evidenceStartDate}</dd></div><div><dt>共同現金保留</dt><dd>{decimal.format((config.cash_buffer_pct ?? .05) * 100)}%</dd></div><div><dt>共同風險預算</dt><dd>{decimal.format((config.risk_budget_pct ?? .01) * 100)}% / 檔</dd></div><div><dt>成交與退出</dt><dd>隔日開盤 · 禁止追價 · T+3</dd></div><div><dt>正式接管</dt><dd>禁止自動升級</dd></div></dl>
      </section>

      <section className="panel">
        <PanelHeader eyebrow="Legacy reference" title="舊制參考帳戶" description="保留既有歷史點時規則與舊 AI 前瞻帳戶，不與 2026-07-20 起跑的新競賽混算" />
        <div className="table-scroll"><table className="data-table compact-table"><thead><tr><th>帳戶</th><th>證據性質</th><th>權益</th><th>總報酬</th><th>結案</th><th>勝率</th></tr></thead><tbody>{[rule, legacyAi].filter((account) => account != null).map((account) => <tr key={account.accountKey}><td>{account.name}</td><td>{account.evidenceMode === "prospective_only" ? "舊 AI 真正前瞻" : "歷史點時重播"}</td><td>{money(account.equity)}</td><td className={account.totalReturnPct >= 0 ? "positive-text" : "negative-text"}>{pct(account.totalReturnPct)}</td><td>{account.closedTrades}</td><td>{rate(account.winRate)}</td></tr>)}</tbody></table></div>
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
  const cloudEvidence = snapshot.cloudEvidence;
  const cloudVerified = cloudEvidence.status === "verified";
  const cutoverReady = cloudEvidence.cutoverReady;
  const cloudActionLabels = {
    repair_connection: "修復 Supabase",
    run_cutover_audit: "累積並重跑驗收",
    activate_cloud_primary: "切換 Cloud Primary",
    monitor_cloud_primary: "持續監控",
  };
  const cloudActionLabel = cloudActionLabels[cloudEvidence.nextAction];
  const databaseSizeMb = cloudEvidence.databaseBytes / 1024 / 1024;
  const healthChecks = [
    { label: "公開資料快照", ok: snapshotFresh, detail: formatDateTime(snapshot.generatedAt) },
    { label: "GitHub Actions API", ok: workflowRuns.length > 0, detail: `${workflowRuns.length} 筆執行紀錄` },
    { label: "特徵資料完整", ok: snapshot.overview.featureSnapshots >= snapshot.overview.candidateEvents, detail: `${snapshot.overview.featureSnapshots}/${snapshot.overview.candidateEvents}` },
    { label: "回測批次可追溯", ok: Boolean(lastBacktest), detail: lastBacktest ? formatDateTime(lastBacktest.startedAt) : "尚無紀錄" },
    { label: "前瞻標註未逾期", ok: snapshot.researchHealth.staleOutcomes === 0, detail: `${snapshot.researchHealth.staleOutcomes} 筆逾期` },
    { label: "歷史重播證據", ok: snapshot.researchHealth.completedReplayRuns > 0, detail: snapshot.researchHealth.latestReplayAt ? formatDateTime(snapshot.researchHealth.latestReplayAt) : "尚未執行" },
    { label: "Supabase 證據快照", ok: cloudVerified, detail: cloudEvidence.eventAt ? `${formatDateTime(cloudEvidence.eventAt)} · ${cloudEvidence.errorCode || `run ${cloudEvidence.latestScanRunId ?? "--"}`}` : "尚未驗證" },
    { label: "Cloud Primary 切換閘門", ok: cutoverReady, detail: cloudEvidence.auditAt ? `${cloudEvidence.passedChecks}/${cloudEvidence.totalChecks} · ${formatDateTime(cloudEvidence.auditAt)}` : "尚未執行驗收" },
  ];
  return (
    <div className="view-stack">
      <section className="metrics-grid metrics-grid-five">
        <Metric label="自動化服務" value={workflowRuns.length ? "ONLINE" : "DEGRADED"} detail={`${workflowRuns.length} 筆最近執行`} tone={workflowRuns.length ? "positive" : "danger"} icon={Workflow} />
        <Metric label="執行成功率" value={`${decimal.format(workflowSuccessRate)}%`} detail={`最近 ${completed.length} 筆已完成`} tone={workflowSuccessRate >= 80 ? "positive" : "warning"} icon={CheckCircle2} />
        <Metric label="最近成功" value={latestSuccess ? modeLabel(latestSuccess.name.includes("Intraday") ? "intraday" : "eod") : "--"} detail={formatDateTime(latestSuccess?.updatedAt)} icon={Clock3} />
        <Metric label="最近失敗" value={latestFailed ? "需檢查" : "無"} detail={latestFailed ? formatDateTime(latestFailed.updatedAt) : "最近紀錄未見失敗"} tone={latestFailed ? "danger" : "positive"} icon={TriangleAlert} />
        <Metric label="切換資格" value={cutoverReady ? "READY" : cloudEvidence.auditStatus === "blocked" ? "BLOCKED" : "PENDING"} detail={cloudEvidence.auditAt ? `${cloudEvidence.passedChecks}/${cloudEvidence.totalChecks} 項通過` : "等待首次驗收"} tone={cutoverReady ? "positive" : cloudEvidence.auditStatus === "blocked" ? "danger" : "warning"} icon={Database} />
      </section>

      <section className="operations-grid">
        <div className="panel action-panel">
          <PanelHeader eyebrow="Manual control" title="受控執行入口" description="站內控制碼授權；GitHub 憑證只保留在伺服器端" />
          <DirectIntradayControl />
          <a className="action-link" href={dailyUrl} target="_blank" rel="noreferrer"><span><Clock3 size={19} /><span><strong>執行盤後結算</strong><small>盤後訊號、T+3/T+20 回測與模型結果更新</small></span></span><ArrowUpRight size={18} /></a>
          <div className="permission-note"><ShieldCheck size={17} /><p>站內觸發由伺服器端授權，仍會套用交易時段、防重複與資料覆蓋率檢查。</p></div>
        </div>
        <div className="panel health-panel">
          <PanelHeader eyebrow="System checks" title="服務健康檢查" description="部署、資料、特徵、回測與前瞻成熟度" />
          <div className="health-list">{healthChecks.map((check) => <div className="health-row" key={check.label}><span className={check.ok ? "ok" : "warn"}>{check.ok ? <CircleCheck size={16} /> : <TriangleAlert size={16} />}</span><div><strong>{check.label}</strong><small>{check.detail}</small></div><span>{check.ok ? "正常" : "注意"}</span></div>)}</div>
          <div className={`permission-note cloud-status-note ${cloudVerified ? "ok" : "warning"}`}><Database size={17} /><p>{cloudEvidence.message} 目前模式：{cloudEvidence.migrationMode === "dual_write" ? "雲端與 Git 雙寫驗證" : "雲端主儲存"}。</p></div>
        </div>
      </section>

      <section className="panel workflow-panel">
        <PanelHeader eyebrow="Data plane" title="Cloud Primary 切換驗收" description="上線前需通過雲端時效、每日備份、跨工作流寫入、雙重雜湊、SQLite 還原與資料筆數一致性" trailing={<span className="record-count">{cloudEvidence.passedChecks}/{cloudEvidence.totalChecks || "--"} 通過</span>} />
        <div className="cloud-audit-summary">
          <dl>
            <div><dt>目前模式</dt><dd>{cloudEvidence.migrationMode === "cloud_primary" ? "Cloud Primary" : "Dual Write"}</dd></div>
            <div><dt>每日快照</dt><dd>{cloudEvidence.dailySnapshots}</dd></div>
            <div><dt>驗證寫入</dt><dd>{cloudEvidence.verifiedPushes}</dd></div>
            <div><dt>來源工作流</dt><dd>{cloudEvidence.workflowCount}</dd></div>
            <div><dt>SQLite 大小</dt><dd>{decimal.format(databaseSizeMb)} MB</dd></div>
            <div><dt>下一動作</dt><dd>{cloudActionLabel}</dd></div>
          </dl>
        </div>
        <div className={`cloud-action-banner ${cloudVerified ? "ok" : "warning"}`}><span><TriangleAlert size={16} /></span><div><strong>{cloudActionLabel}</strong><p>{cloudEvidence.recommendedAction}</p>{cloudEvidence.errorCode ? <code>{cloudEvidence.errorCode}</code> : null}</div></div>
        <div className="table-scroll"><table className="data-table compact-table cloud-audit-table"><thead><tr><th>驗收項目</th><th>狀態</th><th>目前證據</th><th>要求</th></tr></thead><tbody>{cloudEvidence.cutoverChecks.length ? cloudEvidence.cutoverChecks.map((check) => <tr key={check.key}><td>{check.label}</td><td><span className={`status-pill ${check.passed ? "selected" : "blocked"}`}>{check.passed ? "通過" : "阻擋"}</span></td><td>{check.detail}</td><td>{check.requirement}</td></tr>) : <tr><td colSpan={4}>尚未執行 Cloud Primary 切換稽核；目前仍由 Git 資料庫安全備援。</td></tr>}</tbody></table></div>
      </section>

      <section className="panel workflow-panel">
        <PanelHeader eyebrow="Live automation" title="最近工作流程" description="可直接開啟 GitHub 查看步驟、耗時與錯誤紀錄" trailing={<a className="icon-link" href="https://github.com/corn92888/Stock_AI_Scanner/actions" target="_blank" rel="noreferrer" aria-label="開啟 GitHub Actions" title="開啟 GitHub Actions"><Code2 size={17} /></a>} />
        <div className="workflow-grid">{workflowRuns.length ? workflowRuns.slice(0, 10).map((run) => <a href={run.url} target="_blank" rel="noreferrer" className="workflow-row" key={run.id}><span className={`run-dot ${statusTone(run)}`} /><div><strong>{run.name}</strong><small>{workflowTriggerLabel(run)} · {formatDateTime(run.createdAt)}</small></div><span className={`workflow-status ${statusTone(run)}`}>{run.status !== "completed" ? "執行中" : run.conclusion === "success" ? "成功" : run.conclusion === "skipped" ? "略過" : run.conclusion ?? "未知"}</span><ExternalLink size={14} /></a>) : <div className="empty-state">GitHub 狀態暫時無法讀取，掃描入口仍可使用。</div>}</div>
      </section>

      <section className="panel">
        <PanelHeader eyebrow="Backtest ledger" title="最近回測批次" description="正式與研究訊號分開記錄，部分成熟結果會在後續交易日更新" trailing={<span className="record-count">{snapshot.backtestRuns.length} 筆</span>} />
        <div className="table-scroll"><table className="data-table compact-table"><thead><tr><th>開始時間</th><th>樣本範圍</th><th>狀態</th><th>要求</th><th>完成</th><th>部分成熟</th><th>略過</th><th>錯誤</th></tr></thead><tbody>{snapshot.backtestRuns.length ? snapshot.backtestRuns.map((run) => <tr key={run.id}><td>{formatDateTime(run.startedAt)}</td><td>{run.selectionScope}</td><td><span className={`status-pill ${run.status === "completed" ? "selected" : "blocked"}`}>{run.status}</span></td><td>{run.signalsRequested}</td><td>{run.completedCount}</td><td>{run.partialCount}</td><td>{run.skippedCount}</td><td className="risk-cell">{run.errorText || "無"}</td></tr>) : <tr><td colSpan={8}>尚無批次回測紀錄。</td></tr>}</tbody></table></div>
      </section>
    </div>
  );
}

export default function DashboardShell({ snapshot, workflowRuns, snapshotFresh, initialView }: { snapshot: DashboardSnapshot; workflowRuns: WorkflowRun[]; snapshotFresh: boolean; initialView?: string }) {
  const router = useRouter();
  const [view, setView] = useState<ViewId>(isViewId(initialView) ? initialView : "decision");
  const [mobileMenu, setMobileMenu] = useState(false);
  const active = navItems.find((item) => item.id === view) ?? navItems[0];
  const latestModel = (snapshot.aiModels ?? [])[0];
  const aiState = (snapshot.overview.prospectivePredictions ?? 0) > 0 ? "AI 前瞻運作" : latestModel ? "AI 影子就緒" : "AI 尚未就緒";
  const selectView = (next: ViewId) => {
    setView(next);
    setMobileMenu(false);
    router.replace(`/?view=${next}`, { scroll: false });
  };

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
        <nav>{navItems.map((item) => { const Icon = item.icon; return <a href={`/?view=${item.id}`} className={view === item.id ? "active" : ""} onClick={(event) => { event.preventDefault(); selectView(item.id); }} key={item.id}><Icon size={19} /><span><strong>{item.label}</strong><small>{item.hint}</small></span><ChevronRight size={15} /></a>; })}</nav>
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
          {view === "market" && <GlobalMarketView snapshot={snapshot} />}
          {view === "performance" && <PerformanceView snapshot={snapshot} />}
          {view === "paper" && <PaperTradingView snapshot={snapshot} />}
          {view === "pipeline" && <PipelineView snapshot={snapshot} />}
          {view === "operations" && <OperationsView snapshot={snapshot} workflowRuns={workflowRuns} snapshotFresh={snapshotFresh} />}
        </div>
      </div>
    </main>
  );
}
