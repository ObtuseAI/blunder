import { getJson } from "./api";
import type {
  CandidateStage,
  FundControlSnapshot,
  ServiceState,
  ServiceStatus,
  SnapshotResult,
  VenueMode,
  VenueStatus,
} from "./fund-types";

type UnknownRecord = Record<string, unknown>;

interface CoreControlSnapshotWire {
  schema: "dumbmoney.control-snapshot.v1";
  observed_at: string;
  policy_epoch?: number | null;
  policy_digest?: string | null;
  desired_modes?: {
    dummy_kalshi?: string;
    dopey_robinhood?: string;
  };
  kill_state?: {
    active?: boolean | null;
    generation?: number | null;
    reason?: string | null;
    changed_at?: string | null;
  };
  ledger?: {
    last_global_sequence?: number | null;
    chain_head?: string | null;
  };
  venues?: {
    dummy_kalshi?: UnknownRecord;
    dopey_robinhood?: UnknownRecord;
  };
  openrouter?: {
    daily_budget_cents?: number | null;
    spent_cents?: number | null;
    remaining_cents?: number | null;
    resets_at?: string | null;
    research_paused?: boolean | null;
  };
  services?: unknown;
  capital?: UnknownRecord;
  portfolio?: UnknownRecord;
  loss?: UnknownRecord;
  evidence?: UnknownRecord;
  backup?: UnknownRecord;
  status?: string;
  reason_codes?: string[];
}

const serviceIds: ServiceStatus["id"][] = [
  "core",
  "research-mesh",
  "model-gateway",
  "dummy-kalshi",
  "dopey-robinhood",
];

const candidateStages: CandidateStage[] = [
  "IDEA",
  "REPLAY",
  "POINT_IN_TIME_BACKTEST",
  "FORWARD_SHADOW",
  "PAPER",
  "CAPPED_LIVE_CANARY",
  "SCALE_REVIEW",
];

const serviceNames: Record<ServiceStatus["id"], string> = {
  core: "DumbMoney Core",
  "research-mesh": "Research Mesh",
  "model-gateway": "Model Gateway",
  "dummy-kalshi": "Dummy · Kalshi",
  "dopey-robinhood": "Dopey · Robinhood",
};

const serviceWireKeys: Record<ServiceStatus["id"], string> = {
  core: "DumbMoneyCore",
  "research-mesh": "DumbMoneyResearchMesh",
  "model-gateway": "DumbMoneyModelGateway",
  "dummy-kalshi": "DumbMoneyDummyKalshi",
  "dopey-robinhood": "DumbMoneyDopeyRobinhood",
};

const hasObjectShape = (value: unknown): value is UnknownRecord =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const finite = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

const text = (value: unknown): string | null => (typeof value === "string" ? value : null);

const truth = (value: unknown): boolean | null => (typeof value === "boolean" ? value : null);

const serviceState = (value: unknown): ServiceState =>
  value === "READY" || value === "HEALTHY"
    ? "ONLINE"
    : value === "FAILED"
      ? "OFFLINE"
      : ["ONLINE", "DEGRADED", "STARTING", "OFFLINE", "UNKNOWN"].includes(String(value))
        ? (value as ServiceState)
        : "UNKNOWN";

const venueMode = (value: unknown): VenueMode =>
  [
    "RECONCILIATION_ONLY",
    "MECHANICAL_CANARY",
    "AGGRESSIVE_BOUNDED",
    "READ_ONLY",
    "PAPER",
    "LIVE",
    "PAUSED",
    "FROZEN",
    "UNKNOWN",
  ].includes(String(value))
    ? (value as VenueMode)
    : "UNKNOWN";

