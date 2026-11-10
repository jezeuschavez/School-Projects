"use strict";
/* Slug Macros dashboard — vanilla JS, no build step. */

const state = { tab: "today", meta: null, day: {}, settings: null, progress: null };
const view = document.getElementById("view");
const SLOT_LABEL = { breakfast: "Breakfast", brunch: "Brunch", lunch: "Lunch", dinner: "Dinner", snack: "Snack" };
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

// ---------------------------------------------------------------- utils
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const r0 = (n) => Math.round(n ?? 0);
const r1 = (n) => Math.round((n ?? 0) * 10) / 10;
const fmtDate = (iso, opts = { weekday: "long", month: "short", day: "numeric" }) =>
  new Date(iso + "T12:00:00").toLocaleDateString(undefined, opts);

async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || "GET",
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
    credentials: "same-origin",
  });
  if (res.status === 401) { renderLogin(); throw new Error("login required"); }
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (_) {}
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return res.json();
}

let toastTimer;
function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 3200);
}

function loading(msg) {
  view.innerHTML = `<div class="card empty"><div class="spinner"></div>${esc(msg)}</div>`;
}

// ---------------------------------------------------------------- sheet
const sheet = document.getElementById("sheet");
const backdrop = document.getElementById("sheet-backdrop");
function openSheet(html) {
  sheet.innerHTML = `<div class="grabber"></div>${html}`;
  sheet.hidden = false; backdrop.hidden = false;
  sheet.scrollTop = 0;
}
function closeSheet() { sheet.hidden = true; backdrop.hidden = true; sheet.innerHTML = ""; }
backdrop.addEventListener("click", closeSheet);

