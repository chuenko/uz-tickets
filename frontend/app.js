const tg = window.Telegram?.WebApp;
const API = window.API_BASE;
const INIT = tg?.initData || "";

if (tg) { tg.ready(); tg.expand(); }

// ── безпечна зона зверху (виріз + шапка Telegram), щоб контент не ліз під них ──
function applySafeArea() {
  const top = (tg?.safeAreaInset?.top || 0) + (tg?.contentSafeAreaInset?.top || 0);
  document.documentElement.style.setProperty("--safe-top", top + "px");
}
if (tg) {
  tg.onEvent?.("safeAreaChanged", applySafeArea);
  tg.onEvent?.("contentSafeAreaChanged", applySafeArea);
  applySafeArea();
}

// ── helpers ───────────────────────────────────
function $(id) { return document.getElementById(id); }
function show(view) {
  ["view-list", "view-add", "view-status", "view-settings"].forEach(v => $(v).hidden = v !== view);
  document.body.classList.toggle("subview", view !== "view-list");
  window.scrollTo({ top: 0, behavior: "instant" });
}
function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 2200);
}
function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
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
let editingFrom = false;
let editingTo = false;

// ── список маршрутів ──────────────────────────
async function loadRoutes() {
  const box = $("routes");
  box.innerHTML = '<div class="empty">Завантажуємо ваші маршрути…</div>';
  try {
    const { routes } = await api("/api/routes");
    if (!routes.length) {
      box.innerHTML = '<div class="empty">Тут з’являться ваші маршрути.<br>Додайте перший — і ми почнемо шукати квитки.</div>';
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
  const extra = [];
  if (r.train_filter) extra.push("поїзди: " + esc(r.train_filter));
  else if (r.wagon_filter) extra.push("вагони: " + esc(r.wagon_filter));
  const sub = esc(r.date) + (r.active ? "" : " · ⏸ пауза") + (extra.length ? " · " + extra.join(" · ") : "");
  el.innerHTML = `
    <div class="row">
      <div class="ico${r.active ? "" : " paused"}"></div>
      <div>
        <div class="title">${esc(r.from_name)} → ${esc(r.to_name)}</div>
        <div class="meta">${sub}</div>
      </div>
    </div>
    <div class="actions">
      <button data-act="status" class="grow">Перевірити місця</button>
      <button data-act="settings" title="Налаштування">⚙</button>
      <button data-act="toggle" title="${r.active ? "Призупинити" : "Відновити"}">${r.active ? "Ⅱ" : "▶"}</button>
      <button data-act="delete" class="danger" title="Видалити">×</button>
    </div>`;
  el.querySelector('[data-act="status"]').onclick = () => openStatus(r);
  el.querySelector('[data-act="settings"]').onclick = () => openSettings(r);
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
  editingFrom = editingTo = false;
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
  const wasEditing = editingFrom;
  editingFrom = false;
  draft.from = st;
  $("picked-from").textContent = "Звідки: " + st.name;
  $("from-results").innerHTML = "";
  $("step-from").hidden = true;
  if (wasEditing) {
    draft.to = null;
    $("to-q").value = "";
    $("to-results").innerHTML = "";
    $("step-date").hidden = true;
  }
  $("step-to").hidden = false;
  $("to-q").focus();
}
function pickTo(st) {
  if (st.id === draft.from?.id) { toast("Оберіть іншу станцію"); return; }
  editingTo = false;
  draft.to = st;
  $("picked-to").textContent = `${draft.from.name} → ${st.name}`;
  $("to-results").innerHTML = "";
  $("step-to").hidden = true;
  $("step-date").hidden = false;
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function editFrom() {
  if (!draft.from) return;
  editingFrom = true;
  $("from-q").value = draft.from.name;
  $("from-results").innerHTML = "";
  $("step-to").hidden = true;
  $("step-date").hidden = true;
  $("step-from").hidden = false;
  $("from-q").focus();
  $("from-q").select();
}

function editTo() {
  if (!draft.to) return;
  editingTo = true;
  $("to-q").value = draft.to.name;
  $("to-results").innerHTML = "";
  $("step-from").hidden = true;
  $("step-date").hidden = true;
  $("step-to").hidden = false;
  $("to-q").focus();
  $("to-q").select();
}

function restorePickedFrom() {
  if (!editingFrom || !draft.from) return;
  editingFrom = false;
  $("from-results").innerHTML = "";
  $("step-from").hidden = true;
  if (draft.to) $("step-date").hidden = false;
  else $("step-to").hidden = false;
}

function restorePickedTo() {
  if (!editingTo || !draft.to) return;
  editingTo = false;
  $("to-results").innerHTML = "";
  $("step-to").hidden = true;
  $("step-date").hidden = false;
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
    `<div class="route"><div class="row"><div class="ico"></div><div>
       <div class="title">${esc(r.from_name)} → ${esc(r.to_name)}</div>
       <div class="meta">${esc(r.date)}</div></div></div></div>`;
  $("status-body").innerHTML = '<div class="empty">Завантаження ~10с…</div>';
  try {
    const { ok, trains } = await api(`/api/routes/${r.key}/status`);
    if (!ok) { $("status-body").innerHTML = '<div class="empty">⚠️ Не вдалося отримати дані</div>'; return; }
    if (!trains.length) { $("status-body").innerHTML = '<div class="empty">Поїздів не знайдено</div>'; return; }
    $("status-body").innerHTML = trains.map(t => trainCard(t, r)).join("");
  } catch (e) {
    $("status-body").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

function trainCard(t, route) {
  const bookingUrl =
    `https://booking.uz.gov.ua/search-trips/${encodeURIComponent(route.from_id)}/` +
    `${encodeURIComponent(route.to_id)}/list?startDate=${encodeURIComponent(route.date)}`;
  const rows = Object.entries(t.seats)
    .filter(([, s]) => s.seats > 0)
    .map(([code, s]) =>
      `<tr><td class="ty"><a class="train-link" href="${bookingUrl}" target="_blank" rel="noopener">${esc(s.title)}<span>↗</span></a></td><td class="n">${s.seats}</td><td class="p">${s.price ? s.price + "₴" : "—"}</td></tr>`)
    .join("");
  if (!rows) return "";
  return `<div class="train">
      <div class="thead"><a class="tnum train-link" href="${bookingUrl}" target="_blank" rel="noopener">№${esc(t.number)}<span>↗</span></a><span class="ttime">${esc(t.departure)} → ${esc(t.arrival)}</span></div>
      <table class="seats">${rows}</table>
    </div>`;
}

// ── налаштування маршруту ─────────────────────
let settingsKey = null;
let settingsPassengers = [];

function renderPassengers() {
  const box = $("set-passengers");
  box.innerHTML = settingsPassengers.map((p, i) => `
    <div class="passenger-row" data-index="${i}">
      <input data-field="name" placeholder="Ім’я" value="${esc(p.name || "")}" />
      <input data-field="surname" placeholder="Прізвище" value="${esc(p.surname || "")}" />
      <button type="button" aria-label="Видалити">×</button>
    </div>`).join("");
  box.querySelectorAll(".passenger-row").forEach(row => {
    const i = Number(row.dataset.index);
    row.querySelectorAll("input").forEach(input => {
      input.oninput = () => settingsPassengers[i][input.dataset.field] = input.value;
    });
    row.querySelector("button").onclick = () => {
      settingsPassengers.splice(i, 1);
      renderPassengers();
    };
  });
}

function toggleAutobronFields() {
  $("autobron-fields").hidden = !$("set-autobron").checked;
}

function renderTrainChips(trains, selected) {
  const box = $("set-trains-chips");
  box.innerHTML = "";
  const sel = new Set((selected || []).map(s => s.trim().toUpperCase()).filter(Boolean));
  const items = [], seen = new Set();
  for (const n of sel) { items.push({ number: n }); seen.add(n); }
  for (const t of (trains || [])) {
    const n = (t.number || "").toString();
    if (n && !seen.has(n.toUpperCase())) { items.push(t); seen.add(n.toUpperCase()); }
  }
  if (!items.length) {
    box.innerHTML = '<div class="meta" style="margin:2px">Натисни «Завантажити список поїздів»</div>';
    return;
  }
  for (const t of items) {
    const num = (t.number || "").toString();
    const c = document.createElement("span");
    c.className = "chip" + (sel.has(num.toUpperCase()) ? " on" : "");
    c.dataset.num = num;
    c.textContent = "№" + num + (t.departure ? " · " + t.departure.split(" ").pop() : "");
    c.onclick = () => c.classList.toggle("on");
    box.appendChild(c);
  }
}

function addTrainManually() {
  const input = $("set-train-manual");
  const number = input.value.trim().replace(/^№\s*/, "").toUpperCase();
  if (!number) return;
  if (!/^[0-9]{1,4}[A-ZА-ЯІЇЄҐ]?$/.test(number)) {
    toast("Введіть номер, наприклад 120Д");
    return;
  }
  const selected = [...document.querySelectorAll("#set-trains-chips .chip.on")]
    .map(c => c.dataset.num);
  if (!selected.some(n => n.toUpperCase() === number)) selected.push(number);
  renderTrainChips([], selected);
  input.value = "";
}

async function loadTrains() {
  const sel = [...document.querySelectorAll("#set-trains-chips .chip.on")].map(c => c.dataset.num);
  const btn = $("set-load-trains");
  btn.textContent = "⏳ Завантаження ~15с…"; btn.disabled = true;
  try {
    const { trains } = await api(`/api/routes/${settingsKey}/trains`);
    renderTrainChips(trains, sel);
    btn.textContent = "🔄 Оновити список";
  } catch (e) {
    toast("Помилка: " + e.message);
    btn.textContent = "📋 Завантажити список поїздів";
  }
  btn.disabled = false;
}

function openSettings(r) {
  settingsKey = r.key;
  $("set-head").innerHTML =
    `<div class="route"><div class="row"><div class="ico"></div><div>
       <div class="title">${esc(r.from_name)} → ${esc(r.to_name)}</div>
       <div class="meta">${esc(r.date)}</div></div></div></div>`;
  const wf = (r.wagon_filter || "").split(",").map(s => s.trim().toUpperCase()).filter(Boolean);
  document.querySelectorAll("#set-wagons .chip").forEach(c =>
    c.classList.toggle("on", wf.includes(c.dataset.code.toUpperCase())));
  renderTrainChips([], (r.train_filter || "").split(",").map(s => s.trim()).filter(Boolean));
  $("set-train-manual").value = "";
  $("set-load-trains").textContent = "📋 Завантажити список поїздів";
  $("set-qfrom").value = r.quiet_from || "";
  $("set-qto").value = r.quiet_to || "";
  $("set-notify").value = r.notify_on || "appear_decrease";
  $("set-autobron").checked = Boolean(r.autobron);
  $("set-seat-kind").value = r.seat_kind || "";
  $("set-qty").value = String(r.qty || 1);
  settingsPassengers = Array.isArray(r.passengers) ? r.passengers.map(p => ({ ...p })) : [];
  if (!settingsPassengers.length) settingsPassengers.push({ name: "", surname: "" });
  renderPassengers();
  toggleAutobronFields();
  show("view-settings");
}

async function saveSettings() {
  const wagons = [...document.querySelectorAll("#set-wagons .chip.on")].map(c => c.dataset.code);
  const trains = [...document.querySelectorAll("#set-trains-chips .chip.on")].map(c => c.dataset.num);
  try {
    await api(`/api/routes/${settingsKey}/settings`, {
      method: "POST",
      body: JSON.stringify({
        wagon_filter: wagons.join(","),
        train_filter: trains.join(","),
        quiet_from: $("set-qfrom").value || "",
        quiet_to: $("set-qto").value || "",
        notify_on: $("set-notify").value,
        autobron: $("set-autobron").checked,
        seat_kind: $("set-seat-kind").value,
        qty: Number($("set-qty").value),
        passengers: settingsPassengers
          .map(p => ({ name: p.name.trim(), surname: p.surname.trim() }))
          .filter(p => p.name && p.surname),
      }),
    });
    toast("Збережено ✅");
    show("view-list");
    loadRoutes();
  } catch (e) {
    toast("Помилка: " + e.message);
  }
}

// ── події ─────────────────────────────────────
$("btn-add").onclick = startAdd;
$("btn-cancel").onclick = () => show("view-list");
$("btn-back").onclick = () => show("view-list");
$("btn-save").onclick = saveRoute;
$("from-q").oninput = searchFrom;
$("to-q").oninput = searchTo;
$("picked-from").onclick = editFrom;
$("picked-to").onclick = editTo;
$("picked-from").onkeydown = e => { if (e.key === "Enter" || e.key === " ") editFrom(); };
$("picked-to").onkeydown = e => { if (e.key === "Enter" || e.key === " ") editTo(); };
$("from-q").onblur = () => setTimeout(restorePickedFrom, 180);
$("to-q").onblur = () => setTimeout(restorePickedTo, 180);
$("set-save").onclick = saveSettings;
$("set-back").onclick = () => show("view-list");
$("set-load-trains").onclick = loadTrains;
$("set-add-train").onclick = addTrainManually;
$("set-train-manual").onkeydown = e => {
  if (e.key === "Enter") {
    e.preventDefault();
    addTrainManually();
  }
};
$("set-autobron").onchange = toggleAutobronFields;
$("add-passenger").onclick = () => {
  settingsPassengers.push({ name: "", surname: "" });
  renderPassengers();
};
// чіпи майстра додавання (зберігають у draft.wagons)
document.querySelectorAll("#wagons .chip").forEach(c => {
  c.onclick = () => {
    c.classList.toggle("on");
    const code = c.dataset.code;
    if (draft.wagons.has(code)) draft.wagons.delete(code); else draft.wagons.add(code);
  };
});
// чіпи екрану налаштувань (просто toggle, читаємо при збереженні)
document.querySelectorAll("#set-wagons .chip").forEach(c => {
  c.onclick = () => c.classList.toggle("on");
});

// ── старт ─────────────────────────────────────
if (!API || API.includes("CHANGE-ME")) {
  $("routes").innerHTML = '<div class="empty">⚠️ Не налаштований API_BASE у config.js</div>';
} else {
  loadRoutes();
}
