/* =========================================================================
   AmpPulse AI — Frontend Application
   Complete dashboard: live monitoring, relay control, energy analytics,
   bill prediction, monthly reports, and AI-powered insights.
   ========================================================================= */

const API_BASE = window.AMPPULSE_API_BASE
  || `http://${window.location.hostname || "localhost"}:8000`;
const AUTH_TOKEN_KEY = "amppulse_token";

// ==================== API Client ====================
const Api = {
  token() { return localStorage.getItem(AUTH_TOKEN_KEY); },
  setToken(t) { localStorage.setItem(AUTH_TOKEN_KEY, t); },
  clearToken() { localStorage.removeItem(AUTH_TOKEN_KEY); },

  async _request(path, opts = {}) {
    const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
    const token = this.token();
    if (token) headers["Authorization"] = `Bearer ${token}`;
    let resp;
    try {
      resp = await fetch(`${API_BASE}${path}`, { ...opts, headers });
    } catch (e) {
      throw { code: "BACKEND_UNREACHABLE", message: "Cannot reach the AmpPulse backend. Is it running?" };
    }
    let data = null;
    try { data = await resp.json(); } catch (e) { /* non-JSON */ }
    if (!resp.ok) {
      const err = (data && data.error) || { code: "UNKNOWN_ERROR", message: `Request failed (${resp.status})` };
      throw err;
    }
    return data;
  },

  async health() {
    try { await this._request("/api/v1/health"); return true; } catch (e) { return false; }
  },
  async login(email, password) {
    const data = await this._request("/api/v1/auth/login", {
      method: "POST", body: JSON.stringify({ email, password })
    });
    this.setToken(data.access_token);
    return data.user;
  },
  async register(email, password, name) {
    const data = await this._request("/api/v1/auth/register", {
      method: "POST", body: JSON.stringify({ email, password, name: name || undefined })
    });
    this.setToken(data.access_token);
    return data.user;
  },
  async listDevices() {
    const data = await this._request("/api/v1/devices");
    return data.devices;
  },
  async deviceStatus(deviceId) {
    return this._request(`/api/v1/devices/${deviceId}/status`);
  },
  async deviceHistory(deviceId, limit = 200) {
    const data = await this._request(`/api/v1/devices/${deviceId}/data?limit=${limit}`);
    return data.readings;
  },
  async deviceSummary(deviceId) {
    return this._request(`/api/v1/devices/${deviceId}/summary`);
  },
  async sendCommand(deviceId, channel, value) {
    return this._request(`/api/v1/devices/${deviceId}/commands`, {
      method: "POST",
      body: JSON.stringify({ action: "relay_set", channel, value })
    });
  },
  async renameChannels(deviceId, labels) {
    return this._request(`/api/v1/devices/${deviceId}/channels`, {
      method: "PATCH",
      body: JSON.stringify({ channel_labels: labels })
    });
  },
  /* ---- Saved per-user ESP32 direct connections (keyed by login) ----
   * Each user can save MULTIPLE ESP32 addresses and switch between them. */
  async listEsp32s() {
    const data = await this._request("/api/v1/user/esp32s");
    return data.devices || [];
  },
  async addEsp32(ip, port = 80, name) {
    return this._request("/api/v1/user/esp32", {
      method: "POST",
      body: JSON.stringify({ ip, port, name: name || undefined })
    });
  },
  async updateEsp32(id, patch) {
    return this._request(`/api/v1/user/esp32/${id}`, {
      method: "PUT",
      body: JSON.stringify(patch)
    });
  },
  async deleteEsp32(id) {
    return this._request(`/api/v1/user/esp32/${id}`, { method: "DELETE" });
  },
};

/* Direct fetch to the ESP32's own web server (port 80). No auth - it is a
 * plain local Wi-Fi device. Returns parsed JSON body, or throws on error. */
async function esp32Fetch(path, opts = {}) {
  const cfg = State.esp32;
  if (!cfg.ip) throw { code: "NO_ESP32", message: "No ESP32 IP configured." };
  const url = `http://${cfg.ip}:${cfg.port}${path}`;
  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), 3000);
  try {
    const resp = await fetch(url, { ...opts, signal: controller.signal });
    clearTimeout(t);
    if (!resp.ok) throw { code: "ESP32_HTTP", message: `ESP32 responded ${resp.status}` };
    const text = await resp.text();
    try { return JSON.parse(text); } catch (e) { return { _text: text }; }
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") {
      throw { code: "ESP32_TIMEOUT", message: `ESP32 at ${cfg.ip}:${cfg.port} did not respond in time.` };
    }
    throw { code: "ESP32_UNREACHABLE", message: `Cannot reach ESP32 at ${cfg.ip}:${cfg.port}.` };
  }
}

// ==================== Application State ====================
const State = {
  devices: [],
  activeDeviceId: null,
  latestReading: null,
  history: [],
  summary: null,
  deviceStatus: "offline",
  lastSeenAt: null,
  currentTab: "Overview",
  pollTimer: null,
  userName: "User",
  /* ESP32 direct connection (user-set IP, saved per login via backend).
   * `State.esp32` mirrors the ACTIVE device; `State.esp32s` is the full
   * saved device list (multiple ESP32s supported) and `activeEsp32Id`
   * points at whichever saved device is currently driving the dashboard.
   * direct=true  -> live data + relay control go straight to the device.
   * direct=false -> everything goes through the FastAPI backend. */
  activeEsp32Id: null,
  esp32s: [],                 // saved devices: [{id,name,ip,port,connected,latency_ms,error}]
  esp32Form: { name: "ESP32", ip: "", port: 80 },  // "add a device" form
  esp32: {
    ip: "",
    port: 80,
    name: "",
    connected: false,
    latencyMs: null,
    error: null,
    direct: false,
    draft: "",
    live: null,       // latest reading fetched directly from the ESP32
    lastLiveAt: null, // Date.now() of the last successful ESP32 fetch
    backendWarn: null, // non-fatal error from saving the IP to the backend
  },
}

// Optimistic relay state: channel -> "ON"/"OFF" (cleared after 4s confirmation)
const pendingRelay = {};

// ==================== Navigation ====================
const NAV_ITEMS = [
  { key: "Overview",      icon: "⌂", label: "Overview" },
  { key: "Live Monitor",  icon: "◉", label: "Live Monitor" },
  { key: "My Appliances", icon: "▦", label: "My Appliances" },
  { key: "ESP32 Connect", icon: "📡", label: "ESP32 Connect" },
  { key: "Energy Usage",  icon: "⚡", label: "Energy Usage" },
  { key: "Bill Prediction", icon: "₹", label: "Bill Prediction" },
  { key: "Monthly Reports", icon: "📊", label: "Monthly Reports" },
  { key: "AI Insights",   icon: "✦", label: "AI Insights" },
];

// ==================== UI Helpers ====================
function toast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove("show"), 3000);
}

function fmt(v, d = 2, u = "") {
  if (v === undefined || v === null || isNaN(v)) return "\u2014";
  return `${Number(v).toFixed(d)}${u}`;
}

function smoothScroll(id) {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth" });
}

function toggleNav() {
  const nav = document.querySelector(".nav-links");
  if (nav) nav.style.display = nav.style.display === "flex" ? "none" : "flex";
}

function toggleSidebar() {
  document.getElementById("sidebar")?.classList.toggle("open");
}

