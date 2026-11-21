"""
Google ADK MCP Client Bridge.

Provides a clean asynchronous bridge between Google Agent Development Kit (ADK)
and our custom Kubernetes MCP Server using McpToolset, StdioConnectionParams,
and StdioServerParameters over STDIO.
"""

import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
from mcp import StdioServerParameters

logger = logging.getLogger(__name__)

# Expected seven read-only Kubernetes MCP tools
EXPECTED_K8S_MCP_TOOLS = [
    "scan_cluster",
    "get_pod_details",
    "get_pod_logs",
    "get_events",
    "run_diagnosis",
    "get_pvc_details",
    "get_resource_config",
]

DEFAULT_TIMEOUT_SECONDS = 30.0


class K8sMcpClientBridge:
    """
    Manages the lifecycle of the Google ADK McpToolset bridge to the custom
    Kubernetes MCP Server running as a subprocess over STDIO.
    """

    def __init__(
        self,
        backend_dir: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        python_executable: Optional[str] = None,
        tool_filter: Optional[List[str]] = None,
    ):
        if backend_dir is None:
            # Auto-locate python-v2/backend directory from file location
            current_dir = os.path.dirname(os.path.abspath(__file__))
            backend_dir = os.path.abspath(os.path.join(current_dir, "..", ".."))

        self.backend_dir = os.path.abspath(backend_dir)
        self.timeout = float(timeout)
        self.python_executable = python_executable or sys.executable
        self.tool_filter = tool_filter

        self._toolset: Optional[McpToolset] = None
        self._tools: Optional[List[Any]] = None
        self._is_connected: bool = False

    @property
    def is_connected(self) -> bool:
        """Check if the bridge is currently connected to the MCP server."""
        return self._is_connected

    @property
    def toolset(self) -> McpToolset:
        """Return the underlying ADK McpToolset instance."""
        if not self._toolset or not self._is_connected:
            raise RuntimeError("MCP client bridge is not connected. Call connect() first.")
        return self._toolset

    async def connect(self) -> McpToolset:
        """
        Launch the Kubernetes MCP server as a subprocess and initialize the ADK McpToolset.

        Returns:
            The initialized McpToolset instance.

        Raises:
            ConnectionError: If server startup or tool discovery fails.
        """
        if self._is_connected and self._toolset is not None:
            return self._toolset

        logger.info(
            "Connecting to Kubernetes MCP server via ADK McpToolset (cwd=%s, timeout=%s)",
            self.backend_dir,
            self.timeout,
        )

        try:
            env = dict(os.environ)
            env["PYTHONPATH"] = self.backend_dir

            server_params = StdioServerParameters(
                command=self.python_executable,
                args=["-m", "app.mcp_server.server"],
                cwd=self.backend_dir,
                env=env,
            )

            conn_params = StdioConnectionParams(
                server_params=server_params,
                timeout=self.timeout,
            )

            self._toolset = McpToolset(
                connection_params=conn_params,
                tool_filter=self.tool_filter,
            )

            # Discover and cache tools to verify initialization
            self._tools = await self._toolset.get_tools()
            self._is_connected = True
            logger.info(
                "Successfully connected to Kubernetes MCP server. Discovered %d tools.",
                len(self._tools),
            )
            return self._toolset

        except Exception as exc:
            logger.error("Failed to connect to Kubernetes MCP server via ADK: %s", exc)
            await self.close()
            raise ConnectionError(
                f"Failed to initialize Kubernetes MCP client bridge: {exc}"
            ) from exc

    async def get_tools(self) -> List[Any]:
        """
        Return the list of Google ADK McpTool objects discovered from the MCP server.

        These tools can be passed directly to ADK Agents (e.g., LlmAgent(tools=...)).
        """
        if not self._is_connected or self._toolset is None:
            await self.connect()
        return self._tools or []

    async def get_tool_names(self) -> List[str]:
        """Return the names of all discovered MCP tools."""
        tools = await self.get_tools()
        return [t.name for t in tools]

    async def call_tool(
        self, tool_name: str, arguments: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Programmatically invoke a tool by name over the MCP connection and return
        the parsed JSON response dict.

        Args:
            tool_name: The name of the registered MCP tool to execute.
            arguments: Dictionary of arguments matching the tool's input schema.

        Returns:
            Dictionary containing the tool execution result.
        """
        tools = await self.get_tools()
        matching_tools = [t for t in tools if t.name == tool_name]
        if not matching_tools:
            available = [t.name for t in tools]
            raise ValueError(
                f"Tool '{tool_name}' not found. Available tools: {available}"
            )

        tool = matching_tools[0]
        arguments = arguments or {}

        try:
            session = await tool._create_session(headers=None)
            mcp_result = await tool._call_tool_on_session(
                session, None, arguments, None, None
            )

            # Parse result content
            if not mcp_result or not getattr(mcp_result, "content", None):
                return {
                    "success": False,
                    "tool": tool_name,
                    "error": f"Tool '{tool_name}' returned empty result content",
                }

            first_item = mcp_result.content[0]
            raw_text = getattr(first_item, "text", "")
            if not raw_text:
                return {
                    "success": False,
                    "tool": tool_name,
                    "error": f"Tool '{tool_name}' returned empty text payload",
                }

            try:
                return json.loads(raw_text)
            except json.JSONDecodeError:
                return {
                    "success": True,
                    "tool": tool_name,
                    "raw_text": raw_text,
                }

        except Exception as exc:
            logger.error("Error executing tool '%s': %s", tool_name, exc)
            return {
                "success": False,
                "tool": tool_name,
                "error": str(exc),
            }

    async def close(self) -> None:
        """Close the ADK McpToolset and terminate subprocess resources cleanly."""
        if self._toolset is not None:
            try:
                await self._toolset.close()
            except Exception as exc:
                logger.warning("Error while closing McpToolset: %s", exc)
            finally:
                self._toolset = None
                self._tools = None
                self._is_connected = False
                logger.info("Kubernetes MCP client bridge closed successfully.")

    async def __aenter__(self) -> "K8sMcpClientBridge":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()
