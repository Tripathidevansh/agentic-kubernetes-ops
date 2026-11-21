"""
Agentic Kubernetes Investigation Pipeline.

Coordinates the end-to-end incident investigation sequence:
Observation → Deterministic Diagnosis → Remediation Planning → Verification Planning.
"""

import asyncio
from datetime import datetime, timezone
import logging
import os
import time
from typing import Any, Dict, List, Optional
import uuid

from app.mcp_client import K8sMcpClientBridge
from app.models.investigation import (
    AgentToolCallTrace,
    InvestigationRequest,
    InvestigationResult,
    InvestigationStatus,
    ObservationReport,
)
from app.models.remediation import RemediationProposal
from app.orchestrator.agents.diagnosis_agent import DeterministicDiagnosisAgent
from app.orchestrator.agents.observation_agent import ObservationAgent
from app.orchestrator.agents.remediation_agent import RemediationPlanner
from app.orchestrator.agents.verification_agent import VerificationPlanner
from app.validators.safety_validator import validate_read_only_tools

logger = logging.getLogger(__name__)

DEFAULT_STAGE_TIMEOUT_SECONDS = float(os.environ.get("STAGE_TIMEOUT_SECONDS", "120.0"))
DEFAULT_TOTAL_TIMEOUT_SECONDS = float(os.environ.get("TOTAL_TIMEOUT_SECONDS", "360.0"))
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"


