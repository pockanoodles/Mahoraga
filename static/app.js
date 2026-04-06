// static/app.js
(() => {
  const form = document.querySelector('.input-area');
  const input = document.getElementById('user-input');
  const sendBtn = document.getElementById('send-btn');
  const messages = document.getElementById('messages');

  // ── Markdown renderer (no external deps) ────────────────────────────────

  function renderMarkdown(text) {
    // Escape HTML first
    let html = text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');

    // Fenced code blocks ```lang\n...\n```
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
      const id = 'cb-' + Math.random().toString(36).slice(2, 7);
      return `<div class="code-block-wrapper">
        <button class="copy-btn" data-target="${id}">Copy</button>
        <pre id="${id}"><code>${code.trimEnd()}</code></pre>
      </div>`;
    });

    // Inline code `...`
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

    // Bold **...**
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

    // Italic _..._
    html = html.replace(/_(.+?)_/g, '<em>$1</em>');

    // Paragraph breaks
    html = html.replace(/\n{2,}/g, '</p><p>');
    html = '<p>' + html + '</p>';

    // Single newlines → <br> inside paragraphs
    html = html.replace(/\n/g, '<br>');

    return html;
  }

  // ── Message rendering ────────────────────────────────────────────────────

  function appendMessage(role, text, isStreaming = false) {
    const wrapper = document.createElement('div');
    wrapper.classList.add('message', role);

    const bubble = document.createElement('div');
    bubble.classList.add('bubble');

    if (role === 'assistant') {
      if (isStreaming) {
        bubble.innerHTML = '<span class="cursor"></span>';
      } else {
        bubble.innerHTML = renderMarkdown(text);
      }
    } else if (role === 'system') {
      bubble.textContent = text;
    } else {
      bubble.textContent = text;
    }

    wrapper.appendChild(bubble);
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

      const reader = res.body.getReader();
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
          const chunk = line.slice(6);
          if (chunk.trimEnd() === '[DONE]') break;
          if (chunk.startsWith('[ERROR]')) {
            assistantBubble.textContent = chunk;
            return;
          }
          fullText += chunk;
          // Update bubble raw while streaming (no markdown parse mid-stream)
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

    } catch (err) {
      assistantBubble.textContent = `Network error: ${err.message}`;
    } finally {
      sendBtn.disabled = false;
      input.focus();
      // Trigger sidebar refresh after a message completes
      if (window.sidebarRefresh) window.sidebarRefresh();
    }
  }

  sendBtn.addEventListener('click', submitMessage);
})();
