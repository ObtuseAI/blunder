import { useCallback, useEffect, useMemo, useState } from "react";
import { createDemoSnapshot, loadFundControlSnapshot } from "./fund-data";
import type {
  BackupState,
  CandidateStage,
  FundControlSnapshot,
  ServiceState,
  SnapshotResult,
  VenueMode,
  VenueStatus,
} from "./fund-types";

const refreshIntervalMs = 30_000;

const serviceGlyphs: Record<FundControlSnapshot["services"][number]["id"], string> = {
  core: "CO",
  "research-mesh": "RM",
  "model-gateway": "MG",
  "dummy-kalshi": "DK",
  "dopey-robinhood": "DR",
};

const stageLabels: Record<CandidateStage, string> = {
  IDEA: "Idea",
  REPLAY: "Replay",
  POINT_IN_TIME_BACKTEST: "PIT backtest",
  FORWARD_SHADOW: "Forward shadow",
  PAPER: "Paper",
  CAPPED_LIVE_CANARY: "Live canary",
  SCALE_REVIEW: "Scale review",
};

const formatNumber = (value: number | null): string =>
  value === null ? "UNKNOWN" : new Intl.NumberFormat("en-US").format(value);

const formatPercent = (value: number | null, digits = 1): string =>
  value === null ? "UNKNOWN" : `${value.toFixed(digits)}%`;

const formatUsd = (value: number | null): string =>
  value === null
    ? "UNKNOWN"
    : new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      }).format(value);

const formatTime = (value: string | null): string => {
  if (value === null) return "UNKNOWN";
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? "UNKNOWN"
    : new Intl.DateTimeFormat("en-US", {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
        second: "2-digit",
      }).format(date);
};

const formatAge = (seconds: number | null): string => {
  if (seconds === null) return "UNKNOWN";
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))}s`;
  if (seconds < 3_600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3_600).toFixed(1)}h`;
};

const clippedRatio = (value: number | null, maximum: number): number =>
  value === null || maximum <= 0 ? 0 : Math.max(0, Math.min(100, (value / maximum) * 100));

const shortDigest = (value: string | null): string =>
  value === null || value.length === 0 ? "UNKNOWN" : `${value.slice(0, 12)}…`;

const toneForService = (state: ServiceState): "good" | "warn" | "bad" | "muted" => {
  if (state === "ONLINE") return "good";
  if (state === "DEGRADED" || state === "STARTING") return "warn";
  if (state === "OFFLINE") return "bad";
  return "muted";
};

const toneForMode = (mode: VenueMode): "good" | "warn" | "bad" | "muted" => {
  if (mode === "AGGRESSIVE_BOUNDED" || mode === "MECHANICAL_CANARY" || mode === "LIVE") return "good";
  if (mode === "RECONCILIATION_ONLY" || mode === "READ_ONLY" || mode === "PAPER" || mode === "PAUSED") return "warn";
  if (mode === "FROZEN") return "bad";
  return "muted";
};

const toneForBackup = (state: BackupState): "good" | "warn" | "bad" | "muted" => {
  if (state === "VERIFIED") return "good";
  if (state === "RUNNING" || state === "STALE") return "warn";
  if (state === "FAILED") return "bad";
  return "muted";
};

const toneForControl = (
  status: FundControlSnapshot["control"]["status"],
): "good" | "warn" | "bad" | "muted" => {
  if (status === "LIVE_READY") return "good";
  if (status === "DEGRADED") return "warn";
  if (status === "PAUSED") return "bad";
  return "muted";
};

const MetricBar = ({
  label,
  value,
  maximum,
  display,
  limit,
  tone = "lime",
}: {
  label: string;
  value: number | null;
  maximum: number;
  display: string;
  limit: string;
  tone?: "lime" | "amber" | "cyan";
}) => (
  <div className="metric-bar">
    <div className="metric-bar-copy">
      <span>{label}</span>
      <strong className={value === null ? "unknown-value" : ""}>{display}</strong>
    </div>
    <div
      className={`bar-track bar-${tone}`}
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={maximum}
      aria-valuenow={value ?? undefined}
      aria-valuetext={value === null ? "Unknown" : display}
    >
      <span style={{ width: `${clippedRatio(value, maximum)}%` }} />
    </div>
    <small>{limit}</small>
  </div>
);

