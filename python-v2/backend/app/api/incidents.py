"""
Incidents endpoint.

Exposes deterministic failure diagnoses derived directly by the authoritative
rule-based engine without running LLM investigations.
"""

from datetime import datetime, timezone
import logging
import re
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query

from app.models.api_schemas import IncidentItem, IncidentsResponse
from app.services.diagnosis_service import diagnose_cluster
from app.services.kubernetes_service import collect_cluster_evidence
from app.validators.safety_validator import validate_secret_safety

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Incidents"])

DNS_PATTERN = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")


@router.get("/incidents", response_model=IncidentsResponse)
def get_incidents(
    namespace: str = Query(
        default="k8s-agent-demo",
        description="Target Kubernetes namespace",
    ),
) -> IncidentsResponse:
    """
    Return detected cluster incidents in the target namespace using
    the authoritative deterministic diagnosis engine.
    """
    ns_clean = namespace.strip()
    if not ns_clean or len(ns_clean) > 63 or not DNS_PATTERN.match(ns_clean):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid namespace '{namespace}'. Must be a valid DNS-1123 label (1-63 alphanumeric/hyphens).",
        )

    try:
        evidence = collect_cluster_evidence(namespace=ns_clean)
    except Exception as exc:
        logger.error("Failed to collect cluster evidence for namespace '%s': %s", ns_clean, exc)
        raise HTTPException(
            status_code=503,
            detail=f"Kubernetes cluster unreachable or evidence collection failed: {exc}",
        )

    # Safety assertion
    validate_secret_safety(evidence.get("pods", []))

    # Diagnose via authoritative engine
    report = diagnose_cluster(evidence)
    raw_diagnoses = report.get("diagnoses", [])

    incident_items: List[IncidentItem] = []
    for d in raw_diagnoses:
        incident_items.append(
            IncidentItem(
                failure_type=d.get("failure_type", "UNKNOWN"),
                severity=d.get("severity", "INFO"),
                resource_name=d.get("resource_name", "unknown"),
                namespace=d.get("namespace", ns_clean),
                summary=d.get("summary", ""),
                root_cause=d.get("root_cause", ""),
                evidence=d.get("evidence", []),
                recommended_action=d.get("recommended_action", ""),
                pod=d.get("pod"),
                container=d.get("container"),
                confidence=d.get("confidence", "HIGH"),
            )
        )

    return IncidentsResponse(
        namespace=ns_clean,
        total_incidents=len(incident_items),
        incidents=incident_items,
        collected_at=datetime.now(timezone.utc).isoformat(),
    )
