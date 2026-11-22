import React from 'react';
import { ClusterOverviewResponse, IncidentItem } from '../types/api';

interface OverviewViewProps {
  overview: ClusterOverviewResponse | null;
  incidents: IncidentItem[];
  geminiModel: string;
  operatorName?: string;
  onInvestigatePod: (podName: string) => void;
  onViewAllIncidents: () => void;
  onOpenNewInvestigation: () => void;
}

export const OverviewView: React.FC<OverviewViewProps> = ({
  overview,
  incidents,
  geminiModel,
  operatorName = 'Operator',
  onInvestigatePod,
  onViewAllIncidents,
  onOpenNewInvestigation,
}) => {
  const totalPods = overview?.total_pods ?? 0;
  const runningPods = overview?.running_pods ?? 0;
  const pendingPods = overview?.pending_pods ?? 0;
  const failedPods = overview?.failed_pods ?? 0;
  const totalRestarts =
    overview?.total_restarts ??
    (overview?.pods?.reduce((sum, p) => sum + (p.restarts || 0), 0) || 0);
  const activeIncidentCount = incidents.length;

  const runningPct = totalPods > 0 ? ((runningPods / totalPods) * 100).toFixed(1) : '100';

  // Group pods by prefix/workload
  const workloadMap: Record<string, number> = {};
  overview?.pods?.forEach((p) => {
    // extract workload prefix e.g. "oom-app-..." -> "oom-app"
    const parts = p.name.split('-');
    const workload = parts.length > 2 ? parts.slice(0, -2).join('-') : parts[0];
    workloadMap[workload] = (workloadMap[workload] || 0) + 1;
  });
  const workloadCount = Object.keys(workloadMap).length || 1;

  // Severity breakdown
  let critCount = 0;
  let highCount = 0;
  let medCount = 0;
  let lowCount = 0;
  incidents.forEach((inc) => {
    const sev = (inc.severity || '').toUpperCase();
    if (sev === 'CRITICAL') critCount++;
    else if (sev === 'HIGH') highCount++;
    else if (sev === 'MEDIUM') medCount++;
    else lowCount++;
  });

  return (
    <div className="flex flex-col w-full p-margin-desktop space-y-space-lg animate-in fade-in duration-200">
      {/* TOP WELCOME BANNER & DUAL RUNTIME STATUS */}
      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-space-md bg-surface-container-low p-space-md rounded-lg shadow-sm border border-surface-container-high/40">
        <div className="flex flex-col space-y-space-xs">
          <div className="flex items-center gap-space-sm">
            <span className="font-label-sm text-label-sm uppercase tracking-wider text-primary font-semibold">
              SRE Cockpit // Node Plane
            </span>
            <span className="inline-flex items-center gap-1 font-label-sm text-label-sm px-space-xs py-0.5 rounded bg-surface-container-high text-on-surface-variant font-code-block">
              <span className="w-1.5 h-1.5 rounded-full bg-tertiary-container animate-pulse" />
              Telemetry 60Hz
            </span>
          </div>
          <h1 className="font-headline-xl text-headline-xl text-on-surface font-semibold">
            Good morning, {operatorName}
          </h1>
          <p className="font-body-md text-body-md text-on-surface-variant max-w-3xl">
            Here's what's happening across your Kubernetes environment. Agentic diagnosis active on cluster{' '}
            <span className="text-primary font-code-block font-semibold">
              {overview?.cluster_name || 'k8s-agent-demo'}
            </span>
            .
          </p>
        </div>

        {/* Live Status Pills */}
        <div className="flex flex-wrap items-center gap-space-sm">
          <div className="flex items-center gap-space-xs px-space-md py-1.5 rounded bg-surface-container text-on-surface shadow-sm border border-surface-container-high/50">
            <span className="h-2 w-2 rounded-full bg-tertiary-container" />
            <span className="font-label-sm text-label-sm font-medium">Agent: MCP Online (7/7 Tools)</span>
          </div>
          <div className="flex items-center gap-space-xs px-space-md py-1.5 rounded bg-surface-container text-on-surface shadow-sm border border-surface-container-high/50">
            <span className="material-symbols-outlined text-[16px] text-primary">alt_route</span>
            <span className="font-label-sm text-label-sm font-medium">GitOps Sync: Protected</span>
          </div>
          <div className="flex items-center gap-space-xs px-space-md py-1.5 rounded bg-surface-container text-on-surface shadow-sm border border-surface-container-high/50">
            <span className="material-symbols-outlined text-[16px] text-tertiary">neurology</span>
            <span className="font-label-sm text-label-sm font-medium">{geminiModel}</span>
          </div>
        </div>
      </div>

      {/* METRIC CARDS ROW */}
      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-space-sm">
        {/* Total Pods */}
        <div className="bg-surface-container-low p-space-md rounded-lg shadow-sm border border-surface-container-high flex flex-col justify-between">
          <div className="flex items-center justify-between text-on-surface-variant">
            <span className="font-body-sm text-body-sm">Total Pods</span>
            <span className="material-symbols-outlined text-outline text-[18px]">layers</span>
          </div>
          <div className="mt-2 flex items-baseline gap-space-xs">
            <span className="font-headline-xl text-headline-xl text-on-surface font-bold">
              {totalPods}
            </span>
            <span className="font-label-sm text-label-sm text-outline font-mono">
              {workloadCount} Workloads
            </span>
          </div>
        </div>

        {/* Running Pods */}
        <div className="bg-surface-container-low p-space-md rounded-lg shadow-sm border border-surface-container-high flex flex-col justify-between">
          <div className="flex items-center justify-between text-on-surface-variant">
            <span className="font-body-sm text-body-sm">Running Pods</span>
            <span className="material-symbols-outlined text-tertiary text-[18px]">check_circle</span>
          </div>
          <div className="mt-2 flex items-baseline gap-space-xs">
            <span className="font-headline-xl text-headline-xl text-tertiary font-bold">
              {runningPods}
            </span>
            <span className="font-label-sm text-label-sm text-outline font-mono">
              {runningPct}% healthy
            </span>
          </div>
        </div>

        {/* Pending Pods */}
        <div className="bg-surface-container-low p-space-md rounded-lg shadow-sm border border-surface-container-high flex flex-col justify-between">
          <div className="flex items-center justify-between text-on-surface-variant">
            <span className="font-body-sm text-body-sm">Pending</span>
            <span className="material-symbols-outlined text-[#F59E0B] text-[18px]">hourglass_top</span>
          </div>
          <div className="mt-2 flex items-baseline gap-space-xs">
            <span className="font-headline-xl text-headline-xl text-[#F59E0B] font-bold">
              {pendingPods}
            </span>
            <span className="font-label-sm text-label-sm text-outline font-mono">Scheduling</span>
          </div>
        </div>

        {/* Failed / Error Pods */}
        <div className="bg-surface-container-low p-space-md rounded-lg shadow-sm border border-surface-container-high flex flex-col justify-between">
          <div className="flex items-center justify-between text-on-surface-variant">
            <span className="font-body-sm text-body-sm">Failed Pods</span>
            <span className="material-symbols-outlined text-error text-[18px]">error</span>
          </div>
          <div className="mt-2 flex items-baseline gap-space-xs">
            <span className="font-headline-xl text-headline-xl text-error font-bold">
              {failedPods}
            </span>
            <span className="font-label-sm text-label-sm text-outline font-mono">Terminal</span>
          </div>
        </div>

        {/* Total Restarts */}
        <div className="bg-surface-container-low p-space-md rounded-lg shadow-sm border border-surface-container-high flex flex-col justify-between">
          <div className="flex items-center justify-between text-on-surface-variant">
            <span className="font-body-sm text-body-sm">Total Restarts</span>
            <span className="material-symbols-outlined text-[#818CF8] text-[18px]">refresh</span>
          </div>
          <div className="mt-2 flex items-baseline gap-space-xs">
            <span className="font-headline-xl text-headline-xl text-on-surface font-bold">
              {totalRestarts}
            </span>
            <span className="font-label-sm text-label-sm text-outline font-mono">Lifetime</span>
          </div>
        </div>

        {/* Active Incidents */}
        <div className="bg-surface-container-low p-space-md rounded-lg shadow-sm border border-surface-container-high flex flex-col justify-between">
          <div className="flex items-center justify-between text-on-surface-variant">
            <span className="font-body-sm text-body-sm">Active Incidents</span>
            <span className="material-symbols-outlined text-error text-[18px]">warning</span>
          </div>
          <div className="mt-2 flex items-baseline gap-space-xs">
            <span className="font-headline-xl text-headline-xl text-error font-bold">
              {activeIncidentCount}
            </span>
            <span className="font-label-sm text-label-sm text-error font-mono">
              Action Required
            </span>
          </div>
        </div>
      </div>

      {/* CLUSTER HEALTH & SEVERITY DISTRIBUTION ROW */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-space-md">
        {/* Workload Distribution */}
        <div className="lg:col-span-7 bg-surface-container-low p-space-md rounded-lg shadow-sm border border-surface-container-high flex flex-col justify-between">
          <div className="flex items-center justify-between border-b border-surface-container-high pb-space-sm">
            <div className="flex items-center gap-space-xs">
              <span className="material-symbols-outlined text-primary text-[18px]">donut_large</span>
              <h2 className="font-headline-sm text-headline-sm text-on-surface font-semibold">
                Cluster Health & Workload Distribution
              </h2>
            </div>
            <span className="font-label-sm text-label-sm text-outline font-mono">
              {overview?.namespace || 'k8s-agent-demo'} • live node status
            </span>
          </div>

          <div className="py-space-md grid grid-cols-1 md:grid-cols-2 gap-space-md items-center">
            {/* Visual health bar graph */}
            <div className="space-y-3">
              <div>
                <div className="flex justify-between text-xs mb-1 font-mono">
                  <span className="text-on-surface">Running ({runningPods})</span>
                  <span className="text-tertiary">{runningPct}%</span>
                </div>
                <div className="w-full h-2 rounded bg-surface-container overflow-hidden">
                  <div
                    className="h-full bg-tertiary rounded transition-all duration-500"
                    style={{ width: `${runningPct}%` }}
                  />
                </div>
              </div>
              <div>
                <div className="flex justify-between text-xs mb-1 font-mono">
                  <span className="text-on-surface">Pending / Scheduling ({pendingPods})</span>
                  <span className="text-[#F59E0B]">
                    {totalPods > 0 ? ((pendingPods / totalPods) * 100).toFixed(1) : '0'}%
                  </span>
                </div>
                <div className="w-full h-2 rounded bg-surface-container overflow-hidden">
                  <div
                    className="h-full bg-[#F59E0B] rounded transition-all duration-500"
                    style={{
                      width: `${totalPods > 0 ? (pendingPods / totalPods) * 100 : 0}%`,
                    }}
                  />
                </div>
              </div>
              <div>
                <div className="flex justify-between text-xs mb-1 font-mono">
                  <span className="text-on-surface">Failed / Crashing ({failedPods})</span>
                  <span className="text-error">
                    {totalPods > 0 ? ((failedPods / totalPods) * 100).toFixed(1) : '0'}%
                  </span>
                </div>
                <div className="w-full h-2 rounded bg-surface-container overflow-hidden">
                  <div
                    className="h-full bg-error rounded transition-all duration-500"
                    style={{
                      width: `${totalPods > 0 ? (failedPods / totalPods) * 100 : 0}%`,
                    }}
                  />
                </div>
              </div>
            </div>

            {/* Quick stats panel */}
            <div className="p-space-sm bg-surface-container rounded-lg border border-surface-container-high space-y-2">
              <span className="font-label-sm text-label-sm uppercase tracking-wider text-outline block">
                Workload Breakdown
              </span>
              <div className="max-h-32 overflow-y-auto space-y-1 font-mono text-xs">
                {Object.entries(workloadMap).slice(0, 6).map(([workload, count]) => (
                  <div key={workload} className="flex justify-between items-center text-on-surface-variant">
                    <span className="truncate pr-2">{workload}</span>
                    <span className="px-1.5 py-0.5 rounded bg-surface-container-high text-primary font-bold">
                      {count} pod{count > 1 ? 's' : ''}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* Severity & Incident Breakdown */}
        <div className="lg:col-span-5 bg-surface-container-low p-space-md rounded-lg shadow-sm border border-surface-container-high flex flex-col justify-between">
          <div className="flex items-center justify-between border-b border-surface-container-high pb-space-sm">
            <div className="flex items-center gap-space-xs">
              <span className="material-symbols-outlined text-error text-[18px]">bar_chart</span>
              <h2 className="font-headline-sm text-headline-sm text-on-surface font-semibold">
                Severity & Diagnosis
              </h2>
            </div>
            <button
              onClick={onViewAllIncidents}
              className="font-label-sm text-label-sm text-primary hover:underline font-mono"
            >
              View All ({activeIncidentCount})
            </button>
          </div>

          <div className="py-space-md space-y-3">
            <div className="flex items-center justify-between p-2 rounded bg-surface-container border border-error/20">
              <div className="flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-error" />
                <span className="font-label-md text-label-md text-on-surface">Critical Severity</span>
              </div>
              <span className="font-mono text-sm font-bold text-error">{critCount}</span>
            </div>
            <div className="flex items-center justify-between p-2 rounded bg-surface-container border border-[#F97316]/20">
              <div className="flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-[#F97316]" />
                <span className="font-label-md text-label-md text-on-surface">High Severity</span>
              </div>
              <span className="font-mono text-sm font-bold text-[#F97316]">{highCount}</span>
            </div>
            <div className="flex items-center justify-between p-2 rounded bg-surface-container border border-[#F59E0B]/20">
              <div className="flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-[#F59E0B]" />
                <span className="font-label-md text-label-md text-on-surface">Medium Severity</span>
              </div>
              <span className="font-mono text-sm font-bold text-[#F59E0B]">{medCount}</span>
            </div>
            <div className="flex items-center justify-between p-2 rounded bg-surface-container border border-primary/20">
              <div className="flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-primary" />
                <span className="font-label-md text-label-md text-on-surface">Low / Warning</span>
              </div>
              <span className="font-mono text-sm font-bold text-primary">{lowCount}</span>
            </div>
          </div>
        </div>
      </div>

      {/* ACTIVE INCIDENTS & LIVE TELEMETRY TABLE */}
      <div className="flex flex-col space-y-space-md">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-space-xs">
            <span className="material-symbols-outlined text-primary text-[20px]">dynamic_feed</span>
            <h2 className="font-headline-lg text-headline-lg text-on-surface font-semibold">
              Active Incidents & Live Telemetry
            </h2>
            <span className="font-label-sm text-label-sm text-on-surface-variant ml-2 font-mono">
              {activeIncidentCount} unresolved cluster alerts
            </span>
          </div>
          <button
            onClick={onOpenNewInvestigation}
            className="flex items-center gap-1 font-headline-sm text-headline-sm px-3 py-1.5 rounded bg-primary-container text-on-primary-container font-semibold hover:bg-primary transition-colors"
          >
            <span className="material-symbols-outlined text-[16px]">play_arrow</span>
            <span>Batch Investigate</span>
          </button>
        </div>

        <div className="bg-surface-container-low rounded-lg shadow-sm border border-surface-container-high overflow-hidden">
          <div className="overflow-x-auto w-full">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-surface-container-high bg-surface-container text-outline font-label-sm text-[11px] uppercase tracking-wider font-mono">
                  <th className="py-2.5 px-space-md">Severity</th>
                  <th className="py-2.5 px-space-md">Workload Target</th>
                  <th className="py-2.5 px-space-md">Failure Diagnosis</th>
                  <th className="py-2.5 px-space-md">Root Cause Summary</th>
                  <th className="py-2.5 px-space-md">Confidence</th>
                  <th className="py-2.5 px-space-md text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-surface-container-high text-xs">
                {incidents.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="py-8 text-center text-on-surface-variant font-mono">
                      No active incidents detected. All workloads healthy.
                    </td>
                  </tr>
                ) : (
                  incidents.slice(0, 8).map((inc, i) => {
                    const podName = inc.pod_name || inc.pod || `workload-${i}`;
                    const sev = (inc.severity || 'HIGH').toUpperCase();

                    let sevBadge = 'text-primary bg-primary/10 border-primary/30';
                    if (sev === 'CRITICAL') sevBadge = 'text-error bg-error/15 border-error/40';
                    else if (sev === 'HIGH') sevBadge = 'text-[#F97316] bg-[#F97316]/15 border-[#F97316]/40';
                    else if (sev === 'MEDIUM') sevBadge = 'text-[#F59E0B] bg-[#F59E0B]/15 border-[#F59E0B]/40';

                    return (
                      <tr
                        key={i}
                        className="hover:bg-surface-container transition-colors group cursor-pointer"
                      >
                        <td className="py-2.5 px-space-md whitespace-nowrap">
                          <span
                            className={`inline-block px-2 py-0.5 rounded font-mono text-[11px] font-bold border ${sevBadge}`}
                          >
                            {sev}
                          </span>
                        </td>
                        <td className="py-2.5 px-space-md font-mono text-on-surface font-semibold whitespace-nowrap">
                          {podName}
                        </td>
                        <td className="py-2.5 px-space-md font-mono text-primary font-medium whitespace-nowrap">
                          {inc.failure_type}
                        </td>
                        <td className="py-2.5 px-space-md text-on-surface-variant max-w-md truncate">
                          {inc.root_cause || inc.evidence_summary || 'Root-cause evidence collected.'}
                        </td>
                        <td className="py-2.5 px-space-md font-mono text-on-surface-variant whitespace-nowrap">
                          <span className="px-1.5 py-0.5 rounded bg-surface-container text-tertiary text-[11px]">
                            {inc.confidence || 'HIGH'}
                          </span>
                        </td>
                        <td className="py-2.5 px-space-md text-right whitespace-nowrap">
                          <button
                            onClick={() => onInvestigatePod(podName)}
                            className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-surface-container hover:bg-primary-container hover:text-on-primary-container text-on-surface font-mono text-[11px] transition-colors"
                          >
                            <span className="material-symbols-outlined text-[14px]">psychology</span>
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
    </div>
  );
};