const directService = (
  input: unknown,
  id: ServiceStatus["id"],
  capturedAt: string,
): ServiceStatus => {
  const candidate = Array.isArray(input)
    ? input.find((item) => hasObjectShape(item) && String(item.id).replaceAll("_", "-") === id)
    : hasObjectShape(input)
      ? input[serviceWireKeys[id]] ?? input[id] ?? input[id.replaceAll("-", "_")]
      : undefined;
  if (!hasObjectShape(candidate)) {
    return {
      id,
      name: serviceNames[id],
      state: "UNKNOWN",
      detail: "Service telemetry not published by Core",
      heartbeatAgeSeconds: null,
      version: null,
    };
  }
  const observedAt = text(candidate.observed_at ?? candidate.observedAt);
  const heartbeatAgeSeconds =
    finite(candidate.heartbeat_age_seconds ?? candidate.heartbeatAgeSeconds) ??
    (() => {
      if (observedAt === null) return null;
      const observed = new Date(observedAt).valueOf();
      const captured = new Date(capturedAt).valueOf();
      return Number.isFinite(observed) && Number.isFinite(captured)
        ? Math.max(0, (captured - observed) / 1_000)
        : null;
    })();
  return {
    id,
    name: text(candidate.name) ?? serviceNames[id],
    state: serviceState(candidate.state ?? candidate.status),
    detail: text(candidate.detail) ?? text(candidate.reason) ?? "No service detail reported",
    heartbeatAgeSeconds,
    version: text(candidate.version),
  };
};

const venueFromWire = (
  id: VenueStatus["id"],
  cell: VenueStatus["cell"],
  venueName: VenueStatus["venue"],
  input: UnknownRecord | undefined,
  desiredMode: unknown,
): VenueStatus => {
  const wire = input ?? {};
  return {
    id,
    cell,
    venue: venueName,
    mode: venueMode(wire.mode ?? desiredMode),
    reconciliationAgeSeconds: finite(
      wire.reconciliation_age_seconds ?? wire.reconciliationAgeSeconds,
    ),
    openOrders: finite(wire.open_orders ?? wire.openOrders),
    openPositions: finite(wire.open_positions ?? wire.openPositions),
    unknownOutcomes: finite(wire.unknown_outcomes ?? wire.unknownOutcomes),
    outboxDepth: finite(wire.outbox_depth ?? wire.outboxDepth),
    accountPinned: truth(wire.account_pinned ?? wire.accountPinned),
    detail:
      text(wire.detail) ??
      text(wire.reconciliation_status) ??
      "Core has not published complete venue telemetry",
  };
};

const stageCount = (input: UnknownRecord | undefined, stage: CandidateStage): number | null => {
  const stages = input?.stage_counts ?? input?.stageCounts ?? input?.stages;
  const wireStage =
    stage === "CAPPED_LIVE_CANARY"
      ? "EXPLORATORY_LIVE"
      : stage === "SCALE_REVIEW"
        ? "AGGRESSIVE_BOUNDED"
        : stage;
  if (Array.isArray(stages)) {
    const match = stages.find(
      (item) => hasObjectShape(item) && String(item.stage).toUpperCase() === wireStage,
    );
    return hasObjectShape(match) ? finite(match.count) : null;
  }
  if (hasObjectShape(stages)) return finite(stages[wireStage] ?? stages[wireStage.toLowerCase()]);
  return null;
};

const dollars = (cents: unknown): number | null => {
  const amount = finite(cents);
  return amount === null ? null : amount / 100;
};

