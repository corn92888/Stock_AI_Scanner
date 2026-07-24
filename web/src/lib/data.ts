import "server-only";

import { readFile } from "node:fs/promises";
import path from "node:path";

import type { AlphaForwardSnapshot, AlphaLiveSnapshot, CandidateLearnabilityAudit, CapitalTournament, CloudEvidence, DashboardSnapshot, GlobalMarketSnapshot, InstitutionalFlowSnapshot, PaperSettlement, ReplayAttribution, ResearchHealth, ResearchQuality, StrategyChallengerSnapshot, WorkflowRun } from "./types";

const DEFAULT_DATA_URL =
  "https://raw.githubusercontent.com/corn92888/Stock_AI_Scanner/main/data/dashboard_snapshot.json";
const ACTIONS_URL =
  "https://api.github.com/repos/corn92888/Stock_AI_Scanner/actions/runs?per_page=12";

const EMPTY_RESEARCH_QUALITY: ResearchQuality = {
  executionVersion: "pending",
  outcomeCoveragePct: 0,
  matureCandidateOutcomes: 0,
  matureRejectedOutcomes: 0,
  matureSelectedOutcomes: 0,
  uniqueTradeDates: 0,
  meanNetReturn3d: null,
  meanExcessReturn3d: null,
  positiveRate3d: null,
  successRateT3: null,
  formalMeanNetReturn3d: null,
  formalMeanExcessReturn3d: null,
  rejectedMeanNetReturn3d: null,
  rejectedMeanExcessReturn3d: null,
  selectionNetLift3d: null,
  selectionExcessLift3d: null,
};

const EMPTY_RESEARCH_HEALTH: ResearchHealth = {
  status: "building",
  checkedAt: "",
  latestTradeDate: "",
  prospectiveCohorts: 0,
  pendingCohorts: 0,
  matureT3Cohorts: 0,
  expectedMatureT3: 0,
  staleOutcomes: 0,
  oldestPendingSessions: 0,
  maturityCoveragePct: 0,
  executionScenarioCandidates: 0,
  executionScenarios: 0,
  executionScenariosMatureT20: 0,
  executionScenariosPending: 0,
  replayRuns: 0,
  completedReplayRuns: 0,
  latestReplayAt: null,
  latestReplayStatus: null,
  latestReplayStart: null,
  latestReplayEnd: null,
  replayEvents: 0,
  replaySelected: 0,
  replayMatureT3: 0,
  replayAvailableSymbols: 0,
  replayTradingDays: 0,
  replayUniverseSnapshots: 0,
  replayCheckpointTotal: 0,
  replayCheckpointCompleted: 0,
  replayAttributionRows: 0,
  replayAttributionDimensions: 0,
  replayAttributionAt: null,
  replayEvidenceStorageMode: "none",
  replayRawEventsPersisted: 0,
  warnings: ["研究健康監控尚未完成第一次執行。"],
  replayDataWarnings: [],
  replayUniverseQualityStatus: "unverified",
  replayUniversePartialMemberships: 0,
  replayUniverseMembershipIntervals: 0,
  replaySelectedMeanNetReturn3d: null,
  replaySelectedMeanExcessReturn3d: null,
  replayRejectedMeanNetReturn3d: null,
  replayRejectedMeanExcessReturn3d: null,
  replaySelectionNetLift3d: null,
  replaySelectionExcessLift3d: null,
  replaySelectedSuccessRateT3: null,
  replayRejectedSuccessRateT3: null,
  formalMatureCandidates: 0,
  formalMatureSelected: 0,
  formalMatureRejected: 0,
  formalSelectedTradeDates: 0,
  formalSelectedMeanNetReturn3d: null,
  formalSelectedMeanExcessReturn3d: null,
  formalRejectedMeanNetReturn3d: null,
  formalRejectedMeanExcessReturn3d: null,
  formalSelectionNetLift3d: null,
  formalSelectionExcessLift3d: null,
  strategyChallengerEvaluatedAt: null,
  strategyChallengerVersion: null,
  strategyChallengerStatus: "not_evaluated",
  strategyChallengerSelectedKey: null,
  strategyRecommendationMode: "cash",
  strategyQualifiedCandidates: 0,
  strategyCandidateCount: 0,
  integrityGate: {
    version: "research_integrity_gate_v1",
    status: "blocked",
    recommendationMode: "research_only",
    evidenceReady: false,
    manualApproval: false,
    formalRecommendationsAllowed: false,
    passedChecks: 0,
    totalChecks: 0,
    checks: [],
  },
};

