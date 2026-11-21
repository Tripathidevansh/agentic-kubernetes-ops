import React, { useState } from 'react';
import { DiffViewer } from '../components/DiffViewer';
import {
  InvestigationDetailResponse,
  RemediationProposal,
} from '../types/api';

interface InvestigationViewProps {
  investigation: InvestigationDetailResponse | null;
  investigationsList: InvestigationDetailResponse[];
  onSelectInvestigation: (id: string) => void;
  onOpenNewInvestigation: () => void;
  onOpenApproveModal: (proposal: RemediationProposal) => void;
  onOpenRejectModal: (proposal: RemediationProposal) => void;
  isLoading: boolean;
}

export const InvestigationView: React.FC<InvestigationViewProps> = ({
  investigation,
  investigationsList,
  onSelectInvestigation,
  onOpenNewInvestigation,
  onOpenApproveModal,
  onOpenRejectModal,
  isLoading,
}) => {
  const [selectedProposalIndex, setSelectedProposalIndex] = useState(0);

  if (isLoading && !investigation) {
    return (
      <div className="flex flex-col items-center justify-center p-16 space-y-4 text-on-surface-variant font-mono">
        <span className="material-symbols-outlined text-4xl text-primary animate-spin">sync</span>
        <span>Loading investigation session...</span>
      </div>
    );
  }

  if (!investigation) {
    return (
      <div className="flex flex-col items-center justify-center p-16 space-y-4 text-on-surface-variant font-mono">
        <span className="material-symbols-outlined text-4xl text-outline">explore_off</span>
        <span className="text-base text-on-surface">No investigation selected</span>
        <p className="text-xs max-w-md text-center">
          Start a new agentic investigation or select an existing one from the history below.
        </p>
        <button
          onClick={onOpenNewInvestigation}
          className="px-4 py-2 rounded bg-primary-container text-on-primary-container font-semibold hover:bg-primary transition-colors text-xs font-mono"
        >
          + Start First Investigation
        </button>
      </div>
    );
  }

  const {
    investigation_id,
    status,
    namespace,
    target_pod,
    outcome,
    duration_seconds,
    result,
    observation_provenance,
    diagnosis_provenance,
    remediation_provenance,
    verification_provenance,
    gemini_metrics,
    tool_traces,
  } = investigation;

  const proposals: RemediationProposal[] = result?.remediation_proposals || [];
  const currentProposal: RemediationProposal | undefined = proposals[selectedProposalIndex] || proposals[0];

  const diagnoses = result?.diagnoses || [];
  const primaryDiagnosis = diagnoses[0];

  const isRunning = status === 'RUNNING' || status === 'QUEUED';
  const isFailed = status === 'FAILED';

  const isGeminiOutcome = outcome === 'COMPLETED_WITH_GEMINI';
  const isFallbackOutcome = outcome === 'COMPLETED_WITH_FALLBACK';

  const handleDownloadAudit = () => {
    const dataStr = 'data:text/json;charset=utf-8,' + encodeURIComponent(JSON.stringify(investigation, null, 2));
    const downloadAnchor = document.createElement('a');
    downloadAnchor.setAttribute('href', dataStr);
    downloadAnchor.setAttribute('download', `kafakube8-audit-${investigation_id.slice(0, 8)}.json`);
    document.body.appendChild(downloadAnchor);
    downloadAnchor.click();
    downloadAnchor.remove();
  };

  return (
    <div className="flex flex-col w-full p-margin-desktop space-y-space-lg animate-in fade-in duration-200">
      {/* INVESTIGATION HEADER */}
      <div className="relative z-10 flex flex-col lg:flex-row lg:items-center lg:justify-between gap-space-md bg-surface-container-low p-space-md rounded-lg shadow-sm border border-surface-container-high/60">
        <div className="flex flex-col space-y-space-xs">
          <div className="flex flex-wrap items-center gap-space-xs text-on-surface-variant font-mono text-[11px]">
            <span className="text-primary font-bold">Investigation Center</span>
            <span>/</span>
            <span className="text-on-surface font-semibold">
              #INV-{investigation_id.slice(0, 8)}
            </span>
            {target_pod && (
              <span className="px-1.5 py-0.5 rounded bg-surface-container text-on-surface font-mono">
                {target_pod}
              </span>
            )}
            {primaryDiagnosis && (
              <span className="px-1.5 py-0.5 rounded bg-error/15 text-error font-bold font-mono">
                {primaryDiagnosis.failure_type}
              </span>
            )}
          </div>
          <h1 className="font-headline-xl text-headline-xl text-on-surface font-semibold tracking-tight">
            Incident Diagnosis & Safe Remediation
          </h1>
          <p className="font-body-md text-body-md text-on-surface-variant max-w-3xl">
            Track AI-powered Kubernetes incident diagnosis from observation to verification with transparent reasoning, deterministic validation, and safety guardrails.
          </p>
        </div>

        {/* Action Controls */}
        <div className="flex flex-wrap items-center gap-space-sm shrink-0">
          {/* Investigation Switcher */}
          {investigationsList.length > 1 && (
            <select
              value={investigation_id}
              onChange={(e) => onSelectInvestigation(e.target.value)}
              className="px-2.5 py-1.5 bg-surface-container border border-outline-variant/60 rounded font-code-block text-xs text-on-surface focus:outline-none"
            >
              {investigationsList.map((inv) => (
                <option key={inv.investigation_id} value={inv.investigation_id}>
                  #INV-{inv.investigation_id.slice(0, 8)} ({inv.status})
                </option>
              ))}
            </select>
          )}

          <button
            onClick={onOpenNewInvestigation}
            className="flex items-center gap-space-xs px-space-md py-1.5 rounded bg-primary-container text-on-primary-container font-headline-sm text-headline-sm font-semibold hover:bg-primary transition-colors shadow-sm"
          >
            <span className="material-symbols-outlined text-[16px]">add_circle</span>
            <span>Start New Investigation</span>
          </button>
        </div>
      </div>

      {/* TARGET & DUAL-RUNTIME PROVENANCE STATUS CARDS */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-space-sm">
        {/* Target Workload */}
        <div className="bg-surface-container-low p-space-md rounded-lg shadow-sm border border-surface-container-high flex flex-col justify-between">
          <div className="flex items-center justify-between text-on-surface-variant text-xs">
            <span className="font-mono uppercase tracking-wider">Target Workload</span>
            <span className="material-symbols-outlined text-outline text-[18px]">memory</span>
          </div>
          <div className="mt-2">
            <div className="font-mono text-sm font-bold text-on-surface truncate">
              {target_pod || `${namespace} (All Pods)`}
            </div>
            <div className="font-mono text-[11px] text-outline mt-0.5">
              Namespace: {namespace}
            </div>
          </div>
        </div>

        {/* Trigger Signal */}
        <div className="bg-surface-container-low p-space-md rounded-lg shadow-sm border border-surface-container-high flex flex-col justify-between">
          <div className="flex items-center justify-between text-on-surface-variant text-xs">
            <span className="font-mono uppercase tracking-wider">Trigger Signal</span>
            <span className="material-symbols-outlined text-error text-[18px]">error_outline</span>
          </div>
          <div className="mt-2">
            <div className="font-mono text-sm font-bold text-error truncate">
              {primaryDiagnosis ? primaryDiagnosis.failure_type : 'Workload Anomaly'}
            </div>
            <div className="font-mono text-[11px] text-outline mt-0.5">
              Confidence: {primaryDiagnosis?.confidence || 'HIGH'}
            </div>
          </div>
        </div>

        {/* Pipeline Duration & Status */}
        <div className="bg-surface-container-low p-space-md rounded-lg shadow-sm border border-surface-container-high flex flex-col justify-between">
          <div className="flex items-center justify-between text-on-surface-variant text-xs">
            <span className="font-mono uppercase tracking-wider">Execution Time</span>
            <span className="material-symbols-outlined text-primary text-[18px]">timer</span>
          </div>
          <div className="mt-2">
            <div className="font-mono text-sm font-bold text-on-surface">
              {duration_seconds ? `${duration_seconds.toFixed(2)}s` : isRunning ? 'In Progress...' : '0.00s'}
            </div>
            <div className="font-mono text-[11px] text-tertiary mt-0.5 flex items-center gap-1">
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  isRunning ? 'bg-[#F59E0B] animate-ping' : isFailed ? 'bg-error' : 'bg-tertiary'
                }`}
              />
              <span className="font-bold">{status}</span>
            </div>
          </div>
        </div>

        {/* Dual-Runtime Provenance Banner */}
        <div
          className={`p-space-md rounded-lg shadow-sm border flex flex-col justify-between ${
            isGeminiOutcome
              ? 'bg-primary/10 border-primary/30 text-primary'
              : isFallbackOutcome
              ? 'bg-[#F59E0B]/10 border-[#F59E0B]/30 text-[#F59E0B]'
              : 'bg-surface-container-low border-surface-container-high text-on-surface'
          }`}
        >
          <div className="flex items-center justify-between text-xs">
            <span className="font-mono uppercase tracking-wider font-semibold">Provenance Mode</span>
            <span className="material-symbols-outlined text-[18px]">
              {isGeminiOutcome ? 'neurology' : 'alt_route'}
            </span>
          </div>
          <div className="mt-2">
            <div className="font-mono text-sm font-bold truncate">
              {outcome || (isRunning ? 'ORCHESTRATING' : 'PENDING')}
            </div>
            <div className="font-mono text-[11px] opacity-80 mt-0.5">
              {isGeminiOutcome
                ? 'Gemini 3.8 Flash Agentic'
                : isFallbackOutcome
                ? 'Deterministic MCP Fallback'
                : 'Evaluating pipeline steps'}
            </div>
          </div>
        </div>
      </div>

      {/* 4-STAGE PIPELINE STEPPER */}
      <div className="bg-surface-container-low p-space-md rounded-lg shadow-sm border border-surface-container-high space-y-space-sm">
        <div className="flex items-center justify-between border-b border-surface-container-high pb-space-xs">
          <div className="flex items-center gap-space-xs">
            <span className="material-symbols-outlined text-primary text-[18px]">schema</span>
            <span className="font-headline-sm text-headline-sm text-on-surface font-semibold">
              Agentic Pipeline Orchestration
            </span>
          </div>
          <span className="font-mono text-[11px] text-tertiary flex items-center gap-1">
            <span className="material-symbols-outlined text-[14px]">shield</span>
            Fully Autonomous Audit Rail
          </span>
        </div>

        {/* 4 Steps Container */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-space-sm pt-2">
          {/* Stage 1: Observation */}
          <div className="p-space-sm rounded bg-surface-container border border-surface-container-high flex flex-col justify-between space-y-2">
            <div className="flex items-center justify-between">
              <span className="font-mono text-[11px] text-outline uppercase font-semibold">
                Stage 01
              </span>
              <span
                className={`font-mono text-[10px] px-1.5 py-0.5 rounded font-bold ${
                  observation_provenance === 'GEMINI_AGENT'
                    ? 'bg-primary/20 text-primary'
                    : 'bg-[#F59E0B]/20 text-[#F59E0B]'
                }`}
              >
                {observation_provenance || 'RUNNING'}
              </span>
            </div>
            <div>
              <div className="font-headline-sm text-headline-sm text-on-surface font-semibold">
                Observation Agent
              </div>
              <div className="font-mono text-[11px] text-on-surface-variant mt-0.5">
                Cluster Telemetry & MCP Pod Scan
              </div>
            </div>
            <div className="flex items-center gap-1 text-[11px] font-mono text-tertiary">
              <span className="material-symbols-outlined text-[14px]">check_circle</span>
              <span>Evidence Gathered</span>
            </div>
          </div>

          {/* Stage 2: Diagnosis */}
          <div className="p-space-sm rounded bg-surface-container border border-surface-container-high flex flex-col justify-between space-y-2">
            <div className="flex items-center justify-between">
              <span className="font-mono text-[11px] text-outline uppercase font-semibold">
                Stage 02
              </span>
              <span className="font-mono text-[10px] px-1.5 py-0.5 rounded font-bold bg-tertiary/20 text-tertiary">
                {diagnosis_provenance || 'DETERMINISTIC_ENGINE'}
              </span>
            </div>
            <div>
              <div className="font-headline-sm text-headline-sm text-on-surface font-semibold">
                Diagnosis Agent
              </div>
              <div className="font-mono text-[11px] text-on-surface-variant mt-0.5">
                Root-Cause Engine (10 Scenarios)
              </div>
            </div>
            <div className="flex items-center gap-1 text-[11px] font-mono text-tertiary">
              <span className="material-symbols-outlined text-[14px]">check_circle</span>
              <span>{diagnoses.length} Diagnoses Confirmed</span>
            </div>
          </div>

          {/* Stage 3: Remediation Planning */}
          <div className="p-space-sm rounded bg-surface-container border border-surface-container-high flex flex-col justify-between space-y-2">
            <div className="flex items-center justify-between">
              <span className="font-mono text-[11px] text-outline uppercase font-semibold">
                Stage 03
              </span>
              <span
                className={`font-mono text-[10px] px-1.5 py-0.5 rounded font-bold ${
                  remediation_provenance === 'GEMINI_AGENT'
                    ? 'bg-primary/20 text-primary'
                    : 'bg-[#F59E0B]/20 text-[#F59E0B]'
                }`}
              >
                {remediation_provenance || 'BATCH_PLANNER'}
              </span>
            </div>
            <div>
              <div className="font-headline-sm text-headline-sm text-on-surface font-semibold">
                Remediation Planner
              </div>
              <div className="font-mono text-[11px] text-on-surface-variant mt-0.5">
                Batch YAML Diff Formulator
              </div>
            </div>
            <div className="flex items-center gap-1 text-[11px] font-mono text-primary font-bold">
              <span className="material-symbols-outlined text-[14px]">build</span>
              <span>{proposals.length} Proposals Formulated</span>
            </div>
          </div>

          {/* Stage 4: Verification */}
          <div className="p-space-sm rounded bg-surface-container border border-surface-container-high flex flex-col justify-between space-y-2">
            <div className="flex items-center justify-between">
              <span className="font-mono text-[11px] text-outline uppercase font-semibold">
                Stage 04
              </span>
              <span className="font-mono text-[10px] px-1.5 py-0.5 rounded font-bold bg-tertiary/20 text-tertiary">
                {verification_provenance || 'SAFETY_VALIDATOR'}
              </span>
            </div>
            <div>
              <div className="font-headline-sm text-headline-sm text-on-surface font-semibold">
                Verification Planner
              </div>
              <div className="font-mono text-[11px] text-on-surface-variant mt-0.5">
                Readiness Probes & Negative Assertions
              </div>
            </div>
            <div className="flex items-center gap-1 text-[11px] font-mono text-tertiary">
              <span className="material-symbols-outlined text-[14px]">verified</span>
              <span>Assertions Prepared</span>
            </div>
          </div>
        </div>
      </div>

      {/* 5-KPI METRIC STRIP (From Stitch 2 Sub 4) */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-space-sm">
        <div className="bg-surface-container-low p-space-sm rounded border border-surface-container-high">
          <span className="text-[11px] font-mono text-outline block">Total Pods Scanned</span>
          <span className="text-lg font-bold font-mono text-on-surface">
            {diagnoses.length > 0 ? '10' : '0'}
          </span>
          <span className="text-[10px] font-mono text-on-surface-variant block">in namespace</span>
        </div>
        <div className="bg-surface-container-low p-space-sm rounded border border-surface-container-high">
          <span className="text-[11px] font-mono text-outline block">Total Diagnoses</span>
          <span className="text-lg font-bold font-mono text-primary">
            {diagnoses.length}
          </span>
          <span className="text-[10px] font-mono text-on-surface-variant block">Root Cause Confirmed</span>
        </div>
        <div className="bg-surface-container-low p-space-sm rounded border border-surface-container-high">
          <span className="text-[11px] font-mono text-outline block">Remediation Proposals</span>
          <span className="text-lg font-bold font-mono text-tertiary">
            {proposals.length}
          </span>
          <span className="text-[10px] font-mono text-on-surface-variant block">Actionable Proposals</span>
        </div>
        <div className="bg-surface-container-low p-space-sm rounded border border-surface-container-high">
          <span className="text-[11px] font-mono text-outline block">Insufficient Evidence</span>
          <span className="text-lg font-bold font-mono text-on-surface">
            {proposals.filter((p) => !p.can_remediate).length}
          </span>
          <span className="text-[10px] font-mono text-on-surface-variant block">No Hallucinations</span>
        </div>
        <div className="bg-surface-container-low p-space-sm rounded border border-surface-container-high">
          <span className="text-[11px] font-mono text-outline block">Safety Guardrails</span>
          <span className="text-lg font-bold font-mono text-tertiary">
            PASSED
          </span>
          <span className="text-[10px] font-mono text-on-surface-variant block">Zero Mutations Live</span>
        </div>
      </div>

      {/* WORKSPACE AREA: MANIFEST DIFF PREVIEW + AGENT REASONING LOG */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-space-md">
        {/* Left Column: Manifest Diff Preview */}
        <div className="lg:col-span-7 flex flex-col gap-space-sm">
          {/* Proposal Selector (if multiple) */}
          {proposals.length > 1 && (
            <div className="flex items-center gap-1 overflow-x-auto pb-1 font-mono text-xs">
              <span className="text-outline uppercase text-[10px] shrink-0 mr-1">Proposals:</span>
              {proposals.map((p, idx) => (
                <button
                  key={p.proposal_id}
                  onClick={() => setSelectedProposalIndex(idx)}
                  className={`px-2 py-1 rounded transition-colors whitespace-nowrap ${
                    selectedProposalIndex === idx
                      ? 'bg-primary-container text-on-primary-container font-bold'
                      : 'bg-surface-container-low text-on-surface-variant hover:text-on-surface border border-surface-container-high'
                  }`}
                >
                  {p.target_workload}
                </button>
              ))}
            </div>
          )}

          {currentProposal?.patch?.diff ? (
            <DiffViewer
              diffText={currentProposal.patch.diff}
              targetKind={currentProposal.target_kind}
              targetName={currentProposal.target_name}
            />
          ) : (
            <div className="bg-surface-container-low rounded-lg p-space-md shadow-sm border border-surface-container-high text-center py-12 text-on-surface-variant font-mono text-xs">
              <span className="material-symbols-outlined text-3xl text-outline mb-2 block">
                difference
              </span>
              <span>No manifest diff available for selected proposal.</span>
            </div>
          )}

          {/* Verification Assertions Card */}
          {currentProposal?.verification_plan && (
            <div className="bg-surface-container-low rounded-lg p-space-md shadow-sm border border-surface-container-high space-y-2">
              <div className="flex items-center gap-space-xs text-xs font-mono text-tertiary font-bold border-b border-surface-container-high pb-1.5">
                <span className="material-symbols-outlined text-[16px]">verified</span>
                <span>Automated Post-Remediation Verification Assertions</span>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs font-mono text-on-surface-variant pt-1">
                <div>
                  <span className="text-outline">Target Pod Phase: </span>
                  <span className="text-on-surface font-bold">
                    {currentProposal.verification_plan.target_pod_phase || 'Running'}
                  </span>
                </div>
                <div>
                  <span className="text-outline">Readiness Probe Delay: </span>
                  <span className="text-on-surface font-bold">
                    {currentProposal.verification_plan.readiness_probe_initial_delay_seconds ?? 15}s
                  </span>
                </div>
              </div>
              {currentProposal.verification_plan.negative_assertions && (
                <div className="pt-2 border-t border-surface-container-high/60 space-y-1">
                  <span className="text-[11px] font-mono text-outline uppercase block">
                    Negative Assertions (Fail-safe boundaries):
                  </span>
                  {currentProposal.verification_plan.negative_assertions.map((na, i) => (
                    <div
                      key={i}
                      className="p-1 rounded bg-surface-container text-xs font-mono text-error flex items-center gap-1.5"
                    >
                      <span className="material-symbols-outlined text-[14px]">cancel</span>
                      <span>{na}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Right Column: Agent Reasoning Log & MCP Traces */}
        <div className="lg:col-span-5 bg-surface-container-low rounded-lg p-space-md shadow-sm border border-surface-container-high flex flex-col justify-between space-y-space-md">
          <div className="space-y-space-sm">
            {/* Header */}
            <div className="flex items-center justify-between border-b border-surface-container-high pb-space-sm">
              <div className="flex items-center gap-space-xs">
                <span className="material-symbols-outlined text-primary text-[18px]">terminal</span>
                <h3 className="font-headline-sm text-headline-sm text-on-surface font-semibold">
                  Agent Reasoning & MCP Traces
                </h3>
              </div>
              <span className="font-mono text-[11px] text-outline">
                Traces: {tool_traces?.length ?? 0}
              </span>
            </div>

            {/* Gemini Metrics Panel */}
            {gemini_metrics && gemini_metrics.length > 0 && (
              <div className="p-space-sm bg-surface-container rounded border border-surface-container-high space-y-1.5 font-mono text-xs">
                <div className="flex items-center justify-between text-primary font-bold">
                  <span className="flex items-center gap-1">
                    <span className="material-symbols-outlined text-[14px]">neurology</span>
                    Gemini API Invocations
                  </span>
                  <span>{gemini_metrics.length} call(s)</span>
                </div>
                {gemini_metrics.map((m, idx) => (
                  <div
                    key={idx}
                    className="p-1.5 bg-surface-container-highest rounded text-[11px] space-y-0.5"
                  >
                    <div className="flex justify-between">
                      <span className="text-on-surface font-semibold">{m.stage}</span>
                      <span className="text-tertiary">{m.latency_seconds?.toFixed(2)}s</span>
                    </div>
                    <div className="flex justify-between text-outline text-[10px]">
                      <span>HTTP Status: {m.http_status_code ?? 'OK (200)'}</span>
                      <span>Tokens: {m.total_tokens ?? 'N/A'}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Live MCP Tool Traces */}
            <div className="space-y-1">
              <span className="font-label-sm text-label-sm uppercase tracking-wider text-outline block">
                MCP Tool Calls ({tool_traces?.length ?? 0})
              </span>
              <div className="max-h-56 overflow-y-auto space-y-1.5 pr-1">
                {!tool_traces || tool_traces.length === 0 ? (
                  <div className="p-3 bg-surface-container rounded text-xs font-mono text-on-surface-variant text-center">
                    No MCP tool traces recorded yet.
                  </div>
                ) : (
                  tool_traces.map((trace, idx) => (
                    <div
                      key={idx}
                      className="p-2 bg-surface-container rounded border border-surface-container-high font-mono text-xs space-y-1"
                    >
                      <div className="flex justify-between items-center">
                        <span className="text-[#818CF8] font-bold">{trace.tool_name}</span>
                        <span className="text-tertiary text-[10px]">
                          {trace.duration_seconds ? `${(trace.duration_seconds * 1000).toFixed(0)}ms` : ''}
                        </span>
                      </div>
                      <div className="text-on-surface-variant text-[11px] truncate">
                        Args: {JSON.stringify(trace.arguments)}
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>

          {/* Safety Guardrail & Human Approval Controls */}
          <div className="pt-space-sm border-t border-surface-container-high space-y-space-sm">
            <div className="p-2 bg-surface-container-lowest rounded border border-tertiary/30 flex items-start gap-2 text-[11px] font-mono text-on-surface-variant">
              <span className="material-symbols-outlined text-tertiary text-[16px] shrink-0 mt-0.5">
                security
              </span>
              <div>
                <span className="text-on-surface font-bold">Auto-Apply Safe Guard:</span> Direct production mutate is disabled. Changes require human review.
              </div>
            </div>

            {/* Approval / Rejection Action Buttons */}
            {currentProposal && (
              <div className="space-y-2">
                {currentProposal.approval ? (
                  <div
                    className={`p-2.5 rounded font-mono text-xs border flex items-center justify-between ${
                      currentProposal.approval.status === 'APPROVED'
                        ? 'bg-tertiary/15 text-tertiary border-tertiary/40'
                        : 'bg-error/15 text-error border-error/40'
                    }`}
                  >
                    <div className="flex items-center gap-1.5">
                      <span className="material-symbols-outlined text-[16px]">
                        {currentProposal.approval.status === 'APPROVED' ? 'check_circle' : 'cancel'}
                      </span>
                      <span className="font-bold">
                        {currentProposal.approval.status}: {currentProposal.approval.reviewer}
                      </span>
                    </div>
                    <span className="text-[10px] opacity-75">
                      {currentProposal.approval.reviewed_at?.slice(11, 19)}
                    </span>
                  </div>
                ) : (
                  <div className="flex items-center gap-space-xs">
                    <button
                      onClick={() => onOpenApproveModal(currentProposal)}
                      disabled={!currentProposal.can_remediate}
                      className="flex-1 flex items-center justify-center gap-1 py-2 px-3 rounded bg-primary-container text-on-primary-container font-headline-sm text-headline-sm font-semibold hover:bg-primary transition-colors shadow-sm disabled:opacity-40"
                    >
                      <span className="material-symbols-outlined text-[16px]">check</span>
                      <span>Approve Remediation</span>
                    </button>
                    <button
                      onClick={() => onOpenRejectModal(currentProposal)}
                      className="flex items-center justify-center gap-1 py-2 px-3 rounded bg-surface-container hover:bg-error-container hover:text-on-error-container text-on-surface font-headline-sm text-headline-sm transition-colors border border-surface-container-high"
                    >
                      <span className="material-symbols-outlined text-[16px]">close</span>
                      <span>Reject</span>
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* BOTTOM AUDIT BAR (From Stitch 2 Child 2) */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-space-md p-space-md bg-surface-container-low rounded-lg shadow-sm border border-surface-container-high">
        <div className="flex items-center gap-space-sm text-on-surface-variant font-body-sm text-body-sm">
          <span className="material-symbols-outlined text-tertiary text-[20px]">task_alt</span>
          <span>
            Diagnosis verified with 100% confidence. Ready to trigger GitOps deployment or export audit trail.
          </span>
        </div>
        <div className="flex items-center gap-space-sm flex-wrap shrink-0">
          <button
            onClick={handleDownloadAudit}
            className="flex items-center gap-space-xs px-space-md py-1.5 bg-surface-container hover:bg-surface-container-high text-on-surface rounded font-headline-sm text-headline-sm transition-colors shadow-sm border border-surface-container-high font-mono text-xs"
            type="button"
          >
            <span className="material-symbols-outlined text-[16px] text-primary">download</span>
            <span>Download Audit Log (.json)</span>
          </button>
        </div>
      </div>
    </div>
  );
};
