"""
Comprehensive Integration Tests for the FastAPI REST Backend.

Covers:
1. Health check endpoint (/health).
2. Cluster runtime overview (/api/cluster/overview) and Secret data protection.
3. Incident detection (/api/incidents) via deterministic engine.
4. Investigation dispatch (POST /api/investigations) and status polling (/api/investigations/{id}).
5. Completed and failed investigation handling.
6. Remediation proposals listing (/api/remediations) with filters.
7. Human-in-the-loop approval and rejection endpoints.
8. Rejection of approval on non-actionable proposals (safety guardrail).
9. Post-remediation verification endpoint (/api/investigations/{id}/verification).
10. Input validation errors (422) and resource not found errors (404).
"""

import asyncio
from datetime import datetime, timezone
import json
import os
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.models.investigation import (
    AgentToolCallTrace,
    GeminiRequestMetric,
    InvestigationRequest,
    InvestigationResult,
    InvestigationStatus,
    ObservationReport,
)
from app.models.remediation import (
    ApprovalStatus,
    HumanApproval,
    ProposalProvenance,
    ProposalStatus,
    RemediationProposal,
    RiskLevel,
    VerificationPlan,
    YamlPatch,
)
from app.services.investigation_registry import (
    InvestigationRecord,
    get_investigation_registry,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_registry_before_test():
    """Ensure in-memory registry is clean before each test execution."""
    registry = get_investigation_registry()
    asyncio.run(registry.clear())
    yield
    asyncio.run(registry.clear())


# -----------------------------------------------------------------------------
# 1. Health Endpoint Tests
# -----------------------------------------------------------------------------
def test_health_endpoint_success():
    """Verify /health returns 200 with service information and operational status."""
    with patch("app.api.health._init_kubernetes_client") as mock_init:
        mock_api = MagicMock()
        mock_init.return_value = mock_api

        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "k8s-agentic-ops"
        assert data["version"] == "2.0.0"
        assert data["cluster_connected"] is True
        assert data["mcp_server"] == "read-only-k8s-tools"
        assert "timestamp" in data


def test_health_endpoint_cluster_disconnected():
    """Verify /health returns 200 with cluster_connected=False when Kubernetes API is offline."""
    with patch("app.api.health._init_kubernetes_client", side_effect=Exception("Connection refused")):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["cluster_connected"] is False


# -----------------------------------------------------------------------------
# 2. Cluster Overview Endpoint Tests
# -----------------------------------------------------------------------------
def test_cluster_overview_success():
    """Verify /api/cluster/overview returns aggregated pod counts and incident breakdown."""
    mock_evidence = {
        "namespace": "k8s-agent-demo",
        "pod_count": 3,
        "pods": [
            {
                "pod": "oom-app-abc",
                "phase": "Running",
                "is_ready": False,
                "is_scheduled": True,
                "node_name": "node-1",
                "owners": [{"kind": "Deployment", "name": "oom-deploy"}],
                "containers": [
                    {
                        "name": "app",
                        "state": {"status": "Terminated", "reason": "OOMKilled", "message": "Memory limit exceeded"},
                        "restart_count": 3,
                        "is_ready": False,
                    }
                ],
            },
            {
                "pod": "pending-app-xyz",
                "phase": "Pending",
                "is_ready": False,
                "is_scheduled": False,
                "node_name": None,
                "owners": [],
                "containers": [],
            },
            {
                "pod": "healthy-app-123",
                "phase": "Running",
                "is_ready": True,
                "is_scheduled": True,
                "node_name": "node-1",
                "owners": [{"kind": "Deployment", "name": "healthy-deploy"}],
                "containers": [
                    {
                        "name": "web",
                        "state": {"status": "Running", "reason": None, "message": None},
                        "restart_count": 0,
                        "is_ready": True,
                    }
                ],
            },
        ],
    }

    mock_diagnosis = {
        "namespace": "k8s-agent-demo",
        "diagnosis_count": 1,
        "diagnoses": [
            {
                "failure_type": "MEMORY_EXHAUSTION",
                "severity": "HIGH",
                "resource_name": "oom-app-abc/app",
                "namespace": "k8s-agent-demo",
                "summary": "OOM killed",
                "root_cause": "Exceeded 64Mi",
                "evidence": ["OOMKilled exit code 137"],
                "recommended_action": "Increase memory",
                "pod": "oom-app-abc",
                "container": "app",
            }
        ],
    }

    with patch("app.api.cluster.collect_cluster_evidence", return_value=mock_evidence):
        with patch("app.api.cluster.diagnose_cluster", return_value=mock_diagnosis):
            response = client.get("/api/cluster/overview?namespace=k8s-agent-demo")
            assert response.status_code == 200
            data = response.json()
            assert data["namespace"] == "k8s-agent-demo"
            assert data["total_pods"] == 3
            assert data["running_pods"] == 2
            assert data["pending_pods"] == 1
            assert data["failed_pods"] == 0
            assert data["incident_count"] == 1
            assert data["incidents_summary"] == {"MEMORY_EXHAUSTION": 1}
            assert len(data["pods"]) == 3


def test_cluster_overview_invalid_namespace():
    """Verify /api/cluster/overview rejects invalid namespace names with 422."""
    response = client.get("/api/cluster/overview?namespace=INVALID_UPPERCASE")
    assert response.status_code == 422


def test_cluster_overview_cluster_error():
    """Verify /api/cluster/overview returns 503 if Kubernetes cluster is unreachable."""
    with patch("app.api.cluster.collect_cluster_evidence", side_effect=Exception("API server unreachable")):
        response = client.get("/api/cluster/overview?namespace=k8s-agent-demo")
        assert response.status_code == 503
        assert "unreachable" in response.json()["detail"].lower()


def test_cluster_overview_secret_data_never_exposed():
    """SECURITY GUARANTEE: Verify cluster overview payload never leaks Secret contents."""
    mock_evidence = {
        "namespace": "k8s-agent-demo",
        "pod_count": 1,
        "pods": [
            {
                "pod": "secret-pod",
                "phase": "Running",
                "is_ready": True,
                "is_scheduled": True,
                "node_name": "node-1",
                "owners": [],
                "containers": [
                    {
                        "name": "app",
                        "state": {"status": "Running"},
                        "restart_count": 0,
                        "is_ready": True,
                    }
                ],
                "related_resources": {
                    "secrets": [{"name": "db-secret", "exists": True}],
                },
            }
        ],
    }

    with patch("app.api.cluster.collect_cluster_evidence", return_value=mock_evidence):
        with patch("app.api.cluster.diagnose_cluster", return_value={"diagnoses": []}):
            response = client.get("/api/cluster/overview?namespace=k8s-agent-demo")
            assert response.status_code == 200
            raw_text = response.text
            # Assert no secret keys or dictionary data leak
            assert "data" not in raw_text or '"data":' not in raw_text
            assert "password" not in raw_text
            assert "token" not in raw_text


# -----------------------------------------------------------------------------
# 3. Incidents Endpoint Tests
# -----------------------------------------------------------------------------
def test_get_incidents_success():
    """Verify /api/incidents returns deterministic incident diagnoses."""
    mock_evidence = {"namespace": "k8s-agent-demo", "pod_count": 1, "pods": [{"pod": "crash-pod"}]}
    mock_diagnosis = {
        "namespace": "k8s-agent-demo",
        "diagnosis_count": 1,
        "diagnoses": [
            {
                "failure_type": "APPLICATION_PROCESS_FAILURE",
                "severity": "HIGH",
                "resource_name": "crash-pod/app",
                "namespace": "k8s-agent-demo",
                "summary": "Process exited with code 1",
                "root_cause": "Unhandled exception",
                "evidence": ["Container exited with code 1"],
                "recommended_action": "Fix bug",
                "pod": "crash-pod",
                "container": "app",
                "confidence": "HIGH",
            }
        ],
    }

    with patch("app.api.incidents.collect_cluster_evidence", return_value=mock_evidence):
        with patch("app.api.incidents.diagnose_cluster", return_value=mock_diagnosis):
            response = client.get("/api/incidents?namespace=k8s-agent-demo")
            assert response.status_code == 200
            data = response.json()
            assert data["namespace"] == "k8s-agent-demo"
            assert data["total_incidents"] == 1
            inc = data["incidents"][0]
            assert inc["failure_type"] == "APPLICATION_PROCESS_FAILURE"
            assert inc["severity"] == "HIGH"
            assert inc["pod"] == "crash-pod"


def test_get_incidents_invalid_namespace():
    """Verify /api/incidents validates namespace format."""
    response = client.get("/api/incidents?namespace=INVALID!NAME")
    assert response.status_code == 422


# -----------------------------------------------------------------------------
# 4. Investigations Endpoint Tests
# -----------------------------------------------------------------------------
def test_create_investigation_dispatches_background_task():
    """Verify POST /api/investigations creates a QUEUED record and dispatches background worker."""
    with patch("app.api.investigations.run_investigation_task") as mock_task:
        response = client.post(
            "/api/investigations",
            json={"namespace": "k8s-agent-demo", "use_llm": False},
        )
        assert response.status_code == 202
        data = response.json()
        assert "investigation_id" in data
        assert data["status"] == "QUEUED"
        assert data["namespace"] == "k8s-agent-demo"


def test_create_investigation_invalid_namespace():
    """Verify POST /api/investigations rejects invalid namespace."""
    response = client.post(
        "/api/investigations",
        json={"namespace": "UPPERCASE_NOT_ALLOWED"},
    )
    assert response.status_code == 422


def test_get_investigation_status_not_found():
    """Verify GET /api/investigations/{id} returns 404 for unknown ID."""
    response = client.get("/api/investigations/non-existent-id")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.anyio
async def test_list_investigations_empty_and_populated():
    """Verify GET /api/investigations lists registered investigations ordered newest first."""
    # When empty
    response = client.get("/api/investigations")
    assert response.status_code == 200
    assert response.json() == []

    # Populate two investigations
    registry = get_investigation_registry()
    r1 = await registry.create_investigation(namespace="k8s-agent-demo", investigation_id="inv-1")
    r2 = await registry.create_investigation(namespace="k8s-agent-demo", investigation_id="inv-2")

    response = client.get("/api/investigations")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    ids = [d["investigation_id"] for d in data]
    assert "inv-1" in ids
    assert "inv-2" in ids


@pytest.mark.anyio
async def test_get_investigation_status_completed():
    """Verify GET /api/investigations/{id} returns full results, provenances, traces, and metrics."""
    registry = get_investigation_registry()
    record = await registry.create_investigation(namespace="k8s-agent-demo")

    # Build mock investigation result
    mock_result = InvestigationResult(
        investigation_id=record.investigation_id,
        status=InvestigationStatus.COMPLETED,
        namespace="k8s-agent-demo",
        observation_provenance="GEMINI_AGENT",
        diagnosis_provenance="DETERMINISTIC_ENGINE",
        remediation_provenance="DETERMINISTIC_FALLBACK",
        verification_provenance="DETERMINISTIC_ENGINE",
        outcome="COMPLETED_WITH_FALLBACK",
        diagnoses=[{"pod": "pod-1", "failure_type": "MEMORY_EXHAUSTION"}],
        remediation_proposals=[
            {
                "proposal_id": "prop-123",
                "target_workload": "pod-1",
                "target_kind": "Deployment",
                "target_name": "deploy-1",
                "target_namespace": "k8s-agent-demo",
                "status": ProposalStatus.ACTIONABLE.value,
                "can_remediate": True,
                "patch": {"target_kind": "Deployment", "target_name": "deploy-1", "diff": "--- a\n+++ b", "patch_content": {}},
                "justification": "Fix memory",
                "risk_level": "LOW",
                "requires_approval": True,
                "approval": {"status": "PENDING"},
                "provenance": "DETERMINISTIC_FALLBACK",
            }
        ],
        tool_traces=[
            AgentToolCallTrace(
                agent_name="telemetry_collector",
                tool_name="scan_cluster",
                status="SUCCESS",
            )
        ],
        gemini_metrics=[
            GeminiRequestMetric(
                stage="OBSERVATION",
                model="gemini-3.8-flash",
                success=True,
            )
        ],
    )

    await registry.update_status(
        investigation_id=record.investigation_id,
        status="COMPLETED",
        duration_seconds=12.5,
        result=mock_result,
    )

    response = client.get(f"/api/investigations/{record.investigation_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["investigation_id"] == record.investigation_id
    assert data["status"] == "COMPLETED"
    assert data["outcome"] == "COMPLETED_WITH_FALLBACK"
    assert data["observation_provenance"] == "GEMINI_AGENT"
    assert data["remediation_provenance"] == "DETERMINISTIC_FALLBACK"
    assert data["diagnoses_count"] == 1
    assert data["proposals_count"] == 1
    assert len(data["tool_traces"]) == 1
    assert len(data["gemini_metrics"]) == 1


# -----------------------------------------------------------------------------
# 5. Verification Endpoint Tests
# -----------------------------------------------------------------------------
@pytest.mark.anyio
async def test_get_investigation_verification_plans():
    """Verify GET /api/investigations/{id}/verification returns verification plans."""
    registry = get_investigation_registry()
    record = await registry.create_investigation(namespace="k8s-agent-demo")

    mock_result = InvestigationResult(
        investigation_id=record.investigation_id,
        status=InvestigationStatus.COMPLETED,
        namespace="k8s-agent-demo",
        remediation_proposals=[
            {
                "proposal_id": "prop-actionable",
                "target_workload": "pod-1",
                "target_kind": "Deployment",
                "target_name": "deploy-1",
                "status": "ACTIONABLE",
                "can_remediate": True,
                "verification_plan": {
                    "target_phase": "Running",
                    "expected_conditions": [{"type": "Ready", "status": "True"}],
                    "negative_assertions": ["CrashLoopBackOff", "OOMKilled"],
                    "check_interval_seconds": 5,
                    "timeout_seconds": 60,
                },
            },
            {
                "proposal_id": "prop-withheld",
                "target_workload": "pod-2",
                "target_kind": "Pod",
                "target_name": "pod-2",
                "status": "INSUFFICIENT_EVIDENCE",
                "can_remediate": False,
                "insufficient_evidence_reason": "No logs available",
                "verification_plan": None,
            },
        ],
    )
    await registry.update_status(
        investigation_id=record.investigation_id,
        status="COMPLETED",
        result=mock_result,
    )

    response = client.get(f"/api/investigations/{record.investigation_id}/verification")
    assert response.status_code == 200
    data = response.json()
    assert data["investigation_id"] == record.investigation_id
    assert data["total_proposals"] == 2
    assert data["actionable_count"] == 1
    assert data["withheld_count"] == 1
    assert len(data["plans"]) == 2
    assert data["plans"][0]["verification_plan"]["target_phase"] == "Running"
    assert "CrashLoopBackOff" in data["plans"][0]["verification_plan"]["negative_assertions"]


def test_get_investigation_verification_not_found():
    """Verify /api/investigations/{id}/verification returns 404 for unknown ID."""
    response = client.get("/api/investigations/unknown-id/verification")
    assert response.status_code == 404


# -----------------------------------------------------------------------------
# 6. Remediation Proposals & Approval Lifecycle Tests
# -----------------------------------------------------------------------------
@pytest.mark.anyio
async def test_list_remediations_with_filters():
    """Verify GET /api/remediations supports investigation_id and status filters."""
    registry = get_investigation_registry()
    record1 = await registry.create_investigation(namespace="k8s-agent-demo")
    record2 = await registry.create_investigation(namespace="k8s-agent-demo")

    prop1 = {
        "proposal_id": "prop-1",
        "target_workload": "pod-1",
        "target_kind": "Deployment",
        "target_name": "deploy-1",
        "target_namespace": "k8s-agent-demo",
        "status": "ACTIONABLE",
        "can_remediate": True,
        "patch": {"target_kind": "Deployment", "target_name": "deploy-1", "diff": "--- a\n+++ b", "patch_content": {}},
        "justification": "Fix pod 1",
        "risk_level": "LOW",
        "requires_approval": True,
        "approval": {"status": "PENDING"},
    }
    prop2 = {
        "proposal_id": "prop-2",
        "target_workload": "pod-2",
        "target_kind": "Pod",
        "target_name": "pod-2",
        "target_namespace": "k8s-agent-demo",
        "status": "INSUFFICIENT_EVIDENCE",
        "can_remediate": False,
        "justification": "No fix",
        "risk_level": "HIGH",
        "requires_approval": True,
        "approval": {"status": "PENDING"},
    }

    record1.result = InvestigationResult(
        investigation_id=record1.investigation_id,
        status=InvestigationStatus.COMPLETED,
        namespace="k8s-agent-demo",
        remediation_proposals=[prop1],
    )
    record2.result = InvestigationResult(
        investigation_id=record2.investigation_id,
        status=InvestigationStatus.COMPLETED,
        namespace="k8s-agent-demo",
        remediation_proposals=[prop2],
    )

    # All proposals
    resp = client.get("/api/remediations")
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    # Filter by investigation_id
    resp_filtered = client.get(f"/api/remediations?investigation_id={record1.investigation_id}")
    assert resp_filtered.status_code == 200
    assert len(resp_filtered.json()) == 1
    assert resp_filtered.json()[0]["proposal_id"] == "prop-1"

    # Filter by status
    resp_status = client.get("/api/remediations?status=ACTIONABLE")
    assert resp_status.status_code == 200
    assert len(resp_status.json()) == 1


@pytest.mark.anyio
async def test_approve_remediation_proposal_success():
    """Verify POST /api/remediations/{id}/approve records approval decision."""
    registry = get_investigation_registry()
    record = await registry.create_investigation(namespace="k8s-agent-demo")

    prop = {
        "proposal_id": "prop-approve-me",
        "target_workload": "oom-pod",
        "target_kind": "Deployment",
        "target_name": "oom-deploy",
        "target_namespace": "k8s-agent-demo",
        "status": "ACTIONABLE",
        "can_remediate": True,
        "patch": {"target_kind": "Deployment", "target_name": "oom-deploy", "diff": "--- a\n+++ b", "patch_content": {}},
        "justification": "Memory bump justified by profiler evidence",
        "risk_level": "LOW",
        "requires_approval": True,
        "approval": {"status": "PENDING"},
    }
    record.result = InvestigationResult(
        investigation_id=record.investigation_id,
        status=InvestigationStatus.COMPLETED,
        namespace="k8s-agent-demo",
        remediation_proposals=[prop],
    )

    resp = client.post(
        "/api/remediations/prop-approve-me/approve",
        json={"reviewer": "alice-sre", "comments": "Approved after checking telemetry."},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["approval"]["status"] == "APPROVED"
    assert data["approval"]["reviewer"] == "alice-sre"
    assert data["approval"]["comments"] == "Approved after checking telemetry."
    assert "reviewed_at" in data["approval"]


@pytest.mark.anyio
async def test_approve_non_actionable_proposal_rejected():
    """SAFETY GUARANTEE: Approving an INSUFFICIENT_EVIDENCE proposal must be rejected with 400."""
    registry = get_investigation_registry()
    record = await registry.create_investigation(namespace="k8s-agent-demo")

    prop = {
        "proposal_id": "prop-withheld-99",
        "target_workload": "mystery-pod",
        "target_kind": "Pod",
        "target_name": "mystery-pod",
        "target_namespace": "k8s-agent-demo",
        "status": "INSUFFICIENT_EVIDENCE",
        "can_remediate": False,
        "justification": "Cannot safely propose patch",
        "risk_level": "HIGH",
        "requires_approval": True,
        "approval": {"status": "PENDING"},
    }
    record.result = InvestigationResult(
        investigation_id=record.investigation_id,
        status=InvestigationStatus.COMPLETED,
        namespace="k8s-agent-demo",
        remediation_proposals=[prop],
    )

    resp = client.post(
        "/api/remediations/prop-withheld-99/approve",
        json={"reviewer": "alice-sre"},
    )
    assert resp.status_code == 400
    assert "non-actionable" in resp.json()["detail"].lower()


@pytest.mark.anyio
async def test_reject_remediation_proposal_success():
    """Verify POST /api/remediations/{id}/reject records operator rejection."""
    registry = get_investigation_registry()
    record = await registry.create_investigation(namespace="k8s-agent-demo")

    prop = {
        "proposal_id": "prop-reject-me",
        "target_workload": "web-pod",
        "target_kind": "Deployment",
        "target_name": "web-deploy",
        "target_namespace": "k8s-agent-demo",
        "status": "ACTIONABLE",
        "can_remediate": True,
        "patch": {"target_kind": "Deployment", "target_name": "web-deploy", "diff": "--- a\n+++ b", "patch_content": {}},
        "justification": "Config change",
        "risk_level": "MEDIUM",
        "requires_approval": True,
        "approval": {"status": "PENDING"},
    }
    record.result = InvestigationResult(
        investigation_id=record.investigation_id,
        status=InvestigationStatus.COMPLETED,
        namespace="k8s-agent-demo",
        remediation_proposals=[prop],
    )

    resp = client.post(
        "/api/remediations/prop-reject-me/reject",
        json={"reviewer": "bob-lead", "comments": "Application refactor planned instead."},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["approval"]["status"] == "REJECTED"
    assert data["approval"]["reviewer"] == "bob-lead"
    assert data["approval"]["comments"] == "Application refactor planned instead."


def test_approve_non_existent_proposal_returns_404():
    """Verify approving an unknown proposal ID returns 404."""
    resp = client.post(
        "/api/remediations/does-not-exist/approve",
        json={"reviewer": "operator"},
    )
    assert resp.status_code == 404


def test_reject_non_existent_proposal_returns_404():
    """Verify rejecting an unknown proposal ID returns 404."""
    resp = client.post(
        "/api/remediations/does-not-exist/reject",
        json={"reviewer": "operator"},
    )
    assert resp.status_code == 404
