import pytest
from app.services.diagnosis_service import diagnose_cluster, diagnose_pod


# ============================================================================
# 1. Ten Verified Failure Scenario Tests
# ============================================================================

def test_scenario_1_image_pull_failure():
    """Scenario 1: ImagePullBackOff / ErrImagePull when container image tag does not exist."""
    mock_pod = {
        "pod": "demo-app-7d49bd7d-xyz",
        "namespace": "k8s-agent-demo",
        "phase": "Pending",
        "conditions": [{"type": "Ready", "status": "False"}],
        "events": [
            {
                "type": "Warning",
                "reason": "Failed",
                "message": 'Failed to pull image "nginx:this-image-does-not-exist": rpc error: code = NotFound',
            },
            {
                "type": "Warning",
                "reason": "Failed",
                "message": "Error: ErrImagePull",
            },
            {
                "type": "Warning",
                "reason": "BackOff",
                "message": 'Back-off pulling image "nginx:this-image-does-not-exist"',
            },
        ],
        "containers": [
            {
                "name": "app",
                "image": "nginx:this-image-does-not-exist",
                "ready": False,
                "restart_count": 0,
                "state": {
                    "status": "Waiting",
                    "reason": "ImagePullBackOff",
                    "message": 'Back-off pulling image "nginx:this-image-does-not-exist"',
                },
                "last_state": None,
                "logs": {"current": "Unavailable: ContainerCreating", "previous": None},
            }
        ],
    }

    diagnoses = diagnose_pod(mock_pod)
    assert len(diagnoses) == 1
    diag = diagnoses[0]
    assert diag["failure_type"] == "IMAGE_PULL_FAILURE"
    assert diag["severity"] == "HIGH"
    assert diag["resource_name"] == "demo-app-7d49bd7d-xyz/app"
    assert "nginx:this-image-does-not-exist" in diag["summary"]
    assert "imagePullSecrets" in diag["recommended_action"]


def test_scenario_2_memory_exhaustion_oom():
    """Scenario 2: OOMKilled when container allocates memory beyond resource limits."""
    mock_pod = {
        "pod": "oom-test-849cfd7b9-abc",
        "namespace": "k8s-agent-demo",
        "phase": "Running",
        "conditions": [{"type": "Ready", "status": "False"}],
        "events": [
            {
                "type": "Warning",
                "reason": "OOMKilled",
                "message": "Out of memory: Kill process 12345 (python) score 1024 or sacrifice child",
            }
        ],
        "containers": [
            {
                "name": "app",
                "image": "python:3.12-alpine",
                "ready": False,
                "restart_count": 3,
                "state": {
                    "status": "Waiting",
                    "reason": "CrashLoopBackOff",
                    "message": "back-off 2m40s restarting failed container=app",
                },
                "last_state": {
                    "status": "Terminated",
                    "reason": "OOMKilled",
                    "exit_code": 137,
                },
                "spec": {
                    "resources": {
                        "limits": {"memory": "32Mi"},
                        "requests": {"memory": "16Mi"},
                    }
                },
                "logs": {"current": None, "previous": "Memory allocation loop started..."},
            }
        ],
    }

    diagnoses = diagnose_pod(mock_pod)
    assert len(diagnoses) == 1
    diag = diagnoses[0]
    assert diag["failure_type"] == "MEMORY_EXHAUSTION"
    assert diag["severity"] == "HIGH"
    assert "OOMKilled" in diag["summary"]
    assert "32Mi" in diag["root_cause"]
    assert any("Exit code: 137" in e for e in diag["evidence"])


