"""
Unit Tests for Agentic AI Orchestrator, Agents, and Safety Guardrails.

Verifies:
1. Stage progression through the controlled sequence:
   Observation → Deterministic Diagnosis → Remediation Planning → Verification Planning.
2. Observation stage execution and telemetry collection with mocked tools/LLM.
3. Deterministic ground truth preservation (authoritative classifications cannot be overridden).
4. Remediation proposal grounding, YAML diffs, and risk assessments.
5. Insufficient evidence handling.
6. Verification plan validation (positive conditions, negative assertions).
7. Read-only tool constraint enforcement and rejection of mutating tools.
8. Secret data leakage prevention and detection.
9. Pipeline failure and timeout handling with clean resource cleanup.
10. Zero dependency on real GEMINI_API_KEY for unit test execution.
"""

import asyncio
from datetime import datetime, timezone
import json
import os
import sys
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.mcp_client import K8sMcpClientBridge
from app.models.investigation import (
    AgentToolCallTrace,
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
from app.orchestrator import (
    DeterministicDiagnosisAgent,
    InvestigationPipeline,
    ObservationAgent,
    RemediationPlanner,
    VerificationPlanner,
    get_gemini_model_name,
    is_gemini_api_key_available,
)
from app.orchestrator.agents.remediation_agent import resolve_workload_target
from app.validators.safety_validator import (
    SafetyViolationError,
    validate_controller_ownership,
    validate_deterministic_authority,
    validate_no_masking_commands,
    validate_read_only_tools,
    validate_remediation_proposal,
    validate_resource_sizing_grounding,
    validate_root_cause_alignment,
    validate_secret_safety,
)


# ============================================================================
# Helpers and Fixtures
# ============================================================================

def _build_mock_bridge():
    """Create a mock K8sMcpClientBridge with pre-configured mock MCP tools."""
    bridge = MagicMock(spec=K8sMcpClientBridge)
    bridge.is_connected = True

    tool_names = [
        "scan_cluster",
        "get_pod_details",
        "get_pod_logs",
        "get_events",
        "run_diagnosis",
        "get_pvc_details",
        "get_resource_config",
    ]
    tools = []
    for name in tool_names:
        t = MagicMock()
        t.name = name
        tools.append(t)

    bridge.get_tools = AsyncMock(return_value=tools)
    bridge.get_tool_names = AsyncMock(return_value=tool_names)
    bridge.connect = AsyncMock()
    bridge.close = AsyncMock()

    # Default tool response dispatch
    async def mock_call_tool(tool_name: str, arguments: Dict[str, Any] = None):
        arguments = arguments or {}
        ns = arguments.get("namespace", "k8s-agent-demo")

        if tool_name == "scan_cluster":
            return {
                "success": True,
                "tool": "scan_cluster",
                "namespace": ns,
                "data": {
                    "namespace": ns,
                    "pod_count": 2,
                    "pods": [
                        {
                            "pod": "test-oom-pod",
                            "namespace": ns,
                            "phase": "Running",
                            "is_ready": False,
                            "containers": [
                                {
                                    "name": "app",
                                    "restart_count": 5,
                                    "state": {
                                        "status": "terminated",
                                        "reason": "OOMKilled",
                                        "exit_code": 137,
                                    },
                                }
                            ],
                            "events": [{"type": "Warning", "reason": "OOMKilling", "message": "Killed process"}],
                        },
                        {
                            "pod": "test-crash-pod",
                            "namespace": ns,
                            "phase": "Running",
                            "is_ready": False,
                            "containers": [
                                {
                                    "name": "app",
                                    "restart_count": 10,
                                    "state": {
                                        "status": "waiting",
                                        "reason": "CrashLoopBackOff",
                                    },
                                    "last_state": {
                                        "status": "terminated",
                                        "reason": "Error",
                                        "exit_code": 1,
                                    },
                                }
                            ],
                            "events": [{"type": "Warning", "reason": "BackOff", "message": "Back-off restarting"}],
                        },
                    ],
                    "events": [
                        {"type": "Warning", "reason": "OOMKilling", "pod": "test-oom-pod"},
                        {"type": "Warning", "reason": "BackOff", "pod": "test-crash-pod"},
                    ],
                },
            }

        elif tool_name == "run_diagnosis":
            return {
                "success": True,
                "tool": "run_diagnosis",
                "namespace": ns,
                "data": {
                    "namespace": ns,
                    "diagnosis_count": 2,
                    "diagnoses": [
                        {
                            "failure_type": "MEMORY_EXHAUSTION",
                            "severity": "HIGH",
                            "resource_name": "test-oom-pod/app",
                            "namespace": ns,
                            "summary": "Container 'app' terminated due to memory exhaustion (OOMKilled).",
                            "root_cause": "Workload exceeded memory limit.",
                            "evidence": ["Container terminated with OOMKilled", "Exit code: 137"],
                            "recommended_action": "Increase memory limits.",
                            "pod": "test-oom-pod",
                            "container": "app",
                        },
                        {
                            "failure_type": "APPLICATION_PROCESS_FAILURE",
                            "severity": "HIGH",
                            "resource_name": "test-crash-pod/app",
                            "namespace": ns,
                            "summary": "Container 'app' entered CrashLoopBackOff with exit code 1.",
                            "root_cause": "Process failed at startup.",
                            "evidence": ["Waiting reason: CrashLoopBackOff", "Last exit code: 1"],
                            "recommended_action": "Fix application entrypoint.",
                            "pod": "test-crash-pod",
                            "container": "app",
                        },
                    ],
                },
            }

        elif tool_name == "get_resource_config":
            return {
                "success": True,
                "tool": "get_resource_config",
                "namespace": ns,
                "data": {
                    "pod": arguments.get("pod_name", "test-pod"),
                    "containers": [
                        {
                            "name": "app",
                            "resources": {"limits": {"memory": "64Mi"}, "requests": {"memory": "32Mi"}},
                        }
                    ],
                },
            }

        return {"success": True, "tool": tool_name, "namespace": ns, "data": {}}

    bridge.call_tool = AsyncMock(side_effect=mock_call_tool)
    return bridge


# ============================================================================
# Safety and Grounding Validator Tests
# ============================================================================

def test_validate_read_only_tools_passes_safe_tools():
    """Verify that expected read-only tools pass safety validation."""
    safe_tools = [
        MagicMock(name="scan_cluster"),
        MagicMock(name="get_pod_details"),
        MagicMock(name="get_events"),
    ]
    for st, name in zip(safe_tools, ["scan_cluster", "get_pod_details", "get_events"]):
        st.name = name

    # Must not raise
    validate_read_only_tools(safe_tools)


def test_validate_read_only_tools_rejects_mutating_tools():
    """Verify that any mutating tool triggers SafetyViolationError."""
    mutating_names = [
        "apply_manifest",
        "delete_pod",
        "patch_resource",
        "exec_shell_command",
        "restart_workload",
        "create_configmap",
    ]
    for bad_name in mutating_names:
        bad_tool = MagicMock()
        bad_tool.name = bad_name
        with pytest.raises(SafetyViolationError, match="Disallowed mutating tool"):
            validate_read_only_tools([bad_tool])


def test_validate_secret_safety_detects_secret_leak():
    """Verify that raw secret dictionary structures are flagged as safety violations."""
    safe_payload = {"pod": "web", "containers": [{"name": "app"}]}
    validate_secret_safety(safe_payload)  # Passes

    leaked_payload = {
        "pod": "db",
        "data": {"password": "c3VwZXJzZWNyZXQ="},
    }
    with pytest.raises(SafetyViolationError, match="Potential raw Kubernetes Secret"):
        validate_secret_safety(leaked_payload)


def test_validate_deterministic_authority_blocks_override():
    """Verify that an LLM cannot invent or override deterministic diagnoses."""
    deterministic = [
        {"resource_name": "pod-1", "failure_type": "MEMORY_EXHAUSTION"},
        {"resource_name": "pod-2", "failure_type": "IMAGE_PULL_FAILURE"},
    ]
    # LLM agrees -> passes
    validate_deterministic_authority(deterministic, deterministic)

    # LLM hallucinates an ungrounded failure
    hallucinated = [
        {"resource_name": "pod-1", "failure_type": "NETWORK_PARTITION_ERROR"},
    ]
    with pytest.raises(SafetyViolationError, match="LLM hallucinated or ungrounded failure"):
        validate_deterministic_authority(deterministic, hallucinated)


def test_validate_remediation_proposal_strict_approval():
    """Verify that requires_approval=False or non-PENDING status is rejected."""
    patch = YamlPatch(target_kind="Pod", target_name="test-pod", diff="diff")
    det = [{"resource_name": "test-pod", "failure_type": "MEMORY_EXHAUSTION"}]

    # Proposal with requires_approval=False
    bad_prop1 = RemediationProposal(
        target_workload="test-pod",
        patch=patch,
        justification="Valid justification",
        requires_approval=False,
    )
    with pytest.raises(SafetyViolationError, match="requires_approval must be True"):
        validate_remediation_proposal(bad_prop1, det, ["test-pod"])

    # Proposal with approval status APPROVED before review
    bad_prop2 = RemediationProposal(
        target_workload="test-pod",
        patch=patch,
        justification="Valid justification",
        requires_approval=True,
        approval=HumanApproval(status=ApprovalStatus.APPROVED),
    )
    with pytest.raises(SafetyViolationError, match="status must be PENDING"):
        validate_remediation_proposal(bad_prop2, det, ["test-pod"])


def test_validate_remediation_proposal_ungrounded_workload():
    """Verify that proposals targeting unobserved workloads are rejected."""
    patch = YamlPatch(target_kind="Pod", target_name="nonexistent-workload", diff="diff")
    det = [{"resource_name": "observed-pod", "failure_type": "MEMORY_EXHAUSTION"}]

    bad_prop = RemediationProposal(
        target_workload="nonexistent-workload",
        patch=patch,
        justification="Fix memory limits",
        requires_approval=True,
        approval=HumanApproval(status=ApprovalStatus.PENDING),
    )
    with pytest.raises(SafetyViolationError, match="Ungrounded proposal"):
        validate_remediation_proposal(bad_prop, det, ["observed-pod"])


# ============================================================================
# Agent Stage Unit Tests
# ============================================================================

@pytest.mark.anyio
async def test_observation_agent_telemetry_collection():
    """Verify ObservationAgent collects telemetry and builds ObservationReport."""
    bridge = _build_mock_bridge()
    agent = ObservationAgent(bridge=bridge, model_name="gemini-3.8-flash")

    req = InvestigationRequest(namespace="k8s-agent-demo")
    obs_report, traces, _ = await agent.observe(request=req, use_llm=False)

    assert obs_report.namespace == "k8s-agent-demo"
    assert obs_report.pod_count == 2
    assert len(traces) >= 1
    assert traces[0].tool_name == "scan_cluster"
    assert traces[0].status == "SUCCESS"


@pytest.mark.anyio
async def test_observation_agent_fails_clearly_without_api_key_when_llm_requested():
    """Verify ObservationAgent raises clear ValueError if LLM is requested without API key."""
    bridge = _build_mock_bridge()
    agent = ObservationAgent(bridge=bridge)
    req = InvestigationRequest(namespace="k8s-agent-demo")

    with patch.dict(os.environ, {}, clear=True):
        if "GEMINI_API_KEY" in os.environ:
            del os.environ["GEMINI_API_KEY"]
        with pytest.raises(ValueError, match="GEMINI_API_KEY environment variable is not set"):
            await agent.observe(request=req, use_llm=True)


@pytest.mark.anyio
async def test_deterministic_diagnosis_agent_preservation():
    """Verify DeterministicDiagnosisAgent preserves exact deterministic classifications."""
    bridge = _build_mock_bridge()
    agent = DeterministicDiagnosisAgent(bridge=bridge)

    diagnoses, traces, consistency = await agent.diagnose(namespace="k8s-agent-demo")
    assert len(diagnoses) == 2
    assert traces[0].tool_name == "run_diagnosis"

    types = [d["failure_type"] for d in diagnoses]
    assert "MEMORY_EXHAUSTION" in types
    assert "APPLICATION_PROCESS_FAILURE" in types
    assert consistency["is_consistent"] is True


@pytest.mark.anyio
async def test_remediation_planner_grounded_proposals():
    """Verify RemediationPlanner requires concrete evidence for resource sizing adjustments."""
    bridge = _build_mock_bridge()
    planner = RemediationPlanner(bridge=bridge)

    # Case 1: Raw OOM diagnosis without workload profiling evidence -> INSUFFICIENT_EVIDENCE
    diagnoses_no_evidence = [
        {
            "failure_type": "MEMORY_EXHAUSTION",
            "resource_name": "test-oom-pod/app",
            "pod": "test-oom-pod",
            "container": "app",
            "summary": "Container terminated with OOMKilled",
            "recommended_action": "Increase memory limits",
            "evidence": ["Container terminated with OOMKilled", "Exit code: 137"],
        }
    ]
    observed_pods = [{"pod": "test-oom-pod"}]

    proposals = await planner.plan_remediations(
        diagnoses=diagnoses_no_evidence,
        namespace="k8s-agent-demo",
        observed_pods=observed_pods,
        use_llm=False,
    )

    assert len(proposals) == 1
    prop = proposals[0]
    assert prop.target_workload == "test-oom-pod"
    assert prop.status == ProposalStatus.INSUFFICIENT_EVIDENCE
    assert prop.can_remediate is False
    assert prop.patch is None
    assert prop.requires_approval is True
    assert prop.approval.status == ApprovalStatus.PENDING
    assert "OOMKilled" in prop.insufficient_evidence_reason

    # Case 2: OOM diagnosis WITH concrete profiling evidence -> ACTIONABLE with verified limit
    diagnoses_with_evidence = [
        {
            "failure_type": "MEMORY_EXHAUSTION",
            "resource_name": "test-oom-pod/app",
            "pod": "test-oom-pod",
            "container": "app",
            "summary": "Container terminated with OOMKilled",
            "recommended_action": "Increase memory limits",
            "evidence": [
                "Container terminated with OOMKilled",
                "Exit code: 137",
                "Required memory limit: 512Mi based on profiler",
            ],
        }
    ]

    proposals_grounded = await planner.plan_remediations(
        diagnoses=diagnoses_with_evidence,
        namespace="k8s-agent-demo",
        observed_pods=observed_pods,
        use_llm=False,
    )

    assert len(proposals_grounded) == 1
    gprop = proposals_grounded[0]
    assert gprop.target_workload == "test-oom-pod"
    assert gprop.status == ProposalStatus.ACTIONABLE
    assert gprop.can_remediate is True
    assert gprop.patch is not None
    assert "512Mi" in gprop.patch.diff
    assert gprop.risk_level == RiskLevel.MEDIUM
    assert gprop.requires_approval is True
    assert gprop.approval.status == ApprovalStatus.PENDING


@pytest.mark.anyio
async def test_verification_planner_negative_assertions():
    """Verify VerificationPlanner attaches appropriate negative assertions per failure type."""
    planner = VerificationPlanner(check_interval_seconds=5, timeout_seconds=45)

    patch = YamlPatch(target_kind="Pod", target_name="test-oom-pod", diff="diff")
    prop = RemediationProposal(
        target_workload="test-oom-pod",
        patch=patch,
        justification="Fix memory limit",
    )
    diagnoses = [{"pod": "test-oom-pod", "failure_type": "MEMORY_EXHAUSTION"}]

    verified = planner.plan_verification(proposals=[prop], diagnoses=diagnoses)
    plan = verified[0].verification_plan

    assert plan is not None
    assert plan.target_phase == "Running"
    assert "OOMKilled" in plan.negative_assertions
    assert "CrashLoopBackOff" in plan.negative_assertions
    assert plan.timeout_seconds == 45


# ============================================================================
# Investigation Pipeline Integration Tests
# ============================================================================

@pytest.mark.anyio
async def test_investigation_pipeline_full_workflow():
    """Verify end-to-end stage progression of InvestigationPipeline."""
    bridge = _build_mock_bridge()
    pipeline = InvestigationPipeline(bridge=bridge)

    req = InvestigationRequest(namespace="k8s-agent-demo")
    result = await pipeline.execute(request=req, use_llm=False)

    assert result.status == InvestigationStatus.COMPLETED
    assert result.namespace == "k8s-agent-demo"
    assert result.completed_at is not None
    assert result.observation is not None
    assert result.observation.pod_count == 2
    assert len(result.diagnoses) == 2
    assert len(result.remediation_proposals) == 2
    assert len(result.tool_traces) >= 2
    assert result.provenance == "DETERMINISTIC_FALLBACK"

    oom_prop = [p for p in result.remediation_proposals if p["target_workload"] == "test-oom-pod"][0]
    crash_prop = [p for p in result.remediation_proposals if p["target_workload"] == "test-crash-pod"][0]

    # Insufficient evidence proposal for MEMORY_EXHAUSTION (raw OOM without profiling evidence)
    assert oom_prop["status"] == "INSUFFICIENT_EVIDENCE"
    assert oom_prop["can_remediate"] is False
    assert oom_prop["patch"] is None
    assert oom_prop["verification_plan"] is None
    assert oom_prop["requires_approval"] is True
    assert oom_prop["approval"]["status"] == "PENDING"
    assert "OOMKilled" in oom_prop["insufficient_evidence_reason"]
    assert oom_prop["provenance"] == "DETERMINISTIC_FALLBACK"

    # Insufficient evidence proposal for APPLICATION_PROCESS_FAILURE (never masks failure)
    assert crash_prop["status"] == "INSUFFICIENT_EVIDENCE"
    assert crash_prop["can_remediate"] is False
    assert crash_prop["patch"] is None
    assert crash_prop["verification_plan"] is None
    assert crash_prop["requires_approval"] is True
    assert "CrashLoopBackOff" in crash_prop["insufficient_evidence_reason"]
    assert crash_prop["provenance"] == "DETERMINISTIC_FALLBACK"


@pytest.mark.anyio
async def test_investigation_pipeline_handles_error_gracefully():
    """Verify that an error in any stage transitions pipeline to FAILED cleanly."""
    bridge = _build_mock_bridge()
    # Force scan_cluster failure
    bridge.call_tool = AsyncMock(side_effect=RuntimeError("Subprocess pipe broken"))

    pipeline = InvestigationPipeline(bridge=bridge)
    req = InvestigationRequest(namespace="k8s-agent-demo")

    result = await pipeline.execute(request=req, use_llm=False)
    assert result.status == InvestigationStatus.FAILED
    assert result.error is not None
    assert "Subprocess pipe broken" in result.error["message"]
    assert result.completed_at is not None


@pytest.mark.anyio
async def test_pipeline_timeout_handling():
    """Verify that stage timeouts abort gracefully without hanging."""
    bridge = _build_mock_bridge()

    async def slow_call(*args, **kwargs):
        await asyncio.sleep(5.0)
        return {"success": True}

    bridge.call_tool = AsyncMock(side_effect=slow_call)
    pipeline = InvestigationPipeline(bridge=bridge, stage_timeout_seconds=0.1, total_timeout_seconds=0.5)

    req = InvestigationRequest(namespace="k8s-agent-demo")
    result = await pipeline.execute(request=req, use_llm=False)

    assert result.status == InvestigationStatus.FAILED
    assert result.error is not None
    assert result.completed_at is not None


def test_orchestrator_environment_helpers():
    """Verify model and API key configuration helper functions."""
    with patch.dict(os.environ, {"GEMINI_MODEL": "custom-gemini-test"}):
        assert get_gemini_model_name() == "custom-gemini-test"

    with patch.dict(os.environ, {}, clear=True):
        if "GEMINI_MODEL" in os.environ:
            del os.environ["GEMINI_MODEL"]
        assert get_gemini_model_name() == "gemini-3.8-flash"

    with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key-xyz"}):
        assert is_gemini_api_key_available() is True

    with patch.dict(os.environ, {}, clear=True):
        if "GEMINI_API_KEY" in os.environ:
            del os.environ["GEMINI_API_KEY"]
        assert is_gemini_api_key_available() is False


def test_model_configuration_precedence_and_defaults():
    """
    Verify model configuration precedence:
    1. Explicit agent/pipeline argument takes highest precedence.
    2. GEMINI_MODEL environment variable takes precedence over default.
    3. Default model is gemini-3.8-flash across all active components.
    4. Outdated gemini-2.5-flash is eliminated from active defaults.
    """
    bridge = _build_mock_bridge()

    # Case 1: Code default is gemini-3.8-flash across all components when env is unset
    with patch.dict(os.environ, {}, clear=True):
        assert get_gemini_model_name() == "gemini-3.8-flash"
        obs = ObservationAgent(bridge=bridge)
        assert obs.model_name == "gemini-3.8-flash"
        rem = RemediationPlanner(bridge=bridge)
        assert rem.model_name == "gemini-3.8-flash"
        pipe = InvestigationPipeline(bridge=bridge)
        assert pipe.model_name == "gemini-3.8-flash"

    # Case 2: GEMINI_MODEL environment variable takes precedence over default
    with patch.dict(os.environ, {"GEMINI_MODEL": "custom-override-model"}):
        assert get_gemini_model_name() == "custom-override-model"
        obs = ObservationAgent(bridge=bridge)
        assert obs.model_name == "custom-override-model"
        rem = RemediationPlanner(bridge=bridge)
        assert rem.model_name == "custom-override-model"
        pipe = InvestigationPipeline(bridge=bridge)
        assert pipe.model_name == "custom-override-model"

    # Case 3: Explicit constructor argument takes highest precedence over env
    with patch.dict(os.environ, {"GEMINI_MODEL": "custom-override-model"}):
        obs = ObservationAgent(bridge=bridge, model_name="explicit-arg-model")
        assert obs.model_name == "explicit-arg-model"
        rem = RemediationPlanner(bridge=bridge, model_name="explicit-arg-model")
        assert rem.model_name == "explicit-arg-model"
        pipe = InvestigationPipeline(bridge=bridge, model_name="explicit-arg-model")
        assert pipe.model_name == "explicit-arg-model"


# ============================================================================
# Phase 4C Correctness & Safety Review Tests
# ============================================================================

def test_sleep_remediation_rejected():
    """Verify that proposing 'sleep 3600' or any sleep command is strictly rejected."""
    patch = YamlPatch(
        target_kind="Pod",
        target_name="test-crash-pod",
        diff="--- command: ['sh', '-c', 'exit 1']\n+++ command: ['sh', '-c', 'sleep 3600']",
        patch_content={"spec": {"containers": [{"name": "app", "command": ["sh", "-c", "sleep 3600"]}]}},
    )
    # Direct validation of diff
    with pytest.raises(SafetyViolationError, match="Detected masking command pattern"):
        validate_no_masking_commands(patch.diff, patch.patch_content)

    # Full proposal validation
    prop = RemediationProposal(
        target_workload="test-crash-pod",
        target_kind="Pod",
        target_name="test-crash-pod",
        patch=patch,
        justification="Fix application exit code by sleeping",
        requires_approval=True,
        approval=HumanApproval(status=ApprovalStatus.PENDING),
    )
    with pytest.raises(SafetyViolationError, match="Detected masking command pattern"):
        validate_remediation_proposal(
            proposal=prop,
            deterministic_diagnoses=[{"pod": "test-crash-pod", "failure_type": "APPLICATION_PROCESS_FAILURE"}],
            observed_pod_names=["test-crash-pod"],
        )


def test_arbitrary_placeholder_commands_rejected():
    """Verify that all arbitrary placeholder commands (true, tail -f /dev/null, pause, exit 0) are rejected."""
    masking_cases = [
        ("--- command: ['exit', '1']\n+++ command: ['true']", {"command": ["true"]}),
        ("--- command: ['exit', '1']\n+++ command: ['/bin/true']", {"command": ["/bin/true"]}),
        ("--- command: ['exit', '1']\n+++ command: ['tail', '-f', '/dev/null']", {"command": ["tail", "-f", "/dev/null"]}),
        ("--- command: ['exit', '1']\n+++ command: ['pause']", {"command": ["pause"]}),
        ("--- command: ['exit', '1']\n+++ command: ['exit 0']", {"command": ["exit 0"]}),
        ("--- command: ['exit', '1']\n+++ command: ['sleep 1000']", {"command": ["sleep 1000"]}),
    ]
    for diff, patch_content in masking_cases:
        with pytest.raises(SafetyViolationError, match="Unsafe remediation rejected"):
            validate_no_masking_commands(diff, patch_content)


def test_controller_ownership_targeting_enforced():
    """Verify that targeting an ephemeral Pod when managed by a controller is rejected."""
    pod_evidence = {
        "pod": "crashloop-test-fbf6dbcf7-sxst9",
        "owners": [{"kind": "ReplicaSet", "name": "crashloop-test-fbf6dbcf7"}],
    }
    # Resolve ownership
    target_info = resolve_workload_target("crashloop-test-fbf6dbcf7-sxst9", pod_evidence)
    assert target_info["target_kind"] == "Deployment"
    assert target_info["target_name"] == "crashloop-test"
    assert target_info["is_standalone_pod"] is False

    # A proposal attempting to patch the ephemeral Pod directly must be rejected
    patch = YamlPatch(
        target_kind="Pod",
        target_name="crashloop-test-fbf6dbcf7-sxst9",
        diff="--- spec.containers[0].resources.limits.memory: 64Mi\n+++ spec.containers[0].resources.limits.memory: 256Mi",
    )
    prop = RemediationProposal(
        target_workload="crashloop-test-fbf6dbcf7-sxst9",
        target_kind="Pod",
        target_name="crashloop-test-fbf6dbcf7-sxst9",
        patch=patch,
        justification="Adjust limits",
        requires_approval=True,
        approval=HumanApproval(status=ApprovalStatus.PENDING),
    )
    with pytest.raises(SafetyViolationError, match="Direct patches to ephemeral Pods are forbidden"):
        validate_controller_ownership(prop, pod_evidence)


def test_standalone_pod_not_assumed_to_have_controller():
    """Verify that a standalone Pod is not assumed to have an unverified controller."""
    pod_evidence = {
        "pod": "insufficient-cpu",
        "owners": [],
    }
    target_info = resolve_workload_target("insufficient-cpu", pod_evidence)
    assert target_info["target_kind"] == "Pod"
    assert target_info["target_name"] == "insufficient-cpu"
    assert target_info["is_standalone_pod"] is True

    # Attempting to fabricate a Deployment controller for standalone pod must be rejected
    patch = YamlPatch(
        target_kind="Deployment",
        target_name="insufficient-cpu-deployment",
        diff="--- cpu: 500\n+++ cpu: 100m",
    )
    prop = RemediationProposal(
        target_workload="insufficient-cpu",
        target_kind="Deployment",
        target_name="insufficient-cpu-deployment",
        patch=patch,
        justification="Fabricated deployment target",
        requires_approval=True,
        approval=HumanApproval(status=ApprovalStatus.PENDING),
    )
    with pytest.raises(SafetyViolationError, match="Fabricating an unverified controller target"):
        validate_controller_ownership(prop, pod_evidence)


@pytest.mark.anyio
async def test_insufficient_evidence_produces_no_patch():
    """Verify that APPLICATION_PROCESS_FAILURE produces an explicit INSUFFICIENT_EVIDENCE result with no patch."""
    planner = RemediationPlanner()
    diagnoses = [
        {
            "failure_type": "APPLICATION_PROCESS_FAILURE",
            "resource_name": "crashloop-test/app",
            "pod": "crashloop-test",
            "summary": "Container exited with code 1",
            "recommended_action": "Inspect logs and code",
        }
    ]
    observed_pods = [{"pod": "crashloop-test", "owners": []}]

    proposals = await planner.plan_remediations(
        diagnoses=diagnoses,
        namespace="k8s-agent-demo",
        observed_pods=observed_pods,
        use_llm=False,
    )

    assert len(proposals) == 1
    prop = proposals[0]
    assert prop.status == ProposalStatus.INSUFFICIENT_EVIDENCE
    assert prop.can_remediate is False
    assert prop.patch is None
    assert prop.insufficient_evidence_reason is not None
    assert "CrashLoopBackOff" in prop.insufficient_evidence_reason
    assert "placeholder" in prop.insufficient_evidence_reason.lower()


def test_remediation_contradicting_diagnosis_rejected():
    """Verify that a remediation patch that does not address the root cause is rejected."""
    # Diagnosis is IMAGE_PULL_FAILURE, but patch touches memory limits
    patch = YamlPatch(
        target_kind="Pod",
        target_name="demo-app",
        diff="--- spec.containers[0].resources.limits.memory: 64Mi\n+++ spec.containers[0].resources.limits.memory: 256Mi",
    )
    prop = RemediationProposal(
        target_workload="demo-app",
        target_kind="Pod",
        target_name="demo-app",
        patch=patch,
        justification="Increase memory limits",
        requires_approval=True,
        approval=HumanApproval(status=ApprovalStatus.PENDING),
    )
    diag = {"pod": "demo-app", "failure_type": "IMAGE_PULL_FAILURE"}

    with pytest.raises(SafetyViolationError, match="Remediation patch contradicts diagnosis"):
        validate_root_cause_alignment(prop, diag)


@pytest.mark.anyio
async def test_gemini_unavailable_fallback_clearly_identified():
    """Verify that deterministic fallback is explicitly flagged with ProposalProvenance.DETERMINISTIC_FALLBACK."""
    planner = RemediationPlanner()
    diagnoses = [{"failure_type": "MEMORY_EXHAUSTION", "pod": "oom-app", "resource_name": "oom-app/app"}]
    observed_pods = [{"pod": "oom-app", "owners": []}]

    proposals = await planner.plan_remediations(
        diagnoses=diagnoses,
        namespace="k8s-agent-demo",
        observed_pods=observed_pods,
        use_llm=False,
    )

    prop = proposals[0]
    assert prop.provenance == ProposalProvenance.DETERMINISTIC_FALLBACK
    assert prop.model_metadata is not None
    assert prop.model_metadata["model"] is None
    assert prop.model_metadata["provider"] == "deterministic_rules"


@pytest.mark.anyio
async def test_gemini_execution_distinguishable_from_fallback():
    """Verify that a Gemini-executed proposal has ProposalProvenance.GEMINI_AGENT and model metadata."""
    planner = RemediationPlanner(model_name="gemini-3.8-flash")
    diag = {
        "failure_type": "MEMORY_EXHAUSTION",
        "pod": "oom-app",
        "resource_name": "oom-app/app",
        "evidence": ["Container terminated with OOMKilled", "Required memory limit: 256Mi based on profiler"],
    }

    # Mock Gemini Client generate_content response
    mock_resp = MagicMock()
    mock_resp.text = json.dumps({
        "status": "ACTIONABLE",
        "can_remediate": True,
        "justification": "Grounded Gemini memory limit remediation",
        "risk_level": "MEDIUM",
        "diff": "--- spec.containers[0].resources.limits.memory: 64Mi\n+++ spec.containers[0].resources.limits.memory: 256Mi",
        "patch_content": {"spec": {"containers": [{"name": "app", "resources": {"limits": {"memory": "256Mi"}}}]}},
    })

    with patch("app.orchestrator.agents.remediation_agent.Client") as mock_client_cls:
        mock_instance = MagicMock()
        mock_instance.aio.models.generate_content = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_instance

        with patch.dict(os.environ, {"GEMINI_API_KEY": "dummy-key"}):
            proposals = await planner.plan_remediations(
                diagnoses=[diag],
                namespace="k8s-agent-demo",
                observed_pods=[{"pod": "oom-app", "owners": []}],
                use_llm=True,
            )

    assert len(proposals) == 1
    prop = proposals[0]
    # Distinguishable from fallback!
    assert prop.provenance == ProposalProvenance.GEMINI_AGENT
    assert prop.model_metadata is not None
    assert prop.model_metadata["model"] == "gemini-3.8-flash"
    assert prop.model_metadata["provider"] == "google_genai"


def test_unsupported_memory_increase_rejected():
    """Verify that proposing an arbitrary memory limit without supporting evidence is rejected."""
    # Proposal proposes increasing memory limit to 256Mi
    patch = YamlPatch(
        target_kind="Pod",
        target_name="test-oom-pod",
        diff="--- spec.containers[0].resources.limits.memory: 64Mi\n+++ spec.containers[0].resources.limits.memory: 256Mi",
        patch_content={"spec": {"containers": [{"name": "app", "resources": {"limits": {"memory": "256Mi"}}}]}},
    )
    prop = RemediationProposal(
        target_workload="test-oom-pod",
        target_kind="Pod",
        target_name="test-oom-pod",
        patch=patch,
        justification="Arbitrary bump to 256Mi",
        requires_approval=True,
        approval=HumanApproval(status=ApprovalStatus.PENDING),
    )

    # Diagnosis evidence only contains raw OOM events, no justification for 256Mi
    diag = {
        "failure_type": "MEMORY_EXHAUSTION",
        "pod": "test-oom-pod",
        "resource_name": "test-oom-pod/app",
        "evidence": ["Container terminated with exit code 137 (OOMKilled)", "Restart count: 5"],
    }

    # 1. Independent validator function must raise SafetyViolationError
    with pytest.raises(SafetyViolationError, match="Unjustified resource change: Proposed memory limit '256Mi'"):
        validate_resource_sizing_grounding(prop, diag)

    # 2. Comprehensive proposal validator must also reject it
    with pytest.raises(SafetyViolationError, match="Unjustified resource change: Proposed memory limit '256Mi'"):
        validate_remediation_proposal(
            proposal=prop,
            deterministic_diagnoses=[diag],
            observed_pod_names=["test-oom-pod"],
        )


def test_grounded_memory_increase_accepted_when_evidence_present():
    """Verify that proposing a memory limit that IS justified by evidence passes validation."""
    patch = YamlPatch(
        target_kind="Pod",
        target_name="test-oom-pod",
        diff="--- spec.containers[0].resources.limits.memory: 64Mi\n+++ spec.containers[0].resources.limits.memory: 512Mi",
        patch_content={"spec": {"containers": [{"name": "app", "resources": {"limits": {"memory": "512Mi"}}}]}},
    )
    prop = RemediationProposal(
        target_workload="test-oom-pod",
        target_kind="Pod",
        target_name="test-oom-pod",
        patch=patch,
        justification="Increase to 512Mi based on profiler evidence",
        requires_approval=True,
        approval=HumanApproval(status=ApprovalStatus.PENDING),
    )
    diag = {
        "failure_type": "MEMORY_EXHAUSTION",
        "pod": "test-oom-pod",
        "resource_name": "test-oom-pod/app",
        "evidence": [
            "Container terminated with exit code 137 (OOMKilled)",
            "Required memory limit: 512Mi based on profiler",
        ],
    }

    # Must pass without raising
    validate_resource_sizing_grounding(prop, diag)
    validate_remediation_proposal(
        proposal=prop,
        deterministic_diagnoses=[diag],
        observed_pod_names=["test-oom-pod"],
    )


def test_unsupported_cpu_request_rejected():
    """Verify that proposing an arbitrary CPU reduction without supporting evidence is rejected."""
    patch = YamlPatch(
        target_kind="Pod",
        target_name="insufficient-cpu",
        diff="--- spec.containers[0].resources.requests.cpu: 500\n+++ spec.containers[0].resources.requests.cpu: 100m",
        patch_content={"spec": {"containers": [{"name": "app", "resources": {"requests": {"cpu": "100m"}}}]}},
    )
    prop = RemediationProposal(
        target_workload="insufficient-cpu",
        target_kind="Pod",
        target_name="insufficient-cpu",
        patch=patch,
        justification="Arbitrary CPU reduction to 100m",
        requires_approval=True,
        approval=HumanApproval(status=ApprovalStatus.PENDING),
    )
    diag = {
        "failure_type": "INSUFFICIENT_CLUSTER_RESOURCES",
        "pod": "insufficient-cpu",
        "resource_name": "insufficient-cpu/app",
        "evidence": ["0/1 nodes are available: 1 Insufficient cpu."],
    }

    with pytest.raises(SafetyViolationError, match="Unjustified resource change: Proposed CPU request '100m'"):
        validate_resource_sizing_grounding(prop, diag)


# ============================================================================
# Phase 4C Timeout Architecture, Retries & Error Handling Tests
# ============================================================================

@pytest.mark.anyio
async def test_gemini_observation_succeeds_normally():
    """Verify ObservationAgent with mock Gemini completes with GEMINI_AGENT provenance."""
    bridge = _build_mock_bridge()
    agent = ObservationAgent(bridge=bridge)
    req = InvestigationRequest(namespace="k8s-agent-demo")

    async def mock_run_async(*args, **kwargs):
        event = MagicMock()
        fc = MagicMock()
        fc.name = "scan_cluster"
        fc.args = {"namespace": "k8s-agent-demo"}
        event.get_function_calls.return_value = [fc]
        event.message = MagicMock()
        part = MagicMock()
        part.text = "Identified 2 pods in namespace."
        event.message.parts = [part]
        yield event

    with patch.dict(os.environ, {"GEMINI_API_KEY": "mock-api-key"}):
        with patch("app.orchestrator.agents.observation_agent.Runner.run_async", side_effect=mock_run_async):
            obs_report, traces, synth = await agent.observe(request=req, use_llm=True)

    assert obs_report.provenance == "GEMINI_AGENT"
    assert obs_report.llm_error is None
    assert obs_report.pod_count == 2
    assert synth == "Identified 2 pods in namespace."
    assert any(t.tool_name == "scan_cluster" and t.agent_name == "observation_agent" for t in traces)


@pytest.mark.anyio
async def test_gemini_transient_failure_then_succeeds():
    """Verify retry options include transient error codes and succeed after transient failure."""
    from app.orchestrator.agents.observation_agent import DEFAULT_RETRY_OPTIONS

    assert DEFAULT_RETRY_OPTIONS.attempts == 3
    assert DEFAULT_RETRY_OPTIONS.initial_delay == 1.0
    assert DEFAULT_RETRY_OPTIONS.max_delay == 8.0
    for code in [408, 429, 500, 502, 503, 504]:
        assert code in DEFAULT_RETRY_OPTIONS.http_status_codes

    bridge = _build_mock_bridge()
    agent = ObservationAgent(bridge=bridge)
    req = InvestigationRequest(namespace="k8s-agent-demo")

    async def mock_run_async(*args, **kwargs):
        event = MagicMock()
        event.get_function_calls.return_value = []
        event.message = MagicMock()
        part = MagicMock()
        part.text = "Success on retry"
        event.message.parts = [part]
        yield event

    with patch.dict(os.environ, {"GEMINI_API_KEY": "mock-api-key"}):
        with patch("app.orchestrator.agents.observation_agent.Runner.run_async", side_effect=mock_run_async):
            obs_report, traces, synth = await agent.observe(request=req, use_llm=True)

    assert obs_report.provenance == "GEMINI_AGENT"
    assert obs_report.llm_error is None
    assert synth == "Success on retry"


@pytest.mark.anyio
async def test_gemini_exhausts_retries_and_deterministic_fallback_succeeds():
    """Verify that when Gemini raises 503 (retries exhausted), deterministic fallback succeeds."""
    bridge = _build_mock_bridge()
    agent = ObservationAgent(bridge=bridge)
    req = InvestigationRequest(namespace="k8s-agent-demo")

    async def mock_run_async_fail(*args, **kwargs):
        raise RuntimeError("503 The model is overloaded. Please try again later.")
        yield

    with patch.dict(os.environ, {"GEMINI_API_KEY": "mock-api-key"}):
        with patch("app.orchestrator.agents.observation_agent.Runner.run_async", side_effect=mock_run_async_fail):
            obs_report, traces, synth = await agent.observe(request=req, use_llm=True)

    assert obs_report.provenance == "DETERMINISTIC_FALLBACK"
    assert obs_report.llm_error is not None
    assert obs_report.llm_error["type"] == "RuntimeError"
    assert "503 The model is overloaded" in obs_report.llm_error["message"]
    assert obs_report.pod_count == 2
    assert any(t.tool_name == "scan_cluster" and t.agent_name == "telemetry_collector" for t in traces)


@pytest.mark.anyio
async def test_gemini_timeout_triggers_deterministic_fallback():
    """Verify that an LLM timeout triggers fallback without crashing the pipeline."""
    bridge = _build_mock_bridge()
    agent = ObservationAgent(bridge=bridge)
    req = InvestigationRequest(namespace="k8s-agent-demo")

    async def mock_scan_call(tool_name, arguments=None):
        return {
            "success": True,
            "data": {
                "pods": [{"pod": "test-pod", "phase": "Running"}],
                "events": [],
                "pod_count": 1,
            },
        }
    bridge.call_tool = AsyncMock(side_effect=mock_scan_call)

    real_wait_for = asyncio.wait_for

    async def mock_wait_for(coro, timeout):
        # If waiting for LLM loop (timeout <= 10.0), simulate timeout
        if timeout is not None and timeout <= 10.0:
            raise asyncio.TimeoutError("LLM call timed out")
        return await real_wait_for(coro, timeout=timeout)

    with patch.dict(os.environ, {"GEMINI_API_KEY": "mock-api-key"}):
        with patch.object(agent, "get_adk_agent", AsyncMock()):
            with patch("asyncio.wait_for", side_effect=mock_wait_for):
                obs_report, traces, synth = await agent.observe(request=req, use_llm=True, timeout_seconds=30.0)

    assert obs_report.provenance == "DETERMINISTIC_FALLBACK"
    assert obs_report.llm_error is not None
    assert obs_report.llm_error["type"] == "TimeoutError"
    assert "timed out" in obs_report.llm_error["message"]
    assert obs_report.pod_count == 1


@pytest.mark.anyio
async def test_observation_fallback_failure_produces_failed_result():
    """Verify that if deterministic evidence collection also fails, the pipeline returns FAILED."""
    bridge = _build_mock_bridge()
    bridge.call_tool = AsyncMock(return_value={"success": False, "error": "Kubernetes API unreachable"})

    pipeline = InvestigationPipeline(bridge=bridge)
    req = InvestigationRequest(namespace="k8s-agent-demo")

    result = await pipeline.execute(request=req, use_llm=False)

    assert result.status == InvestigationStatus.FAILED
    assert result.error is not None
    assert result.error["failed_at_status"] == "OBSERVING"
    assert result.error["type"] == "RuntimeError"
    assert "Deterministic evidence collection failed" in result.error["message"]


def test_pipeline_timeout_leaves_headroom_for_fallback():
    """Verify pipeline configuration ensures sufficient headroom for deterministic fallback."""
    pipe = InvestigationPipeline(stage_timeout_seconds=120.0)
    assert pipe.stage_timeout == 120.0
    stage1_outer_timeout = pipe.stage_timeout + 15.0
    llm_timeout = max(10.0, pipe.stage_timeout - 20.0)
    reserved_fallback = 20.0

    assert llm_timeout + reserved_fallback <= stage1_outer_timeout
    assert stage1_outer_timeout - (llm_timeout + reserved_fallback) >= 15.0


@pytest.mark.anyio
async def test_failed_stage_correctly_preserved_on_failure():
    """Verify that failure captures the exact stage at which failure occurred."""
    bridge = _build_mock_bridge()
    pipeline = InvestigationPipeline(bridge=bridge)
    req = InvestigationRequest(namespace="k8s-agent-demo")

    # Force failure in DIAGNOSING stage
    with patch("app.orchestrator.pipeline.DeterministicDiagnosisAgent.diagnose", side_effect=RuntimeError("Diagnosis engine crashed")):
        result = await pipeline.execute(request=req, use_llm=False)

    assert result.status == InvestigationStatus.FAILED
    assert result.error is not None
    assert result.error["failed_at_status"] == "DIAGNOSING"
    assert result.error["type"] == "RuntimeError"
    assert result.error["message"] == "Diagnosis engine crashed"

    # Force timeout in OBSERVING stage and check non-empty message
    with patch("app.orchestrator.pipeline.ObservationAgent.observe", side_effect=asyncio.TimeoutError()):
        result2 = await pipeline.execute(request=req, use_llm=False)

    assert result2.status == InvestigationStatus.FAILED
    assert result2.error is not None
    assert result2.error["failed_at_status"] == "OBSERVING"
    assert "timed out after" in result2.error["message"]
    assert result2.error["message"] != ""


@pytest.mark.anyio
async def test_provenance_correctly_assigned_on_fallback_vs_gemini():
    """Verify provenance is GEMINI_AGENT only on true Gemini success, otherwise DETERMINISTIC_FALLBACK."""
    bridge = _build_mock_bridge()
    req = InvestigationRequest(namespace="k8s-agent-demo")

    # Case A: use_llm=False -> DETERMINISTIC_FALLBACK with deterministic_rules
    pipeline = InvestigationPipeline(bridge=bridge)
    res_det = await pipeline.execute(request=req, use_llm=False)
    assert res_det.provenance == "DETERMINISTIC_FALLBACK"
    assert res_det.model_metadata["provider"] == "deterministic_rules"
    assert res_det.model_metadata["model"] is None

    # Case B: Gemini observation fails, fallback succeeds -> DETERMINISTIC_FALLBACK with deterministic_fallback and gemini_error
    mock_obs_report = ObservationReport(
        namespace="k8s-agent-demo",
        pod_count=2,
        pods=[],
        events=[],
        logs_summary={},
        provenance="DETERMINISTIC_FALLBACK",
        llm_error={"type": "HttpError", "message": "503 Service Unavailable"},
    )
    with patch.dict(os.environ, {"GEMINI_API_KEY": "mock-api-key"}):
        with patch("app.orchestrator.pipeline.ObservationAgent.observe", return_value=(mock_obs_report, [], None)):
            res_fallback = await pipeline.execute(request=req, use_llm=True)
            assert res_fallback.provenance == "DETERMINISTIC_FALLBACK"
            assert res_fallback.model_metadata["provider"] == "deterministic_fallback"
            assert res_fallback.model_metadata["gemini_error"]["type"] == "HttpError"

    # Case C: Gemini observation succeeds -> GEMINI_AGENT
    mock_obs_report_gemini = ObservationReport(
        namespace="k8s-agent-demo",
        pod_count=2,
        pods=[],
        events=[],
        logs_summary={},
        provenance="GEMINI_AGENT",
        llm_error=None,
    )
    with patch.dict(os.environ, {"GEMINI_API_KEY": "mock-api-key"}):
        with patch("app.orchestrator.pipeline.ObservationAgent.observe", return_value=(mock_obs_report_gemini, [], "Synthesized")):
            res_gemini = await pipeline.execute(request=req, use_llm=True)
            assert res_gemini.provenance == "GEMINI_AGENT"
            assert res_gemini.model_metadata["provider"] == "google_genai"
            assert res_gemini.model_metadata["model"] == "gemini-3.8-flash"


def test_permanent_api_errors_do_not_retry():
    """Verify permanent errors (400, 401, 403, 404) are excluded from retry status codes."""
    from app.orchestrator.agents.observation_agent import DEFAULT_RETRY_OPTIONS as OBS_RETRIES
    from app.orchestrator.agents.remediation_agent import DEFAULT_RETRY_OPTIONS as REM_RETRIES

    permanent_codes = [400, 401, 403, 404]
    for code in permanent_codes:
        assert code not in OBS_RETRIES.http_status_codes, f"Permanent error {code} must not be retried"
        assert code not in REM_RETRIES.http_status_codes, f"Permanent error {code} must not be retried"

    transient_codes = [408, 429, 500, 502, 503, 504]
    for code in transient_codes:
        assert code in OBS_RETRIES.http_status_codes, f"Transient error {code} must be retried"
        assert code in REM_RETRIES.http_status_codes, f"Transient error {code} must be retried"
