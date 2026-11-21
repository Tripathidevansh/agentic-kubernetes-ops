import logging
import re
import sys
from typing import Any, Dict, Optional
from kubernetes.client.rest import ApiException

# Configure logger to output strictly to sys.stderr to avoid corrupting MCP STDIO protocol
logger = logging.getLogger("k8s_mcp_server")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Kubernetes RFC 1123 DNS subdomain and label validation
DNS_LABEL_REGEX = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")

# Default Kubernetes client override for unit testing
_injected_k8s_client: Optional[Any] = None


def set_default_k8s_client(client: Optional[Any]) -> None:
    """Inject a mock or custom Kubernetes client for testing purposes."""
    global _injected_k8s_client
    _injected_k8s_client = client


def get_default_k8s_client() -> Optional[Any]:
    """Retrieve injected Kubernetes client if set."""
    return _injected_k8s_client


def validate_namespace(namespace: str) -> Optional[str]:
    """
    Validate Kubernetes namespace according to RFC 1123 label standards.
    Returns error string if invalid, None if valid.
    """
    if not namespace or not isinstance(namespace, str):
        return "Parameter 'namespace' must be a non-empty string."
    ns = namespace.strip()
    if len(ns) > 63:
        return f"Namespace '{ns}' exceeds maximum length of 63 characters."
    if not DNS_LABEL_REGEX.match(ns):
        return (
            f"Namespace '{ns}' is invalid. It must consist of lowercase alphanumeric characters "
            "or '-', and must start and end with an alphanumeric character."
        )
    return None


def validate_resource_name(name: str, param_name: str = "resource_name") -> Optional[str]:
    """
    Validate Kubernetes resource name (Pod, PVC, Container, etc.).
    Returns error string if invalid, None if valid.
    """
    if not name or not isinstance(name, str):
        return f"Parameter '{param_name}' must be a non-empty string."
    res_name = name.strip()
    if len(res_name) > 253:
        return f"Parameter '{param_name}' exceeds maximum length of 253 characters."
    if not DNS_LABEL_REGEX.match(res_name):
        return (
            f"Parameter '{param_name}' value '{res_name}' is invalid. It must consist of lowercase "
            "alphanumeric characters or '-', and must start and end with an alphanumeric character."
        )
    return None


def success_response(tool: str, namespace: str, data: Any) -> Dict[str, Any]:
    """Construct a standardized successful MCP tool response dictionary."""
    return {
        "success": True,
        "tool": tool,
        "namespace": namespace,
        "data": data,
        "error": None,
    }


def error_response(tool: str, namespace: str, code: str, message: str) -> Dict[str, Any]:
    """Construct a standardized failed MCP tool response dictionary."""
    return {
        "success": False,
        "tool": tool,
        "namespace": namespace,
        "data": None,
        "error": {
            "code": code,
            "message": message,
        },
    }


def format_api_exception(tool: str, namespace: str, exc: ApiException) -> Dict[str, Any]:
    """Map Kubernetes ApiException to standardized error response without exposing internal stack traces."""
    code = "KUBERNETES_API_ERROR"
    if exc.status in (401, 403):
        code = "KUBERNETES_PERMISSION_DENIED"
    elif exc.status == 404:
        code = "KUBERNETES_RESOURCE_NOT_FOUND"
    elif exc.status in (502, 503, 504) or exc.status is None:
        code = "KUBERNETES_CONNECTION_ERROR"

    return error_response(
        tool=tool,
        namespace=namespace,
        code=code,
        message=f"Kubernetes API error (status {exc.status}): {exc.reason or str(exc)}",
    )
