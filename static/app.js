// static/app.js

// ── Keyword sets for live complexity detection (mirrors backend tier classifier) ──
const CODE_KW = new Set([
  'code','function','bug','fix','implement','write','debug','error','test',
  'script','python','javascript','typescript','class','api','refactor','syntax',
  'compile','import','module','library','build','deploy','git','bash','shell',
  'html','css','sql','json','yaml','dockerfile','regex','algorithm','loop',
]);
const PLAN_KW = new Set([
  'plan','design','architecture','breakdown','analyze','strategy','roadmap',
  'structure','organize','outline','project','system','mvp','feature','spec',
  'requirements','diagram','flow','steps','tasks','schedule','milestone',
]);

// ── Metrics poll ─────────────────────────────────────────────────────────────
async function updateMetrics() {
  try {
    const res = await fetch('/api/metrics');
    if (!res.ok) return;
    const m = await res.json();
    const el = document.getElementById('metrics-text');
    if (!el) return;
    el.textContent = m.task_count === 0
      ? 'idle'
      : `${m.elapsed_s}s · ${m.tokens} tok · ${m.avg_throughput_tps} t/s`;
  } catch (_) {}
}
setInterval(updateMetrics, 5000);

// ── Backend switcher ─────────────────────────────────────────────────────────
async function initBackendSwitcher() {
  try {
    const res = await fetch('/settings/backend');
    if (!res.ok) return;
    const data = await res.json();
    setActiveBackend(data.active_backend);
  } catch (_) {}
}

function setActiveBackend(backend) {
  document.querySelectorAll('.backend-opt').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.backend === backend);
  });
}

document.querySelectorAll('.backend-opt').forEach(btn => {
  btn.addEventListener('click', async () => {
    try {
      await fetch('/settings/backend', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ active_backend: btn.dataset.backend }),
      });
      setActiveBackend(btn.dataset.backend);
    } catch (_) {}
  });
});

initBackendSwitcher();

// ── Progress bar ─────────────────────────────────────────────────────────────
function showProgressBar() {
  document.getElementById('progress-bar')?.classList.add('running');
}
function hideProgressBar() {
  document.getElementById('progress-bar')?.classList.remove('running');
}

// ── Routing bar ──────────────────────────────────────────────────────────────
let _routingTimer = null;

function showRoutingBar() {
  const bar  = document.getElementById('routing-bar');
  const text = document.getElementById('routing-text');
  if (!bar) return;
  if (text) text.textContent = 'Analyzing…';
  bar.classList.add('visible');
  clearTimeout(_routingTimer);
  _routingTimer = setTimeout(() => {
    if (text && bar.classList.contains('visible'))
      text.textContent = 'Routing to agent…';
  }, 700);
}

function hideRoutingBar() {
  clearTimeout(_routingTimer);
  document.getElementById('routing-bar')?.classList.remove('visible');
}

// ── Complexity chip ──────────────────────────────────────────────────────────
function updateComplexityChip(text) {
  const chip = document.getElementById('complexity-chip');
  if (!chip) return;
  const words = text.toLowerCase().split(/\W+/);
  const hasCode = words.some(w => CODE_KW.has(w));
  const hasPlan = words.some(w => PLAN_KW.has(w));

  if (hasCode) {
    chip.textContent = 'code';
    chip.className = 'complexity-chip type-code';
    chip.style.display = 'inline-block';
  } else if (hasPlan) {
    chip.textContent = 'plan';
    chip.className = 'complexity-chip type-plan';
    chip.style.display = 'inline-block';
  } else if (text.trim().length > 0) {
    chip.textContent = 'general';
    chip.className = 'complexity-chip type-general';
    chip.style.display = 'inline-block';
  } else {
    chip.style.display = 'none';
  }
}

