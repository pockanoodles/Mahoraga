"use client";

// The settings exhibit: the same outside commits under three settings, one
// square each. Choosing a setting lights the commits it solved, and the
// squares that changed from the setting before it carry a ring, so the reader
// sees which commits moved as well as how many. It opens on the first paired
// comparison's setting, which is the finding, and never changes the setting
// itself: a figure that swaps its own number reads as a page that glitched.
// Every square is a row of the engine's export (site.json settings), placed in
// an order no outcome decides, and every count and test is the export's.
import { useMemo, useRef, useState } from "react";
import { copy, type Locale } from "@/content/copy";
import type { SettingsArm, SettingsPair, SettingsTask } from "@/lib/data";
import { Count } from "./Count";
import { Segmented } from "./Segmented";

export function SettingsGrid({
  arms,
  tasks,
  pairs,
  locale,
}: {
  arms: SettingsArm[];
  tasks: SettingsTask[];
  pairs: SettingsPair[];
  locale: Locale;
}) {
  const t = copy[locale].settings;
  const [key, setKey] = useState(pairs[0]?.to ?? arms[0].key);
  const [active, setActive] = useState<number | null>(null);
  // A touch shows a commit on the first tap and opens it on the second. The
  // square a tap showed is kept in a ref, since the click that follows the
  // touch can arrive before a state update renders.
  const touch = useRef(false);
  const shown = useRef<number | null>(null);
  const arm = arms.find((a) => a.key === key) ?? arms[0];
  const n = tasks.length;
  const names = useMemo(() => [...new Set(tasks.map((x) => x.repo))], [tasks]);

  const repos = useMemo(() => {
    const out: { repo: string; items: { task: SettingsTask; i: number }[] }[] = [];
    tasks.forEach((task, i) => {
      const last = out[out.length - 1];
      if (last?.repo === task.repo) last.items.push({ task, i });
      else out.push({ repo: task.repo, items: [{ task, i }] });
    });
    return out;
  }, [tasks]);

  // The paired test that leads into a setting from the one before it. The
  // rings and the change line both compare against that setting, whichever
  // one the reader chose last.
  const into = pairs.find((p) => p.to === key);
  const label = (k: string) => {
    const o = t.options[k];
    if (!o) throw new Error(`copy.ts has no settings option for ${k}`);
    return o.label;
  };
  const lines = (k: string) => {
    const p = pairs.find((x) => x.to === k);
    const from = p && arms.find((a) => a.key === p.from);
    return {
      note: t.options[k]?.note ?? label(k),
      change: p && from ? t.change(p.only_versus, p.only_this, label(from.key), p.mcnemar_p) : t.baseline,
    };
  };
  const now = lines(key);

  const change = (task: SettingsTask) => {
    if (!into || task.resolved[into.from] === task.resolved[key]) return undefined;
    return task.resolved[key] ? "gained" : "lost";
  };

  const act = active != null ? tasks[active] : null;
  const move = (d: number) => setActive((a) => Math.max(0, Math.min(n - 1, (a ?? -1) + d)));

  return (
    <figure className="sgrid" aria-label={t.label(n, names)}>
      <Segmented
        className="sgrid__seg"
        label={t.choose}
        options={arms.map((a) => ({ key: a.key, label: label(a.key) }))}
        value={key}
        onChange={setKey}
      />

      {/* Every setting's lines share one cell, so the tallest sets the head's
          height and the squares below stay put when the reader switches. A
          screen reader hears the current setting's lines once, from the live line. */}
      <div className="sgrid__head" aria-hidden>
        <p className="sgrid__count">
          <Count className="sgrid__k" value={arm.resolved} max={n} />
          <span className="sgrid__n">{t.count(n)}</span>
        </p>
        <div className="sgrid__lines">
          {arms.map((a) => {
            const l = lines(a.key);
            return (
              <div key={a.key} className="sgrid__line" data-on={a.key === key || undefined}>
                <p className="sgrid__note">{l.note}</p>
                <p className="sgrid__change">{l.change}</p>
              </div>
            );
          })}
        </div>
      </div>
      <p className="sr-only" aria-live="polite">
        {locale === "ja" ? `${arm.resolved} ${t.count(n)}。${now.note}${now.change}` : `${arm.resolved} ${t.count(n)}. ${now.note} ${now.change}`}
      </p>

      <div
        className="sgrid__plot"
        tabIndex={0}
        aria-label={t.keys}
        onBlur={(e) => {
          // Focus moving inside the plot, to a square or to the tip's own link,
          // keeps the commit shown. Leaving it starts the taps over.
          if (e.currentTarget.contains(e.relatedTarget)) return;
          shown.current = null;
          setActive(null);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowRight" || e.key === "ArrowDown") {
            e.preventDefault();
            move(1);
          }
          if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
            e.preventDefault();
            move(-1);
          }
          if (e.key === "Escape") setActive(null);
          // Enter on the plot opens the commit shown, and a link inside it opens itself.
          if (e.key === "Enter" && act && e.target === e.currentTarget) window.open(act.url, "_blank", "noreferrer");
        }}
      >
        {repos.map((r) => (
          <div key={r.repo} className="sgrid__repo" aria-hidden>
            <p className="sgrid__repo-name">{r.repo}</p>
            <ol className="sgrid__squares">
              {r.items.map(({ task, i }) => (
                <li key={`${task.repo}/${task.sha}`}>
                  <a
                    href={task.url}
                    target="_blank"
                    rel="noreferrer"
                    tabIndex={-1}
                    className="sgrid__sq"
                    style={{ ["--i" as string]: i }}
                    data-solved={task.resolved[key] || undefined}
                    data-change={change(task)}
                    data-active={active === i || undefined}
                    onPointerDown={(e) => {
                      touch.current = e.pointerType === "touch";
                    }}
                    onPointerEnter={(e) => {
                      if (e.pointerType !== "touch") setActive(i);
                    }}
                    onClick={(e) => {
                      if (touch.current && shown.current !== i) {
                        e.preventDefault();
                        shown.current = i;
                        setActive(i);
                      }
                    }}
                  />
                </li>
              ))}
            </ol>
          </div>
        ))}
        <div className="sgrid__tip" role="status">
          {act ? (
            <>
              <p className="sgrid__tip-head">
                <span>{act.repo}</span>
                <span className="mono">{act.sha}</span>
                <span>{act.committed}</span>
              </p>
              <ul className="sgrid__tip-rows">
                {arms.map((a) => (
                  <li key={a.key} data-solved={act.resolved[a.key] || undefined} data-current={a.key === key || undefined}>
                    <span>{label(a.key)}</span>
                    <span>{act.resolved[a.key] ? t.solved : t.missed}</span>
                  </li>
                ))}
              </ul>
              <a className="sgrid__tip-open" href={act.url} target="_blank" rel="noreferrer">
                {t.open} <span aria-hidden>↗</span>
              </a>
            </>
          ) : (
            <p className="sgrid__tip-hint">{t.hint}</p>
          )}
        </div>
      </div>

      <figcaption className="sgrid__caption">
        <span className="sgrid__legend">
          <span className="sgrid__key" data-solved aria-hidden />
          {t.solved}
          <span className="sgrid__key" aria-hidden />
          {t.missed}
          <span className="sgrid__key" data-change="gained" aria-hidden />
          {t.changed}
        </span>
        <span>{t.order}</span>
      </figcaption>

      <table className="sr-only">
        <caption>{t.label(n, names)}</caption>
        <thead>
          <tr>
            <th scope="col">{t.commit}</th>
            {arms.map((a) => (
              <th key={a.key} scope="col">
                {label(a.key)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {tasks.map((task) => (
            <tr key={`${task.repo}/${task.sha}`}>
              <td>
                {/* A screen reader reaches these by reading. Tab skips them, since the table is never seen. */}
                <a href={task.url} tabIndex={-1}>
                  {`${task.repo} ${task.sha}`}
                </a>{" "}
                {task.committed}
              </td>
              {arms.map((a) => (
                <td key={a.key}>{task.resolved[a.key] ? t.solved : t.missed}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}

