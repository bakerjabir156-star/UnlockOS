/**
 * dashboard.js — UnlockOS Frontend Logic
 * SSE client, polling, pipeline animation, history table, actions
 */

'use strict';

// ── State ────────────────────────────────────────────────────
const STATE = {
  devices: [],
  pipeline: {},
  logs: [],
  history: [],
  historySortCol: 'id',
  historySortDir: 'desc',
  logFilter: 'ALL',
  maxLogs: 400,
  connected: false,
};

// ── Stage meta ───────────────────────────────────────────────
const STAGES = [
  { key: 'DETECTION', label: 'Détection & Identification' },
  { key: 'EXPLOIT',   label: 'Exploitation (Checkm8/MTK)' },
  { key: 'BYPASS',    label: 'Bypass MDM / iCloud' },
  { key: 'PROXY',     label: "Proxy d'Activation" },
  { key: 'FINALIZE',  label: 'Finalisation & Reboot' },
];

const STAGE_PROGRESS = { DETECTION: 10, EXPLOIT: 40, BYPASS: 65, PROXY: 80, FINALIZE: 95 };

// ── DOM shortcuts ─────────────────────────────────────────────
const $ = id => document.getElementById(id);

// ══════════════════════════════════════════════════════════════
// SSE Connection
// ══════════════════════════════════════════════════════════════

let _sseRetries = 0;

function connectSSE() {
  const es = new EventSource('/api/stream');

  es.addEventListener('connected', e => {
    const data = JSON.parse(e.data);
    setEngineStatus(true, data.simulate ? '🟡 Mode Simulateur Actif' : '🟢 Moteur en ligne');
    _sseRetries = 0;
  });

  es.addEventListener('log', e => {
    const event = JSON.parse(e.data);
    if (event.type === 'pipeline_update') {
      handlePipelineUpdate(event);
    } else if (event.type === 'device_update') {
      renderDevices(event.devices || []);
    } else {
      appendLog(event);
    }
  });

  es.onerror = () => {
    setEngineStatus(false, '🔴 Reconnexion…');
    es.close();
    _sseRetries++;
    const delay = Math.min(1000 * Math.pow(2, _sseRetries), 16000);
    setTimeout(connectSSE, delay);
  };
}

// ══════════════════════════════════════════════════════════════
// Polling (status + history every 3s, latency every 5s)
// ══════════════════════════════════════════════════════════════

function startPolling() {
  fetchStatus();
  fetchHistory();
  setInterval(fetchStatus,  3000);
  setInterval(fetchHistory, 8000);
  setInterval(fetchLatency, 5000);
}

async function fetchStatus() {
  try {
    const res = await fetch('/api/status');
    const { stats, pipeline } = await res.json();
    updateHeaderStats(stats);
    updateProxyBadge(stats.proxy_status);
    // Update pipelines from polling as fallback
    Object.entries(pipeline).forEach(([id, p]) => handlePipelineUpdate({ ...p, device_id: id }));
  } catch (_) {}
}

async function fetchHistory() {
  try {
    const res = await fetch('/api/history?limit=100');
    const { history, stats } = await res.json();
    STATE.history = history;
    renderHistory();
    $('history-count').textContent = stats.total || 0;
  } catch (_) {}
}

async function fetchLatency() {
  try {
    const res = await fetch('/api/latency');
    const { latency } = await res.json();
    latency.forEach(({ id, latency_ms }) => {
      const chip = document.querySelector(`.device-card[data-id="${id}"] .latency-chip`);
      if (!chip) return;
      const cls = latency_ms < 0 ? 'bad' : latency_ms < 50 ? 'good' : latency_ms < 150 ? 'warn' : 'bad';
      chip.className = `latency-chip ${cls}`;
      chip.textContent = latency_ms < 0 ? 'ERR' : `${latency_ms}ms`;
    });
  } catch (_) {}
}

// ══════════════════════════════════════════════════════════════
// Header Stats
// ══════════════════════════════════════════════════════════════

function setEngineStatus(online, label) {
  STATE.connected = online;
  $('engine-dot').className = 'status-dot ' + (online ? 'online' : 'offline');
  $('engine-status-text').textContent = label;
  $('engine-status-pill').className = 'status-pill ' + (online ? 'connected' : '');
}

