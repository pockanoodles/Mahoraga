// Running the engine on your own repository, in three numbered steps. Only an
// install channel that works today offers its command. The ones still to come
// are named in one line, the first release's and the later ones apart, so the
// page never invites a command that would fail. Flipping a channel to live in
// content/channels.ts moves it from that line into the first step.
import type { Dict, Locale } from "@/content/copy";
import { CHANNELS, FIRST_STEPS } from "@/content/channels";
import { CopyButton } from "./CopyButton";

function names(list: string[], locale: Locale): string {
  if (locale === "ja") return list.length === 2 ? `${list[0]} と ${list[1]}` : list.join("、");
  return list.length < 2 ? list.join("") : `${list.slice(0, -1).join(", ")} and ${list[list.length - 1]}`;
}

export function Run({ t, locale }: { t: Dict["run"]; locale: Locale }) {
  const live = CHANNELS.filter((c) => c.live);
  const waiting = CHANNELS.filter((c) => !c.live);
  const atLaunch = waiting.filter((c) => c.launch).map((c) => c.name);
  const later = waiting.filter((c) => !c.launch).map((c) => c.name);
  const pending = [
    atLaunch.length > 0 && t.pending(names(atLaunch, locale)),
    later.length > 0 && t.later(names(later, locale), later.length),
  ].filter(Boolean);
  // Install (every live channel), then what to run after it: one step each, and t.steps names them.
  const steps = [live.map((c) => ({ key: c.id, cmd: c.cmd })), ...FIRST_STEPS.map((cmd) => [{ key: cmd, cmd }])];

  return (
    <div className="run">
      <ol className="run__steps">
        {steps.map((cmds, i) => (
          <li key={i} className="run__step">
            <span className="run__n">{String(i + 1).padStart(2, "0")}</span>
            <div className="run__what">
              <p className="run__text">{t.steps[i]}</p>
              {cmds.map((c) => (
                <div key={c.key} className="run__cmd">
                  <code>{c.cmd}</code>
                  <CopyButton text={c.cmd} label={t.copy} done={t.copied} />
                </div>
              ))}
            </div>
          </li>
        ))}
      </ol>
      <p className="run__morning">{t.morning}</p>
      {pending.length > 0 && <p className="small run__pending">{pending.join(locale === "ja" ? "" : " ")}</p>}
    </div>
  );
}
