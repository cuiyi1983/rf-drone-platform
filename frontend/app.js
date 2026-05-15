// ================================================================
// app.js — RF Drone Platform Web UI
// ================================================================

const API_BASE = 'http://localhost:8080';

// ---- State -------------------------------------------------------
let currentSessionId = null;
let socket = null;
let isCollecting = false;
let detectionCount = 0;

// ---- DOM Refs ----------------------------------------------------
const elCollectorType   = document.getElementById('collector-type');
const elDeviceSelect    = document.getElementById('device-select');
const elBtnStart        = document.getElementById('btn-start');
const elBtnStop         = document.getElementById('btn-stop');
const elBtnRefresh      = document.getElementById('btn-refresh-devices');
const elSessionInfo     = document.getElementById('session-info');
const elSessionBadge    = document.getElementById('session-status-badge');
const elSessionIdDisp   = document.getElementById('session-id-display');
const elStatConn        = document.getElementById('stat-connection');
const elStatCollecting  = document.getElementById('stat-collecting');
const elStatFps         = document.getElementById('stat-fps');
const elStatDropped     = document.getElementById('stat-dropped');
const elStatTotalFrames = document.getElementById('stat-total-frames');
const elStatBufferLevel = document.getElementById('stat-buffer-level');
const elDetectionList   = document.getElementById('detection-list');
const elResultCount     = document.getElementById('result-count');
const elLogContainer    = document.getElementById('log-container');

// ---- Log --------------------------------------------------------
function addLog(message, type = 'info') {
  const entry = document.createElement('div');
  entry.className = `log-entry log-${type}`;
  entry.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
  elLogContainer.appendChild(entry);
  elLogContainer.scrollTop = elLogContainer.scrollHeight;
}

// ---- REST helpers -----------------------------------------------
async function api(method, path, body = null) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(API_BASE + path, opts);
  const data = await res.json();
  if (!res.ok) throw new Error(data.message || `HTTP ${res.status}`);
  return data;
}

// ---- Device loading ---------------------------------------------
async function loadDevices() {
  addLog('正在加载设备列表…', 'info');
  elDeviceSelect.innerHTML = '<option value="">加载中…</option>';
  try {
    const data = await api('GET', '/api/v1/devices');
    elDeviceSelect.innerHTML = '<option value="">— 选择设备 —</option>';
    if (data.devices && data.devices.length > 0) {
      data.devices.forEach(d => {
        const opt = document.createElement('option');
        opt.value = d.id;
        opt.textContent = `${d.type} (${d.uri}) ${d.connected ? '🟢' : '🔴'}`;
        elDeviceSelect.appendChild(opt);
      });
      elBtnStart.disabled = false;
      addLog(`发现 ${data.devices.length} 个设备`, 'info');
    } else {
      elDeviceSelect.innerHTML = '<option value="">未发现设备</option>';
      elBtnStart.disabled = true;
      addLog('未发现设备，请检查连接', 'warn');
    }
  } catch (err) {
    elDeviceSelect.innerHTML = '<option value="">加载失败</option>';
    elBtnStart.disabled = true;
    addLog(`设备加载失败: ${err.message}`, 'error');
    // Mock data for offline testing
    mockDevices();
  }
}

function mockDevices() {
  elDeviceSelect.innerHTML = `
    <option value="">— 选择设备 —</option>
    <option value="pluto_usb_2.6.5">PlutoSDR (usb:2.6.5) 🟢</option>
    <option value="simulator_local">Simulator (local) 🟢</option>
  `;
  elBtnStart.disabled = false;
  addLog('已加载 Mock 设备（离线模式）', 'info');
}

