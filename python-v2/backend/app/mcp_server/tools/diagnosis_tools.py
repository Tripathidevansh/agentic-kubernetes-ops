from typing import Any, Dict, Optional
from kubernetes.client.rest import ApiException

from app.mcp_server.common import (
    error_response,
    format_api_exception,
    get_default_k8s_client,
    logger,
    success_response,
    validate_namespace,
)
from app.services.diagnosis_service import diagnose_cluster
from app.services.kubernetes_service import collect_cluster_evidence


def handle_run_diagnosis(
    namespace: str = "k8s-agent-demo",
    v1: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Run the deterministic incident diagnosis engine against fresh cluster evidence.
    Returns structured diagnoses with failure type, severity, root cause, evidence, and actions.
    No LLM calls or fabricated results. Read-only.
    """
    ns_err = validate_namespace(namespace)
    if ns_err:
        return error_response(tool="run_diagnosis", namespace=namespace, code="INVALID_PARAMETER", message=ns_err)

    client = v1 or get_default_k8s_client()
    try:
        evidence = collect_cluster_evidence(namespace=namespace, v1=client)
        diagnosis_report = diagnose_cluster(evidence)

        return success_response(
            tool="run_diagnosis",
            namespace=namespace,
            data={
                "diagnosis_count": diagnosis_report.get("diagnosis_count", 0),
                "diagnoses": diagnosis_report.get("diagnoses", []),
            },
        )
    except ApiException as exc:
        logger.error("Kubernetes API error during run_diagnosis in namespace '%s': %s", namespace, exc)
        return format_api_exception(tool="run_diagnosis", namespace=namespace, exc=exc)
    except Exception as exc:
        logger.error("Unexpected error during run_diagnosis in namespace '%s': %s", namespace, exc)
        return error_response(
            tool="run_diagnosis",
            namespace=namespace,
            code="KUBERNETES_CONNECTION_ERROR",
            message=f"Unable to run diagnosis against Kubernetes API: {str(exc)}",
        )
