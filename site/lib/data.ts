// The engine's export (docs/site/site.json, written by scripts/export_site.py
// from committed data). Every measured number on the page is read from here;
// the page only formats them.
import raw from "@/generated/site.json";

export interface Rate {
  point: number;
  lo: number;
  hi: number;
  tasks: number;
  attempts: number;
}

export interface ClaimMetric {
  value: number;
  decimals: number;
}

export interface Claim {
  id: string;
  description: string;
  artifact: string;
  config: { bank: string; local_arm: string; judge: string; cloud_arm: string; date: string };
  metrics: Record<string, ClaimMetric>;
}

export interface ProbeTask {
  sha: string;
  subject: string;
  body: string;
  files: number;
  lines: number;
  outcomes: Record<string, { outcome: string; minutes: number }>;
}

export interface ProbeArm {
  arm: string;
  cond: string;
  resolved: number;
  n: number;
  rate: Rate;
  median_minutes: number;
  broke_other_code: number;
  multi_file_resolved: number;
  multi_file_n: number;
}

export interface ReplayStep {
  n: number;
  sha: string;
  outcome: string;
  rate: Rate;
}

export interface CurvePoint {
  cloud_usd_per_task: number;
  breakeven: number;
  call: "stay" | "split" | "switch";
  confident: boolean;
  monthly_usd: { lo: number; point: number; hi: number };
  reason: string;
}

export interface SiteData {
  schema: number;
  source: { repo: string; commit: string; version: string };
  claims: Claim[];
  probe: {
    repo: string;
    dates: string;
    machine: string;
    tasks: ProbeTask[];
    arms: ProbeArm[];
    replay: { arm: string; cond: string; order: string; steps: ReplayStep[] };
  };
  verdict_demo: {
    evidence: { rate: Rate; minutes_per_attempt: number; tier: string; label: string; hardware: string };
    assumptions: {
      tasks_per_month: number;
      deferrable_share: number;
      triage_minutes: number;
      hourly_usd: number;
      watts: number;
      usd_per_kwh: number;
      lines: string[];
    };
    curve: CurvePoint[];
  };
}

export const data = raw as unknown as SiteData;

export const REPO_URL = `https://github.com/${data.source.repo}`;

export function claim(id: string): Claim {
  const c = data.claims.find((x) => x.id === id);
  if (!c) throw new Error(`site.json has no claim ${id}`);
  return c;
}

export function arm(name: string, cond: string): ProbeArm {
  const a = data.probe.arms.find((x) => x.arm === name && x.cond === cond);
  if (!a) throw new Error(`site.json has no probe arm ${name} / ${cond}`);
  return a;
}

/** A claim metric written at the precision the claims file publishes it at. */
export function metric(c: Claim, key: string): string {
  const m = c.metrics[key];
  return m.value.toFixed(m.decimals);
}

export const pct = (x: number, digits = 0) => `${(x * 100).toFixed(digits)}%`;

export function usd(x: number, digits = 0): string {
  const abs = Math.abs(x).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return x < 0 ? `−$${abs}` : `$${abs}`;
}

/** The curve point for a given cloud price, or the nearest one. */
export function curveAt(usdPerTask: number): CurvePoint {
  const c = data.verdict_demo.curve;
  return c.reduce((best, p) =>
    Math.abs(p.cloud_usd_per_task - usdPerTask) < Math.abs(best.cloud_usd_per_task - usdPerTask) ? p : best,
  );
}