// ---- Session start / stop ----------------------------------------
async function startSession() {
  const deviceId = elDeviceSelect.value;
  if (!deviceId) { addLog('请先选择设备', 'warn'); return; }

  elBtnStart.disabled = true;
  addLog('正在启动会话…', 'info');

  try {
    // Fetch a default component
    const components = await api('GET', '/api/v1/components');
    const componentId = components.components?.[0]?.id || 'rfuav-two-stage';

    const data = await api('POST', '/api/v1/session/start', {
      component_id: componentId,
      config: { confidence_threshold: 0.5 }
    });

    currentSessionId = data.session_id;
    isCollecting = true;

    elSessionInfo.style.display = 'flex';
    elSessionBadge.textContent = data.status;
    elSessionBadge.className = 'badge running';
    elSessionIdDisp.textContent = `会话ID: ${currentSessionId}`;

    elBtnStop.disabled = false;
    addLog(`会话已启动: ${currentSessionId}`, 'info');

    // Subscribe via Socket.IO
    connectSocket();
    if (socket && socket.connected) {
      socket.emit('subscribe', { session_id: currentSessionId });
    }
  } catch (err) {
    elBtnStart.disabled = false;
    addLog(`启动会话失败: ${err.message}`, 'error');
    // Fallback: mock session
    mockStartSession();
  }
}

function mockStartSession() {
  currentSessionId = 'mock_' + Date.now();
  isCollecting = true;
  elSessionInfo.style.display = 'flex';
  elSessionBadge.textContent = 'running';
  elSessionBadge.className = 'badge running';
  elSessionIdDisp.textContent = `会话ID: ${currentSessionId} (Mock)`;
  elBtnStop.disabled = false;
  addLog('会话 Mock 启动成功', 'info');
  connectSocket();
  startMockStream();
}

async function stopSession() {
  if (!currentSessionId) return;
  elBtnStop.disabled = true;
  addLog('正在停止会话…', 'info');

  try {
    await api('POST', '/api/v1/session/stop', { session_id: currentSessionId });
    addLog('会话已停止', 'info');
  } catch (err) {
    addLog(`停止会话失败: ${err.message}`, 'warn');
  }

  cleanup();
}

function cleanup() {
  if (socket && socket.connected && currentSessionId) {
    socket.emit('unsubscribe', { session_id: currentSessionId });
    socket.disconnect();
    socket = null;
  }
  currentSessionId = null;
  isCollecting = false;
  detectionCount = 0;
  elResultCount.textContent = '0 条';
  elDetectionList.innerHTML = '<div class="empty-state">暂无检测结果，等待启动会话…</div>';
  elSessionInfo.style.display = 'none';
  elBtnStart.disabled = elDeviceSelect.value === '';
  elBtnStop.disabled = true;
  updateStats({ connection: '空闲', collecting: '已停止', fps: '—', dropped: '—', totalFrames: 0, bufferLevel: '—' });
}

// ---- Socket.IO --------------------------------------------------
function connectSocket() {
  if (socket && socket.connected) return;

  socket = io(API_BASE, {
    transports: ['websocket', 'polling'],
    reconnection: true,
    reconnectionDelay: 1000,
    reconnectionAttempts: 10
  });

  socket.on('connect', () => {
    addLog('Socket.IO 已连接', 'info');
    updateStats({ connection: '已连接' });
    if (currentSessionId) {
      socket.emit('subscribe', { session_id: currentSessionId });
    }
  });

  socket.on('disconnect', () => {
    addLog('Socket.IO 连接断开', 'warn');
    updateStats({ connection: '已断开' });
  });

  socket.on('inference_result', (data) => {
    renderDetection(data);
  });

  socket.on('collector_stats', (data) => {
    updateStats({
      fps: data.frames_per_second?.toFixed(1) ?? '—',
      dropped: ((data.dropped_rate ?? 0) * 100).toFixed(1) + '%',
      totalFrames: data.total_frames ?? 0,
      bufferLevel: data.buffer_level ?? '—',
      collecting: '采集中'
    });
  });

  socket.on('device_status', (data) => {
    const icons = { connected: '🟢', disconnected: '🔴', error: '🔴' };
    const icon = icons[data.event] || '⚪';
    addLog(`设备状态: ${icon} ${data.device_id} — ${data.detail || data.event}`, data.event === 'error' ? 'error' : 'info');
    if (data.event === 'disconnected' || data.event === 'error') {
      updateStats({ connection: data.event });
    }
  });

  socket.on('error', (data) => {
    addLog(`错误 #${data.code}: ${data.message}`, 'error');
  });
}

