"""
GitOps Remediation API Endpoints for KafaKube8.

Exposes safe, human-in-the-loop GitOps remediation operations:
- GET  /api/gitops/targets: List trusted scenario targets and replacement images
- GET  /api/gitops/changes: List prepared GitOps changes and audit history
- GET  /api/gitops/changes/{change_id}: Retrieve details of a specific GitOps change
- POST /api/gitops/preview: Preview unified diff and safety checks for an approved proposal
- POST /api/gitops/prepare: Prepare a local Git feature branch for an approved proposal

SAFETY GUARANTEES:
1. Approval verification: Requires explicit operator approval (APPROVED) before branch preparation.
2. Zero cluster mutations: Never interacts with Kubernetes API or executes kubectl writes.
3. Zero remote push: Never executes `git push`, never modifies remote repository.
4. Fail-closed: Missing approvals, dirty working trees, or unexpected images are rejected.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, status

from app.models.gitops import (
    GitOpsChangeRecord,
    GitOpsChangeRequest,
    GitOpsPreviewResponse,
    GitOpsTargetConfig,
)
from app.models.remediation import RemediationProposal
from app.services.gitops_service import (
    GitOpsApprovalRequiredError,
    GitOpsDirtyWorkingTreeError,
    GitOpsError,
    GitOpsManifestSyntaxError,
    GitOpsValidationError,
    get_gitops_service,
)
from app.services.investigation_registry import get_investigation_registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/gitops", tags=["GitOps Remediation"])


@router.get("/targets", response_model=List[GitOpsTargetConfig])
async def list_trusted_targets() -> List[GitOpsTargetConfig]:
    """List all configured trusted GitOps targets with known-good replacement images."""
    service = get_gitops_service()
    return service.list_trusted_targets()


@router.get("/changes", response_model=List[GitOpsChangeRecord])
async def list_gitops_changes(
    proposal_id: Optional[str] = Query(default=None, description="Filter by RemediationProposal ID"),
    status: Optional[str] = Query(default=None, description="Filter by GitOpsStatus"),
) -> List[GitOpsChangeRecord]:
    """List all prepared GitOps changes with audit history and commit details."""
    service = get_gitops_service()
    return await service.list_change_records(proposal_id=proposal_id, status=status)


@router.get("/changes/{change_id}", response_model=GitOpsChangeRecord)
async def get_gitops_change(change_id: str) -> GitOpsChangeRecord:
    """Retrieve details, unified diff, and audit history for a specific prepared GitOps change."""
    service = get_gitops_service()
    record = await service.get_change_record(change_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"GitOps change record '{change_id}' not found.",
        )
    return record


@router.post("/preview", response_model=GitOpsPreviewResponse)
async def preview_gitops_change(request: GitOpsChangeRequest) -> GitOpsPreviewResponse:
    """
    Generate unified diff and validate safety guardrails for a proposal.

    Does NOT write to disk or create Git commits.
    """
    registry = get_investigation_registry()
    record, raw_prop = await registry.find_proposal(request.proposal_id)

    if not raw_prop:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Remediation proposal with ID '{request.proposal_id}' not found.",
        )

    proposal = RemediationProposal(**raw_prop)
    service = get_gitops_service()

    try:
        return service.preview_change(proposal, request)
    except GitOpsValidationError as val_err:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(val_err),
        )
    except GitOpsManifestSyntaxError as syn_err:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(syn_err),
        )
    except GitOpsError as git_err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(git_err),
        )


@router.post("/prepare", response_model=GitOpsChangeRecord, status_code=status.HTTP_201_CREATED)
async def prepare_gitops_change(request: GitOpsChangeRequest) -> GitOpsChangeRecord:
    """
    Prepare a local Git feature branch with the remediated declarative manifest.

    STRICT SAFETY GUARANTEES:
    - Requires prior human operator approval (APPROVED status).
    - Checks for dirty working tree to prevent clobbering uncommitted work.
    - Never pushes to remote; commit is purely local.
    - Never mutates live cluster resources.
    """
    registry = get_investigation_registry()
    record, raw_prop = await registry.find_proposal(request.proposal_id)

    if not raw_prop:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Remediation proposal with ID '{request.proposal_id}' not found.",
        )

    proposal = RemediationProposal(**raw_prop)
    service = get_gitops_service()

    try:
        change_record = service.prepare_git_change(proposal, request)
        if record:
            change_record.investigation_id = record.investigation_id
        return change_record
    except GitOpsApprovalRequiredError as app_err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(app_err),
        )
    except GitOpsDirtyWorkingTreeError as dirty_err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(dirty_err),
        )
    except GitOpsValidationError as val_err:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(val_err),
        )
    except GitOpsManifestSyntaxError as syn_err:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(syn_err),
        )
    except GitOpsError as git_err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(git_err),
        )


@router.post("/changes/{change_id}/pr", response_model=GitOpsChangeRecord)
async def create_github_pr(
    change_id: str,
    base_branch: Optional[str] = Query(default=None, description="Target base branch (defaults to GITHUB_BASE_BRANCH or main)"),
) -> GitOpsChangeRecord:
    """
    Push a prepared local GitOps branch to GitHub and open a Pull Request.
    Requires the change to be in BRANCH_PREPARED status (post-human approval).
    """
    service = get_gitops_service()
    try:
        return await service.create_github_pr(change_id=change_id, base_branch=base_branch)
    except GitOpsValidationError as val_err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(val_err),
        )
    except GitOpsError as git_err:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(git_err),
        )
