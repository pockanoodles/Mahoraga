// static/sidebar.js
(() => {
  // ── Agent color palette ──────────────────────────────────────────────────
  const AGENT_PALETTE = [
    '#4A90D9', '#7B68EE', '#50C878', '#FF8C69', '#DDA0DD',
    '#87CEEB', '#F0A500', '#20B2AA', '#CD853F', '#6495ED',
  ];

  function esc(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function agentColor(workerId) {
    if (!workerId) return '#3a3a5a';
    let hash = 0;
    for (const ch of workerId) {
      hash = (hash * 31 + ch.charCodeAt(0)) & 0xFFFFFFFF;
    }
    return AGENT_PALETTE[Math.abs(hash) % AGENT_PALETTE.length];
  }

  // ── SVG helpers ──────────────────────────────────────────────────────────

  function svgEl(tag, attrs = {}) {
    const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    return el;
  }

  function bezierPath(x1, y1, x2, y2) {
    const midY = (y1 + y2) / 2;
    return `M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`;
  }

  // ── Tree layout ──────────────────────────────────────────────────────────

  function buildLayout(tasks, containerWidth) {
    const NODE_R = 14;
    const LEVEL_H = 90;
    const taskMap = Object.fromEntries(tasks.map(t => [t.id, t]));

    // Build children map from parent_task_id
    const children = {};
    const roots = [];
    for (const task of tasks) {
      if (!task.parent_task_id) {
        roots.push(task.id);
      } else {
        if (!children[task.parent_task_id]) children[task.parent_task_id] = [];
        children[task.parent_task_id].push(task.id);
      }
    }

    // If no parent relationships, treat all tasks as roots in a linear chain
    if (roots.length === 0 && tasks.length > 0) {
      tasks.forEach(t => roots.push(t.id));
    }

    // Assign levels via BFS
    const level = {};
    const queue = [...roots];
    roots.forEach(id => level[id] = 0);
    const visited = new Set(roots);
    while (queue.length) {
      const id = queue.shift();
      for (const cid of (children[id] || [])) {
        if (!visited.has(cid)) {
          visited.add(cid);
          level[cid] = (level[id] ?? 0) + 1;
          queue.push(cid);
        }
      }
    }
    // Any node not reached by BFS gets level 0
    for (const task of tasks) {
      if (level[task.id] === undefined) level[task.id] = 0;
    }

    // Group by level
    const byLevel = {};
    for (const task of tasks) {
      const l = level[task.id];
      if (!byLevel[l]) byLevel[l] = [];
      byLevel[l].push(task.id);
    }

    // Assign x positions: evenly spaced
    const positions = {};
    for (const [l, ids] of Object.entries(byLevel)) {
      const count = ids.length;
      ids.forEach((id, i) => {
        positions[id] = {
          x: ((i + 1) / (count + 1)) * containerWidth,
          y: parseInt(l) * LEVEL_H + NODE_R + 20,
        };
      });
    }

    // Total SVG height
    const maxLevel = Math.max(...Object.keys(byLevel).map(Number), 0);
    const svgHeight = (maxLevel + 1) * LEVEL_H + NODE_R * 2 + 40;

    return { positions, children, roots, taskMap, svgHeight, NODE_R };
  }

  // ── Vine renderer ────────────────────────────────────────────────────────

  const svg = document.getElementById('vine-svg');
  const vineEmpty = document.getElementById('vine-empty');
  const tooltip = document.getElementById('vine-tooltip');

  function renderVine(tasks) {
    svg.innerHTML = '';

    if (!tasks || tasks.length === 0) {
      svg.style.display = 'none';
      vineEmpty.style.display = 'flex';
      return;
    }

    svg.style.display = 'block';
    vineEmpty.style.display = 'none';

    const containerWidth = svg.parentElement.clientWidth || 280;
    const { positions, children, taskMap, svgHeight, NODE_R } = buildLayout(tasks, containerWidth);

    svg.setAttribute('height', svgHeight);
    svg.setAttribute('viewBox', `0 0 ${containerWidth} ${svgHeight}`);

    // Draw edges first (behind nodes)
    const edgeGroup = svgEl('g', { class: 'edges' });
    for (const [parentId, childIds] of Object.entries(children)) {
      const p = positions[parentId];
      if (!p) continue;
      for (const childId of childIds) {
        const c = positions[childId];
        if (!c) continue;
        const childTask = taskMap[childId];
        const isActive = childTask?.status === 'in_progress';
        const isDone = childTask?.status === 'done' || childTask?.status === 'completed';
        const isFailed = childTask?.status === 'failed';

        let stroke = '#3a3a5a';
        let opacity = '0.5';
        if (isActive) { stroke = '#007AFF'; opacity = '0.9'; }
        else if (isDone) { stroke = '#3fb950'; opacity = '0.3'; }
        else if (isFailed) { stroke = '#f85149'; opacity = '0.6'; }

        const path = svgEl('path', {
          d: bezierPath(p.x, p.y + NODE_R, c.x, c.y - NODE_R),
          stroke,
          'stroke-width': isActive ? '2' : '1.5',
          fill: 'none',
          opacity,
        });
        edgeGroup.appendChild(path);
      }
    }
    svg.appendChild(edgeGroup);

    // Draw nodes
    const nodeGroup = svgEl('g', { class: 'nodes' });
    for (const task of tasks) {
      const pos = positions[task.id];
      if (!pos) continue;

      const isActive = task.status === 'in_progress';
      const isDone = task.status === 'done' || task.status === 'completed';
      const isFailed = task.status === 'failed';

      // Outer ring color
      let ringColor = '#3a3a5a';
      if (isActive) ringColor = '#007AFF';
      else if (isDone) ringColor = '#3fb950';
      else if (isFailed) ringColor = '#f85149';

      // Inner fill: agent color
      const fillColor = task.worker_id ? agentColor(task.worker_id) : '#2a2a3e';

      // Glow filter for active nodes
      if (isActive) {
        const filterId = `glow-${task.id.slice(0, 6)}`;
        const defs = svg.querySelector('defs') || svg.insertBefore(svgEl('defs'), svg.firstChild);
        const filter = svgEl('filter', { id: filterId, x: '-50%', y: '-50%', width: '200%', height: '200%' });
        const feGaussianBlur = svgEl('feGaussianBlur', { stdDeviation: '3', result: 'coloredBlur' });
        const feMerge = svgEl('feMerge');
        feMerge.appendChild(svgEl('feMergeNode', { in: 'coloredBlur' }));
        feMerge.appendChild(svgEl('feMergeNode', { in: 'SourceGraphic' }));
        filter.appendChild(feGaussianBlur);
        filter.appendChild(feMerge);
        defs.appendChild(filter);

        // Animated outer ring
        const pulseCircle = svgEl('circle', {
          cx: pos.x, cy: pos.y,
          r: NODE_R + 5,
          fill: 'none',
          stroke: '#007AFF',
          'stroke-width': '1.5',
          opacity: '0.4',
        });
        const animate = svgEl('animate', {
          attributeName: 'r',
          from: NODE_R + 2, to: NODE_R + 9,
          dur: '1.5s', repeatCount: 'indefinite',
        });
        const animateOp = svgEl('animate', {
          attributeName: 'opacity',
          from: '0.4', to: '0',
          dur: '1.5s', repeatCount: 'indefinite',
        });
        pulseCircle.appendChild(animate);
        pulseCircle.appendChild(animateOp);
        nodeGroup.appendChild(pulseCircle);
      }

      // Outer ring
      const outerCircle = svgEl('circle', {
        cx: pos.x, cy: pos.y, r: NODE_R,
        fill: fillColor,
        stroke: ringColor,
        'stroke-width': isActive ? '2.5' : '2',
        opacity: isDone ? '0.55' : '1',
        filter: isActive ? `url(#glow-${task.id.slice(0, 6)})` : '',
      });

      // Hover events
      const g = svgEl('g', { class: 'node-group', style: 'cursor:pointer' });
      g.appendChild(outerCircle);

      // Label below node
      const label = svgEl('text', {
        x: pos.x, y: pos.y + NODE_R + 12,
        'text-anchor': 'middle',
        'font-family': 'var(--font-ui, system-ui)',
        'font-size': '9',
        fill: isDone ? '#6b6b8a' : '#e8e8f0',
      });
      const taskTitle = (task.title || '').length > 18
        ? task.title.slice(0, 16) + '…'
        : (task.title || '');
      label.textContent = taskTitle;
      g.appendChild(label);

      if (task.worker_id) {
        const workerLabel = svgEl('text', {
          x: pos.x, y: pos.y + NODE_R + 22,
          'text-anchor': 'middle',
          'font-family': 'var(--font-mono, monospace)',
          'font-size': '7',
          fill: agentColor(task.worker_id),
          opacity: '0.8',
        });
        const shortWorker = task.worker_id.replace('claude:', '').replace('ollama:', '');
        workerLabel.textContent = shortWorker;
        g.appendChild(workerLabel);
      }

      // Tooltip
      g.addEventListener('mouseenter', (e) => {
        const statusEmoji = isActive ? '⟳' : isDone ? '✓' : isFailed ? '✗' : '○';
        tooltip.innerHTML = `
          <strong>${task.title || '—'}</strong><br>
          ${statusEmoji} ${task.status}<br>
          ${task.worker_id ? '⚙ ' + task.worker_id + '<br>' : ''}
          ${task.elapsed_seconds ? '⏱ ' + task.elapsed_seconds + 's' : ''}
        `.trim();
        tooltip.style.display = 'block';
        positionTooltip(e);
      });
      g.addEventListener('mousemove', positionTooltip);
      g.addEventListener('mouseleave', () => {
        tooltip.style.display = 'none';
      });

      nodeGroup.appendChild(g);
    }
    svg.appendChild(nodeGroup);
  }

  function positionTooltip(e) {
    const margin = 12;
    let left = e.clientX + margin;
    let top = e.clientY - tooltip.offsetHeight / 2;
    if (left + 210 > window.innerWidth) left = e.clientX - 210 - margin;
    tooltip.style.left = left + 'px';
    tooltip.style.top = top + 'px';
  }

  // ── Logs panel ───────────────────────────────────────────────────────────

  const logsList = document.getElementById('logs-list');
  const logsEmpty = document.getElementById('logs-empty');

  function formatTime(ts) {
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  function isToday(ts) {
    const d = new Date(ts * 1000);
    const now = new Date();
    return d.toDateString() === now.toDateString();
  }

  function isYesterday(ts) {
    const d = new Date(ts * 1000);
    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);
    return d.toDateString() === yesterday.toDateString();
  }

  function renderLogs(entries) {
    logsList.innerHTML = '';

    if (!entries || entries.length === 0) {
      logsEmpty.style.display = 'block';
      return;
    }
    logsEmpty.style.display = 'none';

    let lastGroup = null;

    for (const entry of entries) {
      let group = 'Older';
      if (isToday(entry.created_at)) group = 'Today';
      else if (isYesterday(entry.created_at)) group = 'Yesterday';

      if (group !== lastGroup) {
        const label = document.createElement('div');
        label.className = 'logs-group-label';
        label.textContent = group;
        logsList.appendChild(label);
        lastGroup = group;
      }

      const shortWorker = (entry.worker_id || '').replace('claude:', '').replace('ollama:', '') || '—';
      const summary = entry.user_message.length > 50
        ? entry.user_message.slice(0, 48) + '…'
        : entry.user_message;

      const el = document.createElement('div');
      el.className = 'log-entry';
      el.innerHTML = `
        <div class="log-entry-row">
          <span class="log-time">${formatTime(entry.created_at)}</span>
          <span class="log-chip">${shortWorker}</span>
          <span class="log-summary">${esc(summary)}</span>
        </div>
        <div class="log-detail">
          <div class="log-detail-user">"${esc(entry.user_message)}"</div>
          <div class="log-detail-response">${esc(entry.assistant_response.slice(0, 400))}${entry.assistant_response.length > 400 ? '…' : ''}</div>
          <div class="log-detail-cost">$${entry.cost_usd.toFixed(4)} · ${esc(entry.worker_id || 'unknown')}</div>
        </div>
      `;
      el.addEventListener('click', () => el.classList.toggle('expanded'));
      logsList.appendChild(el);
    }
  }

  // ── Cost bar ─────────────────────────────────────────────────────────────

  const costSession = document.getElementById('cost-session');
  const costTotal = document.getElementById('cost-total');
  const costFill = document.getElementById('cost-fill');
  const costTooltipEl = document.getElementById('cost-tooltip');

  const WARN_USD = parseFloat(window.MAHORAGA_COST_WARN || '1.0');
  const ALERT_USD = parseFloat(window.MAHORAGA_COST_ALERT || '5.0');

  function renderCost(data) {
    const session = data.session_usd || 0;
    const total = data.total_usd || 0;
    costSession.textContent = `Session: $${session.toFixed(3)}`;
    costTotal.textContent = `Total: $${total.toFixed(3)}`;

    const pct = Math.min((session / ALERT_USD) * 100, 100);
    costFill.style.width = pct + '%';
    costFill.className = 'cost-fill';
    if (session >= ALERT_USD) costFill.classList.add('alert');
    else if (session >= WARN_USD) costFill.classList.add('warn');

    if (data.breakdown && data.breakdown.length > 0) {
      costTooltipEl.innerHTML = data.breakdown
        .map(b => `${b.model.replace('claude-', '').replace('-20251001', '')}: $${b.cost_usd.toFixed(4)}`)
        .join('<br>');
    } else {
      costTooltipEl.textContent = 'No spend this session';
    }
  }

  // ── Polling ──────────────────────────────────────────────────────────────

  async function refresh() {
    try {
      const [vineRes, logsRes, costRes] = await Promise.all([
        fetch('/missions/active'),
        fetch('/logs/recent?limit=20'),
        fetch('/cost/summary'),
      ]);

      if (vineRes.ok) {
        const data = await vineRes.json();
        renderVine(data.tasks || []);
      }
      if (logsRes.ok) {
        const data = await logsRes.json();
        renderLogs(data.entries || []);
      }
      if (costRes.ok) {
        const data = await costRes.json();
        renderCost(data);
      }
    } catch (_) {
      // Network error — fail silently, try again on next poll
    }
  }

  // Initial load + 3s polling
  refresh();
  setInterval(refresh, 3000);

  // Expose for app.js to call after message completes
  window.sidebarRefresh = refresh;
})();
