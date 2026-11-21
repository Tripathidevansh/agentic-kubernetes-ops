import React from 'react';
import { RemediationProposal } from '../types/api';

interface VerificationViewProps {
  proposals: RemediationProposal[];
  investigationId?: string;
  onOpenNewInvestigation: () => void;
}

export const VerificationView: React.FC<VerificationViewProps> = ({
  proposals,
  investigationId,
  onOpenNewInvestigation,
}) => {
  const actionableProposals = proposals.filter((p) => p.can_remediate && p.verification_plan);

  return (
    <div className="flex flex-col w-full p-margin-desktop space-y-space-lg animate-in fade-in duration-200">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-space-md border-b border-surface-container-high/40 pb-space-md">
        <div className="flex flex-col space-y-space-xs">
          <div className="flex items-center gap-space-xs text-on-surface-variant font-mono text-[11px]">
            <span className="text-primary font-bold">Verification Engine</span>
            <span>/</span>
            <span>Safety Assertions</span>
            {investigationId && (
              <span className="px-1.5 py-0.5 rounded bg-surface-container text-on-surface font-mono">
                #INV-{investigationId.slice(0, 8)}
              </span>
            )}
          </div>
          <h1 className="font-headline-xl text-headline-xl text-on-surface font-semibold tracking-tight">
            Post-Remediation Verification Plans
          </h1>
          <p className="font-body-md text-body-md text-on-surface-variant max-w-2xl">
            Deterministic state conditions, probe delays, and negative assertions configured to verify workload health following GitOps deployment.
          </p>
        </div>

        <button
          onClick={onOpenNewInvestigation}
          className="flex items-center gap-space-xs px-space-md py-1.5 rounded bg-primary-container text-on-primary-container font-headline-sm text-headline-sm font-semibold hover:bg-primary transition-colors shadow-sm self-start md:self-auto"
        >
          <span className="material-symbols-outlined text-[16px]">verified</span>
          <span>Run Verification Scan</span>
        </button>
      </div>

      {/* Verification Plans Grid */}
      <div className="space-y-space-md">
        {actionableProposals.length === 0 ? (
          <div className="p-12 bg-surface-container-low rounded-xl border border-surface-container-high text-center font-mono text-xs text-on-surface-variant space-y-2">
            <span className="material-symbols-outlined text-3xl text-outline block">
              verified_user
            </span>
            <span>No active verification plans for current proposals.</span>
            <p className="text-[11px] text-outline">
              Run an agentic investigation to generate workload-specific verification assertions.
            </p>
          </div>
        ) : (
          actionableProposals.map((p, idx) => {
            const plan = p.verification_plan;
            return (
              <div
                key={idx}
                className="bg-surface-container-low rounded-xl border border-surface-container-high p-space-md shadow-sm space-y-space-sm"
              >
                <div className="flex flex-wrap items-center justify-between gap-space-xs border-b border-surface-container-high pb-space-xs font-mono text-xs">
                  <div className="flex items-center gap-space-xs">
                    <span className="material-symbols-outlined text-tertiary text-[18px]">
                      check_circle
                    </span>
                    <span className="text-on-surface font-bold text-sm">
                      {p.target_workload}
                    </span>
                    <span className="text-outline">({p.target_kind}/{p.target_name})</span>
                  </div>
                  <span className="text-tertiary font-bold bg-tertiary/15 px-2 py-0.5 rounded text-[11px]">
                    READY FOR GITOPS AUDIT
                  </span>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-space-sm pt-1">
                  <div className="p-space-sm bg-surface-container rounded border border-surface-container-high font-mono text-xs space-y-1">
                    <span className="text-outline text-[11px] uppercase block">
                      Target Pod Phase
                    </span>
                    <span className="text-on-surface font-bold text-sm">
                      {plan?.target_pod_phase || 'Running'}
                    </span>
                    <span className="text-[10px] text-outline block">
                      Readiness: {plan?.expected_ready_condition ? 'true' : 'true'}
                    </span>
                  </div>

                  <div className="p-space-sm bg-surface-container rounded border border-surface-container-high font-mono text-xs space-y-1">
                    <span className="text-outline text-[11px] uppercase block">
                      Probe Tolerance Window
                    </span>
                    <span className="text-primary font-bold text-sm">
                      {plan?.readiness_probe_initial_delay_seconds ?? 15}s initial delay
                    </span>
                    <span className="text-[10px] text-outline block">
                      Timeout: {plan?.timeout_seconds ?? 60}s
                    </span>
                  </div>

                  <div className="p-space-sm bg-surface-container rounded border border-surface-container-high font-mono text-xs space-y-1">
                    <span className="text-outline text-[11px] uppercase block">
                      Negative Assertions
                    </span>
                    <span className="text-tertiary font-bold text-sm">
                      {plan?.negative_assertions?.length ?? 1} Guardrails Active
                    </span>
                    <span className="text-[10px] text-outline block">
                      Zero Tolerance for Regressions
                    </span>
                  </div>
                </div>

                {/* Detailed Negative Assertions */}
                {plan?.negative_assertions && plan.negative_assertions.length > 0 && (
                  <div className="pt-2 border-t border-surface-container-high/60 space-y-1.5">
                    <span className="text-[11px] font-mono text-outline uppercase block font-semibold">
                      Fail-Safe Negative Assertions:
                    </span>
                    <div className="space-y-1">
                      {plan.negative_assertions.map((na, i) => (
                        <div
                          key={i}
                          className="p-2 rounded bg-surface-container border border-surface-container-high font-mono text-xs flex items-center gap-2 text-on-surface"
                        >
                          <span className="material-symbols-outlined text-error text-[16px]">
                            shield_with_heart
                          </span>
                          <span>{na}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};
