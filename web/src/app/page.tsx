import DashboardShell from "@/components/dashboard-shell";
import { getDashboardSnapshot, getWorkflowRuns } from "@/lib/data";

export default async function Home({ searchParams }: { searchParams: Promise<{ view?: string }> }) {
  const { view } = await searchParams;
  const [snapshot, workflowRuns] = await Promise.all([
    getDashboardSnapshot(),
    getWorkflowRuns(),
  ]);

  const snapshotFresh = Boolean(snapshot.generatedAt)
    && snapshot.generatedAt.slice(0, 10) >= snapshot.overview.latestTradeDate;

  return (
    <DashboardShell
      snapshot={snapshot}
      workflowRuns={workflowRuns}
      snapshotFresh={snapshotFresh}
      initialView={view}
    />
  );
}
