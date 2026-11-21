"""
Investigation Data Models for Agentic Kubernetes Operations.

Defines schemas for investigation requests, lifecycle statuses, observation
telemetry reports, agent tool call traces, and complete investigation results.
"""

from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel, Field, field_validator


class InvestigationStatus(str, Enum):
    """Lifecycle stages of an automated Kubernetes incident investigation."""
    PENDING = "PENDING"
    OBSERVING = "OBSERVING"
    DIAGNOSING = "DIAGNOSING"
    PLANNING = "PLANNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class AgentToolCallTrace(BaseModel):
    """Execution trace of an MCP tool invocation by an ADK agent."""
    call_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agent_name: str = Field(..., description="Name or role of the agent making the call")
    tool_name: str = Field(..., description="Name of the MCP tool invoked")
    arguments: Dict[str, Any] = Field(default_factory=dict, description="Arguments passed to the tool")
    status: str = Field(default="SUCCESS", description="Call outcome: SUCCESS or ERROR")
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    duration_ms: Optional[float] = Field(default=None, description="Execution duration in milliseconds")
    result_summary: Optional[str] = Field(default=None, description="Compact summary of the tool output")
    error_message: Optional[Any] = Field(default=None, description="Error message if the call failed")


class ObservationReport(BaseModel):
    """Structured cluster telemetry collected during the Observation phase."""
    namespace: str = Field(..., description="Kubernetes namespace observed")
    pod_count: int = Field(default=0, ge=0, description="Total pods scanned")
    pods: List[Dict[str, Any]] = Field(default_factory=list, description="Pod runtime states and specs")
    events: List[Dict[str, Any]] = Field(default_factory=list, description="Kubernetes warning and error events")
    logs_summary: Dict[str, Any] = Field(default_factory=dict, description="Summary of retrieved container logs")
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    provenance: str = Field(
        default="DETERMINISTIC_FALLBACK",
        description="Observation execution mode: GEMINI_AGENT or DETERMINISTIC_FALLBACK",
    )
    llm_error: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Failure details if LLM invocation failed",
    )


class InvestigationRequest(BaseModel):
    """Client request initiating a Kubernetes incident investigation."""
    namespace: str = Field(default="k8s-agent-demo", description="Target Kubernetes namespace")
    pod_name: Optional[str] = Field(default=None, description="Optional target Pod name to isolate")
    investigation_id: Optional[str] = Field(default=None, description="Optional custom investigation ID")
    include_logs: bool = Field(default=True, description="Whether to collect container logs")
    include_events: bool = Field(default=True, description="Whether to collect warning events")

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


class InvestigationResult(BaseModel):
    """Comprehensive artifact produced upon completion of an investigation."""
    investigation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: InvestigationStatus = Field(default=InvestigationStatus.PENDING)
    namespace: str = Field(..., description="Target Kubernetes namespace")
    target_pod: Optional[str] = Field(default=None, description="Specific Pod investigated, if isolated")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = Field(default=None, description="Timestamp of completion or failure")
    observation: Optional[ObservationReport] = Field(default=None, description="Telemetry from Observation stage")
    diagnoses: List[Dict[str, Any]] = Field(default_factory=list, description="Deterministic and agent diagnoses")
    remediation_proposals: List[Dict[str, Any]] = Field(default_factory=list, description="Remediation proposals")
    tool_traces: List[AgentToolCallTrace] = Field(default_factory=list, description="Traces of all tool calls made")
    provenance: str = Field(
        default="DETERMINISTIC_FALLBACK",
        description="Investigation execution mode: GEMINI_AGENT or DETERMINISTIC_FALLBACK"
    )
    model_metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="LLM provider and model metadata if Gemini was executed"
    )
    error: Optional[Dict[str, Any]] = Field(default=None, description="Failure details if investigation failed")
