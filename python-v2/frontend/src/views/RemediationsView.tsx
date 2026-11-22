import React, { useEffect, useState } from 'react';
import { DiffViewer } from '../components/DiffViewer';
import { api } from '../services/api';
import { GitOpsChangeRecord, GitOpsPreviewResponse, RemediationProposal } from '../types/api';

interface RemediationsViewProps {
  proposals: RemediationProposal[];
  operatorName?: string;
  isGuest?: boolean;
  onOpenApproveModal: (proposal: RemediationProposal) => void;
  onOpenRejectModal: (proposal: RemediationProposal) => void;
  onOpenNewInvestigation: () => void;
}

export const RemediationsView: React.FC<RemediationsViewProps> = ({
  proposals,
  operatorName = 'Devansh (Cluster Admin)',
  isGuest = false,
  onOpenApproveModal,
  onOpenRejectModal,
  onOpenNewInvestigation,
}) => {
  const [statusFilter, setStatusFilter] = useState<
    'ALL' | 'ACTIONABLE' | 'INSUFFICIENT_EVIDENCE' | 'APPROVED' | 'REJECTED'
  >('ALL');

  const getProposalState = (p: RemediationProposal): 'APPROVED' | 'REJECTED' | 'ACTIONABLE' | 'INSUFFICIENT_EVIDENCE' => {
    if (p.approval?.status === 'APPROVED') return 'APPROVED';
    if (p.approval?.status === 'REJECTED') return 'REJECTED';
    if (p.can_remediate && p.status === 'ACTIONABLE') return 'ACTIONABLE';
    return 'INSUFFICIENT_EVIDENCE';
  };

  const [selectedProposalId, setSelectedProposalId] = useState<string | null>(
    proposals.length > 0 ? proposals[0].proposal_id : null
  );
  const [gitopsPreview, setGitopsPreview] = useState<Record<string, GitOpsPreviewResponse>>({});
  const [gitopsRecord, setGitopsRecord] = useState<Record<string, GitOpsChangeRecord>>({});
  const [gitopsError, setGitopsError] = useState<Record<string, string>>({});
  const [gitopsLoading, setGitopsLoading] = useState<string | null>(null);

  useEffect(() => {
    api
      .listGitOpsChanges()
      .then((records) => {
        if (!Array.isArray(records)) return;
        const map: Record<string, GitOpsChangeRecord> = {};
        records.forEach((r) => {
          map[r.proposal_id] = r;
        });
        setGitopsRecord((prev) => ({ ...map, ...prev }));
      })
      .catch(() => {
        // Ignore if backend is offline
      });
  }, []);

  const handlePreviewGitOps = async (proposalId: string) => {
    setGitopsLoading(proposalId);
    setGitopsError((prev) => ({ ...prev, [proposalId]: '' }));
    try {
      const preview = await api.previewGitOps(proposalId, operatorName);
      setGitopsPreview((prev) => ({ ...prev, [proposalId]: preview }));
    } catch (err) {
      setGitopsError((prev) => ({
        ...prev,
        [proposalId]: err instanceof Error ? err.message : String(err),
      }));
    } finally {
      setGitopsLoading(null);
    }
  };

  const handlePrepareGitOps = async (proposalId: string) => {
    setGitopsLoading(proposalId);
    setGitopsError((prev) => ({ ...prev, [proposalId]: '' }));
    try {
      const record = await api.prepareGitOps(proposalId, operatorName);
      setGitopsRecord((prev) => ({ ...prev, [proposalId]: record }));
    } catch (err) {
      setGitopsError((prev) => ({
        ...prev,
        [proposalId]: err instanceof Error ? err.message : String(err),
      }));
    } finally {
      setGitopsLoading(null);
    }
  };

  const handleCreateGitOpsPR = async (proposalId: string, changeId: string) => {
    setGitopsLoading(proposalId);
    setGitopsError((prev) => ({ ...prev, [proposalId]: '' }));
    try {
      const updated = await api.createGitOpsPR(changeId);
      setGitopsRecord((prev) => ({ ...prev, [proposalId]: updated }));
    } catch (err) {
      setGitopsError((prev) => ({
        ...prev,
        [proposalId]: err instanceof Error ? err.message : String(err),
      }));
    } finally {
      setGitopsLoading(null);
    }
  };

  const filteredProposals = proposals.filter((p) => {
    if (statusFilter === 'ALL') return true;
    return getProposalState(p) === statusFilter;
  });

  const activeProposal =
    proposals.find((p) => p.proposal_id === selectedProposalId) || filteredProposals[0] || proposals[0];

  const totalCount = proposals.length;
  const actionableCount = proposals.filter((p) => getProposalState(p) === 'ACTIONABLE').length;
  const insufficientCount = proposals.filter((p) => getProposalState(p) === 'INSUFFICIENT_EVIDENCE').length;
  const approvedCount = proposals.filter((p) => getProposalState(p) === 'APPROVED').length;
  const rejectedCount = proposals.filter((p) => getProposalState(p) === 'REJECTED').length;

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
        <button
          onClick={() => setStatusFilter('ALL')}
          className={`px-3 py-1.5 rounded transition-colors ${
            statusFilter === 'ALL'
              ? 'bg-primary-container text-on-primary-container font-bold shadow-sm'
              : 'bg-surface-container text-on-surface-variant hover:text-on-surface'
          }`}
        >
          ALL ({totalCount})
        </button>

        <button
          onClick={() => setStatusFilter('ACTIONABLE')}
          className={`px-3 py-1.5 rounded transition-colors flex items-center gap-1.5 ${
            statusFilter === 'ACTIONABLE'
              ? 'bg-tertiary/20 text-tertiary font-bold border border-tertiary/30 shadow-sm'
              : 'bg-surface-container text-on-surface-variant hover:text-on-surface'
          }`}
        >
          <span className="w-2 h-2 rounded-full bg-tertiary"></span>
          <span>ACTIONABLE ({actionableCount})</span>
        </button>

        <button
          onClick={() => setStatusFilter('INSUFFICIENT_EVIDENCE')}
          className={`px-3 py-1.5 rounded transition-colors flex items-center gap-1.5 ${
            statusFilter === 'INSUFFICIENT_EVIDENCE'
              ? 'bg-[#F59E0B]/20 text-[#F59E0B] font-bold border border-[#F59E0B]/30 shadow-sm'
              : 'bg-surface-container text-on-surface-variant hover:text-on-surface'
          }`}
        >
          <span className="w-2 h-2 rounded-full bg-[#F59E0B]"></span>
          <span>INSUFFICIENT EVIDENCE ({insufficientCount})</span>
        </button>

        <button
          onClick={() => setStatusFilter('APPROVED')}
          className={`px-3 py-1.5 rounded transition-colors flex items-center gap-1.5 ${
            statusFilter === 'APPROVED'
              ? 'bg-primary/20 text-primary font-bold border border-primary/30 shadow-sm'
              : 'bg-surface-container text-on-surface-variant hover:text-on-surface'
          }`}
        >
          <span className="w-2 h-2 rounded-full bg-primary"></span>
          <span>APPROVED ({approvedCount})</span>
        </button>

        <button
          onClick={() => setStatusFilter('REJECTED')}
          className={`px-3 py-1.5 rounded transition-colors flex items-center gap-1.5 ${
            statusFilter === 'REJECTED'
              ? 'bg-error/20 text-error font-bold border border-error/30 shadow-sm'
              : 'bg-surface-container text-on-surface-variant hover:text-on-surface'
          }`}
        >
          <span className="w-2 h-2 rounded-full bg-error"></span>
          <span>REJECTED ({rejectedCount})</span>
        </button>
      </div>

      {/* Main Split: List + Diff / Actions */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-space-md items-start">
        {/* Proposal List */}
        <div className="lg:col-span-5 bg-surface-container-low rounded-xl border border-surface-container-high overflow-hidden shadow-sm space-y-0">
          <div className="p-space-sm bg-surface-container border-b border-surface-container-high flex items-center justify-between font-mono text-xs">
            <span className="text-on-surface font-bold uppercase tracking-wider">
              Workload Proposals
            </span>
            <span className="text-outline">{filteredProposals.length} shown of {totalCount}</span>
          </div>

          <div className="divide-y divide-surface-container-high max-h-[640px] overflow-y-auto">
            {filteredProposals.length === 0 ? (
              <div className="p-8 text-center text-on-surface-variant font-mono text-xs">
                No remediation proposals found for selected filter ({statusFilter}).
              </div>
            ) : (
              filteredProposals.map((p) => {
                const isSelected = activeProposal?.proposal_id === p.proposal_id;
                const state = getProposalState(p);

                return (
                  <div
                    key={p.proposal_id}
                    onClick={() => setSelectedProposalId(p.proposal_id)}
                    className={`p-space-sm cursor-pointer transition-colors space-y-1.5 ${
                      isSelected ? 'bg-surface-container-high' : 'hover:bg-surface-container'
                    }`}
                  >
                    <div className="flex items-center justify-between font-mono text-xs gap-2">
                      <span className="font-bold text-on-surface truncate">
                        {p.target_workload}
                      </span>
                      <span
                        className={`px-1.5 py-0.5 rounded text-[10px] font-bold shrink-0 ${
                          state === 'APPROVED'
                            ? 'bg-tertiary/20 text-tertiary border border-tertiary/30'
                            : state === 'REJECTED'
                            ? 'bg-error/20 text-error border border-error/30'
                            : state === 'ACTIONABLE'
                            ? 'bg-primary/20 text-primary border border-primary/30'
                            : 'bg-[#F59E0B]/20 text-[#F59E0B] border border-[#F59E0B]/30'
                        }`}
                      >
                        {state === 'ACTIONABLE'
                          ? 'ACTIONABLE'
                          : state === 'INSUFFICIENT_EVIDENCE'
                          ? 'GUARDED'
                          : state}
                      </span>
                    </div>

                    <p className="text-xs text-on-surface-variant line-clamp-2">
                      {p.justification || p.summary}
                    </p>

                    <div className="flex items-center justify-between font-mono text-[11px] text-outline pt-1">
                      <div className="flex items-center gap-2">
                        <span>Kind: {p.target_kind}</span>
                        {p.provenance && (
                          <span className="px-1 py-0.2 rounded bg-surface-container text-[9px] text-tertiary">
                            {p.provenance.replace('_', ' ')}
                          </span>
                        )}
                      </div>
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
                  <div className="flex items-center gap-2">
                    <span className="text-primary font-bold">
                      {activeProposal.target_kind}/{activeProposal.target_workload}
                    </span>
                    {activeProposal.target_namespace && (
                      <span className="text-outline">ns: {activeProposal.target_namespace}</span>
                    )}
                  </div>
                  <span className="text-outline">ID: {activeProposal.proposal_id.slice(0, 12)}</span>
                </div>

                {/* State Banner */}
                {getProposalState(activeProposal) === 'INSUFFICIENT_EVIDENCE' && (
                  <div className="p-3 bg-[#F59E0B]/10 border border-[#F59E0B]/30 rounded text-xs space-y-1">
                    <div className="flex items-center gap-1.5 font-bold text-[#F59E0B]">
                      <span className="material-symbols-outlined text-[16px]">shield</span>
                      <span>Safe GitOps Guardrail: Automated Remediation Blocked</span>
                    </div>
                    <p className="text-on-surface text-[12px] leading-relaxed">
                      {activeProposal.insufficient_evidence_reason ||
                        activeProposal.justification ||
                        'Insufficient evidence to safely formulate an automated declarative patch without human or trusted baseline input.'}
                    </p>
                    <p className="text-outline text-[11px] font-mono">
                      Ungrounded patches are strictly prohibited to prevent operational outages.
                    </p>
                  </div>
                )}

                {getProposalState(activeProposal) === 'ACTIONABLE' && (
                  <div className="p-3 bg-tertiary/10 border border-tertiary/30 rounded text-xs space-y-1">
                    <div className="flex items-center gap-1.5 font-bold text-tertiary">
                      <span className="material-symbols-outlined text-[16px]">verified</span>
                      <span>Evidence-Grounded Remediation Proposal Ready for Review</span>
                    </div>
                    <p className="text-on-surface text-[12px] leading-relaxed">
                      {activeProposal.justification || activeProposal.summary}
                    </p>
                    <div className="flex items-center gap-3 text-outline text-[11px] font-mono pt-1">
                      <span>Provenance: <strong>{activeProposal.provenance}</strong></span>
                      <span>Risk: <strong>{activeProposal.risk_level}</strong></span>
                    </div>
                  </div>
                )}

                {/* Technical Justification */}
                <div className="text-xs text-on-surface leading-relaxed">
                  <strong>Technical Summary:</strong> {activeProposal.summary || activeProposal.justification}
                </div>

                {activeProposal.root_cause_explanation && (
                  <div className="p-2 bg-surface-container rounded text-xs font-mono text-on-surface-variant border border-surface-container-high">
                    <strong>Root Cause Alignment:</strong> {activeProposal.root_cause_explanation}
                  </div>
                )}

                {/* Verification Plan */}
                {activeProposal.verification_plan && (
                  <div className="p-2 bg-surface-container rounded text-xs font-mono text-on-surface-variant border border-surface-container-high space-y-1">
                    <div className="font-bold text-on-surface flex items-center gap-1">
                      <span className="material-symbols-outlined text-[14px] text-tertiary">check_circle</span>
                      <span>Automated Post-Fix Verification Plan:</span>
                    </div>
                    <div className="text-[11px] grid grid-cols-2 gap-2 text-outline">
                      <div>Target Phase: <span className="text-on-surface">{activeProposal.verification_plan.target_phase || activeProposal.verification_plan.target_pod_phase || 'Running'}</span></div>
                      <div>Timeout: <span className="text-on-surface">{activeProposal.verification_plan.timeout_seconds || 60}s</span></div>
                      {activeProposal.verification_plan.negative_assertions && (
                        <div className="col-span-2">
                          Negative Assertions: <span className="text-on-surface">{activeProposal.verification_plan.negative_assertions.join(', ')}</span>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* Approval Decision Controls */}
                <div className="pt-2 border-t border-surface-container-high space-y-2">
                  {activeProposal.approval && activeProposal.approval.status !== 'PENDING' ? (
                    <div className="space-y-2">
                      <div className={`text-xs font-mono flex items-center gap-1.5 ${
                        activeProposal.approval.status === 'APPROVED' ? 'text-tertiary' : 'text-error'
                      }`}>
                        <span className="material-symbols-outlined text-[16px]">
                          {activeProposal.approval.status === 'APPROVED' ? 'verified' : 'cancel'}
                        </span>
                        <span>
                          Decision: <strong>{activeProposal.approval.status}</strong> by{' '}
                          {activeProposal.approval.reviewer || 'operator'}
                          {activeProposal.approval.comments ? ` (${activeProposal.approval.comments})` : ''}
                        </span>
                      </div>

                      {activeProposal.approval.status === 'APPROVED' && (
                        <div className="p-2.5 bg-surface-container rounded border border-surface-container-high space-y-2">
                          <div className="flex flex-wrap items-center gap-2">
                            <button
                              type="button"
                              onClick={() => handlePreviewGitOps(activeProposal.proposal_id)}
                              disabled={gitopsLoading === activeProposal.proposal_id}
                              className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-surface-container-high hover:bg-surface-container-highest text-on-surface font-mono text-xs border border-outline-variant/40 transition-colors disabled:opacity-50"
                            >
                              <span className="material-symbols-outlined text-[15px] text-primary">preview</span>
                              <span>Preview GitOps Diff</span>
                            </button>
                            <button
                              type="button"
                              onClick={() => handlePrepareGitOps(activeProposal.proposal_id)}
                              disabled={
                                isGuest ||
                                gitopsLoading === activeProposal.proposal_id ||
                                Boolean(gitopsRecord[activeProposal.proposal_id])
                              }
                              title={isGuest ? 'Guest mode: switch to a Cluster Admin profile to prepare Git branches' : undefined}
                              className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-primary-container text-on-primary-container hover:bg-primary font-mono text-xs font-semibold transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                              <span className="material-symbols-outlined text-[15px]">alt_route</span>
                              <span>
                                {gitopsRecord[activeProposal.proposal_id]
                                  ? 'Local Branch Prepared'
                                  : 'Prepare Local GitOps Branch'}
                              </span>
                            </button>
                          </div>

                          {gitopsError[activeProposal.proposal_id] && (
                            <div className="p-2 rounded bg-error/10 border border-error/30 text-error font-mono text-[11px]">
                              {gitopsError[activeProposal.proposal_id]}
                            </div>
                          )}

                          {gitopsPreview[activeProposal.proposal_id] && !gitopsRecord[activeProposal.proposal_id] && (
                            <div className="p-2 rounded bg-surface-container-lowest border border-primary/30 font-mono text-[11px] space-y-1 text-on-surface-variant">
                              <div className="text-primary font-bold">
                                GitOps Preview: {gitopsPreview[activeProposal.proposal_id].target_file}
                              </div>
                              <div>
                                Image: {gitopsPreview[activeProposal.proposal_id].current_image} →{' '}
                                <span className="text-tertiary font-bold">
                                  {gitopsPreview[activeProposal.proposal_id].replacement_image}
                                </span>
                              </div>
                            </div>
                          )}

                          {gitopsRecord[activeProposal.proposal_id] && (
                            <div className="p-2 rounded bg-tertiary/10 border border-tertiary/30 font-mono text-[11px] space-y-1.5 text-on-surface">
                              <div className="text-tertiary font-bold flex items-center gap-1">
                                <span className="material-symbols-outlined text-[14px]">check_circle</span>
                                <span>
                                  {gitopsRecord[activeProposal.proposal_id].pr_url
                                    ? 'GitHub Pull Request Opened'
                                    : 'Local GitOps Branch Prepared (Ready for PR)'}
                                </span>
                              </div>
                              <div>Branch: {gitopsRecord[activeProposal.proposal_id].branch_name}</div>
                              <div>Commit: {gitopsRecord[activeProposal.proposal_id].commit_hash}</div>
                              {gitopsRecord[activeProposal.proposal_id].pr_url ? (
                                <a
                                  href={gitopsRecord[activeProposal.proposal_id].pr_url!}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-primary-container text-on-primary-container font-semibold hover:bg-primary transition-colors mt-1"
                                >
                                  <span className="material-symbols-outlined text-[14px]">open_in_new</span>
                                  <span>
                                    View Pull Request #{gitopsRecord[activeProposal.proposal_id].pr_number}
                                  </span>
                                </a>
                              ) : (
                                <button
                                  type="button"
                                  onClick={() =>
                                    handleCreateGitOpsPR(
                                      activeProposal.proposal_id,
                                      gitopsRecord[activeProposal.proposal_id].change_id
                                    )
                                  }
                                  disabled={isGuest || gitopsLoading === activeProposal.proposal_id}
                                  title={isGuest ? 'Guest mode: switch to a Cluster Admin profile to create GitHub PRs' : undefined}
                                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded bg-primary-container text-on-primary-container hover:bg-primary font-mono text-xs font-semibold transition-colors disabled:opacity-50 disabled:cursor-not-allowed mt-1"
                                >
                                  <span className="material-symbols-outlined text-[15px]">call_made</span>
                                  <span>Create GitHub PR</span>
                                </button>
                              )}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  ) : activeProposal.can_remediate && activeProposal.status === 'ACTIONABLE' ? (
                    <div className="flex items-center gap-2 w-full">
                      <button
                        onClick={() => onOpenApproveModal(activeProposal)}
                        disabled={isGuest}
                        title={isGuest ? 'Guest mode: switch to a Cluster Admin profile to approve' : undefined}
                        className="flex-1 flex items-center justify-center gap-1.5 py-1.5 px-3 rounded bg-primary-container text-on-primary-container font-headline-sm text-headline-sm font-semibold hover:bg-primary transition-colors shadow-sm disabled:opacity-40 disabled:cursor-not-allowed"
                      >
                        <span className="material-symbols-outlined text-[16px]">check</span>
                        <span>Approve Remediation (Stage GitOps)</span>
                      </button>
                      <button
                        onClick={() => onOpenRejectModal(activeProposal)}
                        disabled={isGuest}
                        title={isGuest ? 'Guest mode: switch to a Cluster Admin profile to reject' : undefined}
                        className="py-1.5 px-3 rounded bg-surface-container hover:bg-error-container hover:text-on-error-container text-on-surface font-headline-sm text-headline-sm transition-colors border border-surface-container-high disabled:opacity-40 disabled:cursor-not-allowed"
                      >
                        <span className="material-symbols-outlined text-[16px]">close</span>
                        <span>Reject</span>
                      </button>
                    </div>
                  ) : (
                    <div className="text-outline font-mono text-xs flex items-center gap-1.5">
                      <span className="material-symbols-outlined text-[14px]">lock</span>
                      <span>Approval disabled — automated patch generation withheld for safety</span>
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
                  No manifest diff generated for this proposal (Status: {activeProposal.status}).
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
