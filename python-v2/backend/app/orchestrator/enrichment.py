"""
Deterministic Trusted-Target Enrichment for GitOps Remediation (Phase 7B.1).

Connects pre-configured trusted GitOps targets (from GitOpsService) to the
remediation planning stage.

SAFETY INVARIANTS:
1. ONLY enriches a proposal when ALL 6 conditions match:
   - Exact namespace.
   - Exact Deployment name and kind (Deployment).
   - Exact container name.
   - Exact current image.
   - Exact supported diagnosis: IMAGE_PULL_FAILURE.
   - Trusted replacement image exists in configuration.
2. Generates a structured proposal with:
   - status: ACTIONABLE
   - can_remediate: True
   - provenance: DETERMINISTIC_ENGINE
   - Explicit justification explaining the trusted fixture mapping
   - A narrowly scoped container image patch
   - requires_approval: True
   - approval: PENDING
3. Preserves original Gemini response, status, and provenance for all non-matching proposals.
4. Prevents arbitrary image overrides: trusted replacement image is strictly derived from configuration.
"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from app.models.gitops import GitOpsTargetConfig
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
from app.services.gitops_service import DEFAULT_TRUSTED_TARGETS, get_gitops_service
from app.validators.safety_validator import validate_remediation_proposal

logger = logging.getLogger(__name__)


def _extract_container_info(
    diagnosis: Dict[str, Any],
    pod_evidence: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract container name and current image reference from diagnosis and pod evidence.
    """
    container_name = diagnosis.get("container")
    if not container_name:
        res_name = diagnosis.get("resource_name") or ""
        if "/" in res_name:
            container_name = res_name.split("/", 1)[1]

    current_image = None

    # Check pod evidence container statuses / specs
    if pod_evidence:
        # Check container_statuses first (runtime state)
        c_statuses = pod_evidence.get("container_statuses") or []
        for cs in c_statuses:
            if isinstance(cs, dict):
                c_name = cs.get("name")
                if not container_name or c_name == container_name:
                    if not container_name:
                        container_name = c_name
                    current_image = cs.get("image")
                    if current_image:
                        break

        # Fallback to spec containers
        if not current_image:
            spec_containers = (
                pod_evidence.get("spec", {}).get("containers")
                or pod_evidence.get("containers")
                or []
            )
            for sc in spec_containers:
                if isinstance(sc, dict):
                    c_name = sc.get("name")
                    if not container_name or c_name == container_name:
                        if not container_name:
                            container_name = c_name
                        current_image = sc.get("image")
                        if current_image:
                            break

    # If image not found in pod_evidence, parse from diagnosis evidence strings, summary, recommended_action
    if not current_image:
        text_sources = [
            *(diagnosis.get("evidence") or []),
            diagnosis.get("summary") or "",
            diagnosis.get("recommended_action") or "",
        ]
        img_pattern_quoted = re.compile(r"image\s+['\"]([^'\"]+)['\"]", re.IGNORECASE)
        img_pattern_failed = re.compile(r"retrieve\s+image:\s*([^\s,;]+)", re.IGNORECASE)
        img_pattern_unquoted = re.compile(r"image:\s*([^\s,;]+)", re.IGNORECASE)

        for text in text_sources:
            if not text:
                continue
            m = img_pattern_quoted.search(str(text))
            if m:
                current_image = m.group(1).strip()
                break
            m = img_pattern_failed.search(str(text))
            if m:
                current_image = m.group(1).strip()
                break
            m = img_pattern_unquoted.search(str(text))
            if m:
                current_image = m.group(1).strip()
                break

    return container_name, current_image


