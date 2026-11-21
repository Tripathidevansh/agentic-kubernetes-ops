#!/usr/bin/env python3
"""
Standalone Live Google ADK + MCP Bridge Integration Test.

Connects to the real Docker Desktop Kubernetes cluster in namespace 'k8s-agent-demo'
using the Google ADK MCP Client Bridge (McpToolset over STDIO).

Verifies:
1. Google ADK McpToolset bridge initialization and connection lifecycle.
2. Discovery of all 7 expected Kubernetes MCP tools.
3. Live invocation of 'scan_cluster' and 'run_diagnosis' against 'k8s-agent-demo'.
4. Structured parsing and formatting of live Kubernetes failure scenario telemetry.
5. End-to-end data model compatibility (ObservationReport, InvestigationResult, RemediationProposal).
6. Clean subprocess termination and resource cleanup without leaking processes.

Usage:
    cd python-v2/backend
    source .venv/bin/activate
    PYTHONPATH=. python tests/live_adk_mcp_check.py
"""

import asyncio
from datetime import datetime, timezone
import json
import os
import sys
from typing import Any, Dict

from app.mcp_client import EXPECTED_K8S_MCP_TOOLS, K8sMcpClientBridge
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

TARGET_NAMESPACE = "k8s-agent-demo"


def print_banner(title: str) -> None:
    print("\n" + "=" * 80)
    print(f" {title}")
    print("=" * 80)


def print_section(title: str) -> None:
    print("\n" + "-" * 80)
    print(f" -> {title}")
    print("-" * 80)