const EMPTY_CLOUD_EVIDENCE: CloudEvidence = {
  backend: "supabase_storage",
  schemaVersion: "supabase_sqlite_snapshot_v1",
  migrationMode: "dual_write",
  gitDatabaseFallback: true,
  configured: false,
  status: "unconfigured",
  operation: "",
  eventAt: "",
  snapshotKey: "live",
  objectPath: "",
  databaseSha256: "",
  databaseBytes: 0,
  compressedBytes: 0,
  latestScanRunId: null,
  latestTradeDate: "",
  sourceWorkflow: "",
  errorCode: "",
  auditVersion: "",
  auditStatus: "not_run",
  auditAt: "",
  cutoverReady: false,
  passedChecks: 0,
  totalChecks: 0,
  dailySnapshots: 0,
  verifiedPushes: 0,
  workflowCount: 0,
  cutoverChecks: [],
  nextAction: "repair_connection",
  recommendedAction: "完成 Supabase 設定後重新執行雲端驗收。",
  message: "雲端證據層尚未完成第一次驗證。",
};

const EMPTY_REPLAY_ATTRIBUTION: ReplayAttribution = {
  replayRunId: null,
  attributionVersion: "",
  generatedAt: "",
  dimensions: [],
  rows: [],
};

const EMPTY_LEARNABILITY_AUDIT: CandidateLearnabilityAudit = {
  auditVersion: "",
  evaluatedAt: null,
  evaluationScope: "historical_development_validation_diagnostic",
  trainingStart: null,
  trainingEnd: null,
  validationStart: null,
  validationEnd: null,
  holdoutEvaluated: false,
  formalRankingEnabled: false,
  reservedHoldoutTradeDates: 0,
  primarySpecKey: null,
  primaryDiagnosis: "not_available",
  bestDiagnosticSpecKey: null,
  primary: null,
  rows: [],
};

const EMPTY_STRATEGY_CHALLENGER: StrategyChallengerSnapshot = {
  version: "",
  evaluatedAt: null,
  status: "not_evaluated",
  recommendationMode: "cash",
  selectedExperimentKey: null,
  diagnosticLeaderKey: null,
  qualifiedCandidates: 0,
  candidateCount: 0,
  datasetFingerprint: "",
  datasetRows: 0,
  datasetStart: null,
  datasetEnd: null,
  lockedComparisons: 0,
  multipleTestingPsrGate: null,
  selectionUsesHoldout: false,
  formalRankingEnabled: false,
  legacyRulePrefilter: true,
  candidateUniverse: "legacy_rule_candidates",
  prequalifiedCandidates: 0,
  holdout: null,
  candidateLeaderboard: [],
  executionMatrix: [],
};

const EMPTY_ALPHA_LIVE: AlphaLiveSnapshot = {
  status: "not_run",
  signalDate: null,
  generatedAt: null,
  modelVersion: null,
  artifactFingerprint: null,
  confidence: null,
  confidenceThreshold: null,
  universeCount: 0,
  eligibleCount: 0,
  selectedCount: 0,
  signals: [],
};

const EMPTY_ALPHA_FORWARD: AlphaForwardSnapshot = {
  version: "alpha_forward_validation_v1",
  evaluatedAt: null,
  evidenceStartDate: "2026-07-24",
  state: "COLLECTING",
  allowNewPositions: true,
  minimumDecisionDays: 120,
  minimumClosedTrades: 150,
  decisionDays: 0,
  closedTrades: 0,
  openPositions: 0,
  totalReturnPct: 0,
  maxDrawdownPct: 0,
  avgNetReturnPct: null,
  avgExcessReturnPct: null,
  positiveRatePct: null,
  profitableMonthRatePct: null,
  profitableMonthCount: 0,
  probabilisticSharpe: null,
  latestSignalDate: null,
  latestSignalStatus: "not_run",
  universeCoveragePct: 0,
  candidatePoolRows: 0,
  dataQualityStatus: "waiting",
  quoteHealth: {
    tradeDate: null,
    runAt: null,
    coveragePct: null,
    attempts: null,
  },
  warnings: [],
  reasonCodes: ["minimum_evidence_not_reached"],
  gates: [],
  accounts: [],
  cohorts: [],
};

const EMPTY_CAPITAL_TOURNAMENT: CapitalTournament = {
  version: "prospective_capital_tournament_v1",
  evidenceStartDate: "2026-07-20",
  evidenceDays: 0,
  minimumEvidenceDays: 120,
  minimumClosedTrades: 100,
  benchmarkAccountKey: null,
  provisionalLeaderAccountKey: null,
  reviewCandidateAccountKey: null,
  status: "collecting_evidence",
  automaticPromotion: false,
  accounts: [],
};

