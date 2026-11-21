"""
Observation Agent using Gemini, Google ADK, and Kubernetes MCP Tools.

Inspects Kubernetes runtime state, logs, events, and resource configurations
via read-only MCP tools to build a factual ObservationReport.
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid

from google.adk.agents.llm_agent import LlmAgent
from google.adk.models.google_llm import Gemini
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from google.genai.types import HttpRetryOptions

from app.mcp_client import K8sMcpClientBridge
from app.models.investigation import (
    AgentToolCallTrace,
    InvestigationRequest,
    ObservationReport,
)
from app.orchestrator.prompts import OBSERVATION_AGENT_INSTRUCTION
from app.validators.safety_validator import (
    validate_read_only_tools,
    validate_secret_safety,
)

logger = logging.getLogger(__name__)

DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"

DEFAULT_RETRY_OPTIONS = HttpRetryOptions(
    attempts=3,
    initial_delay=1.0,
    max_delay=8.0,
    http_status_codes=[408, 429, 500, 502, 503, 504],
)


class ObservationAgent:
    """
    Agent responsible for observing cluster telemetry and runtime health
    using Google ADK and custom Kubernetes MCP tools.
    """

    def __init__(
        self,
        bridge: K8sMcpClientBridge,
        model_name: Optional[str] = None,
        retry_options: Optional[HttpRetryOptions] = None,
    ):
        self.bridge = bridge
        self.model_name = model_name or os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        self.retry_options = retry_options or DEFAULT_RETRY_OPTIONS
        self._agent: Optional[LlmAgent] = None

    async def get_adk_agent(self) -> LlmAgent:
        """
        Build and cache the Google ADK LlmAgent registered with the bridge's MCP tools.
        """
        if self._agent is not None:
            return self._agent

        tools = await self.bridge.get_tools()
        # Verify read-only guardrail before handing tools to the agent
        validate_read_only_tools(tools)

        gemini_model = Gemini(
            model=self.model_name,
            retry_options=self.retry_options,
        )

        self._agent = LlmAgent(
            name="k8s_observation_agent",
            model=gemini_model,
            instruction=OBSERVATION_AGENT_INSTRUCTION,
            tools=tools,
        )
        return self._agent

    async def observe(
        self,
        request: InvestigationRequest,
        use_llm: bool = True,
        timeout_seconds: float = 60.0,
    ) -> Tuple[ObservationReport, List[AgentToolCallTrace], Optional[str]]:
        """
        Execute the observation stage for a given InvestigationRequest.

        Args:
            request: The validated investigation request.
            use_llm: Whether to invoke Gemini via ADK (requires GEMINI_API_KEY).
                     If False, collects telemetry directly via MCP tools.
            timeout_seconds: Maximum time allowed for observation.

        Returns:
            Tuple of (ObservationReport, List[AgentToolCallTrace], Optional[llm_synthesis_text])
        """
        traces: List[AgentToolCallTrace] = []
        llm_synthesis: Optional[str] = None
        gemini_succeeded: bool = False
        llm_error_details: Optional[Dict[str, Any]] = None

        api_key = os.environ.get("GEMINI_API_KEY")
        if use_llm and not api_key:
            raise ValueError(
                "GEMINI_API_KEY environment variable is not set. "
                "Please configure GEMINI_API_KEY to run live Agentic AI investigations with Gemini."
            )

        if use_llm:
            logger.info("Executing Observation Agent with Gemini model '%s'", self.model_name)
            llm_timeout = max(10.0, timeout_seconds - 20.0)
            try:
                agent = await self.get_adk_agent()
                session_service = InMemorySessionService()
                runner = Runner(
                    app_name="k8s_agentic_ops",
                    agent=agent,
                    session_service=session_service,
                    auto_create_session=True,
                )

                prompt_text = (
                    f"Investigate Kubernetes failures in namespace '{request.namespace}'."
                )
                if request.pod_name:
                    prompt_text += f" Focus on target Pod '{request.pod_name}'."
                prompt_text += " Use your read-only tools to gather facts and summarize the failure."

                msg = types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=prompt_text)],
                )

                session_id = f"obs-{uuid.uuid4().hex[:8]}"
                user_id = "sre-operator"

                async def _run_agent_loop():
                    nonlocal llm_synthesis
                    synthesis_parts = []
                    async for event in runner.run_async(
                        user_id=user_id,
                        session_id=session_id,
                        new_message=msg,
                    ):
                        # Capture tool calls made by Gemini
                        func_calls = event.get_function_calls()
                        if func_calls:
                            for fc in func_calls:
                                trace = AgentToolCallTrace(
                                    agent_name="observation_agent",
                                    tool_name=fc.name,
                                    arguments=dict(fc.args) if fc.args else {},
                                    status="SUCCESS",
                                )
                                traces.append(trace)

                        # Capture model response text
                        if event.message and event.message.parts:
                            for p in event.message.parts:
                                if getattr(p, "text", None):
                                    synthesis_parts.append(p.text)

                    if synthesis_parts:
                        llm_synthesis = "\n".join(synthesis_parts)

                await asyncio.wait_for(_run_agent_loop(), timeout=llm_timeout)
                gemini_succeeded = True
            except asyncio.TimeoutError:
                logger.warning(
                    "Observation Agent LLM timed out after %.1f seconds; falling back to deterministic telemetry collection.",
                    llm_timeout,
                )
                llm_error_details = {
                    "type": "TimeoutError",
                    "message": f"Observation Agent Gemini invocation timed out after {llm_timeout:.1f}s",
                }
                gemini_succeeded = False
            except Exception as exc:
                logger.warning(
                    "Observation Agent LLM execution encountered an error (%s: %s); falling back to deterministic telemetry collection.",
                    type(exc).__name__,
                    exc,
                )
                llm_error_details = {
                    "type": type(exc).__name__,
                    "message": str(exc),
                }
                gemini_succeeded = False

        # Telemetry ingestion: query scan_cluster to ensure complete structured data
        t0 = time.time()
        fallback_timeout = 20.0
        try:
            scan_res = await asyncio.wait_for(
                self.bridge.call_tool(
                    "scan_cluster",
                    {"namespace": request.namespace},
                ),
                timeout=fallback_timeout,
            )
        except Exception as scan_err:
            logger.error("Deterministic scan_cluster call failed: %s", scan_err)
            raise RuntimeError(f"Deterministic evidence collection failed: {scan_err}") from scan_err

        duration_ms = (time.time() - t0) * 1000.0

        scan_success = scan_res.get("success", False)
        traces.append(
            AgentToolCallTrace(
                agent_name="telemetry_collector",
                tool_name="scan_cluster",
                arguments={"namespace": request.namespace},
                status="SUCCESS" if scan_success else "ERROR",
                duration_ms=duration_ms,
                result_summary=f"Pods scanned: {len(scan_res.get('data', {}).get('pods', [])) if scan_res.get('data') else 0}",
                error_message=str(scan_res.get("error")) if not scan_success else None,
            )
        )

        if not scan_success:
            err_msg = scan_res.get("error") or "Unknown error in scan_cluster"
            raise RuntimeError(f"Deterministic evidence collection failed: {err_msg}")

        scan_data = scan_res.get("data") or {}
        pods = scan_data.get("pods", [])
        events = scan_data.get("events", [])
        pod_count = scan_data.get("pod_count", len(pods))

        # Filter by pod_name if specified
        if request.pod_name:
            pods = [p for p in pods if p.get("pod") == request.pod_name]
            events = [e for e in events if e.get("pod") == request.pod_name or request.pod_name in (e.get("message") or "")]

        # Validate that no secret contents leaked into the observed data
        validate_secret_safety(pods)

        provenance = "GEMINI_AGENT" if gemini_succeeded else "DETERMINISTIC_FALLBACK"

        observation_report = ObservationReport(
            namespace=request.namespace,
            pod_count=len(pods),
            pods=pods,
            events=events,
            logs_summary={"scanned_pods": [p.get("pod") for p in pods]},
            provenance=provenance,
            llm_error=llm_error_details,
        )

        return observation_report, traces, llm_synthesis

        return observation_report, traces, llm_synthesis
