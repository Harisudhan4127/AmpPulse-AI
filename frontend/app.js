/* =========================================================================
 * AmpPulse AI — frontend
 * Single-purpose home energy dashboard driven by the local FastAPI backend.
 * Features per abstract.txt: live status, appliance-wise usage, wireless
 * relay ON/OFF, estimated EB bill, usage history, monthly reports and
 * AI-powered saving insights.
 * ======================================================================= */

const API_BASE = window.AMPPULSE_API_BASE
  || `http://${window.location.hostname || "localhost"}:8000`;
const AUTH_TOKEN_KEY = "amppulse_token";

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
    try { data = await resp.json(); } catch (e) { /* empty/non-JSON body */ }

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
  /* Wireless relay control — queued on the backend, applied by the ESP32
   * on its next 3-second command poll, confirmed in the next telemetry. */
  async sendCommand(deviceId, channel, value) {
    return this._request(`/api/v1/devices/${deviceId}/commands`, {
      method: "POST", body: JSON.stringify({ action: "relay_set", channel, value })
    });
  },
  async renameChannels(deviceId, labels) {
    return this._request(`/api/v1/devices/${deviceId}/channels`, {
      method: "PATCH", body: JSON.stringify({ channel_labels: labels })
    });
  },
};

const AppState = {
  devices: [],
  activeDeviceId: null,
  latestReading: null,
  history: [],
  summary: null,
  deviceStatus: "offline",
  lastSeenAt: null,
  currentTab: "Overview",
  pollTimer: null,
};

/* Optimistic relay state: channel -> "ON"/"OFF". Set when the user toggles a
 * switch, cleared 4s later once the ESP32 confirms the new state in
 * telemetry. Lets the UI feel instant while the wireless command travels. */
const pendingRelay = {};

const NAV = [
  "Overview", "Live Monitor", "My Appliances",
  "Energy Usage", "Bill Prediction", "Monthly Reports", "AI Insights",
];

const DASH_TITLE = "Home Energy Dashboard";

function toast(t) {
  const x = document.getElementById("toast");
  x.textContent = t; x.classList.add("show");
  setTimeout(() => x.classList.remove("show"), 2600);
}

function scrollToId(id) { document.getElementById(id)?.scrollIntoView({ behavior: "smooth" }); }
function toggleSidebar() { document.querySelector(".sidebar").classList.toggle("open"); }
function toggleMenu() {
  document.querySelector(".nav-links").style.display =
    document.querySelector(".nav-links").style.display === "flex" ? "none" : "flex";
}

