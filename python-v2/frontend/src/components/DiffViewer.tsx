import React, { useState } from 'react';

interface DiffViewerProps {
  diffText: string;
  targetKind: string;
  targetName: string;
}

export const DiffViewer: React.FC<DiffViewerProps> = ({
  diffText,
  targetKind,
  targetName,
}) => {
  const [copied, setCopied] = useState(false);

  const lines = diffText ? diffText.split('\n') : [];
  let additions = 0;
  let deletions = 0;

  lines.forEach((line) => {
    if (line.startsWith('+') && !line.startsWith('+++')) additions++;
    if (line.startsWith('-') && !line.startsWith('---')) deletions++;
  });

  const handleCopy = () => {
    navigator.clipboard.writeText(diffText);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="bg-surface-container-low rounded-lg p-space-md shadow-sm border border-surface-container-high flex flex-col gap-space-sm overflow-hidden">
      {/* Diff Header */}
      <div className="flex flex-wrap items-center justify-between gap-space-xs border-b border-surface-container-high pb-space-sm">
        <div className="flex items-center gap-space-xs truncate">
          <span className="material-symbols-outlined text-primary text-[18px]">difference</span>
          <h3 className="font-headline-sm text-headline-sm text-on-surface">Manifest Diff Preview</h3>
          <span className="font-code-block text-[11px] px-1.5 py-0.5 rounded bg-surface-container text-on-surface-variant font-mono truncate">
            {targetKind.toLowerCase()}/{targetName}.yaml
          </span>
        </div>
        <div className="flex items-center gap-space-xs font-code-block text-[11px]">
          {deletions > 0 && (
            <span className="text-error bg-error/10 px-1.5 py-0.5 rounded font-mono">
              -{deletions} lines
            </span>
          )}
          {additions > 0 && (
            <span className="text-tertiary bg-tertiary/10 px-1.5 py-0.5 rounded font-mono">
              +{additions} lines
            </span>
          )}
          <button
            onClick={handleCopy}
            className="flex items-center gap-1 px-2 py-0.5 rounded bg-surface-container text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high transition-colors"
            title="Copy diff to clipboard"
          >
            <span className="material-symbols-outlined text-[14px]">
              {copied ? 'check' : 'content_copy'}
            </span>
            <span>{copied ? 'Copied' : 'Copy'}</span>
          </button>
        </div>
      </div>

      {/* Diff Content Box */}
      <div className="bg-surface-container-lowest rounded border border-outline-variant/30 font-code-block text-code-block overflow-x-auto max-h-[380px] p-2 leading-relaxed">
        {lines.length === 0 ? (
          <div className="p-4 text-center text-on-surface-variant text-xs">
            No manifest diff generated.
          </div>
        ) : (
          lines.map((line, idx) => {
            const isAdd = line.startsWith('+') && !line.startsWith('+++');
            const isDel = line.startsWith('-') && !line.startsWith('---');
            const isHeader = line.startsWith('@@') || line.startsWith('---') || line.startsWith('+++');

            let lineStyle = 'text-on-surface-variant';
            let bgStyle = '';

            if (isAdd) {
              lineStyle = 'text-[#86EFAC]';
              bgStyle = 'bg-[#22C55E]/15';
            } else if (isDel) {
              lineStyle = 'text-[#FCA5A5]';
              bgStyle = 'bg-[#EF4444]/15';
            } else if (isHeader) {
              lineStyle = 'text-primary font-semibold';
              bgStyle = 'bg-surface-container/60';
            }

            return (
              <div
                key={idx}
                className={`flex items-start px-2 py-0.5 rounded-sm font-mono text-[12px] ${lineStyle} ${bgStyle}`}
              >
                <span className="w-8 shrink-0 text-outline select-none text-right pr-3 opacity-60">
                  {idx + 1}
                </span>
                <span className="w-4 shrink-0 select-none font-bold">
                  {isAdd ? '+' : isDel ? '-' : ' '}
                </span>
                <span className="whitespace-pre flex-1">
                  {isAdd || isDel ? line.slice(1) : line}
                </span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};
