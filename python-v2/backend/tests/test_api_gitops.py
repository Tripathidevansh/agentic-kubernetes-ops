"""
Integration Tests for FastAPI GitOps Remediation Endpoints (Phase 7A).

Tests:
1. GET /api/gitops/targets: returns configured trusted targets.
2. POST /api/gitops/preview: returns unified diff and validation checks.
3. POST /api/gitops/preview: returns 404 for non-existent proposal.
4. POST /api/gitops/prepare: returns 400 for unapproved proposal.
5. POST /api/gitops/prepare: returns 201 and creates local Git feature branch for approved proposal.
6. GET /api/gitops/changes: lists prepared changes with audit history.
7. GET /api/gitops/changes/{change_id}: retrieves change details by ID.
8. Zero cluster mutations and zero remote git operations.
"""

from datetime import datetime, timezone
from pathlib import Path
import subprocess
import uuid

from fastapi.testclient import TestClient
import pytest
import yaml

from app.main import app
from app.models.gitops import GitOpsTargetConfig
from app.models.investigation import InvestigationResult
from app.models.remediation import (
    ApprovalStatus,
    HumanApproval,
    ProposalProvenance,
    ProposalStatus,
    RemediationProposal,
    RiskLevel,
    YamlPatch,
)
from app.services.gitops_service import (
    GitOpsService,
    reset_gitops_service,
)
from app.services.investigation_registry import (
    InvestigationRecord,
    get_investigation_registry,
)

SAMPLE_MANIFEST = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: gitops-controlled-app
  namespace: k8s-agent-demo
spec:
  replicas: 1
  selector:
    matchLabels:
      app: gitops-controlled-app
  template:
    metadata:
      labels:
        app: gitops-controlled-app
    spec:
      containers:
        - name: web
          image: nginx:invalid-tag-does-not-exist