const ServiceCard = ({
  service,
}: {
  service: FundControlSnapshot["services"][number];
}) => {
  const tone = toneForService(service.state);
  return (
    <article className={`service-card tone-${tone}`}>
      <div className="service-card-head">
        <span className="service-glyph" aria-hidden="true">{serviceGlyphs[service.id]}</span>
        <span className={`state-chip tone-${tone}`}><i aria-hidden="true" />{service.state}</span>
      </div>
      <strong>{service.name}</strong>
      <p>{service.detail}</p>
      <div className="service-meta">
        <span>Heartbeat {formatAge(service.heartbeatAgeSeconds)}</span>
        <span>{service.version ?? "Version unknown"}</span>
      </div>
    </article>
  );
};

const VenueCard = ({ venue }: { venue: VenueStatus }) => {
  const modeTone = toneForMode(venue.mode);
  return (
    <article className="venue-card">
      <div className="venue-head">
        <div>
          <span className="section-kicker">{venue.cell} cell</span>
          <h3>{venue.venue}</h3>
        </div>
        <span className={`mode-chip tone-${modeTone}`}>{venue.mode.replaceAll("_", " ")}</span>
      </div>
      <p className="venue-detail">{venue.detail}</p>
      <dl className="venue-metrics">
        <div>
          <dt>Reconciled</dt>
          <dd className={venue.reconciliationAgeSeconds === null ? "unknown-value" : ""}>
            {venue.reconciliationAgeSeconds === null ? "UNKNOWN" : `${formatAge(venue.reconciliationAgeSeconds)} ago`}
          </dd>
        </div>
        <div>
          <dt>Open orders</dt>
          <dd className={venue.openOrders === null ? "unknown-value" : ""}>{formatNumber(venue.openOrders)}</dd>
        </div>
        <div>
          <dt>Positions</dt>
          <dd className={venue.openPositions === null ? "unknown-value" : ""}>{formatNumber(venue.openPositions)}</dd>
        </div>
        <div>
          <dt>Unknown outcomes</dt>
          <dd className={venue.unknownOutcomes === null ? "unknown-value" : ""}>{formatNumber(venue.unknownOutcomes)}</dd>
        </div>
        <div>
          <dt>Outbox depth</dt>
          <dd className={venue.outboxDepth === null ? "unknown-value" : ""}>{formatNumber(venue.outboxDepth)}</dd>
        </div>
        <div>
          <dt>Account pinned</dt>
          <dd className={venue.accountPinned === null ? "unknown-value" : ""}>
            {venue.accountPinned === null ? "UNKNOWN" : venue.accountPinned ? "YES" : "NO"}
          </dd>
        </div>
      </dl>
    </article>
  );
};

const createInitialResult = (): SnapshotResult => ({
  origin: "DEMO_UNAVAILABLE",
  snapshot: createDemoSnapshot(),
  error: "Waiting for the authenticated DumbMoney Core snapshot.",
});

