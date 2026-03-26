/* global acquireVsCodeApi, hljs, escapeHtml, renderMarkdown, formatModelBadge, formatToolStatus */

const vscode = acquireVsCodeApi();

const messagesEl = /** @type {HTMLElement} */ (document.getElementById('messages'));
const inputEl = /** @type {HTMLTextAreaElement} */ (document.getElementById('input'));
const sendBtn = /** @type {HTMLButtonElement} */ (document.getElementById('sendBtn'));
const toolStatusEl = /** @type {HTMLElement} */ (document.getElementById('toolStatus'));
const modelBadgeEl = /** @type {HTMLElement} */ (document.getElementById('modelBadge'));

/** @type {HTMLElement | null} */
let currentBubble = null;
let currentText = '';
let isStreaming = false;

function appendUserMessage(text) {
  const el = document.createElement('div');
  el.className = 'message user';
  el.innerHTML = `
    <span class="message-label">you</span>
    <div class="message-bubble">${escapeHtml(text)}</div>
  `;
  messagesEl.appendChild(el);
  scrollToBottom();
}

function createAgentBubble() {
  const wrapper = document.createElement('div');
  wrapper.className = 'message agent';
  wrapper.innerHTML = `
    <span class="message-label">agent</span>
    <div class="message-bubble"></div>
  `;
  messagesEl.appendChild(wrapper);
  return /** @type {HTMLElement} */ (wrapper.querySelector('.message-bubble'));
}

function appendToken(bubble, token) {
  currentText += token;
  // Stream as escaped text; markdown rendered on done
  bubble.textContent = currentText;
  scrollToBottom();
}

function finalizeMessage(bubble) {
  bubble.innerHTML = renderMarkdown(escapeHtml(currentText), hljs);
  scrollToBottom();
  currentText = '';
}

function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function setStreaming(streaming) {
  isStreaming = streaming;
  sendBtn.disabled = streaming;
}

// --- Event handlers from extension host ---

window.addEventListener('message', (event) => {
  const e = event.data;

  switch (e.type) {
    case 'model':
      modelBadgeEl.textContent = formatModelBadge(e.model);
      break;

    case 'tool_call':
      toolStatusEl.textContent = formatToolStatus(e.tool, e);
      break;

    case 'token':
      if (currentBubble) {
        appendToken(currentBubble, e.content);
      }
      break;

    case 'done':
      if (currentBubble) {
        finalizeMessage(currentBubble);
        currentBubble = null;
      }
      toolStatusEl.textContent = '';
      setStreaming(false);
      break;

    case 'error':
      if (currentBubble) {
        currentBubble.innerHTML = `<span style="color:#c15f3c">Error: ${escapeHtml(e.message)}</span>`;
        currentBubble = null;
      }
      toolStatusEl.textContent = '';
      setStreaming(false);
      break;
  }
});

// --- Send message ---

function sendMessage() {
  const text = inputEl.value.trim();
  if (!text || isStreaming) {
    return;
  }

  setStreaming(true);
  inputEl.value = '';
  inputEl.style.height = 'auto';

  appendUserMessage(text);
  currentBubble = createAgentBubble();
  currentText = '';

  vscode.postMessage({ type: 'chat', message: text });
}

sendBtn.addEventListener('click', sendMessage);

inputEl.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

// Auto-resize textarea as user types
inputEl.addEventListener('input', () => {
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + 'px';
});
