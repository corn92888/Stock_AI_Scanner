import { timingSafeEqual } from "node:crypto";

import { NextRequest, NextResponse } from "next/server";

const REPOSITORY = "corn92888/Stock_AI_Scanner";
const WORKFLOW = "intraday_scan.yml";
const API_ROOT = `https://api.github.com/repos/${REPOSITORY}`;

type GitHubRun = {
  id: number;
  status: string;
  conclusion: string | null;
  created_at: string;
  updated_at: string;
  html_url: string;
};

function githubHeaders(token?: string) {
  return {
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

function authorized(supplied: string | null, expected: string) {
  if (!supplied) return false;
  const suppliedBuffer = Buffer.from(supplied);
  const expectedBuffer = Buffer.from(expected);
  return suppliedBuffer.length === expectedBuffer.length
    && timingSafeEqual(suppliedBuffer, expectedBuffer);
}

async function recentRuns(token?: string) {
  const response = await fetch(
    `${API_ROOT}/actions/workflows/${WORKFLOW}/runs?per_page=8`,
    { headers: githubHeaders(token), cache: "no-store" },
  );
  if (!response.ok) {
    throw new Error(`GitHub runs API returned ${response.status}`);
  }
  const payload = (await response.json()) as { workflow_runs?: GitHubRun[] };
  return payload.workflow_runs ?? [];
}

function publicRun(run: GitHubRun | undefined) {
  if (!run) return null;
  return {
    id: run.id,
    status: run.status,
    conclusion: run.conclusion,
    createdAt: run.created_at,
    updatedAt: run.updated_at,
    url: run.html_url,
  };
}

export async function GET() {
  try {
    const token = process.env.GITHUB_ACTIONS_TOKEN;
    const runs = await recentRuns(token);
    const active = runs.find((run) => run.status === "queued" || run.status === "in_progress");
    return NextResponse.json({
      configured: Boolean(token && process.env.SCAN_TRIGGER_SECRET),
      active: publicRun(active),
      latest: publicRun(runs[0]),
    });
  } catch {
    return NextResponse.json(
      { configured: false, active: null, latest: null, error: "無法取得掃描狀態" },
      { status: 502 },
    );
  }
}

export async function POST(request: NextRequest) {
  const token = process.env.GITHUB_ACTIONS_TOKEN;
  const triggerSecret = process.env.SCAN_TRIGGER_SECRET;
  if (!token || !triggerSecret) {
    return NextResponse.json(
      { error: "站內掃描尚未完成伺服器端設定" },
      { status: 503 },
    );
  }
  if (!authorized(request.headers.get("x-scan-trigger-secret"), triggerSecret)) {
    return NextResponse.json({ error: "掃描控制碼不正確" }, { status: 401 });
  }

  try {
    const runs = await recentRuns(token);
    const active = runs.find((run) => run.status === "queued" || run.status === "in_progress");
    if (active) {
      return NextResponse.json(
        { error: "已有盤中掃描正在執行", run: publicRun(active) },
        { status: 409 },
      );
    }

    const response = await fetch(
      `${API_ROOT}/actions/workflows/${WORKFLOW}/dispatches`,
      {
        method: "POST",
        headers: {
          ...githubHeaders(token),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          ref: "main",
          inputs: { trigger_source: "dashboard" },
        }),
        cache: "no-store",
      },
    );
    if (!response.ok) {
      return NextResponse.json(
        { error: `GitHub 拒絕觸發掃描 (${response.status})` },
        { status: 502 },
      );
    }
    return NextResponse.json(
      { accepted: true, requestedAt: new Date().toISOString() },
      { status: 202 },
    );
  } catch {
    return NextResponse.json({ error: "觸發掃描時無法連線 GitHub" }, { status: 502 });
  }
}
