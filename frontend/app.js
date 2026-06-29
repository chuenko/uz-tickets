const tg = window.Telegram?.WebApp;
const API = window.API_BASE;
const INIT = tg?.initData || "";

if (tg) { tg.ready(); tg.expand(); }

// ── helpers ───────────────────────────────────
function $(id) { return document.getElementById(id); }
function show(view) {
  ["view-list", "view-add", "view-status"].forEach(v => $(v).hidden = v !== view);
}
function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 2200);
}
function esc(s) {
  return String(s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      "X-Init-Data": INIT,
      ...(opts.headers || {}),
    },
  });
  if (!res.ok) {
    let detail = res.status;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return res.json();
}

function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

// ── state ─────────────────────────────────────
const draft = { from: null, to: null, wagons: new Set() };

// ── список маршрутів ──────────────────────────
async function loadRoutes() {
  const box = $("routes");
  box.innerHTML = '<div class="empty">Завантаження…</div>';
  try {
    const { routes } = await api("/api/routes");
    if (!routes.length) {
      box.innerHTML = '<div class="empty">Поки немає маршрутів.<br>Додай перший 👆</div>';
      return;
    }
    box.innerHTML = "";
    for (const r of routes) box.appendChild(routeCard(r));
  } catch (e) {
    box.innerHTML = `<div class="empty">Помилка: ${esc(e.message)}</div>`;
  }
}

function routeCard(r) {
  const el = document.createElement("div");
  el.className = "route";
  const dot = r.active ? "🟢" : "🔴";
  el.innerHTML = `
    <div class="title">${dot} ${esc(r.from_name)} → ${esc(r.to_name)}</div>
    <div class="meta">📅 ${esc(r.date)}${r.wagon_filter ? " · вагони: " + esc(r.wagon_filter) : ""}</div>
    <div class="actions">
      <button data-act="status">🔍 Статус</button>
      <button data-act="toggle">${r.active ? "⏸ Пауза" : "▶️ Увімкн."}</button>
      <button data-act="delete" class="danger">🗑</button>
    </div>`;
  el.querySelector('[data-act="status"]').onclick = () => openStatus(r);
  el.querySelector('[data-act="toggle"]').onclick = async () => {
    await api(`/api/routes/${r.key}/active?active=${!r.active}`, { method: "POST" });
    loadRoutes();
  };
  el.querySelector('[data-act="delete"]').onclick = async () => {
    if (tg) tg.HapticFeedback?.impactOccurred("medium");
    await api(`/api/routes/${r.key}`, { method: "DELETE" });
    toast("Видалено");
    loadRoutes();
  };
  return el;
}

// ── додавання ─────────────────────────────────
function startAdd() {
  draft.from = draft.to = null;
  draft.wagons.clear();
  $("from-q").value = "";
  $("to-q").value = "";
  $("from-results").innerHTML = "";
  $("to-results").innerHTML = "";
  $("step-from").hidden = false;
  $("step-to").hidden = true;
  $("step-date").hidden = true;
  document.querySelectorAll(".chip").forEach(c => c.classList.remove("on"));
  const d = new Date(); d.setDate(d.getDate() + 1);
  $("date").value = d.toISOString().slice(0, 10);
  $("date").min = new Date().toISOString().slice(0, 10);
  show("view-add");
}

const searchFrom = debounce(() => doSearch($("from-q").value, "from-results", pickFrom), 350);
const searchTo = debounce(() => doSearch($("to-q").value, "to-results", pickTo), 350);

async function doSearch(q, boxId, onPick) {
  const box = $(boxId);
  if (!q || q.trim().length < 2) { box.innerHTML = ""; return; }
  box.innerHTML = '<div class="empty">Пошук…</div>';
  try {
    const { stations } = await api("/api/stations?q=" + encodeURIComponent(q.trim()));
    if (!stations.length) { box.innerHTML = '<div class="empty">Нічого не знайдено</div>'; return; }
    box.innerHTML = "";
    for (const st of stations) {
      const item = document.createElement("div");
      item.className = "item";
      item.textContent = st.name;
      item.onclick = () => onPick(st);
      box.appendChild(item);
    }
  } catch (e) {
    box.innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

function pickFrom(st) {
  draft.from = st;
  $("picked-from").textContent = "Звідки: " + st.name;
  $("step-to").hidden = false;
  $("to-q").focus();
}
function pickTo(st) {
  if (st.id === draft.from?.id) { toast("Оберіть іншу станцію"); return; }
  draft.to = st;
  $("picked-to").textContent = `${draft.from.name} → ${st.name}`;
  $("step-date").hidden = false;
  $("step-date").scrollIntoView({ behavior: "smooth" });
}

async function saveRoute() {
  if (!draft.from || !draft.to || !$("date").value) { toast("Заповни всі поля"); return; }
  const wagon_filter = [...draft.wagons].join(",");
  try {
    await api("/api/routes", {
      method: "POST",
      body: JSON.stringify({
        from_id: draft.from.id, from_name: draft.from.name,
        to_id: draft.to.id, to_name: draft.to.name,
        date: $("date").value, wagon_filter,
      }),
    });
    toast("Маршрут додано ✅");
    show("view-list");
    loadRoutes();
  } catch (e) {
    toast("Помилка: " + e.message);
  }
}

// ── статус ────────────────────────────────────
async function openStatus(r) {
  show("view-status");
  $("status-head").innerHTML =
    `<div class="route"><div class="title">${esc(r.from_name)} → ${esc(r.to_name)}</div>
     <div class="meta">📅 ${esc(r.date)}</div></div>`;
  $("status-body").innerHTML = '<div class="empty">Завантаження ~10с…</div>';
  try {
    const { ok, trains } = await api(`/api/routes/${r.key}/status`);
    if (!ok) { $("status-body").innerHTML = '<div class="empty">⚠️ Не вдалося отримати дані</div>'; return; }
    if (!trains.length) { $("status-body").innerHTML = '<div class="empty">Поїздів не знайдено</div>'; return; }
    $("status-body").innerHTML = trains.map(trainCard).join("");
  } catch (e) {
    $("status-body").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

function trainCard(t) {
  const rows = Object.entries(t.seats)
    .filter(([, s]) => s.seats > 0)
    .map(([code, s]) =>
      `<tr><td class="ty">${esc(s.title)}</td><td class="n">${s.seats}</td><td class="p">${s.price ? s.price + "₴" : "—"}</td></tr>`)
    .join("");
  if (!rows) return "";
  return `<div class="train">
      <div class="thead"><span class="tnum">№${esc(t.number)}</span><span class="ttime">${esc(t.departure)} → ${esc(t.arrival)}</span></div>
      <table class="seats">${rows}</table>
    </div>`;
}

// ── події ─────────────────────────────────────
$("btn-add").onclick = startAdd;
$("btn-cancel").onclick = () => show("view-list");
$("btn-back").onclick = () => show("view-list");
$("btn-save").onclick = saveRoute;
$("from-q").oninput = searchFrom;
$("to-q").oninput = searchTo;
document.querySelectorAll(".chip").forEach(c => {
  c.onclick = () => {
    c.classList.toggle("on");
    const code = c.dataset.code;
    if (draft.wagons.has(code)) draft.wagons.delete(code); else draft.wagons.add(code);
  };
});

// ── старт ─────────────────────────────────────
if (!API || API.includes("CHANGE-ME")) {
  $("routes").innerHTML = '<div class="empty">⚠️ Не налаштований API_BASE у config.js</div>';
} else {
  loadRoutes();
}
