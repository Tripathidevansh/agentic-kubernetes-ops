"""
Gemini Remediation Planner for Safe GitOps Operations.

Interprets grounded deterministic diagnoses and actual workload configurations
to propose declarative YAML patches targeting appropriate controllers (Deployment,
StatefulSet, etc.) or standalone Pods, with risk classification and strict
human-in-the-loop review guardrails. Rejects masking commands and returns
INSUFFICIENT_EVIDENCE when safe automated fixes cannot be determined.
"""

from datetime import datetime, timezone
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional
import uuid

from google.genai import Client, types

from app.mcp_client import K8sMcpClientBridge
from app.models.remediation import (
    ApprovalStatus,
    HumanApproval,
    ProposalProvenance,
    ProposalStatus,
    RemediationProposal,
    RiskLevel,
    VerificationPlan,
    YamlPatch,
)
from app.orchestrator.prompts import REMEDIATION_AGENT_INSTRUCTION
from app.validators.safety_validator import (
    SafetyViolationError,
    validate_no_masking_commands,
    validate_remediation_proposal,
    validate_resource_sizing_grounding,
)

logger = logging.getLogger(__name__)

DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"

DEFAULT_RETRY_OPTIONS = types.HttpRetryOptions(
    attempts=3,
    initial_delay=1.0,
    max_delay=8.0,
    http_status_codes=[408, 429, 500, 502, 503, 504],
)


