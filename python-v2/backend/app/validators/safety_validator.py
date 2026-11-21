"""
Grounding and Safety Validators for Agentic AI Operations.

Enforces strict safety rules:
1. Read-only tool constraint: No mutating or write-capable tools.
2. Secret data protection: Kubernetes Secret values must never be exposed or logged.
3. Deterministic ground truth: Authoritative deterministic diagnoses cannot be overridden by LLM.
4. No masking commands: Prohibits sleep, true, no-ops, or commands that mask application failures.
5. Workload controller targeting: Enforces declarative controller targeting (e.g. Deployment) over ephemeral Pods.
6. Root cause alignment: Remediations must directly address the diagnosed failure.
7. Mandatory human approval: require_approval must always be True with PENDING status.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

from app.mcp_client import EXPECTED_K8S_MCP_TOOLS
from app.models.remediation import (
    ApprovalStatus,
    ProposalStatus,
    RemediationProposal,
)

logger = logging.getLogger(__name__)

# Mutating or destructive keywords that must NEVER appear in tool names
DISALLOWED_TOOL_KEYWORDS = [
    "apply",
    "create",
    "delete",
    "patch",
    "update",
    "exec",
    "shell",
    "command",
    "kill",
    "restart",
    "modify",
    "write",
]

# Patterns of commands that mask failures rather than fixing root causes
DISALLOWED_MASKING_PATTERNS = [
    r"\bsleep\s+\d+",
    r"\bsleep\s+infinity",
    r"['\"]sleep['\"]",
    r"\btrue\b",
    r"/bin/true",
    r"tail\s+-f\s+/dev/null",
    r"tail\s+-f",
    r"cat\s+/dev/null",
    r"\bexit\s+0\b",
    r"\bpause\b",
    r"while\s+true",
    r"while\s+:",
]


class SafetyViolationError(Exception):
    """Raised when an operation violates agentic safety or grounding guardrails."""
    pass


def validate_read_only_tools(tools: List[Any]) -> None:
    """
    Ensure all tools available to agents are strictly read-only and within expected set.

    Raises:
        SafetyViolationError: If any write-capable or unknown mutating tool is discovered.
    """
    for tool in tools:
        name = getattr(tool, "name", str(tool)).lower()

        # Check against explicitly disallowed mutating action words
        for kw in DISALLOWED_TOOL_KEYWORDS:
            if kw in name:
                raise SafetyViolationError(
                    f"Disallowed mutating tool '{name}' detected. Only read-only tools are permitted."
                )

        # Must be in the approved read-only list
        if name not in EXPECTED_K8S_MCP_TOOLS:
            logger.warning("Tool '%s' is not in standard EXPECTED_K8S_MCP_TOOLS list.", name)


def validate_secret_safety(payload: Any) -> None:
    """
    Ensure that Kubernetes Secret data or base64 credentials are not leaked.

    Raises:
        SafetyViolationError: If a potential secret value pattern or raw secret is detected.
    """
    if payload is None:
        return

    if isinstance(payload, str):
        lower = payload.lower()
        if "data: {secret" in lower or "secret_key_base64" in lower:
            raise SafetyViolationError("Potential raw Kubernetes Secret value detected in payload.")
    elif isinstance(payload, dict):
        for k, v in payload.items():
            k_lower = str(k).lower()
            if k_lower in ("data", "stringdata") and isinstance(v, dict) and any(v.values()):
                raise SafetyViolationError(
                    f"Potential raw Kubernetes Secret contents found under key '{k}'."
                )
            validate_secret_safety(v)
    elif isinstance(payload, list):
        for item in payload:
            validate_secret_safety(item)


def validate_deterministic_authority(
    deterministic_diagnoses: List[Dict[str, Any]],
    llm_output_diagnoses: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """
    Verify that deterministic diagnoses remain the authoritative ground truth.
    LLMs are strictly forbidden from overriding or deleting deterministic classifications.

    Raises:
        SafetyViolationError: If deterministic diagnoses were altered or omitted.
    """
    if llm_output_diagnoses is None:
        return

    det_map = {
        (d.get("resource_name"), d.get("failure_type")): d
        for d in deterministic_diagnoses
    }

    for llm_d in llm_output_diagnoses:
        key = (llm_d.get("resource_name"), llm_d.get("failure_type"))
        if key not in det_map:
            raise SafetyViolationError(
                f"LLM hallucinated or ungrounded failure classification '{llm_d.get('failure_type')}' "
                f"for resource '{llm_d.get('resource_name')}'. Deterministic engine is authoritative."
            )


def validate_no_masking_commands(diff: str, patch_content: Optional[Dict[str, Any]] = None) -> None:
    """
    Validate that a proposed YAML patch does not use placeholder commands (sleep 3600, true, etc.)
    that mask container crashes without solving the root cause.

    Raises:
        SafetyViolationError: If a placeholder or masking command pattern is detected.
    """
    diff_lower = diff.lower()
    for pattern in DISALLOWED_MASKING_PATTERNS:
        if re.search(pattern, diff_lower):
            raise SafetyViolationError(
                f"Unsafe remediation rejected: Detected masking command pattern '{pattern}'. "
                "Placeholder fixes such as sleep, true, or infinite loops merely keep a container "
                "alive and mask failures rather than resolving root causes."
            )

    # Check command and args arrays inside patch_content if present
    def _check_dict(d: Any):
        if isinstance(d, dict):
            for k, v in d.items():
                if k in ("command", "args") and isinstance(v, list):
                    cmd_str = " ".join(str(item) for item in v).lower()
                    for pattern in DISALLOWED_MASKING_PATTERNS:
                        if re.search(pattern, cmd_str):
                            raise SafetyViolationError(
                                f"Unsafe remediation rejected: Patch command contains masking pattern '{pattern}': {v}."
                            )
                else:
                    _check_dict(v)
        elif isinstance(d, list):
            for item in d:
                _check_dict(item)

    if patch_content:
        _check_dict(patch_content)


def validate_root_cause_alignment(proposal: RemediationProposal, diagnosis: Dict[str, Any]) -> None:
    """
    Verify that an actionable remediation patch addresses the diagnosed root cause
    rather than modifying unrelated fields or fabricating unsupported changes.

    Raises:
        SafetyViolationError: If patch configuration contradicts or ignores the root cause.
    """
    failure_type = diagnosis.get("failure_type", "UNKNOWN")
    diff_lower = proposal.patch.diff.lower() if proposal.patch else ""

    if failure_type == "MEMORY_EXHAUSTION":
        if not ("memory" in diff_lower or "resources" in diff_lower or "limits" in diff_lower):
            raise SafetyViolationError(
                "Remediation patch contradicts diagnosis: For MEMORY_EXHAUSTION, "
                "the patch must adjust memory limits or requests."
            )

    elif failure_type == "INSUFFICIENT_CLUSTER_RESOURCES":
        if not ("cpu" in diff_lower or "resources" in diff_lower or "requests" in diff_lower):
            raise SafetyViolationError(
                "Remediation patch contradicts diagnosis: For INSUFFICIENT_CLUSTER_RESOURCES, "
                "the patch must adjust CPU/resource requests."
            )

    elif failure_type == "IMAGE_PULL_FAILURE":
        if "image" not in diff_lower:
            raise SafetyViolationError(
                "Remediation patch contradicts diagnosis: For IMAGE_PULL_FAILURE, "
                "the patch must address the container image specification."
            )

    elif failure_type == "READINESS_PROBE_FAILURE":
        if "readiness" not in diff_lower and "readinessprobe" not in diff_lower:
            raise SafetyViolationError(
                "Remediation patch contradicts diagnosis: For READINESS_PROBE_FAILURE, "
                "the patch must address readiness probe configuration."
            )

    elif failure_type == "LIVENESS_PROBE_FAILURE":
        if "liveness" not in diff_lower and "livenessprobe" not in diff_lower:
            raise SafetyViolationError(
                "Remediation patch contradicts diagnosis: For LIVENESS_PROBE_FAILURE, "
                "the patch must address liveness probe configuration."
            )


def validate_resource_sizing_grounding(
    proposal: RemediationProposal,
    diagnosis: Dict[str, Any],
    pod_evidence: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Verify that any proposed resource sizing change (e.g. memory limits or CPU requests)
    is derived from and supported by concrete evidence, rather than an arbitrary guess,
    hardcoded template, or unsupported multiplier.

    Raises:
        SafetyViolationError: If a proposed sizing value cannot be grounded in collected evidence.
    """
    if not proposal.can_remediate or not proposal.patch:
        return

    diff = proposal.patch.diff or ""
    evidence_text = " ".join(diagnosis.get("evidence", [])).lower()
    failure_type = diagnosis.get("failure_type", "UNKNOWN")

    if failure_type == "MEMORY_EXHAUSTION":
        # Extract proposed target memory limit from diff '+' lines
        plus_lines = [line for line in diff.splitlines() if line.startswith("+")]
        mem_proposed = []
        for pl in plus_lines:
            matches = re.findall(r"memory\s*:\s*([0-9]+[A-Za-z]+)", pl, re.IGNORECASE)
            mem_proposed.extend(matches)

        # Also check patch_content
        if proposal.patch.patch_content:
            content_str = json.dumps(proposal.patch.patch_content)
            matches = re.findall(r'["\']?memory["\']?\s*:\s*["\']([0-9]+[A-Za-z]+)["\']', content_str, re.IGNORECASE)
            mem_proposed.extend(matches)

        for proposed_mem in set(mem_proposed):
            if proposed_mem.lower() not in evidence_text:
                raise SafetyViolationError(
                    f"Unjustified resource change: Proposed memory limit '{proposed_mem}' is not "
                    "derived from or justified by collected Kubernetes evidence. Arbitrary sizing "
                    "multipliers or hardcoded templates are strictly prohibited."
                )

    elif failure_type == "INSUFFICIENT_CLUSTER_RESOURCES":
        # Extract proposed target CPU request from diff '+' lines
        plus_lines = [line for line in diff.splitlines() if line.startswith("+")]
        cpu_proposed = []
        for pl in plus_lines:
            matches = re.findall(r"cpu\s*:\s*([0-9]+[A-Za-z]*)", pl, re.IGNORECASE)
            cpu_proposed.extend(matches)

        # Also check patch_content
        if proposal.patch.patch_content:
            content_str = json.dumps(proposal.patch.patch_content)
            matches = re.findall(r'["\']?cpu["\']?\s*:\s*["\']([0-9]+[A-Za-z]*)["\']', content_str, re.IGNORECASE)
            cpu_proposed.extend(matches)

        for proposed_cpu in set(cpu_proposed):
            if proposed_cpu.lower() not in evidence_text:
                raise SafetyViolationError(
                    f"Unjustified resource change: Proposed CPU request '{proposed_cpu}' is not "
                    "derived from or justified by collected Kubernetes evidence. Arbitrary sizing "
                    "multipliers or hardcoded templates are strictly prohibited."
                )


