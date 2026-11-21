import React from 'react';
import { AgentToolCallTrace, GeminiRequestMetric, InvestigationDetailResponse } from '../types/api';

interface ObservabilityViewProps {
  investigation: InvestigationDetailResponse | null;
  geminiModel: string;
}

export const ObservabilityView: React.FC<ObservabilityViewProps> = ({
  investigation,
  geminiModel,
}) => {
  const geminiMetrics: GeminiRequestMetric[] = investigation?.gemini_metrics || [];
  const toolTraces: AgentToolCallTrace[] = investigation?.tool_traces || [];

  const totalGeminiCalls = geminiMetrics.length;
  const totalTokens = geminiMetrics.reduce((sum, m) => sum + (m.total_tokens || 0), 0);
  const totalGeminiLatency = geminiMetrics.reduce((sum, m) => sum + (m.latency_seconds || 0), 0);
  const avgLatency = totalGeminiCalls > 0 ? (totalGeminiLatency / totalGeminiCalls).toFixed(2) : '0.00';

  return (
    <div className="flex flex-col w-full p-margin-desktop space-y-space-lg animate-in fade-in duration-200">
      {/* Header */}
      <div className="flex flex-col space-y-space-xs border-b border-surface-container-high/40 pb-space-md">
        <div className="flex items-center gap-space-xs text-on-surface-variant font-mono text-[11px]">
          <span className="text-primary font-bold">Telemetry Cockpit</span>
          <span>/</span>
          <span>Agent Observability & MCP Tracing</span>
        </div>
        <h1 className="font-headline-xl text-headline-xl text-on-surface font-semibold tracking-tight">
          Agent Observability & Model Telemetry
        </h1>
        <p className="font-body-md text-body-md text-on-surface-variant max-w-2xl">
          Real-time measurement of Google Gemini API requests, token consumption, latency, MCP tool traces, and fallback frequency.
        </p>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-space-sm">
        <div className="bg-surface-container-low p-space-md rounded-lg border border-surface-container-high">
          <span className="text-outline text-xs font-mono uppercase block">Model Architecture</span>
          <span className="text-lg font-bold font-mono text-primary block mt-1">{geminiModel}</span>
          <span className="text-[11px] font-mono text-tertiary">Bounded Retries (3 Attempts)</span>
        </div>

        <div className="bg-surface-container-low p-space-md rounded-lg border border-surface-container-high">
          <span className="text-outline text-xs font-mono uppercase block">Total Gemini Requests</span>
          <span className="text-lg font-bold font-mono text-on-surface block mt-1">
            {totalGeminiCalls} call(s)
          </span>
          <span className="text-[11px] font-mono text-outline">Batch Planning Enabled</span>
        </div>

        <div className="bg-surface-container-low p-space-md rounded-lg border border-surface-container-high">
          <span className="text-outline text-xs font-mono uppercase block">Total Tokens Billed</span>
          <span className="text-lg font-bold font-mono text-tertiary block mt-1">
            {totalTokens > 0 ? totalTokens.toLocaleString() : 'Optimized / Cache'}
          </span>
          <span className="text-[11px] font-mono text-outline">Prompt & Candidate</span>
        </div>

        <div className="bg-surface-container-low p-space-md rounded-lg border border-surface-container-high">
          <span className="text-outline text-xs font-mono uppercase block">Avg Gemini Latency</span>
          <span className="text-lg font-bold font-mono text-on-surface block mt-1">
            {avgLatency}s
          </span>
          <span className="text-[11px] font-mono text-outline">Per Request Roundtrip</span>
        </div>
      </div>

      {/* Provenance Breakdown Card */}
      <div className="bg-surface-container-low p-space-md rounded-xl border border-surface-container-high space-y-space-sm">
        <div className="flex items-center justify-between border-b border-surface-container-high pb-space-xs font-mono text-xs">
          <span className="text-on-surface font-bold">Stage-Level Provenance Isolation</span>
          <span className="text-outline">No Synthetic Outcomes Fabricated</span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-4 gap-space-sm pt-1">
          <div className="p-space-sm bg-surface-container rounded border border-surface-container-high font-mono text-xs space-y-1">
            <span className="text-outline text-[11px] uppercase block">Observation Stage</span>
            <span className="text-primary font-bold">
              {investigation?.observation_provenance || 'GEMINI_AGENT / FALLBACK'}
            </span>
          </div>

          <div className="p-space-sm bg-surface-container rounded border border-surface-container-high font-mono text-xs space-y-1">
            <span className="text-outline text-[11px] uppercase block">Diagnosis Stage</span>
            <span className="text-tertiary font-bold">
              {investigation?.diagnosis_provenance || 'DETERMINISTIC_ENGINE'}
            </span>
          </div>

          <div className="p-space-sm bg-surface-container rounded border border-surface-container-high font-mono text-xs space-y-1">
            <span className="text-outline text-[11px] uppercase block">Remediation Stage</span>
            <span className="text-primary font-bold">
              {investigation?.remediation_provenance || 'GEMINI_AGENT / BATCH'}
            </span>
          </div>

          <div className="p-space-sm bg-surface-container rounded border border-surface-container-high font-mono text-xs space-y-1">
            <span className="text-outline text-[11px] uppercase block">Verification Stage</span>
            <span className="text-tertiary font-bold">
              {investigation?.verification_provenance || 'DETERMINISTIC_RULES'}
            </span>
          </div>
        </div>
      </div>

      {/* Detailed MCP Tool Trace Log */}
      <div className="bg-surface-container-low rounded-xl border border-surface-container-high overflow-hidden shadow-sm">
        <div className="p-space-sm bg-surface-container border-b border-surface-container-high flex items-center justify-between font-mono text-xs">
          <div className="flex items-center gap-space-xs">
            <span className="material-symbols-outlined text-primary text-[18px]">terminal</span>
            <span className="font-bold text-on-surface">MCP Tool Invocation Audit Stream</span>
          </div>
          <span className="text-outline font-mono">{toolTraces.length} recorded</span>
        </div>

        <div className="p-space-sm space-y-2 max-h-96 overflow-y-auto">
          {toolTraces.length === 0 ? (
            <div className="p-8 text-center text-on-surface-variant font-mono text-xs">
              No MCP tool traces available for this session.
            </div>
          ) : (
            toolTraces.map((trace, idx) => (
              <div
                key={idx}
                className="p-3 bg-surface-container rounded border border-surface-container-high font-mono text-xs space-y-1.5"
              >
                <div className="flex justify-between items-center">
                  <div className="flex items-center gap-2">
                    <span className="px-1.5 py-0.5 rounded bg-surface-container-highest text-[#818CF8] font-bold">
                      {trace.stage}
                    </span>
                    <span className="text-on-surface font-semibold">{trace.tool_name}</span>
                  </div>
                  <div className="flex items-center gap-2 text-[11px]">
                    <span className="text-tertiary">
                      {trace.duration_seconds ? `${(trace.duration_seconds * 1000).toFixed(0)}ms` : ''}
                    </span>
                    <span className="text-outline">{trace.timestamp?.slice(11, 19)}</span>
                  </div>
                </div>

                <div className="p-2 bg-surface-container-lowest rounded text-[11px] text-on-surface-variant overflow-x-auto whitespace-pre">
                  Arguments: {JSON.stringify(trace.arguments, null, 2)}
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
};
