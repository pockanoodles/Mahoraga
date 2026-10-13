// Number and time formatting. One function per kind of quantity, so a rate is
// written the same way on every screen.
import type { Rate } from "./types";

const MINUS = "−";

export function pct(x: number, digits = 0): string {
  return `${(x * 100).toFixed(digits)}%`;
}

/** "28% [13–51%]": the point with its 95% interval. */
export function rateText(r: Rate): string {
  return `${pct(r.point)} [${pct(r.lo)}–${pct(r.hi)}]`;
}

/** "13–51%": the interval alone. */
export function intervalText(r: Rate): string {
  return `${pct(r.lo)}–${pct(r.hi)}`;
}

/** Signed dollars with a true minus sign: "$1,240", "−$41". */
export function usd(x: number, digits = 0): string {
  const abs = Math.abs(x).toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  return x < 0 ? `${MINUS}$${abs}` : `$${abs}`;
}

export function minutes(m: number): string {
  if (m < 1) return `${Math.round(m * 60)} s`;
  return `${m < 10 ? m.toFixed(1) : Math.round(m)} min`;
}

export function seconds(s: number): string {
  return minutes(s / 60);
}

export function duration(fromIso: string, toIso: string | null): string {
  if (!toIso) return "running";
  const ms = Date.parse(toIso) - Date.parse(fromIso);
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const h = Math.floor(ms / 3_600_000);
  const m = Math.round((ms % 3_600_000) / 60_000);
  return h ? `${h} h ${m} min` : `${m} min`;
}

export function date(iso: string | null, withTime = false): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: d.getFullYear() === new Date().getFullYear() ? undefined : "numeric",
    ...(withTime ? { hour: "numeric", minute: "2-digit" } : {}),
  });
}

export function relative(iso: string | null): string {
  if (!iso) return "never";
  const s = (Date.now() - Date.parse(iso)) / 1000;
  if (!Number.isFinite(s)) return "—";
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86_400) return `${Math.round(s / 3600)} h ago`;
  if (s < 7 * 86_400) return `${Math.round(s / 86_400)} d ago`;
  return date(iso);
}

export function count(n: number): string {
  return n.toLocaleString("en-US");
}

export function shortSha(sha: string): string {
  return sha.slice(0, 7);
}

/** "aider:qwen3.5:latest@ctx=131072" → agent, model, context. */
export function parseArm(arm: string): { agent: string; model: string; ctx: string | null } {
  const [spec, ...opts] = arm.split("@");
  const i = spec.indexOf(":");
  const agent = i < 0 ? spec : spec.slice(0, i);
  const model = i < 0 ? "" : spec.slice(i + 1);
  const ctx = opts.find((o) => o.startsWith("ctx="))?.slice(4) ?? null;
  return { agent, model, ctx };
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${count(n)} ${n === 1 ? one : many}`;
}
