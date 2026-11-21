from unittest.mock import MagicMock
import pytest
from kubernetes.client.rest import ApiException
from app.services.kubernetes_service import collect_cluster_evidence


def _create_mock_pod(
    name="test-pod",
    namespace="k8s-agent-demo",
    phase="Running",
    node_name="node-1",
    container_name="app",
    image="nginx:1.27",
    ready=True,
    restart_count=0,
    state=None,
    last_state=None,
    requests=None,
    limits=None,
    readiness_probe=None,
    volumes=None,
    env=None,
    env_from=None,
):
    """Helper to construct a realistic Kubernetes V1Pod object mock."""
    pod = MagicMock()
    pod.metadata.name = name
    pod.metadata.namespace = namespace
    pod.metadata.labels = {"app": name}
    pod.metadata.owner_references = []
    pod.status.phase = phase
    pod.status.reason = None
    pod.status.message = None
    pod.spec.node_name = node_name

    # Conditions
    ready_cond = MagicMock()
    ready_cond.type = "Ready"
    ready_cond.status = "True" if ready else "False"
    ready_cond.reason = None if ready else "ContainersNotReady"
    ready_cond.message = None
    ready_cond.last_transition_time = None
    pod.status.conditions = [ready_cond]

    # Container Spec
    container = MagicMock()
    container.name = container_name
    container.image = image
    container.resources.requests = requests or {}
    container.resources.limits = limits or {}
    container.readiness_probe = readiness_probe
    container.liveness_probe = None
    container.startup_probe = None
    container.env = env or []
    container.env_from = env_from or []
    pod.spec.containers = [container]
    pod.spec.volumes = volumes or []

    # Container Status
    status = MagicMock()
    status.name = container_name
    status.image_id = "docker-pullable://nginx@sha256:abc"
    status.ready = ready
    status.restart_count = restart_count
    status.state = state
    status.last_state = last_state
    pod.status.container_statuses = [status]

    return pod


def test_collect_cluster_evidence_pod_and_container_level():
    """Verify evidence collector extracts structured pod and container level details."""
    mock_api = MagicMock()

    # Container State mock
    mock_state = MagicMock()
    mock_state.waiting = None
    mock_state.running.started_at = None
    mock_state.terminated = None

    mock_pod = _create_mock_pod(
        name="test-web",
        namespace="k8s-agent-demo",
        phase="Running",
        node_name="worker-1",
        container_name="web",
        image="nginx:1.27",
        ready=True,
        state=mock_state,
        requests={"cpu": "100m", "memory": "128Mi"},
        limits={"cpu": "200m", "memory": "256Mi"},
    )

    mock_api.list_namespaced_pod.return_value.items = [mock_pod]
    mock_api.list_namespaced_event.return_value.items = []
    mock_api.list_namespaced_persistent_volume_claim.return_value.items = []
    mock_api.list_namespaced_config_map.return_value.items = []
    mock_api.list_namespaced_secret.return_value.items = []
    mock_api.read_namespaced_pod_log.return_value = "Ready to accept connections\n"

    evidence = collect_cluster_evidence(namespace="k8s-agent-demo", v1=mock_api)

    assert evidence["namespace"] == "k8s-agent-demo"
    assert evidence["pod_count"] == 1
    pod_data = evidence["pods"][0]
    assert pod_data["pod"] == "test-web"
    assert pod_data["phase"] == "Running"
    assert pod_data["node_name"] == "worker-1"
    assert pod_data["is_scheduled"] is True
    assert pod_data["is_ready"] is True

    container_data = pod_data["containers"][0]
    assert container_data["name"] == "web"
    assert container_data["image"] == "nginx:1.27"
    assert container_data["spec"]["resources"]["requests"]["cpu"] == "100m"
    assert container_data["spec"]["resources"]["limits"]["memory"] == "256Mi"
    assert container_data["logs"]["current"] == "Ready to accept connections\n"