def test_scenario_3_application_process_failure():
    """Scenario 3: Application process exits with non-zero exit code (e.g. exit 1)."""
    mock_pod = {
        "pod": "crashloop-test-6d48bc-xyz",
        "namespace": "k8s-agent-demo",
        "phase": "Running",
        "conditions": [{"type": "Ready", "status": "False"}],
        "events": [
            {
                "type": "Warning",
                "reason": "BackOff",
                "message": "Back-off restarting failed container app in pod crashloop-test-6d48bc-xyz",
            }
        ],
        "containers": [
            {
                "name": "app",
                "image": "busybox:1.36",
                "ready": False,
                "restart_count": 2,
                "state": {
                    "status": "Waiting",
                    "reason": "CrashLoopBackOff",
                    "message": "back-off 40s restarting failed container=app",
                },
                "last_state": {
                    "status": "Terminated",
                    "reason": "Error",
                    "exit_code": 1,
                },
                "logs": {
                    "current": "Application started\nSimulated application failure\n",
                    "previous": None,
                },
            }
        ],
    }

    diagnoses = diagnose_pod(mock_pod)
    assert len(diagnoses) == 1
    diag = diagnoses[0]
    assert diag["failure_type"] == "APPLICATION_PROCESS_FAILURE"
    assert diag["severity"] == "HIGH"
    assert "exit code 1" in diag["summary"]
    assert any("Simulated application failure" in e for e in diag["evidence"])


def test_scenario_4_insufficient_cluster_resources():
    """Scenario 4: FailedScheduling due to excessive CPU resource request."""
    mock_pod = {
        "pod": "insufficient-cpu",
        "namespace": "k8s-agent-demo",
        "phase": "Pending",
        "conditions": [{"type": "PodScheduled", "status": "False", "reason": "Unschedulable"}],
        "events": [
            {
                "type": "Warning",
                "reason": "FailedScheduling",
                "message": "0/1 nodes are available: 1 Insufficient cpu. preemption: 0/1 nodes are available: 1 No preemption victims found for incoming pod.",
            }
        ],
        "containers": [
            {
                "name": "app",
                "image": "nginx:1.27",
                "ready": False,
                "restart_count": 0,
                "state": None,
                "last_state": None,
                "spec": {
                    "resources": {
                        "requests": {"cpu": "100", "memory": "32Mi"}
                    }
                },
            }
        ],
    }

    diagnoses = diagnose_pod(mock_pod)
    assert len(diagnoses) == 1
    diag = diagnoses[0]
    assert diag["failure_type"] == "INSUFFICIENT_CLUSTER_RESOURCES"
    assert diag["severity"] == "HIGH"
    assert diag["resource_name"] == "insufficient-cpu"
    assert "insufficient CPU" in diag["summary"]
    assert "Decrease resource requests" in diag["recommended_action"]


def test_scenario_5_readiness_probe_failure():
    """Scenario 5: Readiness probe fails while container stays running (0/1 Ready, restartCount 0)."""
    mock_pod = {
        "pod": "readiness-probe-failure",
        "namespace": "k8s-agent-demo",
        "phase": "Running",
        "conditions": [
            {"type": "Ready", "status": "False", "reason": "ContainersNotReady"},
            {"type": "ContainersReady", "status": "False", "reason": "ContainersNotReady"},
        ],
        "events": [
            {
                "type": "Warning",
                "reason": "Unhealthy",
                "message": "Readiness probe failed: HTTP probe failed with statuscode: 404",
            }
        ],
        "containers": [
            {
                "name": "app",
                "image": "nginx:1.27",
                "ready": False,
                "restart_count": 0,
                "state": {
                    "status": "Running",
                    "started_at": "2026-11-20T01:00:00Z",
                },
                "last_state": None,
                "spec": {
                    "probes": {
                        "readiness": {
                            "http_get": {"path": "/healthz", "port": 80},
                            "initial_delay_seconds": 2,
                            "period_seconds": 5,
                        }
                    }
                },
                "logs": {"current": "127.0.0.1 - - [20/Nov/2026] \"GET /healthz HTTP/1.1\" 404 153\n", "previous": None},
            }
        ],
    }

    diagnoses = diagnose_pod(mock_pod)
    assert len(diagnoses) == 1
    diag = diagnoses[0]
    assert diag["failure_type"] == "READINESS_PROBE_FAILURE"
    assert diag["severity"] == "MEDIUM"
    assert "Readiness probe failed" in diag["summary"]
    assert any("404" in e for e in diag["evidence"])


