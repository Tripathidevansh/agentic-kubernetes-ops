"""
Health check endpoint.

Reports service availability, API version, Kubernetes cluster reachability,
and configured Gemini model without exposing sensitive infrastructure details.
"""

from datetime import datetime, timezone
import logging
import os

from fastapi import APIRouter

from app.models.api_schemas import HealthResponse
from app.services.kubernetes_service import _init_kubernetes_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    """
    Return operational status of the Agentic AI Kubernetes Operations backend.
    Probes Kubernetes API connectivity safely without failing if cluster is offline.
    """
    cluster_connected = False
    try:
        api = _init_kubernetes_client()
        # Safe read probe
        api.get_api_resources()
        cluster_connected = True
    except Exception as exc:
        logger.debug("Cluster connectivity check failed: %s", exc)
        cluster_connected = False

    model_name = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")

    return HealthResponse(
        status="healthy",
        service="k8s-agentic-ops",
        version="2.0.0",
        cluster_connected=cluster_connected,
        model=model_name,
        mcp_server="read-only-k8s-tools",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
