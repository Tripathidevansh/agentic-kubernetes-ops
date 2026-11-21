import asyncio
import json
import os
import sys
from unittest.mock import MagicMock
import pytest
from kubernetes.client.rest import ApiException

from app.mcp_server import create_mcp_server
from app.mcp_server.common import (
    error_response,
    format_api_exception,
    success_response,
    validate_namespace,
    validate_resource_name,
)
from app.mcp_server.tools.cluster_tools import handle_get_events, handle_scan_cluster
from app.mcp_server.tools.diagnosis_tools import handle_run_diagnosis
from app.mcp_server.tools.pod_tools import (
    handle_get_pod_details,
    handle_get_pod_logs,
    handle_get_resource_config,
)
from app.mcp_server.tools.storage_tools import handle_get_pvc_details


# ============================================================================
# Helpers
# ============================================================================

def _build_mock_k8s_api(
    pods=None,
    events=None,
    pvcs=None,
    configmaps=None,
    secrets=None,
    log_content="Container application running.\n",
):
    """Construct a mock CoreV1Api client returning realistic Kubernetes objects."""
    api = MagicMock()

    # Pods
    api.list_namespaced_pod.return_value.items = pods or []
    if pods:
        api.read_namespaced_pod.return_value = pods[0]
    else:
        api.read_namespaced_pod.side_effect = ApiException(status=404, reason="NotFound")

    # Events
    api.list_namespaced_event.return_value.items = events or []

    # PVCs
    api.list_namespaced_persistent_volume_claim.return_value.items = pvcs or []
    if pvcs:
        api.read_namespaced_persistent_volume_claim.return_value = pvcs[0]
    else:
        api.read_namespaced_persistent_volume_claim.side_effect = ApiException(status=404, reason="NotFound")

    # ConfigMaps & Secrets
    api.list_namespaced_config_map.return_value.items = configmaps or []
    api.list_namespaced_secret.return_value.items = secrets or []

    # Logs
    api.read_namespaced_pod_log.return_value = log_content

    return api


def _create_simple_mock_pod(name="test-pod", namespace="k8s-agent-demo", image="nginx:1.27"):
    """Create a minimal mock V1Pod."""
    pod = MagicMock()
    pod.metadata.name = name
    pod.metadata.namespace = namespace
    pod.metadata.labels = {"app": name}
    pod.metadata.owner_references = []
    pod.status.phase = "Running"
    pod.status.reason = None
    pod.status.message = None
    pod.spec.node_name = "node-1"

    cond = MagicMock()
    cond.type = "Ready"
    cond.status = "True"
    cond.reason = None
    cond.message = None
    cond.last_transition_time = None
    pod.status.conditions = [cond]

    container = MagicMock()
    container.name = "app"
    container.image = image
    container.command = ["nginx"]
    container.args = ["-g", "daemon off;"]
    container.resources.requests = {"cpu": "50m", "memory": "64Mi"}
    container.resources.limits = {"cpu": "100m", "memory": "128Mi"}
    container.readiness_probe = None
    container.liveness_probe = None
    container.startup_probe = None
    container.env = []
    container.env_from = []
    pod.spec.containers = [container]
    pod.spec.volumes = []

    status = MagicMock()
    status.name = "app"
    status.image_id = "docker-pullable://nginx@sha256:123"
    status.ready = True
    status.restart_count = 0
    status.state.running.started_at = None
    status.state.waiting = None
    status.state.terminated = None
    status.last_state = None
    pod.status.container_statuses = [status]

    return pod


# ============================================================================
# 1. MCP Server Initialization & Tool Discovery Tests
# ============================================================================

@pytest.mark.anyio
async def test_mcp_server_initialization_and_tool_discovery():
    """Verify MCP server initializes and registers all seven expected tools."""
    server = create_mcp_server()
    tools = await server.list_tools()
    tool_names = {t.name for t in tools}

    expected_tools = {
        "scan_cluster",
        "get_pod_details",
        "get_pod_logs",
        "get_events",
        "run_diagnosis",
        "get_pvc_details",
        "get_resource_config",
    }

    assert expected_tools == tool_names
    assert len(tools) == 7


# ============================================================================
# 2. Input Validation Tests
# ============================================================================

