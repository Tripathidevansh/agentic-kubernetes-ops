"""
In-Memory Thread-Safe Investigation Registry and Asynchronous Execution Manager.

Manages the lifecycle of incident investigations (QUEUED → RUNNING → COMPLETED/FAILED)
and human approval decisions for a local demonstration environment.

NOTE: This registry uses in-memory storage suitable for local evaluation.
All investigation records and approval decisions reset upon server restart.
"""

import asyncio
from datetime import datetime, timezone
import logging
import os
from typing import Any, Dict, List, Optional, Tuple
import uuid

from app.models.investigation import (
    InvestigationRequest,
    InvestigationResult,
    InvestigationStatus,
)
from app.models.remediation import ApprovalStatus, ProposalStatus
from app.orchestrator.pipeline import InvestigationPipeline
from app.orchestrator.sanitizer import sanitize_error_message

logger = logging.getLogger(__name__)


class InvestigationRecord:
    """Represents the mutable state of an ongoing or completed investigation."""

    def __init__(
        self,
        investigation_id: str,
        namespace: str,
        target_pod: Optional[str] = None,
        use_llm: bool = True,
    ):
        self.investigation_id = investigation_id
        self.namespace = namespace
        self.target_pod = target_pod
        self.use_llm = use_llm
        self.status = "QUEUED"  # QUEUED, RUNNING, COMPLETED, FAILED
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.started_at: Optional[str] = None
        self.completed_at: Optional[str] = None
        self.duration_seconds: Optional[float] = None
        self.result: Optional[InvestigationResult] = None
        self.error: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert record to dictionary format for API serialization."""
        diagnoses_count = len(self.result.diagnoses) if self.result else 0
        proposals_count = len(self.result.remediation_proposals) if self.result else 0

        outcome = self.result.outcome if self.result else (
            "FAILED" if self.status == "FAILED" else None
        )

        return {
            "investigation_id": self.investigation_id,
            "status": self.status,
            "namespace": self.namespace,
            "target_pod": self.target_pod,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
            "outcome": outcome,
            "observation_provenance": self.result.observation_provenance if self.result else None,
            "diagnosis_provenance": self.result.diagnosis_provenance if self.result else None,
            "remediation_provenance": self.result.remediation_provenance if self.result else None,
            "verification_provenance": self.result.verification_provenance if self.result else None,
            "gemini_metrics": self.result.gemini_metrics if self.result else [],
            "tool_traces": self.result.tool_traces if self.result else [],
            "diagnoses_count": diagnoses_count,
            "proposals_count": proposals_count,
            "result": self.result,
            "error": self.error,
        }


class InvestigationRegistry:
    """
    Thread-safe and coroutine-safe in-memory store for investigations and proposal approvals.
    Uses an asyncio.Lock to prevent concurrent state corruption.
    """

    def __init__(self):
        self._records: Dict[str, InvestigationRecord] = {}
        self._lock = asyncio.Lock()

    async def create_investigation(
        self,
        namespace: str,
        target_pod: Optional[str] = None,
        use_llm: bool = True,
        investigation_id: Optional[str] = None,
    ) -> InvestigationRecord:
        """Create and register a new investigation in QUEUED status."""
        async with self._lock:
            inv_id = investigation_id or str(uuid.uuid4())
            record = InvestigationRecord(
                investigation_id=inv_id,
                namespace=namespace,
                target_pod=target_pod,
                use_llm=use_llm,
            )
            self._records[inv_id] = record
            return record

    async def get_investigation(self, investigation_id: str) -> Optional[InvestigationRecord]:
        """Retrieve an investigation record by ID."""
        async with self._lock:
            return self._records.get(investigation_id)

    async def list_investigations(self) -> List[InvestigationRecord]:
        """List all registered investigations."""
        async with self._lock:
            return list(self._records.values())

    async def update_status(
        self,
        investigation_id: str,
        status: str,
        started_at: Optional[str] = None,
        completed_at: Optional[str] = None,
        duration_seconds: Optional[float] = None,
        result: Optional[InvestigationResult] = None,
        error: Optional[Dict[str, Any]] = None,
    ) -> Optional[InvestigationRecord]:
        """Update an investigation record's status and results."""
        async with self._lock:
            record = self._records.get(investigation_id)
            if not record:
                return None

            record.status = status
            if started_at:
                record.started_at = started_at
            if completed_at:
                record.completed_at = completed_at
            if duration_seconds is not None:
                record.duration_seconds = duration_seconds
            if result:
                record.result = result
            if error:
                record.error = error

            return record

    async def find_proposal(
        self, proposal_id: str
    ) -> Tuple[Optional[InvestigationRecord], Optional[Dict[str, Any]]]:
        """Find a proposal and its parent investigation by proposal ID."""
        async with self._lock:
            for record in self._records.values():
                if record.result and record.result.remediation_proposals:
                    for prop in record.result.remediation_proposals:
                        if prop.get("proposal_id") == proposal_id:
                            return record, prop
            return None, None

    async def approve_proposal(
        self, proposal_id: str, reviewer: str, comments: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Record a human operator approval on an actionable remediation proposal.
        SAFETY GUARANTEE: Does NOT apply changes to Kubernetes or push Git commits.
        """
        async with self._lock:
            for record in self._records.values():
                if record.result and record.result.remediation_proposals:
                    for prop in record.result.remediation_proposals:
                        if prop.get("proposal_id") == proposal_id:
                            if not prop.get("can_remediate") or prop.get("status") != ProposalStatus.ACTIONABLE.value:
                                raise ValueError(
                                    f"Cannot approve proposal '{proposal_id}' because it is non-actionable or has insufficient evidence."
                                )

                            prop["approval"] = {
                                "status": ApprovalStatus.APPROVED.value,
                                "reviewer": reviewer,
                                "reviewed_at": datetime.now(timezone.utc).isoformat(),
                                "comments": comments,
                            }
                            return prop

            raise KeyError(f"Remediation proposal '{proposal_id}' not found.")

    async def reject_proposal(
        self, proposal_id: str, reviewer: str, comments: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Record a human operator rejection on a remediation proposal.
        SAFETY GUARANTEE: Strictly records operator decision.
        """
        async with self._lock:
            for record in self._records.values():
                if record.result and record.result.remediation_proposals:
                    for prop in record.result.remediation_proposals:
                        if prop.get("proposal_id") == proposal_id:
                            prop["approval"] = {
                                "status": ApprovalStatus.REJECTED.value,
                                "reviewer": reviewer,
                                "reviewed_at": datetime.now(timezone.utc).isoformat(),
                                "comments": comments,
                            }
                            return prop

            raise KeyError(f"Remediation proposal '{proposal_id}' not found.")

    async def list_remediations(
        self,
        investigation_id: Optional[str] = None,
        status: Optional[str] = None,
        approval_status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List proposals across investigations with optional filtering."""
        proposals: List[Dict[str, Any]] = []
        async with self._lock:
            for record in self._records.values():
                if investigation_id and record.investigation_id != investigation_id:
                    continue
                if record.result and record.result.remediation_proposals:
                    for prop in record.result.remediation_proposals:
                        if status and prop.get("status") != status:
                            continue
                        if approval_status:
                            curr_app = prop.get("approval", {}).get("status")
                            if curr_app != approval_status:
                                continue
                        proposals.append(prop)
        return proposals

    async def clear(self) -> None:
        """Clear all records from registry (for testing)."""
        async with self._lock:
            self._records.clear()


# Global singleton instance
_registry = InvestigationRegistry()


def get_investigation_registry() -> InvestigationRegistry:
    """Access the global in-memory investigation registry."""
    return _registry


async def run_investigation_task(
    investigation_id: str,
    namespace: str,
    target_pod: Optional[str] = None,
    use_llm: bool = True,
    bridge: Optional[Any] = None,
    pipeline_factory: Optional[Any] = None,
    allow_best_effort: bool = False,
) -> None:
    """
    Background worker task executing the Agentic Investigation Pipeline asynchronously.
    Updates the registry through stages and guarantees safe error sanitization on failure.
    """
    registry = get_investigation_registry()
    started_at = datetime.now(timezone.utc).isoformat()
    t0 = asyncio.get_event_loop().time()

    await registry.update_status(
        investigation_id=investigation_id,
        status="RUNNING",
        started_at=started_at,
    )

    try:
        req = InvestigationRequest(
            namespace=namespace,
            pod_name=target_pod,
            investigation_id=investigation_id,
        )

        if pipeline_factory:
            pipeline = pipeline_factory()
        else:
            model_name = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
            pipeline = InvestigationPipeline(
                bridge=bridge,
                model_name=model_name,
                allow_best_effort=allow_best_effort,
            )

        result: InvestigationResult = await pipeline.execute(request=req, use_llm=use_llm)
        duration_s = asyncio.get_event_loop().time() - t0
        completed_at = datetime.now(timezone.utc).isoformat()

        if result.status == InvestigationStatus.COMPLETED:
            await registry.update_status(
                investigation_id=investigation_id,
                status="COMPLETED",
                completed_at=completed_at,
                duration_seconds=round(duration_s, 2),
                result=result,
            )
        else:
            await registry.update_status(
                investigation_id=investigation_id,
                status="FAILED",
                completed_at=completed_at,
                duration_seconds=round(duration_s, 2),
                result=result,
                error=result.error,
            )

    except Exception as exc:
        duration_s = asyncio.get_event_loop().time() - t0
        completed_at = datetime.now(timezone.utc).isoformat()
        logger.error("Background investigation '%s' failed unexpectedly: %s", investigation_id, exc)
        await registry.update_status(
            investigation_id=investigation_id,
            status="FAILED",
            completed_at=completed_at,
            duration_seconds=round(duration_s, 2),
            error={
                "type": type(exc).__name__,
                "message": sanitize_error_message(str(exc)),
                "failed_at_status": "BACKGROUND_WORKER",
            },
        )
