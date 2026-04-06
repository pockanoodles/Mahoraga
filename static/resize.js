// static/resize.js — draggable divider + sidebar collapse/expand

(function () {
  const STORAGE_KEY = 'mah_sidebar_width';
  const DEFAULT_WIDTH = 320;
  const MIN_WIDTH = 200;
  const MAX_WIDTH = 600;

  const divider = document.getElementById('divider');
  const sidebar = document.getElementById('sidebar');
  const collapseBtn = document.getElementById('sidebar-collapse-btn');

  let dragging = false;
  let startX = 0;
  let startWidth = 0;

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
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    e.preventDefault();
  });

  document.addEventListener('mousemove', function (e) {
    if (!dragging) return;
    const delta = e.clientX - startX;
    const newWidth = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, startWidth + delta));
    sidebar.style.width = newWidth + 'px';
  });

  document.addEventListener('mouseup', function () {
    if (!dragging) return;
    dragging = false;
    divider.classList.remove('dragging');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    localStorage.setItem(STORAGE_KEY, sidebar.offsetWidth);
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
