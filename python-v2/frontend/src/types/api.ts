export interface HealthResponse {
  status: string;
  version: string;
  timestamp: string;
  kubernetes_connected: boolean;
  gemini_model: string;
}

export interface PodOverviewItem {
  name: string;
  namespace: string;
  phase: string;
  restarts: number;
  ready: boolean;
  status_reason: string | null;
  message: string | null;
  created_at: string | null;
  node_name: string | null;
}

export interface ClusterOverviewResponse {
  cluster_name: string;
  namespace: string;
  total_pods: number;
  running_pods: number;
  pending_pods: number;
  failed_pods: number;
  crash_loop_pods: number;
  total_restarts: number;
  incident_count: number;
  namespaces: string[];
  incidents_summary: Record<string, number>;
  pods: PodOverviewItem[];
  collected_at: string;
}

export interface IncidentItem {
  id?: string;
  pod_name?: string;
  pod?: string;
  namespace: string;
  scenario_id?: string;
  failure_type: string;
  category?: string;
  severity: "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | string;
  root_cause: string;
  suggested_action?: string;
  recommended_action?: string;
  confidence: string;
  evidence?: string[];
  evidence_summary?: string;
  restarts?: number;
  first_seen?: string;
  last_seen?: string;
}

export interface IncidentsResponse {
  namespace: string;
  total_incidents: number;
  incidents: IncidentItem[];
  collected_at: string;
}

export interface GeminiRequestMetric {
  stage: string;
  request_index: number;
  prompt_tokens?: number | null;
  candidates_tokens?: number | null;
  total_tokens?: number | null;
  latency_seconds: number;
  status: string;
  http_status_code?: number | null;
  retries_attempted?: number | null;
  error_message?: string | null;
}

export interface AgentToolCallTrace {
  stage: string;
  tool_name: string;
  arguments: Record<string, any>;
  duration_seconds: number;
  status: string;
  error_message?: string | null;
  timestamp: string;
}

export interface YamlPatch {
  target_kind: string;
  target_name: string;
  target_namespace?: string;
  diff: string;
  patch_content: Record<string, any>;
}

export interface VerificationPlan {
  target_pod_phase?: string;
  expected_ready_condition?: boolean;
  readiness_probe_initial_delay_seconds?: number;
  timeout_seconds?: number;
  negative_assertions?: string[];
}

export interface HumanApproval {
  status: "PENDING_APPROVAL" | "APPROVED" | "REJECTED";
  reviewer?: string | null;
  reviewed_at?: string | null;
  comments?: string | null;
}

export interface RemediationProposal {
  proposal_id: string;
  diagnosis_id?: string;
  target_workload: string;
  target_kind: string;
  target_name: string;
  status: "ACTIONABLE" | "INSUFFICIENT_EVIDENCE" | "FAILED";
  provenance: string;
  can_remediate: boolean;
  insufficient_evidence_reason?: string | null;
  summary: string;
  root_cause_explanation?: string;
  risk_level: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
  patch?: YamlPatch | null;
  verification_plan?: VerificationPlan | null;
  approval?: HumanApproval;
}

export interface Diagnosis {
  diagnosis_id: string;
  scenario_id: string;
  failure_type: string;
  severity: string;
  target_workload: string;
  confidence: string;
  root_cause: string;
  recommended_action: string;
  evidence: string[];
}

export interface PipelineResult {
  investigation_id: string;
  status: "COMPLETED" | "FAILED";
  outcome?: "COMPLETED_WITH_GEMINI" | "COMPLETED_WITH_FALLBACK" | "FAILED";
  namespace: string;
  observation_provenance?: string;
  diagnosis_provenance?: string;
  remediation_provenance?: string;
  verification_provenance?: string;
  diagnoses: Diagnosis[];
  remediation_proposals: RemediationProposal[];
  gemini_metrics: GeminiRequestMetric[];
  tool_traces: AgentToolCallTrace[];
  summary?: string;
  started_at?: string;
  completed_at?: string;
  duration_seconds?: number;
}

export interface InvestigationDetailResponse {
  investigation_id: string;
  status: "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED";
  namespace: string;
  target_pod?: string | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  duration_seconds?: number | null;
  outcome?: string | null;
  observation_provenance?: string | null;
  diagnosis_provenance?: string | null;
  remediation_provenance?: string | null;
  verification_provenance?: string | null;
  gemini_metrics?: GeminiRequestMetric[];
  tool_traces?: AgentToolCallTrace[];
  diagnoses_count: number;
  proposals_count: number;
  result?: PipelineResult | null;
  error?: Record<string, any> | null;
}

export interface InvestigationCreateResponse {
  investigation_id: string;
  status: string;
  namespace: string;
  created_at: string;
  message: string;
}

export interface VerificationPlanItem {
  proposal_id: string;
  target_workload: string;
  target_kind: string;
  target_name: string;
  can_remediate: boolean;
  verification_plan?: VerificationPlan | null;
  insufficient_evidence_reason?: string | null;
}

export interface InvestigationVerificationResponse {
  investigation_id: string;
  total_proposals: number;
  actionable_count: number;
  withheld_count: number;
  plans: VerificationPlanItem[];
}

export interface RemediationApprovalResponse {
  proposal_id: string;
  approval_status: string;
  approved_by: string;
  approved_at: string;
  message: string;
}

export interface RemediationRejectionResponse {
  proposal_id: string;
  approval_status: string;
  rejected_by: string;
  rejected_at: string;
  reason: string;
  message: string;
}
