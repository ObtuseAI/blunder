import { useCallback, useEffect, useMemo, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { getJson, postJson } from "./api";
import type {
  ApprovalRequest,
  CreateTaskRequest,
  ProjectList,
  ProjectSummary,
  PromotionState,
  TaskEvent,
  TaskList,
  TaskSnapshot,
} from "./types";

type Navigation = "Projects" | "Work" | "Review" | "Library";

const navigation: Navigation[] = ["Projects", "Work", "Review", "Library"];

const emptyRequest = (): CreateTaskRequest => ({
  objective: "",
  constraints: ["Keep external authority disabled"],
  successCriteria: ["Produce reviewable evidence linked to the source revision"],
  deliverables: ["Evidence-backed result"],
  assumptions: [],
  unknowns: [],
});

const shortDigest = (value: string): string => value.slice(0, 12);

const TaskCard = ({ task, onSelect }: { task: TaskSnapshot; onSelect: (task: TaskSnapshot) => void }) => (
  <button className="task-card" type="button" onClick={() => onSelect(task)}>
    <span className={`state-dot state-${task.state.toLowerCase()}`} aria-hidden="true" />
    <span className="task-card-copy">
      <strong>{task.contract.objective}</strong>
      <small>{task.state.replaceAll("_", " ")} · {task.stage}</small>
    </span>
    <time>{new Date(task.updatedAt).toLocaleTimeString()}</time>
  </button>
);

const ApprovalControl = ({
  approval,
  onDecision,
}: {
  approval: ApprovalRequest;
  onDecision: (approval: ApprovalRequest, decision: string) => Promise<void>;
}) => (
  <section className="approval-panel">
    <span className="eyebrow">Needs approval</span>
    <h3>{approval.kind.replaceAll("-", " ")}</h3>
    <p>{approval.reason}</p>
    <div className="button-row">
      <button type="button" className="primary" onClick={() => void onDecision(approval, approval.exactApprovePhrase)}>
        Approve bounded exception
      </button>
      <button type="button" className="quiet danger" onClick={() => void onDecision(approval, approval.exactDenyPhrase)}>
        Deny
      </button>
    </div>
  </section>
);

export const App = () => {
  const [activeNavigation, setActiveNavigation] = useState<Navigation>("Work");
  const [project, setProject] = useState<ProjectSummary | null>(null);
  const [tasks, setTasks] = useState<TaskSnapshot[]>([]);
  const [selectedTask, setSelectedTask] = useState<TaskSnapshot | null>(null);
  const [request, setRequest] = useState<CreateTaskRequest>(emptyRequest());
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [promotions, setPromotions] = useState<PromotionState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  const refresh = useCallback(async (): Promise<void> => {
    const [projects, taskList, promotionState] = await Promise.all([
      getJson<ProjectList>("/api/v1/projects"),
      getJson<TaskList>("/api/v1/tasks"),
      getJson<PromotionState>("/api/v1/promotions"),
    ]);
    const currentProject = projects.projects.find((item) => item.active) ?? null;
    setProject(currentProject);
    setTasks(taskList.tasks);
    setPromotions(promotionState);
    setSelectedTask((current) => {
      if (current === null) return taskList.tasks[0] ?? null;
      return taskList.tasks.find((item) => item.contract.taskId === current.contract.taskId) ?? current;
    });
  }, []);

  useEffect(() => {
    void refresh().catch((reason: unknown) => setError(String(reason)));
    const unlisten = listen<TaskEvent>("task-event", (event) => {
      setEvents((current) => [event.payload, ...current].slice(0, 100));
      void refresh().catch((reason: unknown) => setError(String(reason)));
    });
    return () => {
      void unlisten.then((stop) => stop());
    };
  }, [refresh]);

  const submit = async (): Promise<void> => {
    setWorking(true);
    setError(null);
    try {
      const task = await postJson<TaskSnapshot>("/api/v1/tasks", request);
      setRequest(emptyRequest());
      setSelectedTask(task);
      await refresh();
    } catch (reason: unknown) {
      setError(String(reason));
    } finally {
      setWorking(false);
    }
  };

  const cancel = async (task: TaskSnapshot): Promise<void> => {
    setWorking(true);
    try {
      const updated = await postJson<TaskSnapshot>(`/api/v1/tasks/${task.contract.taskId}/cancel`, {});
      setSelectedTask(updated);
      await refresh();
    } catch (reason: unknown) {
      setError(String(reason));
    } finally {
      setWorking(false);
    }
  };

  const launch = async (task: TaskSnapshot): Promise<void> => {
    setWorking(true);
    setError(null);
    try {
      const updated = await postJson<TaskSnapshot>(`/api/v1/tasks/${task.contract.taskId}/launch`, {});
      setSelectedTask(updated);
      await refresh();
    } catch (reason: unknown) {
      setError(String(reason));
    } finally {
      setWorking(false);
    }
  };

  const integrate = async (task: TaskSnapshot): Promise<void> => {
    setWorking(true);
    setError(null);
    try {
      const updated = await postJson<TaskSnapshot>(`/api/v1/tasks/${task.contract.taskId}/integrate`, {
        confirmation: `INTEGRATE ${task.contract.taskId}`,
      });
      setSelectedTask(updated);
      await refresh();
    } catch (reason: unknown) {
      setError(String(reason));
    } finally {
      setWorking(false);
    }
  };

  const decide = async (approval: ApprovalRequest, decision: string): Promise<void> => {
    setWorking(true);
    try {
      const updated = await postJson<TaskSnapshot>(`/api/v1/approvals/${approval.approvalId}/decision`, { decision });
      setSelectedTask(updated);
      await refresh();
    } catch (reason: unknown) {
      setError(String(reason));
    } finally {
      setWorking(false);
    }
  };

  const reviewTasks = useMemo(() => tasks.filter((task) => task.state === "READY_FOR_REVIEW"), [tasks]);
  const displayedTasks = activeNavigation === "Review" ? reviewTasks : tasks;

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-mark" aria-hidden="true">B</div>
        <div>
          <strong>Blunder</strong>
          <span>{project?.name ?? "Starting core…"}</span>
        </div>
        <div className="topbar-status">
          <span className="status-pulse" />
          {working ? "Working" : "Ready"}
        </div>
      </header>

      <aside className="sidebar" aria-label="Primary navigation">
        <nav>
          {navigation.map((item) => (
            <button
              className={item === activeNavigation ? "active" : ""}
              key={item}
              type="button"
              onClick={() => setActiveNavigation(item)}
            >
              {item}
              {item === "Review" && reviewTasks.length > 0 ? <span className="badge">{reviewTasks.length}</span> : null}
            </button>
          ))}
        </nav>
        <section className="boundary-card">
          <span className="eyebrow">Authority</span>
          <strong>{project?.policy.provider.enabled ? "Bounded provider" : "Provider closed"}</strong>
          <small>Deploy and push closed · promotion threshold-gated</small>
        </section>
      </aside>

      <section className="workspace">
        <div className="workspace-heading">
          <div>
            <span className="eyebrow">{activeNavigation}</span>
            <h1>{activeNavigation === "Work" ? "What outcome do you want?" : activeNavigation}</h1>
          </div>
          <span className="revision">Source {shortDigest(project?.sourceRevision ?? "unversioned")}</span>
        </div>

        {activeNavigation === "Work" ? (
          <section className="composer">
            <textarea
              aria-label="Desired outcome"
              value={request.objective}
              onChange={(event) => setRequest((current) => ({ ...current, objective: event.target.value }))}
              placeholder="Describe the outcome. Blunder will choose the internal strategy."
            />
            <div className="composer-footer">
              <span>No modes · no model picker · evidence required</span>
              <button className="primary" type="button" disabled={working || request.objective.trim().length < 3} onClick={() => void submit()}>
                Start work
              </button>
            </div>
          </section>
        ) : null}

        {error !== null ? <div className="error-banner" role="alert">{error}</div> : null}

        {activeNavigation === "Library" ? (
          <section className="promotion-library" aria-label="Autonomous promotion ledger">
            <div className="promotion-summary">
              <div><span className="eyebrow">Promoted</span><strong>{promotions?.promotedCount ?? 0}</strong></div>
              <div><span className="eyebrow">Blocked</span><strong>{promotions?.blockedCount ?? 0}</strong></div>
              <div><span className="eyebrow">Policy</span><strong>Fixed thresholds</strong></div>
            </div>
            {promotions?.records.slice().reverse().map((record) => (
              <article className="promotion-record" key={record.recordDigest}>
                <div>
                  <span className="eyebrow">{record.decision.scope.replaceAll("-", " ")}</span>
                  <h3>{record.decision.status.replaceAll("_", " ")}</h3>
                  <code>{shortDigest(record.decision.candidateIdentity)} · {shortDigest(record.decision.decisionDigest)}</code>
                </div>
                <span className={record.decision.promoted ? "promotion-pass" : "promotion-blocked"}>
                  {record.decision.promoted ? "Activated" : `${record.decision.failedGates.length} gates blocked`}
                </span>
              </article>
            ))}
            {promotions?.records.length === 0 ? <p className="empty-state">No promotion decisions have been recorded.</p> : null}
          </section>
        ) : (
          <section className="task-list" aria-label="Tasks">
            {displayedTasks.map((task) => <TaskCard task={task} onSelect={setSelectedTask} key={task.contract.taskId} />)}
            {displayedTasks.length === 0 ? <p className="empty-state">No work is waiting here.</p> : null}
          </section>
        )}
      </section>

      <aside className="evidence-drawer" aria-label="Task evidence">
        {selectedTask === null ? (
          <div className="empty-evidence">Select a task to inspect its evidence.</div>
        ) : (
          <>
            <span className="eyebrow">{selectedTask.state.replaceAll("_", " ")}</span>
            <h2>{selectedTask.contract.objective}</h2>
            <p>{selectedTask.statusReason}</p>

            <dl className="evidence-grid">
              <div><dt>Stage</dt><dd>{selectedTask.stage}</dd></div>
              <div><dt>Event</dt><dd>#{selectedTask.eventSequence}</dd></div>
              <div><dt>Budget</dt><dd>${selectedTask.contract.budget.maximumCostUsd.toFixed(2)}</dd></div>
              <div><dt>Providers</dt><dd>{selectedTask.contract.budget.maximumProviderCalls}</dd></div>
            </dl>

            {selectedTask.blockers.length > 0 ? (
              <section className="evidence-section">
                <h3>Blockers</h3>
                <ul>{selectedTask.blockers.map((item) => <li key={item}>{item}</li>)}</ul>
              </section>
            ) : null}

            <section className="evidence-section">
              <h3>Specialists</h3>
              <div className="agent-list">
                {selectedTask.selectedAgents.map((agent) => <span key={agent}>{agent.replace("agent-", "")}</span>)}
              </div>
            </section>

            {selectedTask.approvals.filter((item) => item.status === "PENDING").map((approval) => (
              <ApprovalControl approval={approval} onDecision={decide} key={approval.approvalId} />
            ))}

            <section className="evidence-section">
              <h3>Identity</h3>
              <code>{shortDigest(selectedTask.contract.contractDigest)} · {shortDigest(selectedTask.eventDigest)}</code>
            </section>

            {selectedTask.artifacts.length > 0 ? (
              <section className="evidence-section">
                <h3>Artifacts</h3>
                <ul>{selectedTask.artifacts.map((artifact) => <li key={artifact.artifactId}>{artifact.kind} · {shortDigest(artifact.sha256)}</li>)}</ul>
              </section>
            ) : null}

            {selectedTask.missionId === null && selectedTask.contract.authority.isolatedSourceMutation && selectedTask.contract.authority.provider ? (
              <button className="primary" type="button" disabled={working} onClick={() => void launch(selectedTask)}>Launch isolated mission</button>
            ) : null}

            {selectedTask.state === "READY_FOR_REVIEW" && selectedTask.missionId !== null ? (
              <button className="primary" type="button" disabled={working} onClick={() => void integrate(selectedTask)}>Integrate verified patch</button>
            ) : null}

            {selectedTask.state !== "CANCELLED" ? (
              <button className="quiet" type="button" disabled={working} onClick={() => void cancel(selectedTask)}>Cancel task</button>
            ) : null}
          </>
        )}

        {events.length > 0 ? (
          <section className="event-strip">
            <span className="eyebrow">Live events</span>
            {events.slice(0, 4).map((event) => <small key={event.eventId}>#{event.sequence} {event.kind}</small>)}
          </section>
        ) : null}
      </aside>
    </main>
  );
};