export const normalizeCoreSnapshot = (wire: CoreControlSnapshotWire): FundControlSnapshot => {
  const capital = wire.portfolio ?? wire.capital ?? {};
  const loss = wire.loss ?? {};
  const evidence = wire.evidence;
  const backup = wire.backup ?? {};
  const dummy = venueFromWire(
    "dummy-kalshi",
    "Dummy",
    "Kalshi",
    wire.venues?.dummy_kalshi,
    wire.desired_modes?.dummy_kalshi,
  );
  const dopey = venueFromWire(
    "dopey-robinhood",
    "Dopey",
    "Robinhood",
    wire.venues?.dopey_robinhood,
    wire.desired_modes?.dopey_robinhood,
  );
  const desiredMode =
    ["AGGRESSIVE_BOUNDED", "LIVE"].includes(dummy.mode) &&
    ["AGGRESSIVE_BOUNDED", "LIVE"].includes(dopey.mode)
      ? "AGGRESSIVE_BOUNDED"
      : "RECONCILIATION_ONLY";
  const dailyLimitUsd = dollars(wire.openrouter?.daily_budget_cents);
  const spentUsd = dollars(wire.openrouter?.spent_cents);
  const remainingUsd = dollars(wire.openrouter?.remaining_cents);
  const controlStatus = ["PAUSED", "DEGRADED", "LIVE_READY"].includes(String(wire.status))
    ? (wire.status as FundControlSnapshot["control"]["status"])
    : null;
  const combinedEnvelopeLimitPct = finite(
    capital.combined_envelope_limit_pct ?? capital.combinedEnvelopeLimitPct,
  );
  const dailyLossLimitPct = finite(loss.daily_loss_limit_pct ?? loss.dailyLossLimitPct);
  const highWaterDrawdownKillPct = finite(
    loss.high_water_drawdown_kill_pct ?? loss.highWaterDrawdownKillPct,
  );
  const reasonCodes = Array.isArray(wire.reason_codes)
    ? wire.reason_codes.filter((value): value is string => typeof value === "string" && value.length > 0)
    : [];
  if (
    controlStatus === null ||
    reasonCodes.length === 0 ||
    combinedEnvelopeLimitPct === null ||
    dailyLossLimitPct === null ||
    highWaterDrawdownKillPct === null ||
    dailyLimitUsd === null ||
    finite(wire.policy_epoch) === null ||
    !/^[0-9a-f]{64}$/.test(text(wire.policy_digest) ?? "") ||
    truth(wire.kill_state?.active) === null ||
    wire.desired_modes?.dummy_kalshi === undefined ||
    wire.desired_modes?.dopey_robinhood === undefined
  ) {
    throw new Error("Core snapshot is missing required signed-policy or readiness telemetry.");
  }

  return {
    schema: "obtuse.dumbmoney.control-snapshot.v1",
    capturedAt: wire.observed_at,
    control: {
      status: controlStatus,
      reasonCodes,
    },
    mandate: {
      accountMode: "PERSONAL_PROP",
      riskPosture: "RISK_ACCEPTED_EXPLORATORY",
      desiredMode,
      expiresAt: null,
    },
    services: serviceIds.map((id) => directService(wire.services, id, wire.observed_at)),
    venues: [dummy, dopey],
    capital: {
      combinedEnvelopeLimitPct,
      utilizedPctOfNav: finite(capital.utilized_pct_of_nav ?? capital.utilizedPctOfNav),
      utilizationPctOfEnvelope: finite(
        capital.utilization_pct_of_envelope ?? capital.utilizationPctOfEnvelope,
      ),
      remainingPctOfNav: finite(capital.remaining_pct_of_nav ?? capital.remainingPctOfNav),
      dummyAllocatedPctOfNav: finite(
        capital.dummy_allocated_pct_of_nav ?? capital.dummyAllocatedPctOfNav,
      ),
      dopeyAllocatedPctOfNav: finite(
        capital.dopey_allocated_pct_of_nav ?? capital.dopeyAllocatedPctOfNav,
      ),
    },
    loss: {
      dailyLossLimitPct,
      dailyLossUsedPct: finite(loss.daily_loss_used_pct ?? loss.dailyLossUsedPct),
      highWaterDrawdownKillPct,
      currentDrawdownPct: finite(loss.current_drawdown_pct ?? loss.currentDrawdownPct),
      stopped: truth(loss.stopped) ?? (wire.kill_state?.active === true ? true : null),
    },
    modelBudget: {
      provider: "OpenRouter",
      dailyLimitUsd,
      spentUsd,
      remainingUsd,
      resetsAt: text(wire.openrouter?.resets_at),
      researchPaused: truth(wire.openrouter?.research_paused),
    },
    evidence: {
      activeCandidates: finite(evidence?.active_candidates ?? evidence?.activeCandidates),
      quarantinedCandidates: finite(
        evidence?.quarantined_candidates ?? evidence?.quarantinedCandidates,
      ),
      evidenceCourtsPassing: finite(
        evidence?.evidence_courts_passing ?? evidence?.evidenceCourtsPassing,
      ),
      evidenceCourtsTotal: finite(
        evidence?.evidence_courts_total ?? evidence?.evidenceCourtsTotal,
      ),
      stages: candidateStages.map((stage) => ({ stage, count: stageCount(evidence, stage) })),
    },
    backup: {
      state: ["VERIFIED", "STALE", "FAILED", "RUNNING", "UNKNOWN"].includes(
        String(backup.state ?? backup.restore_status),
      )
        ? ((backup.state ?? backup.restore_status) as FundControlSnapshot["backup"]["state"])
        : "UNKNOWN",
      lastBackupAt: text(backup.last_backup_at ?? backup.lastBackupAt),
      lastRestoreTestAt: text(
        backup.last_restore_test_at ??
          backup.lastRestoreTestAt ??
          backup.last_restore_at ??
          backup.lastRestoreAt,
      ),
      controlLedgerOutboxDepth: finite(
        backup.control_ledger_outbox_depth ?? backup.controlLedgerOutboxDepth,
      ),
      eventLedgerSequence:
        finite(backup.event_ledger_sequence ?? backup.eventLedgerSequence) ??
        finite(wire.ledger?.last_global_sequence),
      detail:
        text(backup.detail) ??
        (text(wire.ledger?.chain_head)
          ? `Ledger chain ${String(wire.ledger?.chain_head).slice(0, 12)}…`
          : "Backup and restore telemetry not published by Core"),
    },
    policyEpoch: finite(wire.policy_epoch),
    policyDigest: text(wire.policy_digest),
    kill: {
      active: truth(wire.kill_state?.active),
      generation: finite(wire.kill_state?.generation),
      reason: text(wire.kill_state?.reason),
      changedAt: text(wire.kill_state?.changed_at),
    },
  };
};

