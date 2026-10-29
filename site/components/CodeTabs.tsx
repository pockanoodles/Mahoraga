"use client";

// Three ways into the same engine. The HTTP response shown is the engine's
// own output for the request above it (the curve point the exporter computed
// at the same price), cut to the fields that matter.
import { useState } from "react";
import { CopyButton } from "./CopyButton";
import { Segmented } from "./Segmented";

export interface Tab {
  id: string;
  label: string;
  code: string;
  copy: string;
  out?: string;
}

export function CodeTabs({ tabs, copyLabel, copiedLabel, label }: { tabs: Tab[]; copyLabel: string; copiedLabel: string; label: string }) {
  const [cur, setCur] = useState(tabs[0].id);
  const tab = tabs.find((t) => t.id === cur) ?? tabs[0];
  return (
    <div className="codetabs">
      <div className="codetabs__bar">
        <Segmented
          role="tab"
          label={label}
          options={tabs.map((t) => ({ key: t.id, label: t.label }))}
          value={cur}
          onChange={setCur}
          ids={(id) => ({ tab: `tab-${id}`, panel: `panel-${id}` })}
        />
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