function updateHeaderStats(stats) {
  $('stat-total').textContent   = stats.total   ?? 0;
  $('stat-success').textContent = stats.success ?? 0;
  $('stat-failed').textContent  = stats.failed  ?? 0;
  $('stat-uptime').textContent  = fmtUptime(stats.uptime_s ?? 0);
}

function fmtUptime(s) {
  if (s < 60)   return `${s}s`;
  if (s < 3600) return `${Math.floor(s/60)}m ${s%60}s`;
  return `${Math.floor(s/3600)}h ${Math.floor((s%3600)/60)}m`;
}

// ══════════════════════════════════════════════════════════════
// Device Panel
// ══════════════════════════════════════════════════════════════

function renderDevices(devices) {
  STATE.devices = devices;
  const list = $('device-list');
  const empty = $('device-empty');
  $('device-count').textContent = devices.length;

  if (!devices.length) {
    empty.style.display = '';
    // Remove all device cards
    list.querySelectorAll('.device-card').forEach(el => el.remove());
    return;
  }
  empty.style.display = 'none';

  // Track existing cards
  const existing = new Set([...list.querySelectorAll('.device-card')].map(el => el.dataset.id));
  const incoming = new Set(devices.map(d => d.id));

  // Remove stale
  existing.forEach(id => {
    if (!incoming.has(id)) {
      const el = list.querySelector(`.device-card[data-id="${id}"]`);
      if (el) { el.style.opacity = '0'; setTimeout(() => el.remove(), 250); }
    }
  });

  // Add new
  devices.forEach(d => {
    if (!existing.has(d.id)) {
      list.appendChild(buildDeviceCard(d));
    }
  });
}

function buildDeviceCard(d) {
  const isIOS   = d.platform === 'ios';
  const isRemote = d.connection === 'remote';
  const icon = isIOS ? '📱' : '🤖';

  const card = document.createElement('div');
  card.className = 'device-card';
  card.dataset.id = d.id;
  card.innerHTML = `
    <div class="device-icon">${icon}</div>
    <div class="device-info">
      <div class="device-model">${esc(d.model)}</div>
      <div class="device-meta">${isIOS ? 'iOS' : 'Android'} ${esc(d.version || '')} · ${esc(d.chipset || '')}</div>
      <div class="device-sn">SN: ${esc(d.serial || 'N/A')}</div>
    </div>
    <div class="device-badges">
      <span class="conn-badge ${isRemote ? 'remote' : 'local'}">${isRemote ? '🌐 Remote' : '🔌 Local'}</span>
      <span class="platform-badge ${isIOS ? 'ios' : 'android'}">${isIOS ? 'iOS' : 'Android'}</span>
      ${isRemote ? `<span class="latency-chip good">…ms</span>` : ''}
    </div>`;
  return card;
}

// ══════════════════════════════════════════════════════════════
// Pipeline Panel
// ══════════════════════════════════════════════════════════════

