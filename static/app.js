// static/app.js

// ── Inference metrics ────────────────────────────────────────────────────────
async function updateMetrics() {
  try {
    const res = await fetch('/api/metrics');
    if (!res.ok) return;
    const m = await res.json();
    const el = document.getElementById('metrics-text');
    if (!el) return;
    if (m.task_count === 0) {
      el.textContent = 'Session: idle';
      return;
    }
    el.textContent = `Session: ${m.elapsed_s}s · ${m.tokens} tok · avg ${m.avg_throughput_tps} t/s`;
  } catch (_) { /* non-critical */ }
}
setInterval(updateMetrics, 5000);

(() => {
  const input    = document.getElementById('user-input');
  const sendBtn  = document.getElementById('send-btn');
  const messages = document.getElementById('messages');
  const emptyEl  = document.getElementById('messages-empty');

  // ── Timestamp formatter ──────────────────────────────────────────────────

  function formatTime() {
    return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  // ── Markdown renderer ────────────────────────────────────────────────────

  function renderMarkdown(text) {
    // 1. Escape HTML
    let html = text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');

    // 2. Stash fenced code blocks so later rules don't mangle them
    const stash = [];
    html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
      const id = 'cb-' + Math.random().toString(36).slice(2, 8);
      const langLabel = lang
        ? `<div class="code-block-header"><span class="code-lang">${lang}</span><button class="copy-btn" data-target="${id}">Copy</button></div>`
        : `<div class="code-block-header"><span class="code-lang"></span><button class="copy-btn" data-target="${id}">Copy</button></div>`;
      stash.push(`<div class="code-block-wrapper">${langLabel}<pre id="${id}"><code>${code.trimEnd()}</code></pre></div>`);
      return `\x00STASH${stash.length - 1}\x00`;
    });

    // 3. Inline code
    html = html.replace(/`([^`\n]+)`/g, '<code>$1</code>');

    // 4. Headers
    html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
    html = html.replace(/^## (.+)$/gm,  '<h2>$1</h2>');
    html = html.replace(/^# (.+)$/gm,   '<h1>$1</h1>');

    // 5. Horizontal rule
    html = html.replace(/^-{3,}$/gm, '<hr>');

    // 6. Blockquote (after HTML escaping, > becomes &gt;)
    html = html.replace(/^&gt; (.+)$/gm, '<blockquote>$1</blockquote>');

    // 7. Bold and italic
    html = html.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
    html = html.replace(/\*\*(.+?)\*\*/g,     '<strong>$1</strong>');
    html = html.replace(/\*([^*\n]+)\*/g,      '<em>$1</em>');
    html = html.replace(/_([^_\n]+)_/g,        '<em>$1</em>');

    // 8. Unordered lists — consecutive lines starting with - or *
    html = html.replace(/(?:^[*\-] .+(?:\n|$))+/gm, (block) => {
      const items = block.trimEnd().split('\n')
        .map(l => `<li>${l.replace(/^[*\-] /, '')}</li>`).join('');
      return `<ul>${items}</ul>`;
    });

    // 9. Ordered lists — consecutive lines starting with N.
    html = html.replace(/(?:^\d+\. .+(?:\n|$))+/gm, (block) => {
      const items = block.trimEnd().split('\n')
        .map(l => `<li>${l.replace(/^\d+\. /, '')}</li>`).join('');
      return `<ol>${items}</ol>`;
    });

    // 10. Paragraphs from double newlines
    html = html.replace(/\n{2,}/g, '</p><p>');
    html = '<p>' + html + '</p>';

    // 11. Single newlines → <br>
    html = html.replace(/\n/g, '<br>');

    // 12. Restore stashed code blocks
    html = html.replace(/\x00STASH(\d+)\x00/g, (_, i) => stash[parseInt(i)]);

    // 13. Unwrap <p> tags that wrap block-level elements
    html = html
      .replace(/<p>(<(?:h[1-3]|ul|ol|hr|blockquote|div)[^>]*>)/g, '$1')
      .replace(/(<\/(?:h[1-3]|ul|ol|hr|blockquote|div)>)<\/p>/g,   '$1');

    return html;
  }

  // ── Empty state ──────────────────────────────────────────────────────────

  function hideEmptyState() {
    if (emptyEl && emptyEl.parentNode) {
      emptyEl.style.display = 'none';
    }
  }

  // ── Message rendering ────────────────────────────────────────────────────

  function appendMessage(role, text, isStreaming = false) {
    hideEmptyState();

    const wrapper = document.createElement('div');
    wrapper.classList.add('message', role);

    if (role === 'assistant') {
      // Header row: avatar + role label
      const header = document.createElement('div');
      header.className = 'msg-header';
      header.innerHTML = `<div class="msg-avatar">M</div><span class="msg-role-label">Mahoraga</span>`;
      wrapper.appendChild(header);
    }

    const bubble = document.createElement('div');
    bubble.classList.add('bubble');

    if (role === 'assistant') {
      bubble.innerHTML = isStreaming ? '<span class="cursor"></span>' : renderMarkdown(text);
    } else {
      bubble.textContent = text;
    }

    wrapper.appendChild(bubble);

    // Timestamp below the bubble
    const time = document.createElement('div');
    time.className = 'msg-time';
    time.textContent = formatTime();
    wrapper.appendChild(time);

    messages.appendChild(wrapper);
    messages.scrollTop = messages.scrollHeight;
    return bubble;
  }

  function finalizeAssistantBubble(bubble, fullText) {
    bubble.innerHTML = renderMarkdown(fullText);
    // Wire copy buttons
    bubble.querySelectorAll('.copy-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const target = document.getElementById(btn.dataset.target);
        if (!target) return;
        navigator.clipboard.writeText(target.textContent).then(() => {
          btn.textContent = 'Copied!';
          setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
        });
      });
    });
    messages.scrollTop = messages.scrollHeight;
  }

  // ── Input auto-grow ──────────────────────────────────────────────────────

  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 160) + 'px';
  });

  // ── Send on Enter, newline on Shift+Enter ────────────────────────────────

  input.addEventListener('keydown', (e) => {
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

    const assistantBubble = appendMessage('assistant', '', true);
    let fullText = '';

    try {
      const res = await fetch('/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, user_id: 'web-user' }),
      });

      if (!res.ok) {
        assistantBubble.textContent = `Error: ${res.status} ${res.statusText}`;
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
          try {
            chunk = JSON.parse(line.slice(6));
          } catch (_) {
            chunk = line.slice(6);
          }
          if (chunk === '[DONE]') break;
          if (typeof chunk === 'string' && chunk.startsWith('[ERROR]')) {
            assistantBubble.textContent = chunk;
            return;
          }
          // Metrics event from OllamaWorker
          if (chunk && typeof chunk === 'object' && chunk.type === 'metrics') {
            const el = document.getElementById('metrics-text');
            if (el) el.textContent = `Last: ${chunk.elapsed_s}s · ${chunk.tokens} tok · ${chunk.throughput_tps} t/s`;
            continue;
          }
          if (typeof chunk !== 'string') continue;
          fullText += chunk;
          // Raw text while streaming (no markdown parse mid-stream)
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
      sendBtn.disabled = false;
      input.focus();
      if (window.sidebarRefresh) window.sidebarRefresh();
    }
  }

  sendBtn.addEventListener('click', submitMessage);
})();