export const App = () => {
  const [result, setResult] = useState<SnapshotResult>(createInitialResult);
  const [refreshing, setRefreshing] = useState(false);

  const refresh = useCallback(async (): Promise<void> => {
    setRefreshing(true);
    try {
      setResult(await loadFundControlSnapshot());
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), refreshIntervalMs);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const snapshot = result.snapshot;
  const openRouterUsage = useMemo(() => {
    if (snapshot.modelBudget.spentUsd === null || snapshot.modelBudget.dailyLimitUsd <= 0) return null;
    return (snapshot.modelBudget.spentUsd / snapshot.modelBudget.dailyLimitUsd) * 100;
  }, [snapshot.modelBudget.dailyLimitUsd, snapshot.modelBudget.spentUsd]);
  const live = result.origin === "LIVE_CORE";
  const killTone =
    snapshot.kill.active === true ? "bad" : snapshot.kill.active === false ? "good" : "muted";
  const lossTelemetryKnown =
    snapshot.loss.stopped !== null &&
    snapshot.loss.dailyLossUsedPct !== null &&
    snapshot.loss.currentDrawdownPct !== null;
  const controlTone = toneForControl(snapshot.control.status);
  const controlReasons =
    snapshot.control.reasonCodes.length === 0
      ? "No control-plane reason codes published"
      : snapshot.control.reasonCodes.join(" · ");

  return (
    <main className="app-shell" data-origin={result.origin} aria-busy={refreshing}>
      <header className="topbar">
        <div className="wordmark">
          <span className="brand-mark" aria-hidden="true">DM</span>
          <div>
            <strong>DumbMoney</strong>
            <span>Autonomous personal-prop control room</span>
          </div>
        </div>
        <div className="identity-strip" aria-label="Operating identity">
          <span className="identity-tag personal">PERSONAL_PROP</span>
          <span className="identity-tag exploratory">RISK_ACCEPTED_EXPLORATORY</span>
        </div>
        <div className="snapshot-controls">
          <div>
            <span className={`source-chip ${live ? "source-live" : "source-demo"}`}>
              <i aria-hidden="true" />
              {live ? "LIVE CORE" : "DEMO · CORE UNAVAILABLE"}
            </span>
            <time>{formatTime(snapshot.capturedAt)}</time>
          </div>
          <button type="button" className="refresh-button" onClick={() => void refresh()} disabled={refreshing}>
            {refreshing ? "Refreshing…" : "Refresh readout"}
          </button>
        </div>
      </header>

      <aside className="sidebar">
        <nav aria-label="Control room sections">
          <a href="#system"><span>01</span>System</a>
          <a href="#capital"><span>02</span>Capital</a>
          <a href="#venues"><span>03</span>Venues</a>
          <a href="#evidence"><span>04</span>Evidence</a>
          <a href="#operations"><span>05</span>Operations</a>
        </nav>
        <section className="rail-boundary">
          <span className="section-kicker">Authority boundary</span>
          <strong>Observe only</strong>
          <p>No orders, capital edits, credentials, transfers, or mandate changes exist in this interface.</p>
        </section>
        <section className="rail-policy">
          <span>Policy epoch</span>
          <strong>{snapshot.policyEpoch === null ? "UNKNOWN" : `#${snapshot.policyEpoch}`}</strong>
          <code>{shortDigest(snapshot.policyDigest)}</code>
        </section>
      </aside>

      <section className="workspace">
        {!live ? (
          <section className="demo-banner" role="alert">
            <strong>DEMO DATA — LIVE CONTROL SNAPSHOT UNAVAILABLE</strong>
            <span>Every value below is a layout placeholder or UNKNOWN. Do not use this screen for trading decisions.</span>
            {result.error !== null ? <code>{result.error}</code> : null}
          </section>
        ) : null}

        <section className="hero">
          <div>
            <span className="section-kicker">DumbMoney / command readout</span>
            <h1>Autonomy, without ambiguity.</h1>
            <p>One read-only view of system health, broker reconciliation, bounded capital, research spend, and proof state.</p>
          </div>
          <div className="hero-state">
            <span className="section-kicker">Authoritative control state</span>
            <span className={`state-chip tone-${controlTone}`}>
              <i aria-hidden="true" />
              {snapshot.control.status.replaceAll("_", " ")}
            </span>
            <small title={controlReasons}>{controlReasons}</small>
            <span className="section-kicker">Desired operating mode</span>
            <strong>{snapshot.mandate.desiredMode.replaceAll("_", " ")}</strong>
            <span className={`kill-chip tone-${killTone}`}>
              <i aria-hidden="true" />
              {snapshot.kill.active === null
                ? "KILL STATE UNKNOWN"
                : snapshot.kill.active
                  ? "GLOBAL KILL ACTIVE"
                  : "GLOBAL KILL CLEAR"}
            </span>
          </div>
        </section>

        <section className="dashboard-section" id="system">
          <div className="section-heading">
            <div>
              <span className="section-index">01</span>
              <div>
                <span className="section-kicker">Runtime fabric</span>
                <h2>Five-service state</h2>
              </div>
            </div>
            <p>Missing telemetry remains UNKNOWN. The desktop never infers health from silence.</p>
          </div>
          <div className="service-grid">
            {snapshot.services.map((service) => <ServiceCard key={service.id} service={service} />)}
          </div>
        </section>

        <section className="dashboard-section" id="capital">
          <div className="section-heading">
            <div>
              <span className="section-index">02</span>
              <div>
                <span className="section-kicker">Bounded authority</span>
                <h2>Capital, loss, and compute</h2>
              </div>
            </div>
            <p>Percentages are measured against explicitly designated DUMBMONEY_NAV.</p>
          </div>
          <div className="capital-grid">
            <article className="panel capital-panel">
              <div className="panel-heading">
                <div>
                  <span className="section-kicker">Capital envelope</span>
                  <h3>{formatPercent(snapshot.capital.utilizationPctOfEnvelope)} utilized</h3>
                </div>
                <span className="ceiling-chip">{formatPercent(snapshot.capital.combinedEnvelopeLimitPct, 0)} NAV ceiling</span>
              </div>
              <MetricBar
                label="Combined deployed / collateral"
                value={snapshot.capital.utilizedPctOfNav}
                maximum={snapshot.capital.combinedEnvelopeLimitPct}
                display={formatPercent(snapshot.capital.utilizedPctOfNav)}
                limit={`${formatPercent(snapshot.capital.remainingPctOfNav)} NAV capacity remaining`}
              />
              <div className="split-metrics">
                <div><span>Dummy · Kalshi</span><strong>{formatPercent(snapshot.capital.dummyAllocatedPctOfNav)}</strong><small>12.5% max sleeve</small></div>
                <div><span>Dopey · Robinhood</span><strong>{formatPercent(snapshot.capital.dopeyAllocatedPctOfNav)}</strong><small>12.5% max sleeve</small></div>
              </div>
            </article>

            <article className="panel loss-panel">
              <div className="panel-heading">
                <div>
                  <span className="section-kicker">Loss controls</span>
                  <h3>
                    {snapshot.loss.stopped === true
                      ? "Loss stop engaged"
                      : lossTelemetryKnown
                        ? "Within published policy"
                        : "Loss telemetry unknown"}
                  </h3>
                </div>
                <span
                  className={`state-chip tone-${
                    snapshot.loss.stopped === true ? "bad" : lossTelemetryKnown ? "good" : "muted"
                  }`}
                >
                  <i aria-hidden="true" />
                  {snapshot.loss.stopped === true ? "STOPPED" : lossTelemetryKnown ? "READOUT" : "UNKNOWN"}
                </span>
              </div>
              <MetricBar
                label="Combined daily loss"
                value={snapshot.loss.dailyLossUsedPct}
                maximum={snapshot.loss.dailyLossLimitPct}
                display={formatPercent(snapshot.loss.dailyLossUsedPct)}
                limit={`${formatPercent(snapshot.loss.dailyLossLimitPct)} daily stop`}
                tone="amber"
              />
              <MetricBar
                label="High-water drawdown"
                value={snapshot.loss.currentDrawdownPct}
                maximum={snapshot.loss.highWaterDrawdownKillPct}
                display={formatPercent(snapshot.loss.currentDrawdownPct)}
                limit={`${formatPercent(snapshot.loss.highWaterDrawdownKillPct)} kill threshold`}
                tone="amber"
              />
            </article>

            <article className="panel model-panel">
              <div className="panel-heading">
                <div>
                  <span className="section-kicker">Research compute</span>
                  <h3>OpenRouter</h3>
                </div>
                <span
                  className={`state-chip tone-${
                    snapshot.modelBudget.researchPaused === true ? "warn" : snapshot.modelBudget.researchPaused === false ? "muted" : "muted"
                  }`}
                >
                  <i aria-hidden="true" />
                  {snapshot.modelBudget.researchPaused === true
                    ? "PAUSED"
                    : snapshot.modelBudget.researchPaused === false
                      ? "$10 HARD CAP"
                      : "STATE UNKNOWN"}
                </span>
              </div>
              <div className="model-spend">
                <strong>{formatUsd(snapshot.modelBudget.spentUsd)}</strong>
                <span>of {formatUsd(snapshot.modelBudget.dailyLimitUsd)} today</span>
              </div>
              <MetricBar
                label="Daily model budget"
                value={openRouterUsage}
                maximum={100}
                display={formatPercent(openRouterUsage)}
                limit={`${formatUsd(snapshot.modelBudget.remainingUsd)} remaining`}
                tone="cyan"
              />
              <small className="reset-copy">
                Reset {snapshot.modelBudget.resetsAt === null ? "UNKNOWN" : formatTime(snapshot.modelBudget.resetsAt)}
              </small>
            </article>
          </div>
        </section>

        <section className="dashboard-section" id="venues">
          <div className="section-heading">
            <div>
              <span className="section-index">03</span>
              <div>
                <span className="section-kicker">Sovereign cells</span>
                <h2>Venue truth</h2>
              </div>
            </div>
            <p>Dummy remains Kalshi-only. Dopey remains Robinhood-only. Neither credential crosses cells.</p>
          </div>
          <div className="venue-grid">
            {snapshot.venues.map((venue) => <VenueCard key={venue.id} venue={venue} />)}
          </div>
        </section>

        <section className="dashboard-section" id="evidence">
          <div className="section-heading">
            <div>
              <span className="section-index">04</span>
              <div>
                <span className="section-kicker">Proof-carrying alpha forge</span>
                <h2>Candidate evidence</h2>
              </div>
            </div>
            <p>Stages describe evidence maturity and authority scope. They do not claim profitability.</p>
          </div>
          <article className="panel evidence-panel">
            <div className="evidence-summary">
              <div>
                <span>Active candidates</span>
                <strong className={snapshot.evidence.activeCandidates === null ? "unknown-value" : ""}>
                  {formatNumber(snapshot.evidence.activeCandidates)}
                </strong>
              </div>
              <div>
                <span>Quarantined</span>
                <strong className={snapshot.evidence.quarantinedCandidates === null ? "unknown-value" : ""}>
                  {formatNumber(snapshot.evidence.quarantinedCandidates)}
                </strong>
              </div>
              <div>
                <span>Evidence courts</span>
                <strong className={snapshot.evidence.evidenceCourtsPassing === null ? "unknown-value" : ""}>
                  {snapshot.evidence.evidenceCourtsPassing === null ||
                  snapshot.evidence.evidenceCourtsTotal === null
                    ? "UNKNOWN"
                    : `${snapshot.evidence.evidenceCourtsPassing}/${snapshot.evidence.evidenceCourtsTotal}`}
                </strong>
              </div>
            </div>
            <div className="stage-pipeline" aria-label="Candidate stages">
              {snapshot.evidence.stages.map((stage, index) => (
                <div className="stage-node" key={stage.stage}>
                  <span className="stage-order">{String(index + 1).padStart(2, "0")}</span>
                  <strong>{stageLabels[stage.stage]}</strong>
                  <span className={stage.count === null ? "unknown-value" : ""}>{formatNumber(stage.count)}</span>
                </div>
              ))}
            </div>
          </article>
        </section>

        <section className="dashboard-section" id="operations">
          <div className="section-heading">
            <div>
              <span className="section-index">05</span>
              <div>
                <span className="section-kicker">Durability</span>
                <h2>Backup and restore evidence</h2>
              </div>
            </div>
            <p>A backup is not trusted until a restore test has been witnessed.</p>
          </div>
          <div className="operations-grid">
            <article className="panel operations-panel">
              <div className="panel-heading">
                <div>
                  <span className="section-kicker">Control ledger</span>
                  <h3>Recovery posture</h3>
                </div>
                <span className={`state-chip tone-${toneForBackup(snapshot.backup.state)}`}>
                  <i aria-hidden="true" />{snapshot.backup.state}
                </span>
              </div>
              <p>{snapshot.backup.detail}</p>
              <dl className="operations-list">
                <div><dt>Last backup</dt><dd>{formatTime(snapshot.backup.lastBackupAt)}</dd></div>
                <div><dt>Last restore test</dt><dd>{formatTime(snapshot.backup.lastRestoreTestAt)}</dd></div>
                <div><dt>Ledger sequence</dt><dd>{formatNumber(snapshot.backup.eventLedgerSequence)}</dd></div>
                <div><dt>Control outbox</dt><dd>{formatNumber(snapshot.backup.controlLedgerOutboxDepth)}</dd></div>
              </dl>
            </article>
            <aside className="truth-card">
              <span className="section-kicker">Operator truth</span>
              <h3>This screen does not trade.</h3>
              <p>It exposes state and evidence only. Orders, cancellations, credentials, capital changes, and mandate signing remain outside the renderer.</p>
              <ul>
                <li>Unknown is never displayed as zero.</li>
                <li>Demo data is never displayed as live.</li>
                <li>Health is not evidence of after-cost edge.</li>
                <li>Cancellation is not represented as a flat book.</li>
              </ul>
            </aside>
          </div>
        </section>

        <footer className="footer">
          <span>DumbMoney operator desktop</span>
          <span>DumbMoney product · Blunder control plane</span>
          <span>Read-only · no profitability claim</span>
        </footer>
      </section>
    </main>
  );
};
