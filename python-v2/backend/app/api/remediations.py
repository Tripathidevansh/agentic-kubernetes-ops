"""
Remediation Proposals and Human-in-the-Loop Review API endpoints.

Lists remediation proposals, supports filtering by investigation and status,
and records operator approval/rejection decisions with strict GitOps safety guardrails.
SAFETY GUARANTEE: Approval endpoints record human decisions ONLY. They NEVER mutate
the live Kubernetes cluster, execute kubectl commands, or push Git commits.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, status

from app.models.api_schemas import (
    RemediationApprovalRequest,
    RemediationRejectionRequest,
)
from app.models.remediation import RemediationProposal
from app.services.investigation_registry import get_investigation_registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/remediations", tags=["Remediations"])


@router.get("", response_model=List[RemediationProposal])
async def list_remediations(
    investigation_id: Optional[str] = Query(
        default=None,
        description="Optional filter by investigation ID",
    ),
    status_filter: Optional[str] = Query(
        default=None,
        alias="status",
        description="Optional filter by proposal status (ACTIONABLE, INSUFFICIENT_EVIDENCE, REJECTED_SAFETY_VIOLATION)",
    ),
    approval_status: Optional[str] = Query(
        default=None,
        description="Optional filter by human approval status (PENDING, APPROVED, REJECTED)",
    ),
) -> List[RemediationProposal]:
    """
    List remediation proposals across investigations with optional filtering.
    """
    registry = get_investigation_registry()

    if investigation_id:
        record = await registry.get_investigation(investigation_id)
        if not record:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Investigation with ID '{investigation_id}' not found.",
            )

    raw_props = await registry.list_remediations(
        investigation_id=investigation_id,
        status=status_filter,
        approval_status=approval_status,
    )

    return [RemediationProposal(**p) for p in raw_props]


@router.post("/{proposal_id}/approve", response_model=RemediationProposal)
async def approve_remediation_proposal(
    proposal_id: str,
    body: RemediationApprovalRequest,
) -> RemediationProposal:
    """
    Record an operator's approval for an actionable remediation proposal.
    SAFETY GUARANTEE: Does NOT apply changes to Kubernetes or trigger Git commits.
    """
    registry = get_investigation_registry()

    try:
        updated_proposal = await registry.approve_proposal(
            proposal_id=proposal_id,
            reviewer=body.reviewer.strip(),
            comments=body.comments.strip() if body.comments else None,
        )
        return RemediationProposal(**updated_proposal)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Remediation proposal with ID '{proposal_id}' not found.",
        )
    except ValueError as val_err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(val_err),
        )


@router.post("/{proposal_id}/reject", response_model=RemediationProposal)
async def reject_remediation_proposal(
    proposal_id: str,
    body: RemediationRejectionRequest,
) -> RemediationProposal:
    """
    Record an operator's rejection for a remediation proposal.
    SAFETY GUARANTEE: Strictly records operator decision.
    """
    registry = get_investigation_registry()

    try:
        updated_proposal = await registry.reject_proposal(
            proposal_id=proposal_id,
            reviewer=body.reviewer.strip(),
            comments=body.comments.strip() if body.comments else None,
        )
        return RemediationProposal(**updated_proposal)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Remediation proposal with ID '{proposal_id}' not found.",
        )
