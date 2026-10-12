// Every run, newest first. A run is one sitting of attempts, usually a night,
// and its manifest records everything a number from it depends on.
import { Link, useParams } from "react-router-dom";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { api } from "@/lib/api";
import { count, date, duration, seconds, shortSha } from "@/lib/format";
import type { RunDetail } from "@/lib/types";
import { useResource } from "@/lib/useResource";
import { OutcomeGlyph } from "@/components/data/Outcome";
import { Empty, ErrorNote, KeyValues, Mono, PageHeader, Section, Stat } from "@/components/ui/layout";
import { outcomeInfo } from "@/lib/outcomes";

export function RunsPage() {
  const r = useResource("runs", api.runs, { refreshMs: 15_000 });
  const runs = r.data?.runs ?? [];
  return (
    <main className="page">
      <PageHeader
        eyebrow="Audit"
        title="Runs"
        sub="Each run writes a manifest before its first attempt: the machine, the model digests, the agent versions, the harness commit and the exact prompts."
      />
      {r.error && <ErrorNote error={r.error} onRetry={r.reload} />}
      {r.data && runs.length === 0 && (
        <Empty title="No runs recorded" command="mahoraga audit doctor ~/code/your-repo --arm aider:qwen3.5:latest">
          Runs appear here once a run starts. Doctor checks the bench and estimates how many nights an answer takes.
        </Empty>
      )}
      {runs.length > 0 && (
        <div className="table-wrap" data-refreshing={r.refreshing || undefined}>
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Run</th>
                <th scope="col">Repository</th>
                <th scope="col">Started</th>
                <th scope="col">Length</th>
                <th scope="col" className="num">Attempts</th>
                <th scope="col">Stopped</th>
                <th scope="col">Machine</th>
                <th scope="col" aria-label="Open" />
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.run_id} className="row-link">
                  <th scope="row">
                    <Link to={`/runs/${encodeURIComponent(run.run_id)}`} className="row-link__a">
                      <Mono>{run.run_id}</Mono>
                      <span className="cell-sub">{run.arms.join(", ")}</span>
                    </Link>
                  </th>
                  <td>{run.repo_name}</td>
                  <td>{date(run.started_at, true)}</td>
                  <td>{duration(run.started_at, run.ended_at)}</td>
                  <td className="num">{run.attempts == null ? "—" : count(run.attempts)}</td>
                  <td>{run.stopped ?? (run.ended_at ? "—" : "running")}</td>
                  <td>{machine(run.machine)}</td>
                  <td className="row-link__chev">
                    <ChevronRight size={16} aria-hidden />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}

function machine(m: RunDetail["run"]["machine"]): string {
  if (!m.model && !m.memory_gb) return "—";
  return [m.model, m.memory_gb ? `${m.memory_gb} GB` : null].filter(Boolean).join(", ");
}

export function RunPage() {
  const { id = "" } = useParams();
  const r = useResource(`run:${id}`, (s) => api.run(id, s));
  const d = r.data;
  return (
    <main className="page">
      <Link to="/runs" className="back">
        <ChevronLeft size={16} aria-hidden /> Runs
      </Link>
      {r.error && <ErrorNote error={r.error} onRetry={r.reload} />}
      {d && <Run d={d} />}
    </main>
  );
}

function Run({ d }: { d: RunDetail }) {
  const m = d.manifest as Record<string, any>;
  const resolved = d.attempts.filter((a) => a.resolved).length;
  const digests: Record<string, string> = m.models?.digests ?? {};
  return (
    <>
      <PageHeader eyebrow={d.run.repo_name} title={<Mono>{d.run.run_id}</Mono>} sub={d.run.arms.join(", ")} />
      <div className="facts facts--page">
        <Stat label="Attempts" value={count(d.attempts.length)} note={`${resolved} resolved`} />
        <Stat label="Length" value={duration(d.run.started_at, d.run.ended_at)} note={date(d.run.started_at, true)} />
        <Stat label="Stopped" value={d.run.stopped ?? "—"} note={d.run.ended_at ? date(d.run.ended_at, true) : "still running"} />
        <Stat label="Machine" value={machine(d.run.machine)} note={m.machine?.power?.on_ac === false ? "on battery" : "on AC"} />
      </div>

      <div className="two-up">
        <Section title="Machine">
          <KeyValues
            rows={[
              ["Model", d.run.machine.model],
              ["Memory", d.run.machine.memory_gb ? `${d.run.machine.memory_gb} GB` : null],
              ["CPU", m.machine?.cpu ?? null],
              ["OS", d.run.machine.os],
              ["Python", m.machine?.python ?? null],
            ]}
          />
        </Section>
        <Section title="Models and agents">
          <KeyValues
            rows={[
              ["Ollama", d.run.ollama],
              ...Object.entries(digests).map(([k, v]) => [k, <Mono key={k}>{String(v).slice(0, 19)}</Mono>] as [string, React.ReactNode]),
              ...Object.entries((m.agents ?? {}) as Record<string, string>).map(([k, v]) => [k, v] as [string, React.ReactNode]),
            ]}
          />
        </Section>
        <Section title="Harness">
          <KeyValues
            rows={[
              ["Commit", d.run.harness.commit ? <Mono>{shortSha(d.run.harness.commit)}</Mono> : null],
              ["Working tree", d.run.harness.dirty == null ? null : d.run.harness.dirty ? "had local changes" : "clean"],
              ["Tasks", m.tasks?.n ?? null],
              ["Prompts hash", m.tasks?.prompts_sha256 ? <Mono>{String(m.tasks.prompts_sha256).slice(0, 16)}</Mono> : null],
            ]}
          />
        </Section>
        <Section title="Settings">
          <KeyValues rows={Object.entries((m.settings ?? {}) as Record<string, unknown>).map(([k, v]) => [k, <Mono key={k}>{JSON.stringify(v)}</Mono>])} />
        </Section>
      </div>

      <Section title="Attempts">
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Task</th>
                <th scope="col">Arm</th>
                <th scope="col">Outcome</th>
                <th scope="col" className="num">Target tests</th>
                <th scope="col" className="num">Regressions</th>
                <th scope="col" className="num">Time</th>
              </tr>
            </thead>
            <tbody>
              {d.attempts.map((a) => (
                <tr key={`${a.arm}/${a.cond}/${a.sha}/${a.rep}`}>
                  <th scope="row">
                    <Mono>{shortSha(a.sha)}</Mono>
                    <span className="cell-sub">{a.subject}</span>
                  </th>
                  <td>
                    <span className="mono">{a.arm}</span>
                    <span className="cell-sub">{a.cond}</span>
                  </td>
                  <td>
                    <span className="outcome-label">
                      <OutcomeGlyph outcome={a.outcome} />
                      {outcomeInfo(a.outcome).label}
                    </span>
                  </td>
                  <td className="num">
                    {a.f2p_pass}/{a.f2p_total}
                  </td>
                  <td className="num">{a.suite_crashed ? "suite crashed" : a.regressions}</td>
                  <td className="num">{a.timed_out ? "timed out" : seconds(a.secs)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
    </>
  );
}
