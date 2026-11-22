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
    GeminiRequestMetric,
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
from app.orchestrator.enrichment import enrich_proposals_with_trusted_targets
from app.orchestrator.sanitizer import sanitize_error_message
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
        allow_best_effort: bool = False,
    ):
        self._external_bridge = bridge is not None
        self.bridge = bridge or K8sMcpClientBridge()
        self.model_name = model_name or os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        self.allow_best_effort = allow_best_effort
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
        gemini_metrics: List[GeminiRequestMetric] = []
        obs_agent: Optional[ObservationAgent] = None
        rem_planner: Optional[RemediationPlanner] = None
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
                nonlocal obs_agent, rem_planner
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

                rem_planner = RemediationPlanner(
                    bridge=self.bridge,
                    model_name=self.model_name,
                    allow_best_effort=self.allow_best_effort,
                )
                proposals = await asyncio.wait_for(
                    rem_planner.plan_remediations(
                        diagnoses=diagnoses,
                        namespace=request.namespace,
                        observed_pods=obs_report.pods if obs_report else [],
                        use_llm=use_llm,
                    ),
                    timeout=self.stage_timeout,
                )
                all_traces.extend(rem_planner.get_traces())

                # Deterministic trusted-target enrichment (Phase 7B.1)
                proposals = enrich_proposals_with_trusted_targets(
                    proposals=proposals,
                    diagnoses=diagnoses,
                    namespace=request.namespace,
                    observed_pods=obs_report.pods if obs_report else [],
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
                all_traces.extend(ver_planner.get_traces())
                result.remediation_proposals = [p.model_dump() for p in verified_proposals]

                # Independent stage provenances
                obs_provenance = obs_report.provenance if obs_report else "DETERMINISTIC_FALLBACK"
                result.observation_provenance = obs_provenance
                result.diagnosis_provenance = "DETERMINISTIC_ENGINE"
                result.remediation_provenance = rem_planner.remediation_provenance
                result.verification_provenance = "DETERMINISTIC_ENGINE"

                # Collect Gemini metrics across stages
                stage_metrics: List[GeminiRequestMetric] = []
                if obs_agent:
                    stage_metrics.extend(obs_agent.get_metrics())
                if rem_planner:
                    stage_metrics.extend(rem_planner.get_metrics())
                result.gemini_metrics = stage_metrics

                # Provenance and metadata (backward compatibility)
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

                # Determine overall outcome
                gemini_obs_ok = (result.observation_provenance == "GEMINI_AGENT")
                gemini_rem_ok = (result.remediation_provenance == "GEMINI_AGENT")
                if not diagnoses:
                    both_succeeded = gemini_obs_ok
                else:
                    both_succeeded = gemini_obs_ok and gemini_rem_ok

                if both_succeeded:
                    result.outcome = "COMPLETED_WITH_GEMINI"
                else:
                    result.outcome = "COMPLETED_WITH_FALLBACK"

                # Complete pipeline
                result.status = InvestigationStatus.COMPLETED
                result.completed_at = datetime.now(timezone.utc).isoformat()
                result.tool_traces = all_traces
                logger.info("[%s] Pipeline completed successfully with outcome: %s", investigation_id, result.outcome)
                return result

            # Run all pipeline stages with total timeout guardrail
            return await asyncio.wait_for(_run_pipeline_stages(), timeout=self.total_timeout)

        except Exception as exc:
            logger.error("[%s] Investigation pipeline failed: %s", investigation_id, exc)
            failed_stage = result.status.value
            result.status = InvestigationStatus.FAILED
            result.outcome = "FAILED"
            result.completed_at = datetime.now(timezone.utc).isoformat()
            result.tool_traces = all_traces

            # Harvest any metrics generated before failure
            err_metrics: List[GeminiRequestMetric] = []
            if obs_agent:
                err_metrics.extend(obs_agent.get_metrics())
            if rem_planner:
                err_metrics.extend(rem_planner.get_metrics())
            result.gemini_metrics = err_metrics

            err_msg = str(exc).strip()
            if isinstance(exc, asyncio.TimeoutError) or not err_msg:
                err_msg = f"Stage '{failed_stage}' timed out after {self.stage_timeout:.1f}s"
            result.error = {
                "message": sanitize_error_message(err_msg),
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