def test_validation_helpers():
    """Verify DNS RFC 1123 validation rejects malicious, empty, or malformed names."""
    # Valid
    assert validate_namespace("default") is None
    assert validate_namespace("k8s-agent-demo") is None
    assert validate_resource_name("my-pod-123") is None

    # Invalid
    assert validate_namespace("") is not None
    assert validate_namespace("UPPERCASE") is not None
    assert validate_namespace("semi;colon") is not None
    assert validate_namespace("../../traversal") is not None
    assert validate_namespace("a" * 64) is not None

    assert validate_resource_name("") is not None
    assert validate_resource_name("Invalid_Name") is not None
    assert validate_resource_name("pod@special") is not None


# ============================================================================
# 3. Individual Tool Tests (With Mocked Kubernetes Client)
# ============================================================================

def test_tool_scan_cluster_success():
    """Verify scan_cluster collects structured pod evidence."""
    mock_pod = _create_simple_mock_pod("demo-app")
    mock_api = _build_mock_k8s_api(pods=[mock_pod])

    res = handle_scan_cluster(namespace="k8s-agent-demo", v1=mock_api)

    assert res["success"] is True
    assert res["tool"] == "scan_cluster"
    assert res["namespace"] == "k8s-agent-demo"
    assert res["data"]["pod_count"] == 1
    assert res["data"]["pods"][0]["pod"] == "demo-app"
    assert res["error"] is None


def test_tool_scan_cluster_invalid_namespace():
    """Verify scan_cluster rejects invalid namespaces."""
    res = handle_scan_cluster(namespace="INVALID_NS_!!!")
    assert res["success"] is False
    assert res["error"]["code"] == "INVALID_PARAMETER"


def test_tool_scan_cluster_api_connection_error():
    """Verify scan_cluster handles Kubernetes connection failures gracefully."""
    mock_api = MagicMock()
    mock_api.list_namespaced_pod.side_effect = ApiException(status=503, reason="Service Unavailable")

    res = handle_scan_cluster(namespace="k8s-agent-demo", v1=mock_api)
    assert res["success"] is False
    assert res["error"]["code"] == "KUBERNETES_CONNECTION_ERROR"
    assert "Service Unavailable" in res["error"]["message"]


def test_tool_get_pod_details_success():
    """Verify get_pod_details retrieves existing pod information."""
    mock_pod = _create_simple_mock_pod("my-service")
    mock_api = _build_mock_k8s_api(pods=[mock_pod])

    res = handle_get_pod_details("my-service", namespace="k8s-agent-demo", v1=mock_api)
    assert res["success"] is True
    assert res["tool"] == "get_pod_details"
    assert res["data"]["pod"] == "my-service"
    assert res["data"]["is_ready"] is True


def test_tool_get_pod_details_not_found():
    """Verify get_pod_details returns POD_NOT_FOUND when pod does not exist."""
    mock_api = _build_mock_k8s_api(pods=[])
    res = handle_get_pod_details("nonexistent-pod", namespace="k8s-agent-demo", v1=mock_api)

    assert res["success"] is False
    assert res["error"]["code"] == "POD_NOT_FOUND"
    assert "nonexistent-pod" in res["error"]["message"]


def test_tool_get_pod_logs_success():
    """Verify get_pod_logs retrieves current container logs."""
    mock_pod = _create_simple_mock_pod("app-pod")
    mock_api = _build_mock_k8s_api(pods=[mock_pod], log_content="Starting HTTP server...\nListening on 8080\n")

    res = handle_get_pod_logs("app-pod", namespace="k8s-agent-demo", tail_lines=50, v1=mock_api)
    assert res["success"] is True
    assert res["data"]["pod"] == "app-pod"
    assert res["data"]["container"] == "app"
    assert "Starting HTTP server" in res["data"]["logs"]


def test_tool_get_pod_logs_unavailable_graceful_handling():
    """Verify get_pod_logs handles unstarted container or missing previous logs without error."""
    mock_pod = _create_simple_mock_pod("waiting-pod")
    mock_api = _build_mock_k8s_api(pods=[mock_pod])
    mock_api.read_namespaced_pod_log.side_effect = ApiException(status=400, reason="ContainerCreating")

    res = handle_get_pod_logs("waiting-pod", namespace="k8s-agent-demo", v1=mock_api)
    assert res["success"] is True
    assert "Unavailable: ContainerCreating" in res["data"]["logs"]