function handlePipelineUpdate(event) {
  const id = event.device_id;
  if (!id) return;

  STATE.pipeline[id] = { ...(STATE.pipeline[id] || {}), ...event };

  const pList   = $('pipeline-list');
  const pEmpty  = $('pipeline-empty');
  const stateVal = event.state || 'IDLE';
  const progress = event.progress ?? 0;
  const activeStage = event.stage || 'DETECTION';

  // Remove finished pipelines
  if (stateVal === 'SUCCESS' || stateVal === 'FAILED') {
    setTimeout(() => {
      const el = pList.querySelector(`.pipeline-entry[data-id="${id}"]`);
      if (el) { el.style.opacity = '0'; el.style.transition = 'opacity 0.5s'; setTimeout(() => el.remove(), 500); }
      delete STATE.pipeline[id];
      if (!pList.querySelector('.pipeline-entry')) pEmpty.style.display = '';
    }, 4000);
  }

  pEmpty.style.display = 'none';

  let entry = pList.querySelector(`.pipeline-entry[data-id="${id}"]`);
  if (!entry) {
    entry = buildPipelineEntry(id);
    pList.appendChild(entry);
  }

  // Update bar
  const bar = entry.querySelector('.pipeline-bar');
  if (bar) bar.style.width = Math.min(progress, 100) + '%';

  // Update steps
  STAGES.forEach(({ key }) => {
    const dot   = entry.querySelector(`.step-dot[data-stage="${key}"]`);
    const label = entry.querySelector(`.step-label[data-stage="${key}"]`);
    const stStat = entry.querySelector(`.step-status[data-stage="${key}"]`);
    if (!dot) return;

    const stageIdx  = STAGES.findIndex(s => s.key === key);
    const activeIdx = STAGES.findIndex(s => s.key === activeStage);

    dot.className = 'step-dot';
    label.className = 'step-label';

    if (stageIdx < activeIdx) {
      dot.classList.add(stateVal === 'FAILED' && stageIdx === activeIdx - 1 ? 'failed' : 'done');
      label.classList.add(stateVal === 'FAILED' && stageIdx === activeIdx - 1 ? 'failed' : 'done');
      stStat.textContent = stateVal === 'FAILED' && stageIdx === activeIdx - 1 ? '✗' : '✓';
    } else if (stageIdx === activeIdx) {
      if (stateVal === 'SUCCESS') { dot.classList.add('done');   label.classList.add('done');   stStat.textContent = '✓'; }
      else if (stateVal === 'FAILED') { dot.classList.add('failed'); label.classList.add('failed'); stStat.textContent = '✗'; }
      else { dot.classList.add('active'); label.classList.add('active'); stStat.textContent = '…'; }
    } else {
      stStat.textContent = '';
    }
  });
}

function buildPipelineEntry(id) {
  const dev = STATE.devices.find(d => d.id === id);
  const label = dev ? `${dev.model} · ${dev.serial}` : id;

  const entry = document.createElement('div');
  entry.className = 'pipeline-entry';
  entry.dataset.id = id;

  const steps = STAGES.map(({ key, label: lbl }) => `
    <div class="pipeline-step">
      <div class="step-dot" data-stage="${key}"></div>
      <span class="step-label" data-stage="${key}">${lbl}</span>
      <span class="step-status" data-stage="${key}"></span>
    </div>`).join('');

  entry.innerHTML = `
    <div class="pipeline-device-label">📱 ${esc(label)}</div>
    <div class="pipeline-steps">${steps}</div>
    <div class="pipeline-bar-wrap"><div class="pipeline-bar"></div></div>`;
  return entry;
}

// ══════════════════════════════════════════════════════════════
// Log Console
// ══════════════════════════════════════════════════════════════

function appendLog(event) {
  const lvl = (event.level || 'INFO').toUpperCase();
  STATE.logs.push(event);
  if (STATE.logs.length > STATE.maxLogs) STATE.logs.shift();

  const console_el = $('log-console');
  const line = document.createElement('div');
  const cls = lvl.toLowerCase();
  const visible = STATE.logFilter === 'ALL' || STATE.logFilter === lvl;
  line.className = `console-line ${cls}${visible ? '' : ' hidden'}`;
  line.innerHTML = `
    <span class="ts">${esc(event.ts || '')}</span>
    <span class="lvl">${esc(lvl)}</span>
    <span class="stg">${esc((event.stage || '').substring(0,8))}</span>
    <span class="msg">${esc(event.message || '')}</span>`;

  console_el.appendChild(line);

  // Trim DOM
  while (console_el.children.length > STATE.maxLogs) {
    console_el.removeChild(console_el.firstChild);
  }

  // Auto-scroll
  if ($('toggle-autoscroll').checked) {
    console_el.scrollTop = console_el.scrollHeight;
  }
}

function clearLogs() {
  $('log-console').innerHTML = '';
  STATE.logs = [];
  showToast('Console effacée');
}

function applyLogFilter() {
  STATE.logFilter = $('log-level-filter').value;
  $('log-console').querySelectorAll('.console-line').forEach(line => {
    const lvl = [...line.classList].find(c => ['info','success','error','warning'].includes(c)) || '';
    const match = STATE.logFilter === 'ALL' || lvl.toUpperCase() === STATE.logFilter;
    line.classList.toggle('hidden', !match);
  });
}

