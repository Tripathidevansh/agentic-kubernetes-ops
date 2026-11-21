from typing import Any, Dict, List, Optional
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
from app.services.kubernetes_service import _init_kubernetes_client


def handle_get_pvc_details(
    pvc_name: str,
    namespace: str = "k8s-agent-demo",
    v1: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Inspect PersistentVolumeClaims, storage configurations, events, and dependent Pods.
    Read-only.
    """
    ns_err = validate_namespace(namespace)
    if ns_err:
        return error_response(tool="get_pvc_details", namespace=namespace, code="INVALID_PARAMETER", message=ns_err)

    pvc_err = validate_resource_name(pvc_name, param_name="pvc_name")
    if pvc_err:
        return error_response(tool="get_pvc_details", namespace=namespace, code="INVALID_PARAMETER", message=pvc_err)

    client = v1 or get_default_k8s_client()
    try:
        api = _init_kubernetes_client(client)

        # 1. Fetch the target PVC
        pvc = api.read_namespaced_persistent_volume_claim(name=pvc_name, namespace=namespace)

        # 2. Fetch namespace events involving this PVC
        try:
            raw_events = api.list_namespaced_event(namespace=namespace).items
            pvc_events = [
                {
                    "type": getattr(e, "type", "Normal"),
                    "reason": getattr(e, "reason", None),
                    "message": getattr(e, "message", None),
                    "count": getattr(e, "count", 1) or 1,
                    "timestamp": (
                        e.last_timestamp.isoformat()
                        if getattr(e, "last_timestamp", None)
                        else (e.first_timestamp.isoformat() if getattr(e, "first_timestamp", None) else None)
                    ),
                }
                for e in raw_events
                if getattr(getattr(e, "involved_object", None), "kind", None) == "PersistentVolumeClaim"
                and getattr(getattr(e, "involved_object", None), "name", None) == pvc_name
            ]
        except Exception:
            pvc_events = []

        # 3. Find Pods in the namespace referencing this PVC in their spec.volumes
        referencing_pods: List[str] = []
        try:
            pods_list = api.list_namespaced_pod(namespace=namespace).items
            for pod in pods_list:
                pod_pvc_names = [
                    vol.persistent_volume_claim.claim_name
                    for vol in (pod.spec.volumes or [])
                    if getattr(vol, "persistent_volume_claim", None)
                    and getattr(vol.persistent_volume_claim, "claim_name", None)
                ]
                if pvc_name in pod_pvc_names:
                    referencing_pods.append(pod.metadata.name)
        except Exception as exc:
            logger.warning("Could not correlate referencing pods for PVC '%s': %s", pvc_name, exc)

        # 4. Extract storage details
        capacity_dict = {}
        if getattr(pvc, "status", None) and getattr(pvc.status, "capacity", None):
            capacity_dict = dict(pvc.status.capacity)
        elif getattr(pvc, "spec", None) and getattr(pvc.spec, "resources", None) and getattr(pvc.spec.resources, "requests", None):
            capacity_dict = dict(pvc.spec.resources.requests)

        data = {
            "name": pvc_name,
            "phase": getattr(pvc.status, "phase", "Unknown") if getattr(pvc, "status", None) else "Unknown",
            "storage_class": getattr(pvc.spec, "storage_class_name", None) if getattr(pvc, "spec", None) else None,
            "capacity": capacity_dict,
            "access_modes": list(getattr(pvc.spec, "access_modes", None) or []) if getattr(pvc, "spec", None) else [],
            "bound_volume": getattr(pvc.spec, "volume_name", None) if getattr(pvc, "spec", None) else None,
            "events": pvc_events,
            "referencing_pods": sorted(referencing_pods),
        }

        return success_response(
            tool="get_pvc_details",
            namespace=namespace,
            data=data,
        )
    except ApiException as exc:
        if exc.status == 404:
            return error_response(
                tool="get_pvc_details",
                namespace=namespace,
                code="PVC_NOT_FOUND",
                message=f"PersistentVolumeClaim '{pvc_name}' was not found in namespace '{namespace}'.",
            )
        logger.error("Kubernetes API error during get_pvc_details for '%s': %s", pvc_name, exc)
        return format_api_exception(tool="get_pvc_details", namespace=namespace, exc=exc)
    except Exception as exc:
        logger.error("Unexpected error during get_pvc_details for '%s': %s", pvc_name, exc)
        return error_response(
            tool="get_pvc_details",
            namespace=namespace,
            code="KUBERNETES_CONNECTION_ERROR",
            message=f"Unable to retrieve PVC details from Kubernetes API: {str(exc)}",
        )