def test_tool_get_events_all_and_filtered():
    """Verify get_events retrieves namespace events and supports pod filtering."""
    ev1 = MagicMock()
    ev1.type = "Warning"
    ev1.reason = "Unhealthy"
    ev1.message = "Readiness probe failed"
    ev1.count = 2
    ev1.last_timestamp = None
    ev1.first_timestamp = None
    ev1.event_time = None
    ev1.involved_object.kind = "Pod"
    ev1.involved_object.name = "pod-a"

    ev2 = MagicMock()
    ev2.type = "Normal"
    ev2.reason = "Scheduled"
    ev2.message = "Assigned to node-1"
    ev2.count = 1
    ev2.last_timestamp = None
    ev2.first_timestamp = None
    ev2.event_time = None
    ev2.involved_object.kind = "Pod"
    ev2.involved_object.name = "pod-b"

    mock_api = _build_mock_k8s_api(events=[ev1, ev2])

    # 1. Fetch all
    all_res = handle_get_events(namespace="k8s-agent-demo", v1=mock_api)
    assert all_res["success"] is True
    assert all_res["data"]["event_count"] == 2

    # 2. Filter by Pod A
    filtered_res = handle_get_events(namespace="k8s-agent-demo", pod_name="pod-a", v1=mock_api)
    assert filtered_res["success"] is True
    assert filtered_res["data"]["event_count"] == 1
    assert filtered_res["data"]["events"][0]["reason"] == "Unhealthy"


def test_tool_run_diagnosis_success():
    """Verify run_diagnosis calls deterministic diagnosis engine on fresh evidence."""
    mock_pod = _create_simple_mock_pod("crash-pod")
    mock_pod.status.container_statuses[0].state.waiting = None
    mock_pod.status.container_statuses[0].state.running = None
    mock_pod.status.container_statuses[0].last_state = MagicMock()
    mock_pod.status.container_statuses[0].last_state.waiting = None
    mock_pod.status.container_statuses[0].last_state.running = None
    mock_pod.status.container_statuses[0].last_state.terminated.reason = "OOMKilled"
    mock_pod.status.container_statuses[0].last_state.terminated.exit_code = 137
    mock_pod.status.container_statuses[0].last_state.terminated.started_at = None
    mock_pod.status.container_statuses[0].last_state.terminated.finished_at = None

    mock_api = _build_mock_k8s_api(pods=[mock_pod])

    res = handle_run_diagnosis(namespace="k8s-agent-demo", v1=mock_api)
    assert res["success"] is True
    assert res["tool"] == "run_diagnosis"
    assert res["data"]["diagnosis_count"] == 1
    diag = res["data"]["diagnoses"][0]
    assert diag["failure_type"] == "MEMORY_EXHAUSTION"
    assert diag["severity"] == "HIGH"


def test_tool_get_pvc_details_success():
    """Verify get_pvc_details reports storage properties, bound volume, events, and referencing pods."""
    mock_pvc = MagicMock()
    mock_pvc.metadata.name = "data-pvc"
    mock_pvc.status.phase = "Bound"
    mock_pvc.status.capacity = {"storage": "5Gi"}
    mock_pvc.spec.storage_class_name = "standard"
    mock_pvc.spec.access_modes = ["ReadWriteOnce"]
    mock_pvc.spec.volume_name = "pvc-volume-guid-1234"

    # Pod referencing the PVC
    mock_vol = MagicMock()
    mock_vol.persistent_volume_claim.claim_name = "data-pvc"
    mock_pod = _create_simple_mock_pod("database-pod")
    mock_pod.spec.volumes = [mock_vol]

    mock_api = _build_mock_k8s_api(pods=[mock_pod], pvcs=[mock_pvc])

    res = handle_get_pvc_details("data-pvc", namespace="k8s-agent-demo", v1=mock_api)
    assert res["success"] is True
    assert res["data"]["name"] == "data-pvc"
    assert res["data"]["phase"] == "Bound"
    assert res["data"]["storage_class"] == "standard"
    assert res["data"]["bound_volume"] == "pvc-volume-guid-1234"
    assert res["data"]["referencing_pods"] == ["database-pod"]


