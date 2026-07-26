export type TaskState =
  | "UNDERSTANDING"
  | "EXPLORING"
  | "DESIGNING"
  | "BUILDING"
  | "VERIFYING"
  | "READY_FOR_REVIEW"
  | "BLOCKED"
  | "NEEDS_APPROVAL"
  | "ABSTAINED"
  | "CANCELLED";

export interface ProviderPolicy {
  enabled: boolean;
  allowedProviders: string[];
  maximumCostPerTaskUsd: number;
  maximumCallsPerTask: number;
}

export interface ProjectSummary {
  id: string;
  name: string;
  root: string;
  sourceRevision: string;
  active: boolean;
  policy: {
    provider: ProviderPolicy;
    previewAllowed: boolean;
    externalDeploymentAllowed: boolean;
  };
}

export interface ApprovalRequest {
  approvalId: string;
  taskId: string;
  kind: string;
  reason: string;
  exactApprovePhrase: string;
  exactDenyPhrase: string;
  status: string;
}

export interface TaskContract {
  taskId: string;
  projectId: string;
  objective: string;
  constraints: string[];
  successCriteria: string[];
  deliverables: string[];
  contractDigest: string;
  sourceRevision: string;
  authority: {
    provider: boolean;
    network: boolean;
    isolatedSourceMutation: boolean;
    integrate: boolean;
    commit: boolean;
    push: boolean;
    deploy: boolean;
  };
  budget: {
    maximumSeconds: number;
    maximumProviderCalls: number;
    maximumTokens: number;
    maximumCostUsd: number;
  };
}

export interface TaskSnapshot {
  schema: string;
  contract: TaskContract;
  stage: string;
  state: TaskState;
  statusReason: string;
  selectedAgents: string[];
  approvals: ApprovalRequest[];
  blockers: string[];
  missionId: string | null;
  artifacts: Array<{
    artifactId: string;
    kind: string;
    relativePath: string;
    sha256: string;
    bytes: number;
  }>;
  eventSequence: number;
  eventDigest: string;
  updatedAt: string;
}

export interface TaskEvent {
  eventId: string;
  taskId: string;
  sequence: number;
  kind: string;
  stage: string;
  state: TaskState;
  createdAt: string;
}

export interface ProjectList {
  projects: ProjectSummary[];
}

export interface TaskList {
  tasks: TaskSnapshot[];
}

export interface PromotionDecision {
  scope: string;
  candidateIdentity: string;
  status: string;
  promoted: boolean;
  failedGates: string[];
  decisionDigest: string;
}

export interface PromotionRecord {
  sequence: number;
  createdAt: string;
  decision: PromotionDecision;
  recordDigest: string;
}

export interface PromotionState {
  sequence: number;
  promotedCount: number;
  blockedCount: number;
  thresholdsAreConstitutional: boolean;
  manualPromotionPathAvailable: boolean;
  records: PromotionRecord[];
}

export interface CoreResponse<T> {
  status: number;
  body: T;
}

export interface CreateTaskRequest {
  objective: string;
  constraints: string[];
  successCriteria: string[];
  deliverables: string[];
  assumptions: string[];
  unknowns: string[];
}