// ---------------------------------------------------------------- tabs
document.getElementById("tabbar").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-tab]");
  if (!b) return;
  setTab(b.dataset.tab);
});
function setTab(tab) {
  state.tab = tab;
  document.querySelectorAll("#tabbar button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  try { localStorage.setItem("tab", tab); } catch (_) {}
  render();
  window.scrollTo(0, 0);
}

async function render() {
  const sub = document.getElementById("topbar-sub");
  if (state.tab === "today" || state.tab === "tomorrow") {
    const iso = state.meta[state.tab];
    sub.textContent = fmtDate(iso);
    return renderDay(state.tab);
  }
  sub.textContent = "";
  if (state.tab === "progress") return renderProgress();
  if (state.tab === "settings") return renderSettings();
}

// ---------------------------------------------------------------- day view
async function loadDay(which, force = false) {
  if (!force && state.day[which]) return state.day[which];
  loading(which === "tomorrow" ? "Checking tomorrow's UCSC menu… (first load can take a minute)" : "Loading today's menu and plan…");
  state.day[which] = await api(`/api/day/${state.meta[which]}`);
  return state.day[which];
}

async function renderDay(which, force = false) {
  let day;
  try { day = await loadDay(which, force); }
  catch (e) { view.innerHTML = `<div class="card empty">Couldn't load the plan.<br><span class="small">${esc(e.message)}</span><br><br><button class="btn" id="retry">Try again</button></div>`;
    document.getElementById("retry").onclick = () => renderDay(which, true); return; }
  if (state.tab !== which) return;
  view.innerHTML = dayHTML(day, which);
  bindDay(day, which);
}

function bar(label, eaten, planned, target, cls = "") {
  const max = Math.max(target, planned, eaten) * 1.08 || 1;
  const pct = (v) => Math.min(100, (v / max) * 100);
  return `<div><div class="mbar-top"><span>${label}</span><span><b>${r0(eaten)}</b> eaten · ${r0(planned)} planned / ${r0(target)}</span></div>
    <div class="track ${cls}" role="img" aria-label="${label}: ${r0(eaten)} eaten, ${r0(planned)} planned, target ${r0(target)}">
      <div class="planned" style="width:${pct(planned)}%"></div><div class="eaten" style="width:${pct(eaten)}%"></div>
      <div class="target" style="left:calc(${pct(target)}% - 1px)"></div></div></div>`;
}

function badgesFor(it) {
  const icons = state.meta.icons;
  const out = [];
  if (it.unavailable) out.push(`<span class="badge bad">No longer listed</span>`);
  if (it.has_data === false) out.push(`<span class="badge warn">No nutrition data</span>`);
  for (const ic of it.icons || []) {
    const m = icons[ic];
    if (!m) continue;
    out.push(`<span class="badge ${m.kind === "diet" ? "diet" : ""}">${esc(m.kind === "allergen" ? "Contains " + m.label.toLowerCase() : m.label)}</span>`);
  }
  return out.length ? `<div class="badges">${out.join("")}</div>` : "";
}

function dayHTML(day, which) {
  const plan = day.plan;
  const halls = state.meta.halls;
  const loc = plan?.loc || state.settings?.default_hall || "40";
  const hallSel = `<select class="hall-select" id="hall" aria-label="Dining hall">${Object.entries(halls)
    .map(([k, v]) => `<option value="${k}" ${k === loc ? "selected" : ""}>${esc(v)}</option>`).join("")}</select>`;
  const dt = day.day_type;
  const head = `<div class="card">
    <div class="row">${hallSel}<button class="icon-btn" id="regen" title="Rebuild plan" aria-label="Rebuild plan">⟳</button></div>
    <div class="controls"><div class="seg" role="group" aria-label="Day type">
      <button data-dt="rest" class="${dt === "rest" ? "on" : ""}">Rest day</button>
      <button data-dt="training" class="${dt === "training" ? "on" : ""}">Training day</button></div>
      <span class="faint small">${day.menu?.fetched_at ? "Menu checked " + new Date(day.menu.fetched_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : ""}</span></div></div>`;

  const t = day.targets, e = day.eaten, p = day.planned;
  const remainingK = r0(day.remaining.kcal), remainingP = r0(day.remaining.protein);
  const summary = `<div class="card">
    <div class="hero"><div><div class="hero-num">${remainingK.toLocaleString()}</div><div class="hero-label">kcal left today</div></div>
      <div style="text-align:right"><div class="hero-num" style="color:var(--protein)">${remainingP}<span style="font-size:20px">g</span></div><div class="hero-label">protein left</div></div></div>
    <div class="macro-bars">
      ${bar("Calories", e.kcal, p.kcal, t.kcal)}
      ${bar("Protein (g)", e.protein, p.protein, t.protein, "protein")}
      ${bar("Carbs (g)", e.carbs, p.carbs, t.carbs)}
      ${bar("Fat (g)", e.fat, p.fat, t.fat)}
    </div>
    <div class="legend"><span><i style="background:var(--accent)"></i>Eaten</span><span><i style="background:var(--accent-soft)"></i>Planned</span><span><i style="background:var(--text-2);width:2px"></i>Target</span></div>
    ${plan?.summary ? `<div class="coach">${esc(plan.summary)}</div>` : ""}
    ${plan?.notes?.length ? `<ul class="notes">${plan.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
  </div>`;

  let meals = "";
  if (!plan || plan.status !== "ok") {
    meals = `<div class="card empty">${plan?.notes?.[0] ? esc(plan.notes[0]) : "No plan yet."}<br><br><button class="btn" id="regen2">Check again</button></div>`;
  } else {
    const slots = [];
    for (const it of plan.items) if (!slots.includes(it.slot)) slots.push(it.slot);
    meals = slots.map((slot) => {
      const items = plan.items.filter((i) => i.slot === slot);
      const tot = items.reduce((a, i) => ({ k: a.k + i.totals.kcal, p: a.p + i.totals.protein }), { k: 0, p: 0 });
      const meal = items[0]?.meal;
      return `<div class="card"><div class="meal-head"><h2>${SLOT_LABEL[slot] || slot}</h2>
        <span class="meta">${r0(tot.k)} kcal · ${r0(tot.p)} g protein</span></div>
        ${slot === "snack" ? "" : `<div class="faint small">${esc(meal)} at ${esc(state.meta.halls[plan.loc])}</div>`}
        ${items.map(itemHTML).join("")}
        <div class="item-actions" style="margin-top:4px"><button class="btn ghost" data-add="${slot}" data-meal="${esc(meal)}">+ Add from menu</button></div></div>`;
    }).join("");
  }

  const logRows = day.log.map((r) => `<div class="item"><span></span><div><div class="row"><span class="item-name">${esc(r.name)}</span><span class="spacer"></span>
    <button class="chip-btn" data-dellog="${r.id}" aria-label="Delete">✕</button></div>
    <div class="item-sub">${r0(r.kcal)} kcal · ${r1(r.protein)} g P · ${r1(r.carbs)} g C · ${r1(r.fat)} g F ${r.verified ? "" : "· <span class='badge warn'>You entered</span>"}</div></div></div>`).join("");
  const other = `<div class="card"><div class="meal-head"><h2>Other food</h2></div>
    ${logRows || `<div class="faint small">Anything you ate outside the plan goes here.</div>`}
    <div class="item-actions"><button class="btn" id="log-custom">+ Log food</button><button class="btn ghost" id="log-menu">+ From dining menu</button></div></div>`;

  return head + summary + meals + other;
}

function itemHTML(it) {
  const t = it.totals;
  const step = it.qty < 1 ? 0.25 : 0.5;
  return `<div class="item ${it.eaten ? "eaten" : ""}" data-id="${it.id}">
    <button class="check ${it.eaten ? "on" : ""}" data-eat="${it.id}" aria-label="${it.eaten ? "Mark not eaten" : "Mark eaten"}">${it.eaten ? "✓" : ""}</button>
    <div><button class="item-name" data-detail="${it.id}">${esc(it.name)}</button>
      <div class="item-sub">${esc(it.amount_label)} · <b>${r0(t.kcal)}</b> kcal · <b>${r1(t.protein)}</b> g P · ${r1(t.carbs)} g C · ${r1(t.fat)} g F</div>
      ${badgesFor(it)}
      ${it.note ? `<div class="item-note">${esc(it.note)}</div>` : ""}
      <div class="item-actions">
        <span class="stepper"><button data-qty="${it.id}" data-d="${-step}" aria-label="Less">−</button><span>${it.qty}×</span><button data-qty="${it.id}" data-d="${step}" aria-label="More">+</button></span>
        <button class="chip-btn" data-swap="${it.id}">Swap</button>
      </div></div></div>`;
}

function bindDay(day, which) {
  const iso = state.meta[which];
  const update = async (promise, msg) => {
    try { state.day[which] = await promise; if (msg) toast(msg); renderDay(which); }
    catch (e) { toast(e.message); }
  };
  const regen = async (body = {}) => {
    loading("Rebuilding plan from the live menu…");
    await update(api(`/api/day/${iso}/generate`, { method: "POST", body: { loc: document.getElementById("hall")?.value, refresh_menu: true, ...body } }), "Plan rebuilt");
  };
  document.getElementById("hall").onchange = (e) => {
    loading("Loading that hall's menu…");
    update(api(`/api/day/${iso}/generate`, { method: "POST", body: { loc: e.target.value } }), "Switched dining hall");
  };
  document.getElementById("regen").onclick = () => regen();
  const r2 = document.getElementById("regen2"); if (r2) r2.onclick = () => regen();
  view.querySelectorAll("[data-dt]").forEach((b) => (b.onclick = () =>
    update(api(`/api/day/${iso}/day-type`, { method: "POST", body: { day_type: b.dataset.dt } }),
      b.dataset.dt === "training" ? "Training-day targets" : "Rest-day targets")));
  view.querySelectorAll("[data-eat]").forEach((b) => (b.onclick = () => {
    const it = day.plan.items.find((i) => i.id === b.dataset.eat);
    update(api(`/api/day/${iso}/items/${it.id}/eaten`, { method: "POST", body: { eaten: !it.eaten } }));
  }));
  view.querySelectorAll("[data-qty]").forEach((b) => (b.onclick = () => {
    const it = day.plan.items.find((i) => i.id === b.dataset.qty);
    const q = Math.max(0.25, Math.round((it.qty + parseFloat(b.dataset.d)) * 4) / 4);
    update(api(`/api/day/${iso}/items/${it.id}`, { method: "PATCH", body: { qty: q } }));
  }));
  view.querySelectorAll("[data-swap]").forEach((b) => (b.onclick = () => openSwap(day, which, b.dataset.swap)));
  view.querySelectorAll("[data-detail]").forEach((b) => (b.onclick = () => openDetail(day, which, b.dataset.detail)));
  view.querySelectorAll("[data-add]").forEach((b) => (b.onclick = () => openMenuPicker(day, which, { slot: b.dataset.add, meal: b.dataset.meal, mode: "add" })));
  view.querySelectorAll("[data-dellog]").forEach((b) => (b.onclick = () => update(api(`/api/log/${b.dataset.dellog}`, { method: "DELETE" }), "Removed")));
  document.getElementById("log-custom").onclick = () => openCustomLog(which);
  document.getElementById("log-menu").onclick = () => openMenuPicker(day, which, { mode: "log" });
}

// ---------------------------------------------------------------- swap
async function openSwap(day, which, id) {
  const iso = state.meta[which];
  const it = day.plan.items.find((i) => i.id === id);
  openSheet(`<h2>Swap ${esc(it.name)}</h2><div class="faint small">Same meal, portion matched to keep your totals close.</div><div class="spinner" style="margin-top:20px"></div>`);
  let alts;
  try { alts = await api(`/api/day/${iso}/items/${id}/alternatives`); } catch (e) { toast(e.message); return closeSheet(); }
  const d = (v, unit) => `<span class="${v > 0 ? "delta-pos" : "delta-neg"}">${v > 0 ? "+" : ""}${unit === "g" ? r1(v) : r0(v)}${unit}</span>`;
  openSheet(`<h2>Swap ${esc(it.name)}</h2>
    <label class="toggle"><input type="checkbox" id="dislike"> I don't like ${esc(it.name)}. Don't suggest it again</label>
    ${alts.length ? alts.map((a, i) => `<button class="alt" data-i="${i}"><b>${esc(a.name)}</b>
      <span class="small muted">${esc(a.amount_label)} · ${r0(a.totals.kcal)} kcal (${d(a.delta.kcal, "")}) · ${r1(a.totals.protein)} g P (${d(a.delta.protein, "g")})</span>
      ${badgesFor(a)}</button>`).join("") : `<div class="empty">No alternatives with nutrition data at this meal.</div>`}
    <button class="btn block danger" id="remove-item" style="margin-top:8px">Remove from plan</button>`);
  sheet.querySelectorAll(".alt").forEach((b) => (b.onclick = async () => {
    const a = alts[+b.dataset.i];
    closeSheet();
    try {
      state.day[which] = await api(`/api/day/${iso}/items/${id}/swap`, { method: "POST", body: { rec: a.rec, qty: a.qty, dislike: document.getElementById("dislike")?.checked || false } });
      toast(`Swapped in ${a.name}`); renderDay(which);
    } catch (e) { toast(e.message); }
  }));
  document.getElementById("remove-item").onclick = async () => {
    closeSheet();
    try { state.day[which] = await api(`/api/day/${iso}/items/${id}`, { method: "DELETE" }); renderDay(which); } catch (e) { toast(e.message); }
  };
}

// ---------------------------------------------------------------- item detail (label)
async function openDetail(day, which, id) {
  const iso = state.meta[which];
  const it = day.plan.items.find((i) => i.id === id);
  const per = it.per;
  openSheet(`<h2>${esc(it.name)}</h2><div class="faint small">${esc(it.category || "")} · ${esc(it.meal)} · serving ${esc(it.portion)}</div>
    <table class="simple" style="margin-top:12px"><tr><th></th><th>Per serving</th><th>Your portion (${it.qty}×)</th></tr>
    ${["kcal", "protein", "carbs", "fat"].map((k) => `<tr><td>${k === "kcal" ? "Calories" : k[0].toUpperCase() + k.slice(1) + " (g)"}</td><td>${per[k] == null ? "n/a" : r1(per[k])}</td><td>${per[k] == null ? "n/a" : r1(per[k] * it.qty)}</td></tr>`).join("")}</table>
    <div class="small faint" style="margin-top:6px">Source: UCSC FoodPro ${it.source === "foodpro_label" ? "nutrition label" : "nutrition report"}${it.has_data === false ? ". No usable data, so it isn't counted" : ""}.</div>
    <div id="label-extra" class="small muted" style="margin-top:12px"><div class="spinner"></div></div>
    <div class="row" style="margin-top:14px"><button class="btn block" id="like">👍 Like</button><button class="btn block" id="dislike-btn">👎 Dislike</button></div>`);
  const fb = async (rating) => {
    try { await api("/api/feedback", { method: "POST", body: { name: it.name, rating } }); toast(rating > 0 ? "I'll suggest it more often" : "Won't suggest it again"); closeSheet(); }
    catch (e) { toast(e.message); }
  };
  document.getElementById("like").onclick = () => fb(1);
  document.getElementById("dislike-btn").onclick = () => fb(-1);
  try {
    const label = await api(`/api/label/${iso}/${day.plan.loc}/${encodeURIComponent(it.rec)}`);
    const el = document.getElementById("label-extra");
    if (el) el.innerHTML = `${label.allergens ? `<p><b>Allergens:</b> ${esc(label.allergens)}</p>` : ""}
      ${label.fiber != null ? `<p>Fiber ${label.fiber} g · Sugar ${label.sugar ?? "n/a"} g · Sodium ${label.sodium_mg ?? "n/a"} mg (per serving)</p>` : ""}
      ${label.ingredients ? `<p><b>Ingredients:</b> ${esc(label.ingredients)}</p>` : ""}`;
  } catch (e) {
    const el = document.getElementById("label-extra"); if (el) el.textContent = "Label unavailable right now.";
  }
}

// ---------------------------------------------------------------- menu picker (add / log)
async function openMenuPicker(day, which, { slot, meal, mode }) {
  const iso = state.meta[which];
  const loc = day.plan?.loc || state.settings.default_hall;
  openSheet(`<h2>${mode === "add" ? "Add to " + (SLOT_LABEL[slot] || slot) : "Log from dining menu"}</h2><div class="spinner" style="margin-top:20px"></div>`);
  let menu;
  try { menu = await api(`/api/menu/${iso}/${loc}`); } catch (e) { toast(e.message); return closeSheet(); }
  const mealNames = Object.keys(menu.meals);
  let current = meal && menu.meals[meal] ? meal : mealNames[0];
  const draw = (q = "") => {
    const items = (menu.meals[current] || []).filter((i) => i.name.toLowerCase().includes(q.toLowerCase()));
    sheet.querySelector("#mp-list").innerHTML = items.slice(0, 80).map((i) => `<button class="alt" data-rec="${esc(i.rec)}" ${i.kcal == null ? "disabled" : ""}>
      <b>${esc(i.name)}</b><span class="small muted">${esc(i.category)} · ${esc(i.portion)} · ${i.kcal == null ? "no nutrition data" : `${r0(i.kcal)} kcal · ${r1(i.protein)} g P`}</span></button>`).join("") || `<div class="empty">Nothing matches.</div>`;
    sheet.querySelectorAll("#mp-list .alt").forEach((b) => (b.onclick = async () => {
      closeSheet();
      try {
        state.day[which] = mode === "add"
          ? await api(`/api/day/${iso}/items`, { method: "POST", body: { slot, meal: current, rec: b.dataset.rec, qty: 1 } })
          : await api(`/api/day/${iso}/log-menu`, { method: "POST", body: { loc, meal: current, rec: b.dataset.rec, qty: 1 } });
        toast("Added"); renderDay(which);
      } catch (e) { toast(e.message); }
    }));
  };
  openSheet(`<h2>${mode === "add" ? "Add to " + (SLOT_LABEL[slot] || slot) : "Log from dining menu"}</h2>
    <div class="row" style="margin:8px 0">${mode === "log" || slot === "snack" ? `<select id="mp-meal">${mealNames.map((m) => `<option ${m === current ? "selected" : ""}>${esc(m)}</option>`).join("")}</select>` : ""}
    <input type="text" id="mp-q" placeholder="Search ${esc(current)}…" style="flex:1;min-width:0"></div><div id="mp-list"></div>`);
  draw();
  sheet.querySelector("#mp-q").oninput = (e) => draw(e.target.value);
  const ms = sheet.querySelector("#mp-meal");
  if (ms) ms.onchange = (e) => { current = e.target.value; draw(sheet.querySelector("#mp-q").value); };
}

// ---------------------------------------------------------------- custom log
async function openCustomLog(which) {
  const iso = state.meta[which];
  let saved = [];
  try { saved = await api("/api/custom-foods"); } catch (_) {}
  openSheet(`<h2>Log food</h2><div class="faint small">For food outside the dining hall. Use the label on the package; these numbers are marked as entered by you.</div>
    ${saved.length ? `<div class="field" style="margin-top:10px">Saved foods<select id="cf-saved"><option value="">Choose…</option>${saved.map((f, i) => `<option value="${i}">${esc(f.name)}</option>`).join("")}</select></div>` : ""}
    <form id="cf" class="form-grid" style="margin-top:10px">
      <label class="full">Name<input type="text" name="name" required maxlength="120"></label>
      <label>Calories<input type="number" name="kcal" inputmode="decimal" step="any" min="0" required></label>
      <label>Protein (g)<input type="number" name="protein" inputmode="decimal" step="any" min="0" required></label>
      <label>Carbs (g)<input type="number" name="carbs" inputmode="decimal" step="any" min="0" value="0"></label>
      <label>Fat (g)<input type="number" name="fat" inputmode="decimal" step="any" min="0" value="0"></label>
      <label>Servings<input type="number" name="qty" inputmode="decimal" step="any" min="0.25" value="1"></label>
      <label>Serving size<input type="text" name="serving" placeholder="e.g. 1 bar"></label>
      <label class="toggle full"><input type="checkbox" name="save_food"> Save for next time</label>
      <button class="btn primary full" type="submit">Log it</button></form>`);
  const form = document.getElementById("cf");
  const sel = document.getElementById("cf-saved");
  if (sel) sel.onchange = () => {
    const f = saved[+sel.value]; if (!f) return;
    for (const k of ["name", "kcal", "protein", "carbs", "fat", "serving"]) form.elements[k].value = f[k] ?? "";
  };
  form.onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const body = { name: fd.get("name"), kcal: +fd.get("kcal"), protein: +fd.get("protein"), carbs: +fd.get("carbs") || 0,
      fat: +fd.get("fat") || 0, qty: +fd.get("qty") || 1, serving: fd.get("serving") || null, save_food: !!fd.get("save_food") };
    try { state.day[which] = await api(`/api/day/${iso}/log`, { method: "POST", body }); closeSheet(); toast("Logged"); renderDay(which); }
    catch (err) { toast(err.message); }
  };
}

// ---------------------------------------------------------------- progress
async function renderProgress() {
  loading("Loading progress…");
  try { state.progress = await api("/api/progress"); } catch (e) { view.innerHTML = `<div class="card empty">${esc(e.message)}</div>`; return; }
  if (state.tab !== "progress") return;
  const p = state.progress;
  const today = state.meta.today;
  const adj = p.pending_adjustment;
  view.innerHTML = `
    <div class="card"><h2>Morning weigh-in</h2>
      <form id="wf" class="row" style="margin-top:10px"><input type="date" name="date" value="${today}" style="flex:1;min-width:0">
      <input type="number" name="weight" inputmode="decimal" step="0.1" min="60" max="500" placeholder="lb" required style="width:96px">
      <button class="btn primary" type="submit">Save</button></form>
      <div class="faint small" style="margin-top:6px">Weigh in after waking up, before eating. Daily numbers bounce around; the 7-day average is what counts.</div></div>
    ${adj ? `<div class="card suggest"><h2>Suggested adjustment</h2><p class="muted">${esc(adj.reason)}</p>
      <p><b>${adj.current_kcal} → ${adj.proposed_kcal} kcal/day</b></p>
      <div class="row"><button class="btn primary" data-adj="1">Approve</button><button class="btn" data-adj="0">Not now</button></div></div>` : ""}
    <div class="card"><h2>Progress to ${p.goal_weight} lb</h2>
      <div class="tiles" style="margin-top:12px">
        <div class="tile"><div class="v">${p.current_avg ?? "n/a"}</div><div class="k">7-day average (lb)</div></div>
        <div class="tile"><div class="v">${p.to_go ?? "n/a"}</div><div class="k">lb to go</div></div>
        <div class="tile"><div class="v">${p.rate_lb_per_week ?? "n/a"}</div><div class="k">lb/week (3-wk trend)</div></div>
        <div class="tile"><div class="v">${p.eta_weeks ?? "n/a"}</div><div class="k">weeks to goal at this rate</div></div>
      </div>
      ${p.progress_pct != null ? `<div class="progress-line" role="img" aria-label="${p.progress_pct}% of the way from ${p.start_weight} to ${p.goal_weight} lb"><div style="width:${Math.max(0, Math.min(100, p.progress_pct))}%"></div></div>
        <div class="faint small" style="margin-top:4px">${p.progress_pct}% of the way from ${p.start_weight} lb</div>` : ""}
      <p class="small muted">${esc(p.status || "")}</p>
      ${p.adherence.logged_days ? `<p class="small faint">Last 14 days: ${p.adherence.logged_days} days logged, avg ${p.adherence.avg_kcal} kcal and ${p.adherence.avg_protein} g protein.</p>` : ""}
    </div>
    <div class="card"><h2>Weight trend</h2><div class="faint small">Dots are daily weigh-ins; the line is the 7-day average.</div>
      <div class="chart-wrap" id="chart"></div></div>
    <div class="card"><h2>Weekly averages</h2>${p.weekly.length ? `<table class="simple"><tr><th>Week of</th><th>Avg (lb)</th><th>Change</th><th>Weigh-ins</th></tr>
      ${p.weekly.slice().reverse().map((w) => `<tr><td>${fmtDate(w.week_of, { month: "short", day: "numeric" })}</td><td>${w.avg}</td><td>${w.change == null ? "n/a" : (w.change > 0 ? "+" : "") + w.change}</td><td>${w.n}</td></tr>`).join("")}</table>` : `<div class="faint small">No weigh-ins yet.</div>`}</div>
    <div class="card"><h2>Recent weigh-ins</h2>${p.series.length ? `<table class="simple"><tr><th>Date</th><th>Weight</th><th>7-day avg</th><th></th></tr>
      ${p.series.slice(-14).reverse().map((s) => `<tr><td>${fmtDate(s.date, { month: "short", day: "numeric" })}</td><td>${s.weight}</td><td>${s.avg7}</td><td><button class="chip-btn" data-delw="${s.date}" aria-label="Delete">✕</button></td></tr>`).join("")}</table>` : `<div class="faint small">Nothing yet.</div>`}</div>`;
  drawChart(document.getElementById("chart"), p);
  document.getElementById("wf").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try { await api("/api/weights", { method: "POST", body: { date: fd.get("date"), weight: +fd.get("weight") } }); toast("Saved"); renderProgress(); }
    catch (err) { toast(err.message); }
  };
  view.querySelectorAll("[data-delw]").forEach((b) => (b.onclick = async () => { await api(`/api/weights/${b.dataset.delw}`, { method: "DELETE" }); renderProgress(); }));
  view.querySelectorAll("[data-adj]").forEach((b) => (b.onclick = async () => {
    try { await api(`/api/adjustments/${adj.id}`, { method: "POST", body: { approve: b.dataset.adj === "1" } });
      toast(b.dataset.adj === "1" ? "Calorie target updated; new plans will use it" : "Dismissed");
      state.settings = await api("/api/settings"); state.day = {}; renderProgress(); }
    catch (e) { toast(e.message); }
  }));
}

function drawChart(el, p) {
  const s = p.series;
  if (s.length < 2) { el.innerHTML = `<div class="faint small" style="padding:20px 0">Add a few weigh-ins to see your trend.</div>`; return; }
  const W = 600, H = 240, m = { t: 12, r: 12, b: 26, l: 36 };
  const xs = s.map((d) => new Date(d.date + "T12:00:00").getTime());
  const vals = s.flatMap((d) => [d.weight, d.avg7]).concat([p.goal_weight]);
  let lo = Math.floor(Math.min(...vals) - 1), hi = Math.ceil(Math.max(...vals) + 1);
  const x = (t) => m.l + ((t - xs[0]) / Math.max(1, xs[xs.length - 1] - xs[0])) * (W - m.l - m.r);
  const y = (v) => m.t + (1 - (v - lo) / (hi - lo)) * (H - m.t - m.b);
  const ticks = []; const step = Math.max(1, Math.round((hi - lo) / 4));
  for (let v = Math.ceil(lo); v <= hi; v += step) ticks.push(v);
  const line = s.map((d, i) => `${i ? "L" : "M"}${x(xs[i]).toFixed(1)},${y(d.avg7).toFixed(1)}`).join("");
  const fmt = (t) => new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Weight trend: 7-day average from ${s[0].avg7} to ${s[s.length - 1].avg7} lb; goal ${p.goal_weight} lb">
    ${ticks.map((v) => `<line x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}" stroke="var(--border)" stroke-width="1"/>
      <text x="${m.l - 6}" y="${y(v) + 4}" text-anchor="end" font-size="11" fill="var(--text-3)">${v}</text>`).join("")}
    <line x1="${m.l}" x2="${W - m.r}" y1="${y(p.goal_weight)}" y2="${y(p.goal_weight)}" stroke="var(--text-2)" stroke-width="1.5" stroke-dasharray="5 4"/>
    <text x="${W - m.r}" y="${y(p.goal_weight) - 6}" text-anchor="end" font-size="11" fill="var(--text-2)">Goal ${p.goal_weight}</text>
    ${s.map((d, i) => `<circle cx="${x(xs[i])}" cy="${y(d.weight)}" r="4" fill="var(--text-3)" opacity=".55"/>`).join("")}
    <path d="${line}" fill="none" stroke="var(--accent)" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>
    <text x="${m.l}" y="${H - 6}" font-size="11" fill="var(--text-3)">${fmt(xs[0])}</text>
    <text x="${W - m.r}" y="${H - 6}" text-anchor="end" font-size="11" fill="var(--text-3)">${fmt(xs[xs.length - 1])}</text>
    <line id="xh" y1="${m.t}" y2="${H - m.b}" stroke="var(--text-3)" stroke-width="1" visibility="hidden"/>
    <circle id="xd" r="5" fill="var(--accent)" stroke="var(--surface)" stroke-width="2" visibility="hidden"/>
    <rect x="${m.l}" y="0" width="${W - m.l - m.r}" height="${H}" fill="transparent" id="hit"/></svg><div class="chart-tip" hidden></div>`;
  const svg = el.querySelector("svg"), tip = el.querySelector(".chart-tip"), xh = el.querySelector("#xh"), xd = el.querySelector("#xd");
  const move = (clientX) => {
    const r = svg.getBoundingClientRect();
    const sx = ((clientX - r.left) / r.width) * W;
    let best = 0; xs.forEach((t, i) => { if (Math.abs(x(t) - sx) < Math.abs(x(xs[best]) - sx)) best = i; });
    const d = s[best], px = x(xs[best]);
    xh.setAttribute("x1", px); xh.setAttribute("x2", px); xh.setAttribute("visibility", "visible");
    xd.setAttribute("cx", px); xd.setAttribute("cy", y(d.avg7)); xd.setAttribute("visibility", "visible");
    tip.hidden = false; tip.style.left = (px / W) * r.width + "px"; tip.style.top = (y(d.avg7) / H) * r.height + "px";
    tip.textContent = `${fmt(xs[best])}: ${d.weight} lb · avg ${d.avg7}`;
  };
  const hide = () => { tip.hidden = true; xh.setAttribute("visibility", "hidden"); xd.setAttribute("visibility", "hidden"); };
  svg.addEventListener("pointermove", (e) => move(e.clientX));
  svg.addEventListener("pointerdown", (e) => move(e.clientX));
  svg.addEventListener("pointerleave", hide);
}

// ---------------------------------------------------------------- settings
async function renderSettings() {
  loading("Loading settings…");
  const [s, fb, meta] = await Promise.all([api("/api/settings"), api("/api/feedback"), api("/api/meta")]);
  state.settings = s; state.meta = meta;
  if (state.tab !== "settings") return;
  const rulesText = (s.avoid_rules || []).map((r) => r.join(" ")).join(", ");
  view.innerHTML = `
    <form id="sf">
    <div class="card"><h2>Daily targets</h2><div class="faint small">Starting estimates. The Progress tab suggests changes from your weight trend; nothing changes without your OK.</div>
      <div class="form-grid" style="margin-top:12px">
        <label>Calories<input type="number" name="calories" value="${s.calories}" inputmode="numeric"></label>
        <label>Protein (g)<input type="number" name="protein_g" value="${s.protein_g}" inputmode="numeric"></label>
        <label>Fat (g)<input type="number" name="fat_g" value="${s.fat_g}" inputmode="numeric"></label>
        <label>Training-day extra kcal<input type="number" name="training_day_extra_kcal" value="${s.training_day_extra_kcal}" inputmode="numeric"></label>
        <label>Start weight (lb)<input type="number" step="0.1" name="start_weight_lb" value="${s.start_weight_lb}" inputmode="decimal"></label>
        <label>Goal weight (lb)<input type="number" step="0.1" name="goal_weight_lb" value="${s.goal_weight_lb}" inputmode="decimal"></label>
      </div>
      <div class="faint small" style="margin-top:8px">Carbs fill the remaining calories: ${Math.round((s.calories - s.protein_g * 4 - s.fat_g * 9) / 4)} g on rest days.</div></div>
    <div class="card"><h2>Training days</h2><div class="faint small">These days get training-day targets automatically. You can also flip any single day on its plan.</div>
      <div class="days" style="margin-top:10px">${WEEKDAYS.map((d, i) => `<button type="button" data-wd="${i}" class="${(s.training_weekdays || []).includes(i) ? "on" : ""}">${d}</button>`).join("")}</div></div>
    <div class="card"><h2>Dining & food</h2>
      <div class="field" style="margin-top:8px">Default dining hall (used for the 8 PM plan)<select name="default_hall">${Object.entries(meta.halls).map(([k, v]) => `<option value="${k}" ${k === s.default_hall ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></div>
      <div class="field" style="margin-top:10px">Never suggest (comma-separated; all words must match)<input type="text" name="avoid" value="${esc(rulesText)}" placeholder="scrambled egg, mushroom"></div>
      <label class="toggle"><input type="checkbox" name="use_breakfast_template" ${s.use_breakfast_template ? "checked" : ""}> Use my usual breakfast when it's available</label>
      <label class="toggle"><input type="checkbox" name="include_snack" ${s.include_snack ? "checked" : ""}> Add a snack to close protein gaps</label>
    </div>
    <button class="btn primary block" type="submit">Save settings</button></form>
    <div class="card"><h2>Food preferences</h2><div class="faint small">Learned from your 👍/👎, swaps and what you mark as eaten.</div>
      ${fb.length ? fb.map((f) => `<div class="row" style="padding:8px 0;border-top:1px solid var(--border)"><span>${f.rating > 0 ? "👍" : "👎"} ${esc(f.name)}</span><span class="spacer"></span><button class="chip-btn" data-clear="${esc(f.name)}">Reset</button></div>`).join("") : `<div class="faint small" style="margin-top:8px">No likes or dislikes yet.</div>`}</div>
    <div class="card"><h2>Automation</h2>
      ${meta.jobs.length ? meta.jobs.map((j) => `<div class="small">${esc(j.id)}: next ${j.next ? new Date(j.next).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" }) : "n/a"}</div>`).join("") : `<div class="small faint">Scheduler is off.</div>`}
      ${meta.recent_jobs.length ? `<div class="small faint" style="margin-top:8px">${meta.recent_jobs.map((j) => `${new Date(j.at).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}: ${esc(j.message)}`).join("<br>")}</div>` : ""}
      <div class="small faint" style="margin-top:8px">Menus & nutrition: UCSC Dining FoodPro (nutrition.sa.ucsc.edu). ${meta.llm_enabled ? "Plan notes written by Claude from those numbers." : "Claude notes are off (no API key)."}</div></div>`;
  const wd = new Set(s.training_weekdays || []);
  view.querySelectorAll("[data-wd]").forEach((b) => (b.onclick = () => {
    const i = +b.dataset.wd; wd.has(i) ? wd.delete(i) : wd.add(i); b.classList.toggle("on");
  }));
  view.querySelectorAll("[data-clear]").forEach((b) => (b.onclick = async () => {
    await api("/api/feedback", { method: "POST", body: { name: b.dataset.clear, rating: 0 } }); renderSettings();
  }));
  document.getElementById("sf").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target.elements;
    const body = {
      calories: +f.calories.value, protein_g: +f.protein_g.value, fat_g: +f.fat_g.value,
      training_day_extra_kcal: +f.training_day_extra_kcal.value, start_weight_lb: +f.start_weight_lb.value,
      goal_weight_lb: +f.goal_weight_lb.value, training_weekdays: [...wd], default_hall: f.default_hall.value,
      avoid_rules: f.avoid.value.split(",").map((r) => r.trim().split(/\s+/).filter(Boolean)).filter((r) => r.length),
      use_breakfast_template: f.use_breakfast_template.checked, include_snack: f.include_snack.checked,
    };
    try { state.settings = await api("/api/settings", { method: "PUT", body }); state.day = {}; toast("Saved. Rebuild a plan (⟳) to apply it today"); }
    catch (err) { toast(err.message); }
  };
}

// ---------------------------------------------------------------- login + boot
function renderLogin() {
  document.getElementById("tabbar").hidden = true;
  view.innerHTML = `<div class="card login"><h2>Slug Macros</h2><p class="muted small">Enter your passcode.</p>
    <form id="lf" class="row"><input type="password" name="p" autocomplete="current-password" style="flex:1;min-width:0" required><button class="btn primary">Go</button></form></div>`;
  document.getElementById("lf").onsubmit = async (e) => {
    e.preventDefault();
    const res = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ passcode: e.target.p.value }) });
    if (res.ok) { document.getElementById("tabbar").hidden = false; boot(); } else toast("Wrong passcode");
  };
}

async function boot() {
  const sess = await fetch("/api/session").then((r) => r.json());
  if (!sess.logged_in) return renderLogin();
  [state.meta, state.settings] = await Promise.all([api("/api/meta"), api("/api/settings")]);
  let tab = "today";
  try { tab = localStorage.getItem("tab") || "today"; } catch (_) {}
  setTab(tab);
}
boot().catch((e) => { view.innerHTML = `<div class="card empty">${esc(e.message)}</div>`; });
