from typing import Any, Dict, List, Optional, Set


def _build_diagnosis(
    failure_type: str,
    severity: str,
    resource_name: str,
    namespace: str,
    summary: str,
    root_cause: str,
    evidence: List[str],
    recommended_action: str,
    pod: Optional[str] = None,
    container: Optional[str] = None,
    confidence: str = "HIGH",
) -> Dict[str, Any]:
    """Helper to construct a standardized, backwards-compatible diagnosis dictionary."""
    return {
        "failure_type": failure_type,
        "severity": severity,
        "resource_name": resource_name,
        "namespace": namespace,
        "summary": summary,
        "root_cause": root_cause,
        "evidence": evidence,
        "recommended_action": recommended_action,
        # Backwards compatibility fields
        "pod": pod,
        "container": container,
        "type": failure_type,
        "confidence": confidence,
    }


def _diagnose_pod_level(pod: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Diagnose Pod-level scheduling and volume binding failures."""
    diagnoses: List[Dict[str, Any]] = []
    pod_name = pod.get("pod", "unknown-pod")
    namespace = pod.get("namespace", "default")
    events = pod.get("events", [])
    related_resources = pod.get("related_resources", {})
    pvcs = related_resources.get("pvcs", [])

    # 1. Scenario 9: UNBOUND_PVC (PersistentVolumeClaim binding / provisioning failure)
    has_unbound_pvc_event = any(
        e.get("reason") == "FailedScheduling"
        and any(
            phrase in (e.get("message") or "").lower()
            for phrase in [
                "unbound immediate persistentvolumeclaims",
                "unbound persistentvolumeclaim",
                "unbound immediate pvc",
                "has unbound immediate",
            ]
        )
        for e in events
    )
    unbound_pvcs = [
        pvc for pvc in pvcs
        if pvc.get("phase") in ("Pending", "NotFound")
        or not pvc.get("exists", False)
        or any(
            pe.get("reason") == "ProvisioningFailed"
            for pe in pvc.get("events", [])
        )
    ]

    if has_unbound_pvc_event or (pod.get("phase") == "Pending" and unbound_pvcs):
        for pvc in (unbound_pvcs or pvcs or [{"name": "unbound-pvc", "phase": "Pending"}]):
            pvc_name = pvc.get("name", "unknown-pvc")
            sc_name = pvc.get("storage_class_name")
            pvc_events = pvc.get("events", [])
            evidence = [
                f"Pod '{pod_name}' scheduling blocked by unbound PersistentVolumeClaim '{pvc_name}'",
                f"PVC Phase: {pvc.get('phase', 'Pending')}",
            ]
            if sc_name:
                evidence.append(f"StorageClass requested: {sc_name}")

            for pe in pvc_events:
                if pe.get("message"):
                    evidence.append(f"PVC Event [{pe.get('reason')}]: {pe.get('message')}")

            for e in events:
                if "unbound" in (e.get("message") or "").lower():
                    evidence.append(f"Pod Event [{e.get('reason')}]: {e.get('message')}")

            diagnoses.append(
                _build_diagnosis(
                    failure_type="UNBOUND_PVC",
                    severity="HIGH",
                    resource_name=f"{pod_name} (PVC: {pvc_name})",
                    namespace=namespace,
                    summary=f"Pod '{pod_name}' cannot be scheduled because PersistentVolumeClaim '{pvc_name}' is unbound.",
                    root_cause=(
                        f"The requested StorageClass '{sc_name or 'default'}' does not exist or dynamic "
                        f"provisioning failed for PVC '{pvc_name}'."
                    ),
                    evidence=evidence,
                    recommended_action=(
                        f"Verify that StorageClass '{sc_name or 'default'}' exists and has an active provisioner, "
                        f"or manually create a matching PersistentVolume for claim '{pvc_name}'."
                    ),
                    pod=pod_name,
                    container=None,
                )
            )

    # 2. Scenario 4: INSUFFICIENT_CLUSTER_RESOURCES (Insufficient CPU / Memory)
    # Strictly distinguish from UNBOUND_PVC
    if not diagnoses:
        scheduling_events = [
            e for e in events
            if e.get("reason") == "FailedScheduling"
            and any(
                res in (e.get("message") or "").lower()
                for res in ["insufficient cpu", "insufficient memory", "insufficient pods"]
            )
            and not any(
                pvc_phrase in (e.get("message") or "").lower()
                for pvc_phrase in ["unbound immediate", "persistentvolumeclaim"]
            )
        ]

        if scheduling_events or (
            pod.get("phase") == "Pending"
            and any(
                "insufficient" in (e.get("message") or "").lower()
                for e in events
                if e.get("reason") == "FailedScheduling"
            )
        ):
            ev_messages = [e.get("message") for e in scheduling_events if e.get("message")]
            evidence = [
                f"Pod '{pod_name}' failed scheduling due to insufficient cluster compute capacity",
                f"Pod Phase: {pod.get('phase')}",
            ]
            evidence.extend([f"Scheduling Event: {msg}" for msg in ev_messages])

            diagnoses.append(
                _build_diagnosis(
                    failure_type="INSUFFICIENT_CLUSTER_RESOURCES",
                    severity="HIGH",
                    resource_name=pod_name,
                    namespace=namespace,
                    summary=f"Pod '{pod_name}' cannot be scheduled due to insufficient CPU or resource capacity in the cluster.",
                    root_cause="The Kubernetes scheduler found no cluster node with enough available allocatable capacity to meet the Pod's resource requests.",
                    evidence=evidence,
                    recommended_action="Decrease resource requests in spec.containers[].resources.requests, or scale up cluster nodes to add compute capacity.",
                    pod=pod_name,
                    container=None,
                )
            )

    return diagnoses


def _diagnose_container(
    pod: Dict[str, Any],
    container: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Diagnose container-level failures using state, last_state, events, logs, and probes."""
    diagnoses: List[Dict[str, Any]] = []
    pod_name = pod.get("pod", "unknown-pod")
    namespace = pod.get("namespace", "default")
    container_name = container.get("name", "app")
    image = container.get("image", "unknown-image")

    state = container.get("state") or {}
    last_state = container.get("last_state") or {}
    spec = container.get("spec") or {}
    logs = container.get("logs") or {}
    current_logs = logs.get("current") or ""
    previous_logs = logs.get("previous") or ""
    all_logs = f"{current_logs}\n{previous_logs}".strip()

    current_status = state.get("status")
    current_reason = state.get("reason") or ""
    current_message = state.get("message") or ""

    last_reason = last_state.get("reason") or ""
    last_message = last_state.get("message") or ""
    exit_code = last_state.get("exit_code")
    restart_count = container.get("restart_count", 0)
    ready = container.get("ready", False)

    pod_events = pod.get("events", [])
    related_resources = pod.get("related_resources", {})

    # Helper text corpus for pattern matching
    full_text = f"{current_reason} {current_message} {last_reason} {last_message} {' '.join(e.get('message', '') for e in pod_events)}".lower()

    # Rule 1: IMAGE_PULL_FAILURE
    if (
        current_reason in ("ImagePullBackOff", "ErrImagePull", "ImageInspectError")
        or any(
            e.get("reason") in ("Failed", "ErrImagePull", "ImagePullBackOff", "BackOff")
            and any(kw in (e.get("message") or "").lower() for kw in ["pull image", "pulling image", "errimagepull", "imagepullbackoff", "manifest unknown"])
            for e in pod_events
        )
    ):
        evidence = [
            f"Container '{container_name}' failed to retrieve image: {image}",
            f"Container State: {current_reason or 'ImagePullError'}",
        ]
        if current_message:
            evidence.append(f"State Message: {current_message}")
        for e in pod_events:
            if any(kw in (e.get("message") or "").lower() for kw in ["pull", "image"]):
                evidence.append(f"Event [{e.get('reason')}]: {e.get('message')}")

        return [
            _build_diagnosis(
                failure_type="IMAGE_PULL_FAILURE",
                severity="HIGH",
                resource_name=f"{pod_name}/{container_name}",
                namespace=namespace,
                summary=f"Container '{container_name}' cannot retrieve image '{image}'.",
                root_cause="Kubernetes failed to pull the specified image tag from the container registry (image not found or missing registry credentials).",
                evidence=evidence,
                recommended_action=f"Verify that image '{image}' exists in the container registry, check tag spelling, and ensure imagePullSecrets are configured if private.",
                pod=pod_name,
                container=container_name,
            )
        ]

    # Rule 2: MISSING_CONFIGMAP (CreateContainerConfigError due to ConfigMap)
    cm_missing = (
        current_reason in ("CreateContainerConfigError", "CreateContainerError")
        and "configmap" in full_text
        and "not found" in full_text
    ) or any(
        not cm.get("exists", True) and f"container:{container_name}" in " ".join(cm.get("referenced_by", []))
        for cm in related_resources.get("configmaps", [])
    )

    if cm_missing:
        missing_cm_names = [
            cm.get("name") for cm in related_resources.get("configmaps", [])
            if not cm.get("exists", True)
        ]
        evidence = [
            f"Container '{container_name}' creation blocked by missing ConfigMap",
            f"Container Waiting Reason: {current_reason}",
        ]
        if current_message:
            evidence.append(f"State Message: {current_message}")
        if missing_cm_names:
            evidence.append(f"Missing ConfigMap: {', '.join(missing_cm_names)}")
        for e in pod_events:
            if "configmap" in (e.get("message") or "").lower():
                evidence.append(f"Event [{e.get('reason')}]: {e.get('message')}")

        return [
            _build_diagnosis(
                failure_type="MISSING_CONFIGMAP",
                severity="HIGH",
                resource_name=f"{pod_name}/{container_name}",
                namespace=namespace,
                summary=f"Container '{container_name}' cannot start because a referenced ConfigMap does not exist.",
                root_cause="Kubernetes cannot inject environment variables or mount volumes because the requested ConfigMap was not found in the namespace.",
                evidence=evidence,
                recommended_action=f"Create the missing ConfigMap in namespace '{namespace}' or correct the configMapKeyRef / configMapRef in the Pod specification.",
                pod=pod_name,
                container=container_name,
            )
        ]

    # Rule 3: MISSING_SECRET (CreateContainerConfigError due to Secret)
    # SECURITY: Never expose secret values
    secret_missing = (
        current_reason in ("CreateContainerConfigError", "CreateContainerError")
        and "secret" in full_text
        and "not found" in full_text
    ) or any(
        not s.get("exists", True) and f"container:{container_name}" in " ".join(s.get("referenced_by", []))
        for s in related_resources.get("secrets", [])
    )

    if secret_missing:
        missing_secret_names = [
            s.get("name") for s in related_resources.get("secrets", [])
            if not s.get("exists", True)
        ]
        evidence = [
            f"Container '{container_name}' creation blocked by missing Secret reference",
            f"Container Waiting Reason: {current_reason}",
        ]
        if current_message:
            evidence.append(f"State Message: {current_message}")
        if missing_secret_names:
            evidence.append(f"Missing Secret Name: {', '.join(missing_secret_names)}")
        for e in pod_events:
            if "secret" in (e.get("message") or "").lower():
                evidence.append(f"Event [{e.get('reason')}]: {e.get('message')}")

        return [
            _build_diagnosis(
                failure_type="MISSING_SECRET",
                severity="HIGH",
                resource_name=f"{pod_name}/{container_name}",
                namespace=namespace,
                summary=f"Container '{container_name}' cannot start because a referenced Secret does not exist.",
                root_cause="Kubernetes cannot inject secret environment variables or mount secret volumes because the referenced Secret does not exist.",
                evidence=evidence,
                recommended_action=f"Create the required Secret in namespace '{namespace}' or correct the secretKeyRef / secretRef in the Pod specification without checking secrets into version control.",
                pod=pod_name,
                container=container_name,
            )
        ]

    # Rule 4: MEMORY_EXHAUSTION (OOMKilled)
    if (
        last_reason == "OOMKilled"
        or current_reason == "OOMKilled"
        or (exit_code == 137 and any("oom" in (e.get("message") or "").lower() for e in pod_events))
    ):
        mem_limit = spec.get("resources", {}).get("limits", {}).get("memory", "unspecified")
        evidence = [
            "Container was terminated due to memory exhaustion (OOMKilled)",
            f"Exit code: {exit_code}",
            f"Configured memory limit: {mem_limit}",
            f"Restart count: {restart_count}",
        ]
        for e in pod_events:
            if any(term in (e.get("message") or "").lower() for term in ["oom", "out of memory"]):
                evidence.append(f"Event [{e.get('reason')}]: {e.get('message')}")

        return [
            _build_diagnosis(
                failure_type="MEMORY_EXHAUSTION",
                severity="HIGH",
                resource_name=f"{pod_name}/{container_name}",
                namespace=namespace,
                summary=f"Container '{container_name}' was killed due to memory exhaustion (OOMKilled).",
                root_cause=f"The container exceeded its configured memory limit ({mem_limit}) or host node memory, triggering kernel OOM termination (exit code 137).",
                evidence=evidence,
                recommended_action="Increase container memory limits in spec.containers[].resources.limits.memory, or investigate application code for memory leaks.",
                pod=pod_name,
                container=container_name,
            )
        ]

    # Rule 5: LIVENESS_PROBE_FAILURE
    # Evaluated before startup/application command to ensure probe-driven restarts are correctly classified
    has_liveness_event = any(
        (e.get("reason") == "Unhealthy" and "liveness probe failed" in (e.get("message") or "").lower())
        or (e.get("reason") == "Killing" and "failed liveness probe" in (e.get("message") or "").lower())
        for e in pod_events
    )

    if has_liveness_event and restart_count > 0:
        liveness_spec = spec.get("probes", {}).get("liveness") or {}
        evidence = [
            f"Container '{container_name}' failed liveness probe check",
            f"Container restart count: {restart_count}",
        ]
        if liveness_spec:
            evidence.append(f"Liveness probe configuration: {liveness_spec}")
        for e in pod_events:
            if "liveness" in (e.get("message") or "").lower():
                evidence.append(f"Event [{e.get('reason')}]: {e.get('message')}")

        return [
            _build_diagnosis(
                failure_type="LIVENESS_PROBE_FAILURE",
                severity="HIGH",
                resource_name=f"{pod_name}/{container_name}",
                namespace=namespace,
                summary=f"Container '{container_name}' failed liveness probe, triggering repeated restarts.",
                root_cause="The container failed consecutive liveness health checks, prompting the kubelet to restart the container.",
                evidence=evidence,
                recommended_action="Inspect application health responsiveness, adjust initialDelaySeconds or failureThreshold, or fix failing probe endpoints.",
                pod=pod_name,
                container=container_name,
            )
        ]

    # Rule 6: INVALID_STARTUP_COMMAND (Nonexistent binary / RunContainerError / StartError / Exit code 127)
    # Strictly distinguish from ordinary APPLICATION_PROCESS_FAILURE
    # Only inspect container runtime messages and container start events, excluding probe events
    container_start_events = [
        e for e in pod_events
        if e.get("reason") in ("Failed", "CreateContainerError", "RunContainerError")
        and any(
            phrase in (e.get("message") or "").lower()
            for phrase in [
                "failed to start container",
                "executable file not found",
                "oci runtime create failed",
                "unable to start container process",
                "failed to create containerd task",
                "no such file or directory",
            ]
        )
    ]
    container_runtime_msgs = f"{current_reason} {current_message} {last_reason} {last_message}".lower()

    is_invalid_cmd = (
        exit_code == 127
        or last_reason in ("ContainerCannotRun", "StartError", "RunContainerError")
        or current_reason in ("RunContainerError", "StartError")
        or bool(container_start_events)
        or any(
            phrase in container_runtime_msgs
            for phrase in [
                "executable file not found",
                "oci runtime create failed",
                "unable to start container process: exec",
                "failed to create containerd task",
            ]
        )
    )

    if is_invalid_cmd:
        evidence = [
            f"Container '{container_name}' failed to execute startup command",
            f"Last Exit Code: {exit_code}",
            f"Last Reason: {last_reason or current_reason}",
        ]
        if last_message:
            evidence.append(f"Termination Message: {last_message}")
        if current_message:
            evidence.append(f"State Message: {current_message}")
        for e in container_start_events:
            evidence.append(f"Event [{e.get('reason')}]: {e.get('message')}")

        return [
            _build_diagnosis(
                failure_type="INVALID_STARTUP_COMMAND",
                severity="HIGH",
                resource_name=f"{pod_name}/{container_name}",
                namespace=namespace,
                summary=f"Container '{container_name}' failed to start because the entrypoint command or binary was not found.",
                root_cause="The container runtime could not locate or execute the specified command in the container filesystem or PATH.",
                evidence=evidence,
                recommended_action="Check spec.containers[].command and spec.containers[].args to ensure the specified executable exists in the container image.",
                pod=pod_name,
                container=container_name,
            )
        ]

    # Rule 7: READINESS_PROBE_FAILURE
    # Differentiate from LIVENESS_PROBE_FAILURE: Container is running, not restarted by readiness
    has_readiness_event = any(
        e.get("reason") == "Unhealthy" and "readiness probe failed" in (e.get("message") or "").lower()
        for e in pod_events
    )
    is_unready_running = (
        current_status == "Running"
        and not ready
        and spec.get("probes", {}).get("readiness") is not None
    )

    if has_readiness_event or is_unready_running:
        readiness_spec = spec.get("probes", {}).get("readiness") or {}
        evidence = [
            f"Container '{container_name}' is running but failed readiness probe (ready: {ready})",
        ]
        if readiness_spec:
            evidence.append(f"Readiness probe configuration: {readiness_spec}")
        for e in pod_events:
            if "readiness" in (e.get("message") or "").lower():
                evidence.append(f"Event [{e.get('reason')}]: {e.get('message')}")

        return [
            _build_diagnosis(
                failure_type="READINESS_PROBE_FAILURE",
                severity="MEDIUM",
                resource_name=f"{pod_name}/{container_name}",
                namespace=namespace,
                summary=f"Readiness probe failed for container '{container_name}', preventing traffic routing.",
                root_cause="The container process is running but failing its readiness check, causing the Pod to remain unready (0/1 Ready).",
                evidence=evidence,
                recommended_action="Verify probe endpoint path and port against actual application HTTP routes or scripts, and verify downstream dependency health.",
                pod=pod_name,
                container=container_name,
            )
        ]

    # Rule 8: APPLICATION_PROCESS_FAILURE
    # Application started and executed, but exited unexpectedly (e.g. exit code 1)
    if exit_code is not None and exit_code not in (0, 127, 137):
        evidence = [
            f"Application process exited with non-zero status code: {exit_code}",
            f"Last termination reason: {last_reason or 'Error'}",
            f"Current container state: {current_reason or current_status}",
            f"Restart count: {restart_count}",
        ]
        if current_logs and "Unavailable:" not in current_logs:
            evidence.append(f"Recent application logs:\n{current_logs.strip()}")
        elif all_logs and "Unavailable:" not in all_logs:
            evidence.append(f"Recent application logs:\n{all_logs.strip()}")

        return [
            _build_diagnosis(
                failure_type="APPLICATION_PROCESS_FAILURE",
                severity="HIGH",
                resource_name=f"{pod_name}/{container_name}",
                namespace=namespace,
                summary=f"Container '{container_name}' process exited unexpectedly with exit code {exit_code}.",
                root_cause=f"The application code or script executed and terminated with non-zero exit code {exit_code}.",
                evidence=evidence,
                recommended_action="Inspect container logs and application configuration to address unhandled runtime exceptions or configuration errors.",
                pod=pod_name,
                container=container_name,
            )
        ]

    # Healthy or unclassified container: No false positive
    return diagnoses


def diagnose_pod(pod: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Apply deterministic, correlated diagnosis rules to collected Kubernetes evidence for a single Pod.
    Evaluates both Pod-level (scheduling/PVC) and container-level failure modes.
    """
    diagnoses: List[Dict[str, Any]] = []

    # 1. Evaluate Pod-level scheduling and resource binding issues
    pod_diagnoses = _diagnose_pod_level(pod)
    diagnoses.extend(pod_diagnoses)

    # 2. Evaluate Container-level failures
    for container in pod.get("containers", []):
        container_diagnoses = _diagnose_container(pod, container)
        diagnoses.extend(container_diagnoses)

    return diagnoses


def diagnose_cluster(cluster_evidence: Dict[str, Any]) -> Dict[str, Any]:
    """Diagnose all Pods in a cluster evidence report and aggregate results."""
    results: List[Dict[str, Any]] = []

    for pod in cluster_evidence.get("pods", []):
        results.extend(diagnose_pod(pod))

    return {
        "namespace": cluster_evidence.get("namespace"),
        "diagnosis_count": len(results),
        "diagnoses": results,
    }
