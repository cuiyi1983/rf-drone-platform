// ================================================================
// app.js — RF Drone Platform (refactored)
// ================================================================
'use strict';

const API_BASE = 'http://localhost:5100';

// ---- State -------------------------------------------------------
const S = {
  session_id: null,
  collecting: false,
  component_loaded: false,  // 组件是否已加载
  collector_connected: false, // 采集器是否已连接
  components: [],
  devices: [],
  inf_count: 0,
  // Dynamic table column visibility
  enabledColumns: new Set(['timestamp', 'freq_mhz', 'power_db', 'is_drone', 'drone_prob']),
  // Recent inference results (max 10)
  results: [],
  // Session config cache
  session_config: null,
};

// ---- DOM refs ----------------------------------------------------
const $ = id => document.getElementById(id);

// ---- Log --------------------------------------------------------
function log(msg) {
  const di = $('di');
  if (!di) return;
  const ts = new Date().toLocaleTimeString().slice(0, 8);
  di.innerHTML = '[' + ts + '] ' + msg + '\n' + di.innerHTML;
  if (di.innerHTML.length > 4000) di.innerHTML = di.innerHTML.slice(0, 4000);
}

// ---- REST helpers ------------------------------------------------
async function api(method, path, body = null) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(API_BASE + path, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ message: `HTTP ${res.status}` }));
    throw new Error(err.message || `HTTP ${res.status}`);
  }
  return res.json();
}

// ---- Socket.IO --------------------------------------------------
let socket = null;

function initSocket() {
  if (socket && socket.connected) return;

  socket = io(API_BASE, {
    transports: ['websocket', 'polling'],
    reconnection: true,
    reconnectionDelay: 1000,
    reconnectionAttempts: 10,
  });

  socket.on('connect', () => {
    log('Socket.IO 已连接 (id=' + socket.id + ')');
    updateStatusDot('ok', '采集器已连接');
    if (S.session_id) socket.emit('subscribe', { session_id: S.session_id });
  });

  socket.on('disconnect', () => {
    log('Socket.IO 已断开');
    updateStatusDot('bad', '采集器断开');
  });

  socket.on('inference_result', (data) => {
    handleInferenceResult(data);
  });

  socket.on('collector_stats', (data) => {
    handleCollectorStats(data);
  });

  socket.on('device_status', (data) => {
    const icons = { connected: '🟢', disconnected: '🔴', error: '🔴' };
    log(`${icons[data.event] || '⚪'} ${data.device_id} — ${data.detail || data.event}`);
    if (data.event === 'connected') {
      S.collector_connected = true;
      updateStatusDot('ok', '采集器已连接');
      updateButtonStates();
    } else if (data.event === 'disconnected' || data.event === 'error') {
      S.collector_connected = false;
      updateStatusDot('bad', '采集器未连接');
      updateButtonStates();
    }
  });

  socket.on('error', (data) => {
    log('Socket错误 #' + (data.code || '?') + ': ' + (data.message || ''));
  });
}

function subscribeSession(sessionId) {
  if (!socket || !socket.connected) return;
  socket.emit('subscribe', { session_id: sessionId });
  log('已订阅会话: ' + sessionId);
}

// ---- Inference result handler ------------------------------------
function handleInferenceResult(data) {
  S.inf_count++;
  $('cnt').textContent = S.inf_count;

  // Record
  S.results.unshift(data);
  if (S.results.length > 10) S.results.pop();

  // Render table
  renderResultsTable();

  // Update debug stats
  if (data.debug) {
    $('ds-inf-count').textContent = S.inf_count;
    $('ds-inf-time').textContent = (data.debug.inference_time_ms || 0).toFixed(1) + ' ms';
    if (data.debug.input_shape) $('ds-inf-in').textContent = data.debug.input_shape.join('x');
    if (data.debug.output_shape) $('ds-inf-out').textContent = data.debug.output_shape.join('x');
  }

  // Update cfg info
  if (S.session_config) updateConfigDisplay(S.session_config);
}