// ==================== Landing / Login ====================
function showLanding() {
  if (State.pollTimer) { clearInterval(State.pollTimer); State.pollTimer = null; }
  document.getElementById("dashboard").classList.add("hidden");
  document.getElementById("landing").classList.remove("hidden");
  document.querySelector(".topbar").classList.remove("hidden");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function openLogin() {
  document.getElementById("loginModal").classList.remove("hidden");
  authMode = "login";
  renderAuthMode();
  document.getElementById("loginId").focus();
}

let authMode = "login"; // "login" | "register"

function toggleAuthMode() {
  authMode = authMode === "login" ? "register" : "login";
  renderAuthMode();
  const el = authMode === "login" ? "loginId" : "regName";
  setTimeout(() => document.getElementById(el).focus(), 0);
}

function renderAuthMode() {
  const register = authMode === "register";
  document.getElementById("authTitle").textContent = register ? "Create your account" : "Welcome back";
  document.getElementById("authSubtitle").textContent = register
    ? "Register to start saving your energy dashboard."
    : "Sign in to your energy dashboard.";
  document.getElementById("loginFields").classList.toggle("hidden", register);
  document.getElementById("registerFields").classList.toggle("hidden", !register);
  const toggle = document.getElementById("authToggle");
  if (toggle) {
    toggle.innerHTML = register
      ? `Already have an account? <a href="#" onclick="toggleAuthMode();return false;">Sign in</a>`
      : `Don't have an account? <a href="#" onclick="toggleAuthMode();return false;">Create one</a>`;
  }
  const errEl = document.getElementById("loginError");
  errEl.style.display = "none";
}

function closeModal() {
  document.getElementById("loginModal").classList.add("hidden");
  document.getElementById("loginError").style.display = "none";
}

function logout() {
  Api.clearToken();
  State.activeEsp32Id = null;
  State.esp32s = [];
  State.esp32Form = { name: "ESP32", ip: "", port: 80 };
  State.esp32 = { ip: "", port: 80, name: "", connected: false, latencyMs: null, error: null, direct: false, draft: "", live: null, lastLiveAt: null, backendWarn: null };
  showLanding();
  toast("Logged out successfully");
}

async function enterDashboard() {
  const email = document.getElementById("loginId").value.trim();
  const password = document.getElementById("loginPassword").value;
  const errEl = document.getElementById("loginError");
  errEl.style.display = "none";

  if (!email || !password) {
    errEl.textContent = "Please enter email and password.";
    errEl.style.display = "block";
    return;
  }

  try {
    const user = await Api.login(email, password);
    finishLogin(user);
  } catch (err) {
    errEl.textContent = err.message || "Login failed. Check your credentials.";
    errEl.style.display = "block";
  }
}

async function registerAndEnter() {
  const name = document.getElementById("regName").value.trim();
  const email = document.getElementById("regEmail").value.trim();
  const password = document.getElementById("regPassword").value;
  const confirm = document.getElementById("regConfirm").value;
  const errEl = document.getElementById("loginError");
  errEl.style.display = "none";

  if (!email || !password) {
    errEl.textContent = "Please enter email and password.";
    errEl.style.display = "block";
    return;
  }
  if (password.length < 6) {
    errEl.textContent = "Password must be at least 6 characters.";
    errEl.style.display = "block";
    return;
  }
  if (password !== confirm) {
    errEl.textContent = "Passwords do not match.";
    errEl.style.display = "block";
    return;
  }

  try {
    const user = await Api.register(email, password, name);
    finishLogin(user);
    toast(`Account created \u2014 welcome, ${user.full_name || user.email.split("@")[0]}!`);
  } catch (err) {
    errEl.textContent = err.message || "Registration failed.";
    errEl.style.display = "block";
  }
}

function finishLogin(user) {
  State.userName = user.full_name?.trim() || user.email?.split("@")[0] || "User";
  closeModal();
  document.getElementById("landing").classList.add("hidden");
  document.querySelector(".topbar").classList.add("hidden");
  document.getElementById("dashboard").classList.remove("hidden");
  document.getElementById("userAvatar").textContent = State.userName[0].toUpperCase();
  window.scrollTo(0, 0);
  startDashboard();
}

// ==================== Dashboard Init ====================
async function startDashboard() {
  renderSidebar();
  try {
    State.devices = await Api.listDevices();
    State.activeDeviceId = State.devices[0]?.device_id || null;
  } catch (e) {
    toast(e.message || "Could not load devices");
  }
  await loadEsp32Settings();   // restore the user's saved ESP32 IP (per login)
  await refreshData();
  if (State.pollTimer) clearInterval(State.pollTimer);
  State.pollTimer = setInterval(refreshData, 3000);
}

/* Restore the logged-in user's saved ESP32 devices from the backend and
 * auto-activate one on login. Preference: the previously active device, else
 * the first reachable one. Direct mode only turns on when it answers. */
async function loadEsp32Settings() {
  try {
    const list = await Api.listEsp32s();
    State.esp32s = Array.isArray(list) ? list : [];
  } catch (e) {
    /* Backend unreachable or not logged in - direct mode stays off. */
    return;
  }

  const pick = State.esp32s.find(d => d.id === State.activeEsp32Id)
    || State.esp32s.find(d => d.connected)
    || State.esp32s[0]
    || null;
  if (!pick) {
    State.activeEsp32Id = null;
    return;
  }

  State.activeEsp32Id = pick.id;
  State.esp32.ip = pick.ip;
  State.esp32.port = pick.port || 80;
  State.esp32.name = pick.name || "ESP32";
  State.esp32.draft = pick.ip;
  State.esp32.connected = !!pick.connected;
  State.esp32.latencyMs = pick.latency_ms ?? null;
  State.esp32.error = pick.error ?? null;
  State.esp32.direct = !!pick.connected;
  if (State.esp32.direct) {
    toast(`ESP32 connected: ${State.esp32.ip}`);
  }
}

async function refreshData() {
  const badge = document.getElementById("backendLiveDot");

  // ---- Backend (login, analytics, history, reports) ----
  try {
    const healthy = await Api.health();
    if (badge) {
      badge.classList.toggle("offline", !healthy);
      badge.querySelector(".status-text").textContent = healthy ? "LIVE" : "OFFLINE";
    }
    if (State.activeDeviceId) {
      const [status, history, summary] = await Promise.all([
        Api.deviceStatus(State.activeDeviceId),
        Api.deviceHistory(State.activeDeviceId, 200),
        Api.deviceSummary(State.activeDeviceId),
      ]);
      State.latestReading = status.latest_reading;
      State.history = history;
      State.summary = summary;
      State.deviceStatus = status.status;
      State.lastSeenAt = status.last_seen_at;
    }
  } catch (e) {
    if (badge) {
      badge.classList.add("offline");
      badge.querySelector(".status-text").textContent = "OFFLINE";
    }
  }

  // ---- ESP32 direct mode (live data straight from the device) ----
  if (State.esp32.direct && State.esp32.ip) {
    try {
      const live = await esp32Fetch("/data");
      State.esp32.live = normalizeEsp32Reading(live);
      State.esp32.lastLiveAt = Date.now();
      State.esp32.connected = true;
      State.esp32.error = null;
      /* Live telemetry supersedes backend readings for the live views. */
      State.latestReading = State.esp32.live;
      State.deviceStatus = "online";
    } catch (e) {
      State.esp32.connected = false;
      State.esp32.error = e.message || "ESP32 unreachable";
    }
  }

  if (!document.getElementById("dashboard").classList.contains("hidden")) {
    renderActiveTab();
  }
}

/* The standalone ESP32 sketch returns {voltage,current,power,temperature,
 * humidity,relay1,relay2}. Map it onto the same shape the backend readings
 * use so every view renders identically regardless of the data source. */
function normalizeEsp32Reading(raw) {
  return {
    voltage: Number(raw?.voltage) || 0,
    current: Number(raw?.current) || 0,
    power: Number(raw?.power) || 0,
    energy_kwh: Number(raw?.energy_kwh) || 0,
    frequency: Number(raw?.frequency) || 0,
    power_factor: Number(raw?.power_factor) || null,
    temperature: Number(raw?.temperature) || 0,
    humidity: Number(raw?.humidity) || 0,
    channel_status: {
      "1": raw?.relay1 ? "ON" : "OFF",
      "2": raw?.relay2 ? "ON" : "OFF",
    },
    device_ip: State.esp32.ip,
  };
}

// ==================== Sidebar ====================
function renderSidebar() {
  document.getElementById("sideNav").innerHTML = NAV_ITEMS.map((item, i) =>
    `<button class="${i === 0 ? "active" : ""}" onclick="selectTab('${item.key}', this)">
      <span class="nav-icon">${item.icon}</span>${item.label}
    </button>`
  ).join("");
  State.currentTab = "Overview";
}

function selectTab(key, el) {
  document.querySelectorAll(".sidebar-nav button").forEach(b => b.classList.remove("active"));
  el.classList.add("active");
  State.currentTab = key;
  renderActiveTab();
  document.getElementById("sidebar").classList.remove("open");
}

function renderActiveTab() {
  const views = {
    "Overview": homeView,
    "Live Monitor": liveMonitorView,
    "My Appliances": myAppliancesView,
    "ESP32 Connect": esp32ConnectView,
    "Energy Usage": energyUsageView,
    "Bill Prediction": billPredictionView,
    "Monthly Reports": monthlyReportsView,
    "AI Insights": aiInsightsView,
  };
  const render = views[State.currentTab] || homeView;
  const bar = State.currentTab === "ESP32 Connect" ? "" : connectionBar();
  document.getElementById("dashContent").innerHTML = bar + render();
  document.getElementById("dashRole").textContent = "Home";
  document.getElementById("dashTitle").textContent = "Home Energy Dashboard";
}

// ==================== Data Helpers ====================
function device() {
  return State.devices.find(d => d.device_id === State.activeDeviceId) || {};
}

/* True when we have a live data source: either the backend device is online
 * or the ESP32 direct connection is currently alive. */
function hasLive() {
  if (State.esp32.direct) return State.esp32.connected;
  return !!State.activeDeviceId && State.deviceStatus === "online";
}

function online() {
  if (State.esp32.direct) return State.esp32.connected;
  return State.deviceStatus === "online";
}

function relayState(ch) {
  if (pendingRelay[ch]) return pendingRelay[ch];
  return State.latestReading?.channel_status?.[String(ch)] || "OFF";
}

function channelLabel(ch) {
  return device().channel_labels?.[String(ch)] || `Appliance ${ch}`;
}

function applianceLoad(ch) {
  const r = State.latestReading || {};
  const total = r.power || 0;
  const onChannels = ["1", "2"].filter(c => relayState(c) === "ON").length;
  if (relayState(ch) !== "ON" || onChannels === 0) return 0;
  return total / onChannels;
}

function todayEnergy() { return State.summary?.today_energy_kwh ?? null; }
function todayCost() { return State.summary?.today_cost ?? null; }
function projectedCost() { return State.summary?.projected_month_cost ?? null; }
function rate() {
  const c = State.summary?.today_cost;
  const e = State.summary?.today_energy_kwh;
  return (c && e && e > 0) ? c / e : 8;
}

// ==================== Component Builders ====================
function kpiCard(label, value, sub = "", subClass = "") {
  return `<div class="kpi">
    <div class="kpi-label">${label}</div>
    <div class="kpi-value">${value}</div>
    <div class="kpi-sub ${subClass}">${sub}</div>
  </div>`;
}

function kpiGrid(arr) {
  return `<div class="kpi-grid">${arr.join("")}</div>`;
}

function relayCard(ch, detailed = false) {
  const label = channelLabel(ch);
  const state = relayState(ch);
  const isOn = state === "ON";
  const w = detailed ? applianceLoad(ch) : 0;
  const gpio = State.esp32.direct ? (ch === 1 ? 25 : 26) : (ch === 1 ? 26 : 27);
  return `
    <div class="relay-card ${isOn ? "is-on" : "is-off"}">
      <div class="relay-head">
        <div class="relay-icon">${ch === 1 ? "❄" : "⚡"}</div>
        <div class="relay-name">
          <b>${label}</b>
          <small>Relay channel ${ch} &middot; ESP32 GPIO ${gpio} &middot; ${State.esp32.direct ? "direct" : "backend"}</small>
        </div>
        <span class="relay-status ${isOn ? "on" : "off"}">${isOn ? "ON" : "OFF"}</span>
      </div>
      ${detailed ? `<div class="relay-meta">
        <div><small>Est. Load</small><b>${fmt(w, 0, " W")}</b></div>
        <div><small>Source</small><b class="${State.esp32.direct ? "ok" : ""}">${State.esp32.direct ? "Direct Wi-Fi" : "Backend queue"}</b></div>
      </div>` : ""}
      <div class="relay-footer">
        <span>${isOn ? "Energy in use \u2014 tap to switch OFF" : "Sleeping \u2014 tap to switch ON"}</span>
        <label class="switch ${isOn ? "on" : "off"}" title="${isOn ? "Turn OFF" : "Turn ON"} wirelessly">
          <input type="checkbox" ${isOn ? "checked" : ""} onchange="controlChannel(${ch}, this.checked ? 'ON' : 'OFF')" />
          <span class="slider"></span>
        </label>
      </div>
    </div>`;
}

function emptyState(icon, title, desc) {
  return `<div class="empty-state">
    <div class="empty-state-icon">${icon}</div>
    <h3>${title}</h3>
    <p>${desc}</p>
  </div>`;
}

function insightCard(icon, title, body, type = "") {
  return `<div class="insight-card ${type}">
    <div class="insight-icon">${icon}</div>
    <div class="insight-body">
      <strong>${title}</strong>
      <p>${body}</p>
    </div>
  </div>`;
}

// ==================== Charts ====================
function powerChart() {
  const points = State.history.slice(-40);
  if (points.length < 2) {
    return `<svg viewBox="0 0 900 240" preserveAspectRatio="none">
      <defs><linearGradient id="cg" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0" stop-color="#22c55e" stop-opacity="0.2"/>
        <stop offset="1" stop-color="#22c55e" stop-opacity="0"/>
      </linearGradient></defs>
      <path d="M0 200 Q100 180 200 170 T400 130 T600 100 T900 40 V240 H0Z" fill="url(#cg)"/>
      <path d="M0 200 Q100 180 200 170 T400 130 T600 100 T900 40" fill="none" stroke="#22c55e" stroke-width="2.5"/>
    </svg>`;
  }
  const vals = points.map(p => p.power || 0);
  const min = Math.min(...vals), max = Math.max(...vals);
  const range = (max - min) || 1;
  const w = 900, h = 240, pad = 20;
  const step = w / (points.length - 1);
  const pts = vals.map((v, i) => ({
    x: i * step,
    y: h - pad - ((v - min) / range) * (h - pad * 2)
  }));
  const line = pts.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(" ");
  const area = line + ` L${pts[pts.length - 1].x.toFixed(1)} ${h} L0 ${h} Z`;
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <defs><linearGradient id="cg" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0" stop-color="#22c55e" stop-opacity="0.2"/>
      <stop offset="1" stop-color="#22c55e" stop-opacity="0"/>
    </linearGradient></defs>
    <path d="${area}" fill="url(#cg)"/>
    <path d="${line}" fill="none" stroke="#22c55e" stroke-width="2.5" stroke-linecap="round"/>
    ${pts.slice(-1).map(p => `<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="4" fill="#22c55e"/>`).join("")}
  </svg>`;
}

function voltageChart() {
  const points = State.history.slice(-40);
  if (points.length < 2) return powerChart();
  const vals = points.map(p => p.voltage || 0);
  const min = Math.min(...vals), max = Math.max(...vals);
  const range = (max - min) || 1;
  const w = 900, h = 240, pad = 20;
  const step = w / (points.length - 1);
  const pts = vals.map((v, i) => ({
    x: i * step,
    y: h - pad - ((v - min) / range) * (h - pad * 2)
  }));
  const line = pts.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(" ");
  const area = line + ` L${pts[pts.length - 1].x.toFixed(1)} ${h} L0 ${h} Z`;
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <defs><linearGradient id="vg" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0" stop-color="#06b6d4" stop-opacity="0.2"/>
      <stop offset="1" stop-color="#06b6d4" stop-opacity="0"/>
    </linearGradient></defs>
    <path d="${area}" fill="url(#vg)"/>
    <path d="${line}" fill="none" stroke="#06b6d4" stroke-width="2.5" stroke-linecap="round"/>
  </svg>`;
}

function dailyBars() {
  const daily = State.summary?.daily || [];
  if (!daily.length) return `<p style="color:var(--text-muted);font-size:13px;">Not enough energy data yet. Keep telemetry flowing to build this chart.</p>`;
  const vals = daily.map(d => d.energy_kwh);
  const max = Math.max(...vals) || 1;
  return `<div class="bar-list">${daily.slice(-14).map(d =>
    `<div class="bar-row">
      <span class="bar-label">${d.date}</span>
      <div class="bar-track"><span class="bar-fill" style="width:${(d.energy_kwh / max * 100).toFixed(1)}%"></span></div>
      <span class="bar-value">${fmt(d.energy_kwh, 2, " kWh")} &middot; &#8377;${fmt(d.estimated_cost, 2)}</span>
    </div>`
  ).join("")}</div>`;
}

function recentTable(limit = 10) {
  if (!State.history.length) return `<p style="color:var(--text-muted);font-size:13px;">No readings received yet.</p>`;
  const rows = State.history.slice(-limit).reverse().map(h => {
    const t = new Date(h.created_at).toLocaleString();
    const ch = h.channel_status || {};
    return `<tr>
      <td>${t}</td>
      <td>${fmt(h.voltage, 1, " V")}</td>
      <td>${fmt(h.current, 2, " A")}</td>
      <td>${fmt(h.power, 0, " W")}</td>
      <td>1:${ch["1"] || "OFF"} &middot; 2:${ch["2"] || "OFF"}</td>
    </tr>`;
  }).join("");
  return `<table class="area-table"><thead><tr><th>Time</th><th>Voltage</th><th>Current</th><th>Power</th><th>Relays</th></tr></thead><tbody>${rows}</tbody></table>`;
}

// ==================== AI Insights ====================
function generateInsights(maxItems = 6) {
  const r = State.latestReading || {};
  const out = [];

  if (!online()) {
    out.push({ icon: "⚠️", title: "Device appears offline", body: "No telemetry received recently. Check ESP32 power and Wi-Fi. Commands queue and apply when it reconnects.", type: "danger" });
    return out.slice(0, maxItems);
  }

  const power = r.power || 0;
  const avg = State.history.length
    ? State.history.reduce((s, h) => s + (h.power || 0), 0) / State.history.length
    : 0;

  if (avg > 0 && power > avg * 1.4) {
    out.push({ icon: "⚡", title: "Higher-than-normal load", body: `Live power ${fmt(power, 0, " W")} is ${Math.round((power / avg - 1) * 100)}% above your recent average (${fmt(avg, 0, " W")}).`, type: "warn" });
  }

  if ((r.temperature || 0) > 35) {
    out.push({ icon: "🌡️", title: "Warm near the meter", body: `${fmt(r.temperature, 1, "\u00b0C")} detected. Heat increases appliance loss and affects sensor accuracy.`, type: "warn" });
  }

  if ((r.voltage || 0) > 250) {
    out.push({ icon: "⚠️", title: "High voltage detected", body: `${fmt(r.voltage, 1, " V")} exceeds normal range. Sensitive electronics may be at risk.`, type: "danger" });
  }

  if ((r.voltage || 0) < 190 && r.voltage > 0) {
    out.push({ icon: "⚠️", title: "Low voltage detected", body: `${fmt(r.voltage, 1, " V")} is below normal. Motors may draw more current and overheat.`, type: "warn" });
  }

  for (const ch of ["1", "2"]) {
    if (relayState(ch) === "ON") {
      const w = applianceLoad(ch);
      const monthly = w / 1000 * 24 * 30 * rate();
      const save6 = w / 1000 * 6 * 30 * rate();
      out.push({
        icon: "💡",
        title: `Saving opportunity \u00b7 ${channelLabel(ch)}`,
        body: `At ~${fmt(w, 0, " W")} it costs ~\u20b9${fmt(monthly, 0)}/month if always on. Turning it OFF 6h/day saves ~\u20b9${fmt(save6, 0)}.`,
        type: "info",
      });
    }
  }

  const daily = State.summary?.daily || [];
  if (daily.length > 1) {
    const last = daily[daily.length - 1];
    const prev = daily[daily.length - 2];
    if (last && prev) {
      const diff = last.energy_kwh - prev.energy_kwh;
      if (Math.abs(diff) > 0.01) {
        out.push({
          icon: diff < 0 ? "✅" : "📈",
          title: diff < 0 ? "Usage is improving" : "Usage rose vs yesterday",
          body: `${fmt(Math.abs(diff), 2)} kWh ${diff < 0 ? "less" : "more"} than previous day (\u20b9${fmt(Math.abs(last.estimated_cost - prev.estimated_cost), 2)} difference).`,
          type: diff < 0 ? "" : "warn",
        });
      }
    }
  }

  const pf = r.power_factor;
  if (pf && pf < 0.85) {
    out.push({ icon: "⚡", title: "Low power factor", body: `Power factor is ${fmt(pf, 2)}. Consider power factor correction to improve efficiency and avoid utility penalties.`, type: "warn" });
  }

  if (!out.length) out.push({ icon: "✅", title: "All readings normal", body: "No unusual patterns detected. Your energy usage is within expected range.", type: "" });
  return out.slice(0, maxItems);
}

// ==================== Relay Control ====================
function controlChannel(ch, value) {
  const direct = State.esp32.direct && State.esp32.connected;
  if (!direct && !online()) {
    toast("Device offline \u2014 command queued, will apply on reconnect.");
  }
  pendingRelay[String(ch)] = value;
  renderActiveTab();

  if (direct) {
    /* ESP32 direct mode: hit the sketch's own endpoints. GET /relay1/on,
     * /relay1/off, /relay2/on, /relay2/off. Note the standalone sketch has a
     * single global relay on either side, so relay1 -> /relay1, etc. */
    const path = `/relay${ch}/${value.toLowerCase()}`;
    esp32Fetch(path).then(() => {
      toast(`${channelLabel(ch)} \u2192 ${value} (sent to ESP32 ${State.esp32.ip})`);
      setTimeout(() => {
        delete pendingRelay[String(ch)];
        refreshData();
      }, 1500);
    }).catch(err => {
      delete pendingRelay[String(ch)];
      renderActiveTab();
      toast(err.message || "Failed to control ESP32 relay");
    });
    return;
  }

  Api.sendCommand(State.activeDeviceId, ch, value).then(() => {
    toast(`${channelLabel(ch)} \u2192 ${value} (wireless command sent)`);
    setTimeout(() => {
      delete pendingRelay[String(ch)];
      refreshData();
    }, 4000);
  }).catch(err => {
    delete pendingRelay[String(ch)];
    renderActiveTab();
    toast(err.message || "Failed to send command");
  });
}

// ==================== ESP32 Direct Connection ====================
function connectionBar() {
  const e = State.esp32;
  const draft = e.draft !== undefined ? e.draft : e.ip;
  const statusCls = e.connected ? "ok" : (e.ip ? "warn" : "muted");
  const statusText = e.connected
    ? `● Connected to ${e.ip}${e.latencyMs != null ? ` \u00b7 ${e.latencyMs} ms` : ""}`
    : (e.ip ? `${e.error || "● ESP32 not responding"}` : "● Not connected \u2014 backend mode");
  const deviceOpts = State.esp32s.map(d =>
    `<option value="${d.id}" ${d.id === State.activeEsp32Id ? "selected" : ""}>${escapeHtml(d.name || "ESP32")} (${d.ip})</option>`).join("");
  return `
    <div class="conn-bar" id="connBar">
      <div class="conn-left">
        <span class="conn-icon">📡</span>
        <div class="conn-text">
          <b>ESP32 Direct Connection</b>
          <small>Live data + relay control straight from the device</small>
        </div>
      </div>
      <div class="conn-actions">
        ${State.esp32s.length
          ? `<select id="esp32Select" class="conn-select" onchange="esp32SelectChange(this.value)">
              ${deviceOpts}
            </select>`
          : ""}
        <input id="esp32IpInput" type="text" placeholder="192.168.1.7"
          value="${draft}" oninput="State.esp32.draft=this.value" spellcheck="false" />
        <button class="btn btn-primary btn-sm" onclick="connectEsp32()">Connect</button>
        ${e.connected ? `<button class="btn btn-danger btn-sm" onclick="disconnectEsp32()">Disconnect</button>` : ""}
      </div>
      <div class="conn-status ${statusCls}"><span class="conn-dot"></span>${statusText}</div>
      ${e.backendWarn && e.connected ? `<div class="conn-status warn" style="margin-left:auto">
        <span class="conn-dot"></span>IP saved on device only \u2014 backend rejected it (${e.backendWarn})</div>` : ""}
    </div>`;
}

function esp32SelectChange(id) {
  const dev = State.esp32s.find(d => String(d.id) === String(id));
  if (!dev) return;
  State.esp32.draft = dev.ip;
  State.esp32.port = dev.port || 80;
  connectEsp32();
}

/* Connect (and save) the ESP32 IP for the current login. The backend stores
 * it per user so the next login resumes the direct connection automatically.
 * Multiple devices are supported - each connect becomes a saved, switchable
 * entry; the dashboard drives whichever is active. */
async function connectEsp32() {
  const ip = (document.getElementById("esp32IpInput")?.value || State.esp32.draft || "").trim();
  const port = State.esp32.port || 80;
  if (!ip) {
    toast("Enter the ESP32 IP address first (shown in the Serial Monitor).");
    return;
  }
  State.esp32.draft = ip;
  State.esp32.connecting = true;
  renderActiveTab();
  toast(`Connecting to ESP32 at ${ip}...`);

  /* 1) Find the saved entry, or add a new one - best effort, NEVER fatal.
   *    A stale/old backend (or a missing route) must not stop a direct
   *    connection. */
  let backendErr = null;
  let rec = State.esp32s.find(d => d.ip === ip && (d.port || 80) === port) || null;
  if (!rec) {
    try {
      const res = await Api.addEsp32(ip, port, State.esp32Form.name || "ESP32");
      rec = res.device || null;
    } catch (e) {
      backendErr = e.message || "Backend save failed";
    }
  }

  /* 2) Confirm end-to-end by fetching real data from /data. This is the only
   *    test that matters - it works even if the backend route is down. */
  let live = null;
  let directErr = null;
  let latency = null;
  try {
    const t0 = performance.now();
    const raw = await esp32Fetch("/data");
    latency = Math.round(performance.now() - t0);
    live = normalizeEsp32Reading(raw);
  } catch (fetchErr) {
    directErr = fetchErr.message || `Cannot reach ESP32 at ${ip}.`;
  }

  if (live) {
    State.activeEsp32Id = rec ? rec.id : null;
    State.esp32.ip = ip;
    State.esp32.port = port;
    State.esp32.name = rec ? rec.name : "ESP32";
    State.esp32.connected = true;
    State.esp32.latencyMs = latency;
    State.esp32.error = null;
    State.esp32.backendWarn = backendErr;   // "Request failed (404)" etc., non-fatal
    State.esp32.direct = true;
    State.esp32.live = live;
    State.esp32.lastLiveAt = Date.now();
    State.latestReading = live;
    toast(backendErr
      ? `ESP32 connected: ${ip} \u2014 but the backend rejected saving it.`
      : `ESP32 connected: ${ip} \u2014 live data flowing.`);
  } else {
    State.esp32.ip = ip;
    State.esp32.port = port;
    State.esp32.connected = false;
    State.esp32.backendWarn = null;
    State.esp32.direct = false;
    State.esp32.error = directErr || backendErr || `Could not connect to ESP32 at ${ip}.`;
    toast(State.esp32.error);
  }
  State.esp32.connecting = false;
  syncEsp32List();
  renderActiveTab();
  refreshData();
}

/* Re-pull the backend's device list (probes included) and keep the active id. */
async function syncEsp32List() {
  try {
    State.esp32s = await Api.listEsp32s();
  } catch (e) { /* keep last known list */ }
}

/* Switch the active dashboard device to one of the saved ESP32s. */
async function activateEsp32Dev(id) {
  const dev = State.esp32s.find(d => String(d.id) === String(id));
  if (!dev) { toast("Device not found."); return; }
  State.esp32.draft = dev.ip;
  State.esp32.port = dev.port || 80;
  State.activeEsp32Id = dev.id;
  connectEsp32();
}

/* Forget one saved device completely. */
async function deleteEsp32Dev(id) {
  if (!confirm(`Remove this ESP32 from your saved devices?`)) return;
  try {
    const res = await Api.deleteEsp32(id);
    State.esp32s = res.devices || [];
    if (String(State.activeEsp32Id) === String(id)) {
      await disconnectEsp32(true);
    }
    renderActiveTab();
    toast("ESP32 removed from your saved devices.");
  } catch (e) {
    toast(e.message || "Could not remove device.");
  }
}

/* Add a device from the "add" form and connect to it. */
async function addEsp32Dev() {
  const name = (document.getElementById("devNameInput")?.value || "").trim();
  const ip = (document.getElementById("devIpInput")?.value || "").trim();
  const port = Number(document.getElementById("devPortInput")?.value) || 80;
  State.esp32Form = { name, ip, port };
  if (!ip) { toast("Enter the ESP32 IP address."); return; }
  State.esp32.draft = ip;
  State.esp32.port = port;
  const res = await connectEsp32();
  return res;
}

/* Turn the direct connection off and return to backend-only mode. The saved
 * device is KEPT in the list - only the active session is dropped. Pass
 * keepSaved=true when the caller already deleted the entry. */
async function disconnectEsp32(keepSaved) {
  State.activeEsp32Id = null;
  State.esp32.direct = false;
  State.esp32.connected = false;
  State.esp32.live = null;
  State.esp32.error = null;
  State.esp32.backendWarn = null;
  if (!keepSaved) syncEsp32List();
  /* Re-pull latest reading from the backend device if there is one. */
  if (State.activeDeviceId) {
    try {
      const status = await Api.deviceStatus(State.activeDeviceId);
      State.latestReading = status.latest_reading;
      State.deviceStatus = status.status;
    } catch (e) { /* ignore */ }
  }
  renderActiveTab();
  toast("ESP32 direct connection cleared. Backend mode restored.");
}

// ==================== VIEW: ESP32 Connect ====================
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function esp32ConnectView() {
  const e = State.esp32;
  const draft = e.draft !== undefined ? e.draft : e.ip;
  const f = State.esp32Form;
  const issueMsg = !State.esp32.ip && !State.activeDeviceId
    ? "Static override \u2014 you can see live data even before you register a backend device."
    : "";

  const deviceRows = State.esp32s.length
    ? State.esp32s.map(d => `
      <div class="dev-row ${String(d.id) === String(State.activeEsp32Id) ? "active" : ""}">
        <div class="dev-status ${d.connected ? "ok" : "warn"}"><span class="conn-dot"></span></div>
        <div class="dev-main">
          <b>${escapeHtml(d.name || "ESP32")} ${String(d.id) === String(State.activeEsp32Id) ? `<span class="conn-chip">ACTIVE</span>` : ""}</b>
          <small>${escapeHtml(d.ip)}:${d.port || 80}${d.latency_ms != null ? ` \u00b7 ${d.latency_ms} ms` : ""}</small>
          ${d.error ? `<small class="warn">${escapeHtml(d.error)}</small>` : ""}
        </div>
        <div class="dev-actions">
          <button class="btn btn-sm ${String(d.id) === String(State.activeEsp32Id) && e.connected ? "btn-warn" : "btn-primary"}"
            onclick="activateEsp32Dev(${d.id})">
            ${String(d.id) === String(State.activeEsp32Id) && e.connected ? "Reconnect" : "Connect"}
          </button>
          <button class="btn btn-danger btn-sm" onclick="deleteEsp32Dev(${d.id})">Remove</button>
        </div>
      </div>`).join("")
    : `<div class="empty-state" style="padding:14px"><div class="empty-state-icon">📡</div><h3>No saved ESP32 yet</h3><p>Add one below, or connect by IP on the fly.</p></div>`;

  return `
    <div class="panel">
      <div class="panel-head"><h3>Your ESP32 Devices</h3><small>Multiple devices, saved per login \u2014 switch anytime</small></div>
      <div style="display:flex;flex-wrap:wrap;gap:12px;padding:10px 0">
        <label class="esp-label" style="flex:1;min-width:150px">NAME
          <input id="devNameInput" type="text" placeholder="Living Room" value="${escapeHtml(f.name)}"
            oninput="State.esp32Form.name=this.value" />
        </label>
        <label class="esp-label" style="flex:1;min-width:170px">ESP32 IP ADDRESS
          <input id="devIpInput" type="text" placeholder="192.168.1.7" value="${escapeHtml(f.ip)}"
            oninput="State.esp32Form.ip=this.value" spellcheck="false" />
        </label>
        <label class="esp-label">PORT
          <input id="devPortInput" type="number" min="1" max="65535" value="${f.port || 80}" style="width:90px"
            oninput="State.esp32Form.port=Number(this.value)||0" />
        </label>
        <button class="btn btn-primary" onclick="addEsp32Dev()">Add &amp; Connect</button>
        <button class="btn btn-secondary" onclick="connectEsp32From(this)">Connect by IP</button>
      </div>
      <p style="color:var(--text-muted);font-size:12px;margin-top:6px;line-height:1.6;">
        The ESP32 sketch runs its own web server on port 80. Enter the IP shown in the Arduino Serial Monitor
        (e.g. <code>192.168.1.7</code>). "Add &amp; Connect" saves the device to your login; the dashboard drives
        whichever device is active. ${issueMsg}
      </p>
      <div class="dev-list" style="margin-top:10px">${deviceRows}</div>
    </div>

    <div class="dash-grid-equal" style="margin-top:14px">
      <div class="panel">
        <div class="panel-head"><h3>Active Device \u2014 Live Reading</h3><small>Straight from the device</small></div>
        ${e.live ? `
          <div class="sens-grid" style="grid-template-columns:1fr 1fr">
            <div class="sens-card"><div class="sens-icon volt">🔌</div><div class="sens-info"><small>VOLTAGE</small><strong>${fmt(e.live.voltage, 1, " V")}</strong></div></div>
            <div class="sens-card"><div class="sens-icon curr">⚡</div><div class="sens-info"><small>CURRENT</small><strong>${fmt(e.live.current, 2, " A")}</strong></div></div>
            <div class="sens-card"><div class="sens-icon power">⚡</div><div class="sens-info"><small>POWER</small><strong>${fmt(e.live.power, 0, " W")}</strong></div></div>
            <div class="sens-card"><div class="sens-icon temp">🌡</div><div class="sens-info"><small>TEMPERATURE</small><strong>${fmt(e.live.temperature, 1, " \u00b0C")}</strong></div></div>
          </div>
        ` : `<div class="empty-state"><div class="empty-state-icon">📡</div><h3>No live reading yet</h3><p>Connect to one of your ESP32 devices above to stream live sensor data.</p></div>`}
      </div>
      <div class="panel">
        <div class="panel-head"><h3>Connection Status</h3><small>Direct vs backend</small></div>
        <div class="bar-item"><span>Source</span><b>${e.direct ? `ESP32 direct (${e.ip})` : "Backend / registered device"}</b></div>
        <div class="bar-item"><span>Status</span><b class="${e.connected ? "ok" : "warn"}">${e.connected ? "● Connected" : (e.ip ? "● Not responding" : "● Not configured")}</b></div>
        ${e.latencyMs != null ? `<div class="bar-item"><span>Latency</span><b>${e.latencyMs} ms</b></div>` : ""}
        ${e.error ? `<div class="bar-item"><span>Error</span><b class="warn">${escapeHtml(e.error)}</b></div>` : ""}
        ${e.backendWarn ? `<div class="bar-item"><span>Backend</span><b class="warn">${escapeHtml(e.backendWarn)} \u2014 restart it from this repo (or connect will still work in direct mode)</b></div>` : ""}
        <hr style="border:0;border-top:1px solid var(--border-light);margin:12px 0" />
        <p style="font-size:12px;color:var(--text-muted);line-height:1.6;">
          ${e.direct
            ? `Live readings & relay switches go directly to the ESP32. Analytics, history and reports continue to use the backend when a device is registered.`
            : `Everything goes through the FastAPI backend. Add an ESP32 IP above to connect hardware-to-dashboard instantly.`}
        </p>
      </div>
    </div>`;
}

function connectEsp32From(btn) {
  const ipInput = document.getElementById("devIpInput");
  const portInput = document.getElementById("devPortInput");
  const nameInput = document.getElementById("devNameInput");
  if (nameInput) State.esp32Form.name = nameInput.value;
  if (ipInput) State.esp32.draft = ipInput.value;
  if (portInput) State.esp32.port = Number(portInput.value) || 80;
  connectEsp32();
}

// ==================== VIEW: Overview / Home ====================
function homeView() {
  if (!hasLive()) return emptyState("🔌", "No device connected", "Enter your ESP32 IP in the ESP32 Connect tab (or register a backend device). Live data appears here automatically.");
  const r = State.latestReading || {};
  const s = State.summary || {};
  const isOn = online();
  const liveLabel = State.esp32.direct ? `\u25cf Live (${State.esp32.ip})` : (isOn ? "\u25cf Live" : "\u25cf Device offline");

  return `
    ${kpiGrid([
      kpiCard("LIVE POWER", fmt(r.power, 0, " W"), liveLabel, isOn ? "" : "warn"),
      kpiCard("TODAY'S ENERGY", fmt(s.today_energy_kwh, 2, " kWh"), "today"),
      kpiCard("TODAY'S COST", "\u20b9" + fmt(s.today_cost, 2), "today"),
      kpiCard("PROJECTED BILL", "\u20b9" + fmt(s.projected_month_cost, 0), "AI forecast"),
      kpiCard("VOLTAGE", fmt(r.voltage, 1, " V"), isOn ? "normal" : "stale", isOn ? "" : "muted"),
      kpiCard("FREQUENCY", fmt(r.frequency, 2, " Hz"), ""),
    ])}
    <div class="dash-grid">
      <div class="panel">
        <div class="panel-head"><h3>Live Power Trend</h3><small>Last ${Math.min(State.history.length, 40)} readings</small></div>
        <div class="energy-chart">${powerChart()}</div>
      </div>
      <div class="panel">
        <div class="panel-head"><h3>AI Insights</h3><small>Live analysis</small></div>
        ${generateInsights(3).map(x => insightCard(x.icon, x.title, x.body, x.type)).join("")}
      </div>
    </div>
    <div class="panel" style="margin-top:14px">
      <div class="panel-head"><h3>Wireless Appliance Control</h3><small>Turn relays ON/OFF over Wi-Fi</small></div>
      <div class="appliance-grid">${relayCard(1, false)}${relayCard(2, false)}</div>
    </div>
    <div class="dash-grid-equal" style="margin-top:14px">
      <div class="panel">
        <div class="panel-head"><h3>Recent Readings</h3><small>${State.esp32.direct && !State.history.length ? "Live samples from ESP32" : "Usage history"}</small></div>
        ${State.esp32.direct && !State.history.length
          ? `<div style="display:flex;flex-direction:column;gap:8px">${["1","2"].map(ch => insightCard(relayState(ch) === "ON" ? "⚡" : "◉", channelLabel(ch), `${relayState(ch) === "ON" ? "● ON" : "● OFF"} \u00b7 Relay channel ${ch}`, relayState(ch) === "ON" ? "" : "")).join("")}</div>`
          : recentTable(8)}
      </div>
      <div class="panel">
        <div class="panel-head"><h3>Device Info</h3><small>${State.esp32.direct ? "Direct connection" : "Connectivity"}</small></div>
        <div style="display:flex;flex-direction:column;gap:10px">
          ${insightCard("◉", State.esp32.direct ? `ESP32 @ ${State.esp32.ip}` : (State.activeDeviceId || "No backend device"), `${isOn ? "\u25cf Online" : "\u25cf Offline"} \u00b7 ${State.esp32.direct ? "port " + State.esp32.port : (r.device_ip || device().last_ip || "IP not reported")}`, isOn ? "" : "danger")}
          ${insightCard("🌡️", "Environment", `${fmt(r.temperature, 1, "\u00b0C")} temperature \u00b7 ${fmt(r.humidity, 1, "%")} humidity`)}
        </div>
      </div>
    </div>`;
}

// ==================== VIEW: Live Monitor ====================
function liveMonitorView() {
  if (!hasLive()) return emptyState("🔌", "No device connected", "Connect the ESP32 via its IP (ESP32 Connect tab) or register a backend device to see live sensor data.");
  const r = State.latestReading || {};
  const isOn = online();
  const lastSeen = State.esp32.direct
    ? (State.esp32.lastLiveAt ? new Date(State.esp32.lastLiveAt).toLocaleTimeString() + " (live)" : "never")
    : (State.lastSeenAt ? new Date(State.lastSeenAt).toLocaleString() : "never");
  const devIdLabel = State.esp32.direct ? `ESP32 @ ${State.esp32.ip}:${State.esp32.port}` : (State.activeDeviceId || "No backend device");

  const sensors = [
    { icon: "🔌", name: "VOLTAGE", val: fmt(r.voltage, 1, " V"), cls: "volt" },
    { icon: "⚡", name: "CURRENT", val: fmt(r.current, 2, " A"), cls: "curr" },
    { icon: "⚡", name: "POWER", val: fmt(r.power, 0, " W"), cls: "power" },
    { icon: "∑", name: "ENERGY", val: fmt(r.energy_kwh, 2, " kWh"), cls: "energy" },
    { icon: "~", name: "FREQUENCY", val: fmt(r.frequency, 2, " Hz"), cls: "freq" },
    { icon: "🌡", name: "TEMPERATURE", val: fmt(r.temperature, 1, " \u00b0C"), cls: "temp" },
    { icon: "💧", name: "HUMIDITY", val: fmt(r.humidity, 1, " %"), cls: "humid" },
    { icon: "⚡", name: "POWER FACTOR", val: r.power_factor ? fmt(r.power_factor, 2) : "\u2014", cls: "pf" },
  ];

  return `
    <div class="panel">
      <div class="panel-head"><h3>Device</h3><small>${State.esp32.direct ? "Direct hardware connection" : "Connectivity info"}</small></div>
      ${kpiGrid([
        kpiCard("DEVICE", devIdLabel, State.esp32.direct ? "standalone web server" : (device().firmware_version || "fw unknown"), "muted"),
        kpiCard("IP ADDRESS", r.device_ip || State.esp32.ip || device().last_ip || "not reported", State.esp32.direct ? "port " + State.esp32.port : "sent each telemetry", "muted"),
        kpiCard("STATUS", `<span style="color:${isOn ? "var(--accent)" : "var(--red)"}">${isOn ? "\u25cf ONLINE" : "\u25cf OFFLINE"}</span>`, isOn ? "" : "check connection", isOn ? "" : "warn"),
        kpiCard("LAST SEEN", lastSeen, "local time", "muted"),
      ])}
    </div>
    <div class="panel" style="margin-top:14px">
      <div class="panel-head"><h3>Live Sensor Parameters</h3><small>Refreshes every ~3s</small></div>
      <div class="sens-grid">
        ${sensors.map(s => `<div class="sens-card">
          <div class="sens-icon ${s.cls}">${s.icon}</div>
          <div class="sens-info"><small>${s.name}</small><strong>${s.val}</strong></div>
        </div>`).join("")}
      </div>
    </div>
    <div class="panel" style="margin-top:14px">
      <div class="panel-head"><h3>Wireless Relay Control</h3><small>${State.esp32.direct ? `ESP32 GPIO 25 / 26 \u00b7 direct` : "ESP32 GPIO 26 / 27 \u00b7 via backend"}</small></div>
      <div class="appliance-grid">${relayCard(1, true)}${relayCard(2, true)}</div>
    </div>
    <div class="panel" style="margin-top:14px">
      <div class="panel-head"><h3>Usage History</h3><small>${State.esp32.direct && !State.history.length ? "Streaming live from ESP32" : `Latest ${State.history.length} samples`}</small></div>
      ${State.esp32.direct && !State.history.length
        ? `<p style="color:var(--text-muted);font-size:13px;">Live readings are streaming from the ESP32. Connect a registered backend device for historical charting.</p>`
        : recentTable(12)}
    </div>`;
}

// ==================== VIEW: My Appliances ====================
function myAppliancesView() {
  if (!hasLive()) return emptyState("🛠", "No appliances online", "Connect the ESP32 (ESP32 Connect tab) or register a backend device to manage appliances.");
  const direct = State.esp32.direct;
  return `
    <div class="panel">
      <div class="panel-head">
        <h3>My Appliances &mdash; Wireless ON/OFF</h3>
        <small>${online() ? (direct ? "\u25cf Direct \u2014 switches apply instantly" : "\u25cf Online \u2014 switches take effect within ~3s") : "\u25cf Offline \u2014 commands queue for reconnect"}</small>
      </div>
      <div class="appliance-grid">${relayCard(1, true)}${relayCard(2, true)}</div>
      <p style="color:var(--text-muted);font-size:12px;margin-top:14px;line-height:1.6;">
        ${direct
          ? "Direct mode: the switch hits the ESP32's own endpoints (<code>/relay1/on</code>, <code>/relay1/off</code>, <code>/relay2/on</code>, <code>/relay2/off</code>) so the relay toggles immediately over Wi-Fi."
          : "Backend mode: flip a switch \u2192 the backend queues a relay command \u2192 the ESP32 polls, toggles the relay, and reports the new state back."}
      </p>
    </div>
    ${!direct && State.activeDeviceId ? `
    <div class="panel" style="margin-top:14px">
      <div class="panel-head"><h3>Name Your Appliances</h3><small>Saves to the backend</small></div>
      <div class="channel-labels">
        <label>Channel 1<input id="label1" value="${channelLabel(1)}" placeholder="e.g. Air Conditioner" /></label>
        <label>Channel 2<input id="label2" value="${channelLabel(2)}" placeholder="e.g. Refrigerator" /></label>
        <button class="btn btn-primary btn-sm" onclick="saveChannelLabels()">Save Names</button>
      </div>
    </div>` : ""}`;
}

function saveChannelLabels() {
  const labels = {
    "1": document.getElementById("label1").value.trim() || "Appliance 1",
    "2": document.getElementById("label2").value.trim() || "Appliance 2",
  };
  Api.renameChannels(State.activeDeviceId, labels).then(() => {
    toast("Channel names saved");
    refreshData();
  }).catch(err => toast(err.message || "Failed to save names"));
}

// ==================== VIEW: Energy Usage ====================
function energyUsageView() {
  if (!State.activeDeviceId) return emptyState("⚡", "Analytics need a registered device", "Enter the ESP32 IP for live monitoring and relay control; register the device in the backend for usage analytics.");
  const s = State.summary || {};
  const daily = s.daily || [];
  const total7 = daily.reduce((a, d) => a + d.energy_kwh, 0);
  const avg = daily.length ? total7 / daily.length : 0;
  const on1 = relayState(1) === "ON", on2 = relayState(2) === "ON";
  const totalLoad = (on1 ? applianceLoad(1) : 0) + (on2 ? applianceLoad(2) : 0) || 1;

  return `
    ${kpiGrid([
      kpiCard("TODAY'S ENERGY", fmt(s.today_energy_kwh, 2, " kWh"), "today"),
      kpiCard("COST TODAY", "\u20b9" + fmt(s.today_cost, 2), "today"),
      kpiCard("PERIOD AVERAGE", fmt(avg, 2, " kWh/day"), `${daily.length} day(s)`),
      kpiCard("PERIOD TOTAL", fmt(total7, 2, " kWh"), "recorded days"),
    ])}
    <div class="dash-grid-equal" style="margin-top:14px">
      <div class="panel">
        <div class="panel-head"><h3>Appliance-wise Usage</h3><small>Estimated live split by relay state</small></div>
        <div class="bar-list">
          <div class="bar-row">
            <span class="bar-label">${channelLabel(1)}</span>
            <div class="bar-track"><span class="bar-fill" style="width:${on1 ? (applianceLoad(1) / totalLoad * 100).toFixed(1) : 0}%"></span></div>
            <span class="bar-value">${on1 ? `${fmt(applianceLoad(1), 0, " W")} \u00b7 ON` : "OFF"}</span>
          </div>
          <div class="bar-row">
            <span class="bar-label">${channelLabel(2)}</span>
            <div class="bar-track"><span class="bar-fill" style="width:${on2 ? (applianceLoad(2) / totalLoad * 100).toFixed(1) : 0}%"></span></div>
            <span class="bar-value">${on2 ? `${fmt(applianceLoad(2), 0, " W")} \u00b7 ON` : "OFF"}</span>
          </div>
        </div>
        <p style="color:var(--text-muted);font-size:11px;margin-top:12px;">Single power meter + relay states. Wire a PZEM per outlet for exact per-appliance readings.</p>
      </div>
      <div class="panel">
        <div class="panel-head"><h3>Daily Energy</h3><small>Recorded days</small></div>
        ${dailyBars()}
      </div>
    </div>
    <div class="panel" style="margin-top:14px">
      <div class="panel-head"><h3>Usage History</h3><small>Latest readings</small></div>
      ${recentTable(14)}
    </div>`;
}

// ==================== VIEW: Bill Prediction ====================
function billPredictionView() {
  if (!State.activeDeviceId) return emptyState("₹", "Bill prediction needs a registered device", "Let the ESP32 post telemetry to the backend for a few days to build bill predictions.");
  const s = State.summary || {};
  const daily = s.daily || [];
  const total = daily.reduce((a, d) => a + d.energy_kwh, 0);
  const avg = daily.length ? total / daily.length : 0;

  return `
    ${kpiGrid([
      kpiCard("PROJECTED BILL", "\u20b9" + fmt(s.projected_month_cost, 0), "AI forecast"),
      kpiCard("TODAY'S COST", "\u20b9" + fmt(s.today_cost, 2), "today"),
      kpiCard("AVERAGE / DAY", "\u20b9" + fmt(avg * rate(), 2), `${daily.length} day(s)`),
      kpiCard("TARIFF", "\u20b9" + fmt(rate(), 2, "/kWh"), "flat rate"),
    ])}
    <div class="dash-grid-equal" style="margin-top:14px">
      <div class="panel">
        <div class="panel-head"><h3>AI Bill Prediction</h3><small>Confidence: ${daily.length} day(s) of data</small></div>
        <div class="pred-number">\u20b9${fmt(s.projected_month_cost, 0)}</div>
        <p style="font-size:13px;color:var(--text-secondary);margin:4px 0 0;">Predicted month-end bill at current usage</p>
        <div class="progress-bar">
          <span class="progress-fill" style="width:${Math.min(100, (s.projected_month_cost || 0) / 2000 * 100)}%"></span>
        </div>
        <p style="font-size:12px;color:var(--text-muted);">\u20b9${fmt(s.today_cost, 2)} spent today so far</p>
      </div>
      <div class="panel">
        <div class="panel-head"><h3>How It Works</h3><small>Transparent MVP model</small></div>
        ${insightCard("✦", "Daily energy \u2192 projection", `Average daily kWh across recorded days is multiplied to month end at \u20b9${fmt(rate(), 2)}/kWh.`)}
        ${insightCard("💡", "Save more", "Use the Appliance switches: every hour an appliance is OFF saves its load \u00d7 tariff.")}
      </div>
    </div>
    <div class="panel" style="margin-top:14px">
      <div class="panel-head"><h3>Daily Energy & Cost</h3><small>Last ${daily.length} day(s)</small></div>
      ${dailyBars()}
    </div>`;
}

// ==================== VIEW: Monthly Reports ====================
function monthlyReportsView() {
  if (!State.activeDeviceId) return emptyState("📊", "Reports need a registered device", "Post telemetry to the backend for a few days to generate monthly reports.");
  const s = State.summary || {};
  const daily = s.daily || [];
  if (!daily.length) {
    return `<div class="panel">
      <div class="panel-head"><h3>Monthly Reports</h3></div>
      <p style="color:var(--text-muted);font-size:13px;">Not enough data yet. Keep telemetry flowing for a full day to build reports.</p>
    </div>`;
  }
  const total = daily.reduce((a, d) => a + d.energy_kwh, 0);
  const cost = daily.reduce((a, d) => a + d.estimated_cost, 0);
  const maxDaily = Math.max(...daily.map(d => d.energy_kwh)) || 1;
  const rows = daily.slice().reverse().map(d => `
    <tr>
      <td>${d.date}</td>
      <td>${d.energy_kwh.toFixed(3)} kWh</td>
      <td>\u20b9${d.estimated_cost.toFixed(2)}</td>
    </tr>`).join("");

  return `
    ${kpiGrid([
      kpiCard("REPORT ENERGY", fmt(total, 2, " kWh"), `${daily.length} day(s)`),
      kpiCard("REPORT COST", "\u20b9" + fmt(cost, 2), "estimated"),
      kpiCard("TODAY'S ENERGY", fmt(s.today_energy_kwh, 2, " kWh"), "today"),
      kpiCard("PROJECTED BILL", "\u20b9" + fmt(s.projected_month_cost, 0), "AI forecast"),
    ])}
    <div class="panel" style="margin-top:14px">
      <div class="panel-head"><h3>Daily Breakdown</h3><small>Energy & estimated cost</small></div>
      <table class="area-table">
        <thead><tr><th>Date</th><th>Energy</th><th>Estimated Cost</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="panel" style="margin-top:14px">
      <div class="panel-head"><h3>Monthly Trend</h3><small>Daily consumption</small></div>
      ${dailyBars()}
    </div>`;
}

// ==================== VIEW: AI Insights ====================
function aiInsightsView() {
  if (!hasLive()) return emptyState("✦", "No live data", "Connect the ESP32 by IP or register a backend device to generate AI insights.");
  const items = generateInsights(6);
  return `
    <div class="panel">
      <div class="panel-head"><h3>AI Insights & Saving Recommendations</h3><small>Generated live from your data</small></div>
      <div style="display:flex;flex-direction:column;gap:0">
        ${items.map(x => insightCard(x.icon, x.title, x.body, x.type)).join("")}
      </div>
    </div>
    <div class="panel" style="margin-top:14px">
      <div class="panel-head"><h3>Control the Source of Waste</h3><small>Suggested switches</small></div>
      <p style="color:var(--text-muted);font-size:12px;margin-bottom:14px;">
        Every insight above that points at an appliance is actionable right here \u2014 flip it OFF wirelessly.
      </p>
      <div class="appliance-grid">${relayCard(1, true)}${relayCard(2, true)}</div>
    </div>`;
}

// ==================== Keyboard Shortcuts ====================
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeModal();
    document.getElementById("sidebar")?.classList.remove("open");
  }
  if (e.key === "Enter" && !document.getElementById("loginModal").classList.contains("hidden")) {
    enterDashboard();
  }
});

// ==================== Init ====================
(function init() {
  if (Api.token()) {
    document.getElementById("landing").classList.add("hidden");
    document.querySelector(".topbar").classList.add("hidden");
    document.getElementById("dashboard").classList.remove("hidden");
    startDashboard();
  }
})();
