"use client";

import { useEffect, useState } from "react";

export function CopyButton({ text, label, done }: { text: string; label: string; done: string }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const t = window.setTimeout(() => setCopied(false), 1600);
    return () => window.clearTimeout(t);
  }, [copied]);
  return (
    <button
      type="button"
      className="copy"
      onClick={() => navigator.clipboard?.writeText(text).then(() => setCopied(true))}
      aria-label={`${label}: ${text}`}
    >
      <span aria-live="polite">{copied ? done : label}</span>
    </button>
  );
}