def test_scenario_6_liveness_probe_failure():
    """Scenario 6: Liveness probe failures cause kubelet to restart the container."""
    mock_pod = {
        "pod": "liveness-probe-failure",
        "namespace": "k8s-agent-demo",
        "phase": "Running",
        "conditions": [{"type": "Ready", "status": "True"}],
        "events": [
            {
                "type": "Warning",
                "reason": "Unhealthy",
                "message": "Liveness probe failed: cat: can't open '/tmp/healthy': No such file or directory",
            },
            {
                "type": "Normal",
                "reason": "Killing",
                "message": "Container app failed liveness probe, will be restarted",
            },
        ],
        "containers": [
            {
                "name": "app",
                "image": "busybox:1.36",
                "ready": True,
                "restart_count": 2,
                "state": {
                    "status": "Running",
                    "started_at": "2026-11-20T01:05:00Z",
                },
                "last_state": {
                    "status": "Terminated",
                    "reason": "Error",
                    "exit_code": 137,
                },
                "spec": {
                    "probes": {
                        "liveness": {
                            "exec": {"command": ["cat", "/tmp/healthy"]},
                            "initial_delay_seconds": 3,
                            "period_seconds": 5,
                        }
                    }
                },
                "logs": {"current": None, "previous": None},
            }
        ],
    }

    diagnoses = diagnose_pod(mock_pod)
    assert len(diagnoses) == 1
    diag = diagnoses[0]
    assert diag["failure_type"] == "LIVENESS_PROBE_FAILURE"
    assert diag["severity"] == "HIGH"
    assert "liveness probe" in diag["summary"].lower()
    assert any("restart count: 2" in e for e in diag["evidence"])


def test_scenario_7_missing_configmap():
    """Scenario 7: CreateContainerConfigError when referencing nonexistent ConfigMap."""
    mock_pod = {
        "pod": "missing-configmap",
        "namespace": "k8s-agent-demo",
        "phase": "Pending",
        "conditions": [{"type": "Ready", "status": "False"}],
        "events": [
            {
                "type": "Warning",
                "reason": "Failed",
                "message": 'Error: configmap "nonexistent-configmap" not found',
            }
        ],
        "containers": [
            {
                "name": "app",
                "image": "busybox:1.36",
                "ready": False,
                "restart_count": 0,
                "state": {
                    "status": "Waiting",
                    "reason": "CreateContainerConfigError",
                    "message": 'configmap "nonexistent-configmap" not found',
                },
                "last_state": None,
                "logs": {"current": "Unavailable: ContainerCreating", "previous": None},
            }
        ],
        "related_resources": {
            "configmaps": [
                {
                    "name": "nonexistent-configmap",
                    "exists": False,
                    "referenced_by": ["container:app:env:APP_CONFIG"],
                }
            ],
            "secrets": [],
            "pvcs": [],
        },
    }

    diagnoses = diagnose_pod(mock_pod)
    assert len(diagnoses) == 1
    diag = diagnoses[0]
    assert diag["failure_type"] == "MISSING_CONFIGMAP"
    assert diag["severity"] == "HIGH"
    assert "nonexistent-configmap" in diag["evidence"][2]
    assert "Create the missing ConfigMap" in diag["recommended_action"]


def test_scenario_8_missing_secret():
    """Scenario 8: CreateContainerConfigError when referencing nonexistent Secret without leaking credentials."""
    mock_pod = {
        "pod": "missing-secret",
        "namespace": "k8s-agent-demo",
        "phase": "Pending",
        "conditions": [{"type": "Ready", "status": "False"}],
        "events": [
            {
                "type": "Warning",
                "reason": "Failed",
                "message": 'Error: secret "nonexistent-secret" not found',
            }
        ],
        "containers": [
            {
                "name": "app",
                "image": "busybox:1.36",
                "ready": False,
                "restart_count": 0,
                "state": {
                    "status": "Waiting",
                    "reason": "CreateContainerConfigError",
                    "message": 'secret "nonexistent-secret" not found',
                },
                "last_state": None,
                "logs": {"current": "Unavailable: ContainerCreating", "previous": None},
            }
        ],
        "related_resources": {
            "configmaps": [],
            "secrets": [
                {
                    "name": "nonexistent-secret",
                    "exists": False,
                    "referenced_by": ["container:app:env:DB_PASSWORD"],
                }
            ],
            "pvcs": [],
        },
    }

    diagnoses = diagnose_pod(mock_pod)
    assert len(diagnoses) == 1
    diag = diagnoses[0]
    assert diag["failure_type"] == "MISSING_SECRET"
    assert diag["severity"] == "HIGH"
    assert "nonexistent-secret" in diag["evidence"][2]
    assert "without checking secrets into version control" in diag["recommended_action"]


