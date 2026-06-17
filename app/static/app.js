// KidGate frontend — polls status, renders state, posts actions. Vanilla JS, no build.
const $ = (id) => document.getElementById(id);
let expiresAt = null;      // ISO string of current timer expiry, or null
let tick = null;

function fmtRemaining(ms) {
  if (ms <= 0) return "0:00";
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h > 0 ? `${h}:${String(m).padStart(2,"0")}:${String(sec).padStart(2,"0")}`
               : `${m}:${String(sec).padStart(2,"0")}`;
}

const SOURCE_LABEL = {
  manual: "Manually blocked", temp_block: "Temporary block",
  schedule: "Bedtime schedule", override: "Bedtime override active", allowed: "Open",
};

function render(st) {
  const card = $("card"), state = $("state"), source = $("source"), primary = $("primary");
  card.classList.remove("blocked", "allowed", "unknown");
  if (st.error) {
    card.classList.add("unknown");
    state.textContent = "Can't reach UniFi";
    source.textContent = st.detail || "";
    primary.style.display = "none";
    expiresAt = null; updateCountdown();
    return;
  }
  primary.style.display = "block";
  card.classList.add(st.blocked ? "blocked" : "allowed");
  state.textContent = st.blocked ? "BLOCKED" : "ONLINE";
  let src = SOURCE_LABEL[st.source] || st.source;
  if (st.source === "allowed" && st.scheduled_enabled)
    src += ` · bedtime ${st.bedtime} schedule on`;
  source.textContent = src;

  // Primary button reflects the ad-hoc switch (Block/Allow now).
  if (st.adhoc_enabled) {
    primary.className = "primary allow"; primary.textContent = "Allow now"; primary.dataset.action = "allow";
  } else {
    primary.className = "primary block"; primary.textContent = "Block now"; primary.dataset.action = "block";
  }
  // Show override section only during/near bedtime or when an override is active.
  $("override-section").style.display =
    (st.within_bedtime || st.source === "override" || st.scheduled_enabled) ? "block" : "none";

  expiresAt = st.expires_at;
  updateCountdown();
}

function updateCountdown() {
  const el = $("countdown");
  if (!expiresAt) { el.textContent = ""; return; }
  const ms = new Date(expiresAt) - new Date();
  if (ms <= 0) { el.textContent = ""; refresh(); return; }
  el.textContent = "⏳ " + fmtRemaining(ms) + " left";
}

function showError(msg) {
  const b = $("banner"); b.textContent = msg; b.classList.add("show");
  setTimeout(() => b.classList.remove("show"), 4000);
}

async function refresh() {
  try {
    const r = await fetch("/api/status");
    if (r.status === 401) { location.href = "/login"; return; }
    render(await r.json());
  } catch (e) { render({ error: true, detail: "network error" }); }
}

async function post(path, body) {
  try {
    const r = await fetch(path, {
      method: "POST",
      headers: body ? { "Content-Type": "application/x-www-form-urlencoded" } : {},
      body: body ? new URLSearchParams(body) : undefined,
    });
    const data = await r.json();
    if (!r.ok) { showError(data.detail || "Action failed"); return; }
    render(data);
  } catch (e) { showError("Network error"); }
}

document.addEventListener("click", (e) => {
  const t = e.target;
  if (t.id === "primary") {
    const act = t.dataset.action;
    if (act === "block" && !confirm("Block the kids' internet now?")) return;
    if (act === "allow" && !confirm("Allow the kids' internet now?")) return;
    post(act === "block" ? "/api/block" : "/api/allow");
  } else if (t.dataset.temp) {
    if (confirm(`Block internet for ${t.dataset.temp} minutes?`)) post("/api/temp-block", { minutes: t.dataset.temp });
  } else if (t.dataset.bedtime) {
    if (confirm("Block internet until bedtime?")) post("/api/until-bedtime");
  } else if (t.dataset.override) {
    if (confirm(`Grant ${t.dataset.override} more minutes past bedtime?`)) post("/api/override", { minutes: t.dataset.override });
  } else if (t.dataset.cancelOverride) {
    if (confirm("End the override and resume the bedtime schedule?")) post("/api/cancel-override");
  }
});

refresh();
setInterval(refresh, 5000);   // server truth every 5s
tick = setInterval(updateCountdown, 1000);  // smooth countdown
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/static/sw.js").catch(() => {});
