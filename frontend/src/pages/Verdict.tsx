// The product's answer. The engine's verdict for the measured evidence and the
// reader's own situation, recomputed as they change it. Nothing on this page is
// computed here: the call, the interval, the break-even and every sentence come
// from POST /api/audit/verdict, the same code the CLI prints.
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, EngineError } from "@/lib/api";
import { intervalText, minutes, pct, plural, usd } from "@/lib/format";
import type { Assumptions, BenchSummary, Billing, Plan, Verdict as V } from "@/lib/types";
import { useResource } from "@/lib/useResource";
import { VerdictChart } from "@/components/data/VerdictChart";
import { CopyButton, Field, NumberInput, Segmented, Select, Slider } from "@/components/ui/controls";
import { Empty, ErrorNote, PageHeader, Section, Stat } from "@/components/ui/layout";

interface Inputs {
  arm: string;
  cond: string;
  billing: "api" | "subscription";
  monthly_usd: number;
  plan: string;
  plan_price: number | null;
  assumptions: Required<Assumptions>;
}

const DEFAULTS: Omit<Inputs, "arm" | "cond"> = {
  billing: "api",
  monthly_usd: 150,
  plan: "claude-max-5x",
  plan_price: null,
  assumptions: {
    tasks_per_month: 200,
    deferrable_share: 0.25,
    triage_minutes: 3,
    hourly_usd: 50,
    watts: 40,
    usd_per_kwh: 0.3,
    usage_of_cap: 0.6,
  },
};

// The reader's situation is a per-browser convenience, so it lives in localStorage.
const KEY = "mh-verdict-inputs";
function loadSaved(): Partial<Inputs> {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? "{}");
  } catch {
    return {};
  }
}

/** Every (arm, cond) that has attempts somewhere. Ordered by how much was
 *  measured, never by how well it did: picking the best of several noisy
 *  measurements and reporting it would overstate it (audit/evidence.py). */
function evidenceChoices(benches: BenchSummary[]) {
  const seen = new Map<string, { arm: string; cond: string; benches: number }>();
  for (const b of benches) {
    if (!b.attempts) continue;
    for (const arm of b.arms)
      for (const cond of b.conds) {
        const k = `${arm} / ${cond}`;
        const e = seen.get(k) ?? { arm, cond, benches: 0 };
        e.benches += 1;
        seen.set(k, e);
      }
  }
  // feedback first among equals: it is the CLI's default, since a real queue can run tests.
  return [...seen.values()].sort(
    (a, b) => b.benches - a.benches || a.arm.localeCompare(b.arm) || (a.cond === "feedback" ? -1 : b.cond === "feedback" ? 1 : 0),
  );
}