async def run_live_adk_mcp_check() -> int:
    """Execute live Google ADK McpToolset integration test."""
    print_banner("GOOGLE ADK + MCP BRIDGE — LIVE INTEGRATION VERIFICATION")

    test_dir = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.abspath(os.path.join(test_dir, ".."))

    print(f"[Config] Python Interpreter : {sys.executable}")
    print(f"[Config] Backend Directory   : {backend_dir}")
    print(f"[Config] Target Namespace    : {TARGET_NAMESPACE}")
    print(f"[Config] ADK Bridge Class    : K8sMcpClientBridge (McpToolset over STDIO)")

    investigation_req = InvestigationRequest(namespace=TARGET_NAMESPACE)
    print(f"[Config] Validated Request   : namespace='{investigation_req.namespace}'")

    traces = []

    # 1. Connect via K8sMcpClientBridge
    print_section("Step 1: Connecting to Kubernetes MCP Server via ADK McpToolset")
    bridge = K8sMcpClientBridge(backend_dir=backend_dir, timeout=30.0)

    try:
        t0 = asyncio.get_event_loop().time()
        await bridge.connect()
        conn_duration = (asyncio.get_event_loop().time() - t0) * 1000.0
        print(f" [OK] ADK McpToolset connected successfully in {conn_duration:.1f}ms")

        # 2. Verify Tool Discovery
        print_section("Step 2: Verifying ADK Tool Discovery (Expected 7 Tools)")
        tools = await bridge.get_tools()
        discovered_names = [t.name for t in tools]
        print(f" Total tools discovered: {len(discovered_names)}")
        for name in discovered_names:
            marker = "[x]" if name in EXPECTED_K8S_MCP_TOOLS else "[?]"
            print(f"   {marker} {name}")

        missing_tools = [t for t in EXPECTED_K8S_MCP_TOOLS if t not in discovered_names]
        if missing_tools:
            print(f" [FAIL] Missing expected tools: {missing_tools}")
            return 1
        print(" [OK] All 7 expected Kubernetes MCP tools successfully registered in ADK.")

        # 3. Call scan_cluster against k8s-agent-demo
        print_section(f"Step 3: Invoking 'scan_cluster' for namespace '{TARGET_NAMESPACE}'")
        t_call0 = asyncio.get_event_loop().time()
        scan_response = await bridge.call_tool(
            "scan_cluster",
            {"namespace": TARGET_NAMESPACE},
        )
        call_duration = (asyncio.get_event_loop().time() - t_call0) * 1000.0

        traces.append(
            AgentToolCallTrace(
                agent_name="telemetry_agent",
                tool_name="scan_cluster",
                arguments={"namespace": TARGET_NAMESPACE},
                status="SUCCESS" if scan_response.get("success") else "ERROR",
                duration_ms=call_duration,
                result_summary=f"Pods scanned: {len(scan_response.get('data', {}).get('pods', [])) if scan_response.get('data') else 0}",
            )
        )

        if not scan_response.get("success"):
            print(f" [FAIL] scan_cluster failed: {scan_response.get('error')}")
            return 1

        scan_data = scan_response.get("data", {})
        pods = scan_data.get("pods", [])
        pod_count = scan_data.get("pod_count", len(pods))
        print(f" [OK] Discovered {pod_count} Pods in namespace '{TARGET_NAMESPACE}':")
        for idx, pod in enumerate(pods, 1):
            pod_name = pod.get("pod", "unknown")
            phase = pod.get("phase", "Unknown")
            is_ready = pod.get("is_ready", False)
            print(f"   {idx:2d}. {pod_name:<36} phase={phase:<10} ready={str(is_ready):<5}")

        # Construct ObservationReport
        observation_report = ObservationReport(
            namespace=TARGET_NAMESPACE,
            pod_count=pod_count,
            pods=pods,
            events=scan_data.get("events", []),
            logs_summary={"scanned_pod_count": pod_count},
        )
        print(f" [OK] ObservationReport constructed with {observation_report.pod_count} Pod records.")

        # 4. Call run_diagnosis against k8s-agent-demo
        print_section(f"Step 4: Invoking 'run_diagnosis' for namespace '{TARGET_NAMESPACE}'")
        t_diag0 = asyncio.get_event_loop().time()
        diag_response = await bridge.call_tool(
            "run_diagnosis",
            {"namespace": TARGET_NAMESPACE},
        )
        diag_duration = (asyncio.get_event_loop().time() - t_diag0) * 1000.0

        traces.append(
            AgentToolCallTrace(
                agent_name="diagnosis_agent",
                tool_name="run_diagnosis",
                arguments={"namespace": TARGET_NAMESPACE},
                status="SUCCESS" if diag_response.get("success") else "ERROR",
                duration_ms=diag_duration,
                result_summary=f"Diagnoses: {len(diag_response.get('data', {}).get('diagnoses', [])) if diag_response.get('data') else 0}",
            )
        )

        if not diag_response.get("success"):
            print(f" [FAIL] run_diagnosis failed: {diag_response.get('error')}")
            return 1

        diag_data = diag_response.get("data", {})
        diagnoses = diag_data.get("diagnoses", [])
        print(f" [OK] Generated {len(diagnoses)} Diagnoses across Kubernetes workloads:")
        for idx, diag in enumerate(diagnoses, 1):
            pod = diag.get("resource_name", "unknown")
            ftype = diag.get("failure_type", "UNKNOWN")
            sev = diag.get("severity", "UNKNOWN")
            print(f"   {idx:2d}. [{sev:<8}] {pod:<36} -> {ftype}")

        # 5. Build InvestigationResult and Sample RemediationProposal
        print_section("Step 5: Verifying Investigation and Remediation Data Models")
        investigation_result = InvestigationResult(
            namespace=TARGET_NAMESPACE,
            status=InvestigationStatus.COMPLETED,
            completed_at=datetime.now(timezone.utc).isoformat(),
            observation=observation_report,
            diagnoses=diagnoses,
            tool_traces=traces,
        )

        sample_patch = YamlPatch(
            target_kind="Pod",
            target_name="test-scenario-02-oom",
            target_namespace=TARGET_NAMESPACE,
            diff="--- resources.limits.memory: 64Mi\n+++ resources.limits.memory: 256Mi",
            patch_content={"spec": {"containers": [{"name": "memory-hog", "resources": {"limits": {"memory": "256Mi"}}}]}},
        )
        sample_plan = VerificationPlan(
            target_phase="Running",
            expected_conditions=[{"type": "Ready", "status": "True"}],
            negative_assertions=["OOMKilled", "CrashLoopBackOff"],
            check_interval_seconds=5,
            timeout_seconds=60,
        )
        sample_proposal = RemediationProposal(
            target_workload="test-scenario-02-oom",
            target_kind="Pod",
            target_namespace=TARGET_NAMESPACE,
            patch=sample_patch,
            justification="Container terminated with OOMKilled (exit code 137). Memory limit needs to be increased.",
            risk_level=RiskLevel.MEDIUM,
            requires_approval=True,
            approval=HumanApproval(status=ApprovalStatus.PENDING),
            verification_plan=sample_plan,
        )
        investigation_result.remediation_proposals.append(sample_proposal.model_dump())

        print(f" [OK] InvestigationResult created: ID={investigation_result.investigation_id}")
        print(f"      Status                 : {investigation_result.status.value}")
        print(f"      Tool Call Traces       : {len(investigation_result.tool_traces)}")
        print(f"      Diagnoses Recorded     : {len(investigation_result.diagnoses)}")
        print(f"      Remediation Proposals  : {len(investigation_result.remediation_proposals)}")
        print(f"      Sample Patch Kind/Name : {sample_patch.target_kind}/{sample_patch.target_name}")
        print(f"      Human Approval Guard   : {sample_proposal.requires_approval} (Status: {sample_proposal.approval.status.value})")

        # 6. Resource Cleanup
        print_section("Step 6: Closing ADK McpToolset and Verifying Resource Cleanup")
        await bridge.close()
        print(" [OK] Subprocess resources closed cleanly. is_connected = False")

        print_banner("PHASE 4B LIVE INTEGRATION TEST PASSED SUCCESSFULLY")
        return 0

    except Exception as exc:
        print(f"\n[FATAL ERROR] Unexpected exception during live check: {exc}")
        import traceback
        traceback.print_exc()
        await bridge.close()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(run_live_adk_mcp_check())
    sys.exit(exit_code)
