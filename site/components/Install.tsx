// The install channels, one hairline row each (Design 03's list of points).
// A channel that isn't published yet shows its command dimmed with a plain
// label, so nothing on the page invites a command that would fail today.
import type { Dict, Locale } from "@/content/copy";
import { CHANNELS, FIRST_STEPS } from "@/content/channels";
import { CopyButton } from "./CopyButton";

export function Install({ t, locale }: { t: Dict["install"]; locale: Locale }) {
  return (
    <div className="install">
      <ul className="install__list">
        {CHANNELS.map((c) => (
          <li key={c.id} className="install__row" data-live={c.live || undefined}>
            <div className="install__name">
              <span>{c.name}</span>
              <span className="install__platforms">{c.platforms[locale]}</span>
            </div>
            <code className="install__cmd">{c.cmd}</code>
            <div className="install__action">
              {c.live ? <CopyButton text={c.cmd} label={t.copy} done={t.copied} /> : <span className="install__pending">{t.pending}</span>}
            </div>
          </li>
        ))}
      </ul>
      <div className="install__then">
        <p className="install__then-title">{t.then}</p>
        <ol className="install__steps">
          {FIRST_STEPS.map((cmd, i) => (
            <li key={cmd}>
              <span className="install__step-n">{String(i + 1).padStart(2, "0")}</span>
              <span className="install__step-text">{t.steps[i]}</span>
              <code>{cmd}</code>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
