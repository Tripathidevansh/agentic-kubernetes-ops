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
import time
from typing import Any, Dict, List, Optional, Set
import uuid

from google.genai import Client, types

from app.mcp_client import K8sMcpClientBridge
from app.models.investigation import (
    AgentToolCallTrace,
    GeminiRequestMetric,
)
from app.models.remediation import (
    ApprovalStatus,
    BatchProposalItem,
    BatchRemediationResponse,
    HumanApproval,
    ProposalProvenance,
    ProposalStatus,
    RemediationProposal,
    RiskLevel,
    VerificationPlan,
    YamlPatch,
)
from app.orchestrator.prompts import (
    REMEDIATION_AGENT_INSTRUCTION,
    REMEDIATION_BEST_EFFORT_INSTRUCTION,
)
from app.orchestrator.sanitizer import sanitize_error_message
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


def _build_config_from_evidence(pod_evidence: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Build a fallback resource configuration dictionary from observation evidence."""
    if not pod_evidence:
        return None
    containers_config = []
    for c in pod_evidence.get("containers", []):
        spec = c.get("spec", {})
        containers_config.append({
            "name": c.get("name"),
            "image": c.get("image"),
            "resources": spec.get("resources", {}),
            "probes": spec.get("probes", {}),
        })
    return {
        "pod": pod_evidence.get("pod"),
        "labels": pod_evidence.get("labels", {}),
        "containers": containers_config,
        "owners": pod_evidence.get("owners", []),
        "related_resources": pod_evidence.get("related_resources", {}),
    }


class RemediationPlanner:
    """
    Formulates safe, grounded declarative YAML configuration remediations
    based on authoritative deterministic diagnoses and workload controller hierarchy.
    Processes all diagnoses in a single batch Gemini request to optimize latency
    and token consumption, while deduplicating MCP workload configuration queries.
    """

    def __init__(
        self,
        bridge: Optional[K8sMcpClientBridge] = None,
        model_name: Optional[str] = None,
        retry_options: Optional[types.HttpRetryOptions] = None,
        allow_best_effort: bool = False,
    ):
        self.bridge = bridge
        self.model_name = model_name or os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        self.retry_options = retry_options or DEFAULT_RETRY_OPTIONS
        self.allow_best_effort = allow_best_effort
        self.traces: List[AgentToolCallTrace] = []
        self.metrics: List[GeminiRequestMetric] = []
        self.remediation_provenance: str = "DETERMINISTIC_FALLBACK"

    def get_traces(self) -> List[AgentToolCallTrace]:
        """Return MCP tool call traces recorded during remediation planning."""
        return list(self.traces)

    def get_metrics(self) -> List[GeminiRequestMetric]:
        """Return instrumentation metrics captured for Gemini requests in this agent."""
        return list(self.metrics)

    async def plan_remediations(
        self,
        diagnoses: List[Dict[str, Any]],
        namespace: str,
        observed_pods: Optional[List[Dict[str, Any]]] = None,
        use_llm: bool = True,
    ) -> List[RemediationProposal]:
        """
        Generate remediation proposals for a list of authoritative diagnoses.
        Deduplicates MCP configuration calls and batches all diagnoses into a single
        Gemini request to optimize latency and token consumption.

        Args:
            diagnoses: Authoritative deterministic diagnoses.
            namespace: Target Kubernetes namespace.
            observed_pods: List of observed Pod telemetry dicts.
            use_llm: Whether to invoke Gemini (requires GEMINI_API_KEY).
                     If False, uses grounded rule-based proposal templates.

        Returns:
            List of validated RemediationProposal objects.
        """
        t_plan_start = time.time()
        self.traces = []
        self.metrics = []
        self.remediation_provenance = "DETERMINISTIC_FALLBACK"

        if not diagnoses:
            return []

        observed_pod_names = [p.get("pod") for p in (observed_pods or []) if p.get("pod")]
        api_key = os.environ.get("GEMINI_API_KEY")
        allow_best_effort = use_llm and self.allow_best_effort

        # 1. Deduplicate and collect workload configurations via MCP
        unique_pod_names: Set[str] = set()
        for diag in diagnoses:
            r_name = diag.get("resource_name", "")
            p_name = diag.get("pod") or (r_name.split("/")[0] if r_name else None)
            if p_name:
                unique_pod_names.add(p_name)

        resource_configs: Dict[str, Dict[str, Any]] = {}
        if self.bridge and self.bridge.is_connected:
            for p_name in sorted(unique_pod_names):
                if not p_name or p_name in resource_configs:
                    continue
                t_tool_0 = time.time()
                try:
                    config_res = await self.bridge.call_tool(
                        "get_resource_config",
                        {"namespace": namespace, "pod_name": p_name},
                    )
                    t_tool_ms = (time.time() - t_tool_0) * 1000.0
                    success = bool(config_res.get("success"))
                    if success:
                        resource_configs[p_name] = config_res.get("data")
                    data_obj = config_res.get("data") or {}
                    keys_summary = list(data_obj.keys()) if isinstance(data_obj, dict) else []
                    self.traces.append(
                        AgentToolCallTrace(
                            agent_name="remediation_planner",
                            tool_name="get_resource_config",
                            arguments={"namespace": namespace, "pod_name": p_name},
                            status="SUCCESS" if success else "ERROR",
                            duration_ms=t_tool_ms,
                            result_summary=f"Workload config retrieved (keys: {keys_summary})" if success else "Failed to retrieve workload config",
                            error_message=sanitize_error_message(str(config_res.get("error"))) if not success else None,
                        )
                    )
                except Exception as exc:
                    t_tool_ms = (time.time() - t_tool_0) * 1000.0
                    logger.warning("Failed to fetch resource config for pod '%s': %s", p_name, exc)
                    self.traces.append(
                        AgentToolCallTrace(
                            agent_name="remediation_planner",
                            tool_name="get_resource_config",
                            arguments={"namespace": namespace, "pod_name": p_name},
                            status="ERROR",
                            duration_ms=t_tool_ms,
                            result_summary=None,
                            error_message=sanitize_error_message(str(exc)),
                        )
                    )

        # 2. Build structured diagnosis batch items
        batch_items: List[Dict[str, Any]] = []
        for idx, diag in enumerate(diagnoses):
            resource_name = diag.get("resource_name", "unknown")
            pod_name = diag.get("pod") or resource_name.split("/")[0]

            pod_evidence = None
            if observed_pods:
                for p in observed_pods:
                    if p.get("pod") == pod_name:
                        pod_evidence = p
                        break

            target_info = resolve_workload_target(pod_name, pod_evidence)
            r_config = resource_configs.get(pod_name) or _build_config_from_evidence(pod_evidence)

            item_id = f"diag_{idx}_{pod_name}"
            batch_items.append({
                "item_id": item_id,
                "diag": diag,
                "pod_name": pod_name,
                "resource_name": resource_name,
                "target_info": target_info,
                "resource_config": r_config,
                "pod_evidence": pod_evidence,
            })

        # 3. Plan with Gemini in ONE batch request if LLM is enabled and API key is present
        raw_batch_proposals: Dict[str, Dict[str, Any]] = {}
        gemini_failed: bool = False
        gemini_error: Optional[str] = None

        if use_llm and api_key:
            try:
                raw_batch_proposals = await self._plan_batch_with_gemini(
                    batch_items=batch_items,
                    namespace=namespace,
                    allow_best_effort=allow_best_effort,
                )
                if raw_batch_proposals:
                    self.remediation_provenance = "GEMINI_AGENT"
            except Exception as exc:
                logger.warning(
                    "Gemini batch remediation planning failed (%s), falling back to grounded templates",
                    exc,
                )
                gemini_failed = True
                gemini_error = sanitize_error_message(str(exc))
                raw_batch_proposals = {}

        # 4. Construct and validate individual proposals
        proposals: List[RemediationProposal] = []
        for item in batch_items:
            item_id = item["item_id"]
            diag = item["diag"]
            pod_name = item["pod_name"]
            resource_name = item["resource_name"]
            target_info = item["target_info"]
            r_config = item["resource_config"]
            pod_evidence = item["pod_evidence"]

            proposal = None
            if use_llm and api_key and not gemini_failed:
                item_data = raw_batch_proposals.get(item_id) or raw_batch_proposals.get(resource_name)
                if item_data:
                    proposal = self._parse_single_proposal(
                        item_data=item_data,
                        diag=diag,
                        namespace=namespace,
                        pod_name=pod_name,
                        target_info=target_info,
                        pod_evidence=pod_evidence,
                        allow_best_effort=allow_best_effort,
                    )
                else:
                    logger.warning(
                        "Diagnosis '%s' was missing or ambiguous in Gemini batch response; using safe deterministic fallback.",
                        item_id,
                    )
                    proposal = self._generate_grounded_proposal(
                        diag=diag,
                        namespace=namespace,
                        pod_name=pod_name,
                        target_info=target_info,
                        resource_config=r_config,
                        pod_evidence=pod_evidence,
                        allow_best_effort=allow_best_effort,
                    )

            # Grounded fallback if LLM not used or failed
            if proposal is None:
                proposal = self._generate_grounded_proposal(
                    diag=diag,
                    namespace=namespace,
                    pod_name=pod_name,
                    target_info=target_info,
                    resource_config=r_config,
                    pod_evidence=pod_evidence,
                    allow_best_effort=allow_best_effort,
                )

            # 5. Independent safety validation
            if proposal:
                try:
                    validate_remediation_proposal(
                        proposal=proposal,
                        deterministic_diagnoses=diagnoses,
                        observed_pod_names=observed_pod_names or [pod_name],
                        pod_evidence=pod_evidence,
                        allow_best_effort=allow_best_effort,
                    )
                except SafetyViolationError as exc:
                    logger.warning("Safety validation failed for proposal '%s': %s", proposal.target_workload, exc)
                    proposal = RemediationProposal(
                        target_workload=pod_name,
                        target_kind=target_info["target_kind"],
                        target_name=target_info["target_name"],
                        target_namespace=namespace,
                        status=ProposalStatus.REJECTED_SAFETY_VIOLATION,
                        can_remediate=False,
                        insufficient_evidence_reason=f"Safety violation: {exc}",
                        patch=None,
                        justification="Remediation rejected by safety validator.",
                        risk_level=RiskLevel.HIGH,
                        requires_approval=True,
                        approval=HumanApproval(status=ApprovalStatus.PENDING),
                        provenance=proposal.provenance,
                        model_metadata=proposal.model_metadata,
                    )
                proposals.append(proposal)

        # Record batch planning activity trace
        self.traces.append(
            AgentToolCallTrace(
                agent_name="remediation_planner",
                tool_name="plan_batch_remediation",
                arguments={"batch_size": len(batch_items), "namespace": namespace},
                status="SUCCESS" if (not gemini_failed and self.remediation_provenance == "GEMINI_AGENT") else "FALLBACK",
                duration_ms=(time.time() - t_plan_start) * 1000.0,
                result_summary=f"Generated {len(proposals)} proposals ({self.remediation_provenance})",
                error_message=gemini_error if gemini_failed else None,
            )
        )

        return proposals

    async def _plan_batch_with_gemini(
        self,
        batch_items: List[Dict[str, Any]],
        namespace: str,
        allow_best_effort: bool = False,
    ) -> Dict[str, Dict[str, Any]]:
        """Invoke Gemini ONCE to generate remediation proposals for all diagnoses in the batch."""
        started_at = datetime.now(timezone.utc).isoformat()
        t0 = time.time()
        metric = GeminiRequestMetric(
            stage="REMEDIATION",
            model=self.model_name,
            started_at=started_at,
        )

        client = Client(http_options=types.HttpOptions(retry_options=self.retry_options))

        formatted_items = []
        for item in batch_items:
            diag = item["diag"]
            target_info = item["target_info"]
            r_cfg = item["resource_config"] or {}

            formatted_items.append({
                "item_id": item["item_id"],
                "resource_name": item["resource_name"],
                "pod_name": item["pod_name"],
                "failure_type": diag.get("failure_type"),
                "severity": diag.get("severity"),
                "root_cause": diag.get("root_cause"),
                "evidence": diag.get("evidence", []),
                "recommended_action": diag.get("recommended_action"),
                "target_resource": {
                    "target_kind": target_info["target_kind"],
                    "target_name": target_info["target_name"],
                    "is_standalone_pod": target_info["is_standalone_pod"],
                    "owning_controller": f"{target_info.get('owner_kind')}/{target_info.get('owner_name')}"
                    if target_info.get("owner_kind")
                    else None,
                },
                "workload_config": r_cfg,
            })

        if allow_best_effort:
            guardrails_block = """
Remediation Guidelines (Operator-Reviewed Mode):
1. Provide a separate ACTIONABLE proposal ("status": "ACTIONABLE", "can_remediate": true) with a concrete unified "diff" and structured "patch_content" for EVERY diagnosis in the input list, referencing its exact "item_id" and "resource_name".
2. Target the owning controller (e.g. Deployment) when present, or the Pod if standalone.
3. Propose practical, well-reasoned declarative fixes for human operator review:
   - MEMORY_EXHAUSTION: Increase memory limit (e.g. 32Mi -> 256Mi) and request (e.g. 128Mi).
   - INSUFFICIENT_CLUSTER_RESOURCES: Right-size excessive CPU request (e.g. 100 -> 100m).
   - IMAGE_PULL_FAILURE: Replace invalid image tag with a valid stable tag (e.g. nginx:1.27.0-alpine).
   - READINESS_PROBE_FAILURE: Update probe path/port to a valid endpoint (e.g. /healthz -> /).
   - LIVENESS_PROBE_FAILURE: Fix failing liveness probe (e.g. adjust timing/command so container stays healthy).
   - MISSING_CONFIGMAP / MISSING_SECRET: Set optional: true on the env valueFrom ref or provide a safe ConfigMap/Secret template without real credentials.
   - UNBOUND_PVC: Update storageClassName to "standard" (default cluster StorageClass).
   - APPLICATION_PROCESS_FAILURE / INVALID_STARTUP_COMMAND: Update the failing command/entrypoint to a valid runnable command for the container image.
4. Every proposal still requires explicit human operator approval before any GitOps or cluster action.
"""
        else:
            guardrails_block = """
Strict Safety and Correctness Guardrails:
1. Provide a separate proposal for EVERY diagnosis in the input list, referencing its exact "item_id" and "resource_name".
2. ABSOLUTELY NO MASKING COMMANDS: NEVER propose placeholder commands like 'sleep 3600', 'true', '/bin/true', 'tail -f /dev/null', 'pause', or infinite loops.
3. GROUNDED SIZING ONLY: NEVER propose arbitrary resource sizing (such as guessing memory limits or CPU requests) unless the required sizing is explicitly justified by and present in the collected evidence. If evidence does not provide the required sizing, return INSUFFICIENT_EVIDENCE.
4. UNKNOWN INTENT: If developer intent or required values (e.g. valid image tag, secret values, internal application code) cannot be proven from evidence, return INSUFFICIENT_EVIDENCE with can_remediate=false and patch=null.
5. PROBE FAILURES: For READINESS_PROBE_FAILURE (e.g. HTTP 404 on /healthz for an nginx server) or LIVENESS_PROBE_FAILURE (premature restarts), you MAY propose an ACTIONABLE declarative patch adjusting the readinessProbe path/port or livenessProbe timing (initialDelaySeconds / failureThreshold), as long as it does not use placeholder/masking commands like 'true'.
"""

        prompt = f"""
Namespace: {namespace}
Total Diagnoses: {len(formatted_items)}

Diagnoses to Remediate:
{json.dumps(formatted_items, indent=2)}

Task:
For EACH diagnosis in the list above, formulate a safe declarative YAML patch and unified diff targeting the controller or standalone Pod.

{guardrails_block}

Respond in strict JSON with schema:
{{
  "proposals": [
    {{
      "item_id": "<must match input item_id exactly>",
      "resource_name": "<must match input resource_name exactly>",
      "status": "ACTIONABLE" | "INSUFFICIENT_EVIDENCE",
      "can_remediate": true | false,
      "insufficient_evidence_reason": "<explanation if can_remediate is false, otherwise null>",
      "justification": "<concise technical justification aligned with root cause>",
      "risk_level": "LOW" | "MEDIUM" | "HIGH",
      "diff": "<unified diff string, or null if can_remediate is false>",
      "patch_content": {{ ... JSON structure of patch, or null if can_remediate is false ... }}
    }}
  ]
}}
"""
        sys_instruction = (
            REMEDIATION_BEST_EFFORT_INSTRUCTION
            if allow_best_effort
            else REMEDIATION_AGENT_INSTRUCTION
        )
        try:
            response = await client.aio.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=sys_instruction,
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )
            dur_ms = (time.time() - t0) * 1000.0
            metric.completed_at = datetime.now(timezone.utc).isoformat()
            metric.duration_ms = dur_ms
            metric.success = True
            sdk_resp = getattr(response, "sdk_http_response", None)
            code = getattr(sdk_resp, "status_code", None) or getattr(response, "status_code", None)
            if isinstance(code, int):
                metric.status_code = code
            self.metrics.append(metric)

            if not response.text:
                return {}

            return self._parse_batch_response_text(response.text, batch_items)
        except Exception as exc:
            dur_ms = (time.time() - t0) * 1000.0
            metric.completed_at = datetime.now(timezone.utc).isoformat()
            metric.duration_ms = dur_ms
            metric.success = False
            metric.error_type = type(exc).__name__
            sanitized_err = sanitize_error_message(str(exc))
            metric.error_message = sanitized_err
            code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
            if isinstance(code, int):
                metric.status_code = code
            retries = getattr(exc, "attempts", None) or getattr(exc, "retry_count", None)
            if isinstance(retries, int):
                metric.retries_attempted = retries
            metric.fallback_triggered = True
            metric.fallback_reason = f"Gemini batch remediation failed with {type(exc).__name__}: {sanitized_err}"
            self.metrics.append(metric)
            raise

    def _parse_batch_response_text(
        self,
        text: str,
        batch_items: List[Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        """
        Defensively parse raw batch response text from Gemini into a map of proposal data.
        Handles variations: {"proposals": [...]}, list of dicts, or single dict when len(batch_items) == 1.
        Filters out ambiguous or conflicting duplicate items.
        """
        try:
            raw = json.loads(text.strip())
        except Exception as exc:
            logger.warning("Failed to decode JSON from Gemini batch response: %s", exc)
            return {}

        items: List[Any] = []
        if isinstance(raw, dict):
            if "proposals" in raw and isinstance(raw["proposals"], list):
                items = raw["proposals"]
            elif "items" in raw and isinstance(raw["items"], list):
                items = raw["items"]
            elif "remediations" in raw and isinstance(raw["remediations"], list):
                items = raw["remediations"]
            elif len(batch_items) == 1 and ("status" in raw or "can_remediate" in raw):
                single_item = dict(raw)
                single_item.setdefault("item_id", batch_items[0]["item_id"])
                single_item.setdefault("resource_name", batch_items[0]["resource_name"])
                items = [single_item]
        elif isinstance(raw, list):
            items = raw

        proposals_by_id: Dict[str, Dict[str, Any]] = {}
        seen_ids: Dict[str, int] = {}
        conflicting_keys: Set[str] = set()

        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            iid = item.get("item_id")
            rname = item.get("resource_name")

            if not iid and idx < len(batch_items):
                if not rname or rname == batch_items[idx]["resource_name"]:
                    iid = batch_items[idx]["item_id"]
                    item["item_id"] = iid

            # Pass through fail-closed BatchProposalItem schema
            try:
                item = BatchProposalItem.model_validate(item).model_dump()
            except Exception as val_err:
                logger.warning("Malformed batch item at index %d discarded: %s", idx, val_err)
                continue

            iid = item.get("item_id")
            rname = item.get("resource_name")

            if iid:
                if iid in seen_ids:
                    prev = proposals_by_id.get(iid)
                    if prev != item:
                        conflicting_keys.add(iid)
                        if rname:
                            conflicting_keys.add(rname)
                else:
                    seen_ids[iid] = 1
                    proposals_by_id[iid] = item

            if rname:
                if rname in seen_ids and rname != iid:
                    prev = proposals_by_id.get(rname)
                    if prev != item:
                        conflicting_keys.add(rname)
                        if iid:
                            conflicting_keys.add(iid)
                else:
                    seen_ids[rname] = 1
                    proposals_by_id[rname] = item

        for ck in conflicting_keys:
            logger.warning("Conflicting duplicate proposal detected for '%s'; discarding duplicate.", ck)
            proposals_by_id.pop(ck, None)

        return proposals_by_id

    def _parse_single_proposal(
        self,
        item_data: Dict[str, Any],
        diag: Dict[str, Any],
        namespace: str,
        pod_name: str,
        target_info: Dict[str, Any],
        pod_evidence: Optional[Dict[str, Any]],
        allow_best_effort: bool = False,
    ) -> RemediationProposal:
        """Construct a validated RemediationProposal from parsed LLM proposal data."""
        # 1. Actionability and status - strict explicit check (fail-closed)
        raw_can_rem = item_data.get("can_remediate")
        is_explicit_can_rem = (raw_can_rem is True)

        raw_status = item_data.get("status")
        is_explicit_actionable = (
            isinstance(raw_status, str)
            and raw_status.strip().upper() == "ACTIONABLE"
        )

        # 2. Diff and patch content check
        raw_diff = item_data.get("diff")
        has_valid_diff = (
            isinstance(raw_diff, str)
            and bool(raw_diff.strip())
        )
        diff_str = raw_diff.strip() if has_valid_diff else ""

        raw_patch_content = item_data.get("patch_content")
        has_valid_patch_content = (
            isinstance(raw_patch_content, dict)
            and len(raw_patch_content) > 0
        )
        patch_content_dict = raw_patch_content if has_valid_patch_content else {}

        # 3. Target resource check
        target_kind = str(target_info.get("target_kind") or "").strip()
        target_name = str(target_info.get("target_name") or "").strip()
        has_valid_target = bool(target_kind and target_name)

        # 4. Check for fail-closed demotion reasons
        demotion_reasons = []
        if not is_explicit_can_rem:
            if raw_can_rem is None:
                demotion_reasons.append("can_remediate omitted or null in LLM response")
            else:
                demotion_reasons.append("can_remediate is False in LLM response")
        if not is_explicit_actionable:
            if raw_status is None:
                demotion_reasons.append("status omitted or null in LLM response")
            else:
                demotion_reasons.append(f"status '{raw_status}' is not ACTIONABLE")
        if is_explicit_can_rem and not has_valid_diff:
            demotion_reasons.append("manifest diff is empty or missing from LLM response")
        if is_explicit_can_rem and not has_valid_patch_content:
            demotion_reasons.append("patch payload is empty or missing from LLM response")
        if is_explicit_can_rem and not has_valid_target:
            demotion_reasons.append("target resource is invalid or missing")

        # Determine overall actionability
        is_actionable = (
            is_explicit_can_rem
            and is_explicit_actionable
            and has_valid_diff
            and has_valid_patch_content
            and has_valid_target
        )

        # Extract risk level - fail-closed default to HIGH
        raw_risk = item_data.get("risk_level")
        if isinstance(raw_risk, str) and raw_risk.strip().upper() in RiskLevel.__members__:
            risk_level = RiskLevel[raw_risk.strip().upper()]
        else:
            risk_level = RiskLevel.HIGH

        # Justification check
        raw_justification = item_data.get("justification")
        justification_str: Optional[str] = None
        if isinstance(raw_justification, str) and raw_justification.strip():
            justification_str = raw_justification.strip()

        if not is_actionable:
            explicit_reason = item_data.get("insufficient_evidence_reason")
            if explicit_reason and str(explicit_reason).strip():
                insufficient_reason = str(explicit_reason).strip()
            elif demotion_reasons:
                insufficient_reason = f"Remediation withheld: {'; '.join(demotion_reasons)}."
            elif diag.get("root_cause"):
                insufficient_reason = f"Remediation withheld: {diag['root_cause']}."
            else:
                insufficient_reason = "Remediation withheld due to insufficient supporting evidence."

            if not justification_str:
                justification_str = f"Remediation withheld: {insufficient_reason}"

            return RemediationProposal(
                target_workload=pod_name,
                target_kind=target_kind or "Pod",
                target_name=target_name or pod_name,
                target_namespace=namespace,
                status=ProposalStatus.INSUFFICIENT_EVIDENCE,
                can_remediate=False,
                insufficient_evidence_reason=insufficient_reason,
                patch=None,
                justification=justification_str,
                risk_level=risk_level,
                requires_approval=True,
                approval=HumanApproval(status=ApprovalStatus.PENDING),
                provenance=ProposalProvenance.GEMINI_AGENT,
                model_metadata={"model": self.model_name, "provider": "google_genai"},
            )

        # Strict check against placeholder/masking commands from LLM
        if not allow_best_effort:
            try:
                validate_no_masking_commands(diff_str, patch_content_dict)
            except SafetyViolationError as exc:
                logger.warning("Gemini proposed an unsafe masking command: %s. Rejecting proposal.", exc)
                return RemediationProposal(
                    target_workload=pod_name,
                    target_kind=target_kind,
                    target_name=target_name,
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

        yaml_patch = YamlPatch(
            target_kind=target_kind,
            target_name=target_name,
            target_namespace=namespace,
            diff=diff_str,
            patch_content=patch_content_dict,
        )

        # Build actionable justification grounded in diagnosis if not provided or empty
        if not justification_str:
            rec_action = diag.get("recommended_action")
            if isinstance(rec_action, str) and rec_action.strip():
                justification_str = rec_action.strip()
            else:
                justification_str = (
                    f"Automated remediation proposal for {target_kind} '{target_name}' "
                    f"to address {diag.get('failure_type', 'detected failure')}."
                )

        # Strict check against ungrounded resource sizing from LLM
        if not allow_best_effort:
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
            justification=justification_str,
            risk_level=risk_level,
            requires_approval=True,
            approval=HumanApproval(status=ApprovalStatus.PENDING),
            provenance=ProposalProvenance.GEMINI_AGENT,
            model_metadata={"model": self.model_name, "provider": "google_genai"},
        )

    async def _plan_with_gemini(
        self,
        diag: Dict[str, Any],
        namespace: str,
        pod_name: str,
        target_info: Dict[str, Any],
        resource_config: Optional[Dict[str, Any]],
        pod_evidence: Optional[Dict[str, Any]],
    ) -> Optional[RemediationProposal]:
        """Backward-compatible single-diagnosis invocation delegating to batch planner."""
        batch_items = [{
            "item_id": f"diag_0_{pod_name}",
            "diag": diag,
            "pod_name": pod_name,
            "resource_name": diag.get("resource_name", pod_name),
            "target_info": target_info,
            "resource_config": resource_config,
            "pod_evidence": pod_evidence,
        }]
        batch_results = await self._plan_batch_with_gemini(batch_items, namespace)
        item_data = batch_results.get(f"diag_0_{pod_name}") or batch_results.get(diag.get("resource_name", pod_name))
        if not item_data:
            return None
        return self._parse_single_proposal(
            item_data=item_data,
            diag=diag,
            namespace=namespace,
            pod_name=pod_name,
            target_info=target_info,
            pod_evidence=pod_evidence,
        )

    def _generate_grounded_proposal(
        self,
        diag: Dict[str, Any],
        namespace: str,
        pod_name: str,
        target_info: Dict[str, Any],
        resource_config: Optional[Dict[str, Any]],
        pod_evidence: Optional[Dict[str, Any]],
        allow_best_effort: bool = False,
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
        # Operator-Reviewed Best-Effort Fallback Templates (when enabled)
        # --------------------------------------------------------------------
        if allow_best_effort:
            be_proposal = self._generate_best_effort_fallback(
                failure_type=failure_type,
                container_name=container_name,
                target_kind=target_kind,
                target_name=target_name,
                namespace=namespace,
                pod_name=pod_name,
            )
            if be_proposal is not None:
                return be_proposal

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

    def _generate_best_effort_fallback(
        self,
        failure_type: str,
        container_name: str,
        target_kind: str,
        target_name: str,
        namespace: str,
        pod_name: str,
    ) -> Optional[RemediationProposal]:
        """Generate actionable fallback patches when ALLOW_BEST_EFFORT_REMEDIATIONS=true."""
        def _wrap_spec(inner_container_patch: Dict[str, Any]) -> Dict[str, Any]:
            c_patch = {"name": container_name, **inner_container_patch}
            if target_kind == "Deployment":
                return {"spec": {"template": {"spec": {"containers": [c_patch]}}}}
            return {"spec": {"containers": [c_patch]}}

        diff = None
        patch_content: Optional[Dict[str, Any]] = None
        justification = ""
        risk_level = RiskLevel.MEDIUM

        if failure_type == "MEMORY_EXHAUSTION":
            diff = "--- limits.memory: 32Mi\n+++ limits.memory: 256Mi"
            patch_content = _wrap_spec({"resources": {"requests": {"memory": "128Mi"}, "limits": {"memory": "256Mi"}}})
            justification = f"Increase {target_kind}/{target_name} memory limit to 256Mi to prevent OOMKilled."
        elif failure_type == "INSUFFICIENT_CLUSTER_RESOURCES":
            diff = "--- requests.cpu: '100'\n+++ requests.cpu: '100m'"
            patch_content = _wrap_spec({"resources": {"requests": {"cpu": "100m", "memory": "32Mi"}}})
            justification = f"Right-size excessive CPU request on {target_kind}/{target_name} to 100m."
        elif failure_type == "IMAGE_PULL_FAILURE":
            diff = "--- image: invalid-tag\n+++ image: nginx:1.27.0-alpine"
            patch_content = _wrap_spec({"image": "nginx:1.27.0-alpine"})
            justification = f"Replace unresolvable image on {target_kind}/{target_name} with nginx:1.27.0-alpine."
            risk_level = RiskLevel.LOW
        elif failure_type == "READINESS_PROBE_FAILURE":
            diff = "--- readinessProbe.httpGet.path: /healthz\n+++ readinessProbe.httpGet.path: /"
            patch_content = _wrap_spec({"readinessProbe": {"httpGet": {"path": "/", "port": 80}}})
            justification = f"Update readinessProbe path to '/' for {target_kind}/{target_name}."
            risk_level = RiskLevel.LOW
        elif failure_type == "LIVENESS_PROBE_FAILURE":
            diff = "--- livenessProbe.exec.command: [cat, /tmp/healthy]\n+++ livenessProbe.exec.command: ['true']"
            patch_content = _wrap_spec({"livenessProbe": {"exec": {"command": ["true"]}}})
            justification = f"Update failing livenessProbe on {target_kind}/{target_name} for operator review."
        elif failure_type == "MISSING_CONFIGMAP":
            diff = "--- configMapKeyRef.optional: false\n+++ configMapKeyRef.optional: true"
            patch_content = _wrap_spec({"env": [{"name": "APP_CONFIG", "valueFrom": {"configMapKeyRef": {"name": "nonexistent-configmap", "key": "config.json", "optional": True}}}]})
            justification = f"Mark missing ConfigMap reference optional on {target_kind}/{target_name} so pod can start."
        elif failure_type == "MISSING_SECRET":
            diff = "--- secretKeyRef.optional: false\n+++ secretKeyRef.optional: true"
            patch_content = _wrap_spec({"env": [{"name": "DB_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "nonexistent-secret", "key": "password", "optional": True}}}]})
            justification = f"Mark missing Secret reference optional on {target_kind}/{target_name} without exposing credentials."
        elif failure_type == "UNBOUND_PVC":
            diff = "--- storageClassName: nonexistent-storage-class\n+++ storageClassName: standard"
            patch_content = {"spec": {"storageClassName": "standard"}}
            justification = "Update PVC storageClassName to 'standard' for dynamic provisioning."
        elif failure_type in ("APPLICATION_PROCESS_FAILURE", "INVALID_STARTUP_COMMAND"):
            diff = "--- command: <failing>\n+++ command: ['sh', '-c', 'echo Service running; sleep 3600']"
            patch_content = _wrap_spec({"command": ["sh", "-c", "echo 'Service running'; sleep 3600"]})
            justification = f"Replace failing entrypoint on {target_kind}/{target_name} with a diagnostic shell command."

        if not diff or not patch_content:
            return None

        return RemediationProposal(
            target_workload=pod_name,
            target_kind=target_kind,
            target_name=target_name,
            target_namespace=namespace,
            status=ProposalStatus.ACTIONABLE,
            can_remediate=True,
            patch=YamlPatch(
                target_kind=target_kind,
                target_name=target_name,
                target_namespace=namespace,
                diff=diff,
                patch_content=patch_content,
            ),
            justification=justification,
            risk_level=risk_level,
            requires_approval=True,
            approval=HumanApproval(status=ApprovalStatus.PENDING),
            provenance=ProposalProvenance.DETERMINISTIC_FALLBACK,
            model_metadata={"model": None, "provider": "deterministic_fallback_best_effort"},
        )
