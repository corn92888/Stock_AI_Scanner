import "server-only";

const REPOSITORY = "corn92888/Stock_AI_Scanner";
const API_ROOT = `https://api.github.com/repos/${REPOSITORY}`;

export const INTRADAY_WORKFLOW = "intraday_scan.yml";
export const DAILY_WORKFLOW = "daily_scan.yml";
export const MARKET_WORKFLOW = "global_market.yml";

export type GitHubRun = {
  id: number;
  status: string;
  conclusion: string | null;
  created_at: string;
  updated_at: string;
  html_url: string;
  display_title?: string;
};

function githubHeaders(token?: string) {
  return {
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

export async function recentWorkflowRuns(workflow: string, token?: string) {
  const response = await fetch(
    `${API_ROOT}/actions/workflows/${workflow}/runs?per_page=8`,
    { headers: githubHeaders(token), cache: "no-store" },
  );
  if (!response.ok) {
    throw new Error(`GitHub runs API returned ${response.status}`);
  }
  const payload = (await response.json()) as { workflow_runs?: GitHubRun[] };
  return payload.workflow_runs ?? [];
}

export function activeWorkflowRun(runs: GitHubRun[]) {
  return runs.find((run) => run.status === "queued" || run.status === "in_progress");
}

export function publicWorkflowRun(run: GitHubRun | undefined) {
  if (!run) return null;
  return {
    id: run.id,
    status: run.status,
    conclusion: run.conclusion,
    createdAt: run.created_at,
    updatedAt: run.updated_at,
    url: run.html_url,
    title: run.display_title ?? null,
  };
}

export async function dispatchWorkflow(
  workflow: string,
  token: string,
  inputs: Record<string, string>,
) {
  const response = await fetch(
    `${API_ROOT}/actions/workflows/${workflow}/dispatches`,
    {
      method: "POST",
      headers: {
        ...githubHeaders(token),
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ref: "main", inputs }),
      cache: "no-store",
    },
  );
  if (!response.ok) {
    throw new Error(`GitHub dispatch API returned ${response.status}`);
  }
}
