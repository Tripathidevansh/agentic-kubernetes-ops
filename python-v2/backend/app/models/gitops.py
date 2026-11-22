"""
Pydantic Models and Schemas for GitOps Remediation Foundation.

Defines schemas for declarative GitOps changes, target manifest tracking,
unified diff previews, local branch preparation records, and audit events.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel, Field, field_validator


class GitOpsStatus(str, Enum):
    """Lifecycle status of a declarative GitOps remediation change."""
    PENDING_APPROVAL = "PENDING_APPROVAL"
    READY_FOR_PREPARATION = "READY_FOR_PREPARATION"
    BRANCH_PREPARED = "BRANCH_PREPARED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class GitOpsTargetConfig(BaseModel):
    """Explicitly configured trusted target specification for a controlled GitOps scenario."""
    target_kind: str = Field(default="Deployment", description="Kubernetes resource kind")
    target_name: str = Field(..., description="Kubernetes resource name")
    target_namespace: str = Field(default="k8s-agent-demo", description="Target namespace")
    container_name: str = Field(default="web", description="Target container name within pod template")
    current_image: str = Field(..., description="Current failing or intentional test image reference")
    trusted_replacement_image: str = Field(..., description="Explicitly trusted known-good replacement image")
    manifest_path: str = Field(..., description="Relative or absolute path to the declarative YAML manifest")
    description: Optional[str] = Field(default=None, description="Scenario description and context")


class GitOpsChangeRequest(BaseModel):
    """Payload to request preview or local branch preparation of an approved remediation."""
    proposal_id: str = Field(..., description="Unique ID of the approved RemediationProposal")
    manifest_path: Optional[str] = Field(default=None, description="Optional custom manifest path override")
    target_image: Optional[str] = Field(
        default=None,
        description="Explicit requested image; MUST match configured trusted replacement image",
    )
    operator: str = Field(default="operator", description="Operator identity authorizing the Git change")
    branch_name: Optional[str] = Field(default=None, description="Optional custom git feature branch name")

    @field_validator("proposal_id", "operator")
    @classmethod
    def validate_non_empty(cls, v: str, info) -> str:
        if not v or not v.strip():
            raise ValueError(f"Field '{info.field_name}' must not be empty")
        return v.strip()


class GitOpsPreviewResponse(BaseModel):
    """Preview of the proposed declarative manifest change, unified diff, and safety checks."""
    proposal_id: str = Field(..., description="Target RemediationProposal ID")
    target_resource: str = Field(..., description="Resource identifier (e.g. Deployment/gitops-controlled-app)")
    target_namespace: str = Field(..., description="Kubernetes target namespace")
    target_file: str = Field(..., description="Path to the manifest file")
    container_name: str = Field(..., description="Container being updated")
    current_image: str = Field(..., description="Image before modification")
    replacement_image: str = Field(..., description="Image after modification")
    diff: str = Field(..., description="Unified diff formatted for Git and review")
    is_approved: bool = Field(..., description="Whether the underlying proposal has human approval")
    can_prepare: bool = Field(..., description="Whether all safety validations passed and branch can be prepared")
    validation_messages: List[str] = Field(default_factory=list, description="Diagnostic and validation messages")
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class GitOpsAuditEvent(BaseModel):
    """Immutable audit trail event for GitOps actions."""
    event_type: str = Field(..., description="PREVIEW_GENERATED, BRANCH_PREPARED, VALIDATION_FAILED, etc.")
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    operator: str = Field(default="system")
    details: Dict[str, Any] = Field(default_factory=dict)


class GitOpsChangeRecord(BaseModel):
    """Persistent audit record of a prepared local GitOps remediation branch."""
    change_id: str = Field(default_factory=lambda: f"gitops-chg-{uuid.uuid4().hex[:8]}")
    proposal_id: str = Field(..., description="Underlying approved RemediationProposal ID")
    investigation_id: Optional[str] = Field(default=None, description="Associated investigation ID if known")
    target_resource: str = Field(..., description="Resource (e.g. Deployment/gitops-controlled-app)")
    target_namespace: str = Field(default="k8s-agent-demo")
    target_file: str = Field(..., description="Path to modified manifest")
    container_name: str = Field(...)
    current_image: str = Field(...)
    replacement_image: str = Field(...)
    diff: str = Field(..., description="Unified diff applied to the branch")
    branch_name: Optional[str] = Field(default=None, description="Created local Git feature branch")
    commit_hash: Optional[str] = Field(default=None, description="Local Git commit SHA")
    commit_message: Optional[str] = Field(default=None, description="Git commit message")
    status: GitOpsStatus = Field(default=GitOpsStatus.BRANCH_PREPARED)
    operator: str = Field(default="operator")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    prepared_at: Optional[str] = Field(default=None)
    audit_events: List[GitOpsAuditEvent] = Field(default_factory=list)
    error: Optional[str] = Field(default=None)
