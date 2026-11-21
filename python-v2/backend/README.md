# Agentic AI Kubernetes Incident Diagnosis & Safe GitOps Remediation — FastAPI Backend

FastAPI REST backend exposing the autonomous Agentic AI Kubernetes incident diagnosis and safe GitOps remediation pipeline to web clients (including the React frontend).

## Architecture & Principles

- **Read-Only Kubernetes Access**: Cluster inspection occurs strictly through read-only Model Context Protocol (MCP) tools and Python Kubernetes API collectors.
- **Deterministic Diagnosis Authority**: Authoritative ground-truth diagnoses are generated deterministically by the rule-based engine covering 10 distinct failure modes.
- **AI-Powered Investigation & Remediation**: Gemini (`gemini-3.8-flash`) via Google ADK observes cluster state, interprets telemetry, and plans declarative GitOps patches.
- **Deterministic Grounded Fallback**: If Gemini encounters transient rate limits (HTTP 503), timeouts, or service issues, the pipeline automatically falls back to grounded rule-based proposal templates without failing.
- **Strict Human-in-the-Loop Review**: No automated cluster mutation, `kubectl apply`, or direct Git pushes. Remediation proposals require explicit human approval; approval endpoints record operator decisions only.
- **Secret Protection**: Kubernetes Secret contents (`data`, `stringData`) are strictly excluded from all collectors, prompts, LLM responses, and API endpoints.

---

## Getting Started

### 1. Prerequisites
- Python 3.12+
- Docker Desktop with Kubernetes enabled (for live cluster operations)
- Google Gemini API Key (optional for unit tests; required for live LLM mode)

### 2. Activate the Virtual Environment

```bash
cd python-v2/backend
source .venv/bin/activate
```

Verify dependencies:
```bash
pip list | grep -E "fastapi|uvicorn|pydantic|google-adk"
```

### 3. Configure Environment Variables

Create or update `.env` in `python-v2/backend/`:

```bash
# Gemini LLM Configuration
GEMINI_API_KEY="your-google-ai-api-key"
GEMINI_MODEL="gemini-3.8-flash"

# Timeouts (in seconds)
STAGE_TIMEOUT_SECONDS=120.0
TOTAL_TIMEOUT_SECONDS=360.0

# Server Configuration
HOST="0.0.0.0"
PORT=8000
```

### 4. Start the FastAPI Server

