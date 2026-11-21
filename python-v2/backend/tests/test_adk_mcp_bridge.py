"""
Unit Tests for Google ADK MCP Client Bridge and Data Models.

Verifies:
1. K8sMcpClientBridge initialization and parameter configuration.
2. Connection lifecycle (connect, context manager, close).
3. Tool discovery and filtering of the 7 expected Kubernetes tools.
4. Error handling on invalid subprocess command or connection failure.
5. Safe programmatic tool execution via call_tool.
6. InvestigationRequest and InvestigationResult validation.
7. RemediationProposal, YamlPatch, RiskLevel, and VerificationPlan validation.
"""

import asyncio
from datetime import datetime, timezone
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from pydantic import ValidationError

from app.mcp_client import (
    DEFAULT_TIMEOUT_SECONDS,
    EXPECTED_K8S_MCP_TOOLS,
    K8sMcpClientBridge,
)
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
    RemediationProposal,
    RiskLevel,
    VerificationPlan,
    YamlPatch,
)


# ============================================================================
# Bridge Lifecycle & Configuration Tests
# ============================================================================

def test_bridge_initialization_defaults():
    """Verify default initialization parameters of K8sMcpClientBridge."""
    bridge = K8sMcpClientBridge()
    assert bridge.timeout == DEFAULT_TIMEOUT_SECONDS
    assert bridge.python_executable == sys.executable
    assert os.path.isdir(bridge.backend_dir)
    assert not bridge.is_connected
    assert bridge.tool_filter is None

    with pytest.raises(RuntimeError, match="not connected"):
        _ = bridge.toolset


def test_bridge_initialization_custom():
    """Verify custom parameters for K8sMcpClientBridge."""
    custom_dir = "/tmp"
    bridge = K8sMcpClientBridge(
        backend_dir=custom_dir,
        timeout=15.0,
        python_executable="/usr/bin/python3",
        tool_filter=["scan_cluster", "get_pod_details"],
    )
    assert bridge.backend_dir == custom_dir
    assert bridge.timeout == 15.0
    assert bridge.python_executable == "/usr/bin/python3"
    assert bridge.tool_filter == ["scan_cluster", "get_pod_details"]


@pytest.mark.anyio
async def test_bridge_connect_and_tool_discovery():
    """Verify bridge connects and discovers all 7 tools from the custom MCP server."""
    bridge = K8sMcpClientBridge(timeout=30.0)
    try:
        toolset = await bridge.connect()
        assert bridge.is_connected
        assert toolset is not None

        tools = await bridge.get_tools()
        assert len(tools) == len(EXPECTED_K8S_MCP_TOOLS)

        tool_names = await bridge.get_tool_names()
        for expected in EXPECTED_K8S_MCP_TOOLS:
            assert expected in tool_names
    finally:
        await bridge.close()
        assert not bridge.is_connected


@pytest.mark.anyio
async def test_bridge_context_manager():
    """Verify bridge async context manager connects and closes properly."""
    async with K8sMcpClientBridge(timeout=30.0) as bridge:
        assert bridge.is_connected
        tools = await bridge.get_tools()
        assert len(tools) >= 7

    assert not bridge.is_connected


@pytest.mark.anyio
async def test_bridge_tool_filtering():
    """Verify bridge respects tool_filter to discover only a subset of tools."""
    desired_tools = ["scan_cluster", "run_diagnosis"]
    async with K8sMcpClientBridge(tool_filter=desired_tools, timeout=30.0) as bridge:
        assert bridge.is_connected
        tools = await bridge.get_tools()
        assert len(tools) == 2
        tool_names = [t.name for t in tools]
        assert set(tool_names) == set(desired_tools)


@pytest.mark.anyio
async def test_bridge_connection_failure():
    """Verify bridge raises ConnectionError when subprocess binary fails."""
    bridge = K8sMcpClientBridge(
        python_executable="/nonexistent/binary/python",
        timeout=2.0,
    )
    with pytest.raises(ConnectionError, match="Failed to initialize Kubernetes MCP client bridge"):
        await bridge.connect()

    assert not bridge.is_connected