const EMPTY_PAPER_SETTLEMENT: PaperSettlement = {
  version: "opening_paper_settlement_v1",
  status: "not_run",
  settlementAt: null,
  sessionDate: null,
  source: null,
  entryPolicy: "prior_eod_signal_next_session_open",
  lookaheadProtected: true,
  eligibleCandidates: 0,
  outcomesSaved: 0,
  accountsUpdated: 0,
  newOpenPositions: 0,
  newSkippedOrders: 0,
  newClosedPositions: 0,
  pendingOrders: 0,
  openPositions: 0,
  error: null,
  transitions: [],
};

const EMPTY_GLOBAL_MARKET: GlobalMarketSnapshot = {
  modelVersion: "global_regime_shadow_v1",
  snapshotAt: "",
  score: 50,
  regimeLabel: "資料建立中",
  taiwanBiasScore: 50,
  taiwanBiasLabel: "資料建立中",
  components: [],
  drivers: [],
  instruments: [],
  history: [],
  quality: {
    status: "unavailable",
    coveragePct: 0,
    activeFreshPct: 0,
    available: 0,
    total: 0,
    missingKeys: [],
    warnings: ["跨市場資料尚未完成第一次收集。"],
    formalRankingEnabled: false,
  },
};

const EMPTY_INSTITUTIONAL_FLOW: InstitutionalFlowSnapshot = {
  featureVersion: "institutional_flow_v1_conservative_lag",
  researchGeneration: "generation_2_institutional",
  latestTradeDate: "",
  fetchedAt: "",
  rawRows: 0,
  symbols: 0,
  candidateTargets: 0,
  featureSnapshots: 0,
  completeFeatures: 0,
  coveragePct: 0,
  completeCoveragePct: 0,
  sources: [],
  candidates: [],
  quality: {
    status: "unavailable",
    formalRankingEnabled: false,
    historicalUse: "development_only",
    promotionGate: "prospective_generation_2_evidence",
    warnings: ["法人籌碼資料尚未完成第一次官方收集。"],
  },
};

function normalizeDashboardSnapshot(snapshot: DashboardSnapshot): DashboardSnapshot {
  return {
    ...snapshot,
    overview: {
      ...snapshot.overview,
      candidateOutcomes: snapshot.overview.candidateOutcomes ?? 0,
      candidateMatureT3: snapshot.overview.candidateMatureT3 ?? 0,
      candidateRejectedMatureT3: snapshot.overview.candidateRejectedMatureT3 ?? 0,
      maturePredictionOutcomes: snapshot.overview.maturePredictionOutcomes ?? 0,
      prospectivePredictions: snapshot.overview.prospectivePredictions ?? 0,
      paperAccounts: snapshot.overview.paperAccounts ?? 0,
      paperClosedTrades: snapshot.overview.paperClosedTrades ?? 0,
      paperProspectiveClosedTrades: snapshot.overview.paperProspectiveClosedTrades ?? 0,
    },
    researchQuality: {
      ...EMPTY_RESEARCH_QUALITY,
      ...(snapshot.researchQuality ?? {}),
    },
    researchHealth: {
      ...EMPTY_RESEARCH_HEALTH,
      ...(snapshot.researchHealth ?? {}),
      warnings: snapshot.researchHealth?.warnings ?? EMPTY_RESEARCH_HEALTH.warnings,
      replayDataWarnings: snapshot.researchHealth?.replayDataWarnings ?? [],
      integrityGate: {
        ...EMPTY_RESEARCH_HEALTH.integrityGate,
        ...(snapshot.researchHealth?.integrityGate ?? {}),
        checks: snapshot.researchHealth?.integrityGate?.checks ?? [],
      },
    },
    cloudEvidence: {
      ...EMPTY_CLOUD_EVIDENCE,
      ...(snapshot.cloudEvidence ?? {}),
    },
    replayAttribution: {
      ...EMPTY_REPLAY_ATTRIBUTION,
      ...(snapshot.replayAttribution ?? {}),
      dimensions: snapshot.replayAttribution?.dimensions ?? [],
      rows: snapshot.replayAttribution?.rows ?? [],
    },
    learnabilityAudit: {
      ...EMPTY_LEARNABILITY_AUDIT,
      ...(snapshot.learnabilityAudit ?? {}),
      rows: snapshot.learnabilityAudit?.rows ?? [],
    },
    researchExperiments: snapshot.researchExperiments ?? [],
    aiModels: snapshot.aiModels ?? [],
    modelChallengers: snapshot.modelChallengers ?? [],
    strategyChallenger: {
      ...EMPTY_STRATEGY_CHALLENGER,
      ...(snapshot.strategyChallenger ?? {}),
      candidateLeaderboard: snapshot.strategyChallenger?.candidateLeaderboard ?? [],
      executionMatrix: snapshot.strategyChallenger?.executionMatrix ?? [],
    },
    alphaLive: {
      ...EMPTY_ALPHA_LIVE,
      ...(snapshot.alphaLive ?? {}),
      signals: snapshot.alphaLive?.signals ?? [],
    },
    alphaForward: {
      ...EMPTY_ALPHA_FORWARD,
      ...(snapshot.alphaForward ?? {}),
      quoteHealth: {
        ...EMPTY_ALPHA_FORWARD.quoteHealth,
        ...(snapshot.alphaForward?.quoteHealth ?? {}),
      },
      warnings: snapshot.alphaForward?.warnings ?? [],
      reasonCodes: snapshot.alphaForward?.reasonCodes ?? [],
      gates: snapshot.alphaForward?.gates ?? [],
      accounts: snapshot.alphaForward?.accounts ?? [],
      cohorts: snapshot.alphaForward?.cohorts ?? [],
    },
    paperAccounts: (snapshot.paperAccounts ?? []).map((account) => ({
      ...account,
      comparisonStartAt: account.comparisonStartAt ?? null,
      comparisonReturnPct: account.comparisonReturnPct ?? null,
    })),
    paperEquity: snapshot.paperEquity ?? [],
    paperTrades: snapshot.paperTrades ?? [],
    paperSettlement: {
      ...EMPTY_PAPER_SETTLEMENT,
      ...(snapshot.paperSettlement ?? {}),
      transitions: snapshot.paperSettlement?.transitions ?? [],
    },
    capitalTournament: {
      ...EMPTY_CAPITAL_TOURNAMENT,
      ...(snapshot.capitalTournament ?? {}),
      accounts: snapshot.capitalTournament?.accounts ?? [],
    },
    globalMarket: {
      ...EMPTY_GLOBAL_MARKET,
      ...(snapshot.globalMarket ?? {}),
      quality: {
        ...EMPTY_GLOBAL_MARKET.quality,
        ...(snapshot.globalMarket?.quality ?? {}),
      },
      components: snapshot.globalMarket?.components ?? [],
      drivers: snapshot.globalMarket?.drivers ?? [],
      instruments: snapshot.globalMarket?.instruments ?? [],
      history: snapshot.globalMarket?.history ?? [],
    },
    institutionalFlow: {
      ...EMPTY_INSTITUTIONAL_FLOW,
      ...(snapshot.institutionalFlow ?? {}),
      sources: snapshot.institutionalFlow?.sources ?? [],
      candidates: snapshot.institutionalFlow?.candidates ?? [],
      quality: {
        ...EMPTY_INSTITUTIONAL_FLOW.quality,
        ...(snapshot.institutionalFlow?.quality ?? {}),
        warnings: snapshot.institutionalFlow?.quality?.warnings
          ?? EMPTY_INSTITUTIONAL_FLOW.quality.warnings,
      },
    },
  };
}

