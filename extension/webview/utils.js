/**
 * Pure utility functions shared between the webview and tests.
 * Exports via module.exports when running in Node (tests).
 * Exposes as globals when running in the browser (webview).
 */

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

/**
 * Convert plain text with markdown code fences into HTML with syntax highlighting.
 * @param {string} text  Raw agent response text
 * @param {object} hljs  highlight.js instance
 */
function renderMarkdown(text, hljs) {
  return text.replace(
    /```(\w+)?\n([\s\S]*?)```/g,
    (_match, lang, code) => {
      const trimmed = code.trim();
      const highlighted =
        lang && hljs.getLanguage(lang)
          ? hljs.highlight(trimmed, { language: lang }).value
          : hljs.highlightAuto(trimmed).value;
      return `<pre><code class="hljs">${highlighted}</code></pre>`;
    }
  );
}

/**
 * Shorten a full Ollama model name to a compact badge label.
 * "qwen2.5-coder:7b"  → "7b"
 * "qwen3:14b"         → "qwen3-14b"
 */
function formatModelBadge(model) {
  return model
    .replace('qwen2.5-coder:', '')
    .replace('qwen3:', 'qwen3-')
    .replace(':latest', '');
}

/**
 * Format a tool call into a short status string.
 * "read_file" + {path: "app.py"} → "read file app.py..."
 */
function formatToolStatus(tool, args) {
  const SKIP_KEYS = new Set(['content', 'tool', 'type']);
  const argStr = Object.entries(args || {})
    .filter(([k]) => !SKIP_KEYS.has(k))
    .map(([, v]) => String(v))
    .join(' ');
  const label = tool.replace(/_/g, ' ');
  return argStr ? `${label} ${argStr}...` : `${label}...`;
}

// Export for Node (Jest tests); expose as globals in browser (webview).
if (typeof module !== 'undefined') {
  module.exports = { escapeHtml, renderMarkdown, formatModelBadge, formatToolStatus };
}
