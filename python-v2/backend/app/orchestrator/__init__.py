"""
Agentic AI Orchestrator Package.

Provides multi-agent coordination for Kubernetes incident investigation:
Observation → Deterministic Diagnosis → Remediation Planning → Verification Planning.
"""

import os
from pathlib import Path
from typing import Optional

# Ensure backend/.env is loaded so that GEMINI_MODEL and GEMINI_API_KEY from .env
# take precedence over hardcoded code defaults, while preserving existing shell exports.
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if _env_path.is_file():
        load_dotenv(dotenv_path=_env_path, override=False)
    else:
        load_dotenv(override=False)
except ImportError:
    pass

from app.orchestrator.agents.diagnosis_agent import DeterministicDiagnosisAgent
from app.orchestrator.agents.observation_agent import ObservationAgent
from app.orchestrator.agents.remediation_agent import RemediationPlanner
from app.orchestrator.agents.verification_agent import VerificationPlanner
from app.orchestrator.pipeline import (
    DEFAULT_STAGE_TIMEOUT_SECONDS,
    DEFAULT_TOTAL_TIMEOUT_SECONDS,
    InvestigationPipeline,
)

DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"


def get_gemini_model_name() -> str:
    """Return configured Gemini model name from environment or standard default."""
    return os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)


def is_gemini_api_key_available() -> bool:
    """Check if a valid GEMINI_API_KEY is present in the current environment."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    return bool(key) and key not in ("your_gemini_api_key_here", "your-gemini-api-key", "placeholder")


__all__ = [
    "DEFAULT_GEMINI_MODEL",
    "DEFAULT_STAGE_TIMEOUT_SECONDS",
    "DEFAULT_TOTAL_TIMEOUT_SECONDS",
    "DeterministicDiagnosisAgent",
    "InvestigationPipeline",
    "ObservationAgent",
    "RemediationPlanner",
    "VerificationPlanner",
    "get_gemini_model_name",
    "is_gemini_api_key_available",
]