// ── Main IIFE ────────────────────────────────────────────────────────────────
(() => {
  const input    = document.getElementById('user-input');
  const sendBtn  = document.getElementById('send-btn');
  const messages = document.getElementById('messages');
  const emptyEl  = document.getElementById('messages-empty');

  // ── Time formatter ───────────────────────────────────────────────────────
  function fmtTime() {
    return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  // ── Markdown renderer ─────────────────────────────────────────────────────
  function renderMarkdown(text) {
    // 1. Escape HTML
    let html = text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');

    // 2. Stash fenced code blocks (protect from other rules)
    const stash = [];
    html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
      const id = 'cb-' + Math.random().toString(36).slice(2, 9);
      const langBadge = `<span class="code-lang-badge">${lang || 'text'}</span>`;
      const copyBtn   = `<button class="code-copy-btn" data-target="${id}">Copy</button>`;
      stash.push(
        `<div class="code-block"><div class="code-header">${langBadge}${copyBtn}</div>` +
        `<pre id="${id}"><code>${code.trimEnd()}</code></pre></div>`
      );
      return `\x00S${stash.length - 1}\x00`;
    });

    // 3. Inline code
    html = html.replace(/`([^`\n]+)`/g, '<code>$1</code>');

    // 4. Headers
    html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
    html = html.replace(/^## (.+)$/gm,  '<h2>$1</h2>');
    html = html.replace(/^# (.+)$/gm,   '<h1>$1</h1>');

    // 5. Horizontal rule
    html = html.replace(/^-{3,}$/gm, '<hr>');

    // 6. Blockquote (> became &gt; after escaping)
    html = html.replace(/^&gt; (.+)$/gm, '<blockquote>$1</blockquote>');

    // 7. Bold / italic
    html = html.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
    html = html.replace(/\*\*(.+?)\*\*/g,      '<strong>$1</strong>');
    html = html.replace(/\*([^*\n]+)\*/g,       '<em>$1</em>');
    html = html.replace(/_([^_\n]+)_/g,         '<em>$1</em>');

    // 8. Unordered lists
    html = html.replace(/(?:^[*\-] .+(?:\n|$))+/gm, block => {
      const items = block.trimEnd().split('\n')
        .map(l => `<li>${l.replace(/^[*\-] /, '')}</li>`).join('');
      return `<ul>${items}</ul>`;
    });

    // 9. Ordered lists
    html = html.replace(/(?:^\d+\. .+(?:\n|$))+/gm, block => {
      const items = block.trimEnd().split('\n')
        .map(l => `<li>${l.replace(/^\d+\. /, '')}</li>`).join('');
      return `<ol>${items}</ol>`;
    });

    // 10. Paragraphs
    html = html.replace(/\n{2,}/g, '</p><p>');
    html = '<p>' + html + '</p>';
    html = html.replace(/\n/g, '<br>');

    // 11. Restore code blocks
    html = html.replace(/\x00S(\d+)\x00/g, (_, i) => stash[parseInt(i)]);

    // 12. Unwrap spurious <p> tags around block elements
    html = html
      .replace(/<p>(<(?:h[1-3]|ul|ol|hr|blockquote|div)[^>]*>)/g, '$1')
      .replace(/(<\/(?:h[1-3]|ul|ol|hr|blockquote|div)>)<\/p>/g,   '$1');

    return html;
  }

  // ── Empty state ──────────────────────────────────────────────────────────
  function hideEmptyState() {
    if (emptyEl) emptyEl.style.display = 'none';
  }

  // ── Suggestion chips ─────────────────────────────────────────────────────
  document.querySelectorAll('.suggestion-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      const msg = chip.dataset.msg;
      if (!msg) return;
      input.value = msg;
      input.dispatchEvent(new Event('input'));
      input.focus();
    });
  });

  // ── appendMessage ─────────────────────────────────────────────────────────
  function appendMessage(role, text, isStreaming = false) {
    hideEmptyState();
    const wrapper = document.createElement('div');
    wrapper.classList.add('message', role);

    if (role === 'assistant') {
      // Sender row
      const sender = document.createElement('div');
      sender.className = 'msg-sender';
      sender.innerHTML =
        `<div class="msg-avatar">M</div>` +
        `<span class="msg-role">Mahoraga</span>` +
        `<span class="msg-ts">${fmtTime()}</span>`;
      wrapper.appendChild(sender);
    }

    const bubble = document.createElement('div');
    bubble.classList.add('bubble');

    if (role === 'assistant') {
      bubble.innerHTML = isStreaming
        ? '<span class="cursor"></span>'
        : renderMarkdown(text);
    } else {
      bubble.textContent = text;
    }
    wrapper.appendChild(bubble);

    if (role === 'user') {
      const ts = document.createElement('div');
      ts.className = 'user-time';
      ts.textContent = fmtTime();
      wrapper.appendChild(ts);
    }

    if (role === 'assistant') {
      // Copy action (appears on hover via CSS)
      const actions = document.createElement('div');
      actions.className = 'msg-actions';
      const copyBtn = document.createElement('button');
      copyBtn.className = 'msg-action';
      copyBtn.textContent = '⧉ Copy';
      copyBtn.addEventListener('click', () => {
        navigator.clipboard.writeText(bubble.innerText).then(() => {
          copyBtn.textContent = '✓ Copied';
          setTimeout(() => { copyBtn.textContent = '⧉ Copy'; }, 1500);
        });
      });
      actions.appendChild(copyBtn);
      wrapper.appendChild(actions);
    }

    messages.appendChild(wrapper);
    messages.scrollTop = messages.scrollHeight;
    return bubble;
  }

  // ── finalizeAssistantBubble ──────────────────────────────────────────────
  function finalizeAssistantBubble(bubble, fullText) {
    bubble.innerHTML = renderMarkdown(fullText);
    // Wire copy buttons inside code blocks
    bubble.querySelectorAll('.code-copy-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const target = document.getElementById(btn.dataset.target);
        if (!target) return;
        navigator.clipboard.writeText(target.textContent).then(() => {
          btn.textContent = '✓ Copied';
          btn.classList.add('copied');
          setTimeout(() => {
            btn.textContent = 'Copy';
            btn.classList.remove('copied');
          }, 1500);
        });
      });
    });
    messages.scrollTop = messages.scrollHeight;
  }

  // ── Input auto-grow + complexity chip ────────────────────────────────────
  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 160) + 'px';
    sendBtn.disabled = !input.value.trim();
    updateComplexityChip(input.value);
  });

  // ── Enter / Shift+Enter ──────────────────────────────────────────────────
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submitMessage();
    }
  });

  // ── Submit ───────────────────────────────────────────────────────────────
  async function submitMessage() {
    const text = input.value.trim();
    if (!text || sendBtn.disabled) return;

    appendMessage('user', text);
    input.value = '';
    input.style.height = 'auto';
    sendBtn.disabled = true;

    // Hide complexity chip
    const chip = document.getElementById('complexity-chip');
    if (chip) chip.style.display = 'none';

    // Processing state
    showProgressBar();
    showRoutingBar();

    const assistantBubble = appendMessage('assistant', '', true);
    let fullText   = '';
    let firstChunk = true;

    try {
      const res = await fetch('/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, user_id: 'web-user' }),
      });

      if (!res.ok) {
        assistantBubble.textContent = `Error ${res.status}: ${res.statusText}`;
        return;
      }

      const reader  = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop();

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          let chunk;
          try   { chunk = JSON.parse(line.slice(6)); }
          catch { chunk = line.slice(6); }

          if (chunk === '[DONE]') break;
          if (typeof chunk === 'string' && chunk.startsWith('[ERROR]')) {
            assistantBubble.textContent = chunk;
            return;
          }
          if (chunk && typeof chunk === 'object' && chunk.type === 'metrics') {
            const el = document.getElementById('metrics-text');
            if (el) el.textContent = `${chunk.elapsed_s}s · ${chunk.tokens} tok · ${chunk.throughput_tps} t/s`;
            continue;
          }
          if (typeof chunk !== 'string') continue;

          // Hide routing bar on first real text chunk
          if (firstChunk) {
            firstChunk = false;
            hideRoutingBar();
          }

          fullText += chunk;
          const cursor = assistantBubble.querySelector('.cursor');
          if (cursor) {
            assistantBubble.textContent = fullText;
            assistantBubble.appendChild(cursor);
          } else {
            assistantBubble.textContent = fullText;
          }
          messages.scrollTop = messages.scrollHeight;
        }
      }

      finalizeAssistantBubble(assistantBubble, fullText);
      updateMetrics();

    } catch (err) {
      assistantBubble.textContent = `Network error: ${err.message}`;
    } finally {
      hideProgressBar();
      hideRoutingBar();
      sendBtn.disabled = false;
      input.focus();
      if (window.sidebarRefresh) window.sidebarRefresh();
    }
  }

  sendBtn.addEventListener('click', submitMessage);
})();
