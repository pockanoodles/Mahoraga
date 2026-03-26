const { escapeHtml, renderMarkdown, formatModelBadge, formatToolStatus } = require('../webview/utils.js');

const mockHljs = {
  getLanguage: (lang) => lang === 'python' || lang === 'javascript',
  highlight: (code, opts) => ({ value: `<highlighted>${code}</highlighted>` }),
  highlightAuto: (code) => ({ value: `<auto>${code}</auto>` }),
};

// --- escapeHtml ---

test('escapeHtml escapes angle brackets', () => {
  expect(escapeHtml('<div>')).toBe('&lt;div&gt;');
});

test('escapeHtml escapes ampersands', () => {
  expect(escapeHtml('a & b')).toBe('a &amp; b');
});

test('escapeHtml leaves plain text unchanged', () => {
  expect(escapeHtml('hello world')).toBe('hello world');
});

// --- renderMarkdown ---

test('renderMarkdown renders python code block with syntax highlighting', () => {
  const text = 'Here:\n```python\ndef foo(): pass\n```';
  const result = renderMarkdown(text, mockHljs);
  expect(result).toContain('<pre><code class="hljs">');
  expect(result).toContain('<highlighted>def foo(): pass</highlighted>');
});

test('renderMarkdown renders unknown language with auto-highlighting', () => {
  const text = '```rust\nfn main() {}\n```';
  const result = renderMarkdown(text, mockHljs);
  expect(result).toContain('<auto>fn main() {}</auto>');
});

test('renderMarkdown leaves plain text without code blocks unchanged', () => {
  const text = 'no code here';
  expect(renderMarkdown(text, mockHljs)).toBe('no code here');
});

test('renderMarkdown handles code block without language tag', () => {
  const text = '```\nsome code\n```';
  const result = renderMarkdown(text, mockHljs);
  expect(result).toContain('<auto>some code</auto>');
});

// --- formatModelBadge ---

test('formatModelBadge shortens qwen2.5-coder:7b', () => {
  expect(formatModelBadge('qwen2.5-coder:7b')).toBe('7b');
});

test('formatModelBadge shortens qwen2.5-coder:14b', () => {
  expect(formatModelBadge('qwen2.5-coder:14b')).toBe('14b');
});

test('formatModelBadge formats qwen3 names', () => {
  expect(formatModelBadge('qwen3:14b')).toBe('qwen3-14b');
});

// --- formatToolStatus ---

test('formatToolStatus formats read_file with path', () => {
  const result = formatToolStatus('read_file', { path: 'app.py' });
  expect(result).toBe('read file app.py...');
});

test('formatToolStatus excludes content, tool, and type keys', () => {
  const result = formatToolStatus('write_file', {
    path: 'out.py',
    content: 'x'.repeat(200),
    tool: 'write_file',
    type: 'tool_call',
  });
  expect(result).toBe('write file out.py...');
});

test('formatToolStatus formats run_bash with command', () => {
  const result = formatToolStatus('run_bash', { command: 'pytest tests/' });
  expect(result).toBe('run bash pytest tests/...');
});

test('formatToolStatus handles list_dir with just path', () => {
  const result = formatToolStatus('list_dir', { path: 'src/' });
  expect(result).toBe('list dir src/...');
});
