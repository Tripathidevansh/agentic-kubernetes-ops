import React, { useState } from 'react';
import { DiffViewer } from '../components/DiffViewer';
import { RemediationProposal } from '../types/api';

interface RemediationsViewProps {
  proposals: RemediationProposal[];
  onOpenApproveModal: (proposal: RemediationProposal) => void;
  onOpenRejectModal: (proposal: RemediationProposal) => void;
  onOpenNewInvestigation: () => void;
}

export const RemediationsView: React.FC<RemediationsViewProps> = ({
  proposals,
  onOpenApproveModal,
  onOpenRejectModal,
  onOpenNewInvestigation,
}) => {
  const [statusFilter, setStatusFilter] = useState<'ALL' | 'PENDING' | 'APPROVED' | 'REJECTED'>('ALL');
  const [selectedProposalId, setSelectedProposalId] = useState<string | null>(
    proposals.length > 0 ? proposals[0].proposal_id : null
  );

  const filteredProposals = proposals.filter((p) => {
    if (statusFilter === 'ALL') return true;
    const approvalStatus = p.approval?.status || 'PENDING_APPROVAL';
    if (statusFilter === 'PENDING') return approvalStatus === 'PENDING_APPROVAL';
    if (statusFilter === 'APPROVED') return approvalStatus === 'APPROVED';
    if (statusFilter === 'REJECTED') return approvalStatus === 'REJECTED';
    return true;
  });

  const activeProposal =
    proposals.find((p) => p.proposal_id === selectedProposalId) || filteredProposals[0];

  return (
    <div className="flex flex-col w-full p-margin-desktop space-y-space-lg animate-in fade-in duration-200">
      {/* Top Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-space-md border-b border-surface-container-high/40 pb-space-md">
        <div className="flex flex-col space-y-space-xs">
          <div className="flex items-center gap-space-xs text-on-surface-variant font-mono text-[11px]">
            <span className="text-primary font-bold">Remediation Center</span>
            <span>/</span>
            <span>GitOps Staging</span>
            <span className="px-1.5 py-0.5 rounded bg-surface-container text-tertiary ml-2">
              Human-in-the-Loop Sign-Off
            </span>
          </div>
          <h1 className="font-headline-xl text-headline-xl text-on-surface font-semibold tracking-tight">
            Remediation Proposals
          </h1>
          <p className="font-body-md text-body-md text-on-surface-variant max-w-2xl">
            Review agent-formulated manifest patches. Human approval is strictly required prior to staging changes for GitOps PR integration.
          </p>
        </div>

        <button
          onClick={onOpenNewInvestigation}
          className="flex items-center gap-space-xs px-space-md py-1.5 rounded bg-primary-container text-on-primary-container font-headline-sm text-headline-sm font-semibold hover:bg-primary transition-colors shadow-sm self-start md:self-auto"
        >
          <span className="material-symbols-outlined text-[16px]">psychology</span>
          <span>Generate New Proposals</span>
        </button>
      </div>

      {/* Filter Chips */}
      <div className="flex items-center gap-2 font-mono text-xs overflow-x-auto pb-1">
        {(['ALL', 'PENDING', 'APPROVED', 'REJECTED'] as const).map((filter) => (
          <button
            key={filter}
            onClick={() => setStatusFilter(filter)}
            className={`px-3 py-1 rounded transition-colors ${
              statusFilter === filter
                ? 'bg-primary-container text-on-primary-container font-bold'
                : 'bg-surface-container text-on-surface-variant hover:text-on-surface'
            }`}
          >
            {filter} ({filter === 'ALL' ? proposals.length : proposals.filter((p) => {
              const s = p.approval?.status || 'PENDING_APPROVAL';
              return filter === 'PENDING' ? s === 'PENDING_APPROVAL' : s === filter;
            }).length})
          </button>
        ))}
      </div>

      {/* Main Split: List + Diff / Actions */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-space-md items-start">
        {/* Proposal List */}
        <div className="lg:col-span-5 bg-surface-container-low rounded-xl border border-surface-container-high overflow-hidden shadow-sm space-y-0">
          <div className="p-space-sm bg-surface-container border-b border-surface-container-high flex items-center justify-between font-mono text-xs">
            <span className="text-on-surface font-bold uppercase tracking-wider">
              Workload Proposals
            </span>
            <span className="text-outline">{filteredProposals.length} available</span>
          </div>

          <div className="divide-y divide-surface-container-high max-h-[600px] overflow-y-auto">
            {filteredProposals.length === 0 ? (
              <div className="p-8 text-center text-on-surface-variant font-mono text-xs">
                No remediation proposals found for selected filter.
              </div>
            ) : (
              filteredProposals.map((p) => {
                const isSelected = activeProposal?.proposal_id === p.proposal_id;
                const approvalStatus = p.approval?.status || 'PENDING_APPROVAL';

                return (
                  <div
                    key={p.proposal_id}
                    onClick={() => setSelectedProposalId(p.proposal_id)}
                    className={`p-space-sm cursor-pointer transition-colors space-y-1.5 ${
                      isSelected ? 'bg-surface-container-high' : 'hover:bg-surface-container'
                    }`}
                  >
                    <div className="flex items-center justify-between font-mono text-xs">
                      <span className="font-bold text-on-surface truncate">{p.target_workload}</span>
                      <span
                        className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                          approvalStatus === 'APPROVED'
                            ? 'bg-tertiary/20 text-tertiary'
                            : approvalStatus === 'REJECTED'
                            ? 'bg-error/20 text-error'
                            : 'bg-[#F59E0B]/20 text-[#F59E0B]'
                        }`}
                      >
                        {approvalStatus.replace('_', ' ')}
                      </span>
                    </div>

                    <p className="text-xs text-on-surface-variant line-clamp-2">{p.summary}</p>

                    <div className="flex items-center justify-between font-mono text-[11px] text-outline pt-1">
                      <span>Kind: {p.target_kind}</span>
                      <span
                        className={`font-semibold ${
                          p.risk_level === 'CRITICAL' || p.risk_level === 'HIGH'
                            ? 'text-error'
                            : 'text-tertiary'
                        }`}
                      >
                        Risk: {p.risk_level}
                      </span>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* Selected Proposal Inspection & Diff */}
        <div className="lg:col-span-7 space-y-space-md">
          {activeProposal ? (
            <>
              {/* Proposal Header Card */}
              <div className="bg-surface-container-low p-space-md rounded-lg shadow-sm border border-surface-container-high space-y-space-sm">
                <div className="flex items-center justify-between border-b border-surface-container-high pb-space-xs font-mono text-xs">
                  <span className="text-primary font-bold">
                    Target: {activeProposal.target_workload}
                  </span>
                  <span className="text-outline">ID: {activeProposal.proposal_id.slice(0, 12)}</span>
                </div>

                <div className="text-xs text-on-surface leading-relaxed">
                  <strong>Summary:</strong> {activeProposal.summary}
                </div>

                {activeProposal.root_cause_explanation && (
                  <div className="p-2 bg-surface-container rounded text-xs font-mono text-on-surface-variant border border-surface-container-high">
                    <strong>Root Cause:</strong> {activeProposal.root_cause_explanation}
                  </div>
                )}

                {/* Approval Decision Controls */}
                <div className="pt-2 border-t border-surface-container-high flex items-center justify-between">
                  {activeProposal.approval ? (
                    <div className="text-xs font-mono text-tertiary flex items-center gap-1.5">
                      <span className="material-symbols-outlined text-[16px]">verified</span>
                      <span>
                        Status: <strong>{activeProposal.approval.status}</strong> by{' '}
                        {activeProposal.approval.reviewer}
                      </span>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2 w-full">
                      <button
                        onClick={() => onOpenApproveModal(activeProposal)}
                        disabled={!activeProposal.can_remediate}
                        className="flex-1 flex items-center justify-center gap-1.5 py-1.5 px-3 rounded bg-primary-container text-on-primary-container font-headline-sm text-headline-sm font-semibold hover:bg-primary transition-colors disabled:opacity-40"
                      >
                        <span className="material-symbols-outlined text-[16px]">check</span>
                        <span>Approve Remediation</span>
                      </button>
                      <button
                        onClick={() => onOpenRejectModal(activeProposal)}
                        className="py-1.5 px-3 rounded bg-surface-container hover:bg-error-container hover:text-on-error-container text-on-surface font-headline-sm text-headline-sm transition-colors border border-surface-container-high"
                      >
                        <span className="material-symbols-outlined text-[16px]">close</span>
                        <span>Reject</span>
                      </button>
                    </div>
                  )}
                </div>
              </div>

              {/* Diff Viewer */}
              {activeProposal.patch?.diff ? (
                <DiffViewer
                  diffText={activeProposal.patch.diff}
                  targetKind={activeProposal.target_kind}
                  targetName={activeProposal.target_name}
                />
              ) : (
                <div className="p-8 bg-surface-container-low rounded border border-surface-container-high text-center text-xs font-mono text-on-surface-variant">
                  No manifest diff generated for this proposal.
                </div>
              )}
            </>
          ) : (
            <div className="p-12 bg-surface-container-low rounded border border-surface-container-high text-center text-xs font-mono text-on-surface-variant">
              Select a proposal to inspect manifest diff and record approval.
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