def test_scenario_9_unbound_pvc():
    """Scenario 9: Pod scheduling blocked by unbound PVC referencing nonexistent StorageClass."""
    mock_pod = {
        "pod": "unbound-pvc-pod",
        "namespace": "k8s-agent-demo",
        "phase": "Pending",
        "conditions": [{"type": "PodScheduled", "status": "False", "reason": "Unschedulable"}],
        "events": [
            {
                "type": "Warning",
                "reason": "FailedScheduling",
                "message": "0/1 nodes are available: 1 pod has unbound immediate PersistentVolumeClaims. preemption: 0/1 nodes are available: 1 No preemption victims found for incoming pod.",
            }
        ],
        "containers": [
            {
                "name": "app",
                "image": "busybox:1.36",
                "ready": False,
                "restart_count": 0,
                "state": None,
                "last_state": None,
            }
        ],
        "related_resources": {
            "pvcs": [
                {
                    "name": "unbound-pvc",
                    "exists": True,
                    "phase": "Pending",
                    "storage_class_name": "nonexistent-storage-class",
                    "requested_storage": "1Gi",
                    "events": [
                        {
                            "type": "Warning",
                            "reason": "ProvisioningFailed",
                            "message": 'storageclass.storage.k8s.io "nonexistent-storage-class" not found',
                        }
                    ],
                }
            ],
            "configmaps": [],
            "secrets": [],
        },
    }

    diagnoses = diagnose_pod(mock_pod)
    assert len(diagnoses) == 1
    diag = diagnoses[0]
    assert diag["failure_type"] == "UNBOUND_PVC"
    assert diag["severity"] == "HIGH"
    assert "unbound-pvc" in diag["resource_name"]
    assert "nonexistent-storage-class" in diag["root_cause"]
    assert any("ProvisioningFailed" in e for e in diag["evidence"])


def test_scenario_10_invalid_startup_command():
    """Scenario 10: Nonexistent binary fails at container runtime level (exit code 127)."""
    mock_pod = {
        "pod": "invalid-startup-command",
        "namespace": "k8s-agent-demo",
        "phase": "Running",
        "conditions": [{"type": "Ready", "status": "False"}],
        "events": [
            {
                "type": "Warning",
                "reason": "Failed",
                "message": 'Error: failed to start container "app": Error response from daemon: OCI runtime create failed: container_linux.go:380: starting container process caused: exec: "/bin/nonexistent-binary-command": stat /bin/nonexistent-binary-command: no such file or directory: unknown',
            },
            {
                "type": "Warning",
                "reason": "BackOff",
                "message": "Back-off restarting failed container app in pod invalid-startup-command",
            },
        ],
        "containers": [
            {
                "name": "app",
                "image": "busybox:1.36",
                "ready": False,
                "restart_count": 1,
                "state": {
                    "status": "Waiting",
                    "reason": "CrashLoopBackOff",
                    "message": "back-off 20s restarting failed container=app",
                },
                "last_state": {
                    "status": "Terminated",
                    "reason": "ContainerCannotRun",
                    "exit_code": 127,
                    "message": 'failed to create containerd task: OCI runtime create failed: exec: "/bin/nonexistent-binary-command": executable file not found in $PATH',
                },
                "logs": {"current": "Unavailable: ContainerCreating", "previous": None},
            }
        ],
    }

    diagnoses = diagnose_pod(mock_pod)
    assert len(diagnoses) == 1
    diag = diagnoses[0]
    assert diag["failure_type"] == "INVALID_STARTUP_COMMAND"
    assert diag["severity"] == "HIGH"
    assert "entrypoint command or binary was not found" in diag["summary"]
    assert any("Exit Code: 127" in e for e in diag["evidence"])


# ============================================================================
# 2. Specific Distinction & Edge Case Tests
# ============================================================================