def resolve_workload_target(
    pod_name: str,
    pod_evidence: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Resolve the declarative workload target (controller vs standalone Pod).

    Returns a dict with:
    - target_kind: "Deployment", "StatefulSet", "DaemonSet", "Job", or "Pod"
    - target_name: name of the controller or pod
    - is_standalone_pod: bool
    - owner_kind: Optional[str]
    - owner_name: Optional[str]
    """
    if not pod_evidence:
        return {
            "target_kind": "Pod",
            "target_name": pod_name,
            "is_standalone_pod": True,
            "owner_kind": None,
            "owner_name": None,
        }

    owners = pod_evidence.get("owners") or []
    if not owners:
        return {
            "target_kind": "Pod",
            "target_name": pod_name,
            "is_standalone_pod": True,
            "owner_kind": None,
            "owner_name": None,
        }

    owner = owners[0]
    owner_kind = owner.get("kind", "")
    owner_name = owner.get("name", "")

    if owner_kind == "ReplicaSet":
        # In Kubernetes Deployments, ReplicaSets are named <deployment-name>-<pod-template-hash>
        # (hash is typically 8-10 alphanumeric characters)
        parts = owner_name.rsplit("-", 1)
        if len(parts) == 2 and 8 <= len(parts[1]) <= 10:
            deployment_name = parts[0]
        else:
            deployment_name = owner_name

        return {
            "target_kind": "Deployment",
            "target_name": deployment_name,
            "is_standalone_pod": False,
            "owner_kind": "ReplicaSet",
            "owner_name": owner_name,
        }
    elif owner_kind in ("Deployment", "StatefulSet", "DaemonSet", "Job"):
        return {
            "target_kind": owner_kind,
            "target_name": owner_name,
            "is_standalone_pod": False,
            "owner_kind": owner_kind,
            "owner_name": owner_name,
        }

    return {
        "target_kind": owner_kind,
        "target_name": owner_name,
        "is_standalone_pod": False,
        "owner_kind": owner_kind,
        "owner_name": owner_name,
    }


def _extract_evidence_target_memory(evidence_list: List[str]) -> Optional[str]:
    """Extract concrete target memory sizing if explicitly specified in evidence."""
    pattern = re.compile(
        r"\b(?:required|recommended|target|profiled|sizing)\b\s*(?:memory\b\s*)?(?:limit\b\s*)?[:=]?\s*([0-9]+[A-Za-z]+)",
        re.IGNORECASE,
    )
    for ev in evidence_list:
        match = pattern.search(ev)
        if match:
            return match.group(1)
    return None


def _extract_evidence_target_cpu(evidence_list: List[str]) -> Optional[str]:
    """Extract concrete target CPU request sizing if explicitly specified in evidence."""
    pattern = re.compile(
        r"\b(?:required|recommended|target|allocatable|sizing)\b\s*(?:cpu\b\s*)?(?:request\b\s*)?[:=]?\s*([0-9]+[A-Za-z]*)",
        re.IGNORECASE,
    )
    for ev in evidence_list:
        match = pattern.search(ev)
        if match:
            return match.group(1)
    return None


class RemediationPlanner:
    """
    Formulates safe, grounded declarative YAML configuration remediations
    based on authoritative deterministic diagnoses and workload controller hierarchy.
    """

    def __init__(
        self,
        bridge: Optional[K8sMcpClientBridge] = None,
        model_name: Optional[str] = None,
        retry_options: Optional[types.HttpRetryOptions] = None,
    ):
        self.bridge = bridge
        self.model_name = model_name or os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        self.retry_options = retry_options or DEFAULT_RETRY_OPTIONS

    async def plan_remediations(
        self,
        diagnoses: List[Dict[str, Any]],
        namespace: str,
        observed_pods: Optional[List[Dict[str, Any]]] = None,
        use_llm: bool = True,
    ) -> List[RemediationProposal]:
        """
        Generate remediation proposals for a list of authoritative diagnoses.

        Args:
            diagnoses: Authoritative deterministic diagnoses.
            namespace: Target Kubernetes namespace.
            observed_pods: List of observed Pod telemetry dicts.
            use_llm: Whether to invoke Gemini (requires GEMINI_API_KEY).
                     If False, uses grounded rule-based proposal templates.

        Returns:
            List of validated RemediationProposal objects.
        """
        if not diagnoses:
            return []

        observed_pod_names = [p.get("pod") for p in (observed_pods or []) if p.get("pod")]
        proposals: List[RemediationProposal] = []

        api_key = os.environ.get("GEMINI_API_KEY")

        for diag in diagnoses:
            failure_type = diag.get("failure_type", "UNKNOWN")
            resource_name = diag.get("resource_name", "unknown")
            pod_name = diag.get("pod") or resource_name.split("/")[0]

            # Find matching pod evidence
            pod_evidence = None
            if observed_pods:
                for p in observed_pods:
                    if p.get("pod") == pod_name:
                        pod_evidence = p
                        break

            # Resolve controller ownership
            target_info = resolve_workload_target(pod_name, pod_evidence)

            # 1. Fetch current resource configuration via MCP if bridge is available
            resource_config: Optional[Dict[str, Any]] = None
            if self.bridge and self.bridge.is_connected:
                config_res = await self.bridge.call_tool(
                    "get_resource_config",
                    {"namespace": namespace, "pod_name": pod_name},
                )
                if config_res.get("success"):
                    resource_config = config_res.get("data")

            # 2. Plan remediation via Gemini if enabled and API key is present
            proposal: Optional[RemediationProposal] = None
            if use_llm and api_key:
                try:
                    proposal = await self._plan_with_gemini(
                        diag=diag,
                        namespace=namespace,
                        pod_name=pod_name,
                        target_info=target_info,
                        resource_config=resource_config,
                        pod_evidence=pod_evidence,
                    )
                except Exception as exc:
                    logger.warning("Gemini remediation planning failed (%s), falling back to grounded template", exc)
                    proposal = None

            # 3. Grounded fallback generator (ensures deterministic reliability without API key)
            if proposal is None:
                proposal = self._generate_grounded_proposal(
                    diag=diag,
                    namespace=namespace,
                    pod_name=pod_name,
                    target_info=target_info,
                    resource_config=resource_config,
                    pod_evidence=pod_evidence,
                )

            # 4. Strict safety validation
            if proposal:
                validate_remediation_proposal(
                    proposal=proposal,
                    deterministic_diagnoses=diagnoses,
                    observed_pod_names=observed_pod_names or [pod_name],
                    pod_evidence=pod_evidence,
                )
                proposals.append(proposal)

        return proposals

    async def _plan_with_gemini(
        self,
        diag: Dict[str, Any],
        namespace: str,
        pod_name: str,
        target_info: Dict[str, Any],
        resource_config: Optional[Dict[str, Any]],
        pod_evidence: Optional[Dict[str, Any]],
    ) -> Optional[RemediationProposal]:
        """Invoke Gemini to formulate a structured remediation proposal."""
        client = Client(http_options=types.HttpOptions(retry_options=self.retry_options))

        prompt = f"""
Deterministic Diagnosis:
- Resource: {diag.get('resource_name')}
- Failure Type: {diag.get('failure_type')}
- Severity: {diag.get('severity')}
- Root Cause: {diag.get('root_cause')}
- Evidence: {json.dumps(diag.get('evidence', []))}
- Recommended Action: {diag.get('recommended_action')}

Target Resource Ownership:
- Target Kind: {target_info['target_kind']}
- Target Name: {target_info['target_name']}
- Is Standalone Pod: {target_info['is_standalone_pod']}
- Owning Controller: {target_info.get('owner_kind')}/{target_info.get('owner_name')}

Observed Workload Configuration:
{json.dumps(resource_config or {}, indent=2)}

Task:
Propose a declarative YAML patch and unified diff to remediate this incident, OR return INSUFFICIENT_EVIDENCE if safe automated remediation cannot be formulated without inventing commands, images, or configuration.
NEVER propose placeholder commands like 'sleep 3600', 'true', or commands that merely keep a container alive.
NEVER propose arbitrary resource sizing (such as guessing memory limits or CPU requests) unless the required sizing is explicitly justified by and present in the collected evidence. If evidence does not provide the required sizing, return INSUFFICIENT_EVIDENCE.

Respond in strict JSON with keys:
{{
  "status": "ACTIONABLE" | "INSUFFICIENT_EVIDENCE",
  "can_remediate": true | false,
  "insufficient_evidence_reason": "<explanation if can_remediate is false, otherwise null>",
  "justification": "<concise technical justification aligned with root cause>",
  "risk_level": "LOW" | "MEDIUM" | "HIGH",
  "diff": "<unified diff string, or null if can_remediate is false>",
  "patch_content": {{ ... JSON structure of patch, or null if can_remediate is false ... }}
}}
"""
        response = await client.aio.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=REMEDIATION_AGENT_INSTRUCTION,
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )

        if not response.text:
            return None

        data = json.loads(response.text)
        status_str = str(data.get("status", "ACTIONABLE")).upper()
        can_rem = bool(data.get("can_remediate", True))

        # Check for insufficient evidence
        if status_str == "INSUFFICIENT_EVIDENCE" or not can_rem:
            return RemediationProposal(
                target_workload=pod_name,
                target_kind=target_info["target_kind"],
                target_name=target_info["target_name"],
                target_namespace=namespace,
                status=ProposalStatus.INSUFFICIENT_EVIDENCE,
                can_remediate=False,
                insufficient_evidence_reason=data.get(
                    "insufficient_evidence_reason",
                    "Remediation withheld due to insufficient supporting evidence."
                ),
                patch=None,
                justification=data.get("justification", "Remediation withheld: manual operator intervention required."),
                risk_level=RiskLevel.MEDIUM,
                requires_approval=True,
                approval=HumanApproval(status=ApprovalStatus.PENDING),
                provenance=ProposalProvenance.GEMINI_AGENT,
                model_metadata={"model": self.model_name, "provider": "google_genai"},
            )

        diff = data.get("diff", "")
        patch_content = data.get("patch_content", {})

        # Strict check against placeholder/masking commands from LLM
        try:
            validate_no_masking_commands(diff, patch_content)
        except SafetyViolationError as exc:
            logger.warning("Gemini proposed an unsafe masking command: %s. Rejecting proposal.", exc)
            return RemediationProposal(
                target_workload=pod_name,
                target_kind=target_info["target_kind"],
                target_name=target_info["target_name"],
                target_namespace=namespace,
                status=ProposalStatus.REJECTED_SAFETY_VIOLATION,
                can_remediate=False,
                insufficient_evidence_reason=f"LLM proposed unsafe masking command: {exc}",
                patch=None,
                justification="Remediation rejected: proposed change masks failure rather than solving root cause.",
                risk_level=RiskLevel.HIGH,
                requires_approval=True,
                approval=HumanApproval(status=ApprovalStatus.PENDING),
                provenance=ProposalProvenance.GEMINI_AGENT,
                model_metadata={"model": self.model_name, "provider": "google_genai"},
            )

        risk_str = str(data.get("risk_level", "MEDIUM")).upper()
        risk_level = RiskLevel[risk_str] if risk_str in RiskLevel.__members__ else RiskLevel.MEDIUM

        yaml_patch = YamlPatch(
            target_kind=target_info["target_kind"],
            target_name=target_info["target_name"],
            target_namespace=namespace,
            diff=diff,
            patch_content=patch_content,
        )

        # Strict check against ungrounded resource sizing from LLM
        try:
            temp_prop = RemediationProposal(
                target_workload=pod_name,
                target_kind=target_info["target_kind"],
                target_name=target_info["target_name"],
                target_namespace=namespace,
                status=ProposalStatus.ACTIONABLE,
                can_remediate=True,
                patch=yaml_patch,
                justification="Preliminary LLM proposal",
                risk_level=risk_level,
                requires_approval=True,
                approval=HumanApproval(status=ApprovalStatus.PENDING),
            )
            validate_resource_sizing_grounding(temp_prop, diag, pod_evidence)
        except SafetyViolationError as exc:
            logger.warning("Gemini proposed an ungrounded resource sizing: %s. Rejecting proposal.", exc)
            return RemediationProposal(
                target_workload=pod_name,
                target_kind=target_info["target_kind"],
                target_name=target_info["target_name"],
                target_namespace=namespace,
                status=ProposalStatus.REJECTED_SAFETY_VIOLATION,
                can_remediate=False,
                insufficient_evidence_reason=f"LLM proposed ungrounded resource sizing: {exc}",
                patch=None,
                justification="Remediation rejected: proposed resource sizing is not supported by collected evidence.",
                risk_level=RiskLevel.HIGH,
                requires_approval=True,
                approval=HumanApproval(status=ApprovalStatus.PENDING),
                provenance=ProposalProvenance.GEMINI_AGENT,
                model_metadata={"model": self.model_name, "provider": "google_genai"},
            )

        return RemediationProposal(
            target_workload=pod_name,
            target_kind=target_info["target_kind"],
            target_name=target_info["target_name"],
            target_namespace=namespace,
            status=ProposalStatus.ACTIONABLE,
            can_remediate=True,
            patch=yaml_patch,
            justification=data.get("justification", diag.get("recommended_action", "Remediation recommended")),
            risk_level=risk_level,
            requires_approval=True,
            approval=HumanApproval(status=ApprovalStatus.PENDING),
            provenance=ProposalProvenance.GEMINI_AGENT,
            model_metadata={"model": self.model_name, "provider": "google_genai"},
        )

    def _generate_grounded_proposal(
        self,
        diag: Dict[str, Any],
        namespace: str,
        pod_name: str,
        target_info: Dict[str, Any],
        resource_config: Optional[Dict[str, Any]],
        pod_evidence: Optional[Dict[str, Any]],
    ) -> RemediationProposal:
        """
        Generate a safe, grounded remediation proposal template based strictly
        on the deterministic diagnosis and observed configuration. Returns
        INSUFFICIENT_EVIDENCE whenever an automated fix cannot safely be determined
        without inventing commands, images, or configurations.
        """
        failure_type = diag.get("failure_type", "UNKNOWN")
        container_name = diag.get("container") or "app"
        target_kind = target_info["target_kind"]
        target_name = target_info["target_name"]

        # --------------------------------------------------------------------
        # Resource Adjustments (Strictly Grounded in Evidence)
        # --------------------------------------------------------------------
        if failure_type == "MEMORY_EXHAUSTION":
            target_mem = _extract_evidence_target_memory(diag.get("evidence", []))
            if not target_mem:
                reason = (
                    f"Container '{container_name}' terminated due to memory exhaustion (OOMKilled, exit code 137). "
                    "Kubernetes evidence confirms memory exhaustion occurred, but does not provide the workload's "
                    "actual steady-state memory requirement or determine whether the process suffered from an unbounded "
                    "memory leak. Proposing an arbitrary memory limit without heap profiling or workload specification "
                    "is ungrounded. Sizing must be determined by the workload owner."
                )
                justification = (
                    "Memory exhaustion remediation withheld: actual working-set memory requirements cannot be "
                    "determined from Kubernetes OOM events alone. Workload profiling is required."
                )
                return RemediationProposal(
                    target_workload=pod_name,
                    target_kind=target_kind,
                    target_name=target_name,
                    target_namespace=namespace,
                    status=ProposalStatus.INSUFFICIENT_EVIDENCE,
                    can_remediate=False,
                    insufficient_evidence_reason=reason,
                    patch=None,
                    justification=justification,
                    risk_level=RiskLevel.HIGH,
                    requires_approval=True,
                    approval=HumanApproval(status=ApprovalStatus.PENDING),
                    provenance=ProposalProvenance.DETERMINISTIC_FALLBACK,
                    model_metadata={"model": None, "provider": "deterministic_rules"},
                )

            # Grounded in evidence: Target memory limit explicitly specified in evidence
            current_mem = "unspecified"
            if resource_config:
                containers = resource_config.get("containers") or []
                for c in containers:
                    if c.get("name") == container_name:
                        current_mem = c.get("resources", {}).get("limits", {}).get("memory", "unspecified")
                        break

            if target_kind == "Deployment":
                diff = (
                    f"--- spec.template.spec.containers[name='{container_name}'].resources.limits.memory: {current_mem}\n"
                    f"+++ spec.template.spec.containers[name='{container_name}'].resources.limits.memory: {target_mem}"
                )
                patch_content = {
                    "spec": {
                        "template": {
                            "spec": {
                                "containers": [
                                    {
                                        "name": container_name,
                                        "resources": {"limits": {"memory": target_mem}},
                                    }
                                ]
                            }
                        }
                    }
                }
            else:
                diff = (
                    f"--- spec.containers[name='{container_name}'].resources.limits.memory: {current_mem}\n"
                    f"+++ spec.containers[name='{container_name}'].resources.limits.memory: {target_mem}"
                )
                patch_content = {
                    "spec": {
                        "containers": [
                            {
                                "name": container_name,
                                "resources": {"limits": {"memory": target_mem}},
                            }
                        ]
                    }
                }

            yaml_patch = YamlPatch(
                target_kind=target_kind,
                target_name=target_name,
                target_namespace=namespace,
                diff=diff,
                patch_content=patch_content,
            )
            return RemediationProposal(
                target_workload=pod_name,
                target_kind=target_kind,
                target_name=target_name,
                target_namespace=namespace,
                status=ProposalStatus.ACTIONABLE,
                can_remediate=True,
                patch=yaml_patch,
                justification=(
                    f"Increase {target_kind} container '{container_name}' memory limit to {target_mem} "
                    "as justified by collected workload profiling evidence."
                ),
                risk_level=RiskLevel.MEDIUM,
                requires_approval=True,
                approval=HumanApproval(status=ApprovalStatus.PENDING),
                provenance=ProposalProvenance.DETERMINISTIC_FALLBACK,
                model_metadata={"model": None, "provider": "deterministic_rules"},
            )

        elif failure_type == "INSUFFICIENT_CLUSTER_RESOURCES":
            target_cpu = _extract_evidence_target_cpu(diag.get("evidence", []))
            if not target_cpu:
                reason = (
                    f"Workload '{pod_name}' is unschedulable due to insufficient cluster CPU resources. "
                    "Kubernetes scheduling events do not specify the workload's minimum viable CPU requirements "
                    "or whether additional cluster nodes should be provisioned. Proposing an arbitrary CPU reduction "
                    "is ungrounded. Sizing or cluster scaling must be determined by the cluster operator."
                )
                justification = (
                    "Insufficient cluster resources remediation withheld: viable CPU sizing and cluster capacity "
                    "decisions require operator intervention."
                )
                return RemediationProposal(
                    target_workload=pod_name,
                    target_kind=target_kind,
                    target_name=target_name,
                    target_namespace=namespace,
                    status=ProposalStatus.INSUFFICIENT_EVIDENCE,
                    can_remediate=False,
                    insufficient_evidence_reason=reason,
                    patch=None,
                    justification=justification,
                    risk_level=RiskLevel.HIGH,
                    requires_approval=True,
                    approval=HumanApproval(status=ApprovalStatus.PENDING),
                    provenance=ProposalProvenance.DETERMINISTIC_FALLBACK,
                    model_metadata={"model": None, "provider": "deterministic_rules"},
                )

            # Grounded in evidence: Target CPU request explicitly specified in evidence
            current_cpu = "500"
            if resource_config:
                containers = resource_config.get("containers") or []
                for c in containers:
                    if c.get("name") == container_name:
                        current_cpu = c.get("resources", {}).get("requests", {}).get("cpu", "500")
                        break

            if target_kind == "Deployment":
                diff = (
                    f"--- spec.template.spec.containers[name='{container_name}'].resources.requests.cpu: {current_cpu}\n"
                    f"+++ spec.template.spec.containers[name='{container_name}'].resources.requests.cpu: {target_cpu}"
                )
                patch_content = {
                    "spec": {
                        "template": {
                            "spec": {
                                "containers": [
                                    {
                                        "name": container_name,
                                        "resources": {"requests": {"cpu": target_cpu}},
                                    }
                                ]
                            }
                        }
                    }
                }
            else:
                diff = (
                    f"--- spec.containers[name='{container_name}'].resources.requests.cpu: {current_cpu}\n"
                    f"+++ spec.containers[name='{container_name}'].resources.requests.cpu: {target_cpu}"
                )
                patch_content = {
                    "spec": {
                        "containers": [
                            {
                                "name": container_name,
                                "resources": {"requests": {"cpu": target_cpu}},
                            }
                        ]
                    }
                }

            yaml_patch = YamlPatch(
                target_kind=target_kind,
                target_name=target_name,
                target_namespace=namespace,
                diff=diff,
                patch_content=patch_content,
            )
            return RemediationProposal(
                target_workload=pod_name,
                target_kind=target_kind,
                target_name=target_name,
                target_namespace=namespace,
                status=ProposalStatus.ACTIONABLE,
                can_remediate=True,
                patch=yaml_patch,
                justification=(
                    f"Adjust {target_kind} container '{container_name}' CPU request to {target_cpu} "
                    "as justified by collected cluster resource evidence."
                ),
                risk_level=RiskLevel.MEDIUM,
                requires_approval=True,
                approval=HumanApproval(status=ApprovalStatus.PENDING),
                provenance=ProposalProvenance.DETERMINISTIC_FALLBACK,
                model_metadata={"model": None, "provider": "deterministic_rules"},
            )

        # --------------------------------------------------------------------
        # Insufficient Evidence / Developer or Operator Input Required
        # --------------------------------------------------------------------
        elif failure_type == "APPLICATION_PROCESS_FAILURE":
            reason = (
                f"Container '{container_name}' exited with exit code 1 (CrashLoopBackOff). "
                "The intended application entrypoint, runtime arguments, or internal code fix cannot "
                "be determined without application source code and developer intent. Proposing placeholder "
                "commands (e.g. 'sleep 3600' or 'true') is strictly prohibited as it masks the failure."
            )
            justification = (
                "Application internal crash requires developer code or configuration review. "
                "Automated placeholder commands are prohibited."
            )

        elif failure_type == "INVALID_STARTUP_COMMAND":
            reason = (
                f"Container '{container_name}' command or binary does not exist in image. "
                "The valid executable path cannot be determined without container image inspection "
                "or developer specification. Automated replacement with placeholder commands is prohibited."
            )
            justification = "Invalid startup binary requires developer specification of the valid container entrypoint."

        elif failure_type == "IMAGE_PULL_FAILURE":
            reason = (
                f"Container '{container_name}' image pull failed (ImagePullBackOff / ErrImagePull). "
                "The intended image repository, registry credentials, or tag cannot be determined without "
                "operator input. Automated replacement with an arbitrary substitute image (e.g. nginx) is prohibited."
            )
            justification = "Image pull failure requires operator specification of the correct container image and tag."

        elif failure_type == "MISSING_CONFIGMAP":
            reason = (
                "Referenced ConfigMap does not exist in namespace. "
                "The required configuration keys, values, and schema cannot be fabricated by the agent "
                "and must be provisioned out-of-band by the operator."
            )
            justification = "Missing ConfigMap must be created out-of-band by operator with valid business configuration."

        elif failure_type == "MISSING_SECRET":
            reason = (
                "Referenced Secret does not exist in namespace. "
                "Secret credentials and keys cannot be fabricated by the agent and must be securely "
                "provisioned out-of-band by the operator."
            )
            justification = "Missing Secret must be created out-of-band by operator with authorized credentials."

        elif failure_type == "UNBOUND_PVC":
            reason = (
                "PersistentVolumeClaim references nonexistent StorageClass. "
                "The appropriate cluster StorageClass or PersistentVolume provisioning policy cannot "
                "be determined without cluster storage configuration evidence."
            )
            justification = "Unbound PVC requires operator specification of an available StorageClass or PersistentVolume."

        elif failure_type == "LIVENESS_PROBE_FAILURE":
            reason = (
                f"Liveness probe for container '{container_name}' is failing. "
                "Replacing the probe with a placeholder like 'true' masks the failure. The valid health "
                "check command or endpoint must be specified by application developers."
            )
            justification = "Liveness probe failure requires application-level health check verification."

        elif failure_type == "READINESS_PROBE_FAILURE":
            reason = (
                f"Readiness probe for container '{container_name}' returned failure. "
                "The valid application HTTP health route must be verified against application routing documentation."
            )
            justification = "Readiness probe endpoint must be aligned with application routing."

        else:
            reason = f"Failure category '{failure_type}' requires manual operator inspection; no automated patch is supported."
            justification = "Manual investigation required due to unknown or unclassified failure pattern."

        return RemediationProposal(
            target_workload=pod_name,
            target_kind=target_kind,
            target_name=target_name,
            target_namespace=namespace,
            status=ProposalStatus.INSUFFICIENT_EVIDENCE,
            can_remediate=False,
            insufficient_evidence_reason=reason,
            patch=None,
            justification=justification,
            risk_level=RiskLevel.HIGH,
            requires_approval=True,
            approval=HumanApproval(status=ApprovalStatus.PENDING),
            provenance=ProposalProvenance.DETERMINISTIC_FALLBACK,
            model_metadata={"model": None, "provider": "deterministic_rules"},
        )