def test_collect_cluster_evidence_probe_parsing():
    """Verify HTTP GET, Exec, and TCP Socket probes are correctly parsed into evidence."""
    mock_api = MagicMock()

    mock_probe = MagicMock()
    mock_probe.initial_delay_seconds = 5
    mock_probe.period_seconds = 10
    mock_probe.timeout_seconds = 2
    mock_probe.failure_threshold = 3
    mock_probe.success_threshold = 1
    mock_probe.http_get.path = "/healthz"
    mock_probe.http_get.port = 8080
    mock_probe.http_get.scheme = "HTTP"
    mock_probe.http_get.host = None
    mock_probe._exec = None
    mock_probe.tcp_socket = None

    mock_pod = _create_mock_pod(
        name="probe-pod",
        namespace="k8s-agent-demo",
        readiness_probe=mock_probe,
    )

    mock_api.list_namespaced_pod.return_value.items = [mock_pod]
    mock_api.list_namespaced_event.return_value.items = []
    mock_api.list_namespaced_persistent_volume_claim.return_value.items = []
    mock_api.list_namespaced_config_map.return_value.items = []
    mock_api.list_namespaced_secret.return_value.items = []
    mock_api.read_namespaced_pod_log.return_value = ""

    evidence = collect_cluster_evidence(namespace="k8s-agent-demo", v1=mock_api)
    probe_spec = evidence["pods"][0]["containers"][0]["spec"]["probes"]["readiness"]

    assert probe_spec is not None
    assert probe_spec["initial_delay_seconds"] == 5
    assert probe_spec["http_get"]["path"] == "/healthz"
    assert probe_spec["http_get"]["port"] == 8080


def test_collect_cluster_evidence_pvc_resolution():
    """Verify referenced PersistentVolumeClaims and their events are correlated with the Pod."""
    mock_api = MagicMock()

    # Volume reference on pod
    mock_vol = MagicMock()
    mock_vol.name = "data-vol"
    mock_vol.config_map = None
    mock_vol.secret = None
    mock_vol.persistent_volume_claim.claim_name = "unbound-pvc"

    mock_pod = _create_mock_pod(
        name="unbound-pod",
        namespace="k8s-agent-demo",
        phase="Pending",
        node_name=None,
        volumes=[mock_vol],
    )

    # PVC object in namespace
    mock_pvc = MagicMock()
    mock_pvc.metadata.name = "unbound-pvc"
    mock_pvc.status.phase = "Pending"
    mock_pvc.spec.storage_class_name = "nonexistent-storage-class"
    mock_pvc.spec.resources.requests = {"storage": "1Gi"}

    # PVC Event
    mock_pvc_event = MagicMock()
    mock_pvc_event.involved_object.kind = "PersistentVolumeClaim"
    mock_pvc_event.involved_object.name = "unbound-pvc"
    mock_pvc_event.type = "Warning"
    mock_pvc_event.reason = "ProvisioningFailed"
    mock_pvc_event.message = 'storageclass.storage.k8s.io "nonexistent-storage-class" not found'
    mock_pvc_event.count = 5

    mock_api.list_namespaced_pod.return_value.items = [mock_pod]
    mock_api.list_namespaced_event.return_value.items = [mock_pvc_event]
    mock_api.list_namespaced_persistent_volume_claim.return_value.items = [mock_pvc]
    mock_api.list_namespaced_config_map.return_value.items = []
    mock_api.list_namespaced_secret.return_value.items = []
    mock_api.read_namespaced_pod_log.return_value = ""

    evidence = collect_cluster_evidence(namespace="k8s-agent-demo", v1=mock_api)
    pvcs = evidence["pods"][0]["related_resources"]["pvcs"]

    assert len(pvcs) == 1
    assert pvcs[0]["name"] == "unbound-pvc"
    assert pvcs[0]["exists"] is True
    assert pvcs[0]["phase"] == "Pending"
    assert pvcs[0]["storage_class_name"] == "nonexistent-storage-class"
    assert len(pvcs[0]["events"]) == 1
    assert pvcs[0]["events"][0]["reason"] == "ProvisioningFailed"


