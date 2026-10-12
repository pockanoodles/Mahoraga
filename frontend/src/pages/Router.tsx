// The last stage. Once the verdict says which slice of work local can take,
// the router serves it: a local arm answers first, an execution gate and a
// local judge check the answer, and only rejected tasks escalate to the cloud.
import { api } from "@/lib/api";
import { count, pct, relative } from "@/lib/format";
import { useResource } from "@/lib/useResource";
import { Command, Empty, ErrorNote, Mono, PageHeader, Section, Stat } from "@/components/ui/layout";

function uptime(s: number): string {
  if (s < 3600) return `${Math.round(s / 60)} min`;
  if (s < 86_400) return `${Math.round(s / 3600)} h`;
  return `${Math.round(s / 86_400)} d`;
}

export default function RouterPage() {
  const health = useResource("health", api.health, { refreshMs: 10_000 });
  const stats = useResource("rstats", api.routingStats, { refreshMs: 10_000 });
  const agents = useResource("ragents", api.routingAgents, { refreshMs: 20_000 });
  const decisions = useResource("decisions", (s) => api.decisions(25, s), { refreshMs: 10_000 });
  const err = health.error ?? stats.error ?? agents.error ?? decisions.error;
  const h = health.data;
  const st = stats.data?.stats;
  return (
    <main className="page">
      <PageHeader
        eyebrow="Serve"
        title="Router"
        sub="Local first, checked by a free local judge, and escalated to the cloud only when the answer is rejected."
      />
      {err && <ErrorNote error={err} />}
      {h && st && (
        <div className="facts facts--page">
          <Stat label="Arms online" value={`${h.agents_online} of ${h.agents_registered}`} note={`strategy ${h.strategy}`} />
          <Stat label="Decisions logged" value={count(h.total_decisions)} note={`up ${uptime(h.uptime_s)}`} />
          <Stat label="Succeeded" value={st.total ? pct(st.success_rate) : "—"} note={`${count(st.successes)} of ${count(st.total)}`} />
          <Stat label="Average latency" value={st.total ? `${st.avg_latency.toFixed(1)} s` : "—"} note={`$${st.total_cost.toFixed(2)} spent in total`} />
        </div>
      )}

      <Section title="Arms">
        {agents.data && agents.data.agents.length === 0 && <p className="prose faint">No arms are registered. They come from agents.yaml.</p>}
        {agents.data && agents.data.agents.length > 0 && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Arm</th>
                  <th scope="col">Status</th>
                  <th scope="col" className="num">Decisions</th>
                  <th scope="col" className="num">Succeeded</th>
                  <th scope="col" className="num">Latency</th>
                  <th scope="col" className="num">Cost</th>
                </tr>
              </thead>
              <tbody>
                {agents.data.agents.map((a) => (
                  <tr key={a.name}>
                    <th scope="row">
                      <span className="mono cell-title">{a.name}</span>
                      <span className="cell-sub">{a.capabilities.join(", ")}</span>
                    </th>
                    <td>
                      <span className="list__line">
                        <span className={`dot ${a.healthy ? "dot--on" : "dot--off"}`} />
                        {a.healthy ? "healthy" : a.detail ?? "unavailable"}
                      </span>
                    </td>
                    <td className="num">{count(a.total)}</td>
                    <td className="num">{a.total ? pct(a.success_rate) : "—"}</td>
                    <td className="num">{a.total ? `${a.avg_latency.toFixed(1)} s` : "—"}</td>
                    <td className="num">${a.total_cost.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <Section title="Recent decisions" aside={decisions.data && <span className="faint">{count(decisions.data.total_available)} in the log</span>}>
        {decisions.data && decisions.data.decisions.length === 0 && (
          <Empty
            title="No decisions yet"
            command={`curl -s localhost:8000/api/task -H 'Content-Type: application/json' -d '{"prompt": "Write a function that reverses a linked list."}'`}
          >
            Send the router a task over the HTTP API, or from any MCP client.
          </Empty>
        )}
        {decisions.data && decisions.data.decisions.length > 0 && (
          <ul className="list" data-refreshing={decisions.refreshing || undefined}>
            {decisions.data.decisions.map((d) => (
              <li key={d.id} className="list__row list__row--decision">
                <span className={`dot ${d.success == null ? "dot--idle" : d.success ? "dot--on" : "dot--off"}`} aria-label={d.success == null ? "pending" : d.success ? "succeeded" : "failed"} />
                <span className="decision__goal">{d.task_goal}</span>
                <Mono>{d.selected_agent}</Mono>
                <span className="faint">{relative(d.timestamp)}</span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Connect an agent">
        <p className="prose">
          The router speaks MCP over stdio, so Claude Code or any MCP client can hand it tasks. The bandit that picks
          between local arms is architecture here, not a result, since in head-to-head evaluation it never beat
          round-robin.
        </p>
        <Command>python -m backend.mcp.server</Command>
      </Section>
    </main>
  );
}
