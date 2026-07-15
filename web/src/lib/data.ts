import "server-only";

import { readFile } from "node:fs/promises";
import path from "node:path";

import type { DashboardSnapshot, GlobalMarketSnapshot, ReplayAttribution, ResearchHealth, ResearchQuality, WorkflowRun } from "./types";

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
};

const EMPTY_REPLAY_ATTRIBUTION: ReplayAttribution = {
  replayRunId: null,
  attributionVersion: "",
  generatedAt: "",
  dimensions: [],
  rows: [],
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
    },
    replayAttribution: {
      ...EMPTY_REPLAY_ATTRIBUTION,
      ...(snapshot.replayAttribution ?? {}),
      dimensions: snapshot.replayAttribution?.dimensions ?? [],
      rows: snapshot.replayAttribution?.rows ?? [],
    },
    researchExperiments: snapshot.researchExperiments ?? [],
    aiModels: snapshot.aiModels ?? [],
    modelChallengers: snapshot.modelChallengers ?? [],
    paperAccounts: (snapshot.paperAccounts ?? []).map((account) => ({
      ...account,
      comparisonStartAt: account.comparisonStartAt ?? null,
      comparisonReturnPct: account.comparisonReturnPct ?? null,
    })),
    paperEquity: snapshot.paperEquity ?? [],
    paperTrades: snapshot.paperTrades ?? [],
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
