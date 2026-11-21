# KafaKube8 Frontend — Intelligence in Every Cluster

Phase 6 React + Vite + TypeScript + Tailwind CSS cockpit for autonomous Kubernetes operations and troubleshooting.

Built directly from the Google Stitch SRE cockpit design system with high operational density, dark low-luminance canvas, and real-time integration with the FastAPI backend.

---

## Architecture & Integration

- **Design System:** Google Stitch SRE Cockpit (Dark Theme `#0F131D`, Sky Blue `#38BDF8`, JetBrains Mono, Inter, Material Symbols Outlined).
- **Backend API:** Connects to `http://localhost:8000` (FastAPI + Pydantic V2).
- **Four-Stage Workflow:**
  1. **Observation:** Scans cluster workloads via custom read-only MCP server (7 tools).
  2. **Diagnosis:** 10 deterministic failure scenarios with root-cause and evidence attribution.
  3. **Remediation Planning:** Batch Gemini 3.8 Flash LLM planning with automatic deterministic fallback.
  4. **Verification:** Automated readiness probes and fail-safe negative assertions.
- **Safety Policy:** Strict read-only cluster access, mandatory human operator sign-off, zero direct production mutations.

---

## Getting Started

### 1. Start the FastAPI Backend
In a separate terminal:
```bash
cd python-v2/backend
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 2. Run Frontend in Development Mode
```bash
cd python-v2/frontend
npm run dev
```
The application will launch at: **`http://localhost:5173`**

### 3. Production Build
```bash
cd python-v2/frontend
npm run build
npm run preview
```

---

## Views Implemented

1. **Overview (`/`)**: SRE Cockpit // Node Plane, cluster telemetry freshness, KPI metric cards (Total Pods, Running, Pending, Failed, Restarts, Incidents), Workload distribution breakdown, and Live Telemetry stream with one-click "Investigate" dispatch.
2. **Incidents (`/incidents`)**: Cluster Triage View with search, severity filter chips (All, Critical, High, Medium, Low), incident stream table, telemetry export, and incident evidence side drawer.
3. **Investigation Center (`/investigations`)**: 4-stage pipeline stepper, dual-runtime provenance banner (`COMPLETED_WITH_GEMINI` vs `COMPLETED_WITH_FALLBACK`), Manifest Diff viewer with line-by-line red/green diffs, Gemini request metrics, live MCP tool call traces, and Human Operator sign-off controls.
4. **Remediations (`/remediations`)**: Complete inventory of remediation proposals, status filters (`PENDING_APPROVAL`, `APPROVED`, `REJECTED`), side-by-side YAML diff inspection, and approve/reject workflows.
5. **Verification (`/verification`)**: Automated post-remediation verification assertions, target pod phases, readiness probe tolerances, and negative assertions.
6. **Agent Observability (`/observability`)**: Gemini API invocation latency, token usage, retry telemetry, and MCP tool execution logs.
7. **Cluster Resources (`/resources`)**: Real-time Pod inventory with phase, restarts, ready conditions, node topology, and secret masking verification.
8. **Settings (`/settings`)**: Health probe diagnostics, Kubernetes connection state, Gemini model configuration, stage timeout architecture, and safety policies.
