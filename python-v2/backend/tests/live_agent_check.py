#!/usr/bin/env python3
"""
Standalone Live Gemini + Google ADK Agentic AI Investigation Script.

Connects to the real Docker Desktop Kubernetes cluster in 'k8s-agent-demo'
and runs an end-to-end Agentic AI incident investigation:
Observation (Gemini + ADK + MCP) → Deterministic Diagnosis → Remediation Planning → Verification Planning.

Requirements:
- Set GEMINI_API_KEY in your environment to enable real LLM execution:
    export GEMINI_API_KEY="your-gemini-api-key"
    export GEMINI_MODEL="gemini-3.8-flash"  # Optional, defaults to gemini-3.8-flash

Usage:
    cd python-v2/backend
    source .venv/bin/activate
    PYTHONPATH=. python tests/live_agent_check.py
"""

import asyncio
import json
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from app.mcp_client import K8sMcpClientBridge
from app.models.investigation import InvestigationRequest
from app.orchestrator import (
    InvestigationPipeline,
    get_gemini_model_name,
    is_gemini_api_key_available,
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


async def run_live_agent_check() -> int:
    """Execute live Agentic AI investigation with Gemini and Google ADK."""
    print_banner("AGENTIC AI INVESTIGATION WORKFLOW — LIVE GEMINI CHECK")

    api_key_set = is_gemini_api_key_available()
    model_name = get_gemini_model_name()

    print(f"[Config] Python Interpreter    : {sys.executable}")
    print(f"[Config] Target Namespace       : {TARGET_NAMESPACE}")
    print(f"[Config] Configured Model       : {model_name}")
    print(f"[Config] GEMINI_API_KEY Present : {api_key_set}")

    if not api_key_set:
        print_section("Status: SKIPPED (Credentials Required)")
        print("  GEMINI_API_KEY is not configured in the environment.")
        print("  To run live Gemini agent investigations, configure your API key:")
        print("    export GEMINI_API_KEY=\"<your-google-ai-api-key>\"")
        print(f"    export GEMINI_MODEL=\"{model_name}\"  # optional")
        print("\n  Unit test suite (62+ tests) does NOT require an API key and runs independently.")
        print_banner("LIVE GEMINI CHECK COMPLETED (SKIPPED DUE TO MISSING CREDENTIALS)")
        return 0

    print_section("Step 1: Initializing ADK MCP Bridge and Pipeline")
    bridge = K8sMcpClientBridge(timeout=30.0)

    try:
        await bridge.connect()
        tools = await bridge.get_tools()
        print(f" [OK] ADK McpToolset connected. Discovered {len(tools)} tools: {[t.name for t in tools]}")

        pipeline = InvestigationPipeline(bridge=bridge, model_name=model_name)
        req = InvestigationRequest(namespace=TARGET_NAMESPACE)

        print_section(f"Step 2: Executing Agentic Investigation Pipeline for '{TARGET_NAMESPACE}'")
        t0 = asyncio.get_event_loop().time()
        result = await pipeline.execute(request=req, use_llm=True)
        duration_s = asyncio.get_event_loop().time() - t0

        print(f" [OK] Pipeline finished in {duration_s:.2f}s with status: {result.status.value}")
        print(f"      Investigation ID    : {result.investigation_id}")
        print(f"      Target Namespace    : {result.namespace}")
        print(f"      Completed At        : {result.completed_at}")
        print(f"      Pipeline Provenance : {result.provenance}")
        if result.model_metadata:
            print(f"      Model / Provider    : {result.model_metadata.get('model')} / {result.model_metadata.get('provider')}")
            if result.model_metadata.get("gemini_error"):
                print(f"      Gemini Fallback Err : {result.model_metadata.get('gemini_error')}")

        if result.error:
            print_section("Pipeline Failure Details")
            print(f" [FAIL] Failed Stage        : {result.error.get('failed_at_status')}")
            print(f"        Error Type          : {result.error.get('type')}")
            print(f"        Error Message       : {result.error.get('message')}")
            return 1

        # Summary of Observation stage
        print_section("Step 3: Observation Stage Summary")
        if result.observation:
            print(f" Observation Provenance : {result.observation.provenance}")
            if result.observation.llm_error:
                print(f" LLM Fallback Reason    : {result.observation.llm_error}")
            print(f" Pods Scanned           : {result.observation.pod_count}")
            for idx, p in enumerate(result.observation.pods[:5], 1):
                print(f"   {idx}. {p.get('pod'):<36} phase={p.get('phase')}")
            if len(result.observation.pods) > 5:
                print(f"   ... and {len(result.observation.pods) - 5} more Pods")

        # Summary of Deterministic Diagnoses
        print_section("Step 4: Authoritative Deterministic Diagnoses")
        print(f" Total Diagnoses: {len(result.diagnoses)}")
        for idx, d in enumerate(result.diagnoses, 1):
            sev = d.get("severity", "INFO")
            res = d.get("resource_name", "unknown")
            ftype = d.get("failure_type", "UNKNOWN")
            print(f"   {idx:2d}. [{sev:<8}] {res:<36} -> {ftype}")

        # Summary of Remediation Proposals
        print_section("Step 5: Grounded Remediation Proposals & Human Approval Guardrails")
        print(f" Total Proposals: {len(result.remediation_proposals)}")
        for idx, prop in enumerate(result.remediation_proposals, 1):
            workload = prop.get("target_workload")
            t_kind = prop.get("target_kind")
            t_name = prop.get("target_name")
            p_status = prop.get("status")
            can_rem = prop.get("can_remediate")
            prov = prop.get("provenance")
            risk = prop.get("risk_level")
            req_app = prop.get("requires_approval")
            app_status = prop.get("approval", {}).get("status")
            vplan = prop.get("verification_plan") or {}
            neg_asserts = vplan.get("negative_assertions", [])

            print(f"   {idx:2d}. Workload: {workload} -> Target: {t_kind}/{t_name}")
            print(f"       Status             : {p_status} (Actionable: {can_rem})")
            print(f"       Provenance         : {prov}")
            print(f"       Risk Level         : {risk}")
            print(f"       Human Approval Req : {req_app} (Status: {app_status})")
            if can_rem:
                print(f"       Expected Post-Phase: {vplan.get('target_phase')}")
                print(f"       Negative Assertions: {neg_asserts}")
            else:
                print(f"       Withheld Reason    : {prop.get('insufficient_evidence_reason')}")
            print(f"       Justification      : {prop.get('justification')[:80]}...")

        # Summary of Tool Traces
        print_section("Step 6: Execution Tool Call Traces")
        print(f" Total Traces Recorded: {len(result.tool_traces)}")
        for idx, tr in enumerate(result.tool_traces, 1):
            agent = tr.agent_name
            tname = tr.tool_name
            dur = f"{tr.duration_ms:.1f}ms" if tr.duration_ms else "N/A"
            print(f"   {idx:2d}. [{dur:>8}] {agent:<30} -> {tname}")

        print_banner("PHASE 4C LIVE GEMINI INVESTIGATION PASSED SUCCESSFULLY")
        return 0

    except Exception as exc:
        print(f"\n[FATAL ERROR] Unexpected exception during live check: {exc}")
        import traceback
        traceback.print_exc()
        return 1

    finally:
        await bridge.close()


if __name__ == "__main__":
    exit_code = asyncio.run(run_live_agent_check())
    sys.exit(exit_code)
