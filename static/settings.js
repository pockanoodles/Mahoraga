// static/settings.js
(() => {
  const settingsBtn = document.getElementById('settings-btn');
  const overlay = document.getElementById('drawer-overlay');
  const drawer = document.getElementById('settings-drawer');
  const closeBtn = document.getElementById('drawer-close-btn');
  const drawerBody = document.getElementById('drawer-body');

  function openDrawer() {
    overlay.style.display = 'block';
    drawer.style.display = 'flex';
    requestAnimationFrame(() => drawer.classList.add('open'));
    loadSettings();
  }

  function closeDrawer() {
    drawer.classList.remove('open');
    setTimeout(() => {
      overlay.style.display = 'none';
      drawer.style.display = 'none';
    }, 250);
  }

  async function loadSettings() {
    drawerBody.innerHTML = '<p class="drawer-loading">Loading…</p>';
    try {
      const res = await fetch('/settings');
      if (!res.ok) throw new Error(res.statusText);
      const s = await res.json();

      drawerBody.innerHTML = `
        <div class="settings-row">
          <span class="settings-label">Executor Model</span>
          <span class="settings-value">${s.executor_model}</span>
        </div>
        <div class="settings-row">
          <span class="settings-label">Anthropic API Key</span>
          <span class="settings-value">${s.anthropic_api_key}</span>
        </div>
        <div class="settings-row">
          <span class="settings-label">Telegram Token</span>
          <span class="settings-value">${s.telegram_token}</span>
        </div>
        <div class="settings-row">
          <span class="settings-label">Brave API Key</span>
          <span class="settings-value">${s.brave_api_key}</span>
        </div>
        <p class="settings-note">
          To change settings, update your <code>.env</code> file and restart Mahoraga.
        </p>
      `;
    } catch (err) {
      drawerBody.innerHTML = `<p class="drawer-loading">Failed to load settings: ${err.message}</p>`;
    }
  }

  settingsBtn.addEventListener('click', openDrawer);
  closeBtn.addEventListener('click', closeDrawer);
  overlay.addEventListener('click', closeDrawer);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeDrawer();
  });
})();