export default function VerdictPage() {
  const benches = useResource("benches", api.benches);
  const pricing = useResource("pricing", api.pricing);
  const choices = useMemo(() => evidenceChoices(benches.data?.benches ?? []), [benches.data]);

  const [inputs, setInputs] = useState<Inputs | null>(null);
  useEffect(() => {
    if (inputs || !choices.length) return;
    const saved = loadSaved();
    const q = new URLSearchParams(window.location.search);
    const asked = choices.find((c) => c.arm === q.get("arm") && c.cond === q.get("cond"));
    const valid = asked ?? choices.find((c) => c.arm === saved.arm && c.cond === saved.cond) ?? choices[0];
    setInputs({
      ...DEFAULTS,
      ...saved,
      assumptions: { ...DEFAULTS.assumptions, ...(saved.assumptions ?? {}) },
      arm: valid.arm,
      cond: valid.cond,
    });
  }, [choices, inputs]);
  useEffect(() => {
    if (!inputs) return;
    try {
      localStorage.setItem(KEY, JSON.stringify(inputs));
    } catch {
      /* private mode */
    }
  }, [inputs]);

  const [verdict, setVerdict] = useState<V>();
  const [vError, setVError] = useState<EngineError>();
  const [pending, setPending] = useState(false);
  useEffect(() => {
    if (!inputs) return;
    const billing: Billing =
      inputs.billing === "api"
        ? { kind: "api", monthly_usd: inputs.monthly_usd }
        : { kind: "subscription", plan: inputs.plan, plan_price: inputs.plan_price };
    const ctl = new AbortController();
    setPending(true);
    const t = window.setTimeout(() => {
      api
        .verdict({ benches: null, arm: inputs.arm, cond: inputs.cond, billing, assumptions: inputs.assumptions }, ctl.signal)
        .then((v) => {
          setVerdict(v);
          setVError(undefined);
        })
        .catch((e) => {
          if (e.name !== "AbortError") setVError(e instanceof EngineError ? e : new EngineError(String(e), -1));
        })
        .finally(() => !ctl.signal.aborted && setPending(false));
    }, 220);
    return () => {
      ctl.abort();
      window.clearTimeout(t);
    };
  }, [inputs]);

  if (benches.error) return <Page><ErrorNote error={benches.error} onRetry={benches.reload} /></Page>;
  if (benches.loading || (choices.length && !inputs)) return <Page />;
  if (!choices.length) {
    return (
      <Page>
        <Empty title="Nothing measured yet" command="mahoraga audit mine ~/code/your-repo">
          A verdict needs graded attempts on your own code. Mine a repository's history into tasks, run a local agent on
          them overnight, and the verdict appears here. To look around first, <code>mahoraga demo</code> loads the
          published probe.
        </Empty>
      </Page>
    );
  }

  const set = (patch: Partial<Inputs>) => setInputs((s) => (s ? { ...s, ...patch } : s));
  const setA = (patch: Partial<Inputs["assumptions"]>) =>
    setInputs((s) => (s ? { ...s, assumptions: { ...s.assumptions, ...patch } } : s));
  const i = inputs!;

  return (
    <Page actions={verdict && <CopyButton text={verdict.rendered} label="Copy as text" />}>
      <div className="verdict-layout">
        <section className="panel verdict-card" aria-live="polite" data-refreshing={pending || undefined}>
          {vError && !verdict ? (
            <ErrorNote error={vError} />
          ) : verdict ? (
            <VerdictBody v={verdict} error={vError} />
          ) : (
            <div className="verdict-card__placeholder" />
          )}
        </section>

        <aside className="panel inputs" aria-label="Your situation">
          <h2 className="panel__title">Your situation</h2>
          <Field label="Evidence" htmlFor="ev">
            <Select
              id="ev"
              value={`${i.arm} / ${i.cond}`}
              onChange={(v) => {
                const c = choices.find((x) => `${x.arm} / ${x.cond}` === v);
                if (c) set({ arm: c.arm, cond: c.cond });
              }}
              options={choices.map((c) => ({
                value: `${c.arm} / ${c.cond}`,
                label: `${c.arm} / ${c.cond}`,
              }))}
            />
          </Field>
          <Field label="How you pay for cloud agents">
            <Segmented
              label="Billing"
              value={i.billing}
              onChange={(v) => set({ billing: v })}
              options={[
                { value: "api", label: "By the token" },
                { value: "subscription", label: "Subscription" },
              ]}
            />
          </Field>
          {i.billing === "api" ? (
            <Field label="Cloud spend per month" htmlFor="spend" hint={`${usd(i.monthly_usd / Math.max(1, i.assumptions.tasks_per_month), 2)} a task`}>
              <NumberInput id="spend" value={i.monthly_usd} onChange={(v) => set({ monthly_usd: v })} min={1} max={100000} step={10} prefix="$" />
            </Field>
          ) : (
            <PlanFields plans={pricing.data?.plans ?? []} inputs={i} set={set} setA={setA} />
          )}
          <Field label="Cloud coding tasks per month" htmlFor="tpm">
            <NumberInput id="tpm" value={i.assumptions.tasks_per_month} onChange={(v) => setA({ tasks_per_month: Math.round(v) })} min={1} max={100000} step={10} />
          </Field>
          <Field label="Share that can wait overnight" htmlFor="defer">
            <Slider id="defer" local value={i.assumptions.deferrable_share} onChange={(v) => setA({ deferrable_share: v })} min={0.05} max={1} step={0.05} format={(v) => pct(v)} />
          </Field>
          <Field label="Time to triage a failed attempt" htmlFor="triage">
            <NumberInput id="triage" value={i.assumptions.triage_minutes} onChange={(v) => setA({ triage_minutes: v })} min={0} max={120} step={1} suffix="min" />
          </Field>
          <Field label="Your time" htmlFor="rate">
            <NumberInput id="rate" value={i.assumptions.hourly_usd} onChange={(v) => setA({ hourly_usd: v })} min={0} max={2000} step={5} prefix="$" suffix="/ h" />
          </Field>
          <details className="disclosure">
            <summary>Electricity</summary>
            <div className="disclosure__body">
              <Field label="Machine draw while an attempt runs" htmlFor="watts">
                <NumberInput id="watts" value={i.assumptions.watts} onChange={(v) => setA({ watts: v })} min={1} max={2000} step={5} suffix="W" />
              </Field>
              <Field label="Electricity price" htmlFor="kwh">
                <NumberInput id="kwh" value={i.assumptions.usd_per_kwh} onChange={(v) => setA({ usd_per_kwh: v })} min={0} max={5} step={0.01} prefix="$" suffix="/ kWh" />
              </Field>
            </div>
          </details>
        </aside>
      </div>

      {verdict && (
        <div className="two-up">
          <Section title="What would change it">
            <ul className="lines">
              {verdict.sensitivity.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
          </Section>
          <Section title="Assumed, not measured">
            <ul className="lines lines--muted">
              {verdict.assumptions.lines.map((s) => (
                <li key={s}>{s}</li>
              ))}
              {verdict.billing.kind === "subscription" && <li>you use {pct(verdict.assumptions.usage_of_cap)} of your plan's cap</li>}
            </ul>
          </Section>
        </div>
      )}

      {verdict && (
        <Section title="Evidence">
          <p className="prose">
            {verdict.evidence.label}, graded {verdict.evidence.tier}. {verdict.evidence.hardware}{" "}
            <Link to="/repositories">See the tasks behind it</Link>.
          </p>
        </Section>
      )}
    </Page>
  );
}

function Page({ children, actions }: { children?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <main className="page">
      <PageHeader eyebrow="Audit" title="Verdict" sub="Is local coding AI worth it for your code, on this machine?" actions={actions} />
      {children}
    </main>
  );
}

const WORD: Record<V["call"], string> = { stay: "Stay", split: "Split", switch: "Switch" };
const MEANS: Record<V["call"], string> = {
  stay: "Keep this work on the cloud.",
  split: "Send the work that can wait to local.",
  switch: "Move this work to local.",
};

function VerdictBody({ v, error }: { v: V; error?: EngineError }) {
  const r = v.evidence.rate;
  const note =
    v.breakeven != null
      ? `At ${usd(v.billing.monthly_usd / v.assumptions.tasks_per_month, 2)} a cloud task, local pays once it resolves at least ${pct(Math.min(v.breakeven, 1))} of the tasks you queue for it, after electricity and the time a failed attempt costs you.`
      : undefined;
  return (
    <>
      <div className="verdict-card__head">
        <p className="eyebrow">The call</p>
        <p className={`verdict-status${v.confident ? "" : " verdict-status--open"}`}>
          {v.confident ? "Decided" : "Not yet decided"}
        </p>
      </div>
      <p className="verdict-word" data-call={v.call}>
        {WORD[v.call]}
      </p>
      <p className="verdict-means">{MEANS[v.call]}</p>
      <p className="verdict-reason">{v.reason}</p>
      <VerdictChart rate={r} breakeven={v.breakeven} breakevenNote={note} />
      <div className="facts">
        <Stat label="Measured" value={pct(r.point)} note={`${intervalText(r)}, 95% interval`} />
        <Stat label="Tasks" value={r.tasks} note={plural(r.attempts, "attempt")} />
        <Stat label="Per attempt" value={minutes(v.evidence.minutes_per_attempt)} note="median" />
        {v.monthly_usd ? (
          <Stat
            label="Saving per month"
            value={usd(v.monthly_usd.point)}
            note={`${usd(v.monthly_usd.lo)} to ${usd(v.monthly_usd.hi)}`}
          />
        ) : (
          <Stat label="Billing" value={v.billing.plan?.name ?? "API"} note={usd(v.billing.monthly_usd) + " a month"} />
        )}
      </div>
      {error && <ErrorNote error={error} />}
    </>
  );
}

function PlanFields({
  plans,
  inputs,
  set,
  setA,
}: {
  plans: Plan[];
  inputs: Inputs;
  set: (p: Partial<Inputs>) => void;
  setA: (p: Partial<Inputs["assumptions"]>) => void;
}) {
  const plan = plans.find((p) => p.id === inputs.plan);
  const needsPrice = plan ? !plan.verified || plan.usd_per_month == null : false;
  return (
    <>
      <Field
        label="Plan"
        htmlFor="plan"
        hint={plan && (plan.verified ? `$${plan.usd_per_month}/mo, read ${plan.as_of}` : "price not verified")}
      >
        <Select
          id="plan"
          value={inputs.plan}
          onChange={(v) => set({ plan: v, plan_price: null })}
          options={plans.map((p) => ({ value: p.id, label: p.name }))}
        />
      </Field>
      {needsPrice && (
        <Field label="What you pay for it" htmlFor="pp" hint="the vendor page didn't state it">
          <NumberInput id="pp" value={inputs.plan_price ?? 0} onChange={(v) => set({ plan_price: v })} min={0} max={10000} step={10} prefix="$" suffix="/ mo" />
        </Field>
      )}
      <Field label="How much of its cap you use" htmlFor="cap">
        <Slider id="cap" value={inputs.assumptions.usage_of_cap} onChange={(v) => setA({ usage_of_cap: v })} min={0.05} max={1} step={0.05} format={(v) => pct(v)} />
      </Field>
    </>
  );
}
