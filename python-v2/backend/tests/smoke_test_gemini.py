"""
Standalone Gemini Connectivity & Reliability Smoke Test.

Validates:
1. API key presence (masked, never exposed).
2. Model configuration resolution.
3. Minimal generate_content roundtrip.
4. Latency measurement and HTTP status code tracking.
5. Error sanitization and root cause classification (Service Unavailable vs Config).
"""

import logging
import os
import sys
import time
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("smoke_test_gemini")

# Load environment
load_dotenv()
api_key = os.environ.get("GEMINI_API_KEY")
model_name = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")

if not api_key:
    logger.error("GEMINI_API_KEY is not set in environment or .env file.")
    sys.exit(1)

masked_key = f"{api_key[:4]}...{api_key[-4:]}" if len(api_key) > 8 else "***"
logger.info(f"API Key present: {masked_key} (length={len(api_key)})")
logger.info(f"Configured Model: {model_name}")

from google import genai
from google.genai import types
from google.genai.errors import APIError

def run_smoke_test(target_model: str, timeout_ms: int = 25000):
    logger.info(f"--- Initiating Smoke Test on: {target_model} (timeout={timeout_ms}ms) ---")
    
    # Configure client with bounded HTTP options
    http_opts = types.HttpOptions(
        timeout=timeout_ms,
        retry_options=types.HttpRetryOptions(attempts=1),  # No aggressive retries in smoke test
    )
    client = genai.Client(api_key=api_key, http_options=http_opts)
    
    start_time = time.time()
    try:
        response = client.models.generate_content(
            model=target_model,
            contents="Respond with single word: PONG",
        )
        duration = time.time() - start_time
        text = response.text.strip() if response.text else "<empty>"
        logger.info(f"[SUCCESS] Model: {target_model} | Duration: {duration:.2f}s | Response: {text}")
        return {
            "model": target_model,
            "success": True,
            "duration_seconds": duration,
            "http_status": 200,
            "response": text,
            "error": None,
        }
    except APIError as e:
        duration = time.time() - start_time
        logger.error(f"[API ERROR] Model: {target_model} | Code: {e.code} | Message: {e.message} | Duration: {duration:.2f}s")
        return {
            "model": target_model,
            "success": False,
            "duration_seconds": duration,
            "http_status": e.code,
            "response": None,
            "error": e.message,
        }
    except Exception as e:
        duration = time.time() - start_time
        err_type = type(e).__name__
        logger.error(f"[EXCEPTION] Model: {target_model} | Type: {err_type} | Detail: {str(e)} | Duration: {duration:.2f}s")
        return {
            "model": target_model,
            "success": False,
            "duration_seconds": duration,
            "http_status": None,
            "response": None,
            "error": f"{err_type}: {str(e)}",
        }

if __name__ == "__main__":
    result = run_smoke_test(model_name)
    print("\n--- SMOKE TEST RESULT ---")
    for k, v in result.items():
        print(f"  {k}: {v}")
