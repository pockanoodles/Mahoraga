// Tiny markdown renderer — ported from static/app.js.
// Handles fenced code blocks, inline code, bold, italic, paragraphs, line breaks.
// Output is trusted for display because all user/LLM content is HTML-escaped first.

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function renderMarkdown(text: string): string {
  let html = escapeHtml(text);

  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_m, _lang, code: string) => {
    const id = "cb-" + Math.random().toString(36).slice(2, 7);
    return `<div class="code-block-wrapper"><button class="copy-btn" data-target="${id}">Copy</button><pre id="${id}"><code>${code.replace(/\s+$/, "")}</code></pre></div>`;
  });

  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/_(.+?)_/g, "<em>$1</em>");
  html = html.replace(/\n{2,}/g, "</p><p>");
  html = "<p>" + html + "</p>";
  html = html.replace(/\n/g, "<br>");
  return html;
}

export function wireCopyButtons(root: HTMLElement): void {
  root.querySelectorAll<HTMLButtonElement>(".copy-btn").forEach((btn) => {
    if (btn.dataset.bound === "1") return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", () => {
      const target = document.getElementById(btn.dataset.target || "");
      if (!target) return;
      void navigator.clipboard.writeText(target.textContent || "").then(() => {
        const original = btn.textContent;
        btn.textContent = "Copied!";
        window.setTimeout(() => {
          btn.textContent = original;
        }, 1500);
      });
    });
  });
}