// ---- Render detection -------------------------------------------
function renderDetection(data) {
  if (!data.detections || data.detections.length === 0) return;

  // Remove empty state if present
  const empty = elDetectionList.querySelector('.empty-state');
  if (empty) empty.remove();

  // Keep max 50 entries visible
  const entries = elDetectionList.querySelectorAll('.detection-entry');
  if (entries.length >= 50) entries[entries.length - 1].remove();

  const entry = document.createElement('div');
  entry.className = 'detection-entry';

  const time = new Date((data.timestamp || Date.now() / 1000) * 1000).toLocaleTimeString();
  const conf = (data.detections[0].confidence * 100).toFixed(1);

  entry.innerHTML = `
    <span class="det-time">${time}</span>
    <span class="det-model">${data.detections[0].model}</span>
    <span class="det-conf" style="background:${confColor(conf)}">${conf}%</span>
    <span class="det-freq">${(data.detections[0].frequency / 1e6).toFixed(1)} MHz</span>
    ${data.debug ? `<span class="det-debug">${data.debug.inference_time_ms?.toFixed(1) || '?'} ms</span>` : ''}
  `;

  elDetectionList.insertBefore(entry, elDetectionList.firstChild);

  detectionCount++;
  elResultCount.textContent = `${detectionCount} 条`;
}

function confColor(conf) {
  if (conf >= 90) return '#22c55e';
  if (conf >= 70) return '#f59e0b';
  return '#ef4444';
}

// ---- Update stats display ----------------------------------------
function updateStats(stats) {
  if (stats.connection    !== undefined) elStatConn.textContent        = stats.connection;
  if (stats.collecting    !== undefined) elStatCollecting.textContent  = stats.collecting;
  if (stats.fps           !== undefined) elStatFps.textContent         = stats.fps;
  if (stats.dropped       !== undefined) elStatDropped.textContent     = stats.dropped;
  if (stats.totalFrames   !== undefined) elStatTotalFrames.textContent = stats.totalFrames;
  if (stats.bufferLevel   !== undefined) elStatBufferLevel.textContent = stats.bufferLevel;
}

// ---- Mock data stream (offline testing) -------------------------
let mockInterval = null;

function startMockStream() {
  if (mockInterval) clearInterval(mockInterval);
  const models = ['DJI_MAVIC3_PRO', 'DJI_PHANTOM4', 'DJI_MINI3_PRO', 'DJI_AIR3'];
  mockInterval = setInterval(() => {
    if (!isCollecting) return;

    const conf = (0.7 + Math.random() * 0.28).toFixed(3);
    const freq = 5800 + Math.floor(Math.random() * 50);

    renderDetection({
      session_id: currentSessionId,
      frame_id: Date.now(),
      timestamp: Date.now() / 1000,
      detections: [{
        model: models[Math.floor(Math.random() * models.length)],
        confidence: parseFloat(conf),
        frequency: freq * 1e6
      }],
      debug: {
        inference_time_ms: (8 + Math.random() * 5).toFixed(2)
      }
    });

    const fps = (4 + Math.random() * 3).toFixed(1);
    const dropped = (Math.random() * 0.05).toFixed(3);
    updateStats({
      fps: fps,
      dropped: (parseFloat(dropped) * 100).toFixed(1) + '%',
      totalFrames: Math.floor(5000 + Math.random() * 1000),
      bufferLevel: Math.floor(20 + Math.random() * 30),
      collecting: '采集中'
    });
  }, 800);
  addLog('Mock 数据流已启动（离线模式）', 'info');
}

// ---- Event listeners ---------------------------------------------
elBtnStart.addEventListener('click', startSession);
elBtnStop.addEventListener('click', () => {
  if (mockInterval) { clearInterval(mockInterval); mockInterval = null; }
  stopSession();
});
elBtnRefresh.addEventListener('click', loadDevices);

elCollectorType.addEventListener('change', () => {
  addLog(`采集器类型切换为: ${elCollectorType.value}`, 'info');
  loadDevices();
});

// ---- Init --------------------------------------------------------
loadDevices();
addLog('Web UI 已加载，等待设备初始化…', 'info');