"""
Fail-Safe Remediation Schema and Parser Regression Tests for KafaKube8.

Validates that:
1. Missing or null AI-generated fields (can_remediate, status, risk_level) fail-closed.
2. Contradictory actionability combinations are strictly demoted to INSUFFICIENT_EVIDENCE.
3. Actionable proposals require explicit True, explicit ACTIONABLE, valid diff, valid patch payload,
   valid target, grounded justification, and pending human approval.
4. Duplicate/conflicting or missing batch item IDs fall back safely without corrupting other batch items.
"""

from unittest.mock import patch, MagicMock
import pytest
from pydantic import ValidationError

from app.models.remediation import (
    ApprovalStatus,
    BatchProposalItem,
    HumanApproval,
    ProposalProvenance,
    ProposalStatus,
    RemediationProposal,
    RiskLevel,
    VerificationPlan,
    YamlPatch,
)
from app.orchestrator.agents.remediation_agent import RemediationPlanner
from app.validators import validate_remediation_proposal, SafetyViolationError


# ============================================================================
# 1. BatchProposalItem Fail-Closed Schema Normalization Tests
# ============================================================================

def test_batch_proposal_item_null_can_remediate_defaults_to_false():
    """Verify missing/null can_remediate defaults to False (fail-closed)."""
    item = BatchProposalItem(item_id="diag_1", can_remediate=None)
    assert item.can_remediate is False
    assert item.status == "INSUFFICIENT_EVIDENCE"


def test_batch_proposal_item_omitted_can_remediate_defaults_to_false():
    """Verify omitted can_remediate defaults to False."""
    item = BatchProposalItem(item_id="diag_1")
    assert item.can_remediate is False
    assert item.status == "INSUFFICIENT_EVIDENCE"


def test_batch_proposal_item_null_status_defaults_to_insufficient_evidence():
    """Verify missing/null status defaults to INSUFFICIENT_EVIDENCE (fail-closed)."""
    item = BatchProposalItem(item_id="diag_1", status=None, can_remediate=True)
    assert item.status == "INSUFFICIENT_EVIDENCE"
    assert item.can_remediate is False


def test_batch_proposal_item_omitted_status_defaults_to_insufficient_evidence():
    """Verify omitted status defaults to INSUFFICIENT_EVIDENCE."""
    item = BatchProposalItem(item_id="diag_1", can_remediate=True)
    assert item.status == "INSUFFICIENT_EVIDENCE"
    assert item.can_remediate is False


def test_batch_proposal_item_unknown_status_defaults_to_insufficient_evidence():
    """Verify unknown or malformed status defaults to INSUFFICIENT_EVIDENCE."""
    for unknown_status in ["MAYBE", "PARTIAL", "UNKNOWN", "REMEDIATE", ""]:
        item = BatchProposalItem(item_id="diag_1", status=unknown_status, can_remediate=True)
        assert item.status == "INSUFFICIENT_EVIDENCE"
        assert item.can_remediate is False


def test_batch_proposal_item_null_risk_level_defaults_to_high():
    """Verify missing or null risk_level fails closed to HIGH."""
    item = BatchProposalItem(item_id="diag_1", risk_level=None)
    assert item.risk_level == "HIGH"


def test_batch_proposal_item_unknown_risk_level_defaults_to_high():
    """Verify unknown risk_level fails closed to HIGH."""
    item = BatchProposalItem(item_id="diag_1", risk_level="EXTREME")
    assert item.risk_level == "HIGH"


def test_batch_proposal_item_contradiction_actionable_with_cannot_remediate():
    """Verify status=ACTIONABLE with can_remediate=False is demoted to INSUFFICIENT_EVIDENCE."""
    item = BatchProposalItem(item_id="diag_1", status="ACTIONABLE", can_remediate=False)
    assert item.status == "INSUFFICIENT_EVIDENCE"
    assert item.can_remediate is False


def test_batch_proposal_item_contradiction_can_remediate_with_insufficient_evidence():
    """Verify can_remediate=True with status=INSUFFICIENT_EVIDENCE is demoted to can_remediate=False."""
    item = BatchProposalItem(
        item_id="diag_1",
        status="INSUFFICIENT_EVIDENCE",
        can_remediate=True,
        diff="--- a\n+++ b",
        patch_content={"spec": {}},
    )
    assert item.status == "INSUFFICIENT_EVIDENCE"
    assert item.can_remediate is False


