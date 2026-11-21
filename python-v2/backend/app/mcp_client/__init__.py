"""
Google ADK MCP Client Bridge Module.

Provides an asynchronous bridge between the Google Agent Development Kit (ADK)
and our custom Kubernetes MCP Server using McpToolset, StdioConnectionParams,
and StdioServerParameters over STDIO.
"""

from app.mcp_client.client import (
    DEFAULT_TIMEOUT_SECONDS,
    EXPECTED_K8S_MCP_TOOLS,
    K8sMcpClientBridge,
)

__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "EXPECTED_K8S_MCP_TOOLS",
    "K8sMcpClientBridge",
]
