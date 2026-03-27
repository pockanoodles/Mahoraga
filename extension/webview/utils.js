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
 * Escape HTML special chars in plain text segments (not code blocks).
 * @param {string} s
 */
function _escapeSegment(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/**
 * Convert markdown to HTML with syntax-highlighted code blocks.
 * Handles: fenced code, inline code, bold, italic, headers, bullets, newlines.
 * @param {string} text  Raw agent response text (NOT pre-escaped)
 * @param {object} hljs  highlight.js instance
 */
function renderMarkdown(text, hljs) {
  // 1. Extract fenced code blocks first so inner content is never escaped/processed
  const CODE_PLACEHOLDER = '\x00CODE\x00';
  const codeBlocks = [];
  let out = text.replace(/```(\w+)?\n?([\s\S]*?)```/g, (_match, lang, code) => {
    const trimmed = code.trim();
    const highlighted =
      lang && hljs.getLanguage(lang)
        ? hljs.highlight(trimmed, { language: lang }).value
        : hljs.highlightAuto(trimmed).value;
    codeBlocks.push(`<pre><code class="hljs">${highlighted}</code></pre>`);
    return CODE_PLACEHOLDER + (codeBlocks.length - 1) + '\x00';
  });

  // 2. Escape HTML in the remaining text
  out = _escapeSegment(out);

  // 3. Inline markdown (order matters: bold before italic)
  out = out
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');

  // 4. Block-level: headers and bullets (line by line)
  out = out
    .split('\n')
    .map((line) => {
      if (/^###\s/.test(line)) return `<h4>${line.slice(4)}</h4>`;
      if (/^##\s/.test(line))  return `<h3>${line.slice(3)}</h3>`;
      if (/^#\s/.test(line))   return `<h3>${line.slice(2)}</h3>`;
      if (/^[-*]\s/.test(line)) return `<li>${line.slice(2)}</li>`;
      return line;
    })
    .join('\n');

  // 5. Wrap consecutive <li> runs in <ul>
  out = out.replace(/(<li>.*<\/li>\n?)+/g, (m) => `<ul>${m}</ul>`);

  // 6. Convert remaining newlines to <br>
  out = out.replace(/\n/g, '<br>');

  // 7. Restore code blocks
  out = out.replace(new RegExp(CODE_PLACEHOLDER + '(\\d+)\x00', 'g'), (_m, i) => codeBlocks[+i]);

  return out;
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