def test_batch_proposal_item_actionable_missing_diff_demoted():
    """Verify can_remediate=True with null or empty diff is demoted to INSUFFICIENT_EVIDENCE."""
    for empty_diff in [None, "", "   "]:
        item = BatchProposalItem(
            item_id="diag_1",
            status="ACTIONABLE",
            can_remediate=True,
            diff=empty_diff,
            patch_content={"spec": {"replicas": 2}},
        )
        assert item.status == "INSUFFICIENT_EVIDENCE"
        assert item.can_remediate is False


def test_batch_proposal_item_actionable_missing_patch_content_demoted():
    """Verify can_remediate=True with null or empty patch_content is demoted to INSUFFICIENT_EVIDENCE."""
    for empty_patch in [None, {}, []]:
        item = BatchProposalItem(
            item_id="diag_1",
            status="ACTIONABLE",
            can_remediate=True,
            diff="--- spec.replicas: 1\n+++ spec.replicas: 2",
            patch_content=empty_patch,
        )
        assert item.status == "INSUFFICIENT_EVIDENCE"
        assert item.can_remediate is False


def test_batch_proposal_item_valid_actionable_preserved():
    """Verify a complete, valid actionable proposal remains actionable."""
    item = BatchProposalItem(
        item_id="diag_1",
        resource_name="web-pod",
        status="ACTIONABLE",
        can_remediate=True,
        justification="Grounded limit adjustment based on telemetry.",
        risk_level="MEDIUM",
        diff="--- limits.memory: 128Mi\n+++ limits.memory: 512Mi",
        patch_content={"spec": {"template": {"spec": {"containers": [{"name": "app", "resources": {"limits": {"memory": "512Mi"}}}]}}}},
    )
    assert item.status == "ACTIONABLE"
    assert item.can_remediate is True
    assert item.risk_level == "MEDIUM"


# ============================================================================
# 2. Pipeline Parser (_parse_single_proposal) Fail-Closed Boundary Tests
# ============================================================================

@pytest.fixture
def base_context():
    planner = RemediationPlanner(model_name="gemini-3.8-flash")
    diag = {
        "resource_name": "oom-pod",
        "failure_type": "OOM_KILLED",
        "root_cause": "Container exceeded 128Mi memory limit",
        "recommended_action": "Increase memory limit to 512Mi based on profile",
        "evidence": ["LastState: Terminated (Reason: OOMKilled, ExitCode: 137)", "Workload memory profile: peak=340Mi, suggested_limit=512Mi"],
    }
    target_info = {"target_kind": "Deployment", "target_name": "oom-app"}
    return planner, diag, target_info


def test_parser_demotes_null_can_remediate(base_context):
    planner, diag, target_info = base_context
    item_data = {
        "status": "ACTIONABLE",
        "can_remediate": None,
        "diff": "valid diff",
        "patch_content": {"spec": {}},
    }
    proposal = planner._parse_single_proposal(item_data, diag, "k8s-agent-demo", "oom-pod", target_info, None)
    assert proposal.can_remediate is False
    assert proposal.status == ProposalStatus.INSUFFICIENT_EVIDENCE
    assert proposal.patch is None
    assert "can_remediate omitted or null" in proposal.insufficient_evidence_reason


def test_parser_demotes_omitted_can_remediate(base_context):
    planner, diag, target_info = base_context
    item_data = {
        "status": "ACTIONABLE",
        "diff": "valid diff",
        "patch_content": {"spec": {}},
    }
    proposal = planner._parse_single_proposal(item_data, diag, "k8s-agent-demo", "oom-pod", target_info, None)
    assert proposal.can_remediate is False
    assert proposal.status == ProposalStatus.INSUFFICIENT_EVIDENCE
    assert proposal.patch is None


def test_parser_demotes_null_status(base_context):
    planner, diag, target_info = base_context
    item_data = {
        "status": None,
        "can_remediate": True,
        "diff": "valid diff",
        "patch_content": {"spec": {}},
    }
    proposal = planner._parse_single_proposal(item_data, diag, "k8s-agent-demo", "oom-pod", target_info, None)
    assert proposal.can_remediate is False
    assert proposal.status == ProposalStatus.INSUFFICIENT_EVIDENCE
    assert proposal.patch is None


