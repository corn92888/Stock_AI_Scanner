import { NextRequest, NextResponse } from "next/server";

import {
  activeWorkflowRun,
  dispatchWorkflow,
  INTRADAY_WORKFLOW,
  publicWorkflowRun,
  recentWorkflowRuns,
} from "@/lib/github-actions";
import { secretsMatch } from "@/lib/secret-auth";

export async function GET() {
  try {
    const token = process.env.GITHUB_ACTIONS_TOKEN;
    const runs = await recentWorkflowRuns(INTRADAY_WORKFLOW, token);
    const active = activeWorkflowRun(runs);
    return NextResponse.json({
      configured: Boolean(token && process.env.SCAN_TRIGGER_SECRET),
      active: publicWorkflowRun(active),
      latest: publicWorkflowRun(runs[0]),
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
  if (!secretsMatch(request.headers.get("x-scan-trigger-secret"), triggerSecret)) {
    return NextResponse.json({ error: "掃描控制碼不正確" }, { status: 401 });
  }

  try {
    const runs = await recentWorkflowRuns(INTRADAY_WORKFLOW, token);
    const active = activeWorkflowRun(runs);
    if (active) {
      return NextResponse.json(
        { error: "已有盤中掃描正在執行", run: publicWorkflowRun(active) },
        { status: 409 },
      );
    }

    await dispatchWorkflow(
      INTRADAY_WORKFLOW,
      token,
      { trigger_source: "dashboard", scheduled_cron: "" },
    );
    return NextResponse.json(
      { accepted: true, requestedAt: new Date().toISOString() },
      { status: 202 },
    );
  } catch {
    return NextResponse.json({ error: "觸發掃描時無法連線 GitHub" }, { status: 502 });
  }
}