// ---- Collector stats handler -------------------------------------
function handleCollectorStats(data) {
  // Buffer bar
  const level = data.buffer_level || 0;
  const bufFill = $('buf-fill');
  const bufVal = $('buf-val');
  const pct = Math.min(100, Math.max(0, level));
  bufFill.style.width = pct + '%';
  bufFill.className = 'buf-fill' + (pct > 80 ? ' warn' : '');
  bufVal.textContent = (level !== null && level !== undefined) ? level : '--';

  // Stats
  $('buf-dropped').textContent = data.dropped != null ? (data.dropped * 100).toFixed(1) + '%' : '--';
  $('buf-fps').textContent = data.frames_per_second != null ? data.frames_per_second.toFixed(1) : '--';
  $('buf-frames').textContent = data.total_frames != null ? data.total_frames : '--';
  $('buf-coll').textContent = S.collecting ? '采集中' : '已停止';

  // Update btn state
  updateButtonStates();
}

// ---- Status dot helper -------------------------------------------
function updateStatusDot(cls, text) {
  const dot = $('dot');
  const stat = $('stat');
  const mdl = $('mdl');
  if (dot) dot.className = 'dot ' + cls;
  if (stat) stat.textContent = text;
  if (mdl) mdl.textContent = S.component_loaded ? '模型已加载' : '未加载模型';
}

// ---- Update button states -----------------------------------------
function updateButtonStates() {
  const btnS = $('btnS');
  const btnX = $('btnX');
  const recChk = $('recChk');
  if (!btnS || !btnX) return;

  // btnS: enabled when model loaded, collector connected, and not collecting
  btnS.disabled = S.collecting || !S.component_loaded || !S.collector_connected;
  // btnX: enabled when collecting=true
  btnX.disabled = !S.collecting;
  // IQ recording checkbox: enabled when collecting
  if (recChk) recChk.disabled = !S.collecting;
}

// ---- Dynamic column toggle ---------------------------------------
function initColumnToggle() {
  document.querySelectorAll('.col-toggle').forEach(btn => {
    btn.addEventListener('click', () => {
      const col = btn.dataset.col;
      if (S.enabledColumns.has(col)) {
        S.enabledColumns.delete(col);
        btn.classList.remove('active');
      } else {
        S.enabledColumns.add(col);
        btn.classList.add('active');
      }
      renderResultsTable();
    });
  });
}