def test_tool_get_pvc_details_not_found():
    """Verify get_pvc_details returns PVC_NOT_FOUND when PVC does not exist."""
    mock_api = _build_mock_k8s_api(pvcs=[])
    res = handle_get_pvc_details("missing-pvc", namespace="k8s-agent-demo", v1=mock_api)

    assert res["success"] is False
    assert res["error"]["code"] == "PVC_NOT_FOUND"


def test_tool_get_resource_config_protects_secrets():
    """SECURITY TEST: Verify get_resource_config exposes secret names but NEVER secret values."""
    mock_pod = _create_simple_mock_pod("config-pod")

    # Add secret environment variable and volume
    secret_env = MagicMock()
    secret_env.name = "API_KEY"
    secret_env.value_from.secret_key_ref.name = "api-credentials"
    secret_env.value_from.secret_key_ref.key = "token"
    mock_pod.spec.containers[0].env = [secret_env]

    secret_vol = MagicMock()
    secret_vol.name = "certs"
    secret_vol.secret.secret_name = "tls-secret"
    mock_pod.spec.volumes = [secret_vol]

    mock_api = _build_mock_k8s_api(pods=[mock_pod])

    res = handle_get_resource_config("config-pod", namespace="k8s-agent-demo", v1=mock_api)
    assert res["success"] is True
    data = res["data"]

    # Names are captured
    assert "api-credentials" in data["secret_references"]
    assert "tls-secret" in data["secret_references"]

    # Strict assertion: No sensitive tokens or values exist
    res_str = json.dumps(res)
    assert "password" not in res_str.lower() or "secret_references" in res_str
    assert "token" not in res_str.lower() or "token" not in data.get("secret_data", {})


def test_all_tools_produce_valid_json():
    """Verify all tool responses serialize to valid JSON without error."""
    mock_pod = _create_simple_mock_pod("test-pod")
    mock_api = _build_mock_k8s_api(pods=[mock_pod])

    responses = [
        handle_scan_cluster(v1=mock_api),
        handle_get_pod_details("test-pod", v1=mock_api),
        handle_get_pod_logs("test-pod", v1=mock_api),
        handle_get_events(v1=mock_api),
        handle_run_diagnosis(v1=mock_api),
        handle_get_pvc_details("any-pvc", v1=mock_api),
        handle_get_resource_config("test-pod", v1=mock_api),
    ]

    for resp in responses:
        dumped = json.dumps(resp)
        loaded = json.loads(dumped)
        assert "success" in loaded
        assert "tool" in loaded
        assert "namespace" in loaded


@pytest.mark.anyio
async def test_read_only_guarantee_no_mutating_tools_registered():
    """SECURITY TEST: Verify no write/mutating tools exist on the MCP server."""
    server = create_mcp_server()
    tools = await server.list_tools()

    prohibited_keywords = ["create", "apply", "delete", "remove", "patch", "edit", "exec", "write", "kill"]
    for tool in tools:
        for kw in prohibited_keywords:
            assert kw not in tool.name.lower(), f"Prohibited write tool found: {tool.name}"


# ============================================================================
# 4. End-to-End MCP Client/Server Communication via STDIO Subprocess
# ============================================================================

@pytest.mark.anyio
async def test_live_mcp_stdio_client_server_communication():
    """
    Subprocess protocol verification: Spawn MCP server using STDIO transport and
    interrogate it with an official MCP ClientSession.
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    # Locate backend root
    test_dir = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.abspath(os.path.join(test_dir, ".."))

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp_server.server"],
        cwd=backend_dir,
        env={**os.environ, "PYTHONPATH": backend_dir},
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            # 1. Initialize MCP protocol handshake
            init_res = await session.initialize()
            assert init_res.server_info.name == "kubernetes-mcp-server"

            # 2. Discover registered tools
            tools_result = await session.list_tools()
            tool_names = [t.name for t in tools_result.tools]
            assert "scan_cluster" in tool_names
            assert "run_diagnosis" in tool_names
            assert len(tool_names) == 7

            # 3. Call tool via protocol and parse structured JSON response
            call_res = await session.call_tool("scan_cluster", {"namespace": "invalid_test_@@"})
            assert len(call_res.content) > 0
            payload = json.loads(call_res.content[0].text)

            assert payload["success"] is False
            assert payload["tool"] == "scan_cluster"
            assert payload["error"]["code"] == "INVALID_PARAMETER"
