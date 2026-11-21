from typing import Any, Dict, Optional
from kubernetes.client.rest import ApiException

from app.mcp_server.common import (
    error_response,
    format_api_exception,
    get_default_k8s_client,
    logger,
    success_response,
    validate_namespace,
    validate_resource_name,
)
from app.services.kubernetes_service import _init_kubernetes_client, collect_cluster_evidence


def handle_scan_cluster(
    namespace: str = "k8s-agent-demo",
    v1: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Collect structured evidence from the requested namespace using existing collector.
    Read-only operation.
    """
    ns_err = validate_namespace(namespace)
    if ns_err:
        return error_response(tool="scan_cluster", namespace=namespace, code="INVALID_PARAMETER", message=ns_err)

    client = v1 or get_default_k8s_client()
    try:
        evidence = collect_cluster_evidence(namespace=namespace, v1=client)
        return success_response(
            tool="scan_cluster",
            namespace=namespace,
            data={
                "pod_count": evidence.get("pod_count", 0),
                "pods": evidence.get("pods", []),
            },
        )
    except ApiException as exc:
        logger.error("Kubernetes API error during scan_cluster in namespace '%s': %s", namespace, exc)
        return format_api_exception(tool="scan_cluster", namespace=namespace, exc=exc)
    except Exception as exc:
        logger.error("Unexpected error during scan_cluster in namespace '%s': %s", namespace, exc)
        return error_response(
            tool="scan_cluster",
            namespace=namespace,
            code="KUBERNETES_CONNECTION_ERROR",
            message=f"Unable to connect to Kubernetes API: {str(exc)}",
        )


def handle_get_events(
    namespace: str = "k8s-agent-demo",
    pod_name: Optional[str] = None,
    v1: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Retrieve relevant Kubernetes events in a namespace, optionally filtered by Pod name.
    Read-only operation.
    """
    ns_err = validate_namespace(namespace)
    if ns_err:
        return error_response(tool="get_events", namespace=namespace, code="INVALID_PARAMETER", message=ns_err)

    if pod_name is not None:
        pod_err = validate_resource_name(pod_name, param_name="pod_name")
        if pod_err:
            return error_response(tool="get_events", namespace=namespace, code="INVALID_PARAMETER", message=pod_err)

    client = v1 or get_default_k8s_client()
    try:
        api = _init_kubernetes_client(client)
        events_list = api.list_namespaced_event(namespace=namespace).items

        formatted_events = []
        for e in events_list:
            inv_obj = getattr(e, "involved_object", None)
            inv_kind = getattr(inv_obj, "kind", None)
            inv_name = getattr(inv_obj, "name", None)

            # Apply Pod filter if requested
            if pod_name and (inv_kind != "Pod" or inv_name != pod_name):
                continue

            timestamp = None
            if getattr(e, "last_timestamp", None):
                timestamp = e.last_timestamp.isoformat()
            elif getattr(e, "first_timestamp", None):
                timestamp = e.first_timestamp.isoformat()
            elif getattr(e, "event_time", None):
                timestamp = e.event_time.isoformat()

            formatted_events.append({
                "type": getattr(e, "type", "Normal"),
                "reason": getattr(e, "reason", None),
                "message": getattr(e, "message", None),
                "timestamp": timestamp,
                "count": getattr(e, "count", 1) or 1,
                "associated_resource": {
                    "kind": inv_kind,
                    "name": inv_name,
                },
            })

        return success_response(
            tool="get_events",
            namespace=namespace,
            data={
                "event_count": len(formatted_events),
                "events": formatted_events,
            },
        )
    except ApiException as exc:
        logger.error("Kubernetes API error during get_events in namespace '%s': %s", namespace, exc)
        return format_api_exception(tool="get_events", namespace=namespace, exc=exc)
    except Exception as exc:
        logger.error("Unexpected error during get_events in namespace '%s': %s", namespace, exc)
        return error_response(
            tool="get_events",
            namespace=namespace,
            code="KUBERNETES_CONNECTION_ERROR",
            message=f"Unable to retrieve events from Kubernetes API: {str(exc)}",
        )
