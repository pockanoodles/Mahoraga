"use client";

// Three ways into the same engine. The HTTP response shown is the engine's
// own output for the request above it (the curve point the exporter computed
// at the same price), cut to the fields that matter.
import { useRef, useState } from "react";
import { CopyButton } from "./CopyButton";

export interface Tab {
  id: string;
  label: string;
  code: string;
  copy: string;
  out?: string;
}

export function CodeTabs({ tabs, copyLabel, copiedLabel, label }: { tabs: Tab[]; copyLabel: string; copiedLabel: string; label: string }) {
  const [cur, setCur] = useState(tabs[0].id);
  const list = useRef<HTMLDivElement>(null);
  const tab = tabs.find((t) => t.id === cur) ?? tabs[0];
  return (
    <div className="codetabs">
      <div className="codetabs__bar">
        <div className="seg" role="tablist" aria-label={label} ref={list}>
          {tabs.map((t, i) => (
            <button
              key={t.id}
              role="tab"
              id={`tab-${t.id}`}
              aria-selected={t.id === cur}
              aria-controls={`panel-${t.id}`}
              tabIndex={t.id === cur ? 0 : -1}
              className="seg__item"
              onClick={() => setCur(t.id)}
              onKeyDown={(e) => {
                if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
                e.preventDefault();
                const next = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
                setCur(next.id);
                list.current?.querySelector<HTMLElement>(`#tab-${next.id}`)?.focus();
              }}
            >
              {t.label}
            </button>
          ))}
        </div>
        <CopyButton text={tab.copy} label={copyLabel} done={copiedLabel} />
      </div>
      <div role="tabpanel" id={`panel-${tab.id}`} aria-labelledby={`tab-${tab.id}`} className="codetabs__panel" tabIndex={0}>
        <pre className="codetabs__code">
          <code>{tab.code}</code>
        </pre>
        {tab.out && (
          <pre className="codetabs__out">
            <code>{tab.out}</code>
          </pre>
        )}
      </div>
    </div>
  );
}
