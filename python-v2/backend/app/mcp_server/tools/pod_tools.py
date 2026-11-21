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
from app.services.kubernetes_service import _init_kubernetes_client, _parse_probe, collect_cluster_evidence


def handle_get_pod_details(
    pod_name: str,
    namespace: str = "k8s-agent-demo",
    v1: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Retrieve detailed runtime and status information about one specific Pod.
    Reuses existing collected evidence. Read-only.
    """
    ns_err = validate_namespace(namespace)
    if ns_err:
        return error_response(tool="get_pod_details", namespace=namespace, code="INVALID_PARAMETER", message=ns_err)

    pod_err = validate_resource_name(pod_name, param_name="pod_name")
    if pod_err:
        return error_response(tool="get_pod_details", namespace=namespace, code="INVALID_PARAMETER", message=pod_err)

    client = v1 or get_default_k8s_client()
    try:
        evidence = collect_cluster_evidence(namespace=namespace, v1=client)
        pods = evidence.get("pods", [])
        matching_pod = next((p for p in pods if p.get("pod") == pod_name), None)

        if not matching_pod:
            return error_response(
                tool="get_pod_details",
                namespace=namespace,
                code="POD_NOT_FOUND",
                message=f"Pod '{pod_name}' was not found in namespace '{namespace}'.",
            )

        return success_response(
            tool="get_pod_details",
            namespace=namespace,
            data=matching_pod,
        )
    except ApiException as exc:
        logger.error("Kubernetes API error during get_pod_details for '%s': %s", pod_name, exc)
        return format_api_exception(tool="get_pod_details", namespace=namespace, exc=exc)
    except Exception as exc:
        logger.error("Unexpected error during get_pod_details for '%s': %s", pod_name, exc)
        return error_response(
            tool="get_pod_details",
            namespace=namespace,
            code="KUBERNETES_CONNECTION_ERROR",
            message=f"Unable to retrieve Pod details from Kubernetes API: {str(exc)}",
        )


def handle_get_pod_logs(
    pod_name: str,
    namespace: str = "k8s-agent-demo",
    previous: bool = False,
    container_name: Optional[str] = None,
    tail_lines: int = 100,
    v1: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Retrieve application logs for a Pod (current or previous container execution).
    Limits output to a sensible maximum and handles unavailable logs gracefully.
    Read-only.
    """
    ns_err = validate_namespace(namespace)
    if ns_err:
        return error_response(tool="get_pod_logs", namespace=namespace, code="INVALID_PARAMETER", message=ns_err)

    pod_err = validate_resource_name(pod_name, param_name="pod_name")
    if pod_err:
        return error_response(tool="get_pod_logs", namespace=namespace, code="INVALID_PARAMETER", message=pod_err)

    if container_name is not None:
        cnt_err = validate_resource_name(container_name, param_name="container_name")
        if cnt_err:
            return error_response(tool="get_pod_logs", namespace=namespace, code="INVALID_PARAMETER", message=cnt_err)

    # Bound tail_lines to sensible limits (1 to 500 lines)
    clamped_tail = max(1, min(tail_lines, 500))

    client = v1 or get_default_k8s_client()
    try:
        api = _init_kubernetes_client(client)

        # If container_name was not provided, lookup Pod spec to identify container
        target_container = container_name
        if not target_container:
            try:
                pod = api.read_namespaced_pod(name=pod_name, namespace=namespace)
                containers = [c.name for c in (pod.spec.containers or [])]
                if not containers:
                    return error_response(
                        tool="get_pod_logs",
                        namespace=namespace,
                        code="CONTAINER_NOT_FOUND",
                        message=f"Pod '{pod_name}' has no containers specified in spec.",
                    )
                target_container = containers[0]
            except ApiException as exc:
                if exc.status == 404:
                    return error_response(
                        tool="get_pod_logs",
                        namespace=namespace,
                        code="POD_NOT_FOUND",
                        message=f"Pod '{pod_name}' was not found in namespace '{namespace}'.",
                    )
                raise

        # Attempt to retrieve logs
        try:
            logs = api.read_namespaced_pod_log(
                name=pod_name,
                namespace=namespace,
                container=target_container,
                previous=previous,
                tail_lines=clamped_tail,
            )
        except ApiException as exc:
            # Handle containers that have never started or missing previous logs gracefully
            if exc.status in (400, 404) or "previous" in (exc.reason or "").lower():
                return success_response(
                    tool="get_pod_logs",
                    namespace=namespace,
                    data={
                        "pod": pod_name,
                        "container": target_container,
                        "previous": previous,
                        "tail_lines": clamped_tail,
                        "logs": f"Unavailable: {exc.reason or 'Container logs unavailable'}",
                    },
                )
            raise

        return success_response(
            tool="get_pod_logs",
            namespace=namespace,
            data={
                "pod": pod_name,
                "container": target_container,
                "previous": previous,
                "tail_lines": clamped_tail,
                "logs": logs or "",
            },
        )
    except ApiException as exc:
        if exc.status == 404:
            return error_response(
                tool="get_pod_logs",
                namespace=namespace,
                code="POD_NOT_FOUND",
                message=f"Pod '{pod_name}' was not found in namespace '{namespace}'.",
            )
        logger.error("Kubernetes API error during get_pod_logs for '%s': %s", pod_name, exc)
        return format_api_exception(tool="get_pod_logs", namespace=namespace, exc=exc)
    except Exception as exc:
        logger.error("Unexpected error during get_pod_logs for '%s': %s", pod_name, exc)
        return error_response(
            tool="get_pod_logs",
            namespace=namespace,
            code="KUBERNETES_CONNECTION_ERROR",
            message=f"Unable to retrieve logs from Kubernetes API: {str(exc)}",
        )


def handle_get_resource_config(
    pod_name: str,
    namespace: str = "k8s-agent-demo",
    v1: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Inspect a workload's resource configuration: images, requests, limits, commands,
    probes, and referenced ConfigMaps/Secrets (names only).
    SECURITY GUARANTEE: Never retrieves, logs, or exposes actual Secret values.
    Read-only.
    """
    ns_err = validate_namespace(namespace)
    if ns_err:
        return error_response(tool="get_resource_config", namespace=namespace, code="INVALID_PARAMETER", message=ns_err)

    pod_err = validate_resource_name(pod_name, param_name="pod_name")
    if pod_err:
        return error_response(tool="get_resource_config", namespace=namespace, code="INVALID_PARAMETER", message=pod_err)

    client = v1 or get_default_k8s_client()
    try:
        api = _init_kubernetes_client(client)
        pod = api.read_namespaced_pod(name=pod_name, namespace=namespace)

        containers_config: List[Dict[str, Any]] = []
        cm_refs: Set[str] = set()
        secret_refs: Set[str] = set()
        pvc_refs: Set[str] = set()

        # Extract volume references
        for vol in (pod.spec.volumes or []):
            cfg = getattr(vol, "config_map", None)
            if cfg is not None and isinstance(getattr(cfg, "name", None), str):
                cm_refs.add(cfg.name)

            sec = getattr(vol, "secret", None)
            if sec is not None and isinstance(getattr(sec, "secret_name", None), str):
                secret_refs.add(sec.secret_name)

            pvc = getattr(vol, "persistent_volume_claim", None)
            if pvc is not None and isinstance(getattr(pvc, "claim_name", None), str):
                pvc_refs.add(pvc.claim_name)

        # Extract container-level specifications
        for container in (pod.spec.containers or []):
            # Environment variable references
            for env_from in (getattr(container, "env_from", None) or []):
                cm_ref = getattr(env_from, "config_map_ref", None)
                if cm_ref is not None and isinstance(getattr(cm_ref, "name", None), str):
                    cm_refs.add(cm_ref.name)
                sec_ref = getattr(env_from, "secret_ref", None)
                if sec_ref is not None and isinstance(getattr(sec_ref, "name", None), str):
                    secret_refs.add(sec_ref.name)

            for env_var in (getattr(container, "env", None) or []):
                val_from = getattr(env_var, "value_from", None)
                if val_from is not None:
                    cm_k = getattr(val_from, "config_map_key_ref", None)
                    if cm_k is not None and isinstance(getattr(cm_k, "name", None), str):
                        cm_refs.add(cm_k.name)
                    sec_k = getattr(val_from, "secret_key_ref", None)
                    if sec_k is not None and isinstance(getattr(sec_k, "name", None), str):
                        secret_refs.add(sec_k.name)

            res_obj = getattr(container, "resources", None)
            res_req = getattr(res_obj, "requests", {}) or {}
            res_lim = getattr(res_obj, "limits", {}) or {}

            containers_config.append({
                "name": container.name,
                "image": container.image,
                "command": list(getattr(container, "command", None) or []),
                "args": list(getattr(container, "args", None) or []),
                "resources": {
                    "requests": dict(res_req),
                    "limits": dict(res_lim),
                },
                "probes": {
                    "readiness": _parse_probe(getattr(container, "readiness_probe", None)),
                    "liveness": _parse_probe(getattr(container, "liveness_probe", None)),
                    "startup": _parse_probe(getattr(container, "startup_probe", None)),
                },
            })

        return success_response(
            tool="get_resource_config",
            namespace=namespace,
            data={
                "pod": pod_name,
                "labels": dict(getattr(pod.metadata, "labels", {}) or {}),
                "containers": containers_config,
                "volumes": [v.name for v in (pod.spec.volumes or [])],
                "configmap_references": sorted(str(r) for r in cm_refs),
                "secret_references": sorted(str(r) for r in secret_refs),
                "pvc_references": sorted(str(r) for r in pvc_refs),
            },
        )
    except ApiException as exc:
        if exc.status == 404:
            return error_response(
                tool="get_resource_config",
                namespace=namespace,
                code="POD_NOT_FOUND",
                message=f"Pod '{pod_name}' was not found in namespace '{namespace}'.",
            )
        logger.error("Kubernetes API error during get_resource_config for '%s': %s", pod_name, exc)
        return format_api_exception(tool="get_resource_config", namespace=namespace, exc=exc)
    except Exception as exc:
        logger.error("Unexpected error during get_resource_config for '%s': %s", pod_name, exc)
        return error_response(
            tool="get_resource_config",
            namespace=namespace,
            code="KUBERNETES_CONNECTION_ERROR",
            message=f"Unable to retrieve resource configuration from Kubernetes API: {str(exc)}",
        )