def test_parser_demotes_omitted_status(base_context):
    planner, diag, target_info = base_context
    item_data = {
        "can_remediate": True,
        "diff": "valid diff",
        "patch_content": {"spec": {}},
    }
    proposal = planner._parse_single_proposal(item_data, diag, "k8s-agent-demo", "oom-pod", target_info, None)
    assert proposal.can_remediate is False
    assert proposal.status == ProposalStatus.INSUFFICIENT_EVIDENCE
    assert proposal.patch is None


def test_parser_demotes_unknown_status(base_context):
    planner, diag, target_info = base_context
    item_data = {
        "status": "POSSIBLE",
        "can_remediate": True,
        "diff": "valid diff",
        "patch_content": {"spec": {}},
    }
    proposal = planner._parse_single_proposal(item_data, diag, "k8s-agent-demo", "oom-pod", target_info, None)
    assert proposal.can_remediate is False
    assert proposal.status == ProposalStatus.INSUFFICIENT_EVIDENCE
    assert proposal.patch is None


def test_parser_demotes_actionable_missing_diff(base_context):
    planner, diag, target_info = base_context
    for empty_diff in [None, "", "   "]:
        item_data = {
            "status": "ACTIONABLE",
            "can_remediate": True,
            "diff": empty_diff,
            "patch_content": {"spec": {"replicas": 2}},
        }
        proposal = planner._parse_single_proposal(item_data, diag, "k8s-agent-demo", "oom-pod", target_info, None)
        assert proposal.can_remediate is False
        assert proposal.status == ProposalStatus.INSUFFICIENT_EVIDENCE
        assert proposal.patch is None
        assert "diff is empty or missing" in proposal.insufficient_evidence_reason


def test_parser_demotes_actionable_missing_patch_content(base_context):
    planner, diag, target_info = base_context
    for empty_patch in [None, {}, "string-not-dict"]:
        item_data = {
            "status": "ACTIONABLE",
            "can_remediate": True,
            "diff": "--- limits: 100Mi\n+++ limits: 500Mi",
            "patch_content": empty_patch,
        }
        proposal = planner._parse_single_proposal(item_data, diag, "k8s-agent-demo", "oom-pod", target_info, None)
        assert proposal.can_remediate is False
        assert proposal.status == ProposalStatus.INSUFFICIENT_EVIDENCE
        assert proposal.patch is None
        assert "patch payload is empty or missing" in proposal.insufficient_evidence_reason


def test_parser_demotes_actionable_missing_target_resource(base_context):
    planner, diag, _ = base_context
    invalid_target_info = {"target_kind": "", "target_name": ""}
    item_data = {
        "status": "ACTIONABLE",
        "can_remediate": True,
        "diff": "--- limits: 100Mi\n+++ limits: 500Mi",
        "patch_content": {"spec": {"replicas": 2}},
    }
    proposal = planner._parse_single_proposal(item_data, diag, "k8s-agent-demo", "oom-pod", invalid_target_info, None)
    assert proposal.can_remediate is False
    assert proposal.status == ProposalStatus.INSUFFICIENT_EVIDENCE
    assert proposal.patch is None
    assert "target resource is invalid or missing" in proposal.insufficient_evidence_reason


def test_parser_valid_actionable_creates_complete_proposal(base_context):
    planner, diag, target_info = base_context
    item_data = {
        "status": "ACTIONABLE",
        "can_remediate": True,
        "justification": "Increase memory limit from 128Mi to 512Mi based on 340Mi profile.",
        "risk_level": "LOW",
        "diff": "--- limits.memory: 128Mi\n+++ limits.memory: 512Mi",
        "patch_content": {"spec": {"template": {"spec": {"containers": [{"name": "app", "resources": {"limits": {"memory": "512Mi"}}}]}}}},
    }
    proposal = planner._parse_single_proposal(item_data, diag, "k8s-agent-demo", "oom-pod", target_info, None)
    assert proposal.can_remediate is True
    assert proposal.status == ProposalStatus.ACTIONABLE
    assert proposal.patch is not None
    assert proposal.patch.diff == item_data["diff"]
    assert proposal.patch.patch_content == item_data["patch_content"]
    assert proposal.target_kind == "Deployment"
    assert proposal.target_name == "oom-app"
    assert proposal.requires_approval is True
    assert proposal.approval.status == ApprovalStatus.PENDING
    assert proposal.risk_level == RiskLevel.LOW


