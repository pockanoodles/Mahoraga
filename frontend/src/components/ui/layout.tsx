// Page structure. Every screen is a large title, then sections separated by
// space (Design 03: outer gaps at least three times inner gaps), with hairlines
// only where rows need them.
import type { ReactNode } from "react";
import { EngineError } from "@/lib/api";
import { CopyButton } from "./controls";

export function PageHeader({
  eyebrow,
  title,
  sub,
  actions,
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  sub?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="page-head">
      <div className="page-head__text">
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h1 className="large-title">{title}</h1>
        {sub && <p className="page-head__sub">{sub}</p>}
      </div>
      {actions && <div className="page-head__actions">{actions}</div>}
    </header>
  );
}

export function Section({
  title,
  aside,
  children,
  id,
}: {
  title?: ReactNode;
  aside?: ReactNode;
  children: ReactNode;
  id?: string;
}) {
  return (
    <section className="section" id={id} aria-labelledby={id && title ? `${id}-title` : undefined}>
      {(title || aside) && (
        <div className="section__head">
          {title && (
            <h2 className="section__title" id={id ? `${id}-title` : undefined}>
              {title}
            </h2>
          )}
          {aside && <div className="section__aside">{aside}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, note }: { label: string; value: ReactNode; note?: ReactNode }) {
  return (
    <div className="stat">
      <p className="stat__label">{label}</p>
      <p className="stat__value">{value}</p>
      {note && <p className="stat__note">{note}</p>}
    </div>
  );
}

/** A command the reader can run, with a copy button. */
export function Command({ children }: { children: string }) {
  return (
    <div className="command">
      <code className="command__text">
        <span className="command__prompt" aria-hidden>
          $
        </span>
        {children}
      </code>
      <CopyButton text={children} />
    </div>
  );
}

/** An honest empty state: what's missing, and the one command that fills it. */
export function Empty({ title, children, command }: { title: string; children?: ReactNode; command?: string }) {
  return (
    <div className="empty">
      <p className="empty__title">{title}</p>
      {children && <p className="empty__body">{children}</p>}
      {command && <Command>{command}</Command>}
    </div>
  );
}

export function ErrorNote({ error, onRetry }: { error: EngineError; onRetry?: () => void }) {
  return (
    <div className="error-note" role="alert">
      <p>
        {error.unreachable
          ? "The engine stopped answering. It may have been shut down."
          : error.message}
      </p>
      {onRetry && (
        <button type="button" className="btn btn--ghost btn--sm" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function KeyValues({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="kv">
      {rows.map(([k, v]) => (
        <div className="kv__row" key={k}>
          <dt>{k}</dt>
          <dd>{v ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Mono({ children }: { children: ReactNode }) {
  return <span className="mono">{children}</span>;
}
