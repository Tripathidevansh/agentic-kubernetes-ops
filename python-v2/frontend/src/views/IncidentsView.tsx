import React, { useState } from 'react';
import { IncidentItem } from '../types/api';

interface IncidentsViewProps {
  incidents: IncidentItem[];
  namespace: string;
  onInvestigatePod: (podName: string) => void;
  onBatchInvestigate: () => void;
}

export const IncidentsView: React.FC<IncidentsViewProps> = ({
  incidents,
  namespace,
  onInvestigatePod,
  onBatchInvestigate,
}) => {
  const [search, setSearch] = useState('');
  const [severityFilter, setSeverityFilter] = useState<string>('ALL');
  const [selectedIncident, setSelectedIncident] = useState<IncidentItem | null>(null);

  const filtered = incidents.filter((inc) => {
    const podName = inc.pod_name || inc.pod || '';
    const failureType = inc.failure_type || '';
    const rootCause = inc.root_cause || '';
    const matchesSearch =
      podName.toLowerCase().includes(search.toLowerCase()) ||
      failureType.toLowerCase().includes(search.toLowerCase()) ||
      rootCause.toLowerCase().includes(search.toLowerCase());

    const incSev = (inc.severity || '').toUpperCase();
    const matchesSev = severityFilter === 'ALL' || incSev === severityFilter;

    return matchesSearch && matchesSev;
  });

  const handleExportTelemetry = () => {
    const dataStr = 'data:text/json;charset=utf-8,' + encodeURIComponent(JSON.stringify(incidents, null, 2));
    const downloadAnchor = document.createElement('a');
    downloadAnchor.setAttribute('href', dataStr);
    downloadAnchor.setAttribute('download', `kafakube8-telemetry-${namespace}-${Date.now()}.json`);
    document.body.appendChild(downloadAnchor);
    downloadAnchor.click();
    downloadAnchor.remove();
  };

  return (
    <div className="flex flex-col w-full p-margin-desktop space-y-space-lg animate-in fade-in duration-200">
      {/* Top Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-space-md border-b border-surface-container-high/40 pb-space-md">
        <div className="flex flex-col space-y-space-xs">
          <div className="flex items-center gap-space-xs text-on-surface-variant font-label-sm text-[11px] font-mono">
            <span>Incidents</span>
            <span>/</span>
            <span className="text-primary font-semibold">Cluster Triage View</span>
            <span className="px-1.5 py-0.5 rounded bg-surface-container text-tertiary ml-2">
              MCP Tool: k8s-telemetry-pull
            </span>
          </div>
          <h1 className="font-headline-xl text-headline-xl text-on-surface font-semibold tracking-tight">
            Cluster Incidents
          </h1>
          <p className="font-body-md text-body-md text-on-surface-variant max-w-2xl">
            Identify, investigate and understand Kubernetes workload failures with automated root-cause evidence.
          </p>
        </div>

        {/* Quick Context Actions */}
        <div className="flex items-center gap-space-sm flex-wrap">
          <button
            onClick={handleExportTelemetry}
            className="flex items-center gap-space-xs px-space-md py-1.5 rounded bg-surface-container hover:bg-surface-container-high text-on-surface font-headline-sm text-headline-sm transition-colors border border-surface-container-high"
            type="button"
          >
            <span className="material-symbols-outlined text-[16px] text-primary">file_download</span>
            <span>Export Telemetry</span>
          </button>
          <button
            onClick={onBatchInvestigate}
            className="flex items-center gap-space-xs px-space-md py-1.5 rounded bg-primary-container text-on-primary-container font-headline-sm text-headline-sm font-semibold hover:bg-primary transition-colors shadow-sm"
            type="button"
          >
            <span className="material-symbols-outlined text-[16px]">psychology</span>
            <span>Batch Investigate</span>
          </button>
        </div>
      </div>

      {/* Filter / Search Bar */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-space-sm bg-surface-container-low p-space-sm rounded-lg border border-surface-container-high">
        {/* Search Input */}
        <div className="relative flex-1 max-w-md">
          <span className="material-symbols-outlined absolute left-2.5 top-2 text-[18px] text-outline">
            search
          </span>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by pod, failure type, or root cause..."
            className="w-full pl-9 pr-3 py-1.5 bg-surface-container border border-outline-variant/50 rounded font-code-block text-xs text-on-surface focus:outline-none focus:border-primary"
          />
        </div>

        {/* Severity Filter Chips */}
        <div className="flex items-center gap-1 font-mono text-xs overflow-x-auto">
          {['ALL', 'CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].map((sev) => (
            <button
              key={sev}
              onClick={() => setSeverityFilter(sev)}
              className={`px-2.5 py-1 rounded transition-colors ${
                severityFilter === sev
                  ? 'bg-primary-container text-on-primary-container font-bold'
                  : 'bg-surface-container text-on-surface-variant hover:text-on-surface'
              }`}
            >
              {sev}
            </button>
          ))}
        </div>
      </div>

      {/* Main Content Area: Table + Side Drawer */}
      <div className="flex flex-col lg:flex-row gap-space-md items-start w-full">
        {/* Incident Stream Table */}
        <div className="flex-1 min-w-0 bg-surface-container-lowest rounded-xl overflow-hidden shadow-lg border border-surface-container-high/60 w-full">
          <div className="p-space-sm bg-surface-container-low flex items-center justify-between border-b border-surface-container-high">
            <div className="flex items-center gap-space-xs">
              <span className="material-symbols-outlined text-primary text-[18px]">dataset</span>
              <span className="font-headline-sm text-headline-sm text-on-surface font-semibold">
                Kubernetes Workload Failure Stream
              </span>
              <span className="font-label-sm text-label-sm px-1.5 py-0.5 rounded bg-surface-container text-on-surface-variant font-mono">
                {filtered.length} items
              </span>
            </div>
            <div className="flex items-center gap-2 text-xs font-mono text-tertiary">
              <span className="w-1.5 h-1.5 rounded-full bg-tertiary animate-pulse" />
              <span>Live Ingestion Active</span>
            </div>
          </div>

          <div className="overflow-x-auto w-full">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-surface-container-high bg-surface-container text-outline font-label-sm text-[11px] uppercase tracking-wider font-mono">
                  <th className="py-2.5 px-space-md">Incident ID / Failure</th>
                  <th className="py-2.5 px-space-md">Severity</th>
                  <th className="py-2.5 px-space-md">Workload Target</th>
                  <th className="py-2.5 px-space-md">Diagnosis Summary</th>
                  <th className="py-2.5 px-space-md">Confidence</th>
                  <th className="py-2.5 px-space-md text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-surface-container-high text-xs">
                {filtered.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="py-12 text-center text-on-surface-variant font-mono">
                      No incidents match current filter.
                    </td>
                  </tr>
                ) : (
                  filtered.map((inc, idx) => {
                    const podName = inc.pod_name || inc.pod || `pod-${idx}`;
                    const incId = `#INC-${8900 + idx}`;
                    const sev = (inc.severity || 'HIGH').toUpperCase();

                    let sevBadge = 'text-primary bg-primary/10 border-primary/30';
                    if (sev === 'CRITICAL') sevBadge = 'text-error bg-error/15 border-error/40';
                    else if (sev === 'HIGH') sevBadge = 'text-[#F97316] bg-[#F97316]/15 border-[#F97316]/40';
                    else if (sev === 'MEDIUM') sevBadge = 'text-[#F59E0B] bg-[#F59E0B]/15 border-[#F59E0B]/40';

                    const isSelected = selectedIncident?.pod_name === podName || selectedIncident?.pod === podName;

                    return (
                      <tr
                        key={idx}
                        onClick={() => setSelectedIncident(inc)}
                        className={`transition-colors cursor-pointer ${
                          isSelected
                            ? 'bg-surface-container-high'
                            : 'hover:bg-surface-container'
                        }`}
                      >
                        <td className="py-3 px-space-md whitespace-nowrap">
                          <div className="flex flex-col font-mono">
                            <span className="text-outline text-[11px]">{incId}</span>
                            <span className="text-on-surface font-semibold text-xs">
                              {inc.failure_type}
                            </span>
                          </div>
                        </td>
                        <td className="py-3 px-space-md whitespace-nowrap">
                          <span
                            className={`inline-block px-2 py-0.5 rounded font-mono text-[11px] font-bold border ${sevBadge}`}
                          >
                            {sev}
                          </span>
                        </td>
                        <td className="py-3 px-space-md font-mono text-on-surface font-semibold whitespace-nowrap">
                          {podName}
                        </td>
                        <td className="py-3 px-space-md text-on-surface-variant max-w-sm truncate">
                          {inc.root_cause || inc.evidence_summary || 'Root-cause evidence collected.'}
                        </td>
                        <td className="py-3 px-space-md font-mono text-on-surface-variant whitespace-nowrap">
                          <span className="px-1.5 py-0.5 rounded bg-surface-container text-tertiary text-[11px]">
                            {inc.confidence || 'HIGH'}
                          </span>
                        </td>
                        <td className="py-3 px-space-md text-right whitespace-nowrap space-x-1">
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              onInvestigatePod(podName);
                            }}
                            className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-primary-container text-on-primary-container hover:bg-primary font-mono text-[11px] font-semibold transition-colors"
                          >
                            <span className="material-symbols-outlined text-[13px]">psychology</span>
                            <span>Investigate</span>
                          </button>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>

          <div className="p-space-sm bg-surface-container-lowest border-t border-surface-container-high flex items-center justify-between font-mono text-[11px] text-outline">
            <span>Cluster ID: us-east1-prod-02 • Node Pool: pool-c2d-highmem-8</span>
            <span className="text-tertiary">Deterministic Rule Engine Active</span>
          </div>
        </div>

        {/* Detail Side Drawer (if incident selected) */}
        {selectedIncident && (
          <div className="w-full lg:w-96 bg-surface-container-low rounded-xl border border-surface-container-high p-space-md shadow-xl space-y-space-md animate-in slide-in-from-right-4 duration-150">
            <div className="flex items-center justify-between border-b border-surface-container-high pb-space-sm">
              <div className="flex items-center gap-space-xs">
                <span className="material-symbols-outlined text-primary text-[18px]">troubleshoot</span>
                <h3 className="font-headline-sm text-headline-sm text-on-surface font-semibold">
                  Incident Evidence
                </h3>
              </div>
              <button
                onClick={() => setSelectedIncident(null)}
                className="text-on-surface-variant hover:text-on-surface p-1 rounded"
              >
                <span className="material-symbols-outlined text-[16px]">close</span>
              </button>
            </div>

            {/* Incident Summary Card */}
            <div className="p-space-sm bg-surface-container rounded-lg border border-surface-container-high space-y-2 font-mono text-xs">
              <div className="flex justify-between">
                <span className="text-outline">TARGET POD:</span>
                <span className="text-on-surface font-bold truncate max-w-[200px]">
                  {selectedIncident.pod_name || selectedIncident.pod}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-outline">SCENARIO:</span>
                <span className="text-primary font-semibold">{selectedIncident.failure_type}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-outline">SEVERITY:</span>
                <span className="text-error font-bold">{selectedIncident.severity}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-outline">CONFIDENCE:</span>
                <span className="text-tertiary font-bold">{selectedIncident.confidence}</span>
              </div>
            </div>

            {/* Root Cause Details */}
            <div className="space-y-1">
              <span className="font-label-sm text-label-sm uppercase tracking-wider text-outline block">
                Deterministic Root Cause
              </span>
              <div className="p-space-sm bg-surface-container rounded text-xs text-on-surface font-mono leading-relaxed border border-outline-variant/30">
                {selectedIncident.root_cause}
              </div>
            </div>

            {/* Evidence List */}
            {selectedIncident.evidence && selectedIncident.evidence.length > 0 && (
              <div className="space-y-1">
                <span className="font-label-sm text-label-sm uppercase tracking-wider text-outline block">
                  Cluster Telemetry Evidence
                </span>
                <div className="space-y-1">
                  {selectedIncident.evidence.map((ev, i) => (
                    <div
                      key={i}
                      className="p-1.5 bg-surface-container-highest rounded text-[11px] text-on-surface-variant font-mono"
                    >
                      • {ev}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Recommended Action */}
            <div className="space-y-1">
              <span className="font-label-sm text-label-sm uppercase tracking-wider text-outline block">
                Recommended Remediation
              </span>
              <div className="p-space-sm bg-surface-container rounded text-xs text-tertiary font-mono border border-tertiary/20">
                {selectedIncident.recommended_action || selectedIncident.suggested_action || 'Generate GitOps patch.'}
              </div>
            </div>

            {/* Dispatch Action */}
            <button
              onClick={() => {
                const pod = selectedIncident.pod_name || selectedIncident.pod || '';
                onInvestigatePod(pod);
              }}
              className="w-full flex items-center justify-center gap-space-xs px-space-md py-2 rounded bg-primary-container text-on-primary-container font-headline-sm text-headline-sm font-semibold hover:bg-primary transition-colors shadow-sm"
            >
              <span className="material-symbols-outlined text-[18px]">psychology</span>
              <span>Launch Agentic Investigation</span>
            </button>
          </div>
        )}
      </div>
    </div>
  );
};
