from typing import Any, Dict, List, Optional, Set
from kubernetes import client, config
from kubernetes.client.rest import ApiException


def _container_state(state: Any) -> Optional[Dict[str, Any]]:
    """Convert Kubernetes container state into structured Python dictionary."""
    if state is None:
        return None

    if isinstance(state, dict):
        return state

    if getattr(state, "waiting", None):
        return {
            "status": "Waiting",
            "reason": state.waiting.reason,
            "message": state.waiting.message,
        }

    if getattr(state, "running", None):
        return {
            "status": "Running",
            "started_at": (
                state.running.started_at.isoformat()
                if getattr(state.running, "started_at", None)
                else None
            ),
        }

    if getattr(state, "terminated", None):
        return {
            "status": "Terminated",
            "reason": state.terminated.reason,
            "message": state.terminated.message,
            "exit_code": state.terminated.exit_code,
            "started_at": (
                state.terminated.started_at.isoformat()
                if getattr(state.terminated, "started_at", None)
                else None
            ),
            "finished_at": (
                state.terminated.finished_at.isoformat()
                if getattr(state.terminated, "finished_at", None)
                else None
            ),
        }

    return None


def _parse_probe(probe: Any) -> Optional[Dict[str, Any]]:
    """Extract structured probe configuration from a container probe specification."""
    if probe is None:
        return None

    if isinstance(probe, dict):
        return probe

    probe_dict: Dict[str, Any] = {
        "initial_delay_seconds": getattr(probe, "initial_delay_seconds", None),
        "period_seconds": getattr(probe, "period_seconds", None),
        "timeout_seconds": getattr(probe, "timeout_seconds", None),
        "failure_threshold": getattr(probe, "failure_threshold", None),
        "success_threshold": getattr(probe, "success_threshold", None),
    }

    http_get = getattr(probe, "http_get", None)
    if http_get:
        probe_dict["http_get"] = {
            "path": getattr(http_get, "path", None),
            "port": getattr(http_get, "port", None),
            "scheme": getattr(http_get, "scheme", None),
            "host": getattr(http_get, "host", None),
        }

    exec_action = getattr(probe, "_exec", None) or getattr(probe, "exec", None)
    if exec_action:
        probe_dict["exec"] = {
            "command": getattr(exec_action, "command", None),
        }

    tcp_socket = getattr(probe, "tcp_socket", None)
    if tcp_socket:
        probe_dict["tcp_socket"] = {
            "port": getattr(tcp_socket, "port", None),
            "host": getattr(tcp_socket, "host", None),
        }

    return probe_dict


def _init_kubernetes_client(v1: Optional[client.CoreV1Api] = None) -> client.CoreV1Api:
    """Initialize CoreV1Api with in-cluster or local kubeconfig, or return existing client."""
    if v1 is not None:
        return v1

    try:
        config.load_incluster_config()
    except Exception:
        try:
            config.load_kube_config()
        except Exception:
            pass

    return client.CoreV1Api()