function showLanding() {
  if (AppState.pollTimer) { clearInterval(AppState.pollTimer); AppState.pollTimer = null; }
  document.getElementById("dashboard").classList.add("hidden");
  document.getElementById("landing").classList.remove("hidden");
  document.querySelector(".topbar").classList.remove("hidden");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function openLogin() { document.getElementById("loginModal").classList.remove("hidden"); }
function closeModal() { document.getElementById("loginModal").classList.add("hidden"); }

function enterDashboard() {
  const email = document.getElementById("loginId").value.trim();
  const password = document.getElementById("loginPassword").value;
  const errEl = document.getElementById("loginError");
  errEl.style.display = "none";

  Api.login(email, password).then(user => {
    closeModal();
    document.getElementById("landing").classList.add("hidden");
    document.querySelector(".topbar").classList.add("hidden");
    document.getElementById("dashboard").classList.remove("hidden");
    window.scrollTo(0, 0);
    startHomeSession();
  }).catch(err => {
    errEl.textContent = err.message || "Login failed. Check your email/password.";
    errEl.style.display = "block";
  });
}

async function startHomeSession() {
  renderDashboard();
  try {
    AppState.devices = await Api.listDevices();
    AppState.activeDeviceId = AppState.devices[0]?.device_id || null;
  } catch (e) {
    toast(e.message || "Could not load devices");
  }
  await refreshHomeData();
  if (AppState.pollTimer) clearInterval(AppState.pollTimer);
  AppState.pollTimer = setInterval(refreshHomeData, 5000);
}

async function refreshHomeData() {
  const dot = document.getElementById("backendLiveDot");
  try {
    const healthy = await Api.health();
    if (dot) { dot.textContent = healthy ? "● LIVE" : "● OFFLINE"; dot.style.color = healthy ? "" : "#ff6b6b"; }

    if (AppState.activeDeviceId) {
      const [status, history, summary] = await Promise.all([
        Api.deviceStatus(AppState.activeDeviceId),
        Api.deviceHistory(AppState.activeDeviceId, 200),
        Api.deviceSummary(AppState.activeDeviceId, 7),
      ]);
      AppState.latestReading = status.latest_reading;
      AppState.history = history;
      AppState.summary = summary;
      AppState.deviceStatus = status.status;
      AppState.lastSeenAt = status.last_seen_at;
    }
  } catch (e) {
    if (dot) { dot.textContent = "● OFFLINE"; dot.style.color = "#ff6b6b"; }
  }
  if (!document.getElementById("dashboard").classList.contains("hidden")) {
    document.getElementById("dashContent").innerHTML = renderActiveTab();
  }
}

function renderActiveTab() {
  switch (AppState.currentTab) {
    case "Live Monitor": return liveMonitorView();
    case "My Appliances": return myAppliancesView();
    case "Energy Usage": return energyUsageView();
    case "Bill Prediction": return billPredictionView();
    case "Monthly Reports": return monthlyReportsView();
    case "AI Insights": return aiInsightsView();
    default: return homeView();
  }
}

function renderDashboard() {
  document.getElementById("sideRole").textContent = "HOME";
  document.getElementById("dashRole").textContent = "Home";
  document.getElementById("dashTitle").textContent = DASH_TITLE;
  document.getElementById("sideNav").innerHTML =
    NAV.map((n, i) => `<button class="${i === 0 ? "active" : ""}" onclick="selectDash('${n}',this)">${icon(n)} ${n}</button>`).join("");
  AppState.currentTab = "Overview";
  document.getElementById("dashContent").innerHTML = homeView();
}

function icon(n) {
  return ({ "Overview": "⌂", "Live Monitor": "◉", "My Appliances": "▦",
            "Energy Usage": "≈", "Bill Prediction": "₹", "Monthly Reports": "▤",
            "AI Insights": "✦" })[n] || "•";
}

function selectDash(n, el) {
  document.querySelectorAll(".sidebar nav button").forEach(x => x.classList.remove("active"));
  el.classList.add("active");
  AppState.currentTab = n;
  document.getElementById("dashContent").innerHTML = renderActiveTab();
}

/* ---------------- data helpers ---------------- */

function fmt(v, d = 2, u = "") {
  return (v === undefined || v === null || isNaN(v)) ? "—" : `${Number(v).toFixed(d)}${u}`;
}

function device() {
  return AppState.devices.find(d => d.device_id === AppState.activeDeviceId) || {};
}

function online() { return AppState.deviceStatus === "online"; }

function relayState(ch) {
  if (pendingRelay[ch]) return pendingRelay[ch];
  return AppState.latestReading?.channel_status?.[String(ch)] || "OFF";
}

function channelLabel(ch) { return device().channel_labels?.[String(ch)] || `Appliance ${ch}`; }

/* Estimated live power per ON appliance: the single power meter reports the
 * total, and the relays tell us which appliances are ON, so we split the load
 * evenly among them. Honest MVP approximation - a per-channel meter (PZEM
 * per outlet) would give exact numbers. */
function applianceLoad(ch) {
  const r = AppState.latestReading || {};
  const total = r.power || 0;
  const onChannels = ["1", "2"].filter(c => relayState(c) === "ON").length;
  if (relayState(ch) !== "ON" || onChannels === 0) return 0;
  return total / onChannels;
}

function todayEnergy() { return AppState.summary?.today_energy_kwh ?? null; }
function todayCost() { return AppState.summary?.today_cost ?? null; }
function projectedCost() { return AppState.summary?.projected_month_cost ?? null; }
function rate() { return (AppState.summary?.today_cost && AppState.summary?.today_energy_kwh) ? (AppState.summary.today_cost / AppState.summary.today_energy_kwh) : 8; }

function kpiGrid(arr) {
  return `<div class="kpi-grid">${arr.map(x => `<div class="kpi"><small>${x[0]}</small><strong>${x[1]}</strong><em>${x[2] || ""}</em></div>`).join("")}</div>`;
}

function kpi(a, b, c) { return [a, b, c]; }

function recentTable(limit = 10) {
  if (!AppState.history.length) return `<p style="color:#87939e">No readings received yet.</p>`;
  const rows = AppState.history.slice(-limit).reverse().map(h => {
    const t = new Date(h.created_at).toLocaleString();
    const ch = h.channel_status || {};
    return `<tr>
      <td>${t}</td>
      <td>${fmt(h.voltage, 1, " V")}</td>
      <td>${fmt(h.current, 2, " A")}</td>
      <td>${fmt(h.power, 0, " W")}</td>
      <td>1:${ch["1"] || "OFF"} · 2:${ch["2"] || "OFF"}</td>
    </tr>`;
  }).join("");
  return `<table class="area-table"><thead><tr><th>Time</th><th>Voltage</th><th>Current</th><th>Power</th><th>Relays</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function powerChart() {
  const points = AppState.history.slice(-30);
  if (points.length < 2) {
    return `<svg viewBox="0 0 900 220" preserveAspectRatio="none"><path d="M0 180 C80 160 95 120 150 145 S240 190 285 130 S360 65 420 110 S510 160 555 90 S650 45 700 100 S780 140 900 35 V220 H0Z" fill="rgba(55,211,138,.18)"/><path d="M0 180 C80 160 95 120 150 145 S240 190 285 130 S360 65 420 110 S510 160 555 90 S650 45 700 100 S780 140 900 35" fill="none" stroke="#37b879" stroke-width="4"/></svg>`;
  }
  const vals = points.map(p => p.power || 0);
  const min = Math.min(...vals), max = Math.max(...vals);
  const range = (max - min) || 1;
  const w = 900, h = 220;
  const step = w / (points.length - 1);
  const path = vals.map((v, i) => `${i === 0 ? "M" : "L"}${(i * step).toFixed(1)} ${(h - ((v - min) / range) * h * 0.8 - 20).toFixed(1)}`).join(" ");
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><path d="${path} V${h} H0 Z" fill="rgba(55,211,138,.18)"/><path d="${path}" fill="none" stroke="#37b879" stroke-width="4"/></svg>`;
}

function dailyBars() {
  const daily = AppState.summary?.daily || [];
  if (!daily.length) return `<p style="color:#87939e">Not enough energy data yet. Keep telemetry flowing all day to build this.</p>`;
  const vals = daily.map(d => d.energy_kwh);
  const max = Math.max(...vals) || 1;
  return `<div class="bar-list">${daily.slice(-14).map(d =>
    `<div class="bar-row"><span>${d.date}</span><div class="bar"><span style="width:${(d.energy_kwh / max * 100).toFixed(1)}%"></span></div><b>${fmt(d.energy_kwh, 2, " kWh")} · ₹${fmt(d.estimated_cost, 2)}</b></div>`
  ).join("")}</div>`;
}

function barRow(label, pct, right) {
  return `<div class="bar-row"><span>${label}</span><div class="bar"><span style="width:${pct}%"></span></div><b>${right}</b></div>`;
}

function insightsFromData(maxItems = 4) {
  const r = AppState.latestReading || {};
  const out = [];

  if (!online()) {
    out.push({ ic: "⚠", title: "Device appears offline", body: "No telemetry received recently. Check ESP32 power and Wi-Fi, then try toggling an appliance — commands queue up and apply the moment it reconnects." });
    return out;
  }

  const power = r.power || 0;
  const avg = AppState.history.length
    ? AppState.history.reduce((s, h) => s + (h.power || 0), 0) / AppState.history.length : 0;
  if (avg && power > avg * 1.4) {
    out.push({ ic: "⚡", title: "Higher-than-normal load", body: `Live power ${fmt(power, 0, " W")} is ${Math.round((power / avg - 1) * 100)}% above your recent average (${fmt(avg, 0, " W")}).` });
  }
  if ((r.temperature || 0) > 35) {
    out.push({ ic: "🌡", title: "Warm near the meter", body: `${fmt(r.temperature, 1, "°C")} found — heat increases appliance loss and affects accuracy.` });
  }

  /* Concrete saving plan from the relays: how much each ON appliance costs
   * per month if run unchanged, and the saving for a 6h/day OFF window. */
  for (const ch of ["1", "2"]) {
    if (relayState(ch) === "ON") {
      const w = applianceLoad(ch);
      const monthly = w / 1000 * 24 * 30 * rate();
      const save6 = w / 1000 * 6 * 30 * rate();
      out.push({
        ic: "💡",
        title: `Saving opportunity · ${channelLabel(ch)}`,
        body: `At ~${fmt(w, 0, " W")} it costs ~₹${fmt(monthly, 0)}/month if always on. Turning it OFF 6h/day saves ~₹${fmt(save6, 0)}.`,
      });
    }
  }

  const daily = AppState.summary?.daily || [];
  if (daily.length > 1) {
    const last = daily[daily.length - 1], prev = daily[daily.length - 2];
    if (last && prev) {
      const diff = last.energy_kwh - prev.energy_kwh;
      if (Math.abs(diff) > 0.01) {
        out.push({
          ic: diff < 0 ? "✓" : "↗",
          title: diff < 0 ? "Usage is improving" : "Usage rose vs yesterday",
          body: `${fmt(Math.abs(diff), 2)} kWh ${diff < 0 ? "less" : "more"} than the previous recorded day (₹${fmt(Math.abs(last.estimated_cost - prev.estimated_cost), 2)}).`,
        });
      }
    }
  }

  if (!out.length) out.push({ ic: "✓", title: "All readings normal", body: "No unusual pattern detected. You're all set." });
  return out.slice(0, maxItems);
}

function dummyDeviceBlock() {
  return `<div class="panel" style="margin-top:15px"><div class="panel-head"><h3>No device connected</h3></div>
    <p style="color:#87939e">Flash the ESP32 firmware and register it (see <code>setup_esp32.py</code>). Live data and wireless control appear here automatically.</p></div>`;
}

/* ---------------- wireless relay control ---------------- */

/* Wireless ON/OFF: POST a relay_set command to the backend. The ESP32 picks
 * it up on its next ~3s command poll, flips GPIO 26/27, and the next
 * telemetry reading confirms the new state back here. */
function controlChannel(ch, value) {
  if (!online()) {
    toast("Device is offline — command queued, it will apply when the ESP32 reconnects.");
  }
  pendingRelay[String(ch)] = value;             // optimistic flip now
  document.getElementById("dashContent").innerHTML = renderActiveTab();
  Api.sendCommand(AppState.activeDeviceId, ch, value).then(() => {
    toast(`Wireless command sent: ${channelLabel(ch)} → ${value}`);
    setTimeout(() => {
      delete pendingRelay[String(ch)];
      refreshHomeData();
    }, 4000);
  }).catch(err => {
    delete pendingRelay[String(ch)];
    document.getElementById("dashContent").innerHTML = renderActiveTab();
    toast(err.message || "Failed to send command");
  });
}

function relayCard(ch, big) {
  const label = channelLabel(ch);
  const state = relayState(ch);
  const on = state === "ON";
  const w = big ? applianceLoad(ch) : 0;
  const switchHtml = `
    <label class="switch ${on ? "on" : "off"}" title="${on ? "Turn OFF" : "Turn ON"} wirelessly">
      <input type="checkbox" ${on ? "checked" : ""} onchange="controlChannel(${ch}, this.checked ? 'ON' : 'OFF')" />
      <span class="slider"></span>
    </label>`;
  return `
    <div class="relay-card ${on ? "is-on" : "is-off"}">
      <div class="relay-head">
        <span class="app-icon">${ch === 1 ? "❄" : "⌁"}</span>
        <div class="relay-name"><b>${label}</b><small>Relay channel ${ch} · ESP32 GPIO ${ch === 1 ? 26 : 27}</small></div>
        <span class="status ${on ? "on" : "off"}">● ${on ? "ON" : "OFF"}</span>
      </div>
      ${big ? `<div class="relay-meta">
        <div><small>Estimated load</small><b>${fmt(w, 0, " W")}</b></div>
        <div><small>Wireless</small><b class="ok">Wi-Fi ready</b></div>
      </div>` : ""}
      <div class="relay-footer"><span>${on ? "Energy in use — tap to switch OFF" : "Sleeping — tap to switch ON"}</span>${switchHtml}</div>
    </div>`;
}

/* ---------------- views ---------------- */

function homeView() {
  if (!AppState.activeDeviceId) return dummyDeviceBlock();
  const r = AppState.latestReading || {};
  const s = AppState.summary || {};
  const on = online();

  return `
    ${kpiGrid([
      kpi("LIVE POWER", fmt(r.power, 0, " W"), on ? "● Live" : "● Device offline"),
      kpi("TODAY'S ENERGY", fmt(s.today_energy_kwh, 2, " kWh"), "today"),
      kpi("TODAY'S COST", "₹" + fmt(s.today_cost, 2), "today"),
      kpi("PROJECTED MONTH BILL", "₹" + fmt(s.projected_month_cost, 0), "AI forecast"),
      kpi("LIVE VOLTAGE", fmt(r.voltage, 1, " V"), on ? "normal" : "stale"),
      kpi("FREQUENCY", fmt(r.frequency, 2, " Hz"), ""),
    ])}
    <div class="dash-grid">
      <div class="panel"><div class="panel-head"><h3>Live Power</h3><small>Last ${Math.min(AppState.history.length, 30)} readings · W</small></div>
        <div class="energy-chart">${powerChart()}</div></div>
      <div class="panel"><div class="panel-head"><h3>AI Insights</h3><small>Live analysis</small></div>
        <div>${insightsFromData(3).map(x => `<div class="insight"><span>${x.ic}</span><div><strong>${x.title}</strong><p>${x.body}</p></div></div>`).join("")}</div></div>
    </div>
    <div class="panel" style="margin-top:15px"><div class="panel-head"><h3>Wireless Appliance Control</h3><small>Turn relays ON/OFF over Wi-Fi</small></div>
      <div class="appliance-grid">${relayCard(1, false)}${relayCard(2, false)}</div></div>
    <div class="dash-grid" style="margin-top:15px">
      <div class="panel"><div class="panel-head"><h3>Recent Readings</h3><small>Usage history</small></div>${recentTable(8)}</div>
      <div class="panel"><div class="panel-head"><h3>Device</h3><small>Connectivity</small></div>
        <div class="insight"><span>◉</span><div><strong>${AppState.activeDeviceId}</strong><p>${on ? "● Online" : "● Offline"} · ${r.device_ip || device().last_ip || "IP not reported yet"}</p></div></div>
        <div class="insight"><span>🌡</span><div><strong>Environment</strong><p>${fmt(r.temperature, 1, "°C")} · humidity ${fmt(r.humidity, 1, "%")}</p></div></div>
      </div>
    </div>`;
}

function liveMonitorView() {
  if (!AppState.activeDeviceId) return dummyDeviceBlock();
  const r = AppState.latestReading || {};
  const on = online();
  const lastSeen = AppState.lastSeenAt ? new Date(AppState.lastSeenAt).toLocaleString() : "never";

  const sensors = [
    { ic: "🔌", name: "VOLTAGE", val: fmt(r.voltage, 1, " V") },
    { ic: "⚡", name: "CURRENT", val: fmt(r.current, 2, " A") },
    { ic: "🛢", name: "POWER", val: fmt(r.power, 0, " W") },
    { ic: "∑", name: "ENERGY", val: fmt(r.energy_kwh, 2, " kWh") },
    { ic: "~", name: "FREQUENCY", val: fmt(r.frequency, 2, " Hz") },
    { ic: "🌡", name: "TEMPERATURE", val: fmt(r.temperature, 1, " °C") },
    { ic: "💧", name: "HUMIDITY", val: fmt(r.humidity, 1, " %") },
    { ic: "☁", name: "POWER FACTOR", val: r.power_factor ? fmt(r.power_factor, 2) : "—" },
  ];

  return `
    <div class="panel"><div class="panel-head"><h3>Device</h3><small>Connectivity info</small></div>
      ${kpiGrid([
        kpi("DEVICE ID", AppState.activeDeviceId, device().firmware_version || "fw unknown"),
        kpi("ESP32 IP ADDRESS", r.device_ip || device().last_ip || "not reported", "sent with each telemetry"),
        kpi("STATUS", `<span class="${on ? "ok" : "warn"}">● ${on ? "ONLINE" : "OFFLINE"}</span>`, on ? "" : "no telemetry within 90s"),
        kpi("LAST SEEN", lastSeen, "local time"),
      ])}
    </div>
    <div class="panel" style="margin-top:15px"><div class="panel-head"><h3>Live Sensor Parameters</h3><small>Refreshes every ~5s</small></div>
      <div class="sens-grid">${sensors.map(s => `<div class="sens-card"><div class="sens-icon">${s.ic}</div><small>${s.name}</small><strong>${s.val}</strong></div>`).join("")}</div>
    </div>
    <div class="panel" style="margin-top:15px"><div class="panel-head"><h3>Wireless Relay Control</h3><small>ESP32 GPIO 26 / 27</small></div>
      <div class="appliance-grid">${relayCard(1, true)}${relayCard(2, true)}</div>
    </div>
    <div class="panel" style="margin-top:15px"><div class="panel-head"><h3>Usage History</h3><small>Latest ${AppState.history.length} samples</small></div>${recentTable(12)}</div>`;
}

function myAppliancesView() {
  if (!AppState.activeDeviceId) return dummyDeviceBlock();
  return `
    <div class="panel ${online() ? "" : "warn-outline"}"><div class="panel-head"><h3>My Appliances — Wireless ON/OFF</h3>
      <small>${online() ? "● Device online — switches take effect within ~3 seconds" : "● Device offline — commands queue and apply on reconnect"}</small></div>
      <div class="appliance-grid">${relayCard(1, true)}${relayCard(2, true)}</div>
      <p style="color:#87939e;margin-top:12px">Flip a switch → the backend queues a relay command → your laptop sends it over Wi-Fi → the ESP32 toggles the relay and reports the new state back. No direct wiring to the laptop needed.</p>
    </div>
    <div class="panel" style="margin-top:15px"><div class="panel-head"><h3>Name your appliances</h3><small>Saves to the backend</small></div>
      <div style="display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end;padding:6px 0 0">
        <label>Channel 1<input id="label1" value="${channelLabel(1)}"/></label>
        <label>Channel 2<input id="label2" value="${channelLabel(2)}"/></label>
        <button class="btn primary" onclick="saveChannelLabels()">Save names</button>
      </div>
    </div>`;
}

function saveChannelLabels() {
  const labels = {
    "1": document.getElementById("label1").value.trim() || "Appliance 1",
    "2": document.getElementById("label2").value.trim() || "Appliance 2",
  };
  Api.renameChannels(AppState.activeDeviceId, labels).then(() => {
    toast("Channel names saved");
    refreshHomeData();
  }).catch(err => toast(err.message || "Failed to save names"));
}

function energyUsageView() {
  if (!AppState.activeDeviceId) return dummyDeviceBlock();
  const s = AppState.summary || {};
  const total7 = (s.daily || []).reduce((a, d) => a + d.energy_kwh, 0);
  const avg = (s.daily || []).length ? total7 / s.daily.length : 0;
  const on1 = relayState(1) === "ON", on2 = relayState(2) === "ON";

  return `
    ${kpiGrid([
      kpi("TODAY'S ENERGY", fmt(s.today_energy_kwh, 2, " kWh"), "today"),
      kpi("COST TODAY", "₹" + fmt(s.today_cost, 2), "today"),
      kpi("PERIOD AVERAGE", fmt(avg, 2, " kWh/day"), `${(s.daily || []).length} day(s)`),
      kpi("PERIOD TOTAL", fmt(total7, 2, " kWh"), "recorded days"),
    ])}
    <div class="dash-grid" style="margin-top:15px">
      <div class="panel"><div class="panel-head"><h3>Appliance-wise Usage</h3><small>Estimated live split by relay state</small></div>
        <div class="bar-list">
          ${barRow(channelLabel(1), on1 ? (applianceLoad(1) / ((on1 ? applianceLoad(1) : 0) + (on2 ? applianceLoad(2) : 0) || 1) * 100) : 0, on1 ? `${fmt(applianceLoad(1), 0, " W")} · ON` : "OFF")}
          ${barRow(channelLabel(2), on2 ? (applianceLoad(2) / ((on1 ? applianceLoad(1) : 0) + (on2 ? applianceLoad(2) : 0) || 1) * 100) : 0, on2 ? `${fmt(applianceLoad(2), 0, " W")} · ON` : "OFF")}
        </div>
        <p style="color:#87939e;margin-top:10px">Single power meter + relay states ⇒ the load is split evenly across ON appliances. Wire a PZEM per outlet for exact per-appliance readings.</p>
      </div>
      <div class="panel"><div class="panel-head"><h3>Daily Energy</h3><small>Recorded days</small></div>${dailyBars()}</div>
    </div>
    <div class="panel" style="margin-top:15px"><div class="panel-head"><h3>Usage History</h3><small>Latest readings</small></div>${recentTable(14)}</div>`;
}

function billPredictionView() {
  if (!AppState.activeDeviceId) return dummyDeviceBlock();
  const s = AppState.summary || {};
  const daily = s.daily || [];
  const total = daily.reduce((a, d) => a + d.energy_kwh, 0);
  const avg = daily.length ? total / daily.length : 0;

  return `
    ${kpiGrid([
      kpi("PROJECTED MONTH BILL", "₹" + fmt(s.projected_month_cost, 0), "AI forecast"),
      kpi("TODAY'S COST", "₹" + fmt(s.today_cost, 2), "today"),
      kpi("AVERAGE / DAY", "₹" + fmt(avg * rate(), 2), `${daily.length} day(s)`),
      kpi("TARIFF", "₹" + fmt(rate(), 2, "/kWh"), "flat rate"),
    ])}
    <div class="dash-grid" style="margin-top:15px">
      <div class="panel"><div class="panel-head"><h3>AI Bill Prediction</h3><small>Confidence based on ${daily.length} day(s) of data</small></div>
        <div class="pred-number">₹${fmt(s.projected_month_cost, 0)}</div>
        <small>Predicted month-end bill at current usage</small>
        <div class="progress" style="margin:17px 0 8px"><span style="width:${Math.min(100, (s.projected_month_cost || 0) / 2000 * 100)}%"></span></div>
        <small>₹${fmt(s.today_cost, 2)} so far today</small>
      </div>
      <div class="panel"><div class="panel-head"><h3>How it works</h3><small>Transparent MVP model</small></div>
        <div class="insight"><span>✦</span><div><strong>Daily energy → projection</strong><p>Average daily kWh across recorded days is multiplied to the month end at ₹${fmt(rate(), 2)}/kWh.</p></div></div>
        <div class="insight"><span>💡</span><div><strong>Save more</strong><p>Use the Appliance switches: every hour an appliance is OFF saves its load × tariff.</p></div></div>
      </div>
    </div>
    <div class="panel" style="margin-top:15px"><div class="panel-head"><h3>Daily energy & cost</h3><small>Last ${daily.length} day(s)</small></div>${dailyBars()}</div>`;
}

function monthlyReportsView() {
  if (!AppState.activeDeviceId) return dummyDeviceBlock();
  const s = AppState.summary || {};
  const daily = s.daily || [];
  if (!daily.length) {
    return `<div class="panel"><div class="panel-head"><h3>Monthly Reports</h3></div><p style="color:#87939e">Not enough energy data yet. Keep telemetry flowing for a full day to build reports.</p></div>`;
  }
  const total = daily.reduce((a, d) => a + d.energy_kwh, 0);
  const cost = daily.reduce((a, d) => a + d.estimated_cost, 0);
  const rows = daily.slice().reverse().map(d => `
    <tr><td>${d.date}</td><td>${d.energy_kwh.toFixed(3)} kWh</td><td>₹${d.estimated_cost.toFixed(2)}</td></tr>`).join("");

  return `
    ${kpiGrid([
      kpi("REPORT PERIOD ENERGY", fmt(total, 2, " kWh"), `${daily.length} day(s)`),
      kpi("REPORT PERIOD COST", "₹" + fmt(cost, 2), "estimated"),
      kpi("TODAY'S ENERGY", fmt(s.today_energy_kwh, 2, " kWh"), "today"),
      kpi("PROJECTED MONTH BILL", "₹" + fmt(s.projected_month_cost, 0), "AI forecast"),
    ])}
    <div class="panel" style="margin-top:15px"><div class="panel-head"><h3>Daily breakdown</h3><small>Energy & estimated cost</small></div>
      <table class="area-table"><thead><tr><th>Date</th><th>Energy</th><th>Estimated cost</th></tr></thead><tbody>${rows}</tbody></table>
    </div>
    <div class="panel" style="margin-top:15px"><div class="panel-head"><h3>Monthly trend</h3><small>Daily consumption</small></div>${dailyBars()}</div>`;
}

function aiInsightsView() {
  if (!AppState.activeDeviceId) return dummyDeviceBlock();
  const items = insightsFromData(6);
  return `
    <div class="panel"><div class="panel-head"><h3>AI Insights & Saving Recommendations</h3><small>Generated live from your data</small></div>
      <div style="display:grid;gap:0">${items.map(x => `<div class="insight"><span>${x.ic}</span><div><strong>${x.title}</strong><p>${x.body}</p></div></div>`).join("")}</div>
    </div>
    <div class="panel" style="margin-top:15px"><div class="panel-head"><h3>Control the source of waste</h3><small>Suggested switches</small></div>
      <p style="color:#87939e;margin-bottom:12px">Every insight above that points at an appliance is actionable right here — flip it OFF wirelessly.</p>
      <div class="appliance-grid">${relayCard(1, true)}${relayCard(2, true)}</div>
    </div>`;
}