import React, { useEffect, useState } from 'react';

interface NewInvestigationModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (namespace: string, podName?: string, useLlm?: boolean) => void;
  availablePods: string[];
  defaultNamespace?: string;
  isSubmitting: boolean;
}

export const NewInvestigationModal: React.FC<NewInvestigationModalProps> = ({
  isOpen,
  onClose,
  onSubmit,
  availablePods,
  defaultNamespace = 'k8s-agent-demo',
  isSubmitting,
}) => {
  const [namespace, setNamespace] = useState(defaultNamespace);
  const [selectedPod, setSelectedPod] = useState('');
  const [useLlm, setUseLlm] = useState(true);

  useEffect(() => {
    if (isOpen) {
      setNamespace(defaultNamespace);
      setSelectedPod('');
    }
  }, [isOpen, defaultNamespace]);

  if (!isOpen) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit(namespace.trim(), selectedPod ? selectedPod.trim() : undefined, useLlm);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="w-full max-w-lg bg-surface-container-low border border-surface-container-high rounded-xl shadow-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-150">
        {/* Header */}
        <div className="flex items-center justify-between px-space-md py-space-sm bg-surface-container border-b border-surface-container-high">
          <div className="flex items-center gap-space-xs text-primary">
            <span className="material-symbols-outlined text-[20px]">psychology</span>
            <span className="font-headline-md text-headline-md text-on-surface">
              Dispatch Agentic Investigation
            </span>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high transition-colors"
          >
            <span className="material-symbols-outlined text-[18px]">close</span>
          </button>
        </div>

        {/* Form Body */}
        <form onSubmit={handleSubmit} className="p-space-md space-y-space-md">
          {/* Target Namespace */}
          <div className="space-y-1">
            <label className="font-label-sm text-label-sm text-outline uppercase tracking-wider block">
              Target Kubernetes Namespace
            </label>
            <input
              type="text"
              value={namespace}
              onChange={(e) => setNamespace(e.target.value)}
              required
              className="w-full px-space-sm py-1.5 bg-surface-container border border-outline-variant rounded font-code-block text-code-block text-on-surface focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary"
              placeholder="e.g. k8s-agent-demo"
            />
            <p className="font-body-sm text-[11px] text-on-surface-variant">
              Agent will scan workloads and cluster state in this namespace.
            </p>
          </div>

          {/* Target Pod (Optional) */}
          <div className="space-y-1">
            <label className="font-label-sm text-label-sm text-outline uppercase tracking-wider block">
              Target Pod (Optional - isolate investigation)
            </label>
            <select
              value={selectedPod}
              onChange={(e) => setSelectedPod(e.target.value)}
              className="w-full px-space-sm py-1.5 bg-surface-container border border-outline-variant rounded font-code-block text-code-block text-on-surface focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary"
            >
              <option value="">-- All Pods in Namespace (Full Triage) --</option>
              {availablePods.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </div>

          {/* Execution Mode / LLM Toggle */}
          <div className="p-space-sm bg-surface-container rounded-lg border border-surface-container-high space-y-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-space-xs">
                <span className="material-symbols-outlined text-[18px] text-primary">
                  neurology
                </span>
                <span className="font-headline-sm text-headline-sm text-on-surface">
                  Gemini 3.8 Flash Dual-Runtime
                </span>
              </div>
              <label className="relative inline-flex items-center cursor-pointer">
                <input
                  type="checkbox"
                  checked={useLlm}
                  onChange={(e) => setUseLlm(e.target.checked)}
                  className="sr-only peer"
                />
                <div className="w-9 h-5 bg-surface-container-highest peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-primary-container"></div>
              </label>
            </div>
            <p className="font-body-sm text-[11px] text-on-surface-variant leading-relaxed">
              {useLlm
                ? 'Observation and batch remediation will leverage Google Gemini with bounded retries and automatic deterministic fallback.'
                : 'Deterministic-only mode: runs purely on rule-based telemetry, grounded root-cause analyzers, and MCP tools (0 LLM requests).'}
            </p>
          </div>

          {/* Safety Notice */}
          <div className="flex items-start gap-space-xs p-space-xs bg-surface-container-lowest border border-outline-variant/40 rounded text-on-surface-variant font-body-sm text-[11px]">
            <span className="material-symbols-outlined text-tertiary text-[16px] shrink-0 mt-0.5">
              shield
            </span>
            <span>
              <strong>Safety Boundary Enforced:</strong> The pipeline operates in strictly read-only mode via MCP. No cluster mutations will occur automatically.
            </span>
          </div>

          {/* Footer Actions */}
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
              className="flex items-center gap-space-xs px-space-md py-1.5 rounded bg-primary-container text-on-primary-container font-headline-sm text-headline-sm font-semibold hover:bg-primary transition-colors shadow-sm disabled:opacity-50"
            >
              {isSubmitting ? (
                <>
                  <span className="material-symbols-outlined text-[16px] animate-spin">sync</span>
                  <span>Dispatching...</span>
                </>
              ) : (
                <>
                  <span className="material-symbols-outlined text-[16px]">play_arrow</span>
                  <span>Start Investigation</span>
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
