import DashboardShell from "@/components/dashboard-shell";
import { getDashboardSnapshot, getWorkflowRuns } from "@/lib/data";

function getTaipeiMarketState(now: Date) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    weekday: "short",
  }).formatToParts(now);
  const value = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? "";
  const minutes = Number(value("hour")) * 60 + Number(value("minute"));
  const weekday = value("weekday");

  return {
    tradeDate: `${value("year")}-${value("month")}-${value("day")}`,
    isTradingSession: weekday !== "Sat" && weekday !== "Sun" && minutes >= 9 * 60 && minutes <= 13 * 60 + 30,
  };
}

export default async function Home({ searchParams }: { searchParams: Promise<{ view?: string }> }) {
  const { view } = await searchParams;
  const [snapshot, workflowRuns] = await Promise.all([
    getDashboardSnapshot(),
    getWorkflowRuns(),
  ]);

  const now = new Date();
  const generatedAt = Date.parse(snapshot.generatedAt ?? "");
  const snapshotAgeMs = now.getTime() - generatedAt;
  const snapshotFresh = Number.isFinite(generatedAt)
    && snapshotAgeMs >= -5 * 60_000
    && snapshotAgeMs <= 2 * 60 * 60_000;
  const marketState = getTaipeiMarketState(now);
  const marketDataFresh = !marketState.isTradingSession
    || snapshot.overview.latestTradeDate >= marketState.tradeDate;

  return (
    <DashboardShell
      snapshot={snapshot}
      workflowRuns={workflowRuns}
      snapshotFresh={snapshotFresh}
      marketDataFresh={marketDataFresh}
      initialView={view}
    />
  );
}
