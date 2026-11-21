import sys
from typing import Any, Dict, Optional

try:
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:
    from mcp.server.fastmcp import FastMCP

from app.mcp_server.common import logger, set_default_k8s_client
from app.mcp_server.tools.cluster_tools import handle_get_events, handle_scan_cluster
from app.mcp_server.tools.diagnosis_tools import handle_run_diagnosis
from app.mcp_server.tools.pod_tools import (
    handle_get_pod_details,
    handle_get_pod_logs,
    handle_get_resource_config,
)
from app.mcp_server.tools.storage_tools import handle_get_pvc_details


def create_mcp_server(v1: Optional[Any] = None) -> FastMCP:
    """
    Factory function to instantiate and configure the custom Kubernetes FastMCP server.
    Registers all seven read-only operations and binds the optional Kubernetes client.
    """
    if v1 is not None:
        set_default_k8s_client(v1)

    server = FastMCP(
        name="kubernetes-mcp-server",
        instructions=(
            "Read-only Kubernetes Operations and Incident Diagnosis MCP Server. "
            "Enables safe cluster inspection, Pod telemetry analysis, event retrieval, "
            "and deterministic failure diagnosis. Write, mutate, and delete operations "
            "are strictly prohibited."
        ),
    )

    # -------------------------------------------------------------------------
    # Tool 1: scan_cluster
    # -------------------------------------------------------------------------
    @server.tool(
        name="scan_cluster",
        description=(
            "Collect comprehensive structured telemetry and evidence from the target Kubernetes namespace. "
            "Returns pod count, container states, restart counts, resource specs, probes, and associated events."
        ),
    )
    def scan_cluster(namespace: str = "k8s-agent-demo") -> Dict[str, Any]:
        return handle_scan_cluster(namespace=namespace, v1=v1)

    # -------------------------------------------------------------------------
    # Tool 2: get_pod_details
    # -------------------------------------------------------------------------
    @server.tool(
        name="get_pod_details",
        description=(
            "Retrieve detailed runtime status, conditions, container telemetry, and probe configs "
            "for a specific Pod in the namespace. Reuses structured evidence."
        ),
    )
    def get_pod_details(pod_name: str, namespace: str = "k8s-agent-demo") -> Dict[str, Any]:
        return handle_get_pod_details(pod_name=pod_name, namespace=namespace, v1=v1)

    # -------------------------------------------------------------------------
    # Tool 3: get_pod_logs
    # -------------------------------------------------------------------------
    @server.tool(
        name="get_pod_logs",
        description=(
            "Retrieve application logs from a Pod container. Supports current and previous executions, "
            "tail limits, and gracefully handles containers that have never started or missing previous logs."
        ),
    )
    def get_pod_logs(
        pod_name: str,
        namespace: str = "k8s-agent-demo",
        previous: bool = False,
        container_name: Optional[str] = None,
        tail_lines: int = 100,
    ) -> Dict[str, Any]:
        return handle_get_pod_logs(
            pod_name=pod_name,
            namespace=namespace,
            previous=previous,
            container_name=container_name,
            tail_lines=tail_lines,
            v1=v1,
        )

    # -------------------------------------------------------------------------
    # Tool 4: get_events
    # -------------------------------------------------------------------------
    @server.tool(
        name="get_events",
        description=(
            "Retrieve Kubernetes warning and informational events in the namespace, optionally filtered "
            "by a specific Pod name."
        ),
    )
    def get_events(
        namespace: str = "k8s-agent-demo",
        pod_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        return handle_get_events(namespace=namespace, pod_name=pod_name, v1=v1)

    # -------------------------------------------------------------------------
    # Tool 5: run_diagnosis
    # -------------------------------------------------------------------------
    @server.tool(
        name="run_diagnosis",
        description=(
            "Execute the deterministic incident diagnosis engine against fresh cluster evidence. "
            "Accurately classifies failure modes (ImagePullBackOff, OOMKilled, CrashLoopBackOff, "
            "FailedScheduling, Probe Failures, Missing ConfigMap/Secret, Unbound PVC, Invalid Command)."
        ),
    )
    def run_diagnosis(namespace: str = "k8s-agent-demo") -> Dict[str, Any]:
        return handle_run_diagnosis(namespace=namespace, v1=v1)

    # -------------------------------------------------------------------------
    # Tool 6: get_pvc_details
    # -------------------------------------------------------------------------
    @server.tool(
        name="get_pvc_details",
        description=(
            "Inspect PersistentVolumeClaims (PVCs), requested StorageClass, binding phase, "
            "provisioning events, and correlate referencing Pods."
        ),
    )
    def get_pvc_details(pvc_name: str, namespace: str = "k8s-agent-demo") -> Dict[str, Any]:
        return handle_get_pvc_details(pvc_name=pvc_name, namespace=namespace, v1=v1)

    # -------------------------------------------------------------------------
    # Tool 7: get_resource_config
    # -------------------------------------------------------------------------
    @server.tool(
        name="get_resource_config",
        description=(
            "Inspect a workload's resource configuration: container images, CPU/memory requests and limits, "
            "commands, arguments, probes, volumes, and referenced ConfigMap/Secret names (values are never exposed)."
        ),
    )
    def get_resource_config(pod_name: str, namespace: str = "k8s-agent-demo") -> Dict[str, Any]:
        return handle_get_resource_config(pod_name=pod_name, namespace=namespace, v1=v1)

    return server


# Default singleton instance
server = create_mcp_server()


def main() -> None:
    """Entry point to launch the Kubernetes MCP Server using STDIO transport."""
    logger.info("Initializing Kubernetes MCP Server with STDIO transport...")
    srv = create_mcp_server()
    srv.run(transport="stdio")


if __name__ == "__main__":
    main()
