"""
Verification Planner Agent.

Generates post-remediation verification plans defining target Pod phase,
readiness conditions, stability criteria, and negative assertions.
Only attaches verification plans to actionable proposals that contain a patch.
"""

import logging
from typing import Any, Dict, List, Optional

from app.models.remediation import RemediationProposal, VerificationPlan

logger = logging.getLogger(__name__)

# Grounded negative assertion mappings for the failure categories
FAILURE_NEGATIVE_ASSERTIONS = {
    "IMAGE_PULL_FAILURE": ["ImagePullBackOff", "ErrImagePull"],
    "MEMORY_EXHAUSTION": ["OOMKilled", "CrashLoopBackOff"],
    "APPLICATION_PROCESS_FAILURE": ["CrashLoopBackOff", "Error"],
    "INSUFFICIENT_CLUSTER_RESOURCES": ["FailedScheduling"],
    "READINESS_PROBE_FAILURE": ["ContainersNotReady"],
    "LIVENESS_PROBE_FAILURE": ["Unhealthy", "ContainerRestart"],
    "MISSING_CONFIGMAP": ["CreateContainerConfigError"],
    "MISSING_SECRET": ["CreateContainerConfigError"],
    "UNBOUND_PVC": ["FailedScheduling", "FailedBinding"],
    "INVALID_STARTUP_COMMAND": ["CrashLoopBackOff", "RunContainerError"],
}


class VerificationPlanner:
    """
    Formulates structured post-remediation verification plans.
    Guarantees strict positive conditions and negative assertions.
    """

    def __init__(self, check_interval_seconds: int = 5, timeout_seconds: int = 60):
        self.check_interval_seconds = check_interval_seconds
        self.timeout_seconds = timeout_seconds

    def plan_verification(
        self,
        proposals: List[RemediationProposal],
        diagnoses: Optional[List[Dict[str, Any]]] = None,
    ) -> List[RemediationProposal]:
        """
        Formulate and attach verification plans to actionable remediation proposals.
        Non-actionable proposals (can_remediate=False) do not receive a verification plan.

        Args:
            proposals: List of generated RemediationProposal objects.
            diagnoses: Authoritative deterministic diagnoses for correlation.

        Returns:
            Updated list of proposals with attached VerificationPlan objects where actionable.
        """
        diag_map = {}
        if diagnoses:
            for d in diagnoses:
                target = d.get("pod") or d.get("resource_name", "").split("/")[0]
                if target:
                    diag_map[target] = d.get("failure_type", "UNKNOWN")

        for proposal in proposals:
            if not proposal.can_remediate:
                proposal.verification_plan = None
                continue

            failure_type = diag_map.get(proposal.target_workload, "UNKNOWN")

            # Determine negative assertions matching the failure signature
            negative_assertions = list(
                FAILURE_NEGATIVE_ASSERTIONS.get(
                    failure_type, ["CrashLoopBackOff", "FailedScheduling", "Error"]
                )
            )

            plan = VerificationPlan(
                target_phase="Running",
                expected_conditions=[
                    {"type": "Ready", "status": "True"},
                    {"type": "ContainersReady", "status": "True"},
                ],
                negative_assertions=negative_assertions,
                check_interval_seconds=self.check_interval_seconds,
                timeout_seconds=self.timeout_seconds,
            )

            proposal.verification_plan = plan
            logger.debug(
                "Attached VerificationPlan to proposal '%s' (target: %s, failure: %s)",
                proposal.proposal_id,
                proposal.target_workload,
                failure_type,
            )

        return proposals
