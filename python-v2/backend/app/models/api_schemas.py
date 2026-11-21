"""
Pydantic Request and Response Schemas for the FastAPI REST Backend.

Defines schemas for health checks, cluster overview, incidents, investigations,
remediation approvals, and post-remediation verification plans.
"""

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel, Field, field_validator

from app.models.investigation import (
    AgentToolCallTrace,
    GeminiRequestMetric,
    InvestigationResult,
    InvestigationStatus,
)
from app.models.remediation import (
    RemediationProposal,
    VerificationPlan,
)


class HealthResponse(BaseModel):
    """Application health and runtime status."""
    status: str = Field(default="healthy", description="Application operational status")
    service: str = Field(default="k8s-agentic-ops", description="Service identifier")
    version: str = Field(default="2.0.0", description="API version")
    cluster_connected: bool = Field(default=False, description="Whether Kubernetes cluster is reachable")
    model: str = Field(default="gemini-3.8-flash", description="Configured Gemini LLM model")
    mcp_server: str = Field(default="read-only-k8s-tools", description="MCP server mode")
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class PodContainerStatus(BaseModel):
    """Runtime status of an individual container in a Pod."""
    name: str = Field(..., description="Container name")
    state: Optional[str] = Field(default=None, description="Waiting, Running, or Terminated")
    reason: Optional[str] = Field(default=None, description="State reason (e.g. CrashLoopBackOff, OOMKilled)")
    message: Optional[str] = Field(default=None, description="Human-readable state details")
    restart_count: int = Field(default=0, ge=0, description="Total container restarts")
    ready: bool = Field(default=False, description="Whether container passed readiness probes")


class PodStatusSummary(BaseModel):
    """Sanitized, high-level summary of an individual Pod."""
    name: str = Field(..., description="Pod name")
    namespace: str = Field(..., description="Kubernetes namespace")
    phase: Optional[str] = Field(default="Unknown", description="Pod phase (Running, Pending, Failed, etc.)")
    is_ready: bool = Field(default=False, description="Whether all containers are ready")
    is_scheduled: bool = Field(default=False, description="Whether Pod is scheduled to a node")
    node_name: Optional[str] = Field(default=None, description="Node running the Pod")
    restarts: int = Field(default=0, ge=0, description="Total restarts across containers")
    container_statuses: List[PodContainerStatus] = Field(default_factory=list)
    owners: List[Dict[str, str]] = Field(default_factory=list, description="Controlling owner references")


class ClusterOverviewResponse(BaseModel):
    """High-level cluster and namespace runtime health overview."""
    namespace: str = Field(..., description="Observed Kubernetes namespace")
    total_pods: int = Field(default=0, ge=0)
    running_pods: int = Field(default=0, ge=0)
    pending_pods: int = Field(default=0, ge=0)
    failed_pods: int = Field(default=0, ge=0)
    succeeded_pods: int = Field(default=0, ge=0)
    unknown_pods: int = Field(default=0, ge=0)
    incident_count: int = Field(default=0, ge=0, description="Total active diagnosed incidents")
    incidents_summary: Dict[str, int] = Field(default_factory=dict, description="Incidents count by failure type")
    pods: List[PodStatusSummary] = Field(default_factory=list, description="Summary of surveyed Pods")
    collected_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class IncidentItem(BaseModel):
    """A deterministic incident diagnosis derived from cluster telemetry."""
    failure_type: str = Field(..., description="Categorized failure type (e.g. MEMORY_EXHAUSTION)")
    severity: str = Field(..., description="Severity level: CRITICAL, HIGH, MEDIUM, LOW, INFO")
    resource_name: str = Field(..., description="Target resource identifier")
    namespace: str = Field(..., description="Kubernetes namespace")
    summary: str = Field(..., description="Concise incident summary")
    root_cause: str = Field(..., description="Root cause explanation")
    evidence: List[str] = Field(default_factory=list, description="Concrete evidence items")
    recommended_action: str = Field(..., description="Recommended remediation action")
    pod: Optional[str] = Field(default=None, description="Target Pod name")
    container: Optional[str] = Field(default=None, description="Target container name if container-level")
    confidence: str = Field(default="HIGH", description="Diagnosis confidence level")