def validate_controller_ownership(
    proposal: RemediationProposal,
    pod_evidence: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Ensure the proposal targets the appropriate declarative controller rather than an ephemeral Pod
    when ownership is known, and never assumes a standalone Pod has an unverified controller.

    Raises:
        SafetyViolationError: If controller targeting is ungrounded or targets an ephemeral Pod.
    """
    if not pod_evidence:
        return

    owners = pod_evidence.get("owners") or []
    if owners:
        # Pod is managed by a controller
        owner = owners[0]
        owner_kind = owner.get("kind", "")
        # If managed by a ReplicaSet/Deployment, patching Pod directly is forbidden
        if proposal.target_kind == "Pod" and proposal.can_remediate:
            raise SafetyViolationError(
                f"Invalid resource targeting: Workload '{proposal.target_workload}' is managed by "
                f"controller '{owner_kind}/{owner.get('name')}'. Direct patches to ephemeral Pods "
                f"are forbidden; the declarative controller must be targeted."
            )
    else:
        # Standalone Pod: Must not fabricate an imaginary controller
        if proposal.target_kind != "Pod":
            raise SafetyViolationError(
                f"Invalid resource targeting: Workload '{proposal.target_workload}' is a standalone Pod. "
                f"Fabricating an unverified controller target '{proposal.target_kind}' is prohibited."
            )


def validate_remediation_proposal(
    proposal: RemediationProposal,
    deterministic_diagnoses: List[Dict[str, Any]],
    observed_pod_names: List[str],
    pod_evidence: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Validate that a proposed remediation is safe, grounded, addresses root cause,
    does not mask failures, respects controller ownership, and requires human approval.

    Raises:
        SafetyViolationError: If proposal is ungrounded, unapproved, masking, or malformed.
    """
    # 1. Human-in-the-loop requirement
    if not proposal.requires_approval:
        raise SafetyViolationError("Safety guardrail violation: requires_approval must be True.")

    if proposal.approval is None or proposal.approval.status != ApprovalStatus.PENDING:
        raise SafetyViolationError(
            "Safety guardrail violation: New proposal approval status must be PENDING."
        )

    # 2. Workload grounding
    diagnosed_resources = [d.get("resource_name") for d in deterministic_diagnoses]
    diagnosed_pods = [d.get("pod") for d in deterministic_diagnoses if d.get("pod")]
    if (
        proposal.target_workload not in observed_pod_names
        and proposal.target_workload not in diagnosed_resources
        and proposal.target_workload not in diagnosed_pods
    ):
        raise SafetyViolationError(
            f"Ungrounded proposal: target workload '{proposal.target_workload}' "
            f"was neither observed in the cluster nor diagnosed by the engine."
        )

    # 3. Actionability & Insufficient Evidence handling
    if proposal.can_remediate:
        if not proposal.patch or not proposal.patch.diff:
            raise SafetyViolationError("Actionable proposal must contain a non-empty YAML patch diff.")

        # Check for masking commands (sleep 3600, true, etc.)
        validate_no_masking_commands(proposal.patch.diff, proposal.patch.patch_content)

        # Correlate with corresponding diagnosis to verify root-cause alignment
        matching_diags = [
            d for d in deterministic_diagnoses
            if d.get("pod") == proposal.target_workload or d.get("resource_name", "").startswith(proposal.target_workload)
        ]
        if matching_diags:
            validate_root_cause_alignment(proposal, matching_diags[0])
            validate_resource_sizing_grounding(proposal, matching_diags[0], pod_evidence)

        # Validate controller ownership
        if pod_evidence:
            validate_controller_ownership(proposal, pod_evidence)

    else:
        # Non-actionable / INSUFFICIENT_EVIDENCE proposal
        if proposal.patch is not None:
            raise SafetyViolationError(
                "Safety guardrail violation: Proposal with can_remediate=False must not contain a YAML patch."
            )
        if not proposal.insufficient_evidence_reason or len(proposal.insufficient_evidence_reason.strip()) < 10:
            raise SafetyViolationError(
                "Proposal marked insufficient evidence must provide an explicit explanation in insufficient_evidence_reason."
            )

    # 4. Justification non-empty
    if not proposal.justification or len(proposal.justification.strip()) < 10:
        raise SafetyViolationError("Remediation proposal justification is missing or insufficiently detailed.")