@pytest.mark.anyio
async def test_bridge_call_tool_unknown():
    """Verify call_tool raises ValueError for non-existent tool name."""
    async with K8sMcpClientBridge(timeout=30.0) as bridge:
        with pytest.raises(ValueError, match="Tool 'nonexistent_tool' not found"):
            await bridge.call_tool("nonexistent_tool", {})


@pytest.mark.anyio
async def test_bridge_call_tool_mocked_success():
    """Verify call_tool parses valid JSON payload from an MCP tool."""
    bridge = K8sMcpClientBridge()
    mock_tool = MagicMock()
    mock_tool.name = "scan_cluster"

    # Mock tool._create_session and _call_tool_on_session
    mock_session = MagicMock()
    mock_tool._create_session = AsyncMock(return_value=mock_session)

    mock_content_item = MagicMock()
    mock_content_item.text = '{"success": true, "tool": "scan_cluster", "namespace": "k8s-agent-demo"}'
    mock_result = MagicMock()
    mock_result.content = [mock_content_item]
    mock_tool._call_tool_on_session = AsyncMock(return_value=mock_result)

    bridge._tools = [mock_tool]
    bridge._is_connected = True
    bridge._toolset = MagicMock()

    res = await bridge.call_tool("scan_cluster", {"namespace": "k8s-agent-demo"})
    assert res["success"] is True
    assert res["tool"] == "scan_cluster"
    assert res["namespace"] == "k8s-agent-demo"


# ============================================================================
# Investigation Data Model Tests
# ============================================================================

def test_investigation_request_defaults():
    """Verify InvestigationRequest default values and valid namespace."""
    req = InvestigationRequest()
    assert req.namespace == "k8s-agent-demo"
    assert req.pod_name is None
    assert req.include_logs is True
    assert req.include_events is True


def test_investigation_request_custom():
    """Verify InvestigationRequest custom valid namespace and pod."""
    req = InvestigationRequest(
        namespace="prod-workloads",
        pod_name="web-service-1234",
        include_logs=False,
    )
    assert req.namespace == "prod-workloads"
    assert req.pod_name == "web-service-1234"
    assert req.include_logs is False


def test_investigation_request_invalid_namespace():
    """Verify InvestigationRequest rejects invalid DNS-1123 namespaces."""
    invalid_namespaces = [
        "UPPERCASE",
        "has_underscore",
        "-leading-hyphen",
        "trailing-hyphen-",
        "has space",
        "a" * 64,  # Exceeds 63 chars
        "",
    ]
    for ns in invalid_namespaces:
        with pytest.raises(ValidationError):
            InvestigationRequest(namespace=ns)


def test_investigation_result_lifecycle():
    """Verify InvestigationResult state progression and trace collection."""
    res = InvestigationResult(
        namespace="k8s-agent-demo",
        target_pod="crashloop-app",
        status=InvestigationStatus.OBSERVING,
    )
    assert res.status == InvestigationStatus.OBSERVING
    assert res.target_pod == "crashloop-app"
    assert len(res.tool_traces) == 0

    # Add a tool trace
    trace = AgentToolCallTrace(
        agent_name="telemetry_collector",
        tool_name="get_pod_details",
        arguments={"namespace": "k8s-agent-demo", "pod_name": "crashloop-app"},
        status="SUCCESS",
        duration_ms=45.2,
        result_summary="Pod in CrashLoopBackOff",
    )
    res.tool_traces.append(trace)
    assert len(res.tool_traces) == 1
    assert res.tool_traces[0].agent_name == "telemetry_collector"

    # Transition to COMPLETED
    res.status = InvestigationStatus.COMPLETED
    res.completed_at = datetime.now(timezone.utc).isoformat()
    assert res.status == InvestigationStatus.COMPLETED
    assert res.completed_at is not None


def test_observation_report_model():
    """Verify ObservationReport structure and serialization."""
    obs = ObservationReport(
        namespace="k8s-agent-demo",
        pod_count=2,
        pods=[{"pod": "app-1", "phase": "Running"}, {"pod": "app-2", "phase": "Pending"}],
        events=[{"type": "Warning", "reason": "FailedScheduling"}],
        logs_summary={"app-1": "healthy"},
    )
    dumped = obs.model_dump()
    assert dumped["namespace"] == "k8s-agent-demo"
    assert dumped["pod_count"] == 2
    assert len(dumped["pods"]) == 2
    assert len(dumped["events"]) == 1


