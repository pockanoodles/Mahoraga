// static/resize.js — draggable divider + sidebar collapse/expand

(function () {
  const STORAGE_KEY = 'mah_sidebar_width';
  const DEFAULT_WIDTH = 320;
  const MIN_WIDTH = 0;

  const divider = document.getElementById('divider');
  const sidebar = document.getElementById('sidebar');
  const collapseBtn = document.getElementById('sidebar-collapse-btn');

  let dragging = false;
  let startX = 0;
  let startWidth = 0;
  let rafId = null;

  function getMaxWidth() {
    // Leave at least 320px for the chat column
    return window.innerWidth - 320 - divider.offsetWidth;
  }

  // ── Restore saved width ──────────────────────────────────────────────────
  const saved = parseInt(localStorage.getItem(STORAGE_KEY), 10);
  if (!isNaN(saved)) {
    sidebar.style.width = saved + 'px';
  } else {
    sidebar.style.width = DEFAULT_WIDTH + 'px';
  }

  // ── Drag to resize ───────────────────────────────────────────────────────
  divider.addEventListener('mousedown', function (e) {
    dragging = true;
    startX = e.clientX;
    startWidth = sidebar.offsetWidth;
    divider.classList.add('dragging');
    sidebar.classList.add('no-transition');
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    e.preventDefault();
  });

  document.addEventListener('mousemove', function (e) {
    if (!dragging) return;
    if (rafId) return;

    rafId = requestAnimationFrame(function () {
      const delta = e.clientX - startX;
      const maxW = getMaxWidth();
      const newWidth = Math.min(maxW, Math.max(MIN_WIDTH, startWidth + delta));

      if (newWidth <= 40) {
        sidebar.classList.add('collapsed');
        sidebar.style.width = '0px';
        collapseBtn.textContent = '›';
      } else {
        if (sidebar.classList.contains('collapsed')) {
          sidebar.classList.remove('collapsed');
          collapseBtn.textContent = '‹';
        }
        sidebar.style.width = newWidth + 'px';
      }

      rafId = null;
    });
  });

  document.addEventListener('mouseup', function () {
    if (!dragging) return;
    dragging = false;
    if (rafId) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }
    divider.classList.remove('dragging');
    sidebar.classList.remove('no-transition');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    const currentWidth = sidebar.offsetWidth;
    if (currentWidth > 0) {
      localStorage.setItem(STORAGE_KEY, currentWidth);
    }
  });

  // ── Collapse / expand ────────────────────────────────────────────────────
  collapseBtn.addEventListener('click', function () {
    const collapsed = sidebar.classList.toggle('collapsed');
    collapseBtn.textContent = collapsed ? '›' : '‹';
    if (!collapsed) {
      const restored = parseInt(localStorage.getItem(STORAGE_KEY), 10);
      sidebar.style.width = (!isNaN(restored) ? restored : DEFAULT_WIDTH) + 'px';
    }
  });

  // ── Section chevron collapse ─────────────────────────────────────────────
  document.querySelectorAll('.section-chevron').forEach(function (btn) {
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      const sectionName = btn.dataset.section;
      const body = document.getElementById(sectionName + '-body');
      if (!body) return;
      const collapsed = body.classList.toggle('collapsed');
      btn.style.transform = collapsed ? 'rotate(-90deg)' : '';
    });
  });

  document.querySelectorAll('.section-header').forEach(function (header) {
    header.addEventListener('click', function () {
      const btn = header.querySelector('.section-chevron');
      if (btn) btn.click();
    });
  });
})();
