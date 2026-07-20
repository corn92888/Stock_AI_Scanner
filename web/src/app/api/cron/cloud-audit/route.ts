import { NextRequest, NextResponse } from "next/server";

import {
  activeWorkflowRun,
  CLOUD_AUDIT_WORKFLOW,
  dispatchWorkflow,
  publicWorkflowRun,
  recentWorkflowRuns,
} from "@/lib/github-actions";
import { cronAuthorized } from "@/lib/secret-auth";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const token = process.env.GITHUB_ACTIONS_TOKEN;
  const cronSecret = process.env.CRON_SECRET;
  if (!token || !cronSecret) {
    return NextResponse.json(
      { accepted: false, error: "Cron service is not configured" },
      { status: 503 },
    );
  }
  if (!cronAuthorized(request.headers.get("authorization"), cronSecret)) {
    return NextResponse.json({ accepted: false, error: "Unauthorized" }, { status: 401 });
  }

  try {
    const runs = await recentWorkflowRuns(CLOUD_AUDIT_WORKFLOW, token);
    const active = activeWorkflowRun(runs);
    if (active) {
      return NextResponse.json({
        accepted: false,
        reason: "workflow_already_active",
        run: publicWorkflowRun(active),
      });
    }

    await dispatchWorkflow(CLOUD_AUDIT_WORKFLOW, token, {
      trigger_source: "vercel-cron",
      require_ready: "true",
    });
    return NextResponse.json(
      { accepted: true, requestedAt: new Date().toISOString() },
      { status: 202 },
    );
  } catch {
    return NextResponse.json(
      { accepted: false, error: "Unable to dispatch cloud evidence audit" },
      { status: 502 },
    );
  }
}
