import React, { useState } from 'react';

interface HeaderProps {
  namespace: string;
  onSelectNamespace: (ns: string) => void;
  availableNamespaces: string[];
  geminiModel: string;
  isRefreshing: boolean;
  onRefresh: () => void;
  onOpenNewInvestigation: () => void;
  lastUpdatedSeconds: number;
  incidentCount?: number;
  onOpenIncidents?: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  namespace,
  onSelectNamespace,
  availableNamespaces,
  geminiModel,
  isRefreshing,
  onRefresh,
  onOpenNewInvestigation,
  lastUpdatedSeconds,
  incidentCount = 0,
  onOpenIncidents,
}) => {
  const [nsOpen, setNsOpen] = useState(false);
  const [notifOpen, setNotifOpen] = useState(false);
  return (
    <header className="fixed top-0 left-60 right-0 h-14 bg-surface-container-lowest/90 backdrop-blur-md z-40 px-gutter-desktop flex items-center justify-between shadow-[0_1px_8px_rgba(0,0,0,0.3)] border-b border-surface-container-high/30 select-none">
      {/* Left: Cluster & Namespace Breadcrumb */}
      <div className="flex items-center gap-space-sm">
        <div className="flex items-center gap-space-xs text-on-surface-variant">
          <span className="material-symbols-outlined text-[18px]">hub</span>
          <span className="font-label-md text-label-md text-on-surface-variant">Clusters</span>
          <span className="text-outline">/</span>
          <span className="font-label-md text-label-md text-on-surface-variant">production</span>
          <span className="text-outline">/</span>
        </div>
        <span className="font-code-block text-code-block px-space-xs py-0.5 rounded bg-surface-container text-primary font-medium">
          {namespace}
        </span>
      </div>

      {/* Right Controls */}
      <div className="flex items-center gap-space-md">
        {/* Namespace Selector Dropdown */}
        <div className="relative">
          <button
            type="button"
            onClick={() => {
              setNsOpen((v) => !v);
              setNotifOpen(false);
            }}
            className="flex items-center gap-space-xs px-space-sm py-1 bg-surface-container rounded cursor-pointer hover:bg-surface-container-high transition-colors"
          >
            <span className="font-label-sm text-label-sm text-outline">NS:</span>
            <span className="font-code-block text-code-block text-on-surface font-semibold">
              {namespace}
            </span>
            <span className="material-symbols-outlined text-[16px] text-on-surface-variant">
              expand_more
            </span>
          </button>
          {nsOpen && (
            <div className="absolute right-0 top-full mt-1 w-48 bg-surface-container-high border border-outline-variant rounded shadow-xl py-1 z-50">
              {availableNamespaces.map((ns) => (
                <button
                  key={ns}
                  type="button"
                  onClick={() => {
                    onSelectNamespace(ns);
                    setNsOpen(false);
                  }}
                  className={`w-full text-left px-3 py-1.5 text-xs font-mono transition-colors ${
                    ns === namespace
                      ? 'text-primary bg-surface-container-highest font-bold'
                      : 'text-on-surface hover:bg-surface-container'
                  }`}
                >
                  {ns}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Telemetry freshness & refresh */}
        <div className="flex items-center gap-space-xs text-on-surface-variant">
          <span className="h-2 w-2 rounded-full bg-tertiary-container animate-ping" />
          <span className="font-label-sm text-label-sm text-on-surface-variant">
            Updated {lastUpdatedSeconds}s ago
          </span>
          <button
            onClick={onRefresh}
            className={`p-1 text-on-surface-variant hover:text-on-surface rounded hover:bg-surface-container transition-colors ${
              isRefreshing ? 'animate-spin text-primary' : ''
            }`}
            title="Refresh Telemetry"
            type="button"
          >
            <span className="material-symbols-outlined text-[16px]">sync</span>
          </button>
        </div>

        {/* Gemini Model Badge */}
        <div className="flex items-center gap-space-xs px-space-sm py-1 rounded-full bg-surface-container text-primary">
          <span className="h-2 w-2 rounded-full bg-primary-container" />
          <span className="font-label-sm text-label-sm font-medium">
            {geminiModel} • Online
          </span>
        </div>

        {/* Notifications */}
        <div className="relative">
          <button
            onClick={() => {
              setNotifOpen((v) => !v);
              setNsOpen(false);
            }}
            className="relative p-1.5 text-on-surface-variant hover:text-on-surface rounded hover:bg-surface-container transition-colors"
            type="button"
            title="Cluster Notifications"
          >
            <span className="material-symbols-outlined text-[20px]">notifications</span>
            {incidentCount > 0 && (
              <span className="absolute top-1 right-1 h-2 w-2 rounded-full bg-error" />
            )}
          </button>
          {notifOpen && (
            <div className="absolute right-0 top-full mt-1 w-64 bg-surface-container-high border border-outline-variant rounded-lg shadow-xl p-3 z-50 space-y-2 font-mono text-xs">
              <div className="font-bold text-on-surface flex items-center justify-between">
                <span>Cluster Alerts</span>
                <span className="text-error">{incidentCount} active</span>
              </div>
              <p className="text-on-surface-variant text-[11px]">
                {incidentCount > 0
                  ? `${incidentCount} incident(s) detected in namespace '${namespace}'.`
                  : `All workloads healthy in '${namespace}'.`}
              </p>
              {onOpenIncidents && (
                <button
                  type="button"
                  onClick={() => {
                    onOpenIncidents();
                    setNotifOpen(false);
                  }}
                  className="w-full py-1.5 rounded bg-primary-container text-on-primary-container font-semibold hover:bg-primary transition-colors text-[11px]"
                >
                  View Incidents
                </button>
              )}
            </div>
          )}
        </div>

        {/* Primary Action: New Investigation */}
        <button
          onClick={onOpenNewInvestigation}
          className="flex items-center gap-space-xs px-space-sm py-1.5 bg-primary-container text-on-primary-container font-headline-sm text-headline-sm rounded hover:bg-primary transition-colors shadow-sm font-semibold active:scale-95"
          type="button"
        >
          <span className="material-symbols-outlined text-[18px]">add</span>
          <span>New Investigation</span>
        </button>
      </div>
    </header>
  );
};
