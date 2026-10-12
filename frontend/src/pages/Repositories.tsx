// Every repository the audit has mined, and what each one has measured so far.
import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";
import { api } from "@/lib/api";
import { useEngine } from "@/lib/engine";
import { count, relative } from "@/lib/format";
import { useResource } from "@/lib/useResource";
import { Empty, ErrorNote, Mono, PageHeader } from "@/components/ui/layout";

export default function RepositoriesPage() {
  const r = useResource("benches", api.benches, { refreshMs: 15_000 });
  // A demo bench was just copied into place, so its file times say nothing about the probe.
  const { engine } = useEngine();
  const benches = r.data?.benches ?? [];
  return (
    <main className="page">
      <PageHeader
        eyebrow="Audit"
        title="Repositories"
        sub="Each repository's own commits become tasks: the fix is removed, the tests stay, and an attempt counts only when they pass."
      />
      {r.error && <ErrorNote error={r.error} onRetry={r.reload} />}
      {r.data && benches.length === 0 && (
        <Empty title="No repositories yet" command="mahoraga audit mine ~/code/your-repo">
          Mining reads a repository's history and keeps every commit whose own tests fail before it and pass after it.
          It only reads the repository, and it works in a copy.
        </Empty>
      )}
      {benches.length > 0 && (
        <div className="table-wrap" data-refreshing={r.refreshing || undefined}>
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Repository</th>
                <th scope="col" className="num">Tasks</th>
                <th scope="col" className="num">Attempts</th>
                <th scope="col">Arms</th>
                <th scope="col">Last activity</th>
                <th scope="col" aria-label="Open" />
              </tr>
            </thead>
            <tbody>
              {benches.map((b) => (
                <tr key={b.id} className="row-link">
                  <th scope="row">
                    <Link to={`/repositories/${encodeURIComponent(b.id)}`} className="row-link__a">
                      <span className="cell-title">{b.name}</span>
                      <span className="cell-sub">{b.repo ? <Mono>{b.repo}</Mono> : <Mono>{b.id}</Mono>}</span>
                    </Link>
                  </th>
                  <td className="num">
                    {count(b.tasks)}
                    {b.dropped > 0 && <span className="cell-sub">{count(b.dropped)} dropped</span>}
                  </td>
                  <td className="num">
                    {count(b.attempts)}
                    {b.degraded > 0 && <span className="cell-sub">{count(b.degraded)} degraded</span>}
                  </td>
                  <td>
                    {b.arms.length ? (
                      <span className="chips">
                        {b.arms.map((a) => (
                          <span key={a} className="chip mono">
                            {a}
                          </span>
                        ))}
                      </span>
                    ) : (
                      <span className="faint">Not run yet</span>
                    )}
                  </td>
                  <td>{engine?.demo ? <span className="faint">recorded run</span> : relative(b.last_activity)}</td>
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
