import "server-only";

import { readFile } from "node:fs/promises";
import path from "node:path";

import type { DashboardSnapshot, ResearchQuality, WorkflowRun } from "./types";

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
    },
    researchQuality: {
      ...EMPTY_RESEARCH_QUALITY,
      ...(snapshot.researchQuality ?? {}),
    },
    aiModels: snapshot.aiModels ?? [],
  };
}

async function localSnapshot(): Promise<DashboardSnapshot> {
  const file = path.join(process.cwd(), "public", "dashboard_snapshot.json");
  return normalizeDashboardSnapshot(
    JSON.parse(await readFile(file, "utf8")) as DashboardSnapshot,
  );
}

export async function getDashboardSnapshot(): Promise<DashboardSnapshot> {
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
