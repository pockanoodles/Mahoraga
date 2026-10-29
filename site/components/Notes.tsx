// A page's footnotes, and the export and commit its numbers were read from.
import type { Dict } from "@/content/copy";
import { REPO_URL, data } from "@/lib/data";

export function Notes({ t, notes }: { t: Dict; notes: string[] }) {
  return (
    <section className="notes" aria-labelledby="notes-title">
      <h2 id="notes-title" className="notes__title">
        {t.footnotesTitle}
      </h2>
      {notes.length > 0 && (
        <ol>
          {notes.map((text, i) => (
            <li key={i} id={`note-${i + 1}`}>
              {text}
            </li>
          ))}
        </ol>
      )}
      <p className="notes__source">
        {t.notes.source[0]} <code>docs/site/site.json</code>
        {t.notes.source[1]}
        <a className="textlink" href={`${REPO_URL}/commit/${data.source.commit}`} target="_blank" rel="noreferrer">
          <code>{data.source.commit}</code>
        </a>
        {t.notes.source[2]}
      </p>
    </section>
  );
}