# ============================================================================
# Remediation Data Model Tests
# ============================================================================

def test_remediation_proposal_valid():
    """Verify construction of a complete, valid RemediationProposal."""
    patch = YamlPatch(
        target_kind="Pod",
        target_name="oom-app",
        target_namespace="k8s-agent-demo",
        diff="--- resources.limits.memory: 100Mi\n+++ resources.limits.memory: 512Mi",
        patch_content={"spec": {"containers": [{"name": "app", "resources": {"limits": {"memory": "512Mi"}}}]}},
    )
    plan = VerificationPlan(
        target_phase="Running",
        expected_conditions=[{"type": "Ready", "status": "True"}],
        negative_assertions=["OOMKilled", "CrashLoopBackOff"],
        check_interval_seconds=5,
        timeout_seconds=60,
    )
    approval = HumanApproval(
        status=ApprovalStatus.PENDING,
        reviewer=None,
    )

    prop = RemediationProposal(
        target_workload="oom-app",
        target_kind="Pod",
        target_namespace="k8s-agent-demo",
        patch=patch,
        justification="Container terminated due to OOMKilled (exit code 137). Memory limit needs to be increased.",
        risk_level=RiskLevel.MEDIUM,
        requires_approval=True,
        approval=approval,
        verification_plan=plan,
    )

    assert prop.target_workload == "oom-app"
    assert prop.risk_level == RiskLevel.MEDIUM
    assert prop.requires_approval is True
    assert prop.approval.status == ApprovalStatus.PENDING
    assert prop.verification_plan.timeout_seconds == 60


def test_remediation_proposal_empty_fields_rejected():
    """Verify RemediationProposal rejects empty target_workload or justification."""
    patch = YamlPatch(
        target_kind="Pod",
        target_name="test-app",
        diff="test diff",
    )
    with pytest.raises(ValidationError):
        RemediationProposal(
            target_workload="",  # Empty
            patch=patch,
            justification="Valid justification",
        )

    with pytest.raises(ValidationError):
        RemediationProposal(
            target_workload="test-app",
            patch=patch,
            justification="  ",  # Whitespace only
        )


def test_yaml_patch_empty_fields_rejected():
    """Verify YamlPatch rejects empty target_kind, target_name, or diff."""
    with pytest.raises(ValidationError):
        YamlPatch(target_kind="", target_name="app", diff="diff")

    with pytest.raises(ValidationError):
        YamlPatch(target_kind="Pod", target_name="  ", diff="diff")

    with pytest.raises(ValidationError):
        YamlPatch(target_kind="Pod", target_name="app", diff="")


def test_risk_level_validation():
    """Verify RiskLevel enum values and rejection of invalid values."""
    assert RiskLevel.LOW.value == "LOW"
    assert RiskLevel.MEDIUM.value == "MEDIUM"
    assert RiskLevel.HIGH.value == "HIGH"

    patch = YamlPatch(target_kind="Pod", target_name="test", diff="diff")
    with pytest.raises(ValidationError):
        RemediationProposal(
            target_workload="test",
            patch=patch,
            justification="reason",
            risk_level="EXTREME",  # Invalid
        )


def test_verification_plan_bounds():
    """Verify VerificationPlan interval and timeout validation constraints."""
    # Valid bounds
    plan = VerificationPlan(check_interval_seconds=10, timeout_seconds=120)
    assert plan.check_interval_seconds == 10
    assert plan.timeout_seconds == 120

    # Interval too small (< 1)
    with pytest.raises(ValidationError):
        VerificationPlan(check_interval_seconds=0)

    # Interval too large (> 60)
    with pytest.raises(ValidationError):
        VerificationPlan(check_interval_seconds=61)

    # Timeout too small (< 5)
    with pytest.raises(ValidationError):
        VerificationPlan(timeout_seconds=2)

    # Timeout too large (> 300)
    with pytest.raises(ValidationError):
        VerificationPlan(timeout_seconds=301)
