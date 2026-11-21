#!/usr/bin/env python3
"""
Standalone Live MCP Integration Test.

Connects to the real Docker Desktop Kubernetes cluster in namespace 'k8s-agent-demo'
using the official MCP Python SDK (stdio_client, StdioServerParameters, ClientSession).
Launches the custom Kubernetes MCP server as a subprocess over STDIO, verifies tool
discovery, invokes 'scan_cluster' and 'run_diagnosis', and formats the live results.

Usage:
    cd python-v2/backend
    source .venv/bin/activate
    PYTHONPATH=. python tests/live_mcp_check.py
"""

import asyncio
import json
import os
import sys
from typing import Any, Dict, List

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Target Kubernetes namespace
TARGET_NAMESPACE = "k8s-agent-demo"

# Seven expected MCP tools
EXPECTED_TOOLS = [
    "scan_cluster",
    "get_pod_details",
    "get_pod_logs",
    "get_events",
    "run_diagnosis",
    "get_pvc_details",
    "get_resource_config",
]


def print_banner(title: str) -> None:
    print("\n" + "=" * 78)
    print(f" {title}")
    print("=" * 78)


def print_section(title: str) -> None:
    print("\n" + "-" * 78)
    print(f" -> {title}")
    print("-" * 78)


def parse_mcp_tool_result(result: Any, tool_name: str) -> Dict[str, Any]:
    """Extract and parse JSON payload from MCP CallToolResult content."""
    if not result or not getattr(result, "content", None):
        raise ValueError(f"Tool '{tool_name}' returned empty or malformed MCP response content.")

    first_content = result.content[0]
    raw_text = getattr(first_content, "text", "")
    if not raw_text:
        raise ValueError(f"Tool '{tool_name}' returned empty text payload.")

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Tool '{tool_name}' returned non-JSON text: {raw_text[:200]}...") from exc


