"""
Error message sanitizer for Agentic Kubernetes Operations.

Redacts potential secrets, credentials, API keys, and authorization tokens
from error messages and logs before exposing them in reports and traces.
"""

import re
from typing import Optional


def sanitize_error_message(message: Optional[str]) -> Optional[str]:
    """
    Sanitize an error message string by masking API keys and sensitive tokens.

    Args:
        message: Raw error message string.

    Returns:
        Sanitized error message string with credentials masked, or None if input was None.
    """
    if not message or not isinstance(message, str):
        return message

    sanitized = message
    # Mask Google AI / Gemini API keys (AIzaSy...)
    sanitized = re.sub(r"AIza[0-9A-Za-z-_]{35}", "[REDACTED_API_KEY]", sanitized)
    # Mask AQ. tokens
    sanitized = re.sub(r"AQ\.[0-9A-Za-z-_]{20,}", "[REDACTED_TOKEN]", sanitized)
    # Mask Bearer tokens
    sanitized = re.sub(r"(Bearer\s+)[A-Za-z0-9\._\-]+", r"\1[REDACTED_TOKEN]", sanitized, flags=re.IGNORECASE)
    # Mask query param key=... or api_key=...
    sanitized = re.sub(
        r"((?:key|api_key|token|secret|password)=)[^\s&\"']+",
        r"\1[REDACTED]",
        sanitized,
        flags=re.IGNORECASE,
    )
    return sanitized
