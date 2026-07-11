export type Overview = {
  latestTradeDate: string;
  latestRunAt: string;
  latestMode: string;
  scanRuns: number;
  signals: number;
  candidateEvents: number;
  featureSnapshots: number;
  predictions: number;
  predictionOutcomes: number;
  modelVersions: number;
  newsEvidence: number;
  backtestResults: number;
  formalSelections: number;
  formalBacktestResults: number;
  formalMatureT3: number;
  formalCompleteT20: number;
  matureT3: number;
  completeT20: number;
};

export type Candidate = {
  tradeDate: string;
  runAt: string;
  selectionRank: number | null;
  rawRank: number;
  code: string;
  name: string;
  industry: string;
  strategies: string[];
  score: number;
  signalPrice: number;
  pctChange: number;
  turnoverBillion: number;
  volumeRatio5: number;
  intradayPosition: number | null;
  observationPrice: number | null;
  chaseLimit: number | null;
  stopDistancePct: number | null;
  tradable: boolean;
  isSelected: boolean;
  selectionStatus: string;
  statusLabel: string;
  riskFlags: string[];
  blockReasons: string[];
  policyVersion: string;
};

export type DailyCandidate = {
  tradeDate: string;
  candidates: number;
  tradable: number;
  selected: number;
  analyzedRuns: number;
};

export type Performance = {
  tradeDate: string;
  mode: string;
  strategy: string;
  strategyLabel: string;
  code: string;
  name: string;
  maturedHorizon: number;
  outcomeStatus: string;
  netReturn1d: number | null;
  netReturn3d: number | null;
  netReturn5d: number | null;
  excessReturn3d: number | null;
  maxReturn3d: number | null;
  maxDrawdown3d: number | null;
  successT3: boolean | null;
  costsBps: number;
  entryMethod: string;
  testedAt: string;
  isFormalSelection: boolean;
  policyVersion: string;
};

export type ScanRun = {
  id: number;
  runAt: string;
  tradeDate: string;
  mode: string;
  source: string;
  strategyVersion: string;
  gitCommit: string;
  reportPath: string;
  automationSlot: string;
};

export type BacktestRun = {
  id: number;
  startedAt: string;
  finishedAt: string | null;
  status: string;
  signalsRequested: number;
  completedCount: number;
  partialCount: number;
  skippedCount: number;
  errorText: string | null;
  selectionScope: string;
};

export type DashboardSnapshot = {
  schemaVersion: string;
  generatedAt: string;
  candidateDetailDays: number;
  overview: Overview;
  candidates: Candidate[];
  dailyCandidates: DailyCandidate[];
  statusCounts: { status: string; count: number; label: string }[];
  performance: Performance[];
  scanRuns: ScanRun[];
  backtestRuns: BacktestRun[];
};

export type WorkflowRun = {
  id: number;
  name: string;
  event: string;
  status: string;
  conclusion: string | null;
  createdAt: string;
  updatedAt: string;
  url: string;
};