const isCoreWire = (value: unknown): value is CoreControlSnapshotWire =>
  hasObjectShape(value) &&
  value.schema === "dumbmoney.control-snapshot.v1" &&
  typeof value.observed_at === "string";

const secondsFromNow = (seconds: number): string =>
  new Date(Date.now() + seconds * 1_000).toISOString();

export const createDemoSnapshot = (): FundControlSnapshot => ({
  schema: "obtuse.dumbmoney.control-snapshot.v1",
  capturedAt: new Date().toISOString(),
  control: {
    status: "UNKNOWN",
    reasonCodes: ["LIVE_CONTROL_SNAPSHOT_UNAVAILABLE"],
  },
  mandate: {
    accountMode: "PERSONAL_PROP",
    riskPosture: "RISK_ACCEPTED_EXPLORATORY",
    desiredMode: "RECONCILIATION_ONLY",
    expiresAt: secondsFromNow(86_400 * 28),
  },
  services: serviceIds.map((id) => ({
    id,
    name: serviceNames[id],
    state: "UNKNOWN",
    detail: "Demo placeholder — live service state unavailable",
    heartbeatAgeSeconds: null,
    version: null,
  })),
  venues: [
    venueFromWire("dummy-kalshi", "Dummy", "Kalshi", undefined, "RECONCILIATION_ONLY"),
    venueFromWire("dopey-robinhood", "Dopey", "Robinhood", undefined, "RECONCILIATION_ONLY"),
  ],
  capital: {
    combinedEnvelopeLimitPct: 25,
    utilizedPctOfNav: null,
    utilizationPctOfEnvelope: null,
    remainingPctOfNav: null,
    dummyAllocatedPctOfNav: null,
    dopeyAllocatedPctOfNav: null,
  },
  loss: {
    dailyLossLimitPct: 3,
    dailyLossUsedPct: null,
    highWaterDrawdownKillPct: 10,
    currentDrawdownPct: null,
    stopped: null,
  },
  modelBudget: {
    provider: "OpenRouter",
    dailyLimitUsd: 10,
    spentUsd: null,
    remainingUsd: null,
    resetsAt: null,
    researchPaused: null,
  },
  evidence: {
    activeCandidates: null,
    quarantinedCandidates: null,
    evidenceCourtsPassing: null,
    evidenceCourtsTotal: null,
    stages: candidateStages.map((stage) => ({ stage, count: null })),
  },
  backup: {
    state: "UNKNOWN",
    lastBackupAt: null,
    lastRestoreTestAt: null,
    controlLedgerOutboxDepth: null,
    eventLedgerSequence: null,
    detail: "Backup and restore evidence unavailable in demo mode.",
  },
  policyEpoch: null,
  policyDigest: null,
  kill: {
    active: null,
    generation: null,
    reason: null,
    changedAt: null,
  },
});

export const loadFundControlSnapshot = async (): Promise<SnapshotResult> => {
  try {
    const value = await getJson<unknown>("/api/v1/fund/control-snapshot");
    if (!isCoreWire(value)) {
      throw new Error("Core returned an unsupported or incomplete DumbMoney control snapshot.");
    }
    return { origin: "LIVE_CORE", snapshot: normalizeCoreSnapshot(value), error: null };
  } catch (reason: unknown) {
    return {
      origin: "DEMO_UNAVAILABLE",
      snapshot: createDemoSnapshot(),
      error: reason instanceof Error ? reason.message : String(reason),
    };
  }
};
