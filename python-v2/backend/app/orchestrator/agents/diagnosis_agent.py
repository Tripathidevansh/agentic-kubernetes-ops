"""
Deterministic Incident Diagnosis Agent.

Executes the deterministic incident diagnosis engine as the authoritative
ground truth component. Gemini is strictly forbidden from overriding or
modifying these diagnoses.
"""

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from app.mcp_client import K8sMcpClientBridge
from app.models.investigation import AgentToolCallTrace, ObservationReport
from app.services.diagnosis_service import diagnose_cluster
from app.validators.safety_validator import validate_deterministic_authority

logger = logging.getLogger(__name__)


class DeterministicDiagnosisAgent:
    """
    Authoritative diagnosis provider using deterministic rule-based evaluation.
    This component does NOT invoke an LLM, ensuring 100% reproducible ground truth.
    """

    def __init__(self, bridge: Optional[K8sMcpClientBridge] = None):
        self.bridge = bridge

    async def diagnose(
        self,
        namespace: str,
        pod_name: Optional[str] = None,
        observation: Optional[ObservationReport] = None,
    ) -> Tuple[List[Dict[str, Any]], List[AgentToolCallTrace], Dict[str, Any]]:
        """
        Execute deterministic incident diagnosis.

        Args:
            namespace: Target Kubernetes namespace.
            pod_name: Optional specific Pod name to filter.
            observation: Telemetry collected during the observation phase.

        Returns:
            Tuple of:
            - List of authoritative diagnosis dictionaries.
            - List of AgentToolCallTrace records.
            - Consistency analysis dictionary.
        """
        traces: List[AgentToolCallTrace] = []
        raw_diagnoses: List[Dict[str, Any]] = []

        # 1. Obtain diagnoses either via MCP tool or directly via deterministic service
        t0 = time.time()
        if self.bridge and self.bridge.is_connected:
            diag_res = await self.bridge.call_tool(
                "run_diagnosis",
                {"namespace": namespace},
            )
            duration_ms = (time.time() - t0) * 1000.0

            success = diag_res.get("success", False)
            traces.append(
                AgentToolCallTrace(
                    agent_name="deterministic_diagnosis_engine",
                    tool_name="run_diagnosis",
                    arguments={"namespace": namespace},
                    status="SUCCESS" if success else "ERROR",
                    duration_ms=duration_ms,
                    result_summary=f"Diagnoses generated: {len(diag_res.get('data', {}).get('diagnoses', [])) if diag_res.get('data') else 0}",
                    error_message=str(diag_res.get("error")) if not success else None,
                )
            )

            if success and diag_res.get("data"):
                raw_diagnoses = diag_res["data"].get("diagnoses", [])
        else:
            # Fallback to direct service invocation if bridge not provided or in in-memory test
            if observation and observation.pods:
                cluster_evidence = {
                    "namespace": namespace,
                    "pod_count": observation.pod_count,
                    "pods": observation.pods,
                }
                res = diagnose_cluster(cluster_evidence)
                raw_diagnoses = res.get("diagnoses", [])
            else:
                raw_diagnoses = []

        # 2. Scope filtering if pod_name was requested
        filtered_diagnoses: List[Dict[str, Any]] = []
        for d in raw_diagnoses:
            target_res = d.get("resource_name", "")
            pod_field = d.get("pod", "")
            if pod_name:
                if pod_name == target_res or pod_name == pod_field or target_res.startswith(f"{pod_name}/"):
                    filtered_diagnoses.append(d)
            else:
                filtered_diagnoses.append(d)

        # 3. Consistency cross-check against Observation Report
        consistency_report: Dict[str, Any] = {
            "is_consistent": True,
            "discrepancies": [],
            "diagnosed_pods": [d.get("resource_name") for d in filtered_diagnoses],
        }

        if observation:
            observed_pod_names = {p.get("pod") for p in observation.pods if p.get("pod")}
            diagnosed_pods = {
                d.get("pod") or d.get("resource_name")
                for d in filtered_diagnoses
            }

            # Check if any diagnosed pod was never seen in observation
            for d_pod in diagnosed_pods:
                # Handle "pod-name/container" format
                base_pod = d_pod.split("/")[0] if d_pod else ""
                if base_pod and base_pod not in observed_pod_names:
                    msg = f"Discrepancy: Diagnosis generated for pod '{d_pod}' not present in observation."
                    consistency_report["discrepancies"].append(msg)
                    consistency_report["is_consistent"] = False
                    logger.warning(msg)

        # Enforce invariant: deterministic engine is authoritative
        validate_deterministic_authority(filtered_diagnoses)

        return filtered_diagnoses, traces, consistency_report
