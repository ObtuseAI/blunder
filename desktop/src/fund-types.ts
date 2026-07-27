export type ServiceState = "ONLINE" | "DEGRADED" | "STARTING" | "OFFLINE" | "UNKNOWN";

export type VenueMode =
  | "RECONCILIATION_ONLY"
  | "MECHANICAL_CANARY"
  | "AGGRESSIVE_BOUNDED"
  | "READ_ONLY"
  | "PAPER"
  | "LIVE"
  | "PAUSED"
  | "FROZEN"
  | "UNKNOWN";

export type CandidateStage =
  | "IDEA"
  | "REPLAY"
  | "POINT_IN_TIME_BACKTEST"
  | "FORWARD_SHADOW"
  | "PAPER"
  | "CAPPED_LIVE_CANARY"
  | "SCALE_REVIEW";

export type BackupState = "VERIFIED" | "STALE" | "FAILED" | "RUNNING" | "UNKNOWN";

export interface ServiceStatus {
  id: "core" | "research-mesh" | "model-gateway" | "dummy-kalshi" | "dopey-robinhood";
  name: string;
  state: ServiceState;
  detail: string;
  heartbeatAgeSeconds: number | null;
  version: string | null;
}

export interface VenueStatus {
  id: "dummy-kalshi" | "dopey-robinhood";
  cell: "Dummy" | "Dopey";
  venue: "Kalshi" | "Robinhood";
  mode: VenueMode;
  reconciliationAgeSeconds: number | null;
  openOrders: number | null;
  openPositions: number | null;
  unknownOutcomes: number | null;
  outboxDepth: number | null;
  accountPinned: boolean | null;
  detail: string;
}

export interface CapitalStatus {
  combinedEnvelopeLimitPct: number;
  utilizedPctOfNav: number | null;
  utilizationPctOfEnvelope: number | null;
  remainingPctOfNav: number | null;
  dummyAllocatedPctOfNav: number | null;
  dopeyAllocatedPctOfNav: number | null;
}

export interface LossStatus {
  dailyLossLimitPct: number;
  dailyLossUsedPct: number | null;
  highWaterDrawdownKillPct: number;
  currentDrawdownPct: number | null;
  stopped: boolean | null;
}

export interface ModelBudgetStatus {
  provider: "OpenRouter";
  dailyLimitUsd: number;
  spentUsd: number | null;
  remainingUsd: number | null;
  resetsAt: string | null;
  researchPaused: boolean | null;
}

export interface CandidateStageStatus {
  stage: CandidateStage;
  count: number | null;
}

export interface EvidenceStatus {
  activeCandidates: number | null;
  quarantinedCandidates: number | null;
  evidenceCourtsPassing: number | null;
  evidenceCourtsTotal: number | null;
  stages: CandidateStageStatus[];
}

export interface BackupStatus {
  state: BackupState;
  lastBackupAt: string | null;
  lastRestoreTestAt: string | null;
  controlLedgerOutboxDepth: number | null;
  eventLedgerSequence: number | null;
  detail: string;
}

export interface FundControlSnapshot {
  schema: "obtuse.dumbmoney.control-snapshot.v1";
  capturedAt: string;
  control: {
    status: "PAUSED" | "DEGRADED" | "LIVE_READY" | "UNKNOWN";
    reasonCodes: string[];
  };
  mandate: {
    accountMode: "PERSONAL_PROP";
    riskPosture: "RISK_ACCEPTED_EXPLORATORY";
    desiredMode: "AGGRESSIVE_BOUNDED" | "RECONCILIATION_ONLY";
    expiresAt: string | null;
  };
  services: ServiceStatus[];
  venues: VenueStatus[];
  capital: CapitalStatus;
  loss: LossStatus;
  modelBudget: ModelBudgetStatus;
  evidence: EvidenceStatus;
  backup: BackupStatus;
  policyEpoch: number | null;
  policyDigest: string | null;
  kill: {
    active: boolean | null;
    generation: number | null;
    reason: string | null;
    changedAt: string | null;
  };
}

export interface SnapshotResult {
  origin: "LIVE_CORE" | "DEMO_UNAVAILABLE";
  snapshot: FundControlSnapshot;
  error: string | null;
}
