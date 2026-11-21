import React from 'react';

export type NavTab =
  | 'overview'
  | 'incidents'
  | 'investigations'
  | 'remediations'
  | 'verification'
  | 'observability'
  | 'resources'
  | 'settings';

interface SidebarProps {
  activeTab: NavTab;
  onSelectTab: (tab: NavTab) => void;
  incidentCount: number;
  remediationCount: number;
  clusterConnected: boolean;
}

export const Sidebar: React.FC<SidebarProps> = ({
  activeTab,
  onSelectTab,
  incidentCount,
  remediationCount,
  clusterConnected,
}) => {
  const EMBLEM_URL =
    'https://lh3.googleusercontent.com/aida/ALJJyrBxbx6bhuAVcfoPObd9_nkQgU19q4sVtwCPyo7Np0cJWfQbEdGjNDyZQy_0Lc81h85Z5BrY9Z6SZ898AMS7uJ0lSmIisSvmZTRGGUFCjoMOhPSpwziF5OHl4RE9SDxNxvJ4GI07md8ETPWytkgDdJYWs-_SH8sEUcdqBvpSE_jlt3h1_BmWFeAotGsTGN5l3VJWpQYLSNCO0Hmvr5IN1T-bZGgWZpQS1W71cRKPRqZU_w';

  const navItems: { id: NavTab; label: string; icon: string; badge?: number }[] = [
    { id: 'overview', label: 'Overview', icon: 'dashboard' },
    { id: 'incidents', label: 'Incidents', icon: 'warning', badge: incidentCount },
    { id: 'investigations', label: 'Investigations', icon: 'explore' },
    { id: 'remediations', label: 'Remediation', icon: 'build', badge: remediationCount },
    { id: 'verification', label: 'Verification', icon: 'verified_user' },
  ];

  const systemItems: { id: NavTab; label: string; icon: string }[] = [
    { id: 'observability', label: 'Agent Observability', icon: 'memory' },
    { id: 'resources', label: 'Cluster Resources', icon: 'layers' },
    { id: 'settings', label: 'Settings', icon: 'settings' },
  ];

  return (
    <aside className="fixed left-0 top-0 h-full w-60 bg-surface-container-lowest z-50 flex flex-col justify-between shadow-[0_1px_8px_rgba(0,0,0,0.25)] border-r border-surface-container-high/30 select-none">
      <div className="flex flex-col">
        {/* Brand Header */}
        <div className="h-14 px-gutter flex items-center justify-between bg-surface-container-low border-b border-surface-container-high/20">
          <div className="flex items-center gap-space-xs">
            <img
              alt="KafaKube8 Emblem"
              className="h-8 w-auto object-contain rounded"
              src={EMBLEM_URL}
              onError={(e) => {
                // Fallback icon if offline
                (e.currentTarget as HTMLElement).style.display = 'none';
              }}
            />
            <div className="flex flex-col leading-none">
              <span className="font-bold text-slate-100 tracking-tight text-[14px]">
                KafaKube<span className="text-primary-container font-extrabold">8</span>
              </span>
              <span className="font-label-sm text-label-sm text-on-surface-variant font-medium tracking-wide uppercase">
                AGENTIC K8s OPS
              </span>
            </div>
          </div>
          <div
            className={`h-2.5 w-2.5 rounded-full ${
              clusterConnected ? 'bg-tertiary-container animate-pulse' : 'bg-error animate-ping'
            }`}
            title={clusterConnected ? 'Cluster Connected' : 'Cluster Disconnected'}
          />
        </div>

        {/* Subtitle / Philosophy */}
        <div className="px-gutter pt-space-xs pb-1.5 flex flex-col space-y-0.5 border-b border-surface-container-high/40">
          <span className="font-body-sm text-[11px] text-on-surface-variant font-medium leading-none">
            Intelligence in Every Cluster
          </span>
          <span className="font-label-sm text-[11px] text-outline font-mono leading-none tracking-tight">
            Observe • Diagnose • Remediate • Verify
          </span>
        </div>

        {/* Cluster Switcher */}
        <div className="p-gutter">
          <div className="flex items-center justify-between px-space-sm py-space-xs bg-surface-container rounded-lg cursor-pointer hover:bg-surface-container-high transition-colors">
            <div className="flex items-center gap-space-xs truncate">
              <span
                className={`h-2 w-2 rounded-full shrink-0 ${
                  clusterConnected ? 'bg-tertiary-container' : 'bg-error'
                }`}
              />
              <span className="font-code-block text-code-block text-on-surface truncate">
                Local K8s (Prod)
              </span>
            </div>
            <span className="material-symbols-outlined text-[16px] text-on-surface-variant">
              unfold_more
            </span>
          </div>
        </div>

        {/* Main Operations Section */}
        <div className="px-gutter mb-space-xs">
          <span className="font-label-sm text-label-sm uppercase tracking-wider text-outline font-semibold">
            Main Operations
          </span>
        </div>
        <nav className="flex flex-col gap-space-xs px-gutter mb-space-md">
          {navItems.map((item) => {
            const isActive = activeTab === item.id;
            return (
              <button
                key={item.id}
                onClick={() => onSelectTab(item.id)}
                className={`flex items-center justify-between px-space-sm py-1.5 rounded-lg transition-colors w-full text-left ${
                  isActive
                    ? 'bg-surface-container-high text-primary font-semibold shadow-sm'
                    : 'text-on-surface-variant hover:bg-surface-container hover:text-on-surface'
                }`}
              >
                <div className="flex items-center gap-space-xs">
                  <span className="material-symbols-outlined text-[18px]">{item.icon}</span>
                  <span className="font-body-md text-body-md">{item.label}</span>
                </div>
                {item.badge !== undefined && item.badge > 0 && (
                  <span
                    className={`font-label-sm text-[10px] px-1.5 py-0.5 rounded-full font-bold ${
                      item.id === 'incidents'
                        ? 'bg-error-container text-on-error-container'
                        : 'bg-primary-container text-on-primary-container'
                    }`}
                  >
                    {item.badge}
                  </span>
                )}
              </button>
            );
          })}
        </nav>

        {/* System & Telemetry Section */}
        <div className="px-gutter mb-space-xs">
          <span className="font-label-sm text-label-sm uppercase tracking-wider text-outline font-semibold">
            System & Telemetry
          </span>
        </div>
        <nav className="flex flex-col gap-space-xs px-gutter">
          {systemItems.map((item) => {
            const isActive = activeTab === item.id;
            return (
              <button
                key={item.id}
                onClick={() => onSelectTab(item.id)}
                className={`flex items-center gap-space-xs px-space-sm py-1.5 rounded-lg transition-colors w-full text-left ${
                  isActive
                    ? 'bg-surface-container-high text-primary font-semibold shadow-sm'
                    : 'text-on-surface-variant hover:bg-surface-container hover:text-on-surface'
                }`}
              >
                <span className="material-symbols-outlined text-[18px]">{item.icon}</span>
                <span className="font-body-md text-body-md">{item.label}</span>
              </button>
            );
          })}
        </nav>
      </div>

      {/* Operator User Card */}
      <div className="p-gutter border-t border-surface-container-high/40 bg-surface-container-lowest">
        <div className="flex items-center justify-between p-space-xs rounded-lg hover:bg-surface-container transition-colors cursor-pointer">
          <div className="flex items-center gap-space-xs">
            <div className="w-8 h-8 rounded-full bg-surface-container-high flex items-center justify-center font-headline-sm text-headline-sm text-primary font-bold">
              OP
            </div>
            <div className="flex flex-col text-left">
              <span className="font-headline-sm text-headline-sm text-on-surface truncate">
                Alex Vance
              </span>
              <span className="font-label-sm text-label-sm text-on-surface-variant">
                Cluster Admin
              </span>
            </div>
          </div>
          <div className="w-7 h-7 rounded-full bg-primary flex items-center justify-center shrink-0">
            <span className="material-symbols-outlined text-on-primary text-[16px]">
              person
            </span>
          </div>
        </div>
      </div>
    </aside>
  );
};