def collect_cluster_evidence(
    namespace: str = "k8s-agent-demo",
    v1: Optional[client.CoreV1Api] = None,
) -> Dict[str, Any]:
    """
    Read-only collector for comprehensive Kubernetes Pod, container, and related resource evidence.
    Gracefully handles missing resources, API exceptions, and avoids duplicate API calls.
    SECURITY GUARANTEE: Never retrieves, serializes, logs, or exposes actual Secret values.
    """
    api = _init_kubernetes_client(v1)

    # 1. Fetch Pods in target namespace
    pods = api.list_namespaced_pod(namespace=namespace)

    # 2. Fetch Namespace Events once for all pods and PVCs in this namespace
    try:
        raw_events = api.list_namespaced_event(namespace=namespace).items
    except Exception:
        raw_events = []

    # 3. Fetch related namespace resources once to avoid repeated network round-trips
    try:
        raw_pvcs = api.list_namespaced_persistent_volume_claim(namespace=namespace).items
        pvc_map = {
            pvc.metadata.name: pvc
            for pvc in raw_pvcs
            if getattr(pvc, "metadata", None) and getattr(pvc.metadata, "name", None)
        }
    except Exception:
        pvc_map = {}

    try:
        raw_cms = api.list_namespaced_config_map(namespace=namespace).items
        known_configmaps: Set[str] = {
            cm.metadata.name
            for cm in raw_cms
            if getattr(cm, "metadata", None) and getattr(cm.metadata, "name", None)
        }
    except Exception:
        known_configmaps = set()

    try:
        raw_secrets = api.list_namespaced_secret(namespace=namespace).items
        # SECURITY REQUIREMENT: Only collect names to verify existence. NEVER access data or string_data!
        known_secrets: Set[str] = {
            s.metadata.name
            for s in raw_secrets
            if getattr(s, "metadata", None) and getattr(s.metadata, "name", None)
        }
    except Exception:
        known_secrets = set()

    results: List[Dict[str, Any]] = []

    for pod in pods.items:
        pod_name = pod.metadata.name
        container_evidence: List[Dict[str, Any]] = []

        statuses = {
            status.name: status
            for status in (pod.status.container_statuses or [])
        }

        # Track referenced resources for this Pod
        referenced_cms: Dict[str, List[str]] = {}
        referenced_secrets: Dict[str, List[str]] = {}
        referenced_pvc_names: Set[str] = set()

        # Check Pod volumes for ConfigMaps, Secrets, and PVCs
        for vol in (pod.spec.volumes or []):
            if getattr(vol, "config_map", None) and vol.config_map.name:
                referenced_cms.setdefault(vol.config_map.name, []).append(f"volume:{vol.name}")
            if getattr(vol, "secret", None) and vol.secret.secret_name:
                referenced_secrets.setdefault(vol.secret.secret_name, []).append(f"volume:{vol.name}")
            if getattr(vol, "persistent_volume_claim", None) and vol.persistent_volume_claim.claim_name:
                referenced_pvc_names.add(vol.persistent_volume_claim.claim_name)

        for container in pod.spec.containers:
            status = statuses.get(container.name)

            # Inspect env and env_from for ConfigMaps and Secrets
            for env_from in (getattr(container, "env_from", None) or []):
                cm_ref = getattr(env_from, "config_map_ref", None)
                if cm_ref and getattr(cm_ref, "name", None):
                    referenced_cms.setdefault(cm_ref.name, []).append(f"container:{container.name}:envFrom")

                sec_ref = getattr(env_from, "secret_ref", None)
                if sec_ref and getattr(sec_ref, "name", None):
                    referenced_secrets.setdefault(sec_ref.name, []).append(f"container:{container.name}:envFrom")

            for env_item in (getattr(container, "env", None) or []):
                val_from = getattr(env_item, "value_from", None)
                if val_from:
                    cm_key_ref = getattr(val_from, "config_map_key_ref", None)
                    if cm_key_ref and getattr(cm_key_ref, "name", None):
                        referenced_cms.setdefault(cm_key_ref.name, []).append(
                            f"container:{container.name}:env:{env_item.name}"
                        )

                    sec_key_ref = getattr(val_from, "secret_key_ref", None)
                    if sec_key_ref and getattr(sec_key_ref, "name", None):
                        referenced_secrets.setdefault(sec_key_ref.name, []).append(
                            f"container:{container.name}:env:{env_item.name}"
                        )

            # Resource requests and limits
            res_obj = getattr(container, "resources", None)
            res_requests = getattr(res_obj, "requests", {}) or {}
            res_limits = getattr(res_obj, "limits", {}) or {}

            # Probes specifications
            spec_evidence = {
                "resources": {
                    "requests": dict(res_requests),
                    "limits": dict(res_limits),
                },
                "probes": {
                    "readiness": _parse_probe(getattr(container, "readiness_probe", None)),
                    "liveness": _parse_probe(getattr(container, "liveness_probe", None)),
                    "startup": _parse_probe(getattr(container, "startup_probe", None)),
                },
            }

            item: Dict[str, Any] = {
                "name": container.name,
                "image": container.image,
                "image_id": getattr(status, "image_id", None) if status else None,
                "ready": getattr(status, "ready", False) if status else False,
                "restart_count": getattr(status, "restart_count", 0) if status else 0,
                "state": _container_state(status.state) if status else None,
                "last_state": (
                    _container_state(status.last_state) if status else None
                ),
                "spec": spec_evidence,
                "logs": {
                    "current": None,
                    "previous": None,
                },
            }

            # Retrieve recent logs from the current container
            try:
                item["logs"]["current"] = api.read_namespaced_pod_log(
                    name=pod_name,
                    namespace=namespace,
                    container=container.name,
                    tail_lines=30,
                )
            except ApiException as exc:
                item["logs"]["current"] = f"Unavailable: {exc.reason}"
            except Exception as exc:
                item["logs"]["current"] = f"Unavailable: {str(exc)}"

            # Retrieve previous-container logs when a restart has occurred
            if status and getattr(status, "restart_count", 0) > 0:
                try:
                    item["logs"]["previous"] = api.read_namespaced_pod_log(
                        name=pod_name,
                        namespace=namespace,
                        container=container.name,
                        previous=True,
                        tail_lines=30,
                    )
                except ApiException as exc:
                    item["logs"]["previous"] = f"Unavailable: {exc.reason}"
                except Exception as exc:
                    item["logs"]["previous"] = f"Unavailable: {str(exc)}"

            container_evidence.append(item)

        # Collect events specifically associated with this Pod
        pod_events = [
            e for e in raw_events
            if getattr(getattr(e, "involved_object", None), "kind", None) == "Pod"
            and getattr(getattr(e, "involved_object", None), "name", None) == pod_name
        ]

        event_evidence = [
            {
                "type": getattr(event, "type", None),
                "reason": getattr(event, "reason", None),
                "message": getattr(event, "message", None),
                "count": getattr(event, "count", 1) or 1,
                "first_timestamp": (
                    event.first_timestamp.isoformat()
                    if getattr(event, "first_timestamp", None)
                    else None
                ),
                "last_timestamp": (
                    event.last_timestamp.isoformat()
                    if getattr(event, "last_timestamp", None)
                    else None
                ),
            }
            for event in pod_events
        ]

        # Pod conditions and readiness
        conditions = [
            {
                "type": getattr(c, "type", None),
                "status": getattr(c, "status", None),
                "reason": getattr(c, "reason", None),
                "message": getattr(c, "message", None),
                "last_transition_time": (
                    c.last_transition_time.isoformat()
                    if getattr(c, "last_transition_time", None)
                    else None
                ),
            }
            for c in (pod.status.conditions or [])
        ]
        is_ready = any(
            c.get("type") == "Ready" and c.get("status") == "True"
            for c in conditions
        )

        # Related PVC evidence
        pvc_evidence: List[Dict[str, Any]] = []
        for claim_name in sorted(referenced_pvc_names):
            pvc_events = [
                {
                    "type": getattr(e, "type", None),
                    "reason": getattr(e, "reason", None),
                    "message": getattr(e, "message", None),
                    "count": getattr(e, "count", 1) or 1,
                }
                for e in raw_events
                if getattr(getattr(e, "involved_object", None), "kind", None) == "PersistentVolumeClaim"
                and getattr(getattr(e, "involved_object", None), "name", None) == claim_name
            ]

            pvc_obj = pvc_map.get(claim_name)
            if pvc_obj:
                spec_res = getattr(pvc_obj.spec, "resources", None)
                req_map = getattr(spec_res, "requests", {}) or {}
                storage_req = req_map.get("storage") if isinstance(req_map, dict) else None

                pvc_evidence.append({
                    "name": claim_name,
                    "exists": True,
                    "phase": getattr(pvc_obj.status, "phase", None) if pvc_obj.status else None,
                    "storage_class_name": getattr(pvc_obj.spec, "storage_class_name", None),
                    "requested_storage": storage_req,
                    "events": pvc_events,
                })
            else:
                pvc_evidence.append({
                    "name": claim_name,
                    "exists": False,
                    "phase": "NotFound",
                    "storage_class_name": None,
                    "requested_storage": None,
                    "events": pvc_events,
                })

        # Related ConfigMaps evidence
        cm_evidence = [
            {
                "name": cm_name,
                "exists": cm_name in known_configmaps,
                "referenced_by": refs,
            }
            for cm_name, refs in sorted(referenced_cms.items())
        ]

        # Related Secrets evidence (NO secret data, only metadata)
        secret_evidence = [
            {
                "name": sec_name,
                "exists": sec_name in known_secrets,
                "referenced_by": refs,
            }
            for sec_name, refs in sorted(referenced_secrets.items())
        ]

        results.append({
            "pod": pod_name,
            "namespace": namespace,
            "phase": getattr(pod.status, "phase", None),
            "pod_reason": getattr(pod.status, "reason", None),
            "pod_message": getattr(pod.status, "message", None),
            "labels": dict(getattr(pod.metadata, "labels", {}) or {}),
            "node_name": getattr(pod.spec, "node_name", None),
            "is_scheduled": bool(getattr(pod.spec, "node_name", None)),
            "is_ready": is_ready,
            "conditions": conditions,
            "owners": [
                {
                    "kind": owner.kind,
                    "name": owner.name,
                }
                for owner in (getattr(pod.metadata, "owner_references", None) or [])
            ],
            "containers": container_evidence,
            "events": event_evidence,
            "related_resources": {
                "pvcs": pvc_evidence,
                "configmaps": cm_evidence,
                "secrets": secret_evidence,
            },
        })

    return {
        "namespace": namespace,
        "pod_count": len(results),
        "pods": results,
    }
