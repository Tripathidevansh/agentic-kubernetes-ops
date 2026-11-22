import React, { useState } from 'react';

export interface OperatorProfile {
  id: string;
  name: string;
  role: 'Cluster Admin' | 'Guest';
  initials: string;
  isGuest: boolean;
}

export const OPERATOR_PROFILES: OperatorProfile[] = [
  { id: 'devansh', name: 'Devansh', role: 'Cluster Admin', initials: 'DV', isGuest: false },
  { id: 'saksham', name: 'Saksham', role: 'Cluster Admin', initials: 'SK', isGuest: false },
  { id: 'samyak', name: 'Samyak', role: 'Cluster Admin', initials: 'SM', isGuest: false },
  { id: 'guest', name: 'Guest', role: 'Guest', initials: 'GU', isGuest: true },
];

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
  activeOperator: OperatorProfile;
  onSelectOperator: (op: OperatorProfile) => void;
}

export const Sidebar: React.FC<SidebarProps> = ({
  activeTab,
  onSelectTab,
  incidentCount,
  remediationCount,
  clusterConnected,
  activeOperator,
  onSelectOperator,
}) => {
  const [profileOpen, setProfileOpen] = useState(false);
  const EMBLEM_URL = '/logo.png';

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

        {/* Cluster Status Badge */}
        <div className="p-gutter">
          <div className="flex items-center justify-between px-space-sm py-space-xs bg-surface-container rounded-lg">
            <div className="flex items-center gap-space-xs truncate">
              <span
                className={`h-2 w-2 rounded-full shrink-0 ${
                  clusterConnected ? 'bg-tertiary-container' : 'bg-error'
                }`}
              />
              <span className="font-code-block text-code-block text-on-surface truncate">
                Local K8s ({clusterConnected ? 'Online' : 'Offline'})
              </span>
            </div>
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

      {/* Operator User Card with Dropdown */}
      <div className="relative p-gutter border-t border-surface-container-high/40 bg-surface-container-lowest">
        {profileOpen && (
          <div className="absolute left-3 right-3 bottom-full mb-2 bg-surface-container-high border border-outline-variant rounded-lg shadow-2xl py-1 z-50">
            <div className="px-3 py-1 text-[10px] font-mono uppercase tracking-wider text-outline border-b border-surface-container">
              Switch Operator Profile
            </div>
            {OPERATOR_PROFILES.map((profile) => {
              const isSelected = profile.id === activeOperator.id;
              return (
                <button
                  key={profile.id}
                  type="button"
                  onClick={() => {
                    onSelectOperator(profile);
                    setProfileOpen(false);
                  }}
                  className={`w-full flex items-center justify-between px-3 py-2 text-left transition-colors ${
                    isSelected
                      ? 'bg-surface-container-highest text-primary'
                      : 'text-on-surface hover:bg-surface-container'
                  }`}
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="w-6 h-6 rounded-full bg-surface-container flex items-center justify-center text-[10px] font-mono font-bold text-primary shrink-0">
                      {profile.initials}
                    </span>
                    <div className="flex flex-col leading-tight truncate">
                      <span className="text-xs font-semibold truncate">{profile.name}</span>
                      <span className="text-[10px] font-mono text-on-surface-variant">
                        {profile.role}
                      </span>
                    </div>
                  </div>
                  {isSelected && (
                    <span className="material-symbols-outlined text-[16px] text-primary">check</span>
                  )}
                </button>
              );
            })}
          </div>
        )}

        <button
          type="button"
          onClick={() => setProfileOpen((v) => !v)}
          className="w-full flex items-center justify-between p-space-xs rounded-lg hover:bg-surface-container transition-colors cursor-pointer"
        >
          <div className="flex items-center gap-space-xs min-w-0">
            <div className="w-8 h-8 rounded-full bg-surface-container-high flex items-center justify-center font-headline-sm text-headline-sm text-primary font-bold shrink-0">
              {activeOperator.initials}
            </div>
            <div className="flex flex-col text-left truncate">
              <span className="font-headline-sm text-headline-sm text-on-surface truncate">
                {activeOperator.name}
              </span>
              <span className="font-label-sm text-label-sm text-on-surface-variant">
                {activeOperator.role}
              </span>
            </div>
          </div>
          <div className="w-7 h-7 rounded-full bg-primary flex items-center justify-center shrink-0">
            <span className="material-symbols-outlined text-on-primary text-[16px]">
              {profileOpen ? 'expand_more' : 'unfold_more'}
            </span>
          </div>
        </button>
      </div>
    </aside>
  );
};
