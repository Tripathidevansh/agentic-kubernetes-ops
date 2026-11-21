"""
FastAPI Backend Application Entrypoint.

Agentic AI Framework for Intelligent Kubernetes Incident Diagnosis
and Safe GitOps Remediation using Model Context Protocol (MCP) and Gemini.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
import os

from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.cluster import router as cluster_router
from app.api.health import router as health_router
from app.api.incidents import router as incidents_router
from app.api.investigations import router as investigations_router
from app.api.remediations import router as remediations_router
from app.orchestrator.sanitizer import sanitize_error_message

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("k8s_agentic_ops")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan context manager for startup and shutdown events."""
    logger.info("Starting Agentic Kubernetes Operations Backend v2.0.0...")
    model = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
    api_key_set = bool(os.environ.get("GEMINI_API_KEY"))
    logger.info("Configured Gemini model: '%s' (API key configured: %s)", model, api_key_set)
    logger.info("Safety guardrails active: Read-only cluster access, human-in-the-loop review.")
    yield
    logger.info("Shutting down Agentic Kubernetes Operations Backend...")


app = FastAPI(
    title="Agentic Kubernetes Operations & GitOps Remediation API",
    description=(
        "FastAPI REST backend for automated Kubernetes incident investigation, "
        "deterministic diagnosis, and safe human-in-the-loop GitOps remediation "
        "using Model Context Protocol (MCP) and Gemini."
    ),
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# -----------------------------------------------------------------------------
# CORS Middleware Configuration (Ready for React / Vite Frontend)
# -----------------------------------------------------------------------------
ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    "*",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------------------------------------------------------------
# Global Exception Handlers
# -----------------------------------------------------------------------------
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle Pydantic request validation errors cleanly."""
    from fastapi.encoders import jsonable_encoder

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": jsonable_encoder(exc.errors()),
            "code": "VALIDATION_ERROR",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Handle standard HTTP exceptions with consistent response envelope."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": sanitize_error_message(str(exc.detail)),
            "code": "HTTP_EXCEPTION",
            "status_code": exc.status_code,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """
    Catch-all exception handler to protect against unhandled crashes.
    Guarantees secrets and raw tokens are sanitized before returning error details.
    """
    logger.error("Unhandled server exception: %s", exc, exc_info=True)
    sanitized_msg = sanitize_error_message(str(exc))
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": f"Internal server error: {sanitized_msg}",
            "code": "INTERNAL_SERVER_ERROR",
            "type": type(exc).__name__,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


# -----------------------------------------------------------------------------
# Route Registration
# -----------------------------------------------------------------------------
# Health probe
app.include_router(health_router)

# Core API routes under /api prefix
app.include_router(cluster_router, prefix="/api")
app.include_router(incidents_router, prefix="/api")
app.include_router(investigations_router, prefix="/api")
app.include_router(remediations_router, prefix="/api")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")
    uvicorn.run("app.main:app", host=host, port=port, reload=True)
