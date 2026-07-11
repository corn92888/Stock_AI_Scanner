import "server-only";

import { readFile } from "node:fs/promises";
import path from "node:path";

import type { DashboardSnapshot, WorkflowRun } from "./types";

const DEFAULT_DATA_URL =
  "https://raw.githubusercontent.com/corn92888/Stock_AI_Scanner/main/data/dashboard_snapshot.json";
const ACTIONS_URL =
  "https://api.github.com/repos/corn92888/Stock_AI_Scanner/actions/runs?per_page=12";

async function localSnapshot(): Promise<DashboardSnapshot> {
  const file = path.join(process.cwd(), "public", "dashboard_snapshot.json");
  return JSON.parse(await readFile(file, "utf8")) as DashboardSnapshot;
}

export async function getDashboardSnapshot(): Promise<DashboardSnapshot> {
  try {
    const response = await fetch(process.env.DASHBOARD_DATA_URL ?? DEFAULT_DATA_URL, {
      next: { revalidate: 60 },
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error(`snapshot request failed: ${response.status}`);
    const snapshot = (await response.json()) as DashboardSnapshot;
    if (snapshot.schemaVersion !== "dashboard_v1") {
      throw new Error(`unsupported dashboard schema: ${snapshot.schemaVersion}`);
    }
    return snapshot;
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