Launch using Uvicorn:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The interactive Swagger OpenAPI documentation is available at:
**[http://localhost:8000/docs](http://localhost:8000/docs)**
Alternative ReDoc documentation: **[http://localhost:8000/redoc](http://localhost:8000/redoc)**

---

## API Endpoints Reference

### 1. Health Probe
- **`GET /health`**
  - **Description**: Probes service status, API version, and cluster reachability.
  - **Response Example**:
    ```json
    {
      "status": "healthy",
      "service": "k8s-agentic-ops",
      "version": "2.0.0",
      "cluster_connected": true,
      "model": "gemini-3.8-flash",
      "mcp_server": "read-only-k8s-tools",
      "timestamp": "2026-11-21T12:00:00.000000+00:00"
    }
    ```

---

### 2. Cluster Overview
- **`GET /api/cluster/overview?namespace=k8s-agent-demo`**
  - **Description**: Returns Pod counts by runtime phase (`Running`, `Pending`, `Failed`), container restart tallies, and active diagnosed incidents breakdown.
  - **Safety**: Never exposes Secret contents or environment variables.
  - **Response Example**:
    ```json
    {
      "namespace": "k8s-agent-demo",
      "total_pods": 10,
      "running_pods": 3,
      "pending_pods": 2,
      "failed_pods": 5,
      "succeeded_pods": 0,
      "unknown_pods": 0,
      "incident_count": 10,
      "incidents_summary": {
        "MEMORY_EXHAUSTION": 1,
        "IMAGE_PULL_FAILURE": 1,
        "APPLICATION_PROCESS_FAILURE": 1
      },
      "pods": [
        {
          "name": "oom-app-abc",
          "namespace": "k8s-agent-demo",
          "phase": "Running",
          "is_ready": false,
          "is_scheduled": true,
          "node_name": "docker-desktop",
          "restarts": 4,
          "container_statuses": [
            {
              "name": "app",
              "state": "Terminated",
              "reason": "OOMKilled",
              "message": "Memory limit exceeded",
              "restart_count": 4,
              "ready": false
            }
          ],
          "owners": [{"kind": "Deployment", "name": "oom-deploy"}]
        }
      ],
      "collected_at": "2026-11-21T12:00:00.000000+00:00"
    }
    ```

---

### 3. Incidents
- **`GET /api/incidents?namespace=k8s-agent-demo`**
  - **Description**: Returns deterministic diagnoses derived directly from collected telemetry without running LLM investigations.
  - **Response Example**:
    ```json
    {
      "namespace": "k8s-agent-demo",
      "total_incidents": 1,
      "incidents": [
        {
          "failure_type": "MEMORY_EXHAUSTION",
          "severity": "HIGH",
          "resource_name": "oom-app-abc/app",
          "namespace": "k8s-agent-demo",
          "summary": "Container 'app' terminated due to out-of-memory (OOMKilled) condition.",
          "root_cause": "The container exceeded its configured memory limit of 64Mi and was terminated by the Linux OOM killer.",
          "evidence": ["Container 'app' terminated with reason: OOMKilled, exit code: 137"],
          "recommended_action": "Increase memory limits or optimize application heap usage.",
          "pod": "oom-app-abc",
          "container": "app",
          "confidence": "HIGH"
        }
      ],
      "collected_at": "2026-11-21T12:00:00.000000+00:00"
    }
    ```

---

### 4. Investigations

#### Dispatch Investigation
- **`POST /api/investigations`**
  - **Description**: Starts an asynchronous end-to-end incident investigation. Returns immediately with HTTP 202 Accepted.
  - **Request Body**:
    ```json
    {
      "namespace": "k8s-agent-demo",
      "pod_name": null,
      "use_llm": true
    }
    ```
  - **Response Example** (HTTP 202):
    ```json
    {
      "investigation_id": "7d5f97ac-420c-4c9f-a8df-0f149d149458",
      "status": "QUEUED",
      "namespace": "k8s-agent-demo",
      "created_at": "2026-11-21T12:00:00.000000+00:00",
      "message": "Investigation for namespace 'k8s-agent-demo' queued. Poll /api/investigations/7d5f97ac-420c-4c9f-a8df-0f149d149458 for status."
    }
    ```

#### Poll Investigation Status & Results
- **`GET /api/investigations/{investigation_id}`**
  - **Description**: Returns live progress, final results, stage provenances, Gemini request metrics, and tool execution traces.
  - **Response Example**:
    ```json
    {
      "investigation_id": "7d5f97ac-420c-4c9f-a8df-0f149d149458",
      "status": "COMPLETED",
      "namespace": "k8s-agent-demo",
      "target_pod": null,
      "created_at": "2026-11-21T12:00:00.000000+00:00",
      "started_at": "2026-11-21T12:00:00.050000+00:00",
      "completed_at": "2026-11-21T12:00:25.120000+00:00",
      "duration_seconds": 25.07,
      "outcome": "COMPLETED_WITH_FALLBACK",
      "observation_provenance": "DETERMINISTIC_FALLBACK",
      "diagnosis_provenance": "DETERMINISTIC_ENGINE",
      "remediation_provenance": "DETERMINISTIC_FALLBACK",
      "verification_provenance": "DETERMINISTIC_ENGINE",
      "gemini_metrics": [
        {
          "stage": "OBSERVATION",
          "model": "gemini-3.8-flash",
          "duration_ms": 10002.5,
          "success": false,
          "error_type": "TimeoutError",
          "fallback_triggered": true,
          "fallback_reason": "Observation Agent Gemini invocation timed out after 10.0s"
        }
      ],
      "tool_traces": [
        {
          "agent_name": "telemetry_collector",
          "tool_name": "scan_cluster",
          "status": "SUCCESS",
          "duration_ms": 42.1
        }
      ],
      "diagnoses_count": 10,
      "proposals_count": 10,
      "result": { ... },
      "error": null
    }
    ```

#### Post-Remediation Verification Plans
- **`GET /api/investigations/{investigation_id}/verification`**
  - **Description**: Returns automated post-remediation verification assertions formulated during the investigation.
  - **Response Example**:
    ```json
    {
      "investigation_id": "7d5f97ac-420c-4c9f-a8df-0f149d149458",
      "total_proposals": 10,
      "actionable_count": 8,
      "withheld_count": 2,
      "plans": [
        {
          "proposal_id": "prop-123",
          "target_workload": "oom-app-abc",
          "target_kind": "Deployment",
          "target_name": "oom-deploy",
          "can_remediate": true,
          "verification_plan": {
            "target_phase": "Running",
            "expected_conditions": [
              {"type": "Ready", "status": "True"},
              {"type": "ContainersReady", "status": "True"}
            ],
            "negative_assertions": [
              "OOMKilled",
              "CrashLoopBackOff"
            ],
            "check_interval_seconds": 5,
            "timeout_seconds": 60
          },
          "insufficient_evidence_reason": null
        }
      ]
    }
    ```

---

### 5. Remediation Proposals & Approvals

#### List Remediations
- **`GET /api/remediations`**
- **Query Parameters**:
  - `investigation_id` (optional): Filter by specific investigation.
  - `status` (optional): `ACTIONABLE`, `INSUFFICIENT_EVIDENCE`, `REJECTED_SAFETY_VIOLATION`.
  - `approval_status` (optional): `PENDING`, `APPROVED`, `REJECTED`.

#### Approve Proposal
- **`POST /api/remediations/{proposal_id}/approve`**
  - **Request Body**:
    ```json
    {
      "reviewer": "sre-operator",
      "comments": "Approved memory limit increase to 256Mi based on profiler evidence."
    }
    ```
  - **Safety**:
    - Only actionable proposals (`status == "ACTIONABLE"` and `can_remediate == true`) can be approved. Attempting to approve non-actionable proposals returns `400 Bad Request`.
    - Strictly records approval status; does NOT mutate Kubernetes or create Git commits.

#### Reject Proposal
- **`POST /api/remediations/{proposal_id}/reject`**
  - **Request Body**:
    ```json
    {
      "reviewer": "sre-operator",
      "comments": "Rejected: scheduled maintenance window required."
    }
    ```

---

## Investigation Lifecycle

```
[ POST /api/investigations ]
           |
           v
       (QUEUED)
           | (background task spawned)
           v
       (RUNNING)
           |
    +------+------+
    |             |
    v             v
(COMPLETED)    (FAILED)
```

- **In-Memory Registry**: Designed for local academic demonstration. Investigation records and approvals are held in a thread-safe, coroutine-locked in-memory store.
- **Persistence Note**: All records and approval states reset when the backend process restarts.

---

## Safety Boundaries & GitOps Guarantees

1. **Zero Kubernetes Write Operations**: No endpoint executes `kubectl apply`, `delete`, `patch`, or `create`.
2. **Read-Only MCP Server**: The backend communicates with Kubernetes exclusively via read-only tools validated by safety interceptors.
3. **No Direct LLM Cluster Access**: Gemini is never granted write tools or direct terminal access.
4. **Human Review Mandatory**: All proposals require human operator review.
5. **Secret Data Protection**: The evidence collector and serializer only verify existence of referenced secrets; secret keys and data are never collected, logged, or serialized.