def test_collect_cluster_evidence_never_leaks_secret_values():
    """SECURITY TEST: Verify Secret data / values are NEVER retrieved or exposed in collected evidence."""
    mock_api = MagicMock()

    # Mock secret in namespace with simulated sensitive data
    mock_secret = MagicMock()
    mock_secret.metadata.name = "db-secret"
    # Even if someone accessed .data or .string_data, it should NOT be in the collector's output
    mock_secret.data = {"password": "SUPER_SECRET_PLAINTEXT_PW"}
    mock_secret.string_data = {"token": "SECRET_TOKEN"}

    # Pod referencing the secret via env var
    env_item = MagicMock()
    env_item.name = "DB_PASS"
    env_item.value_from.config_map_key_ref = None
    env_item.value_from.secret_key_ref.name = "db-secret"
    env_item.value_from.secret_key_ref.key = "password"

    mock_pod = _create_mock_pod(
        name="secure-pod",
        namespace="k8s-agent-demo",
        env=[env_item],
    )

    mock_api.list_namespaced_pod.return_value.items = [mock_pod]
    mock_api.list_namespaced_event.return_value.items = []
    mock_api.list_namespaced_persistent_volume_claim.return_value.items = []
    mock_api.list_namespaced_config_map.return_value.items = []
    mock_api.list_namespaced_secret.return_value.items = [mock_secret]
    mock_api.read_namespaced_pod_log.return_value = ""

    evidence = collect_cluster_evidence(namespace="k8s-agent-demo", v1=mock_api)

    # 1. Verify existence metadata is reported
    secrets_meta = evidence["pods"][0]["related_resources"]["secrets"]
    assert len(secrets_meta) == 1
    assert secrets_meta[0]["name"] == "db-secret"
    assert secrets_meta[0]["exists"] is True

    # 2. Strict assertion: The sensitive strings must not appear anywhere in serialized evidence
    evidence_str = str(evidence)
    assert "SUPER_SECRET_PLAINTEXT_PW" not in evidence_str
    assert "SECRET_TOKEN" not in evidence_str


def test_collect_cluster_evidence_handles_log_retrieval_api_exception():
    """Verify log retrieval API failure (e.g. 404 or container waiting) does not fail collector."""
    mock_api = MagicMock()

    mock_pod = _create_mock_pod(name="no-log-pod")
    mock_api.list_namespaced_pod.return_value.items = [mock_pod]
    mock_api.list_namespaced_event.return_value.items = []
    mock_api.list_namespaced_persistent_volume_claim.return_value.items = []
    mock_api.list_namespaced_config_map.return_value.items = []
    mock_api.list_namespaced_secret.return_value.items = []

    # Simulate ApiException on log read
    exc = ApiException(status=400, reason="BadRequest")
    mock_api.read_namespaced_pod_log.side_effect = exc

    evidence = collect_cluster_evidence(namespace="k8s-agent-demo", v1=mock_api)
    log_status = evidence["pods"][0]["containers"][0]["logs"]["current"]

    assert "Unavailable: BadRequest" in log_status


def test_collect_cluster_evidence_handles_rbac_forbidden_on_secrets():
    """Verify collector gracefully handles RBAC 403 Forbidden on Secrets listing without failing."""
    mock_api = MagicMock()

    mock_pod = _create_mock_pod(name="test-pod")
    mock_api.list_namespaced_pod.return_value.items = [mock_pod]
    mock_api.list_namespaced_event.return_value.items = []
    mock_api.list_namespaced_persistent_volume_claim.return_value.items = []
    mock_api.list_namespaced_config_map.return_value.items = []
    mock_api.read_namespaced_pod_log.return_value = ""

    # Simulate 403 Forbidden when listing Secrets
    mock_api.list_namespaced_secret.side_effect = ApiException(status=403, reason="Forbidden")

    evidence = collect_cluster_evidence(namespace="k8s-agent-demo", v1=mock_api)
    assert evidence["pod_count"] == 1
    # Secrets list defaults safely to empty
    assert evidence["pods"][0]["related_resources"]["secrets"] == []


def test_collect_cluster_evidence_handles_missing_pvc_volume():
    """Verify collector correctly marks missing PVC as exists=False and phase=NotFound."""
    mock_api = MagicMock()

    mock_vol = MagicMock()
    mock_vol.name = "nonexistent-vol"
    mock_vol.config_map = None
    mock_vol.secret = None
    mock_vol.persistent_volume_claim.claim_name = "deleted-pvc"

    mock_pod = _create_mock_pod(name="missing-pvc-pod", volumes=[mock_vol])

    mock_api.list_namespaced_pod.return_value.items = [mock_pod]
    mock_api.list_namespaced_event.return_value.items = []
    mock_api.list_namespaced_persistent_volume_claim.return_value.items = []  # No PVCs exist
    mock_api.list_namespaced_config_map.return_value.items = []
    mock_api.list_namespaced_secret.return_value.items = []
    mock_api.read_namespaced_pod_log.return_value = ""

    evidence = collect_cluster_evidence(namespace="k8s-agent-demo", v1=mock_api)
    pvcs = evidence["pods"][0]["related_resources"]["pvcs"]

    assert len(pvcs) == 1
    assert pvcs[0]["name"] == "deleted-pvc"
    assert pvcs[0]["exists"] is False
    assert pvcs[0]["phase"] == "NotFound"