// ══════════════════════════════════════════════════════════════
// History Table
// ══════════════════════════════════════════════════════════════

function renderHistory() {
  const tbody = $('history-body');
  if (!STATE.history.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="7">Aucun historique disponible</td></tr>';
    return;
  }

  const sorted = [...STATE.history].sort((a, b) => {
    let va = a[STATE.historySortCol] ?? '';
    let vb = b[STATE.historySortCol] ?? '';
    if (typeof va === 'string') va = va.toLowerCase();
    if (typeof vb === 'string') vb = vb.toLowerCase();
    return STATE.historySortDir === 'asc'
      ? (va < vb ? -1 : va > vb ? 1 : 0)
      : (va > vb ? -1 : va < vb ? 1 : 0);
  });

  tbody.innerHTML = sorted.map(r => `
    <tr>
      <td>${esc(r.timestamp || '')}</td>
      <td>${esc(r.model || '')}</td>
      <td style="font-family:var(--font-mono);font-size:0.7rem">${esc(r.serial_num || '')}</td>
      <td>${esc(r.ios_version || '')}</td>
      <td><span class="method-tag ${esc(r.method || '')}">${esc(r.method || '').replace('_',' ')}</span></td>
      <td><span class="status-tag ${(r.status || '').toLowerCase()}">${esc(r.status || '')}</span></td>
      <td>${r.duration_s ? r.duration_s + 's' : '—'}</td>
    </tr>`).join('');
}

function sortHistory(col) {
  if (STATE.historySortCol === col) {
    STATE.historySortDir = STATE.historySortDir === 'asc' ? 'desc' : 'asc';
  } else {
    STATE.historySortCol = col;
    STATE.historySortDir = 'desc';
  }
  renderHistory();
}

// ══════════════════════════════════════════════════════════════
// Manual Actions
// ══════════════════════════════════════════════════════════════

async function triggerAction(action) {
  const btn = $(`btn-${action.replace(/_/g, '-')}`);
  if (btn) { btn.disabled = true; btn.style.opacity = '0.6'; }

  const feedback = $('action-feedback');
  feedback.textContent = `⏳ Exécution: ${action}…`;

  try {
    const res = await fetch('/api/action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    });
    const data = await res.json();
    feedback.textContent = `✅ ${data.result || 'OK'}`;
    showToast(`✅ ${data.result || action}`);
  } catch (err) {
    feedback.textContent = `❌ Erreur réseau`;
    showToast(`❌ Action échouée: ${action}`, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.style.opacity = ''; }
    setTimeout(() => { feedback.textContent = ''; }, 5000);
  }
}

// ══════════════════════════════════════════════════════════════
// Proxy Badge
// ══════════════════════════════════════════════════════════════

function updateProxyBadge(status) {
  const badge = $('proxy-badge');
  if (!badge) return;
  badge.textContent = status || 'STOPPED';
  badge.className = 'proxy-badge' + (status === 'RUNNING' ? ' running' : '');
}

// ══════════════════════════════════════════════════════════════
// Toast
// ══════════════════════════════════════════════════════════════

let _toastTimer = null;
function showToast(msg, type = 'info') {
  const el = $('toast');
  el.textContent = msg;
  el.style.borderColor = type === 'error' ? 'rgba(255,77,106,0.5)' : 'rgba(0,212,255,0.4)';
  el.classList.add('show');
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.remove('show'), 3500);
}

// ══════════════════════════════════════════════════════════════
// Utilities
// ══════════════════════════════════════════════════════════════

function esc(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ══════════════════════════════════════════════════════════════
// Load initial logs snapshot on page load
// ══════════════════════════════════════════════════════════════

async function loadInitialLogs() {
  try {
    const res = await fetch('/api/logs?n=60');
    const { logs } = await res.json();
    logs.forEach(appendLog);
  } catch (_) {}
}

// ══════════════════════════════════════════════════════════════
// Init
// ══════════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', () => {
  connectSSE();
  startPolling();
  loadInitialLogs();
  setEngineStatus(false, '🔴 Connexion…');
});