def test_parser_valid_insufficient_evidence_proposal(base_context):
    planner, diag, target_info = base_context
    item_data = {
        "status": "INSUFFICIENT_EVIDENCE",
        "can_remediate": False,
        "insufficient_evidence_reason": "No memory profile available for sizing; developer input required.",
        "risk_level": "HIGH",
    }
    proposal = planner._parse_single_proposal(item_data, diag, "k8s-agent-demo", "oom-pod", target_info, None)
    assert proposal.can_remediate is False
    assert proposal.status == ProposalStatus.INSUFFICIENT_EVIDENCE
    assert proposal.patch is None
    assert proposal.requires_approval is True
    assert proposal.approval.status == ApprovalStatus.PENDING
    assert "No memory profile available" in proposal.insufficient_evidence_reason


# ============================================================================
# 3. Batch Parsing Resilience Tests (Missing & Duplicate Item IDs)
# ============================================================================

def test_parse_batch_response_text_handles_duplicate_conflicting_items():
    """Verify duplicate conflicting item IDs are discarded safely and isolated."""
    planner = RemediationPlanner()
    batch_items = [
        {"item_id": "diag_01", "resource_name": "pod-1"},
        {"item_id": "diag_02", "resource_name": "pod-2"},
    ]
    raw_text = """
    {
      "proposals": [
        {"item_id": "diag_01", "status": "INSUFFICIENT_EVIDENCE", "can_remediate": false},
        {"item_id": "diag_01", "status": "ACTIONABLE", "can_remediate": true, "diff": "diff", "patch_content": {"a": 1}},
        {"item_id": "diag_02", "status": "INSUFFICIENT_EVIDENCE", "can_remediate": false}
      ]
    }
    """
    parsed = planner._parse_batch_response_text(raw_text, batch_items)
    # diag_01 had conflicting duplicate entries -> discarded
    assert "diag_01" not in parsed
    # diag_02 had clean entry -> retained
    assert "diag_02" in parsed


def test_parse_batch_response_text_handles_missing_item_id_via_position():
    """Verify positional recovery when item_id is omitted but matches batch_items."""
    planner = RemediationPlanner()
    batch_items = [
        {"item_id": "diag_01", "resource_name": "pod-1"},
    ]
    raw_text = """
    {
      "proposals": [
        {"resource_name": "pod-1", "status": "INSUFFICIENT_EVIDENCE", "can_remediate": false}
      ]
    }
    """
    parsed = planner._parse_batch_response_text(raw_text, batch_items)
    assert "diag_01" in parsed


# ============================================================================
# 4. RemediationProposal Model Consistency & Safety Validator Tests
# ============================================================================

def test_safety_validator_rejects_contradiction_can_remediate_true_status_insufficient():
    """Verify safety validator rejects proposal with can_remediate=True but status!=ACTIONABLE."""
    patch_obj = YamlPatch(target_kind="Pod", target_name="test-pod", diff="diff")
    prop = RemediationProposal(
        target_workload="test-pod",
        status=ProposalStatus.INSUFFICIENT_EVIDENCE,
        can_remediate=False,
        justification="Valid justification string for pod",
        requires_approval=True,
        approval=HumanApproval(status=ApprovalStatus.PENDING),
        insufficient_evidence_reason="Valid explanation of insufficient evidence",
    )
    # Force contradiction bypassing model validator to test safety validator guardrail
    object.__setattr__(prop, "can_remediate", True)
    with pytest.raises(SafetyViolationError, match="Contradictory proposal"):
        validate_remediation_proposal(prop, [{"resource_name": "test-pod", "failure_type": "OOM_KILLED"}], ["test-pod"])