async function localSnapshot(): Promise<DashboardSnapshot> {
  const file = path.join(process.cwd(), "public", "dashboard_snapshot.json");
  return normalizeDashboardSnapshot(
    JSON.parse(await readFile(file, "utf8")) as DashboardSnapshot,
  );
}

export async function getDashboardSnapshot(): Promise<DashboardSnapshot> {
  if (process.env.NODE_ENV !== "production") {
    return localSnapshot();
  }
  try {
    const sourceUrl = process.env.DASHBOARD_DATA_URL ?? DEFAULT_DATA_URL;
    const versionedUrl = new URL(sourceUrl);
    versionedUrl.searchParams.set("minute", String(Math.floor(Date.now() / 60_000)));
    const response = await fetch(versionedUrl, {
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error(`snapshot request failed: ${response.status}`);
    const snapshot = (await response.json()) as DashboardSnapshot;
    if (snapshot.schemaVersion !== "dashboard_v2") {
      throw new Error(`unsupported dashboard schema: ${snapshot.schemaVersion}`);
    }
    return normalizeDashboardSnapshot(snapshot);
  } catch {
    return localSnapshot();
  }
}

export async function getWorkflowRuns(): Promise<WorkflowRun[]> {
  try {
    const response = await fetch(ACTIONS_URL, {
      next: { revalidate: 60 },
      headers: {
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
      },
    });
    if (!response.ok) return [];
    const payload = (await response.json()) as {
      workflow_runs?: Array<{
        id: number;
        name: string;
        display_title: string;
        event: string;
        status: string;
        conclusion: string | null;
        created_at: string;
        updated_at: string;
        html_url: string;
      }>;
    };
    return (payload.workflow_runs ?? []).map((run) => ({
      id: run.id,
      name: run.name,
      displayTitle: run.display_title,
      event: run.event,
      status: run.status,
      conclusion: run.conclusion,
      createdAt: run.created_at,
      updatedAt: run.updated_at,
      url: run.html_url,
    }));
  } catch {
    return [];
  }
}
