"""
Cluster Overview endpoint.

Aggregates Pod counts by runtime phase, summarizes container states, and correlates
active diagnosed incidents in the target namespace.
SECURITY GUARANTEE: Never exposes Secret values or environment variables.
"""

from collections import Counter
from datetime import datetime, timezone
import logging
import re
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query

from app.models.api_schemas import (
    ClusterOverviewResponse,
    PodContainerStatus,
    PodStatusSummary,
)
from app.services.diagnosis_service import diagnose_cluster
from app.services.kubernetes_service import collect_cluster_evidence
from app.validators.safety_validator import validate_secret_safety

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Cluster"])

DNS_PATTERN = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")


@router.get("/cluster/overview", response_model=ClusterOverviewResponse)
def get_cluster_overview(
    namespace: str = Query(
        default="k8s-agent-demo",
        description="Target Kubernetes namespace to survey",
    ),
) -> ClusterOverviewResponse:
    """
    Retrieve high-level runtime telemetry and incident overview for a namespace.
    Uses the existing read-only Kubernetes collector and deterministic diagnosis engine.
    """
    # 1. Validate namespace format
    ns_clean = namespace.strip()
    if not ns_clean or len(ns_clean) > 63 or not DNS_PATTERN.match(ns_clean):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid namespace '{namespace}'. Must be a valid DNS-1123 label (1-63 alphanumeric/hyphens).",
        )

    # 2. Collect evidence via existing Kubernetes service
    try:
        evidence = collect_cluster_evidence(namespace=ns_clean)
    except Exception as exc:
        logger.error("Failed to collect cluster evidence for namespace '%s': %s", ns_clean, exc)
        raise HTTPException(
            status_code=503,
            detail=f"Kubernetes cluster unreachable or evidence collection failed: {exc}",
        )

    # 3. Security verification: ensure no Secret values leaked into telemetry
    pods_raw = evidence.get("pods", [])
    validate_secret_safety(pods_raw)

    # 4. Diagnose incidents using existing deterministic engine
    diagnosis_report = diagnose_cluster(evidence)
    diagnoses = diagnosis_report.get("diagnoses", [])
    incident_types = [d.get("failure_type", "UNKNOWN") for d in diagnoses]
    incidents_summary = dict(Counter(incident_types))

    # 5. Aggregate Pod phases and states
    running = 0
    pending = 0
    failed = 0
    succeeded = 0
    unknown = 0
    pod_summaries: List[PodStatusSummary] = []

    for p in pods_raw:
        phase = p.get("phase") or "Unknown"
        if phase == "Running":
            running += 1
        elif phase == "Pending":
            pending += 1
        elif phase == "Failed":
            failed += 1
        elif phase == "Succeeded":
            succeeded += 1
        else:
            unknown += 1

        total_restarts = 0
        container_statuses: List[PodContainerStatus] = []
        for c in p.get("containers", []):
            st = c.get("state") or {}
            restarts = c.get("restart_count", 0)
            total_restarts += restarts
            container_statuses.append(
                PodContainerStatus(
                    name=c.get("name", "unknown"),
                    state=st.get("status"),
                    reason=st.get("reason"),
                    message=st.get("message"),
                    restart_count=restarts,
                    ready=c.get("is_ready", False),
                )
            )

        pod_summaries.append(
            PodStatusSummary(
                name=p.get("pod", "unknown"),
                namespace=ns_clean,
                phase=phase,
                is_ready=p.get("is_ready", False),
                is_scheduled=p.get("is_scheduled", False),
                node_name=p.get("node_name"),
                restarts=total_restarts,
                container_statuses=container_statuses,
                owners=p.get("owners", []),
            )
        )

    return ClusterOverviewResponse(
        namespace=ns_clean,
        total_pods=len(pods_raw),
        running_pods=running,
        pending_pods=pending,
        failed_pods=failed,
        succeeded_pods=succeeded,
        unknown_pods=unknown,
        incident_count=len(diagnoses),
        incidents_summary=incidents_summary,
        pods=pod_summaries,
        collected_at=datetime.now(timezone.utc).isoformat(),
    )
