"use client";

// The settings exhibit: the same outside commits under three settings, one
// square each. Choosing a setting lights the commits it solved, and the
// squares that changed from the setting before it carry a ring, so the reader
// sees which commits moved as well as how many. It opens on the first setting and
// steps to the second once it scrolls into view, which is the finding itself.
// Every square is a row of the engine's export (site.json settings), placed in
// an order no outcome decides, and every count and test is the export's.
import { useEffect, useMemo, useRef, useState } from "react";
import { copy, type Locale } from "@/content/copy";
import type { SettingsArm, SettingsPair, SettingsTask } from "@/lib/data";

const STEP_DELAY_MS = 700;

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
  const [key, setKey] = useState(arms[0].key);
  const [active, setActive] = useState<number | null>(null);
  // A touch shows a commit on the first tap and opens it on the second. The
  // square a tap showed is kept in a ref, since the click that follows the
  // touch can arrive before a state update renders.
  const touch = useRef(false);
  const shown = useRef<number | null>(null);
  const chosen = useRef(false); // the reader chose a setting, so the page stops choosing
  const box = useRef<HTMLElement>(null);
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

  useEffect(() => {
    const el = box.current;
    if (!el || arms.length < 2) return;
    const step = () => {
      if (!chosen.current) setKey(arms[1].key);
    };
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      step();
      return;
    }
    let timer = 0;
    const io = new IntersectionObserver(
      ([e]) => {
        if (!e.isIntersecting) return;
        io.disconnect();
        timer = window.setTimeout(step, STEP_DELAY_MS);
      },
      { rootMargin: "0px 0px -25% 0px" },
    );
    io.observe(el);
    return () => {
      io.disconnect();
      window.clearTimeout(timer);
    };
  }, [arms]);

  // The paired test that leads into this setting from the one before it. The
  // rings and the change line both compare against that setting, whichever
  // one the reader chose last.
  const into = pairs.find((p) => p.to === key);
  const from = into ? arms.find((a) => a.key === into.from) : undefined;
  const label = (k: string) => {
    const o = t.options[k];
    if (!o) throw new Error(`copy.ts has no settings option for ${k}`);
    return o.label;
  };

  const choose = (k: string) => {
    chosen.current = true;
    setKey(k);
  };
  const change = (task: SettingsTask) => {
    if (!into || task.resolved[into.from] === task.resolved[key]) return undefined;
    return task.resolved[key] ? "gained" : "lost";
  };

  const act = active != null ? tasks[active] : null;
  const move = (d: number) => setActive((a) => Math.max(0, Math.min(n - 1, (a ?? -1) + d)));

  return (
    <figure ref={box} className="sgrid" aria-label={t.label(n, names)}>
      <div className="seg sgrid__seg" role="radiogroup" aria-label={t.choose}>
        {arms.map((a) => (
          <button key={a.key} type="button" role="radio" aria-checked={a.key === key} className="seg__item" onClick={() => choose(a.key)}>
            {label(a.key)}
          </button>
        ))}
      </div>

      <div className="sgrid__head" aria-live="polite">
        <p className="sgrid__count">
          <span className="sgrid__k">{arm.resolved}</span>
          <span className="sgrid__n">{t.count(n)}</span>
        </p>
        <p className="sgrid__note">{t.options[key]?.note ?? label(key)}</p>
        <p className="sgrid__change">
          {into && from ? t.change(into.only_versus, into.only_this, label(from.key), into.mcnemar_p) : t.baseline}
        </p>
      </div>

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
          if (e.key === "Enter" && act) window.open(act.url, "_blank", "noreferrer");
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
                <a href={task.url}>{`${task.repo} ${task.sha}`}</a> {task.committed}
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