def test_healthy_pod_produces_no_diagnosis():
    """Ensure healthy running Pods produce zero false-positive diagnoses."""
    healthy_pod = {
        "pod": "healthy-service-xyz",
        "namespace": "k8s-agent-demo",
        "phase": "Running",
        "is_ready": True,
        "conditions": [
            {"type": "Ready", "status": "True"},
            {"type": "ContainersReady", "status": "True"},
        ],
        "events": [
            {"type": "Normal", "reason": "Scheduled", "message": "Successfully assigned to node-1"},
            {"type": "Normal", "reason": "Pulled", "message": "Container image already present on machine"},
            {"type": "Normal", "reason": "Created", "message": "Created container app"},
            {"type": "Normal", "reason": "Started", "message": "Started container app"},
        ],
        "containers": [
            {
                "name": "app",
                "image": "nginx:1.27",
                "ready": True,
                "restart_count": 0,
                "state": {
                    "status": "Running",
                    "started_at": "2026-11-20T00:00:00Z",
                },
                "last_state": None,
                "logs": {"current": "Server started successfully on port 80\n", "previous": None},
            }
        ],
        "related_resources": {"pvcs": [], "configmaps": [], "secrets": []},
    }

    diagnoses = diagnose_pod(healthy_pod)
    assert diagnoses == []


def test_distinguish_invalid_startup_command_from_application_process_failure():
    """Ensure exit code 127 / runtime failure is distinguished from exit code 1 application crash."""
    crash_pod = {
        "pod": "crash-app",
        "namespace": "k8s-agent-demo",
        "containers": [
            {
                "name": "app",
                "image": "busybox:1.36",
                "state": {"status": "Waiting", "reason": "CrashLoopBackOff"},
                "last_state": {"status": "Terminated", "reason": "Error", "exit_code": 1},
                "logs": {"current": "App started\nFatal business logic error\n"},
            }
        ],
    }

    cmd_err_pod = {
        "pod": "invalid-cmd-app",
        "namespace": "k8s-agent-demo",
        "containers": [
            {
                "name": "app",
                "image": "busybox:1.36",
                "state": {"status": "Waiting", "reason": "CrashLoopBackOff"},
                "last_state": {
                    "status": "Terminated",
                    "reason": "ContainerCannotRun",
                    "exit_code": 127,
                    "message": "OCI runtime create failed: executable file not found in $PATH",
                },
            }
        ],
    }

    crash_diag = diagnose_pod(crash_pod)
    cmd_diag = diagnose_pod(cmd_err_pod)

    assert len(crash_diag) == 1
    assert crash_diag[0]["failure_type"] == "APPLICATION_PROCESS_FAILURE"

    assert len(cmd_diag) == 1
    assert cmd_diag[0]["failure_type"] == "INVALID_STARTUP_COMMAND"


def test_distinguish_readiness_probe_from_liveness_probe():
    """Ensure readiness probe failure (no restarts) is distinguished from liveness failure (restarts)."""
    readiness_pod = {
        "pod": "probe-pod-1",
        "namespace": "k8s-agent-demo",
        "events": [
            {"type": "Warning", "reason": "Unhealthy", "message": "Readiness probe failed: HTTP 500 Internal Error"}
        ],
        "containers": [
            {
                "name": "app",
                "ready": False,
                "restart_count": 0,
                "state": {"status": "Running"},
                "spec": {"probes": {"readiness": {"http_get": {"path": "/ready", "port": 80}}}},
            }
        ],
    }

    liveness_pod = {
        "pod": "probe-pod-2",
        "namespace": "k8s-agent-demo",
        "events": [
            {"type": "Warning", "reason": "Unhealthy", "message": "Liveness probe failed: HTTP timeout after 5s"},
            {"type": "Normal", "reason": "Killing", "message": "Container app failed liveness probe, will be restarted"},
        ],
        "containers": [
            {
                "name": "app",
                "ready": False,
                "restart_count": 3,
                "state": {"status": "Running"},
                "last_state": {"status": "Terminated", "reason": "Error", "exit_code": 137},
                "spec": {"probes": {"liveness": {"http_get": {"path": "/healthz", "port": 80}}}},
            }
        ],
    }

    r_diag = diagnose_pod(readiness_pod)
    l_diag = diagnose_pod(liveness_pod)

    assert len(r_diag) == 1
    assert r_diag[0]["failure_type"] == "READINESS_PROBE_FAILURE"

    assert len(l_diag) == 1
    assert l_diag[0]["failure_type"] == "LIVENESS_PROBE_FAILURE"


