import React, { useEffect, useState } from 'react';
import { RemediationProposal } from '../types/api';

interface ApprovalModalProps {
  proposal: RemediationProposal | null;
  action: 'approve' | 'reject' | null;
  defaultReviewer?: string;
  onClose: () => void;
  onConfirmApprove: (proposalId: string, reviewer: string, comments: string) => void;
  onConfirmReject: (proposalId: string, reviewer: string, reason: string) => void;
  isSubmitting: boolean;
}

export const ApprovalModal: React.FC<ApprovalModalProps> = ({
  proposal,
  action,
  defaultReviewer = 'Devansh (Cluster Admin)',
  onClose,
  onConfirmApprove,
  onConfirmReject,
  isSubmitting,
}) => {
  const [reviewer, setReviewer] = useState(defaultReviewer);
  const [note, setNote] = useState('');

  useEffect(() => {
    setReviewer(defaultReviewer);
    setNote(
      action === 'approve'
        ? 'Verified root cause and declarative manifest diff against incident evidence.'
        : 'Requires manual verification before applying configuration change.'
    );
  }, [action, proposal?.proposal_id, defaultReviewer]);

  if (!proposal || !action) return null;

  const isApprove = action === 'approve';

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (isApprove) {
      onConfirmApprove(proposal.proposal_id, reviewer, note);
    } else {
      onConfirmReject(proposal.proposal_id, reviewer, note);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="w-full max-w-lg bg-surface-container-low border border-surface-container-high rounded-xl shadow-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-150">
        {/* Header */}
        <div
          className={`flex items-center justify-between px-space-md py-space-sm border-b ${
            isApprove
              ? 'bg-surface-container text-primary border-primary/30'
              : 'bg-surface-container text-error border-error/30'
          }`}
        >
          <div className="flex items-center gap-space-xs">
            <span className="material-symbols-outlined text-[20px]">
              {isApprove ? 'verified_user' : 'cancel'}
            </span>
            <span className="font-headline-md text-headline-md text-on-surface">
              {isApprove ? 'Human Operator Remediation Sign-Off' : 'Reject Remediation Proposal'}
            </span>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high transition-colors"
          >
            <span className="material-symbols-outlined text-[18px]">close</span>
          </button>
        </div>

        {/* Content */}
        <form onSubmit={handleSubmit} className="p-space-md space-y-space-md">
          {/* Target Resource Summary */}
          <div className="p-space-sm bg-surface-container rounded border border-surface-container-high space-y-1">
            <div className="flex items-center justify-between text-xs font-mono">
              <span className="text-outline">TARGET WORKLOAD:</span>
              <span className="text-on-surface font-semibold">{proposal.target_workload}</span>
            </div>
            <div className="flex items-center justify-between text-xs font-mono">
              <span className="text-outline">PROPOSAL ID:</span>
              <span className="text-primary font-semibold">{proposal.proposal_id.slice(0, 12)}...</span>
            </div>
            <div className="flex items-center justify-between text-xs font-mono">
              <span className="text-outline">RISK LEVEL:</span>
              <span
                className={`font-bold ${
                  proposal.risk_level === 'CRITICAL' || proposal.risk_level === 'HIGH'
                    ? 'text-error'
                    : proposal.risk_level === 'MEDIUM'
                    ? 'text-[#F59E0B]'
                    : 'text-tertiary'
                }`}
              >
                {proposal.risk_level}
              </span>
            </div>
            <p className="text-xs text-on-surface-variant pt-1 border-t border-surface-container-high/60">
              {proposal.justification || proposal.summary}
            </p>
          </div>

          {/* Safety Guarantee Callout */}
          <div className="p-space-xs bg-surface-container-lowest border border-outline-variant/40 rounded flex items-start gap-space-xs text-[11px] text-on-surface-variant">
            <span className="material-symbols-outlined text-primary text-[16px] shrink-0 mt-0.5">
              shield
            </span>
            <span>
              <strong>Zero Cluster Mutation Guarantee:</strong> This action records human review in the audit registry. Per system policy, changes are staged for GitOps PR review and never mutated live.
            </span>
          </div>

          {/* Reviewer Name */}
          <div className="space-y-1">
            <label className="font-label-sm text-label-sm text-outline uppercase tracking-wider block">
              Reviewer Identity
            </label>
            <input
              type="text"
              value={reviewer}
              onChange={(e) => setReviewer(e.target.value)}
              required
              className="w-full px-space-sm py-1.5 bg-surface-container border border-outline-variant rounded font-body-sm text-on-surface focus:outline-none focus:border-primary"
            />
          </div>

          {/* Review Note / Comments */}
          <div className="space-y-1">
            <label className="font-label-sm text-label-sm text-outline uppercase tracking-wider block">
              {isApprove ? 'Approval Comments / Sign-Off Justification' : 'Rejection Reason'}
            </label>
            <textarea
              rows={3}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              required
              className="w-full px-space-sm py-1.5 bg-surface-container border border-outline-variant rounded font-body-sm text-on-surface focus:outline-none focus:border-primary resize-none"
            />
          </div>

          {/* Actions */}
          <div className="flex items-center justify-end gap-space-sm pt-space-xs">
            <button
              type="button"
              onClick={onClose}
              disabled={isSubmitting}
              className="px-space-md py-1.5 rounded font-headline-sm text-headline-sm text-on-surface-variant hover:text-on-surface hover:bg-surface-container transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSubmitting}
              className={`flex items-center gap-space-xs px-space-md py-1.5 rounded font-headline-sm text-headline-sm font-semibold transition-colors shadow-sm disabled:opacity-50 ${
                isApprove
                  ? 'bg-primary-container text-on-primary-container hover:bg-primary'
                  : 'bg-error-container text-on-error-container hover:bg-error hover:text-white'
              }`}
            >
              {isSubmitting ? (
                <>
                  <span className="material-symbols-outlined text-[16px] animate-spin">sync</span>
                  <span>Processing...</span>
                </>
              ) : isApprove ? (
                <>
                  <span className="material-symbols-outlined text-[16px]">check</span>
                  <span>Confirm Human Approval</span>
                </>
              ) : (
                <>
                  <span className="material-symbols-outlined text-[16px]">close</span>
                  <span>Confirm Rejection</span>
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