class InvestigationPipeline:
    """
    Coordinated workflow orchestrating Observation, Deterministic Diagnosis,
    Remediation Planning, and Verification Planning.
    """

    def __init__(
        self,
        bridge: Optional[K8sMcpClientBridge] = None,
        model_name: Optional[str] = None,
        stage_timeout_seconds: Optional[float] = None,
        total_timeout_seconds: Optional[float] = None,
    ):
        self._external_bridge = bridge is not None
        self.bridge = bridge or K8sMcpClientBridge()
        self.model_name = model_name or os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        if stage_timeout_seconds is not None:
            self.stage_timeout = float(stage_timeout_seconds)
        else:
            self.stage_timeout = float(os.environ.get("STAGE_TIMEOUT_SECONDS", str(DEFAULT_STAGE_TIMEOUT_SECONDS)))
        if total_timeout_seconds is not None:
            self.total_timeout = float(total_timeout_seconds)
        else:
            self.total_timeout = float(os.environ.get("TOTAL_TIMEOUT_SECONDS", str(DEFAULT_TOTAL_TIMEOUT_SECONDS)))

    async def execute(
        self,
        request: InvestigationRequest,
        use_llm: bool = True,
    ) -> InvestigationResult:
        """
        Execute the full incident investigation pipeline for an InvestigationRequest.

        Args:
            request: Target namespace and optional pod scope.
            use_llm: Whether to invoke Gemini via Google ADK and GenAI.
                     If False, runs deterministically (ideal for unit testing).

        Returns:
            Fully populated InvestigationResult.
        """
        investigation_id = request.investigation_id or str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()

        result = InvestigationResult(
            investigation_id=investigation_id,
            status=InvestigationStatus.PENDING,
            namespace=request.namespace,
            target_pod=request.pod_name,
            created_at=created_at,
        )

        all_traces: List[AgentToolCallTrace] = []
        should_close_bridge = not self._external_bridge

        try:
            # Connect MCP bridge if not already connected
            if not self.bridge.is_connected:
                logger.info("Connecting MCP bridge for investigation '%s'...", investigation_id)
                await self.bridge.connect()

            # Ensure all available tools adhere to the read-only safety guardrail
            tools = await self.bridge.get_tools()
            validate_read_only_tools(tools)

            async def _run_pipeline_stages() -> InvestigationResult:
                # -------------------------------------------------------------
                # Stage 1: Observation
                # -------------------------------------------------------------
                result.status = InvestigationStatus.OBSERVING
                logger.info("[%s] Starting Stage 1: Observation", investigation_id)

                obs_agent = ObservationAgent(bridge=self.bridge, model_name=self.model_name)
                stage1_outer_timeout = self.stage_timeout + 15.0
                obs_report, obs_traces, _ = await asyncio.wait_for(
                    obs_agent.observe(request=request, use_llm=use_llm, timeout_seconds=self.stage_timeout),
                    timeout=stage1_outer_timeout,
                )
                result.observation = obs_report
                all_traces.extend(obs_traces)

                # -------------------------------------------------------------
                # Stage 2: Deterministic Diagnosis (Authoritative Ground Truth)
                # -------------------------------------------------------------
                result.status = InvestigationStatus.DIAGNOSING
                logger.info("[%s] Starting Stage 2: Deterministic Diagnosis", investigation_id)

                diag_agent = DeterministicDiagnosisAgent(bridge=self.bridge)
                diagnoses, diag_traces, _ = await asyncio.wait_for(
                    diag_agent.diagnose(
                        namespace=request.namespace,
                        pod_name=request.pod_name,
                        observation=obs_report,
                    ),
                    timeout=self.stage_timeout,
                )
                result.diagnoses = diagnoses
                all_traces.extend(diag_traces)

                # -------------------------------------------------------------
                # Stage 3: Remediation Planning
                # -------------------------------------------------------------
                result.status = InvestigationStatus.PLANNING
                logger.info("[%s] Starting Stage 3: Remediation Planning", investigation_id)

                rem_planner = RemediationPlanner(bridge=self.bridge, model_name=self.model_name)
                proposals = await asyncio.wait_for(
                    rem_planner.plan_remediations(
                        diagnoses=diagnoses,
                        namespace=request.namespace,
                        observed_pods=obs_report.pods if obs_report else [],
                        use_llm=use_llm,
                    ),
                    timeout=self.stage_timeout,
                )

                # -------------------------------------------------------------
                # Stage 4: Verification Planning
                # -------------------------------------------------------------
                logger.info("[%s] Starting Stage 4: Verification Planning", investigation_id)
                ver_planner = VerificationPlanner()
                verified_proposals = ver_planner.plan_verification(
                    proposals=proposals,
                    diagnoses=diagnoses,
                )
                result.remediation_proposals = [p.model_dump() for p in verified_proposals]

                # Provenance and metadata
                gemini_used = (
                    use_llm
                    and bool(os.environ.get("GEMINI_API_KEY"))
                    and obs_report is not None
                    and getattr(obs_report, "provenance", None) == "GEMINI_AGENT"
                )
                if gemini_used:
                    result.provenance = "GEMINI_AGENT"
                    result.model_metadata = {"model": self.model_name, "provider": "google_genai"}
                else:
                    result.provenance = "DETERMINISTIC_FALLBACK"
                    metadata: Dict[str, Any] = {
                        "model": self.model_name if use_llm else None,
                        "provider": "deterministic_fallback" if use_llm else "deterministic_rules",
                    }
                    if obs_report and getattr(obs_report, "llm_error", None):
                        metadata["gemini_error"] = obs_report.llm_error
                    result.model_metadata = metadata

                # Complete pipeline
                result.status = InvestigationStatus.COMPLETED
                result.completed_at = datetime.now(timezone.utc).isoformat()
                result.tool_traces = all_traces
                logger.info("[%s] Pipeline completed successfully.", investigation_id)
                return result

            # Run all pipeline stages with total timeout guardrail
            return await asyncio.wait_for(_run_pipeline_stages(), timeout=self.total_timeout)

        except Exception as exc:
            logger.error("[%s] Investigation pipeline failed: %s", investigation_id, exc)
            failed_stage = result.status.value
            result.status = InvestigationStatus.FAILED
            result.completed_at = datetime.now(timezone.utc).isoformat()
            result.tool_traces = all_traces
            err_msg = str(exc).strip()
            if isinstance(exc, asyncio.TimeoutError) or not err_msg:
                err_msg = f"Stage '{failed_stage}' timed out after {self.stage_timeout:.1f}s"
            result.error = {
                "message": err_msg,
                "type": type(exc).__name__,
                "failed_at_status": failed_stage,
            }
            return result

        finally:
            if should_close_bridge:
                await self.bridge.close()

    async def close(self) -> None:
        """Close the underlying MCP client bridge."""
        if self.bridge:
            await self.bridge.close()

    async def __aenter__(self) -> "InvestigationPipeline":
        if not self.bridge.is_connected:
            await self.bridge.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()
