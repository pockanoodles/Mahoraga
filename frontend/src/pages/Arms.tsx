// The arms: an agent driving a local model. What each has measured across
// every repository, and what this machine has ready to run.
import { api } from "@/lib/api";
import { count, rateText } from "@/lib/format";
import { useResource } from "@/lib/useResource";
import { IntervalBar } from "@/components/data/IntervalBar";
import { Command, ErrorNote, Mono, PageHeader, Section } from "@/components/ui/layout";

// From each project's own install docs (aider.chat/docs/install.html; the
// opencode README's official Homebrew formula), checked 2026-10-12.
const INSTALL: Record<string, string> = {
  aider: "uv tool install --force --python python3.12 --with pip aider-chat@latest",
  opencode: "brew install opencode",
};

export default function ArmsPage() {
  const r = useResource("arms", api.arms, { refreshMs: 20_000 });
  const d = r.data;
  return (
    <main className="page">
      <PageHeader
        eyebrow="Audit"
        title="Arms"
        sub="An arm is an agent driving a local model, named agent:model. Rates pool every repository, one task at a time."
      />
      {r.error && <ErrorNote error={r.error} onRetry={r.reload} />}
      {d && (
        <>
          <Section title="Measured">
            {d.arms.length === 0 ? (
              <p className="prose faint">No arm has attempted a task yet.</p>
            ) : (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th scope="col">Arm</th>
                      <th scope="col">Conditions</th>
                      <th scope="col" className="num">Repositories</th>
                      <th scope="col" className="num">Attempts</th>
                      <th scope="col">Rate, 95% interval</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.arms.map((a) => (
                      <tr key={a.arm}>
                        <th scope="row">
                          <span className="mono cell-title">{a.arm}</span>
                          <span className="cell-sub">
                            {a.agent} driving {a.model}
                          </span>
                        </th>
                        <td>{a.conds.join(", ")}</td>
                        <td className="num">{a.benches}</td>
                        <td className="num">{count(a.attempts)}</td>
                        <td>
                          {a.rate ? (
                            <span className="rate-cell">
                              <IntervalBar rate={a.rate} />
                              <span className="mono">{rateText(a.rate)}</span>
                            </span>
                          ) : (
                            <span className="faint">pooled only within one condition</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Section>

          <div className="two-up">
            <Section title="Local models">
              {d.models.length === 0 ? (
                <div className="stack">
                  <p className="prose faint">Ollama isn't answering, or has no models pulled.</p>
                  <Command>ollama pull qwen3.5:latest</Command>
                </div>
              ) : (
                <ul className="list">
                  {d.models.map((m) => (
                    <li key={m.name} className="list__row">
                      <Mono>{m.name}</Mono>
                      <span className="faint mono">{m.digest ? m.digest.slice(0, 12) : ""}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Section>
            <Section title="Agents">
              <ul className="list">
                {d.agents.map((a) => (
                  <li key={a.name} className="list__row list__row--stack">
                    <span className="list__line">
                      <span className={`dot ${a.on_path ? "dot--on" : "dot--off"}`} />
                      <Mono>{a.name}</Mono>
                      <span className="faint">{a.on_path ? "on PATH" : "not installed"}</span>
                    </span>
                    {!a.on_path && INSTALL[a.name] && <Command>{INSTALL[a.name]}</Command>}
                  </li>
                ))}
              </ul>
            </Section>
          </div>
        </>
      )}
    </main>
  );
}
