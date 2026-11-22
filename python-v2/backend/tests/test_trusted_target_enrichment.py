"""
Comprehensive test suite for Phase 7B.1: Deterministic Trusted-Target Enrichment.

Verifies:
1. Exact trusted target match generates an actionable proposal.
2. Wrong namespace rejected (remains INSUFFICIENT_EVIDENCE).
3. Wrong Deployment name rejected.
4. Wrong workload kind rejected.
5. Wrong container rejected.
6. Missing container rejected.
7. Wrong current image rejected.
8. Missing current image rejected.
9. Wrong diagnosis (e.g. CRASH_LOOP) rejected.
10. No trusted replacement image configured rejected.
11. Arbitrary image override by client rejected.
12. Existing 10 failure scenarios remain INSUFFICIENT_EVIDENCE.
13. Enriched proposal enters investigation registry and appears in GET /api/remediations.
14. Human approval is strictly mandatory before GitOps staging.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.gitops import GitOpsTargetConfig
from app.models.investigation import InvestigationResult, InvestigationStatus
from app.models.remediation import (
    ApprovalStatus,
    ProposalProvenance,
    ProposalStatus,
    RemediationProposal,
    RiskLevel,
    YamlPatch,
)
from app.orchestrator.enrichment import (
    _extract_container_info,
    enrich_proposals_with_trusted_targets,
)
from app.services.gitops_service import DEFAULT_TRUSTED_TARGETS, GitOpsService
from app.services.investigation_registry import get_investigation_registry


@pytest.fixture
def trusted_targets_fixture():
    return {
        ("Deployment", "gitops-controlled-app", "k8s-agent-demo"): GitOpsTargetConfig(
            target_kind="Deployment",
            target_name="gitops-controlled-app",
            target_namespace="k8s-agent-demo",
            container_name="web",
            current_image="nginx:invalid-tag-does-not-exist",
            trusted_replacement_image="nginx:1.27.0-alpine",
            manifest_path="python-v2/manifests/gitops/controlled-imagepull.yaml",
            description="Controlled ImagePullBackOff scenario with verified alpine replacement image",
        ),
    }


@pytest.fixture
def base_insufficient_proposal():
    return RemediationProposal(
        proposal_id="test-prop-001",
        target_workload="gitops-controlled-app-69c5957b9c-4xjdf",
        target_kind="Deployment",
        target_name="gitops-controlled-app",
        target_namespace="k8s-agent-demo",
        status=ProposalStatus.INSUFFICIENT_EVIDENCE,
        can_remediate=False,
        insufficient_evidence_reason="Ungrounded container image reference.",
        justification="Image reference could not be verified.",
        risk_level=RiskLevel.HIGH,
        provenance=ProposalProvenance.DETERMINISTIC_FALLBACK,
    )


@pytest.fixture
def matching_diagnosis():
    return {
        "failure_type": "IMAGE_PULL_FAILURE",
        "severity": "HIGH",
        "resource_name": "gitops-controlled-app-69c5957b9c-4xjdf/web",
        "namespace": "k8s-agent-demo",
        "summary": "Container 'web' cannot retrieve image 'nginx:invalid-tag-does-not-exist'.",
        "root_cause": "Image pull failed with ImagePullBackOff",
        "evidence": [
            "Container 'web' failed to retrieve image: nginx:invalid-tag-does-not-exist",
            "Container State: ImagePullBackOff",
        ],
        "recommended_action": "Verify image nginx:invalid-tag-does-not-exist exists",
        "pod": "gitops-controlled-app-69c5957b9c-4xjdf",
        "container": "web",
    }


@pytest.fixture
def matching_pod_evidence():
    return {
        "pod": "gitops-controlled-app-69c5957b9c-4xjdf",
        "namespace": "k8s-agent-demo",
        "owners": [
            {
                "kind": "ReplicaSet",
                "name": "gitops-controlled-app-69c5957b9c",
                "controller": True,
            }
        ],
        "container_statuses": [
            {
                "name": "web",
                "image": "nginx:invalid-tag-does-not-exist",
                "state": {"waiting": {"reason": "ImagePullBackOff"}},
            }
        ],
        "spec": {
            "containers": [
                {
                    "name": "web",
                    "image": "nginx:invalid-tag-does-not-exist",
                }
            ]
        },
    }


# ==============================================================================
# 1. Exact Trusted Target Match
# ==============================================================================

def test_exact_trusted_target_match_generates_actionable_proposal(
    trusted_targets_fixture,
    base_insufficient_proposal,
    matching_diagnosis,
    matching_pod_evidence,
):
    """Exact match across all 6 conditions generates deterministic ACTIONABLE proposal."""
    proposals = [base_insufficient_proposal]
    diagnoses = [matching_diagnosis]
    observed_pods = [matching_pod_evidence]

    enriched = enrich_proposals_with_trusted_targets(
        proposals=proposals,
        diagnoses=diagnoses,
        namespace="k8s-agent-demo",
        observed_pods=observed_pods,
        trusted_targets=trusted_targets_fixture,
    )

    assert len(enriched) == 1
    p = enriched[0]

    assert p.status == ProposalStatus.ACTIONABLE
    assert p.can_remediate is True
    assert p.insufficient_evidence_reason is None
    assert p.provenance == ProposalProvenance.DETERMINISTIC_ENGINE
    assert p.target_kind == "Deployment"
    assert p.target_name == "gitops-controlled-app"
    assert p.target_namespace == "k8s-agent-demo"

    # Patch details
    assert p.patch is not None
    assert p.patch.target_kind == "Deployment"
    assert p.patch.target_name == "gitops-controlled-app"
    assert "nginx:1.27.0-alpine" in p.patch.diff
    assert "nginx:invalid-tag-does-not-exist" in p.patch.diff

    # Justification explains deterministic ground-truth mapping
    assert "Deterministic ground-truth mapping" in p.justification
    assert "nginx:1.27.0-alpine" in p.justification

    # Human-in-the-loop requirement preserved
    assert p.requires_approval is True
    assert p.approval is not None
    assert p.approval.status == ApprovalStatus.PENDING

    # Verification plan
    assert p.verification_plan is not None
    assert p.verification_plan.target_phase == "Running"
    assert "ImagePullBackOff" in p.verification_plan.negative_assertions


# ==============================================================================
# 2. Condition Violations: Rejection Tests
# ==============================================================================

def test_wrong_namespace_rejected(
    trusted_targets_fixture,
    base_insufficient_proposal,
    matching_diagnosis,
    matching_pod_evidence,
):
    """Proposal with mismatching namespace is NOT enriched."""
    wrong_ns_diag = dict(matching_diagnosis, namespace="production")
    wrong_ns_pod = dict(matching_pod_evidence, namespace="production")

    enriched = enrich_proposals_with_trusted_targets(
        proposals=[base_insufficient_proposal],
        diagnoses=[wrong_ns_diag],
        namespace="production",  # Different from k8s-agent-demo
        observed_pods=[wrong_ns_pod],
        trusted_targets=trusted_targets_fixture,
    )

    assert len(enriched) == 1
    assert enriched[0].status == ProposalStatus.INSUFFICIENT_EVIDENCE
    assert enriched[0].can_remediate is False


def test_wrong_deployment_name_rejected(
    trusted_targets_fixture,
    matching_diagnosis,
    matching_pod_evidence,
):
    """Proposal with different Deployment name is NOT enriched."""
    other_proposal = RemediationProposal(
        proposal_id="test-prop-wrong-dep",
        target_workload="other-deployment-pod-1234",
        target_kind="Deployment",
        target_name="other-deployment",
        target_namespace="k8s-agent-demo",
        status=ProposalStatus.INSUFFICIENT_EVIDENCE,
        can_remediate=False,
        justification="Insufficient evidence.",
        risk_level=RiskLevel.HIGH,
    )

    enriched = enrich_proposals_with_trusted_targets(
        proposals=[other_proposal],
        diagnoses=[matching_diagnosis],
        namespace="k8s-agent-demo",
        observed_pods=[matching_pod_evidence],
        trusted_targets=trusted_targets_fixture,
    )

    assert len(enriched) == 1
    assert enriched[0].status == ProposalStatus.INSUFFICIENT_EVIDENCE
    assert enriched[0].can_remediate is False


def test_wrong_workload_kind_rejected(
    trusted_targets_fixture,
    matching_diagnosis,
    matching_pod_evidence,
):
    """Proposal with non-Deployment kind (e.g. StatefulSet) is NOT enriched."""
    stateful_proposal = RemediationProposal(
        proposal_id="test-prop-stateful",
        target_workload="gitops-controlled-app-0",
        target_kind="StatefulSet",
        target_name="gitops-controlled-app",
        target_namespace="k8s-agent-demo",
        status=ProposalStatus.INSUFFICIENT_EVIDENCE,
        can_remediate=False,
        justification="Insufficient evidence.",
        risk_level=RiskLevel.HIGH,
    )

    enriched = enrich_proposals_with_trusted_targets(
        proposals=[stateful_proposal],
        diagnoses=[matching_diagnosis],
        namespace="k8s-agent-demo",
        observed_pods=[matching_pod_evidence],
        trusted_targets=trusted_targets_fixture,
    )

    assert len(enriched) == 1
    assert enriched[0].status == ProposalStatus.INSUFFICIENT_EVIDENCE
    assert enriched[0].can_remediate is False


def test_wrong_container_rejected(
    trusted_targets_fixture,
    base_insufficient_proposal,
    matching_diagnosis,
):
    """Proposal whose container does not match trusted container 'web' is NOT enriched."""
    wrong_container_diag = dict(
        matching_diagnosis,
        container="sidecar",
        resource_name="gitops-controlled-app-69c5957b9c-4xjdf/sidecar",
    )
    wrong_container_pod = {
        "pod": "gitops-controlled-app-69c5957b9c-4xjdf",
        "container_statuses": [{"name": "sidecar", "image": "nginx:invalid-tag-does-not-exist"}],
    }

    enriched = enrich_proposals_with_trusted_targets(
        proposals=[base_insufficient_proposal],
        diagnoses=[wrong_container_diag],
        namespace="k8s-agent-demo",
        observed_pods=[wrong_container_pod],
        trusted_targets=trusted_targets_fixture,
    )

    assert len(enriched) == 1
    assert enriched[0].status == ProposalStatus.INSUFFICIENT_EVIDENCE
    assert enriched[0].can_remediate is False


def test_missing_container_rejected(
    trusted_targets_fixture,
    base_insufficient_proposal,
):
    """If container name cannot be identified, proposal is NOT enriched."""
    diag_no_container = {
        "failure_type": "IMAGE_PULL_FAILURE",
        "resource_name": "gitops-controlled-app-69c5957b9c-4xjdf",
        "namespace": "k8s-agent-demo",
        "evidence": ["Image pull failure without container name"],
        "summary": "Pull failure",
        "root_cause": "Unknown",
        "recommended_action": "Check images",
    }

    enriched = enrich_proposals_with_trusted_targets(
        proposals=[base_insufficient_proposal],
        diagnoses=[diag_no_container],
        namespace="k8s-agent-demo",
        observed_pods=[],
        trusted_targets=trusted_targets_fixture,
    )

    assert len(enriched) == 1
    assert enriched[0].status == ProposalStatus.INSUFFICIENT_EVIDENCE
    assert enriched[0].can_remediate is False


def test_wrong_current_image_rejected(
    trusted_targets_fixture,
    base_insufficient_proposal,
    matching_diagnosis,
):
    """If current image differs from trusted current image, proposal is NOT enriched."""
    diag_wrong_img = dict(
        matching_diagnosis,
        evidence=["Container 'web' failed to retrieve image: redis:wrong-tag"],
    )
    pod_wrong_img = {
        "pod": "gitops-controlled-app-69c5957b9c-4xjdf",
        "container_statuses": [{"name": "web", "image": "redis:wrong-tag"}],
    }

    enriched = enrich_proposals_with_trusted_targets(
        proposals=[base_insufficient_proposal],
        diagnoses=[diag_wrong_img],
        namespace="k8s-agent-demo",
        observed_pods=[pod_wrong_img],
        trusted_targets=trusted_targets_fixture,
    )

    assert len(enriched) == 1
    assert enriched[0].status == ProposalStatus.INSUFFICIENT_EVIDENCE
    assert enriched[0].can_remediate is False


def test_missing_current_image_rejected(
    trusted_targets_fixture,
    base_insufficient_proposal,
):
    """If current image cannot be parsed from diagnosis or pod evidence, proposal is NOT enriched."""
    diag_no_img = {
        "failure_type": "IMAGE_PULL_FAILURE",
        "resource_name": "gitops-controlled-app-69c5957b9c-4xjdf/web",
        "container": "web",
        "namespace": "k8s-agent-demo",
        "evidence": ["General network timeout"],
        "summary": "Pull failed",
        "root_cause": "Unknown",
        "recommended_action": "Retry",
    }

    enriched = enrich_proposals_with_trusted_targets(
        proposals=[base_insufficient_proposal],
        diagnoses=[diag_no_img],
        namespace="k8s-agent-demo",
        observed_pods=[],
        trusted_targets=trusted_targets_fixture,
    )

    assert len(enriched) == 1
    assert enriched[0].status == ProposalStatus.INSUFFICIENT_EVIDENCE
    assert enriched[0].can_remediate is False


def test_wrong_diagnosis_failure_type_rejected(
    trusted_targets_fixture,
    base_insufficient_proposal,
    matching_pod_evidence,
):
    """Proposal with failure_type other than IMAGE_PULL_FAILURE (e.g. CRASH_LOOP) is NOT enriched."""
    crash_loop_diag = {
        "failure_type": "CRASH_LOOP",
        "severity": "CRITICAL",
        "resource_name": "gitops-controlled-app-69c5957b9c-4xjdf/web",
        "namespace": "k8s-agent-demo",
        "container": "web",
        "evidence": ["Application exited with code 1"],
        "summary": "CrashLoopBackOff detected",
        "root_cause": "Command failure",
        "recommended_action": "Fix command",
    }

    enriched = enrich_proposals_with_trusted_targets(
        proposals=[base_insufficient_proposal],
        diagnoses=[crash_loop_diag],
        namespace="k8s-agent-demo",
        observed_pods=[matching_pod_evidence],
        trusted_targets=trusted_targets_fixture,
    )

    assert len(enriched) == 1
    assert enriched[0].status == ProposalStatus.INSUFFICIENT_EVIDENCE
    assert enriched[0].can_remediate is False


def test_no_trusted_replacement_image_configured_rejected(
    base_insufficient_proposal,
    matching_diagnosis,
    matching_pod_evidence,
):
    """If trusted target configuration lacks replacement image, proposal is NOT enriched."""
    unconfigured_target = {
        ("Deployment", "gitops-controlled-app", "k8s-agent-demo"): GitOpsTargetConfig(
            target_kind="Deployment",
            target_name="gitops-controlled-app",
            target_namespace="k8s-agent-demo",
            container_name="web",
            current_image="nginx:invalid-tag-does-not-exist",
            trusted_replacement_image="",  # Empty replacement image!
            manifest_path="python-v2/manifests/gitops/controlled-imagepull.yaml",
        ),
    }

    enriched = enrich_proposals_with_trusted_targets(
        proposals=[base_insufficient_proposal],
        diagnoses=[matching_diagnosis],
        namespace="k8s-agent-demo",
        observed_pods=[matching_pod_evidence],
        trusted_targets=unconfigured_target,
    )

    assert len(enriched) == 1
    assert enriched[0].status == ProposalStatus.INSUFFICIENT_EVIDENCE
    assert enriched[0].can_remediate is False


def test_arbitrary_image_override_by_client_rejected(
    trusted_targets_fixture,
    matching_diagnosis,
    matching_pod_evidence,
):
    """
    Client or proposal attempting to suggest an arbitrary unverified replacement image
    is ignored: the deterministic patch strictly adheres to the configured trusted replacement.
    """
    adversarial_proposal = RemediationProposal(
        proposal_id="test-prop-override",
        target_workload="gitops-controlled-app-69c5957b9c-4xjdf",
        target_kind="Deployment",
        target_name="gitops-controlled-app",
        target_namespace="k8s-agent-demo",
        status=ProposalStatus.INSUFFICIENT_EVIDENCE,
        can_remediate=False,
        justification="Client requested unverified image",
        patch=YamlPatch(
            target_kind="Deployment",
            target_name="gitops-controlled-app",
            diff="fake diff",
            patch_content={"image": "malicious/unverified:latest"},
        ),
    )

    enriched = enrich_proposals_with_trusted_targets(
        proposals=[adversarial_proposal],
        diagnoses=[matching_diagnosis],
        namespace="k8s-agent-demo",
        observed_pods=[matching_pod_evidence],
        trusted_targets=trusted_targets_fixture,
    )

    assert len(enriched) == 1
    p = enriched[0]
    assert p.status == ProposalStatus.ACTIONABLE
    # Must use configured trusted replacement, not adversarial/client image
    assert "nginx:1.27.0-alpine" in p.patch.diff
    assert "malicious/unverified" not in p.patch.diff
    assert p.patch.patch_content["spec"]["template"]["spec"]["containers"][0]["image"] == "nginx:1.27.0-alpine"


# ==============================================================================
# 3. Existing 10 Failure Scenarios Remain INSUFFICIENT_EVIDENCE
# ==============================================================================

def test_existing_10_failure_scenarios_remain_insufficient_evidence():
    """
    Verifies that all 10 legacy failure scenarios are excluded from enrichment
    and strictly remain INSUFFICIENT_EVIDENCE.
    """
    legacy_scenarios = [
        ("crash-loop-pod", "CRASH_LOOP", "CrashLoopBackOff"),
        ("missing-cm-pod", "CONFIG_MAP_ERROR", "CreateContainerConfigError"),
        ("missing-secret-pod", "SECRET_ERROR", "CreateContainerConfigError"),
        ("oom-pod", "OOM_KILLED", "OOMKilled"),
        ("readonly-fs-pod", "READONLY_FS", "ReadonlyFilesystemError"),
        ("invalid-cmd-pod", "CONTAINER_CANNOT_RUN", "RunContainerError"),
        ("unbound-pvc-pod", "UNBOUND_PVC", "FailedScheduling"),
        ("readiness-probe-pod", "PROBE_FAILURE", "Unhealthy"),
        ("netpol-blocked-pod", "NETWORK_POLICY_BLOCKED", "ConnectionRefused"),
        ("demo-app-658b498f4c-8vzn9", "IMAGE_PULL_FAILURE", "ImagePullBackOff"),
    ]

    proposals = []
    diagnoses = []
    pods = []

    for name, f_type, reason in legacy_scenarios:
        proposals.append(
            RemediationProposal(
                proposal_id=f"prop-{name}",
                target_workload=name,
                target_kind="Deployment" if "demo-app" in name else "Pod",
                target_name=name.split("-")[0],
                target_namespace="k8s-agent-demo",
                status=ProposalStatus.INSUFFICIENT_EVIDENCE,
                can_remediate=False,
                justification=f"Insufficient evidence for {name}",
                risk_level=RiskLevel.HIGH,
            )
        )
        diagnoses.append(
            {
                "failure_type": f_type,
                "resource_name": f"{name}/app",
                "container": "app",
                "namespace": "k8s-agent-demo",
                "evidence": [f"State reason: {reason}"],
                "summary": f"{name} failed with {f_type}",
                "root_cause": "Configuration error",
                "recommended_action": "Manual inspection required",
                "pod": name,
            }
        )
        pods.append({"pod": name, "namespace": "k8s-agent-demo"})

    enriched = enrich_proposals_with_trusted_targets(
        proposals=proposals,
        diagnoses=diagnoses,
        namespace="k8s-agent-demo",
        observed_pods=pods,
        trusted_targets=DEFAULT_TRUSTED_TARGETS,
    )

    assert len(enriched) == 10
    for p in enriched:
        assert p.status == ProposalStatus.INSUFFICIENT_EVIDENCE
        assert p.can_remediate is False
        assert p.provenance != ProposalProvenance.DETERMINISTIC_ENGINE


# ==============================================================================
# 4. Registry and API Integration
# ==============================================================================

@pytest.mark.anyio
async def test_enriched_proposal_enters_registry_and_api_list(
    trusted_targets_fixture,
    base_insufficient_proposal,
    matching_diagnosis,
    matching_pod_evidence,
):
    """Enriched proposal is recorded in InvestigationRegistry and served by GET /api/remediations."""
    enriched = enrich_proposals_with_trusted_targets(
        proposals=[base_insufficient_proposal],
        diagnoses=[matching_diagnosis],
        namespace="k8s-agent-demo",
        observed_pods=[matching_pod_evidence],
        trusted_targets=trusted_targets_fixture,
    )

    p = enriched[0]
    assert p.status == ProposalStatus.ACTIONABLE

    registry = get_investigation_registry()
    record = await registry.create_investigation(namespace="k8s-agent-demo")
    record.result = InvestigationResult(
        investigation_id=record.investigation_id,
        status=InvestigationStatus.COMPLETED,
        namespace="k8s-agent-demo",
        remediation_proposals=[p.model_dump()],
    )

    client = TestClient(app)
    response = client.get("/api/remediations?namespace=k8s-agent-demo")
    assert response.status_code == 200

    data = response.json()
    assert isinstance(data, list)
    found = [item for item in data if item["proposal_id"] == p.proposal_id]
    assert len(found) == 1
    assert found[0]["status"] == "ACTIONABLE"
    assert found[0]["can_remediate"] is True
    assert found[0]["provenance"] == "DETERMINISTIC_ENGINE"
    assert "nginx:1.27.0-alpine" in found[0]["patch"]["diff"]


# ==============================================================================
# 5. Human Approval Requirement
# ==============================================================================

@pytest.mark.anyio
async def test_human_approval_is_mandatory_and_lifecycle(
    trusted_targets_fixture,
    base_insufficient_proposal,
    matching_diagnosis,
    matching_pod_evidence,
):
    """
    Enriched proposal requires human approval before staging.
    POST /api/remediations/{id}/approve approves it.
    POST /api/remediations/{id}/reject rejects it.
    Direct execution without approval is blocked.
    """
    enriched = enrich_proposals_with_trusted_targets(
        proposals=[base_insufficient_proposal],
        diagnoses=[matching_diagnosis],
        namespace="k8s-agent-demo",
        observed_pods=[matching_pod_evidence],
        trusted_targets=trusted_targets_fixture,
    )
    p = enriched[0]

    # Verify requires_approval and status
    assert p.requires_approval is True
    assert p.approval.status == ApprovalStatus.PENDING

    # GitOpsService validation rejects unapproved proposal
    gitops = GitOpsService(trusted_targets=trusted_targets_fixture)
    errors = gitops.validate_proposal(p, require_approval=True)
    assert any("approval" in err.lower() for err in errors)

    # Register in InvestigationRegistry
    registry = get_investigation_registry()
    record = await registry.create_investigation(namespace="k8s-agent-demo")
    record.result = InvestigationResult(
        investigation_id=record.investigation_id,
        status=InvestigationStatus.COMPLETED,
        namespace="k8s-agent-demo",
        remediation_proposals=[p.model_dump()],
    )

    client = TestClient(app)

    # Approve proposal
    appr_resp = client.post(
        f"/api/remediations/{p.proposal_id}/approve",
        json={"reviewer": "sre-lead", "comments": "Approved for staging"},
    )
    assert appr_resp.status_code == 200
    appr_data = appr_resp.json()
    assert appr_data["approval"]["status"] == "APPROVED"
    assert appr_data["approval"]["reviewer"] == "sre-lead"

    # Now GitOpsService validation passes for approved proposal
    p_approved = RemediationProposal.model_validate(appr_data)
    assert gitops.validate_proposal(p_approved, require_approval=True) == []

    # Test rejection endpoint
    rej_proposal = RemediationProposal(
        proposal_id="test-prop-reject-me",
        target_workload="gitops-controlled-app",
        target_kind="Deployment",
        target_name="gitops-controlled-app",
        target_namespace="k8s-agent-demo",
        status=ProposalStatus.ACTIONABLE,
        can_remediate=True,
        justification="Actionable for rejection test",
    )
    record.result.remediation_proposals.append(rej_proposal.model_dump())

    rej_resp = client.post(
        f"/api/remediations/{rej_proposal.proposal_id}/reject",
        json={"reviewer": "sre-lead", "comments": "Not approved during maintenance window"},
    )
    assert rej_resp.status_code == 200
    rej_data = rej_resp.json()
    assert rej_data["approval"]["status"] == "REJECTED"
    assert rej_data["approval"]["reviewer"] == "sre-lead"
