// The map (ADR 2026-11-08): one row per kind of work, local and cloud measured
// on the same tasks, and whether local has caught up. Every number is read from
// a claim in the engine's export, as in the log, so a row can't disagree with
// the attempts behind it. A table on a wide screen; on a phone each row stacks,
// and each cell carries its column's name.
import type { Dict } from "@/content/copy";
import { PRICE } from "@/content/log";
import { blob, claim, num, pct } from "@/lib/data";

const REPORT = "docs/report/2026-10-local-coding-ai.md";

export function MapTable({ t }: { t: Dict["map"] }) {
  const laptop = claim("report_2026_10_laptop_131k");
  const vs = claim("report_2026_11_laptop_vs_cloud");
  const after = claim("report_2026_11_laptop_vs_cloud_after_june");
  const big = claim("report_2026_10_studio_qwen36_outside");
  const n = num;
  const rows = [
    {
      work: t.work47(n(vs, "pairs")),
      local: t.laptop(n(vs, "pair_resolved"), n(vs, "pairs"), pct(n(laptop, "rate")), pct(n(laptop, "rate_lo")), pct(n(laptop, "rate_hi"))),
      cloud: t.opus(n(vs, "versus_resolved"), n(vs, "pairs"), `$${n(vs, "cloud_usd_per_task").toFixed(2)}`),
      caught: t.needsSpent(pct(n(vs, "breakeven_rate"))),
    },
    {
      work: t.workAfter(n(after, "pairs")),
      local: t.count(n(after, "pair_resolved"), n(after, "pairs")),
      cloud: t.count(n(after, "versus_resolved"), n(after, "pairs")),
      caught: t.no,
    },
    {
      work: t.work92(n(big, "tasks")),
      local: t.studio35(n(big, "resolved"), n(big, "tasks"), pct(n(big, "rate")), pct(n(big, "rate_lo")), pct(n(big, "rate_hi"))),
      cloud: t.notRun,
      caught: t.needsAssumed(pct(n(big, "breakeven_rate")), `$${PRICE.toFixed(2)}`),
    },
  ];
  // The laptop's own run is the local side of the paired rows.
  if (n(laptop, "tasks") !== n(vs, "pairs")) throw new Error("the map pairs the laptop's run with a cloud run on different tasks");

  return (
    <div className="map">
      <table className="map__table">
        <thead>
          <tr>
            <th scope="col">{t.head.work}</th>
            <th scope="col">{t.head.local}</th>
            <th scope="col">{t.head.cloud}</th>
            <th scope="col">{t.head.caught}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.work}>
              <th scope="row">{r.work}</th>
              <td data-label={t.head.local}>{r.local}</td>
              <td data-label={t.head.cloud}>{r.cloud}</td>
              <td data-label={t.head.caught} className="map__caught">
                {r.caught}
              </td>
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
