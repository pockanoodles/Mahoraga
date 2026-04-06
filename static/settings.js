(() => {
  const settingsBtn = document.getElementById('settings-btn');
  const drawer = document.getElementById('settings-drawer');
  const overlay = document.getElementById('drawer-overlay');
  const drawerBody = document.getElementById('drawer-body');
  const closeBtn = document.getElementById('drawer-close-btn');

  function openDrawer() {
    drawer.style.display = 'flex';
    overlay.style.display = 'block';
    loadSettings();
  }

  function closeDrawer() {
    drawer.style.display = 'none';
    overlay.style.display = 'none';
  }

  async function loadSettings() {
    drawerBody.innerHTML = '<p class="drawer-loading">Loading…</p>';
    try {
      const [sRes, bRes] = await Promise.all([
        fetch('/settings'),
        fetch('/settings/backend'),
      ]);
      const s = await sRes.json();
      const b = await bRes.json();

      drawerBody.innerHTML = `
        <div class="drawer-section">
          <div class="drawer-section-label">BACKEND</div>
          <div class="drawer-row"><span>Active</span><span>${b.active_backend === 'claude' ? 'Claude' : 'Ollama'}</span></div>
        </div>
        <div class="drawer-section">
          <div class="drawer-section-label">CLAUDE</div>
          <div class="drawer-row"><span>API Key</span><span class="drawer-mono">${s.anthropic_api_key}</span></div>
          <div class="drawer-row"><span>Planner</span><span class="drawer-mono">claude-haiku-4-5</span></div>
          <div class="drawer-row"><span>Executor</span><span class="drawer-mono">claude-sonnet-4-6</span></div>
        </div>
        <div class="drawer-section">
          <div class="drawer-section-label">OLLAMA</div>
          <div class="drawer-row"><span>URL</span><span class="drawer-mono">${b.ollama_base_url}</span></div>
          <div class="drawer-section-label drawer-sub-label">ROUTING TABLE</div>
          <div class="drawer-row"><span>planner</span><span class="drawer-mono">qwen3.5:2b</span></div>
          <div class="drawer-row"><span>fast</span><span class="drawer-mono">qwen3.5:2b</span></div>
          <div class="drawer-row"><span>coder</span><span class="drawer-mono">qwen2.5-coder:7b</span></div>
          <div class="drawer-row"><span>general</span><span class="drawer-mono">qwen3.5:9b</span></div>
        </div>
        <p class="drawer-hint">To change settings, edit your .env file and restart Mahoraga.</p>
      `;
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