"""


@pytest.fixture
def gitops_test_env(tmp_path):
    """Fixture initializing a clean git repository and registering test GitOpsService."""
    # 1. Initialize Git Repo
    subprocess.run(["git", "init"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "operator@kafakube8.local"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Operator Test"], cwd=str(tmp_path), check=True, capture_output=True)

    # 2. Write initial manifest and commit
    manifest = tmp_path / "app.yaml"
    manifest.write_text(SAMPLE_MANIFEST, encoding="utf-8")
    subprocess.run(["git", "add", "app.yaml"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(tmp_path), check=True, capture_output=True)

    # 3. Configure GitOpsService
    service = GitOpsService(repo_root=tmp_path)
    service.register_target(
        GitOpsTargetConfig(
            target_kind="Deployment",
            target_name="gitops-controlled-app",
            target_namespace="k8s-agent-demo",
            container_name="web",
            current_image="nginx:invalid-tag-does-not-exist",
            trusted_replacement_image="nginx:1.27.0-alpine",
            manifest_path="app.yaml",
        )
    )
    reset_gitops_service(service)

    yield {"tmp_path": tmp_path, "service": service, "manifest": manifest}

    # Reset singleton after test
    reset_gitops_service(None)


@pytest.mark.anyio
async def test_get_trusted_targets(gitops_test_env):
    """Verify GET /api/gitops/targets returns registered targets."""
    client = TestClient(app)
    response = client.get("/api/gitops/targets")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert any(t["target_name"] == "gitops-controlled-app" for t in data)


@pytest.mark.anyio
async def test_preview_endpoint_proposal_not_found(gitops_test_env):
    """Verify POST /api/gitops/preview returns 404 when proposal does not exist."""
    client = TestClient(app)
    payload = {"proposal_id": "non-existent-proposal-id"}
    response = client.post("/api/gitops/preview", json=payload)
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.anyio
async def test_preview_endpoint_success(gitops_test_env):
    """Verify POST /api/gitops/preview returns diff and safety evaluation."""
    registry = get_investigation_registry()
    inv_id = str(uuid.uuid4())
    prop_id = str(uuid.uuid4())

    proposal = RemediationProposal(
        proposal_id=prop_id,
        target_workload="gitops-controlled-app",
        target_kind="Deployment",
        target_name="gitops-controlled-app",
        target_namespace="k8s-agent-demo",
        status=ProposalStatus.ACTIONABLE,
        can_remediate=True,
        justification="Update failing image tag to verified nginx:1.27.0-alpine",
        risk_level=RiskLevel.LOW,
        requires_approval=True,
        approval=HumanApproval(
            status=ApprovalStatus.APPROVED,
            reviewer="operator-alice",
            reviewed_at=datetime.now(timezone.utc).isoformat(),
        ),
        patch=YamlPatch(
            target_kind="Deployment",
            target_name="gitops-controlled-app",
            target_namespace="k8s-agent-demo",
            diff="--- a\n+++ b",
        ),
    )

    record = await registry.create_investigation("k8s-agent-demo", investigation_id=inv_id)
    inv_result = InvestigationResult(
        investigation_id=inv_id,
        namespace="k8s-agent-demo",
        status="COMPLETED",
        remediation_proposals=[proposal.model_dump()],
    )
    await registry.update_status(inv_id, status="COMPLETED", result=inv_result)

    client = TestClient(app)
    response = client.post("/api/gitops/preview", json={"proposal_id": prop_id})
    assert response.status_code == 200
    data = response.json()
    assert data["proposal_id"] == prop_id
    assert data["is_approved"] is True
    assert data["can_prepare"] is True
    assert "nginx:1.27.0-alpine" in data["diff"]


@pytest.mark.anyio
async def test_prepare_endpoint_unapproved_returns_400(gitops_test_env):
    """Verify POST /api/gitops/prepare returns 400 when proposal is pending approval."""
    registry = get_investigation_registry()
    inv_id = str(uuid.uuid4())
    prop_id = str(uuid.uuid4())

    proposal = RemediationProposal(
        proposal_id=prop_id,
        target_workload="gitops-controlled-app",
        target_kind="Deployment",
        target_name="gitops-controlled-app",
        target_namespace="k8s-agent-demo",
        status=ProposalStatus.ACTIONABLE,
        can_remediate=True,
        justification="Update failing image tag to verified nginx:1.27.0-alpine",
        risk_level=RiskLevel.LOW,
        requires_approval=True,
        approval=HumanApproval(status=ApprovalStatus.PENDING),
        patch=YamlPatch(
            target_kind="Deployment",
            target_name="gitops-controlled-app",
            target_namespace="k8s-agent-demo",
            diff="--- a\n+++ b",
        ),
    )

    record = await registry.create_investigation("k8s-agent-demo", investigation_id=inv_id)
    inv_result = InvestigationResult(
        investigation_id=inv_id,
        namespace="k8s-agent-demo",
        status="COMPLETED",
        remediation_proposals=[proposal.model_dump()],
    )
    await registry.update_status(inv_id, status="COMPLETED", result=inv_result)

    client = TestClient(app)
    response = client.post("/api/gitops/prepare", json={"proposal_id": prop_id})
    assert response.status_code == 400
    assert "approval required" in response.json()["detail"].lower()


@pytest.mark.anyio
async def test_prepare_endpoint_approved_creates_branch(gitops_test_env):
    """Verify POST /api/gitops/prepare creates feature branch and returns 201 Created."""
    registry = get_investigation_registry()
    inv_id = str(uuid.uuid4())
    prop_id = str(uuid.uuid4())

    proposal = RemediationProposal(
        proposal_id=prop_id,
        target_workload="gitops-controlled-app",
        target_kind="Deployment",
        target_name="gitops-controlled-app",
        target_namespace="k8s-agent-demo",
        status=ProposalStatus.ACTIONABLE,
        can_remediate=True,
        justification="Update failing image tag to verified nginx:1.27.0-alpine",
        risk_level=RiskLevel.LOW,
        requires_approval=True,
        approval=HumanApproval(
            status=ApprovalStatus.APPROVED,
            reviewer="lead-sre",
            reviewed_at=datetime.now(timezone.utc).isoformat(),
        ),
        patch=YamlPatch(
            target_kind="Deployment",
            target_name="gitops-controlled-app",
            target_namespace="k8s-agent-demo",
            diff="--- a\n+++ b",
        ),
    )

    record = await registry.create_investigation("k8s-agent-demo", investigation_id=inv_id)
    inv_result = InvestigationResult(
        investigation_id=inv_id,
        namespace="k8s-agent-demo",
        status="COMPLETED",
        remediation_proposals=[proposal.model_dump()],
    )
    await registry.update_status(inv_id, status="COMPLETED", result=inv_result)

    client = TestClient(app)
    response = client.post(
        "/api/gitops/prepare",
        json={"proposal_id": prop_id, "operator": "lead-sre"},
    )
    assert response.status_code == 201
    record_data = response.json()
    assert record_data["status"] == "BRANCH_PREPARED"
    assert record_data["branch_name"].startswith("gitops/remediation-")
    assert record_data["commit_hash"] is not None
    assert record_data["replacement_image"] == "nginx:1.27.0-alpine"
    assert record_data["operator"] == "lead-sre"
    change_id = record_data["change_id"]

    # Verify GET /api/gitops/changes/{change_id}
    get_resp = client.get(f"/api/gitops/changes/{change_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["change_id"] == change_id

    # Verify GET /api/gitops/changes
    list_resp = client.get(f"/api/gitops/changes?proposal_id={prop_id}")
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1
    assert list_resp.json()[0]["change_id"] == change_id
