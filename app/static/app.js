// KidGate frontend — polls status, renders state, posts actions. Vanilla JS, no build.
const $ = (id) => document.getElementById(id);
let expiresAt = null;      // ISO string of current timer expiry, or null
let tick = null;
let busy = false;          // an action request is in flight
let loaded = false;        // first successful status has rendered
let lastKey = "";          // guards redundant aria-live announcements
let toastTimer = null;

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

function setText(el, val) { if (el.textContent !== val) el.textContent = val; }

function render(st) {
  const card = $("card"), state = $("state"), source = $("source"),
        primary = $("primary"), retry = $("retry");
  card.classList.remove("blocked", "allowed", "unknown");

  if (st.error) {
    card.classList.add("unknown");
    setText(state, "Can't reach UniFi");
    setText(source, st.detail || "Check the UniFi controller is powered on.");
    primary.style.display = "none";
    retry.style.display = "block";
    setChipsDisabled(true);
    expiresAt = null; updateCountdown();
    lastKey = "error";
    return;
  }

  retry.style.display = "none";
  primary.style.display = "block";
  if (!busy) { primary.disabled = false; setChipsDisabled(false); }
  loaded = true;

  card.classList.add(st.blocked ? "blocked" : "allowed");
  setText(state, st.blocked ? "BLOCKED" : "ONLINE");
  let src = SOURCE_LABEL[st.source] || st.source;
  if (st.source === "allowed" && st.scheduled_enabled)
    src += ` · bedtime ${st.bedtime} schedule on`;
  else if (st.source === "manual" || (st.source === "allowed" && !st.scheduled_enabled))
    src += " · stays until you change it";
  setText(source, src);

  // Primary button reflects the ad-hoc switch (Block/Allow now), unless a request is pending.
  if (!busy) {
    if (st.adhoc_enabled) {
      primary.className = "primary allow"; primary.textContent = "Allow now"; primary.dataset.action = "allow";
    } else {
      primary.className = "primary block"; primary.textContent = "Block now"; primary.dataset.action = "block";
    }
  }
  // Show override section only during/near bedtime or when an override is active.
  $("override-section").style.display =
    (st.within_bedtime || st.source === "override" || st.scheduled_enabled) ? "block" : "none";

  expiresAt = st.expires_at;
  updateCountdown();
  lastKey = `${st.blocked}|${st.source}`;
}

function updateCountdown() {
  const el = $("countdown");
  if (!expiresAt) { el.classList.remove("show"); el.textContent = ""; return; }
  const ms = new Date(expiresAt) - new Date();
  if (ms <= 0) { el.classList.remove("show"); el.textContent = ""; refresh(); return; }
  el.textContent = "⏳ " + fmtRemaining(ms) + " left";
  el.classList.add("show");
}

function setChipsDisabled(on) {
  document.querySelectorAll(".chip").forEach((c) => { c.disabled = on; });
}

function showError(msg) {
  const b = $("banner");
  b.innerHTML = "";
  b.append(document.createTextNode(msg));
  const x = document.createElement("button");
  x.className = "dismiss"; x.setAttribute("aria-label", "Dismiss"); x.textContent = "×";
  x.addEventListener("click", () => b.classList.remove("show"));
  b.appendChild(x);
  b.classList.remove("show"); void b.offsetWidth;   // restart the shake if same error repeats
  b.classList.add("show");
  clearTimeout(b._t);
  b._t = setTimeout(() => b.classList.remove("show"), 8000);   // errors are high-stakes — linger
}

// ── Undo toast ──────────────────────────────────────────────────────────
function showToast(msg, undoPath, undoBody) {
  const t = $("toast");
  setText($("toast-msg"), msg);
  const undo = $("toast-undo");
  undo.style.display = undoPath ? "" : "none";
  undo.onclick = undoPath ? () => { hideToast(); post(undoPath, undoBody); } : null;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(hideToast, 6000);
}
function hideToast() { $("toast").classList.remove("show"); }

async function refresh() {
  if (busy) return;
  try {
    const r = await fetch("/api/status");
    if (r.status === 401) { location.href = "/login"; return; }
    render(await r.json());
  } catch (e) { render({ error: true }); }
}

// Post an action. `pendingEl` gets a pending look; `undo` = {msg, path, body} shown on success.
async function post(path, body, pendingEl, undo) {
  if (busy) return;
  busy = true;
  hideToast();
  const primary = $("primary");
  primary.disabled = true; setChipsDisabled(true);
  let restoreLabel = null;
  if (pendingEl === primary) {
    restoreLabel = primary.textContent;
    primary.classList.add("pending"); primary.textContent = "Working…";
  } else if (pendingEl) {
    pendingEl.dataset._label = pendingEl.textContent;
  }
  try {
    const r = await fetch(path, {
      method: "POST",
      headers: body ? { "Content-Type": "application/x-www-form-urlencoded" } : {},
      body: body ? new URLSearchParams(body) : undefined,
    });
    const data = await r.json();
    busy = false;
    if (pendingEl === primary) primary.classList.remove("pending");
    if (!r.ok) { render(data.error ? data : { error: true, detail: data.detail }); showError(data.detail || "Action failed"); return; }
    render(data);
    if (undo) showToast(undo.msg, undo.path, undo.body);
  } catch (e) {
    busy = false;
    if (pendingEl === primary) { primary.classList.remove("pending"); if (restoreLabel) primary.textContent = restoreLabel; }
    primary.disabled = false; setChipsDisabled(false);
    showError("Network error — action may not have gone through.");
  }
}

document.addEventListener("click", (e) => {
  const t = e.target.closest("button");
  if (!t || busy) return;

  if (t.id === "retry") { setText($("state"), "Checking…"); refresh(); return; }
  if (t.id === "primary") {
    const act = t.dataset.action;
    if (act === "block") post("/api/block", null, t, { msg: "Internet blocked.", path: "/api/allow" });
    else if (act === "allow") post("/api/allow", null, t, { msg: "Internet allowed.", path: "/api/block" });
  } else if (t.dataset.temp) {
    post("/api/temp-block", { minutes: t.dataset.temp }, t, { msg: `Blocked for ${t.dataset.temp} min.`, path: "/api/allow" });
  } else if (t.dataset.bedtime) {
    post("/api/until-bedtime", null, t, { msg: "Blocked until bedtime.", path: "/api/allow" });
  } else if (t.dataset.override) {
    post("/api/override", { minutes: t.dataset.override }, t, { msg: `+${t.dataset.override} min granted.`, path: "/api/cancel-override" });
  } else if (t.dataset.cancelOverride) {
    post("/api/cancel-override", null, t);
  }
});

refresh();
setInterval(refresh, 5000);   // server truth every 5s
tick = setInterval(updateCountdown, 1000);  // smooth countdown
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/static/sw.js").catch(() => {});