// ---- Render results table ----------------------------------------
function renderResultsTable() {
  const tbody = $('rtbody');
  const thead = $('rthead');
  if (!tbody || !thead) return;

  // Always show at least timestamp col
  if (S.enabledColumns.size === 0) S.enabledColumns.add('timestamp');

  // Rebuild thead
  const colMap = {
    timestamp: '时间',
    freq_mhz: '频率(MHz)',
    power_db: '功率(dB)',
    is_drone: '检测结果',
    drone_prob: 'Drone%',
    noise_prob: 'Noise%',
    process_time_ms: '推理ms',
  };
  thead.innerHTML = '';
  const tr = document.createElement('tr');
  S.enabledColumns.forEach(col => {
    const th = document.createElement('th');
    th.dataset.col = col;
    th.textContent = colMap[col] || col;
    th.style.cursor = 'pointer';
    th.addEventListener('click', () => {
      document.querySelectorAll('.col-toggle').forEach(b => {
        if (b.dataset.col === col) b.click();
      });
    });
    tr.appendChild(th);
  });
  thead.appendChild(tr);

  if (S.results.length === 0) {
    tbody.innerHTML = '<tr><td colspan="' + S.enabledColumns.size + '" class="no-data">等待启动采数…</td></tr>';
    return;
  }

  tbody.innerHTML = '';
  S.results.forEach(r => {
    const tr = document.createElement('tr');
    S.enabledColumns.forEach(col => {
      const td = document.createElement('td');
      td.className = col === 'freq_mhz' || col === 'power_db' || col === 'process_time_ms' ? 'rfd' :
                     col === 'drone_prob' || col === 'noise_prob' ? 'rna' : 'rfd';

      switch (col) {
        case 'timestamp':
          td.textContent = r.timestamp ? new Date(r.timestamp * 1000).toLocaleTimeString().slice(0, 8) : '--';
          break;
        case 'freq_mhz':
          td.textContent = r.freq_mhz != null ? r.freq_mhz.toFixed(1) : '--';
          break;
        case 'power_db':
          td.textContent = r.power_db != null ? r.power_db.toFixed(1) + ' dB' : '--';
          break;
        case 'is_drone':
          td.textContent = r.is_drone ? 'DRONE' : 'NOISE';
          td.style.color = r.is_drone ? '#e74c3c' : '#27ae60';
          td.style.fontWeight = '600';
          break;
        case 'drone_prob':
          td.textContent = r.drone_prob != null ? (r.drone_prob * 100).toFixed(1) + '%' : '--';
          if (r.is_drone) td.style.color = '#e74c3c';
          break;
        case 'noise_prob':
          td.textContent = r.noise_prob != null ? (r.noise_prob * 100).toFixed(1) + '%' : '--';
          break;
        case 'process_time_ms':
          td.textContent = r.process_time_ms != null ? r.process_time_ms.toFixed(1) + ' ms' : '--';
          break;
        default:
          td.textContent = r[col] != null ? r[col] : '--';
      }
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
}

// ---- Update config display from session config -------------------
function updateConfigDisplay(cfg) {
  if (!cfg) return;
  // Inference config
  if (cfg.inference_config) {
    const ic = cfg.inference_config;
    $('cfg-component').textContent = ic.component_id || '--';
    $('cfg-freq').textContent = ic.center_freq_hz ? (ic.center_freq_hz / 1e6).toFixed(1) + ' MHz' : '--';
    $('cfg-sr').textContent = ic.sample_rate_hz ? (ic.sample_rate_hz / 1e6).toFixed(0) + ' MHz' : '--';
  }
  // Collector config
  if (cfg.collector_config) {
    const cc = cfg.collector_config;
    $('cfg-device').textContent = cc.uri || '--';
    $('cfg-freq').textContent = cc.center_freq_hz ? (cc.center_freq_hz / 1e6).toFixed(1) + ' MHz' : '--';
    $('cfg-sr').textContent = cc.sample_rate_rate_hz ? (cc.sample_rate_hz / 1e6).toFixed(0) + ' MHz' : '--';
    $('cfg-gain').textContent = cc.gain_db ? cc.gain_db + ' dB' : '--';
    $('cfg-bw').textContent = cc.bandwidth_hz ? (cc.bandwidth_hz / 1e6).toFixed(0) + ' MHz' : '--';
    $('crt').textContent = cc.uri || '--';
  }
}

// ---- Load components list -----------------------------------------
async function loadComponents() {
  try {
    const data = await api('GET', '/api/v1/components');
    S.components = data.components || [];
    const sel = $('msel');
    sel.innerHTML = '<option value="">-- 选择组件 --</option>';
    S.components.forEach(c => {
      const o = document.createElement('option');
      o.value = c.id;
      o.textContent = c.id + (c.version ? ' (v' + c.version + ')' : '');
      sel.appendChild(o);
    });
    log('加载 ' + S.components.length + ' 个推理组件');
  } catch (e) {
    log('加载组件失败: ' + e.message);
  }
}

// ---- Load component schema and render params --------------------
let currentComponentId = null;

async function loadComponentSchema(componentId) {
  if (!componentId) return;
  currentComponentId = componentId;

  try {
    const schema = await api('GET', '/api/v1/components/' + componentId + '/config-schema');
    renderSchemaParams(schema);
    log('已加载组件参数 schema');
    S.component_loaded = true;
    updateButtonStates();
    log('组件已加载，参数已呈现，请在观测页启动采数');
  } catch (e) {
    log('加载组件 schema 失败: ' + e.message);
  }
}

// ---- Transform JSON Schema (platform format) to app.js expected format ---
function transformSchema(configSchema) {
  if (!configSchema) return { parameters: {}, defaults: {} };
  const parameters = {};
  const defaults = {};
  for (const [key, prop] of Object.entries(configSchema)) {
    parameters[key] = {
      label: prop.title || key,
      type: prop.type === 'number' ? 'number' : (prop.type === 'boolean' ? 'boolean' : 'text'),
      minimum: prop.minimum,
      maximum: prop.maximum,
      step: prop.multipleOf || (prop.type === 'number' ? 'any' : undefined),
    };
    if (prop.default !== undefined) defaults[key] = prop.default;
    // Select for enum fields
    if (prop.enum) {
      parameters[key].type = 'select';
      parameters[key].options = prop.enum.map(v => ({ value: v, label: String(v) }));
    }
  }
  return { parameters, defaults };
}

function renderSchemaParams(schema) {
  const container = $('schema-params');
  const transformed = transformSchema(schema);
  const params = transformed.parameters;
  const defaults = transformed.defaults;

  if (!params || Object.keys(params).length === 0) {
    container.style.display = 'none';
    return;
  }
  container.style.display = 'grid';
  container.innerHTML = '';

  Object.entries(params).forEach(([key, param]) => {
    const item = document.createElement('div');
    item.className = 'param-item';

    const label = document.createElement('label');
    label.textContent = param.label || key;
    item.appendChild(label);

    if (param.type === 'select' && param.options) {
      const sel = document.createElement('select');
      sel.className = 'frm inp';
      sel.id = 'sp_' + key;
      param.options.forEach(opt => {
        const o = document.createElement('option');
        o.value = opt.value !== undefined ? opt.value : opt;
        o.textContent = opt.label || opt;
        sel.appendChild(o);
      });
      if (defaults[key] !== undefined) sel.value = defaults[key];
      item.appendChild(sel);
    } else if (param.type === 'boolean') {
      const chk = document.createElement('input');
      chk.type = 'checkbox';
      chk.id = 'sp_' + key;
      if (defaults[key] !== undefined) chk.checked = defaults[key];
      item.appendChild(chk);
    } else {
      const input = document.createElement('input');
      input.type = 'number';
      input.className = 'frm inp';
      input.id = 'sp_' + key;
      if (defaults[key] !== undefined) input.value = defaults[key];
      if (param.minimum !== undefined) input.min = param.minimum;
      if (param.maximum !== undefined) input.max = param.maximum;
      if (param.step && param.step !== 'any') input.step = param.step;
      item.appendChild(input);
    }

    container.appendChild(item);
  });
}

// ---- Collect schema param values ---------------------------------
function collectSchemaParams() {
  const params = {};
  document.querySelectorAll('#schema-params .param-item').forEach(item => {
    const input = item.querySelector('input, select');
    if (!input) return;
    const key = input.id.replace('sp_', '');
    let val = input.value;
    if (input.type === 'number') val = parseFloat(val);
    params[key] = val;
  });
  // Pluto-Repayer: IQ 文件路径
  if ($("deviceSel").value && $("deviceSel").value.includes("pluto-repeater")) {
    const fp = $("iqFilePath");
    if (fp && fp.value) params.iq_file_path = fp.value;
  }
  return params;
}

// ---- Start session (load component + connect collector) ----------
async function startSession() {
  const componentId = currentComponentId || $('msel').value;
  if (!componentId) {
    log('请先选择并加载推理组件');
    return;
  }

  $('btnS').disabled = true;
  log('正在启动会话…');

  try {
    const params = collectSchemaParams();
    const data = await api('POST', '/api/v1/session/start', {
      component_id: componentId,
      config: params,
    });

    S.session_id = data.session_id;
    S.collecting = true;

    // Cache session config
    S.session_config = data.config || {};

    updateStatusDot('run', '采数中');
    log('会话已启动: ' + S.session_id);

    // Subscribe socket
    initSocket();
    if (socket && socket.connected) subscribeSession(S.session_id);

    // Update UI
    updateButtonStates();
    updateConfigDisplay(S.session_config);

    // Update collector config from current settings
    S.session_config.collector_config = S.session_config.collector_config || {};
    const cp = $('collector-params');
    if (cp) cp.style.display = 'block';

    $('cst').textContent = '采集中';

    // Mark as collecting in dot
    const dot = $('dot');
    if (dot) dot.className = 'dot run';

  } catch (e) {
    $('btnS').disabled = false;
    log('启动会话失败: ' + e.message);
    // Try to fetch config anyway for display
    tryLoadSessionConfig();
  }
}

// ---- Try load current session config for display ---------------
async function tryLoadSessionConfig() {
  if (!S.session_id) return;
  try {
    const data = await api('GET', '/api/v1/session/' + S.session_id + '/config');
    S.session_config = data;
    updateConfigDisplay(data);
  } catch (e) {
    // ignore
  }
}

// ---- Stop session -----------------------------------------------
async function stopSession() {
  if (!S.session_id) return;
  $('btnX').disabled = true;
  log('正在停止会话…');

  try {
    await api('POST', '/api/v1/session/stop', { session_id: S.session_id });
    log('会话已停止');
  } catch (e) {
    log('停止会话失败: ' + e.message);
  }

  S.collecting = false;
  S.session_id = null;
  S.results = [];

  if (socket && socket.connected) {
    socket.emit('unsubscribe', { session_id: S.session_id });
  }

  // Reset UI
  updateStatusDot('ok', '采集器已连接');
  $('cst').textContent = '已停止';
  $('buf-coll').textContent = '已停止';
  $('cnt').textContent = '0';
  S.inf_count = 0;

  // Clear table
  renderResultsTable();

  // Reset button states
  updateButtonStates();
}

// ---- Scan devices -----------------------------------------------
async function scanDevices() {
  const btn = $('scanBtn');
  if (btn) btn.disabled = true;
  log('正在扫描设备…');

  try {
    const data = await api('POST', '/api/v1/devices/refresh');
    S.devices = data.devices || [];
    const sel = $('deviceSel');
    sel.innerHTML = '<option value="">-- 选择设备 --</option>';

    if (S.devices.length === 0) {
      const o = document.createElement('option');
      o.value = '';
      o.textContent = '未发现设备';
      o.disabled = true;
      sel.appendChild(o);
      log('未发现设备');
    } else {
      S.devices.forEach(d => {
        const o = document.createElement('option');
        o.value = d.id;
        o.textContent = (d.type || '?') + ' (' + (d.uri || d.id) + ')';
        sel.appendChild(o);
      });
      log('发现 ' + S.devices.length + ' 个设备');
    }
  } catch (e) {
    log('设备扫描失败: ' + e.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ---- Load device capabilities and init form ----------------------
async function loadDeviceCapabilities(deviceId) {
  if (!deviceId) return;
  try {
    // Pre-fill form with values from currently loaded model's collector config
    // (model recommends values before collector is connected)
    if (S.session_config && S.session_config.collector_config) {
      const cc = S.session_config.collector_config;
      if (cc.frequency) $('cf').value = (cc.frequency / 1e6).toFixed(1);
      if (cc.sample_rate) $('sr').value = (cc.sample_rate / 1e6).toFixed(0);
      if (cc.gain) $('gain').value = cc.gain;
      if (cc.buffer_size) $('bw').value = (cc.buffer_size / 1e6).toFixed(0);
    }
    log('设备参数已预填');
  } catch (e) {
    log('加载设备能力失败: ' + e.message);
  }
}

// ---- Apply collector params (apply config to collector) --------
async function applyCollectorParams() {
  const deviceId = $('deviceSel').value;
  if (!deviceId) {
    const aps = $('aps');
    if (aps) aps.innerHTML = '<span class="err"><i class="bi bi-x-circle"></i> 请先连接采集器</span>';
    setTimeout(() => { if (aps) aps.innerHTML = ''; }, 3000);
    return;
  }
  const aps = $('aps');
  if (aps) aps.innerHTML = '<span style="color:var(--mut)">应用配置中…</span>';
  try {
    const payload = {
      frequency: parseFloat($('cf')?.value || 5805) * 1e6,
      sample_rate: parseFloat($('sr')?.value || 60) * 1e6,
      gain: parseFloat($('gain')?.value || 20),
      buffer_size: 524288,
    };
    await api('POST', '/api/v1/collector/apply_component_config', {
      source: 'ui',
      component_id: 'sim-inference',
      requirements: {},
      config: payload,
    });
    if (aps) aps.innerHTML = '<span class="ok"><i class="bi bi-check-circle"></i> 配置已应用</span>';
    log('采集器参数已应用: ' + JSON.stringify(payload));
    // Save to session config
    if (!S.session_config) S.session_config = {};
    S.session_config.collector_config = { ...S.session_config.collector_config, ...payload };
    updateConfigDisplay(S.session_config);
  } catch (e) {
    if (aps) aps.innerHTML = '<span class="err"><i class="bi bi-x-circle"></i> ' + e.message + '</span>';
    log('应用配置失败: ' + e.message);
  }
  setTimeout(() => { if (aps) aps.innerHTML = ''; }, 3000);
}

// ---- Connect collector ----------------------------------------------
async function connectCollector() {
  const deviceId = $('deviceSel').value;
  if (!deviceId) {
    const aps = $('aps');
    if (aps) aps.innerHTML = '<span class="err"><i class="bi bi-x-circle"></i> 请先选择设备</span>';
    setTimeout(() => { if (aps) aps.innerHTML = ''; }, 3000);
    return;
  }
  const aps = $('aps');
  if (aps) aps.innerHTML = '<span style="color:var(--mut)">正在连接…</span>';
  try {
    await api('POST', '/api/v1/collector/connect', { device_uri: deviceId });
    S.collector_connected = true;
    updateButtonStates();
    if (aps) aps.innerHTML = '<span class="ok"><i class="bi bi-check-circle"></i> 连接成功</span>';
    log('采集器已连接: ' + deviceId);
    updateStatusDot('ok', '采集器已就绪');
    $('crt').textContent = deviceId;
    // Show collector params after connecting
    const cp = $('collector-params');
    if (cp) cp.style.display = 'block';
  } catch (e) {
    if (aps) aps.innerHTML = '<span class="err"><i class="bi bi-x-circle"></i> ' + e.message + '</span>';
    log('连接失败: ' + e.message);
  }
  setTimeout(() => { if (aps) aps.innerHTML = ''; }, 3000);
}

// ---- Tab navigation --------------------------------------------
function initTabs() {
  document.querySelectorAll('#tabs .nav-link').forEach(a => {
    a.addEventListener('click', e => {
      e.preventDefault();
      document.querySelectorAll('#tabs .nav-link').forEach(x => x.classList.remove('active'));
      a.classList.add('active');
      document.querySelectorAll('.pg').forEach(p => p.classList.remove('on'));
      const pgId = 'pg-' + a.dataset.pg;
      const pg = document.getElementById(pgId);
      if (pg) pg.classList.add('on');
    });
  });
}

// ---- Event bindings ---------------------------------------------
function bind() {
  // Model select + load
  $('msel').addEventListener('change', () => {
    const val = $('msel').value;
    if (val) loadComponentSchema(val);
  });

  $('mlbtn').addEventListener('click', () => {
    const val = $('msel').value;
    if (!val) { log('请先选择组件'); return; }
    loadComponentSchema(val);
    // 显示已加载提示
    const applied = $('cfg-applied');
    if (applied) applied.innerHTML = '<span class="ok">组件已加载，参数已呈现，请在观测页启动采数</span>';
    setTimeout(() => { if (applied) applied.innerHTML = ''; }, 4000);
  });

  // Device select
  $('deviceSel').addEventListener('change', () => {
    const isRepeater = $('deviceSel').value && $('deviceSel').value.includes('pluto-repeater');
    const rp = $('repeater-panel');
    if (rp) rp.style.display = isRepeater ? 'block' : 'none';
    loadDeviceCapabilities($('deviceSel').value);
  });

  // Scan devices
  $('scanBtn').addEventListener('click', scanDevices);

  // Apply collector config (connect + apply params)
  $('connBtn').addEventListener('click', connectCollector);
  $('apbtn').addEventListener('click', applyCollectorParams);

  // Pluto-Repayer: 文件选择
  $('browseIQBtn')?.addEventListener('click', () => {
    $('iqFileInput')?.click();
  });
  $('iqFileInput')?.addEventListener('change', (e) => {
    const fp = $('iqFilePath');
    const fi = $('iqFileInput');
    if (fp && fi?.files?.[0]) {
      fp.value = fi.files[0].name;
      fp.title = fi.files[0].path || fi.files[0].name;
    }
  });

// ---- Init --------------------------------------------------------
function init() {
  initTabs();
  initColumnToggle();
  bind();
  loadComponents();
  scanDevices();
  updateButtonStates();

  // Start socket connection early
  initSocket();

  // Periodically refresh session config for live display
  setInterval(() => {
    if (S.session_id) tryLoadSessionConfig();
  }, 5000);

  log('Web UI 已初始化');
}

document.addEventListener('DOMContentLoaded', init);