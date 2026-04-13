(() => {
  const settingsBtn = document.getElementById('settings-btn');
  const drawer = document.getElementById('settings-drawer');
  const overlay = document.getElementById('drawer-overlay');
  const drawerBody = document.getElementById('drawer-body');
  const closeBtn = document.getElementById('drawer-close-btn');

  function openDrawer() {
    drawer.style.display = 'flex';
    overlay.style.display = 'block';
    // Force a reflow before adding .open so the slide-in transition fires
    requestAnimationFrame(() => drawer.classList.add('open'));
    loadSettings();
  }

  function closeDrawer() {
    drawer.classList.remove('open');
    overlay.style.display = 'none';
    // Hide after the slide-out transition completes (0.25s)
    setTimeout(() => { drawer.style.display = 'none'; }, 260);
  }

  async function loadSettings() {
    drawerBody.innerHTML = '<p class="drawer-loading">Loading…</p>';
    try {
      const [sRes, bRes, wRes] = await Promise.all([
        fetch('/settings'),
        fetch('/settings/backend'),
        fetch('/settings/workdir'),
      ]);
      const s = await sRes.json();
      const b = await bRes.json();
      const w = await wRes.json();

      drawerBody.innerHTML = `
        <div class="drawer-section">
          <div class="drawer-section-label">OLLAMA</div>
          <div class="drawer-row"><span>URL</span><span class="drawer-mono">${b.ollama_base_url}</span></div>
          <div class="drawer-row"><span>Backend</span><span class="drawer-mono">${b.active_backend}</span></div>
        </div>
        <div class="drawer-section">
          <div class="drawer-section-label">WORKERS</div>
          <div class="drawer-row"><span>planner</span><span class="drawer-mono">qwen3:4b-q4_K_M</span></div>
          <div class="drawer-row"><span>fast</span><span class="drawer-mono">qwen3:4b-q4_K_M</span></div>
          <div class="drawer-row"><span>coder</span><span class="drawer-mono">qwen3:4b-q4_K_M</span></div>
          <div class="drawer-row"><span>general</span><span class="drawer-mono">qwen3:4b-q4_K_M</span></div>
          <div class="drawer-row"><span>aider</span><span class="drawer-mono">ollama_chat/qwen3:4b-q4_K_M</span></div>
        </div>
        <div class="drawer-section">
          <div class="drawer-section-label">WORKING DIRECTORY</div>
          <div class="drawer-row" style="flex-direction:column;align-items:stretch;gap:6px">
            <span style="font-size:11px;color:var(--text-muted)">Files written by Aider, Codex, Gemini, OpenCode land here.</span>
            <input type="text" id="workdir-input" class="settings-input"
                   value="${w.workdir || ''}"
                   placeholder="(uvicorn CWD — set to override)" />
            <button id="workdir-save" class="settings-btn">Save</button>
            <span id="workdir-status" style="font-size:11px;color:var(--text-muted)"></span>
          </div>
        </div>
        <p class="drawer-hint">To change Ollama settings, edit your .env file and restart Mahoraga.</p>
      `;

      document.getElementById('workdir-save')?.addEventListener('click', async () => {
        const wd = document.getElementById('workdir-input').value;
        const statusEl = document.getElementById('workdir-status');
        try {
          const res = await fetch('/settings/workdir', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ workdir: wd }),
          });
          const data = await res.json();
          if (res.ok) {
            statusEl.textContent = `Saved: ${data.workdir}`;
            statusEl.style.color = 'var(--success)';
          } else {
            statusEl.textContent = data.detail || 'Error saving workdir';
            statusEl.style.color = 'var(--error)';
          }
        } catch (err) {
          statusEl.textContent = `Network error: ${err.message}`;
          statusEl.style.color = 'var(--error)';
        }
      });

    } catch (err) {
      drawerBody.innerHTML = `<p class="drawer-loading">Failed to load settings.</p>`;
    }
  }

  settingsBtn.addEventListener('click', openDrawer);
  closeBtn.addEventListener('click', closeDrawer);
  overlay.addEventListener('click', closeDrawer);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeDrawer();
  });
})();
