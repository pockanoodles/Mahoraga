// The log as an Atom feed, at /mahoraga/log.xml, so a reader can follow new
// results without the page counting who visits. It is rendered once at build
// time from the same entries the page shows (content/log.ts), newest first.
import { copy } from "@/content/copy";
import { LOG, UPDATED } from "@/content/log";
import { SITE } from "../_lib/meta";

export const dynamic = "force-static";

const PAGE = `${SITE}/mahoraga/`;

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

export function GET(): Response {
  const t = copy.en;
  const entries = LOG.map((e) =>
    [
      "  <entry>",
      `    <id>tag:kaitosoeno.com,2026:mahoraga/log/${esc(e.id)}</id>`,
      `    <title>${esc(`${t.log.kinds[e.kind]}: ${e.title.en}`)}</title>`,
      `    <link rel="alternate" type="text/html" href="${PAGE}#log-${esc(e.id)}"/>`,
      `    <updated>${e.date}T00:00:00Z</updated>`,
      `    <category term="${e.kind}"/>`,
      `    <summary>${esc(e.body.en)}</summary>`,
      ...e.receipts.map((r) => `    <link rel="related" href="${esc(r.href)}" title="${esc(r.label.en)}"/>`),
      "  </entry>",
    ].join("\n"),
  );
  const xml = [
    '<?xml version="1.0" encoding="utf-8"?>',
    '<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="en">',
    `  <id>${PAGE}</id>`,
    `  <title>${esc(t.log.feed)}</title>`,
    `  <subtitle>${esc(t.meta.description)}</subtitle>`,
    `  <link rel="alternate" type="text/html" href="${PAGE}#log"/>`,
    `  <link rel="self" type="application/atom+xml" href="${PAGE}log.xml"/>`,
    `  <updated>${UPDATED}T00:00:00Z</updated>`,
    "  <author><name>Kaito Soeno</name></author>",
    ...entries,
    "</feed>",
    "",
  ].join("\n");
  return new Response(xml, { headers: { "Content-Type": "application/atom+xml; charset=utf-8" } });
}