def enrich_proposals_with_trusted_targets(
    proposals: List[RemediationProposal],
    diagnoses: List[Dict[str, Any]],
    namespace: str,
    observed_pods: Optional[List[Dict[str, Any]]] = None,
    trusted_targets: Optional[Dict[Tuple[str, str, str], GitOpsTargetConfig]] = None,
) -> List[RemediationProposal]:
    """
    Deterministically enrich remediation proposals matching configured trusted GitOps targets.

    For an exact match against a preconfigured trusted target, produce a deterministic,
    evidence-grounded remediation proposal with status ACTIONABLE and can_remediate=True.
    Preserves original Gemini response and provenance for every non-matching proposal.
    """
    if trusted_targets is None:
        try:
            service = get_gitops_service()
            targets_map = service.trusted_targets
        except Exception:
            targets_map = DEFAULT_TRUSTED_TARGETS
    else:
        targets_map = trusted_targets

    observed_pods_map: Dict[str, Dict[str, Any]] = {}
    if observed_pods:
        for p in observed_pods:
            if isinstance(p, dict) and p.get("pod"):
                observed_pods_map[p["pod"]] = p
            elif isinstance(p, dict) and p.get("name"):
                observed_pods_map[p["name"]] = p

    enriched_list: List[RemediationProposal] = []

    for proposal in proposals:
        # Find matching diagnosis for this proposal
        matching_diag = None
        for d in diagnoses:
            d_res = d.get("resource_name") or ""
            d_pod = d.get("pod") or ""
            if (
                d_pod == proposal.target_workload
                or d_res == proposal.target_name
                or d_res == proposal.target_workload
                or proposal.target_workload.startswith(d_res)
                or d_res.startswith(proposal.target_name)
            ):
                matching_diag = d
                break

        if not matching_diag:
            enriched_list.append(proposal)
            continue

        # Condition 5: Exact supported diagnosis must be IMAGE_PULL_FAILURE
        if matching_diag.get("failure_type") != "IMAGE_PULL_FAILURE":
            enriched_list.append(proposal)
            continue

        # Condition 2: Exact Deployment kind
        if proposal.target_kind != "Deployment":
            enriched_list.append(proposal)
            continue

        # Condition 1 & 2: Match against trusted target registry by (kind, name, namespace)
        target_name = proposal.target_name
        if not target_name:
            for (t_kind, t_n, t_ns) in targets_map.keys():
                if t_kind == proposal.target_kind and (
                    proposal.target_workload == t_n or proposal.target_workload.startswith(f"{t_n}-")
                ):
                    target_name = t_n
                    break

        if not target_name:
            enriched_list.append(proposal)
            continue

        target_namespace = proposal.target_namespace or namespace
        target_key = (proposal.target_kind, target_name, target_namespace)
        target_config = targets_map.get(target_key)

        if not target_config:
            # Not in trusted target configuration -> retain original Gemini / fallback proposal
            enriched_list.append(proposal)
            continue

        # Verify exact namespace match
        if target_config.target_namespace != target_namespace or target_config.target_namespace != namespace:
            enriched_list.append(proposal)
            continue

        # Find pod evidence if available
        pod_evidence = observed_pods_map.get(proposal.target_workload)

        # Condition 3 & 4: Exact container name and exact current image
        diag_container, diag_image = _extract_container_info(matching_diag, pod_evidence)

        # Condition 3: Exact container name (must be present and must match)
        if not diag_container or diag_container != target_config.container_name:
            logger.info(
                "Proposal '%s' container '%s' did not match trusted target container '%s'. Skipping enrichment.",
                target_config.target_name,
                diag_container,
                target_config.container_name,
            )
            enriched_list.append(proposal)
            continue

        # Condition 4: Exact current image (must be present and must match)
        if not diag_image or diag_image != target_config.current_image:
            logger.info(
                "Proposal '%s' current image '%s' did not match trusted target current image '%s'. Skipping enrichment.",
                target_config.target_name,
                diag_image,
                target_config.current_image,
            )
            enriched_list.append(proposal)
            continue

        # Condition 6: Trusted replacement image exists in configuration
        replacement_image = target_config.trusted_replacement_image
        if not replacement_image or not replacement_image.strip():
            logger.warning(
                "Trusted target '%s' lacks replacement image in configuration. Skipping enrichment.",
                target_config.target_name,
            )
            enriched_list.append(proposal)
            continue

        # ALL 6 CONDITIONS MATCH: Deterministically enrich proposal
        logger.info(
            "Proposal '%s' matched trusted GitOps target. Generating deterministic ACTIONABLE proposal "
            "with replacement image '%s'.",
            target_config.target_name,
            replacement_image,
        )

        diff_text = (
            f"--- a/manifests/{target_config.target_name}.yaml\n"
            f"+++ b/manifests/{target_config.target_name}.yaml\n"
            f"@@ -17,2 +17,2 @@\n"
            f"       containers:\n"
            f"         - name: {target_config.container_name}\n"
            f"-          image: {target_config.current_image}\n"
            f"+          image: {replacement_image}\n"
        )

        justification_msg = (
            f"Deterministic ground-truth mapping: Workload Deployment/{target_config.target_name} "
            f"container '{target_config.container_name}' diagnosed with ImagePullBackOff matched "
            f"trusted GitOps remediation fixture. Replaced unresolvable image reference "
            f"'{target_config.current_image}' with verified stable release '{replacement_image}'."
        )

        enriched = RemediationProposal(
            proposal_id=proposal.proposal_id,
            target_workload=proposal.target_workload,
            target_kind="Deployment",
            target_name=target_config.target_name,
            target_namespace=target_config.target_namespace,
            status=ProposalStatus.ACTIONABLE,
            can_remediate=True,
            insufficient_evidence_reason=None,
            patch=YamlPatch(
                target_kind="Deployment",
                target_name=target_config.target_name,
                target_namespace=target_config.target_namespace,
                diff=diff_text,
                patch_content={
                    "spec": {
                        "template": {
                            "spec": {
                                "containers": [
                                    {
                                        "name": target_config.container_name,
                                        "image": replacement_image,
                                    }
                                ]
                            }
                        }
                    }
                },
            ),
            justification=justification_msg,
            risk_level=RiskLevel.LOW,
            requires_approval=True,
            approval=HumanApproval(
                status=ApprovalStatus.PENDING,
                reviewer=None,
                reviewed_at=None,
                comments=None,
            ),
            verification_plan=VerificationPlan(
                target_phase="Running",
                expected_conditions=[{"type": "Ready", "status": "True"}],
                negative_assertions=["ImagePullBackOff", "ErrImagePull", "CrashLoopBackOff"],
                check_interval_seconds=5,
                timeout_seconds=60,
            ),
            provenance=ProposalProvenance.DETERMINISTIC_ENGINE,
            model_metadata={
                "provider": "deterministic_fixture",
                "fixture_target": target_config.target_name,
                "trusted_replacement_image": replacement_image,
            },
        )

        # Validate with existing safety validator
        try:
            validate_remediation_proposal(
                proposal=enriched,
                deterministic_diagnoses=diagnoses,
                observed_pod_names=[proposal.target_workload],
                pod_evidence=pod_evidence,
            )
        except Exception as exc:
            logger.error("Safety validation failed for enriched proposal: %s", exc)
            enriched_list.append(proposal)
            continue

        enriched_list.append(enriched)

    return enriched_list
