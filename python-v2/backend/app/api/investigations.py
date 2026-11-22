"""
Investigations API endpoints.

Orchestrates asynchronous execution of the end-to-end incident investigation
pipeline, status polling, and verification plan inspection.
"""

import asyncio
from datetime import datetime, timezone
import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from app.models.api_schemas import (
    InvestigationCreateRequest,
    InvestigationCreateResponse,
    InvestigationDetailResponse,
    InvestigationVerificationResponse,
    VerificationPlanItem,
)
from app.services.investigation_registry import (
    get_investigation_registry,
    run_investigation_task,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/investigations", tags=["Investigations"])


@router.get("", response_model=List[InvestigationDetailResponse])
async def list_investigations() -> List[InvestigationDetailResponse]:
    """
    List all registered investigations, ordered newest first.
    """
    registry = get_investigation_registry()
    records = await registry.list_investigations()
    # Sort by creation time descending
    sorted_records = sorted(records, key=lambda r: r.created_at, reverse=True)
    return [InvestigationDetailResponse(**r.to_dict()) for r in sorted_records]


@router.post("", response_model=InvestigationCreateResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_investigation(
    body: InvestigationCreateRequest,
    background_tasks: BackgroundTasks,
) -> InvestigationCreateResponse:
    """
    Initiate an end-to-end Agentic AI incident investigation in the background.
    Returns immediately with an investigation ID to allow clients to poll for progress.
    """
    registry = get_investigation_registry()

    # Register investigation record in QUEUED state
    record = await registry.create_investigation(
        namespace=body.namespace,
        target_pod=body.pod_name,
        use_llm=body.use_llm,
    )

    # Dispatch to background task runner
    allow_best_effort = os.environ.get("ALLOW_BEST_EFFORT_REMEDIATIONS", "").strip().lower() == "true"
    background_tasks.add_task(
        run_investigation_task,
        investigation_id=record.investigation_id,
        namespace=body.namespace,
        target_pod=body.pod_name,
        use_llm=body.use_llm,
        allow_best_effort=allow_best_effort,
    )

    return InvestigationCreateResponse(
        investigation_id=record.investigation_id,
        status="QUEUED",
        namespace=body.namespace,
        created_at=record.created_at,
        message=f"Investigation for namespace '{body.namespace}' queued. Poll /api/investigations/{record.investigation_id} for status.",
    )


@router.get("/{investigation_id}", response_model=InvestigationDetailResponse)
async def get_investigation_status(investigation_id: str) -> InvestigationDetailResponse:
    """
    Retrieve live progress, results, provenance, Gemini metrics, and tool traces
    for a specific investigation.
    """
    registry = get_investigation_registry()
    record = await registry.get_investigation(investigation_id)

    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Investigation with ID '{investigation_id}' not found.",
        )

    return InvestigationDetailResponse(**record.to_dict())


@router.get("/{investigation_id}/verification", response_model=InvestigationVerificationResponse)
async def get_investigation_verification(investigation_id: str) -> InvestigationVerificationResponse:
    """
    Retrieve automated post-remediation verification plans formulated during the investigation.
    Plans specify target Pod phases, readiness conditions, and negative assertions.
    """
    registry = get_investigation_registry()
    record = await registry.get_investigation(investigation_id)

    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Investigation with ID '{investigation_id}' not found.",
        )

    plans: List[VerificationPlanItem] = []
    actionable_count = 0
    withheld_count = 0

    if record.result and record.result.remediation_proposals:
        for p in record.result.remediation_proposals:
            can_rem = bool(p.get("can_remediate", False))
            if can_rem:
                actionable_count += 1
            else:
                withheld_count += 1

            plans.append(
                VerificationPlanItem(
                    proposal_id=p.get("proposal_id", ""),
                    target_workload=p.get("target_workload", ""),
                    target_kind=p.get("target_kind", "Pod"),
                    target_name=p.get("target_name", ""),
                    can_remediate=can_rem,
                    verification_plan=p.get("verification_plan"),
                    insufficient_evidence_reason=p.get("insufficient_evidence_reason"),
                )
            )

    return InvestigationVerificationResponse(
        investigation_id=investigation_id,
        total_proposals=len(plans),
        actionable_count=actionable_count,
        withheld_count=withheld_count,
        plans=plans,
    )