class IncidentsResponse(BaseModel):
    """List of detected cluster incidents."""
    namespace: str = Field(..., description="Target namespace")
    total_incidents: int = Field(default=0, ge=0)
    incidents: List[IncidentItem] = Field(default_factory=list)
    collected_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class InvestigationCreateRequest(BaseModel):
    """Request payload to initiate an agentic incident investigation."""
    namespace: str = Field(default="k8s-agent-demo", description="Target Kubernetes namespace")
    pod_name: Optional[str] = Field(default=None, description="Optional target Pod name to isolate")
    use_llm: bool = Field(default=True, description="Whether to invoke Gemini LLM stages")

    @field_validator("namespace")
    @classmethod
    def validate_namespace_name(cls, v: str) -> str:
        if not v or not isinstance(v, str):
            raise ValueError("Namespace must be a non-empty string")
        v = v.strip()
        if len(v) > 63:
            raise ValueError("Namespace must not exceed 63 characters")
        dns_pattern = r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$"
        if not re.match(dns_pattern, v):
            raise ValueError(
                f"Invalid namespace '{v}'. Must be a valid DNS-1123 label (lowercase alphanumeric and hyphens)"
            )
        return v


class InvestigationCreateResponse(BaseModel):
    """Acknowledgement of investigation dispatch."""
    investigation_id: str = Field(..., description="Unique investigation identifier")
    status: str = Field(default="QUEUED", description="Investigation status: QUEUED or RUNNING")
    namespace: str = Field(..., description="Target namespace")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    message: str = Field(default="Investigation queued. Poll /api/investigations/{investigation_id} for status.")


class InvestigationDetailResponse(BaseModel):
    """Comprehensive status and results for an investigation."""
    investigation_id: str
    status: str = Field(..., description="QUEUED, RUNNING, COMPLETED, or FAILED")
    namespace: str
    target_pod: Optional[str] = None
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    outcome: Optional[str] = Field(default=None, description="COMPLETED_WITH_GEMINI, COMPLETED_WITH_FALLBACK, or FAILED")
    observation_provenance: Optional[str] = None
    diagnosis_provenance: Optional[str] = None
    remediation_provenance: Optional[str] = None
    verification_provenance: Optional[str] = None
    gemini_metrics: List[GeminiRequestMetric] = Field(default_factory=list)
    tool_traces: List[AgentToolCallTrace] = Field(default_factory=list)
    diagnoses_count: int = 0
    proposals_count: int = 0
    result: Optional[InvestigationResult] = None
    error: Optional[Dict[str, Any]] = None


class RemediationApprovalRequest(BaseModel):
    """Operator decision to approve an actionable remediation proposal."""
    reviewer: str = Field(..., min_length=1, description="Operator identity or username")
    comments: Optional[str] = Field(default=None, description="Operator review notes or rationale")


class RemediationRejectionRequest(BaseModel):
    """Operator decision to reject a remediation proposal."""
    reviewer: str = Field(..., min_length=1, description="Operator identity or username")
    comments: Optional[str] = Field(default=None, description="Operator rejection notes or feedback")


class VerificationPlanItem(BaseModel):
    """Verification plan details for an individual proposal."""
    proposal_id: str
    target_workload: str
    target_kind: str
    target_name: str
    can_remediate: bool
    verification_plan: Optional[VerificationPlan] = None
    insufficient_evidence_reason: Optional[str] = None


class InvestigationVerificationResponse(BaseModel):
    """Verification plans associated with an investigation."""
    investigation_id: str
    total_proposals: int = 0
    actionable_count: int = 0
    withheld_count: int = 0
    plans: List[VerificationPlanItem] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    """Standardized API error response format."""
    detail: str = Field(..., description="Sanitized human-readable error description")
    code: str = Field(default="ERROR", description="Machine-readable error classification code")
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