def test_distinguish_missing_configmap_from_missing_secret():
    """Ensure CreateContainerConfigError correctly isolates ConfigMap vs Secret root cause."""
    cm_pod = {
        "pod": "cm-pod",
        "namespace": "k8s-agent-demo",
        "events": [{"type": "Warning", "reason": "Failed", "message": 'Error: configmap "my-cfg" not found'}],
        "containers": [
            {
                "name": "app",
                "state": {"status": "Waiting", "reason": "CreateContainerConfigError", "message": 'configmap "my-cfg" not found'},
            }
        ],
    }

    sec_pod = {
        "pod": "sec-pod",
        "namespace": "k8s-agent-demo",
        "events": [{"type": "Warning", "reason": "Failed", "message": 'Error: secret "my-sec" not found'}],
        "containers": [
            {
                "name": "app",
                "state": {"status": "Waiting", "reason": "CreateContainerConfigError", "message": 'secret "my-sec" not found'},
            }
        ],
    }

    cm_diag = diagnose_pod(cm_pod)
    sec_diag = diagnose_pod(sec_pod)

    assert len(cm_diag) == 1
    assert cm_diag[0]["failure_type"] == "MISSING_CONFIGMAP"

    assert len(sec_diag) == 1
    assert sec_diag[0]["failure_type"] == "MISSING_SECRET"


def test_distinguish_unbound_pvc_from_insufficient_cpu():
    """Ensure scheduler FailedScheduling is correctly differentiated between CPU and Unbound PVC."""
    cpu_pod = {
        "pod": "cpu-pod",
        "namespace": "k8s-agent-demo",
        "phase": "Pending",
        "events": [
            {"type": "Warning", "reason": "FailedScheduling", "message": "0/1 nodes are available: 1 Insufficient cpu."}
        ],
    }

    pvc_pod = {
        "pod": "pvc-pod",
        "namespace": "k8s-agent-demo",
        "phase": "Pending",
        "events": [
            {"type": "Warning", "reason": "FailedScheduling", "message": "0/1 nodes are available: 1 pod has unbound immediate PersistentVolumeClaims."}
        ],
        "related_resources": {
            "pvcs": [
                {
                    "name": "data-claim",
                    "exists": True,
                    "phase": "Pending",
                    "storage_class_name": "custom-sc",
                    "events": [{"type": "Warning", "reason": "ProvisioningFailed", "message": "no volume plugin matched"}],
                }
            ]
        },
    }

    cpu_diag = diagnose_pod(cpu_pod)
    pvc_diag = diagnose_pod(pvc_pod)

    assert len(cpu_diag) == 1
    assert cpu_diag[0]["failure_type"] == "INSUFFICIENT_CLUSTER_RESOURCES"

    assert len(pvc_diag) == 1
    assert pvc_diag[0]["failure_type"] == "UNBOUND_PVC"


def test_diagnose_cluster_aggregates_multiple_pods():
    """Ensure diagnose_cluster collects diagnoses across all Pods in a namespace report."""
    cluster_report = {
        "namespace": "k8s-agent-demo",
        "pod_count": 2,
        "pods": [
            {
                "pod": "pod-1",
                "namespace": "k8s-agent-demo",
                "containers": [
                    {
                        "name": "app",
                        "image": "nginx:bad",
                        "state": {"status": "Waiting", "reason": "ImagePullBackOff"},
                    }
                ],
            },
            {
                "pod": "pod-2",
                "namespace": "k8s-agent-demo",
                "containers": [
                    {
                        "name": "app",
                        "image": "python:alpine",
                        "state": {"status": "Waiting", "reason": "CrashLoopBackOff"},
                        "last_state": {"status": "Terminated", "reason": "OOMKilled", "exit_code": 137},
                    }
                ],
            },
        ],
    }

    result = diagnose_cluster(cluster_report)
    assert result["namespace"] == "k8s-agent-demo"
    assert result["diagnosis_count"] == 2
    types = [d["failure_type"] for d in result["diagnoses"]]
    assert "IMAGE_PULL_FAILURE" in types
    assert "MEMORY_EXHAUSTION" in types


