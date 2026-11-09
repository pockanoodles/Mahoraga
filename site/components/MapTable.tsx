// The map (ADR 2026-11-08): one row per kind of work, local and cloud measured
// on the same tasks, whether local has caught up with each cloud tier, and what
// the price says. Every number is read from a claim in the engine's export, as
// in the log, so a row can't disagree with the attempts behind it. A table on a
// wide screen; on a phone each row stacks, and each cell carries its column's
// name. The cloud and caught-up cells have a line per tier.
import type { Dict } from "@/content/copy";
import { PRICE } from "@/content/log";
import { blob, claim, metric, num, pct, points, type Claim } from "@/lib/data";

const REPORT = "docs/report/2026-10-local-coding-ai.md";


// A tier's standing on a row (ADR 2026-11-08-caught-up-by-capability). A
// crossing can't be printed from one claim: it needs the tasks committed after
// the cloud model's cutoff not to read behind, and a confirmation claim on
// freshly mined tasks (decisions 4 and 5). Neither exists yet, so the build
// stops on a crossing until they do.
function standingLine(t: Dict["map"], model: string, c: Claim): string {
  const s = metric(c, "standing");
  const args = [model, points(num(c, "gap")), points(num(c, "gap_lo")), points(num(c, "gap_hi"))] as const;
  if (s === "behind") return t.behind(...args);
  if (s === "open") return t.open(...args);
  throw new Error(`${c.id} reads "${s}", and the map prints a crossing only with the commit-date split and a confirmation claim behind it`);
}

export function MapTable({ t }: { t: Dict["map"] }) {
  const laptop = claim("report_2026_10_laptop_131k");
  const vs = claim("report_2026_11_laptop_vs_cloud");
  const after = claim("report_2026_11_laptop_vs_cloud_after_june");
  const big = claim("report_2026_10_studio_qwen36_outside");
  const n = num;
  // Each cloud tier against the laptop on the same tasks, all of them and the
  // ones committed after June 2026.
  const tiers = [
    { model: "Opus 5.5", vs, after },
    { model: "Sonnet 5.5", vs: claim("report_2026_11_laptop_vs_sonnet55"), after: claim("report_2026_11_laptop_vs_sonnet55_after_june") },
    { model: "Haiku 5.5", vs: claim("report_2026_11_laptop_vs_haiku55"), after: claim("report_2026_11_laptop_vs_haiku55_after_june") },
  ];
  type Cell = string | string[];
  const rows: { work: string; local: string; cloud: Cell; caught: Cell; price: string }[] = [
    {
      work: t.work47(n(vs, "pairs")),
      local: t.laptop(n(vs, "pair_resolved"), n(vs, "pairs"), pct(n(laptop, "rate")), pct(n(laptop, "rate_lo")), pct(n(laptop, "rate_hi"))),
      cloud: tiers.map((x, i) => {
        const line = t.tier(x.model, n(x.vs, "versus_resolved"), n(x.vs, "pairs"), `$${n(x.vs, "cloud_usd_per_task").toFixed(2)}`);
        return i === 0 ? t.agent(line) : line;
      }),
      caught: tiers.map((x) => standingLine(t, x.model, x.vs)),
      price: t.stayTiers(...(tiers.map((x) => pct(n(x.vs, "breakeven_rate"))) as [string, string, string])),
    },
    {
      work: t.workAfter(n(after, "pairs")),
      local: t.count(n(after, "pair_resolved"), n(after, "pairs")),
      cloud: tiers.map((x) => t.tierCount(x.model, n(x.after, "versus_resolved"), n(x.after, "pairs"))),
      caught: tiers.map((x) => standingLine(t, x.model, x.after)),
      price: t.notPriced,
    },
    {
      work: t.work92(n(big, "tasks")),
      local: t.studio35(n(big, "resolved"), n(big, "tasks"), pct(n(big, "rate")), pct(n(big, "rate_lo")), pct(n(big, "rate_hi"))),
      cloud: t.notRun,
      caught: t.noCloud,
      price: t.stayAssumed(pct(n(big, "breakeven_rate")), `$${PRICE.toFixed(2)}`),
    },
  ];
  // The laptop's own run is the local side of the paired rows, every tier ran
  // its tasks, every gap is read against the one margin the lead prints, and
  // the price column's "stay" is each verdict's call.
  for (const x of tiers) {
    if (n(laptop, "tasks") !== n(x.vs, "pairs")) throw new Error(`the map pairs the laptop's run with ${x.model} on different tasks`);
    if (n(after, "pairs") !== n(x.after, "pairs")) throw new Error(`the map's after-June row pairs ${x.model} on different tasks`);
    for (const c of [x.vs, x.after]) {
      if (num(c, "margin") !== num(vs, "margin")) throw new Error(`${c.id} reads its gap against another margin`);
    }
    if (metric(x.vs, "call") !== "stay") throw new Error(`${x.vs.id}'s verdict is no longer stay; the price column says it is`);
  }
  if (metric(big, "call") !== "stay") throw new Error(`${big.id}'s verdict is no longer stay; the price column says it is`);
  const lines = (cell: Cell) =>
    Array.isArray(cell)
      ? cell.map((line) => (
          <span key={line} className="map__line">
            {line}
          </span>
        ))
      : cell;

  return (
    <div className="map">
      <table className="map__table">
        <thead>
          <tr>
            <th scope="col">{t.head.work}</th>
            <th scope="col">{t.head.local}</th>
            <th scope="col">{t.head.cloud}</th>
            <th scope="col">{t.head.caught}</th>
            <th scope="col">{t.head.price}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.work}>
              <th scope="row">{r.work}</th>
              <td data-label={t.head.local}>{r.local}</td>
              <td data-label={t.head.cloud}>{lines(r.cloud)}</td>
              <td data-label={t.head.caught} className="map__caught">
                {lines(r.caught)}
              </td>
              <td data-label={t.head.price}>{r.price}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="map__running">
        <h3 className="map__running-title">{t.runningTitle}</h3>
        <ul>
          {t.running.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </div>
      <p className="small map__rule">{t.rule}</p>
      <p className="small map__rule">
        {t.source}{" "}
        <a className="textlink" href={blob(REPORT)} target="_blank" rel="noreferrer">
          {t.sourceLink} <span aria-hidden>↗</span>
        </a>
      </p>
    </div>
  );
}
