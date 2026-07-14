import { NextRequest, NextResponse } from "next/server";

import {
  dispatchWorkflow,
  INTRADAY_WORKFLOW,
} from "@/lib/github-actions";
import { currentIntradaySchedule } from "@/lib/scanner-schedule";
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

  const schedule = currentIntradaySchedule();
  if (!schedule) {
    return NextResponse.json({
      accepted: false,
      reason: "outside_intraday_slots",
      requestedAt: new Date().toISOString(),
    });
  }

  try {
    await dispatchWorkflow(INTRADAY_WORKFLOW, token, {
      trigger_source: "vercel-cron",
      scheduled_cron: schedule.cron,
    });
    return NextResponse.json(
      {
        accepted: true,
        slot: schedule.slot,
        requestedAt: new Date().toISOString(),
      },
      { status: 202 },
    );
  } catch {
    return NextResponse.json(
      { accepted: false, error: "Unable to dispatch intraday workflow" },
      { status: 502 },
    );
  }
}
