// One repository's bench: how each arm did, how far its interval is from
// useful, and every task with every attempt's outcome.
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ChevronLeft } from "lucide-react";
import { api } from "@/lib/api";
import { count, date, duration, minutes, pct, plural, rateText, shortSha } from "@/lib/format";
import type { ArmSummary, BenchDetail, Task } from "@/lib/types";
import { useResource } from "@/lib/useResource";
import { IntervalBar } from "@/components/data/IntervalBar";
import { OutcomeCell, OutcomeLegend } from "@/components/data/Outcome";
import { Segmented } from "@/components/ui/controls";
import { Empty, ErrorNote, KeyValues, Mono, PageHeader, Section, Stat } from "@/components/ui/layout";
import { Tooltip } from "@/components/ui/Tooltip";

export default function RepositoryPage() {
  const { id = "" } = useParams();
  const r = useResource(`bench:${id}`, (s) => api.bench(id, s));
  const b = r.data;
  return (
    <main className="page">
      <Link to="/repositories" className="back">
        <ChevronLeft size={16} aria-hidden /> Repositories
      </Link>
      {r.error && <ErrorNote error={r.error} onRetry={r.reload} />}
      {b && <Bench b={b} />}
    </main>
  );
}

function Bench({ b }: { b: BenchDetail }) {
  const attempts = b.arms.reduce((n, a) => n + a.n, 0);
  return (
    <>
      <PageHeader
        eyebrow="Repository"
        title={b.name}
        sub={b.repo ? <Mono>{b.repo}</Mono> : <Mono>{b.id}</Mono>}
      />
      <div className="facts facts--page">
        <Stat label="Tasks" value={count(b.tasks.length)} note={b.dropped.length ? `${b.dropped.length} dropped while mining` : "all kept"} />
        <Stat label="Attempts" value={count(attempts)} note={plural(b.arms.length, "arm and condition", "arms and conditions")} />
        <Stat label="Runs" value={count(b.runs.length)} note={b.runs.length ? "with manifests" : "attempts predate run manifests"} />
        <Stat label="Grading" value={b.arms[0]?.tier ?? "test-verified"} note="by the repository's own tests" />
      </div>

      {b.arms.length === 0 ? (
        <Section title="Arms">
          <Empty title="No attempts yet" command={`mahoraga audit run ${b.repo ?? "<repo>"} --arm aider:qwen3.5:latest --until 07:30`}>
            Each attempt resets the task to its base, lets the agent edit, puts the tests back, and runs the full suite.
          </Empty>
        </Section>
      ) : (
        <Section title="Arms" aside={<span className="faint">Intervals count tasks, not attempts.</span>}>
          <ArmsTable arms={b.arms} />
          {b.degraded_note && <p className="note">{b.degraded_note}</p>}
        </Section>
      )}

      {b.arms.length > 0 && <Matrix b={b} />}

      {b.runs.length > 0 && (
        <Section title="Runs">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Run</th>
                <th scope="col">Started</th>
                <th scope="col">Length</th>
                <th scope="col" className="num">Attempts</th>
                <th scope="col">Stopped</th>
              </tr>
            </thead>
            <tbody>
              {b.runs.map((run) => (
                <tr key={run.run_id} className="row-link">
                  <th scope="row">
                    <Link to={`/runs/${encodeURIComponent(run.run_id)}`} className="row-link__a">
                      <Mono>{run.run_id}</Mono>
                    </Link>
                  </th>
                  <td>{date(run.started_at, true)}</td>
                  <td>{duration(run.started_at, run.ended_at)}</td>
                  <td className="num">{run.attempts ?? "—"}</td>
                  <td>{run.stopped ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}

      {(b.config || b.dropped.length > 0) && (
        <Section title="Setup">
          <KeyValues
            rows={[
              ["Test interpreter", b.config ? <Mono>{b.config.python}</Mono> : null],
              ["pytest arguments", b.config?.pytest_args.length ? <Mono>{b.config.pytest_args.join(" ")}</Mono> : "none"],
              ["Dropped while mining", b.dropped.length ? b.dropped.map((d) => `${shortSha(d.sha)} (${d.reason})`).join(", ") : "none"],
            ]}
          />
        </Section>
      )}
    </>
  );
}

function ArmsTable({ arms }: { arms: ArmSummary[] }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th scope="col">Arm</th>
            <th scope="col">Condition</th>
            <th scope="col" className="num">Resolved</th>
            <th scope="col">Rate, 95% interval</th>
            <th scope="col" className="num">Broke other code</th>
            <th scope="col" className="num">Median</th>
          </tr>
        </thead>
        <tbody>
          {arms.map((a) => (
            <tr key={`${a.arm}/${a.cond}`}>
              <th scope="row">
                <span className="mono cell-title">{a.arm}</span>
                <span className="cell-sub">{a.plan.text}</span>
              </th>
              <td>{a.cond}</td>
              <td className="num">
                {a.resolved}/{a.n}
                {a.repeats > 1 && (
                  <span className="cell-sub">
                    {a.tasks_solved_ever} ever, {a.tasks_solved_always} always
                  </span>
                )}
              </td>
              <td>
                <span className="rate-cell">
                  <IntervalBar rate={a.rate} />
                  <span className="mono">{rateText(a.rate)}</span>
                </span>
              </td>
              <td className="num">{a.broke_other_code}</td>
              <td className="num">{minutes(a.median_minutes)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type Sort = "history" | "size" | "solved";

function Matrix({ b }: { b: BenchDetail }) {
  const cols = useMemo(() => b.arms.map((a) => `${a.arm} / ${a.cond}`), [b.arms]);
  const [sort, setSort] = useState<Sort>("history");
  const present = useMemo(() => {
    const s = new Set<string>();
    Object.values(b.matrix).forEach((row) => Object.values(row).forEach((os) => os.forEach((o) => s.add(o))));
    return [...s];
  }, [b.matrix]);
  const tasks = useMemo(() => {
    const solved = (t: Task) => Object.values(b.matrix[t.sha] ?? {}).flat().filter((o) => o === "resolved").length;
    const list = [...b.tasks];
    if (sort === "size") list.sort((x, y) => y.gold_lines - x.gold_lines);
    if (sort === "solved") list.sort((x, y) => solved(y) - solved(x));
    return list;
  }, [b.tasks, b.matrix, sort]);

  return (
    <Section
      title="Tasks"
      aside={
        <Segmented
          label="Sort tasks"
          value={sort}
          onChange={setSort}
          options={[
            { value: "history", label: "History" },
            { value: "size", label: "Size" },
            { value: "solved", label: "Solved" },
          ]}
        />
      }
    >
      <OutcomeLegend present={present} />
      <div className="table-wrap">
        <table className="table matrix">
          <thead>
            <tr>
              <th scope="col">Commit</th>
              <th scope="col" className="num">Files</th>
              <th scope="col" className="num">Lines</th>
              {cols.map((c, i) => (
                <th scope="col" key={c} className="matrix__col">
                  <Tooltip content={c}>
                    <span tabIndex={0} className="matrix__colname">
                      {shortArm(c, i)}
                    </span>
                  </Tooltip>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {tasks.map((t) => (
              <tr key={t.sha}>
                <th scope="row" className="matrix__task">
                  <Tooltip
                    content={
                      <span className="tip-lines">
                        <strong>{t.subject}</strong>
                        {t.body && <span className="tip-body">{t.body}</span>}
                        <span>
                          {plural(t.f2p, "test")} must start passing, and {plural(t.p2p, "test")} must keep passing.
                        </span>
                      </span>
                    }
                  >
                    <span className="matrix__taskinner" tabIndex={0}>
                      <Mono>{shortSha(t.sha)}</Mono>
                      <span className="matrix__subject">{t.subject}</span>
                    </span>
                  </Tooltip>
                </th>
                <td className="num">{t.gold_src.length}</td>
                <td className="num">{t.gold_lines}</td>
                {cols.map((c) => (
                  <td key={c} className="matrix__cell">
                    <OutcomeCell outcomes={b.matrix[t.sha]?.[c]} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <th scope="row" colSpan={3}>
                Resolved
              </th>
              {b.arms.map((a) => (
                <td key={`${a.arm}/${a.cond}`} className="matrix__cell matrix__total">
                  {pct(a.rate.point)}
                </td>
              ))}
            </tr>
          </tfoot>
        </table>
      </div>

    </Section>
  );
}


/** Column heads have to fit a glyph column: model and condition only. */
function shortArm(col: string, i: number): string {
  const [arm, cond] = col.split(" / ");
  const model = arm.split(":").slice(1).join(":").replace(/:latest$/, "") || arm;
  return `${model.split(/[:@]/)[0]} · ${cond ?? i + 1}`;
}