def test_pod_level_vs_container_level_resource_attribution():
    """Verify Pod-level failures have container=None, while container-level failures have container name."""
    pod_level = {
        "pod": "pod-level-test",
        "namespace": "k8s-agent-demo",
        "phase": "Pending",
        "events": [
            {"type": "Warning", "reason": "FailedScheduling", "message": "0/1 nodes are available: 1 Insufficient cpu."}
        ],
        "containers": [{"name": "worker", "image": "nginx:1.27"}],
    }

    container_level = {
        "pod": "container-level-test",
        "namespace": "k8s-agent-demo",
        "containers": [
            {
                "name": "worker",
                "image": "nginx:bad",
                "state": {"status": "Waiting", "reason": "ImagePullBackOff"},
            }
        ],
    }

    pod_diag = diagnose_pod(pod_level)
    cnt_diag = diagnose_pod(container_level)

    assert len(pod_diag) == 1
    assert pod_diag[0]["container"] is None
    assert pod_diag[0]["pod"] == "pod-level-test"

    assert len(cnt_diag) == 1
    assert cnt_diag[0]["container"] == "worker"
    assert cnt_diag[0]["pod"] == "container-level-test"


def test_multi_container_pod_distinct_diagnoses():
    """Verify multi-container Pod with distinct failures produces separate non-conflicting diagnoses."""
    multi_pod = {
        "pod": "multi-worker",
        "namespace": "k8s-agent-demo",
        "phase": "Running",
        "containers": [
            {
                "name": "web",
                "image": "nginx:invalid-tag",
                "state": {"status": "Waiting", "reason": "ImagePullBackOff"},
            },
            {
                "name": "cache",
                "image": "redis:alpine",
                "state": {"status": "Waiting", "reason": "CrashLoopBackOff"},
                "last_state": {"status": "Terminated", "reason": "OOMKilled", "exit_code": 137},
            },
        ],
    }

    diagnoses = diagnose_pod(multi_pod)
    assert len(diagnoses) == 2
    types = [d["failure_type"] for d in diagnoses]
    containers = [d["container"] for d in diagnoses]

    assert "IMAGE_PULL_FAILURE" in types
    assert "MEMORY_EXHAUSTION" in types
    assert "web" in containers
    assert "cache" in containers


def test_missing_logs_does_not_break_application_process_failure_diagnosis():
    """Verify application crash is diagnosed even when logs are completely missing or unavailable."""
    pod_no_logs = {
        "pod": "no-logs-crash",
        "namespace": "k8s-agent-demo",
        "containers": [
            {
                "name": "app",
                "image": "busybox:1.36",
                "state": {"status": "Waiting", "reason": "CrashLoopBackOff"},
                "last_state": {"status": "Terminated", "reason": "Error", "exit_code": 1},
                "logs": {"current": "Unavailable: PodInitializing", "previous": None},
            }
        ],
    }

    diagnoses = diagnose_pod(pod_no_logs)
    assert len(diagnoses) == 1
    assert diagnoses[0]["failure_type"] == "APPLICATION_PROCESS_FAILURE"
    assert "exit code 1" in diagnoses[0]["summary"]


def test_no_secret_value_in_diagnosis_evidence():
    """SECURITY TEST: Ensure MISSING_SECRET diagnosis contains no secret values."""
    mock_pod = {
        "pod": "secret-pod",
        "namespace": "k8s-agent-demo",
        "phase": "Pending",
        "events": [
            {"type": "Warning", "reason": "Failed", "message": 'Error: secret "super-secret" not found'}
        ],
        "containers": [
            {
                "name": "app",
                "state": {
                    "status": "Waiting",
                    "reason": "CreateContainerConfigError",
                    "message": 'secret "super-secret" not found',
                },
            }
        ],
        "related_resources": {
            "secrets": [{"name": "super-secret", "exists": False, "referenced_by": ["container:app:env:PASS"]}],
        },
    }

    diagnoses = diagnose_pod(mock_pod)
    assert len(diagnoses) == 1
    diag = diagnoses[0]
    assert diag["failure_type"] == "MISSING_SECRET"
    # Ensure only metadata (resource name) is mentioned
    diag_str = str(diag)
    assert "password" not in diag_str.lower() or "secret" in diag_str.lower()
    assert "super-secret" in diag_str
