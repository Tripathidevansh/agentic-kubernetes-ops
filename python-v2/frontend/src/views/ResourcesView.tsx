import React, { useState } from 'react';
import { PodOverviewItem } from '../types/api';

interface ResourcesViewProps {
  pods: PodOverviewItem[];
  namespace: string;
  clusterName: string;
  onInvestigatePod: (podName: string) => void;
}

export const ResourcesView: React.FC<ResourcesViewProps> = ({
  pods,
  namespace,
  clusterName,
  onInvestigatePod,
}) => {
  const [search, setSearch] = useState('');
  const [phaseFilter, setPhaseFilter] = useState('ALL');

  const filteredPods = pods.filter((p) => {
    const matchesSearch =
      p.name.toLowerCase().includes(search.toLowerCase()) ||
      (p.node_name && p.node_name.toLowerCase().includes(search.toLowerCase()));
    const matchesPhase = phaseFilter === 'ALL' || p.phase.toUpperCase() === phaseFilter;
    return matchesSearch && matchesPhase;
  });

  return (
    <div className="flex flex-col w-full p-margin-desktop space-y-space-lg animate-in fade-in duration-200">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-space-md border-b border-surface-container-high/40 pb-space-md">
        <div className="flex flex-col space-y-space-xs">
          <div className="flex items-center gap-space-xs text-on-surface-variant font-mono text-[11px]">
            <span className="text-primary font-bold">Cluster Resources</span>
            <span>/</span>
            <span>Pod Inventory</span>
            <span className="px-1.5 py-0.5 rounded bg-surface-container text-tertiary ml-2">
              Secret Masking Verified
            </span>
          </div>
          <h1 className="font-headline-xl text-headline-xl text-on-surface font-semibold tracking-tight">
            Workload & Pod Topology
          </h1>
          <p className="font-body-md text-body-md text-on-surface-variant max-w-2xl">
            Real-time status of all workload pods in cluster <span className="font-mono text-primary">{clusterName}</span> (namespace: <span className="font-mono text-primary">{namespace}</span>).
          </p>
        </div>
      </div>

      {/* Filter and Search Bar */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-space-sm bg-surface-container-low p-space-sm rounded-lg border border-surface-container-high">
        <div className="relative flex-1 max-w-md">
          <span className="material-symbols-outlined absolute left-2.5 top-2 text-[18px] text-outline">
            search
          </span>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter pods by name or node..."
            className="w-full pl-9 pr-3 py-1.5 bg-surface-container border border-outline-variant/50 rounded font-code-block text-xs text-on-surface focus:outline-none focus:border-primary"
          />
        </div>

        <div className="flex items-center gap-1 font-mono text-xs overflow-x-auto">
          {['ALL', 'RUNNING', 'PENDING', 'FAILED'].map((phase) => (
            <button
              key={phase}
              onClick={() => setPhaseFilter(phase)}
              className={`px-3 py-1 rounded transition-colors ${
                phaseFilter === phase
                  ? 'bg-primary-container text-on-primary-container font-bold'
                  : 'bg-surface-container text-on-surface-variant hover:text-on-surface'
              }`}
            >
              {phase}
            </button>
          ))}
        </div>
      </div>

      {/* Pods Table */}
      <div className="bg-surface-container-lowest rounded-xl overflow-hidden shadow-sm border border-surface-container-high">
        <div className="p-space-sm bg-surface-container-low border-b border-surface-container-high flex items-center justify-between font-mono text-xs">
          <div className="flex items-center gap-2">
            <span className="material-symbols-outlined text-primary text-[18px]">layers</span>
            <span className="font-bold text-on-surface">Registered Pods ({filteredPods.length})</span>
          </div>
          <span className="text-tertiary text-[11px]">Strict Read-Only Collector</span>
        </div>

        <div className="overflow-x-auto w-full">
          <table className="w-full text-left border-collapse font-mono text-xs">
            <thead>
              <tr className="border-b border-surface-container-high bg-surface-container text-outline text-[11px] uppercase tracking-wider">
                <th className="py-2.5 px-space-md">Pod Name</th>
                <th className="py-2.5 px-space-md">Phase</th>
                <th className="py-2.5 px-space-md">Ready</th>
                <th className="py-2.5 px-space-md">Restarts</th>
                <th className="py-2.5 px-space-md">Node</th>
                <th className="py-2.5 px-space-md">Status Reason</th>
                <th className="py-2.5 px-space-md text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-surface-container-high text-xs">
              {filteredPods.length === 0 ? (
                <tr>
                  <td colSpan={7} className="py-8 text-center text-on-surface-variant">
                    No pods found matching criteria.
                  </td>
                </tr>
              ) : (
                filteredPods.map((p, idx) => {
                  const isRunning = p.phase.toLowerCase() === 'running';
                  const isPending = p.phase.toLowerCase() === 'pending';
                  const isReady = p.is_ready ?? p.ready ?? false;
                  const firstStatus = p.container_statuses?.[0];
                  const reasonText =
                    p.status_reason ||
                    p.message ||
                    firstStatus?.reason ||
                    firstStatus?.message ||
                    firstStatus?.state ||
                    '—';

                  return (
                    <tr key={idx} className="hover:bg-surface-container transition-colors">
                      <td className="py-2.5 px-space-md font-bold text-on-surface whitespace-nowrap">
                        {p.name}
                      </td>
                      <td className="py-2.5 px-space-md whitespace-nowrap">
                        <span
                          className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                            isRunning
                              ? 'bg-tertiary/20 text-tertiary'
                              : isPending
                              ? 'bg-[#F59E0B]/20 text-[#F59E0B]'
                              : 'bg-error/20 text-error'
                          }`}
                        >
                          {p.phase}
                        </span>
                      </td>
                      <td className="py-2.5 px-space-md whitespace-nowrap">
                        <span className={isReady ? 'text-tertiary font-bold' : 'text-error font-bold'}>
                          {isReady ? 'Yes' : 'No'}
                        </span>
                      </td>
                      <td className="py-2.5 px-space-md text-on-surface whitespace-nowrap">
                        <span className={p.restarts > 0 ? 'text-[#F97316] font-bold' : 'text-outline'}>
                          {p.restarts}
                        </span>
                      </td>
                      <td className="py-2.5 px-space-md text-on-surface-variant whitespace-nowrap">
                        {p.node_name || 'Unscheduled'}
                      </td>
                      <td className="py-2.5 px-space-md text-outline truncate max-w-xs" title={reasonText}>
                        {reasonText}
                      </td>
                      <td className="py-2.5 px-space-md text-right whitespace-nowrap">
                        <button
                          onClick={() => onInvestigatePod(p.name)}
                          className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-surface-container hover:bg-primary-container hover:text-on-primary-container text-on-surface transition-colors text-[11px]"
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
      </div>
    </div>
  );
};