async def run_live_mcp_check() -> int:
    """Execute live MCP integration test over STDIO."""
    print_banner("KUBERNETES MCP SERVER — LIVE INTEGRATION VERIFICATION")

    # Determine backend directory path
    test_dir = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.abspath(os.path.join(test_dir, ".."))

    python_executable = sys.executable
    print(f"[Config] Python Interpreter : {python_executable}")
    print(f"[Config] Backend Directory   : {backend_dir}")
    print(f"[Config] Target Namespace    : {TARGET_NAMESPACE}")
    print(f"[Config] Transport           : STDIO Subprocess")

    server_params = StdioServerParameters(
        command=python_executable,
        args=["-m", "app.mcp_server.server"],
        cwd=backend_dir,
        env={**os.environ, "PYTHONPATH": backend_dir},
    )

    try:
        async with stdio_client(server_params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:

                # ------------------------------------------------------------
                # 1. Initialize MCP Protocol Handshake
                # ------------------------------------------------------------
                print_section("Step 1: Protocol Handshake & Session Initialization")
                init_res = await session.initialize()
                server_name = getattr(init_res.server_info, "name", "unknown")
                server_version = getattr(init_res.server_info, "version", "unknown")
                print(f"[Success] Connected to MCP Server: '{server_name}' (version: {server_version})")

                # ------------------------------------------------------------
                # 2. List & Verify Registered Tools
                # ------------------------------------------------------------
                print_section("Step 2: MCP Tool Registration & Discovery")
                tools_response = await session.list_tools()
                discovered_tools = {t.name: t.description for t in tools_response.tools}

                print(f"[Info] Discovered {len(discovered_tools)} tool(s):")
                missing_tools: List[str] = []
                for expected in EXPECTED_TOOLS:
                    if expected in discovered_tools:
                        desc = discovered_tools[expected]
                        short_desc = desc[:70] + "..." if len(desc) > 70 else desc
                        print(f"  [x] {expected:<22} : {short_desc}")
                    else:
                        print(f"  [ ] {expected:<22} : MISSING!")
                        missing_tools.append(expected)

                if missing_tools:
                    print(f"\n[FAIL] Missing required MCP tools: {missing_tools}")
                    return 1

                print("\n[Success] All 7 expected MCP tools are registered and discoverable.")

                # ------------------------------------------------------------
                # 3. Call 'scan_cluster' against real namespace
                # ------------------------------------------------------------
                print_section(f"Step 3: Call 'scan_cluster' for '{TARGET_NAMESPACE}'")
                scan_raw = await session.call_tool("scan_cluster", {"namespace": TARGET_NAMESPACE})
                scan_payload = parse_mcp_tool_result(scan_raw, "scan_cluster")

                if not scan_payload.get("success"):
                    err = scan_payload.get("error", {})
                    print(f"[FAIL] scan_cluster returned error: {err.get('code')} - {err.get('message')}")
                    return 1

                cluster_data = scan_payload.get("data", {})
                pod_count = cluster_data.get("pod_count", 0)
                pods = cluster_data.get("pods", [])
                print(f"[Success] 'scan_cluster' succeeded! Total Pods discovered: {pod_count}")

                print("\nDiscovered Live Pods:")
                print(f"{'POD NAME':<34} {'PHASE':<10} {'READY':<7} {'RESTARTS':<10} {'NODE':<16}")
                print("-" * 78)
                for pod in pods:
                    pod_name = pod.get("pod", "unknown")
                    phase = pod.get("phase", "Unknown")
                    is_ready = "Yes" if pod.get("is_ready") else "No"
                    node = pod.get("node_name") or "None (Unscheduled)"

                    restarts = 0
                    for c in pod.get("containers", []):
                        restarts += c.get("restart_count", 0)

                    print(f"{pod_name:<34} {phase:<10} {is_ready:<7} {restarts:<10} {node:<16}")

                # ------------------------------------------------------------
                # 4. Call 'run_diagnosis' against real namespace
                # ------------------------------------------------------------
                print_section(f"Step 4: Call 'run_diagnosis' for '{TARGET_NAMESPACE}'")
                diag_raw = await session.call_tool("run_diagnosis", {"namespace": TARGET_NAMESPACE})
                diag_payload = parse_mcp_tool_result(diag_raw, "run_diagnosis")

                if not diag_payload.get("success"):
                    err = diag_payload.get("error", {})
                    print(f"[FAIL] run_diagnosis returned error: {err.get('code')} - {err.get('message')}")
                    return 1

                diag_data = diag_payload.get("data", {})
                diag_count = diag_data.get("diagnosis_count", 0)
                diagnoses = diag_data.get("diagnoses", [])
                print(f"[Success] 'run_diagnosis' succeeded! Total Diagnoses produced: {diag_count}")

                print("\nLive Incident Diagnoses:")
                detected_failure_types = set()

                for i, d in enumerate(diagnoses, 1):
                    f_type = d.get("failure_type", "UNKNOWN")
                    detected_failure_types.add(f_type)
                    sev = d.get("severity", "UNKNOWN")
                    res = d.get("resource_name", "unknown")
                    summary = d.get("summary", "No summary provided.")
                    root_cause = d.get("root_cause", "No root cause provided.")
                    evidence_list = d.get("evidence", [])
                    action = d.get("recommended_action", "No action specified.")

                    print(f"\n[{i:02d}] {f_type} (Severity: {sev})")
                    print(f"     Resource    : {res}")
                    print(f"     Summary     : {summary}")
                    print(f"     Root Cause  : {root_cause}")
                    if evidence_list:
                        first_ev = evidence_list[0].replace("\n", " ")
                        if len(first_ev) > 90:
                            first_ev = first_ev[:90] + "..."
                        print(f"     Key Evidence: {first_ev}")
                    print(f"     Action      : {action}")

                # ------------------------------------------------------------
                # 5. Verify Coverage of Verified Failure Scenarios
                # ------------------------------------------------------------
                print_section("Step 5: Failure Scenario Verification Coverage")
                print(f"[Coverage] Distinct failure types identified: {len(detected_failure_types)}")
                for f_type in sorted(detected_failure_types):
                    print(f"  [v] Detected: {f_type}")

                print_banner("LIVE MCP INTEGRATION VERIFICATION: ALL CHECKS PASSED")
                print(f"[Summary] Server Communication : OK (STDIO JSON-RPC)")
                print(f"[Summary] Tool Discovery       : OK (7/7 Tools)")
                print(f"[Summary] Cluster Scan         : OK ({pod_count} Pods)")
                print(f"[Summary] Diagnosis Engine     : OK ({diag_count} Diagnoses, {len(detected_failure_types)} Failure Modes)")
                return 0

    except Exception as exc:
        print(f"\n[FATAL ERROR] Live MCP verification failed with exception: {exc}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(run_live_mcp_check())
    sys.exit(exit_code)
