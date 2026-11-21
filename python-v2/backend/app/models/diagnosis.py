from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class Diagnosis(BaseModel):
    failure_type: str = Field(..., description="The classified failure type")
    severity: str = Field(..., description="Failure severity: CRITICAL, HIGH, MEDIUM, LOW")
    resource_name: str = Field(..., description="The name of the affected resource")
    namespace: str = Field(..., description="The Kubernetes namespace")
    summary: str = Field(..., description="Human-readable summary of the issue")
    root_cause: str = Field(..., description="Technical root cause analysis")
    evidence: List[str] = Field(default_factory=list, description="Concrete observed Kubernetes evidence")
    recommended_action: str = Field(..., description="Recommended remediation action")

    # Backward compatibility fields
    pod: Optional[str] = None
    container: Optional[str] = None
    type: Optional[str] = None
    confidence: str = "HIGH"


class ClusterDiagnosisResponse(BaseModel):
    namespace: Optional[str] = None
    diagnosis_count: int
    diagnoses: List[Dict[str, Any]]