def test_safety_validator_rejects_contradiction_can_remediate_false_status_actionable():
    """Verify safety validator rejects proposal with can_remediate=False but status==ACTIONABLE."""
    prop = RemediationProposal(
        target_workload="test-pod",
        status=ProposalStatus.INSUFFICIENT_EVIDENCE,
        can_remediate=False,
        justification="Valid justification string for pod",
        requires_approval=True,
        approval=HumanApproval(status=ApprovalStatus.PENDING),
        insufficient_evidence_reason="Valid explanation of insufficient evidence",
    )
    # Model validator naturally normalizes status=ACTIONABLE with can_remediate=False to INSUFFICIENT_EVIDENCE.
    # Force contradiction on the instance to verify safety validator independently guards against it:
    object.__setattr__(prop, "status", ProposalStatus.ACTIONABLE)
    with pytest.raises(SafetyViolationError, match="Contradictory proposal"):
        validate_remediation_proposal(prop, [{"resource_name": "test-pod", "failure_type": "OOM_KILLED"}], ["test-pod"])


def test_model_normalizes_contradictory_actionable_status_with_cannot_remediate():
    """Verify RemediationProposal model validator demotes status=ACTIONABLE when can_remediate=False."""
    prop = RemediationProposal(
        target_workload="test-pod",
        status=ProposalStatus.ACTIONABLE,
        can_remediate=False,
        justification="Valid justification string for pod",
        requires_approval=True,
        approval=HumanApproval(status=ApprovalStatus.PENDING),
        insufficient_evidence_reason="Valid explanation of insufficient evidence",
    )
    assert prop.status == ProposalStatus.INSUFFICIENT_EVIDENCE
    assert prop.can_remediate is False
    assert prop.patch is None


def test_safety_validator_rejects_non_actionable_with_patch():
    """Verify safety validator rejects proposal with can_remediate=False that retains a patch."""
    patch_obj = YamlPatch(target_kind="Pod", target_name="test-pod", diff="diff")
    prop = RemediationProposal(
        target_workload="test-pod",
        status=ProposalStatus.INSUFFICIENT_EVIDENCE,
        can_remediate=False,
        justification="Valid justification string for pod",
        requires_approval=True,
        approval=HumanApproval(status=ApprovalStatus.PENDING),
        insufficient_evidence_reason="Valid explanation of insufficient evidence",
    )
    object.__setattr__(prop, "patch", patch_obj)
    with pytest.raises(SafetyViolationError, match="Proposal with can_remediate=False must not contain a YAML patch"):
        validate_remediation_proposal(prop, [{"resource_name": "test-pod", "failure_type": "OOM_KILLED"}], ["test-pod"])


def test_approval_requirement_always_enforced():
    """Verify requires_approval must be True and status must be PENDING."""
    prop = RemediationProposal(
        target_workload="test-pod",
        status=ProposalStatus.INSUFFICIENT_EVIDENCE,
        can_remediate=False,
        justification="Valid justification string for pod",
        requires_approval=False,  # Violation
        approval=HumanApproval(status=ApprovalStatus.PENDING),
        insufficient_evidence_reason="Valid explanation of insufficient evidence",
    )
    with pytest.raises(SafetyViolationError, match="requires_approval must be True"):
        validate_remediation_proposal(prop, [{"resource_name": "test-pod", "failure_type": "OOM_KILLED"}], ["test-pod"])


def test_serialization_and_deserialization_round_trip():
    """Verify RemediationProposal serializes and re-validates cleanly preserving all invariants."""
    prop = RemediationProposal(
        target_workload="test-pod",
        target_kind="Pod",
        target_name="test-pod",
        status=ProposalStatus.INSUFFICIENT_EVIDENCE,
        can_remediate=False,
        insufficient_evidence_reason="Process exited with code 1; developer review required.",
        justification=None,
    )
    dumped = prop.model_dump()
    assert dumped["status"] == "INSUFFICIENT_EVIDENCE"
    assert dumped["can_remediate"] is False
    assert dumped["patch"] is None
    assert dumped["risk_level"] == "HIGH"
    assert "Remediation withheld:" in dumped["justification"]

    json_str = prop.model_dump_json()
    reloaded = RemediationProposal.model_validate_json(json_str)
    assert reloaded.can_remediate is False
    assert reloaded.status == ProposalStatus.INSUFFICIENT_EVIDENCE
    assert reloaded.patch is None
    assert reloaded.risk_level == RiskLevel.HIGH
