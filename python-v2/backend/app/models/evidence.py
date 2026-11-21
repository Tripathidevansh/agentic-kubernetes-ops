from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ProbeConfig(BaseModel):
    initial_delay_seconds: Optional[int] = None
    period_seconds: Optional[int] = None
    timeout_seconds: Optional[int] = None
    failure_threshold: Optional[int] = None
    success_threshold: Optional[int] = None
    http_get: Optional[Dict[str, Any]] = None
    exec_action: Optional[Dict[str, Any]] = Field(None, alias="exec")
    tcp_socket: Optional[Dict[str, Any]] = None

    class Config:
        populate_by_name = True


class ContainerProbes(BaseModel):
    readiness: Optional[Dict[str, Any]] = None
    liveness: Optional[Dict[str, Any]] = None
    startup: Optional[Dict[str, Any]] = None


class ContainerResources(BaseModel):
    requests: Dict[str, str] = Field(default_factory=dict)
    limits: Dict[str, str] = Field(default_factory=dict)


class ContainerSpecEvidence(BaseModel):
    resources: ContainerResources = Field(default_factory=ContainerResources)
    probes: ContainerProbes = Field(default_factory=ContainerProbes)


class ContainerStateEvidence(BaseModel):
    status: str
    reason: Optional[str] = None
    message: Optional[str] = None
    exit_code: Optional[int] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class ContainerLogsEvidence(BaseModel):
    current: Optional[str] = None
    previous: Optional[str] = None


class ContainerEvidence(BaseModel):
    name: str
    image: Optional[str] = None
    image_id: Optional[str] = None
    ready: bool = False
    restart_count: int = 0
    state: Optional[Dict[str, Any]] = None
    last_state: Optional[Dict[str, Any]] = None
    spec: Optional[Dict[str, Any]] = None
    logs: Dict[str, Optional[str]] = Field(default_factory=lambda: {"current": None, "previous": None})


class PodConditionEvidence(BaseModel):
    type: str
    status: str
    reason: Optional[str] = None
    message: Optional[str] = None
    last_transition_time: Optional[str] = None


class EventEvidence(BaseModel):
    type: Optional[str] = None
    reason: Optional[str] = None
    message: Optional[str] = None
    count: int = 1
    first_timestamp: Optional[str] = None
    last_timestamp: Optional[str] = None


class PvcEvidence(BaseModel):
    name: str
    exists: bool = False
    phase: Optional[str] = None
    storage_class_name: Optional[str] = None
    requested_storage: Optional[str] = None
    events: List[Dict[str, Any]] = Field(default_factory=list)


class ResourceRefEvidence(BaseModel):
    name: str
    exists: bool = False
    referenced_by: List[str] = Field(default_factory=list)


class RelatedResourcesEvidence(BaseModel):
    pvcs: List[Dict[str, Any]] = Field(default_factory=list)
    configmaps: List[Dict[str, Any]] = Field(default_factory=list)
    secrets: List[Dict[str, Any]] = Field(default_factory=list)


class PodEvidence(BaseModel):
    pod: str
    namespace: str
    phase: Optional[str] = None
    pod_reason: Optional[str] = None
    pod_message: Optional[str] = None
    labels: Dict[str, str] = Field(default_factory=dict)
    node_name: Optional[str] = None
    is_scheduled: bool = False
    is_ready: bool = False
    conditions: List[Dict[str, Any]] = Field(default_factory=list)
    owners: List[Dict[str, str]] = Field(default_factory=list)
    containers: List[Dict[str, Any]] = Field(default_factory=list)
    events: List[Dict[str, Any]] = Field(default_factory=list)
    related_resources: Dict[str, Any] = Field(default_factory=dict)


class ClusterEvidence(BaseModel):
    namespace: str
    pod_count: int
    pods: List[Dict[str, Any]] = Field(default_factory=list)
