// Appearance, the engine this app is talking to, the prices the verdict uses,
// and where data goes.
import { api } from "@/lib/api";
import { useEngine } from "@/lib/engine";
import { useTheme, type ThemeChoice } from "@/lib/theme";
import { useResource } from "@/lib/useResource";
import { Segmented } from "@/components/ui/controls";
import { ErrorNote, KeyValues, Mono, PageHeader, Section } from "@/components/ui/layout";

const REPO = "https://github.com/pockanoodles/Mahoraga";

export default function SettingsPage() {
  const [theme, setTheme] = useTheme();
  const { engine } = useEngine();
  const pricing = useResource("pricing", api.pricing);
  return (
    <main className="page">
      <PageHeader title="Settings" />

      <Section title="Appearance">
        <Segmented<ThemeChoice>
          label="Appearance"
          value={theme}
          onChange={setTheme}
          options={[
            { value: "system", label: "System" },
            { value: "light", label: "Light" },
            { value: "dark", label: "Dark" },
          ]}
        />
      </Section>

      <Section title="Engine">
        {engine && (
          <KeyValues
            rows={[
              ["Version", <Mono key="v">{engine.version}</Mono>],
              ["API", `v${engine.api}${engine.headless ? ", headless" : ""}`],
              ["Data", <Mono key="d">{engine.data_dir}</Mono>],
              ["Benches", <Mono key="b">{engine.bench_root}</Mono>],
              ["Ollama", engine.ollama.up ? `${engine.ollama.version ?? "running"} at ${engine.ollama.url}` : `not answering at ${engine.ollama.url}`],
              ["Python", engine.python],
              ["Platform", engine.platform],
            ]}
          />
        )}
      </Section>

      <Section title="Cloud prices">
        <p className="prose">
          Prices are dated observations with a source, and a plan whose price wasn't read from the vendor's own page is
          refused unless you supply what you pay.
        </p>
        {pricing.error && <ErrorNote error={pricing.error} />}
        {pricing.data && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Plan</th>
                  <th scope="col" className="num">Per month</th>
                  <th scope="col">Read</th>
                  <th scope="col">Note</th>
                </tr>
              </thead>
              <tbody>
                {pricing.data.plans.map((p) => (
                  <tr key={p.id}>
                    <th scope="row">
                      <span className="cell-title">{p.name}</span>
                      <a className="cell-sub link" href={p.source} target="_blank" rel="noreferrer">
                        {new URL(p.source).host}
                      </a>
                    </th>
                    <td className="num">{p.verified && p.usd_per_month != null ? `$${p.usd_per_month}` : <span className="faint">not verified</span>}</td>
                    <td>{p.as_of}</td>
                    <td className="cell-note">{p.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <Section title="Privacy">
        <p className="prose">
          The audit reads your repository, never writes to it, and sends nothing anywhere. On macOS its agents run in an
          operating-system sandbox that can write only to their own copy and reach only the local model server. The
          router is the one part that can use the cloud: it escalates a task to the Claude CLI only when the local judge
          rejects the local answer, at most 25 times a day, and <code>MAHORAGA_CASCADE=off</code> keeps every task
          here.{" "}
          <a className="link" href={`${REPO}/blob/main/DATAFLOW.md`} target="_blank" rel="noreferrer">
            Read the data flow
          </a>
          .
        </p>
      </Section>
    </main>
  );
}
