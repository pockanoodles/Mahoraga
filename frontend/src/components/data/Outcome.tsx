// An attempt's outcome as a glyph. Shape carries the meaning and the accent
// marks only "resolved" (the work local could take), so the matrix reads the
// same in greyscale and to a colour-blind reader.
import { outcomeInfo } from "@/lib/outcomes";
import { Tooltip } from "../ui/Tooltip";

export function OutcomeGlyph({ outcome, size = 14 }: { outcome: string; size?: number }) {
  const info = outcomeInfo(outcome);
  const c = 7;
  const r = 5;
  let body;
  switch (outcome) {
    case "resolved":
      body = <circle cx={c} cy={c} r={r + 0.5} fill="var(--mh-accent)" />;
      break;
    case "partial":
      body = (
        <>
          <circle cx={c} cy={c} r={r} fill="none" stroke="currentColor" strokeWidth="1.5" />
          <path d={`M${c} ${c - r} A${r} ${r} 0 0 0 ${c} ${c + r} Z`} fill="currentColor" />
        </>
      );
      break;
    case "partial+broke":
      body = (
        <>
          <circle cx={c} cy={c} r={r} fill="none" stroke="currentColor" strokeWidth="1.5" />
          <path d={`M${c} ${c - r} A${r} ${r} 0 0 0 ${c} ${c + r} Z`} fill="currentColor" />
          <circle cx={c + 2.4} cy={c} r={1.4} fill="currentColor" />
        </>
      );
      break;
    case "broke-other":
      body = (
        <>
          <circle cx={c} cy={c} r={r} fill="none" stroke="currentColor" strokeWidth="1.5" />
          <circle cx={c} cy={c} r={1.8} fill="currentColor" />
        </>
      );
      break;
    case "wrong-file":
      body = (
        <>
          <circle cx={c} cy={c} r={r} fill="none" stroke="currentColor" strokeWidth="1.5" />
          <line x1={c - 3.4} y1={c + 3.4} x2={c + 3.4} y2={c - 3.4} stroke="currentColor" strokeWidth="1.5" />
        </>
      );
      break;
    case "no-edit":
      body = <circle cx={c} cy={c} r={r} fill="none" stroke="currentColor" strokeWidth="1.5" strokeDasharray="2.2 2.2" />;
      break;
    case "wrong-fix":
      body = <circle cx={c} cy={c} r={r} fill="none" stroke="currentColor" strokeWidth="1.5" />;
      break;
    default:
      body = <circle cx={c} cy={c} r={1.6} fill="currentColor" opacity="0.5" />;
  }
  return (
    <svg viewBox="0 0 14 14" width={size} height={size} role="img" aria-label={info.label} className="glyph">
      {body}
    </svg>
  );
}

export function OutcomeCell({ outcomes }: { outcomes: string[] | undefined }) {
  if (!outcomes || outcomes.length === 0) {
    return (
      <span className="cell-empty" aria-label="Not attempted">
        ·
      </span>
    );
  }
  const solved = outcomes.filter((o) => o === "resolved").length;
  const content = (
    <span className="tip-lines">
      {outcomes.map((o, i) => (
        <span key={i}>
          <strong>{outcomeInfo(o).label}</strong>
          {outcomes.length > 1 ? ` (repeat ${i + 1})` : ""}. {outcomeInfo(o).means}
        </span>
      ))}
    </span>
  );
  return (
    <Tooltip content={content}>
      <span className="outcome-cell" tabIndex={0}>
        {outcomes.map((o, i) => (
          <OutcomeGlyph key={i} outcome={o} />
        ))}
        {outcomes.length > 1 && <span className="outcome-cell__count">{`${solved}/${outcomes.length}`}</span>}
      </span>
    </Tooltip>
  );
}

export function OutcomeLegend({ present }: { present?: string[] }) {
  const keys = ["resolved", "partial", "wrong-fix", "broke-other", "partial+broke", "wrong-file", "no-edit"].filter(
    (k) => !present || present.includes(k),
  );
  return (
    <ul className="legend" aria-label="Outcome key">
      {keys.map((k) => (
        <li key={k}>
          <OutcomeGlyph outcome={k} size={12} />
          {outcomeInfo(k).label}
        </li>
      ))}
    </ul>
  );
}
