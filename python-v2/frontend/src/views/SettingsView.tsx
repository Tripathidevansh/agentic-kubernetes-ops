import React from 'react';
import { HealthResponse } from '../types/api';

interface SettingsViewProps {
  health: HealthResponse | null;
  onRefreshHealth: () => void;
}

export const SettingsView: React.FC<SettingsViewProps> = ({
  health,
  onRefreshHealth,
}) => {
  return (
    <div className="flex flex-col w-full p-margin-desktop space-y-space-lg animate-in fade-in duration-200">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-space-md border-b border-surface-container-high/40 pb-space-md">
        <div className="flex flex-col space-y-space-xs">
          <div className="flex items-center gap-space-xs text-on-surface-variant font-mono text-[11px]">
            <span className="text-primary font-bold">Settings</span>
            <span>/</span>
            <span>System Configuration & Guardrails</span>
          </div>
          <h1 className="font-headline-xl text-headline-xl text-on-surface font-semibold tracking-tight">
            System & Safety Configuration
          </h1>
          <p className="font-body-md text-body-md text-on-surface-variant max-w-2xl">
            Inspect model runtime configurations, MCP tool endpoints, timeout budgets, and autonomous safety boundaries.
          </p>
        </div>

        <button
          onClick={onRefreshHealth}
          className="flex items-center gap-space-xs px-space-md py-1.5 rounded bg-surface-container hover:bg-surface-container-high text-on-surface font-headline-sm text-headline-sm transition-colors border border-surface-container-high self-start md:self-auto"
        >
          <span className="material-symbols-outlined text-[16px] text-primary">sync</span>
          <span>Refresh Health Probe</span>
        </button>
      </div>

      {/* Backend & Runtime Health Card */}
      <div className="bg-surface-container-low rounded-xl border border-surface-container-high p-space-md shadow-sm space-y-space-sm">
        <div className="flex items-center justify-between border-b border-surface-container-high pb-space-xs font-mono text-xs">
          <div className="flex items-center gap-2">
            <span className="material-symbols-outlined text-primary text-[18px]">dns</span>
            <span className="font-bold text-on-surface">FastAPI Backend Health</span>
          </div>
          <span
            className={`px-2 py-0.5 rounded text-[10px] font-bold ${
              health?.status === 'healthy' || health?.status === 'ok'
                ? 'bg-tertiary/20 text-tertiary'
                : 'bg-error/20 text-error'
            }`}
          >
            {health?.status === 'healthy' || health?.status === 'ok' ? 'HEALTHY (200 OK)' : 'DEGRADED'}
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-space-sm font-mono text-xs pt-1">
          <div className="p-space-sm bg-surface-container rounded border border-surface-container-high space-y-1">
            <span className="text-outline text-[11px] uppercase block">Backend Version</span>
            <span className="text-on-surface font-bold text-sm">{health?.version || '2.0.0'}</span>
            <span className="text-[10px] text-outline block">FastAPI + Pydantic V2</span>
          </div>

          <div className="p-space-sm bg-surface-container rounded border border-surface-container-high space-y-1">
            <span className="text-outline text-[11px] uppercase block">Kubernetes Connection</span>
            <span
              className={`font-bold text-sm ${
                health?.cluster_connected ?? health?.kubernetes_connected ? 'text-tertiary' : 'text-error'
              }`}
            >
              {health?.cluster_connected ?? health?.kubernetes_connected
                ? 'CONNECTED (In-Cluster/Kubeconfig)'
                : 'DISCONNECTED'}
            </span>
            <span className="text-[10px] text-outline block">Read-only Telemetry</span>
          </div>

          <div className="p-space-sm bg-surface-container rounded border border-surface-container-high space-y-1">
            <span className="text-outline text-[11px] uppercase block">Gemini Model</span>
            <span className="text-primary font-bold text-sm">
              {health?.model || health?.gemini_model || 'gemini-3.8-flash'}
            </span>
            <span className="text-[10px] text-outline block">Google ADK Runner</span>
          </div>
        </div>
      </div>

      {/* Model & Bounded Retry Architecture */}
      <div className="bg-surface-container-low rounded-xl border border-surface-container-high p-space-md shadow-sm space-y-space-sm">
        <div className="flex items-center gap-2 border-b border-surface-container-high pb-space-xs font-mono text-xs">
          <span className="material-symbols-outlined text-[#818CF8] text-[18px]">neurology</span>
          <span className="font-bold text-on-surface">Gemini Orchestration & Retry Architecture</span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-space-md font-mono text-xs pt-1">
          <div className="space-y-2">
            <div className="flex justify-between py-1 border-b border-surface-container-high/50">
              <span className="text-outline">STAGE TIMEOUT BUDGET:</span>
              <span className="text-on-surface font-bold">120 Seconds (Configurable)</span>
            </div>
            <div className="flex justify-between py-1 border-b border-surface-container-high/50">
              <span className="text-outline">OBSERVATION AGENT TIMEOUT:</span>
              <span className="text-on-surface font-bold">100 Seconds (Leaves 20s headroom)</span>
            </div>
            <div className="flex justify-between py-1 border-b border-surface-container-high/50">
              <span className="text-outline">BOUNDED RETRY ATTEMPTS:</span>
              <span className="text-on-surface font-bold">3 Attempts Max</span>
            </div>
            <div className="flex justify-between py-1 border-b border-surface-container-high/50">
              <span className="text-outline">BACKOFF DELAY WINDOW:</span>
              <span className="text-on-surface font-bold">1s Initial • 8s Maximum</span>
            </div>
          </div>

          <div className="p-space-sm bg-surface-container rounded border border-surface-container-high space-y-2">
            <span className="text-primary font-bold uppercase text-[11px] block">
              Deterministic Fallback Guarantee
            </span>
            <p className="text-on-surface-variant text-xs leading-relaxed font-sans">
              If Google Gemini API encounters HTTP 503 (high demand) or times out after 100 seconds, execution gracefully transitions to the deterministic rule engine. Provenance is transparently tagged as <code className="text-[#F59E0B] font-mono">DETERMINISTIC_FALLBACK</code>.
            </p>
          </div>
        </div>
      </div>

      {/* Custom MCP Tools (7 Read-Only Tools) */}
      <div className="bg-surface-container-low rounded-xl border border-surface-container-high p-space-md shadow-sm space-y-space-sm">
        <div className="flex items-center justify-between border-b border-surface-container-high pb-space-xs font-mono text-xs">
          <div className="flex items-center gap-2">
            <span className="material-symbols-outlined text-primary text-[18px]">build_circle</span>
            <span className="font-bold text-on-surface">Registered Model Context Protocol (MCP) Tools</span>
          </div>
          <span className="text-tertiary font-bold">7 / 7 Active</span>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-2 font-mono text-xs pt-1">
          {[
            { name: 'scan_cluster', desc: 'Cluster-wide pod tallies, states, and warning events' },
            { name: 'get_pod_details', desc: 'Granular container exit codes, conditions, and probes' },
            { name: 'get_pod_logs', desc: 'Current and previous container logs (tail limited)' },
            { name: 'get_events', desc: 'Recent K8s warning and scheduling events' },
            { name: 'run_diagnosis', desc: 'Authoritative deterministic 10-scenario diagnosis engine' },
            { name: 'get_pvc_details', desc: 'PersistentVolumeClaim binding phase and storage class' },
            { name: 'get_resource_config', desc: 'Images, CPU/memory limits, probes, and volume refs' },
          ].map((t) => (
            <div
              key={t.name}
              className="p-2.5 bg-surface-container rounded border border-surface-container-high space-y-1"
            >
              <span className="text-[#818CF8] font-bold block">{t.name}</span>
              <span className="text-outline text-[11px] block">{t.desc}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Safety Guardrails */}
      <div className="p-space-md bg-surface-container-lowest rounded-xl border border-tertiary/40 space-y-2">
        <div className="flex items-center gap-2 text-tertiary font-mono text-xs font-bold">
          <span className="material-symbols-outlined text-[18px]">security</span>
          <span>Core Operational Safety Guardrails</span>
        </div>
        <ul className="text-xs font-mono text-on-surface-variant space-y-1 list-disc list-inside">
          <li><strong>Read-Only Cluster Access:</strong> The backend has zero write permissions. No <code>kubectl apply</code> or mutation is executed.</li>
          <li><strong>Mandatory Human-in-the-Loop Review:</strong> Remediation proposals remain non-executable until signed off by an authorized operator.</li>
          <li><strong>GitOps Staging:</strong> Approved proposals produce pull request diffs rather than direct production mutations.</li>
          <li><strong>Secret Data Protection:</strong> All environment variables and Secret references are masked across logs and telemetry.</li>
        </ul>
      </div>
    </div>
  );
};
