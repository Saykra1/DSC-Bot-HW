"use strict";
// Мини апп «Домашки DSC». Без сборки и зависимостей: экраны — функции, которые возвращают HTML-строку.
// Быстрые переходы: ответы API кешируются, экран сначала рисуется из кеша, затем тихо обновляется.

const tg = window.Telegram?.WebApp;
const initData = tg?.initData || "";
let ME = null;
let TZ = "Europe/Moscow";

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const num = (v) => (v === null || v === undefined ? "—" : Number(Number(v).toPrecision(5)).toString());
const initial = (name) => (String(name || "?").trim()[0] || "?").toUpperCase();
const plural = (n, one, few, many) => {
  const a = Math.abs(n) % 100, b = a % 10;
  return a > 10 && a < 20 ? many : b > 1 && b < 5 ? few : b === 1 ? one : many;
};

// ---------- иконки ----------
const I = {
  tasks: "M4 4h16v16H4zM4 9h16M9 9v11",
  trophy: "M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0V4zM7 6H4v1a3 3 0 0 0 3 3M17 6h3v1a3 3 0 0 1-3 3",
  history: "M12 8v4l3 3M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5",
  user: "M20 21a8 8 0 0 0-16 0M12 13a5 5 0 1 0 0-10 5 5 0 0 0 0 10z",
  review: "M9 11l3 3 8-8M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9",
  users: "M16 21v-2a4 4 0 0 0-8 0v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM22 21v-2a4 4 0 0 0-3-3.9M2 21v-2a4 4 0 0 1 3-3.9",
  sliders: "M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6",
  back: "M15 18l-6-6 6-6",
  chev: "M9 18l6-6-6-6",
  check: "M20 6L9 17l-5-5",
  clock: "M12 7v5l3 2M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z",
  file: "M14 3H6v18h12V7zM14 3v4h4",
  doc: "M14 3H6v18h12V7zM14 3v4h4M9 13h6M9 17h4",
  lock: "M7 11V7a5 5 0 0 1 10 0v4M5 11h14v10H5z",
  upload: "M12 16V4M6 10l6-6 6 6M4 20h16",
  download: "M12 4v12M6 10l6 6 6-6M4 20h16",
  send: "M22 2L11 13M22 2l-7 20-4-9-9-4z",
  code: "M16 18l6-6-6-6M8 6l-6 6 6 6",
  search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM21 21l-4.3-4.3",
  plus: "M12 5v14M5 12h14",
  refresh: "M21 12a9 9 0 1 1-3-6.7L21 8M21 3v5h-5",
  alert: "M12 8v5M12 16.5h.01M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z",
  warn: "M12 9v4M12 17h.01M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z",
  bulb: "M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7V17h8v-2.3A7 7 0 0 0 12 2z",
  moon: "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z",
  sun: "M12 4V2M12 22v-2M4 12H2M22 12h-2M5.6 5.6L4.2 4.2M19.8 19.8l-1.4-1.4M5.6 18.4l-1.4 1.4M19.8 4.2l-1.4 1.4M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10z",
  plane: "M21 4L3 11l6 2 2 6 3-4 5 4z",
  close: "M18 6L6 18M6 6l12 12",
  wifiOff: "M2 2l20 20M8.5 16.4a5 5 0 0 1 7 0M5 12.8a10 10 0 0 1 5.2-2.7M19 12.8a10 10 0 0 0-2-1.4M12 20h.01",
  edit: "M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z",
  eye: "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
  table: "M3 3h18v18H3zM3 9h18M3 15h18M9 3v18",
};
const ic = (name, size = 20, sw = 1.8) =>
  `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${I[name]}"/></svg>`;

// ---------- Telegram: тактильный отклик, тема ----------
function hap(kind = "light") {
  const h = tg?.HapticFeedback;
  if (!h) return;
  try {
    if (kind === "select") h.selectionChanged();
    else if (kind === "success" || kind === "error" || kind === "warning") h.notificationOccurred(kind);
    else h.impactOccurred(kind);
  } catch {}
}

const THEME_KEY = "dsc-theme";
function themePref() {
  try { return localStorage.getItem(THEME_KEY) || "dark"; } catch { return "dark"; }
}
function applyTheme(pref = themePref()) {
  const eff = pref === "tg" ? (tg?.colorScheme === "light" ? "light" : "dark") : pref;
  document.documentElement.dataset.theme = eff;
  const bg = eff === "dark" ? "#0F0F10" : "#F3F2EF";
  $('meta[name="theme-color"]')?.setAttribute("content", bg);
  try { tg?.setHeaderColor?.(bg); tg?.setBackgroundColor?.(bg); tg?.setBottomBarColor?.(bg); } catch {}
}
function setTheme(pref) {
  try { localStorage.setItem(THEME_KEY, pref); } catch {}
  applyTheme(pref);
  hap("select");
  $$("[data-theme-opt]").forEach((b) => b.classList.toggle("on", b.dataset.themeOpt === pref));
}

// ---------- даты (всё по Москве) ----------
const dtf = (opts) => new Intl.DateTimeFormat("ru-RU", { timeZone: TZ, ...opts });
const dayKey = (d) => dtf({ year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
const fmt = (iso) => (iso ? dtf({ dateStyle: "short", timeStyle: "short" }).format(new Date(iso)) : "—");
const dm = (iso) => (iso ? dtf({ day: "2-digit", month: "2-digit" }).format(new Date(iso)) : "—");
const hm = (iso) => dtf({ hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(iso));
function when(iso) {
  if (!iso) return "—";
  const d = new Date(iso), now = new Date();
  if (dayKey(d) === dayKey(now)) return `сегодня, ${hm(iso)}`;
  if (dayKey(d) === dayKey(new Date(now - 864e5))) return `вчера, ${hm(iso)}`;
  return `${dm(iso)}, ${hm(iso)}`;
}
const msk = (iso) => (iso ? `${dm(iso)}, ${hm(iso)} МСК` : "—");
function left(iso) {
  let m = Math.max(0, Math.round((new Date(iso) - Date.now()) / 60000));
  const d = Math.floor(m / 1440); m -= d * 1440;
  const h = Math.floor(m / 60); m -= h * 60;
  if (d) return `${d} д ${h} ч`;
  if (h) return `${h} ч ${m} мин`;
  return `${m} мин`;
}
function toInput(iso) {
  if (!iso) return "";
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit",
    day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(new Date(iso)).map((x) => [x.type, x.value]));
  return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`;
}

// ---------- запросы и кеш ----------
const CACHE = new Map();
async function api(path, opts = {}) {
  const method = (opts.method || "GET").toUpperCase();
  const tries = method === "GET" ? 3 : 1; // туннель иногда моргает — GET повторяем
  let lastErr;
  for (let i = 0; i < tries; i++) {
    let res;
    try {
      res = await fetch("/api" + path, { ...opts, headers: { ...(opts.headers || {}), "X-Telegram-Init-Data": initData } });
    } catch {
      lastErr = new Error("Нет связи с сервером, попробуй ещё раз");
      if (i < tries - 1) await sleep(500 * (i + 1));
      continue;
    }
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch {}
      const e = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      e.status = res.status;
      throw e;
    }
    setOffline(false);
    const ct = res.headers.get("content-type") || "";
    const data = ct.includes("application/json") ? await res.json() : await res.blob();
    if (method === "GET" && !(data instanceof Blob)) CACHE.set(path, data);
    if (method !== "GET") CACHE.clear(); // что-то поменяли — старые ответы больше не показываем
    return data;
  }
  if (method === "GET" && CACHE.has(path)) { setOffline(true); return CACHE.get(path); }
  throw lastErr;
}
const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

// Загрузчик для экрана: в режиме "cache" отдаёт сохранённый ответ сразу
function loader(mode) {
  const st = { used: false };
  const get = async (path) => {
    if (mode === "cache" && CACHE.has(path)) { st.used = true; return CACHE.get(path); }
    return api(path);
  };
  get.state = st;
  return get;
}

function setOffline(on) { $("#offline")?.classList.toggle("on", on); }

let toastTimer;
function toast(text, kind = "ok") {
  const t = $("#toast");
  t.className = "toast on" + (kind === "err" ? " err" : "");
  t.innerHTML = `<span class="ic">${ic(kind === "err" ? "close" : "check", 16, 2.4)}</span><span class="grow">${esc(text)}</span>`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("on"), kind === "err" ? 6000 : 3500);
  hap(kind === "err" ? "error" : "success");
}

async function download(path, name) {
  try {
    const blob = await api(path);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  } catch (e) { toast(e.message, "err"); }
}
async function sendFile(path, okText) {
  try { await api(path, { method: "POST" }); toast(okText); } catch (e) { toast(e.message, "err"); }
}

// ---------- навигация ----------
let stack = [];
let seq = 0;
let timers = [];
const isWide = () => matchMedia("(min-width: 1200px)").matches;

function later(fn, ms) {
  const my = seq;
  timers.push(setTimeout(() => { if (my === seq) fn(); }, ms));
}
function every(fn, ms) {
  const my = seq;
  const id = setInterval(() => (my === seq ? fn() : clearInterval(id)), ms);
  timers.push(id);
}
function clearTimers() { timers.forEach((t) => { clearTimeout(t); clearInterval(t); }); timers = []; }

function paint(html, anim) {
  const v = $("#view");
  v.innerHTML = html;
  v.classList.remove("enter", "push", "pop");
  if (anim) { void v.offsetWidth; v.classList.add("enter"); if (anim !== "fade") v.classList.add(anim); }
}
const dirty = () => {
  const a = document.activeElement;
  return (a && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)) || $$("#view [data-dirty]").length > 0;
};

async function show(route, { push = true, anim } = {}) {
  if (push) {
    if (stack.length) stack[stack.length - 1].scroll = scrollY;
    stack.push(route);
  }
  clearTimers();
  const my = ++seq;
  const depth = stack.length;
  $("#app").classList.toggle("deep", depth > 1);
  $("#view").classList.toggle("wide-split", !!route.wideSplit);
  if (tg?.BackButton) depth > 1 ? tg.BackButton.show() : tg.BackButton.hide();
  anim = anim ?? (push ? (depth > 1 ? "push" : "fade") : "pop");
  const skel = setTimeout(() => { if (my === seq) paint(skeleton(route.skel), null); }, 140);
  const get = loader(route.fresh ? "network" : "cache");
  try {
    const html = await route.render(get);
    clearTimeout(skel);
    if (my !== seq) return;
    paint(html, anim);
    window.scrollTo(0, push ? 0 : route.scroll || 0);
    route.after?.();
    if (get.state.used) revalidate(route, my, html);
  } catch (e) {
    clearTimeout(skel);
    if (my !== seq) return;
    paint(errorState(e, depth > 1), anim);
  }
}
async function revalidate(route, my, old) {
  try {
    const html = await route.render(loader("network"));
    if (my !== seq || html === old || dirty()) return;
    const y = scrollY;
    paint(html, null);
    window.scrollTo(0, y);
    route.after?.();
  } catch {}
}
function goBack() {
  if (stack.length > 1) { stack.pop(); hap("light"); show(stack[stack.length - 1], { push: false }); }
}
function reload() { show(stack[stack.length - 1], { push: false, anim: null }); }
function replace(route) { stack.pop(); show(route); }
tg?.BackButton?.onClick(goBack);

// ---------- общие куски разметки ----------
function topbar(title, sub = "", right = "") {
  return `<div class="topbar">
    <button class="icon-btn press" onclick="goBack()" aria-label="Назад">${ic("back")}</button>
    <div class="t grow"><span class="h3 ellipsis">${title}</span>${sub ? `<span class="small muted ellipsis">${sub}</span>` : ""}</div>${right}</div>`;
}
function skeleton(kind = "list") {
  const row = (w) => `<div class="capsule"><span class="skel" style="width:44px;height:44px"></span>
    <span class="grow stack" style="gap:6px"><span class="skel" style="width:${w}%;height:12px"></span><span class="skel" style="width:40%;height:10px"></span></span></div>`;
  return `<div class="stack" style="gap:16px">
    <span class="skel" style="width:180px;height:30px"></span>
    <div class="row"><span class="skel" style="width:84px;height:40px"></span><span class="skel" style="width:110px;height:40px"></span><span class="skel" style="width:96px;height:40px"></span></div>
    <div class="stack">${[70, 55, 62, 48].map(row).join("")}</div></div>`;
}
function emptyState(icon, title, text, action = "") {
  return `<div class="state"><span class="ill">${ic(icon, 40, 1.6)}</span><span class="h2">${title}</span><p>${text}</p>${action}</div>`;
}
function errorState(e, withBack) {
  const offline = /связи/i.test(e.message);
  return `${withBack ? topbar("") : ""}<div class="state err">
    <span class="ill">${ic(offline ? "wifiOff" : "alert", 40, 1.6)}</span>
    <span class="h2">${offline ? "Нет связи" : "Не удалось загрузить"}</span>
    <p>${esc(e.message)}</p>
    <button class="btn dark" onclick="hap();reload()">${ic("refresh", 18, 2)}Повторить</button></div>`;
}
const scoreKind = (score, max) => {
  if (score === null || score === undefined || !max) return "neutral";
  const r = score / max;
  return r >= 0.8 ? "good" : r >= 0.5 ? "warn" : "bad";
};
const verdictLabel = (score, max) => {
  const r = score / max;
  return r >= 0.8 ? "Отлично" : r >= 0.6 ? "Хорошо" : r >= 0.4 ? "Можно лучше" : "Нужно доработать";
};
function codeBlock(src, label, badLines = []) {
  const head = `<div class="head"><span>${label}</span><span>Python</span></div>`;
  if (src === null || src === undefined || !String(src).trim()) return `<div class="code">${head}<div class="empty">(пусто)</div></div>`;
  const lines = String(src).replace(/\s+$/, "").split("\n");
  return `<div class="code">${head}<pre>${lines.map((l, i) => `<span class="ln${badLines.includes(i + 1) ? " bad" : ""}">${hl(l) || " "}</span>`).join("")}</pre></div>`;
}
const KW = new Set("False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield".split(" "));
function hl(line) {
  const re = /(#.*$)|("(?:\\.|[^"\\])*"?|'(?:\\.|[^'\\])*'?)|(\b\d+(?:\.\d+)?(?:e[+-]?\d+)?\b)|([A-Za-z_]\w*)(?=\s*\()|([A-Za-z_]\w*)/g;
  let out = "", last = 0, m;
  while ((m = re.exec(line))) {
    out += esc(line.slice(last, m.index));
    const t = esc(m[0]);
    if (m[1]) out += `<span class="t-com">${t}</span>`;
    else if (m[2]) out += `<span class="t-str">${t}</span>`;
    else if (m[3]) out += `<span class="t-num">${t}</span>`;
    else if (m[4]) out += `<span class="${KW.has(m[4]) ? "t-kw" : "t-fn"}">${t}</span>`;
    else out += KW.has(m[5]) ? `<span class="t-kw">${t}</span>` : t;
    last = re.lastIndex;
  }
  return out + esc(line.slice(last));
}
function pickFile(input, labelSel) {
  const f = input.files[0];
  const box = input.closest(".drop, .file-row");
  box?.classList.toggle("has", !!f);
  if (labelSel && f) $(labelSel).textContent = `${f.name} · ${(f.size / 1024).toFixed(f.size > 1048576 ? 0 : 1)} КБ`;
  if (f) { box?.setAttribute("data-dirty", ""); hap("select"); }
}

// ---------- статусы заданий для участника ----------
function assignmentState(a, subs) {
  const mine = subs.filter((s) => s.assignment_id === a.id);
  const pending = mine.some((s) => s.status === "queued" || s.status === "checking");
  const closed = !a.is_active || (a.deadline && new Date(a.deadline) < new Date());
  if (ME.is_admin) {
    if (!a.published) return { kind: "neutral", icon: "clock", status: `Запланировано на ${dm(a.publish_at)}`, meta: msk(a.publish_at), group: "active" };
    if (closed) return { kind: "bad", icon: "lock", status: "Закрыто", meta: a.deadline ? `дедлайн ${dm(a.deadline)}` : "приём закрыт", group: "done" };
    return { kind: "good", icon: "check", status: "Открыто", meta: a.deadline ? `до ${dm(a.deadline)}` : "без дедлайна", group: "active" };
  }
  // Части задания: тест и/или ноутбук. У старого сервера частей нет — значит, только ноутбук.
  const parts = [a.quiz && { ...a.quiz, kind: "quiz" }, (a.notebook || !a.parts) && { kind: "notebook", best_score: a.best_score,
    attempts_used: a.attempts_used, max_attempts: a.max_attempts, ...(a.notebook || {}) }].filter(Boolean);
  const what = parts.length > 1 ? "тест + ноутбук" : parts[0]?.kind === "quiz" ? "тест" : "";
  const due = a.deadline ? `до ${dm(a.deadline)}` : "без дедлайна";
  const meta = (...xs) => xs.filter(Boolean).join(" · ");
  const doneParts = parts.filter((p) => p.best_score !== null && p.best_score !== undefined);
  const exhausted = parts.every((p) => p.attempts_used >= p.max_attempts);
  if (pending) return { kind: "accent", icon: "clock", status: "Проверяется", meta: meta(what, a.deadline && `до ${dm(a.deadline)}`), group: "active" };
  if (a.best_score !== null && doneParts.length < parts.length && !closed) {
    return { kind: "warn", icon: "check", status: `Готово ${doneParts.length} из ${parts.length} частей`, meta: due, score: `${a.best_score}/${a.max_score}`, group: "active" };
  }
  if (a.best_score !== null) {
    const p = parts.length === 1 ? parts[0] : null;
    return { kind: "good", icon: "check", status: p?.kind === "quiz" ? "Пройдено" : "Проверено",
      meta: p ? `${p.attempts_used} из ${p.max_attempts} ${plural(p.max_attempts, "попытки", "попыток", "попыток")}` : what,
      score: `${a.best_score}/${a.max_score}`, group: closed || exhausted ? "done" : "active" };
  }
  if (closed) return { kind: "bad", icon: "lock", status: a.is_active ? "Просрочено" : "Закрыто", meta: a.reference_available ? (a.notebook ? "эталон открыт" : "ответы открыты") : "приём закрыт", score: "—", group: "done" };
  return { kind: "neutral", icon: parts.length === 1 && parts[0].kind === "quiz" ? "quiz" : "file", status: "Не начато", meta: meta(what, due), group: "active" };
}
function avgScore(list) {
  const done = list.filter((a) => a.best_score !== null && a.max_score);
  if (!done.length) return null;
  return (done.reduce((s, a) => s + (a.best_score / a.max_score) * 10, 0) / done.length).toFixed(1);
}

// =====================================================================
// УЧАСТНИК
// =====================================================================
let HOME_FILTER = "all";
function homeRoute() {
  return {
    async render(get) {
      const [list, subs, hacks] = await Promise.all([get("/assignments"), get("/submissions"), get("/hackathons")]);
      const items = list.map((a) => ({ a, st: assignmentState(a, subs) }));
      const shown = items.filter((x) => HOME_FILTER === "all" || x.st.group === HOME_FILTER);
      const avg = ME.is_admin ? null : avgScore(list);
      const doneCount = items.filter((x) => x.a.best_score !== null).length;
      const nActive = items.filter((x) => x.st.group === "active").length;
      const p = ME.profile;
      const head = `<div class="row between">
        <button class="row press" style="background:none;border:0;padding:0;text-align:left" onclick="openProfile()">
          <span class="avatar">${esc(initial(p.name))}</span>
          <span class="stack" style="gap:0"><span class="small muted">Месяц бота · DSC</span><span class="h3">${esc(p.name)}</span></span>
        </button>
        ${avg !== null ? `<span class="pill" style="pointer-events:none">ср. балл <b class="c-accent" style="margin-left:4px">${avg}</b></span>`
          : ME.is_admin ? `<span class="chip accent big">организатор</span>` : ""}</div>`;
      const warn = !ME.is_admin && !p.fio
        ? `<button class="card tight soft-warn press" style="border:0;text-align:left" onclick="openProfile()"><span class="row">${ic("warn", 18, 2)}
            <span class="grow">Укажи ФИО в профиле, чтобы организаторы засчитали домашки.</span>${ic("chev", 16)}</span></button>` : "";
      const progress = list.length ? `<div class="stack" style="gap:10px">
        <div class="row between" style="align-items:flex-end"><h1 class="h1">Задания</h1>
          ${ME.is_admin ? `<span class="small muted">${list.length} ${plural(list.length, "задание", "задания", "заданий")}</span>`
            : `<span class="small muted">сдано <b style="color:var(--text)">${doneCount}</b> из ${list.length}</span>`}</div>
        <div class="bar">${items.map((x) => `<i class="${x.st.kind === "neutral" ? "" : x.st.kind === "bad" && x.a.best_score === null ? "" : x.st.kind}"></i>`).join("")}</div></div>`
        : `<h1 class="h1">Задания</h1>`;
      const filters = list.length ? `<div class="pills" role="tablist">
        ${[["all", `Все · ${list.length}`], ["active", `Активные · ${nActive}`], ["done", "Завершённые"]].map(([k, l]) =>
          `<button class="pill${HOME_FILTER === k ? " on" : ""}" onclick="HOME_FILTER='${k}';hap('select');reload()">${l}</button>`).join("")}</div>` : "";
      const rows = shown.map(({ a, st }) => `
        <a class="capsule k-${st.kind}" href="#" onclick="event.preventDefault();hap();show(assignmentRoute(${a.id}))">
          <span class="dot">${ic(st.icon, 20, 2)}</span>
          <span class="grow stack" style="gap:0"><span class="h3 ellipsis">${esc(a.title)}</span>
            <span class="small muted ellipsis">${esc(st.status)}${st.meta ? " · " + esc(st.meta) : ""}</span></span>
          ${st.score ? `<span class="score ${st.kind === "good" ? "c-good" : "muted"}">${st.score}</span>` : `<span class="chev">${ic("chev", 16)}</span>`}
        </a>`).join("");
      const live = hacks.filter((h) => h.status === "running" && (h.is_active || ME.is_admin));
      const soon = hacks.filter((h) => h.status === "upcoming" && (h.is_active || ME.is_admin));
      const hcard = live[0] ? `<a class="card inverse press" style="text-decoration:none;border-radius:28px" href="#" onclick="event.preventDefault();openTab('hack')">
          <span class="row between"><span class="chip solid big">Хакатон идёт</span><span class="small" style="opacity:.75">до ${msk(live[0].ends_at)}</span></span>
          <span class="row between" style="align-items:flex-end"><span class="h2">${esc(live[0].title)}</span>
            ${live[0].my_best !== null ? `<span class="small">лучший <b class="mono" style="font-size:16px;color:var(--accent-on-inverse)">${num(live[0].my_best)}</b></span>` : `<span class="small" style="opacity:.75">ещё ${left(live[0].ends_at)}</span>`}</span></a>`
        : soon[0] ? `<a class="card inverse press" style="text-decoration:none;border-radius:28px" href="#" onclick="event.preventDefault();openTab('hack')">
          <span class="chip solid big" style="align-self:flex-start">Скоро хакатон</span>
          <span class="h2">${esc(soon[0].title)}</span><span class="small" style="opacity:.75">старт ${msk(soon[0].starts_at)}</span></a>` : "";
      const body = !list.length
        ? emptyState("tasks", "Заданий пока нет", ME.is_admin ? "Создай первое задание в разделе «Управление»." : "Когда организаторы опубликуют задание, бот напишет в чат.",
            ME.is_admin ? `<button class="btn primary" onclick="openTab('manage')">${ic("plus", 18, 2.2)}Новое задание</button>` : "")
        : shown.length ? `<div class="list-grid">${rows}</div>` : `<p class="muted" style="text-align:center;margin:24px 0">Здесь пусто</p>`;
      return head + warn + progress + filters + body + hcard;
    },
  };
}

// Экран задания. Если частей две (тест и ноутбук) — сначала выбор части, если одна — сразу она.
function assignmentRoute(id, part) {
  const route = {
    async render(get) {
      const [list, subs] = await Promise.all([get("/assignments"), get("/submissions")]);
      const a = list.find((x) => x.id === id);
      if (!a) throw new Error("Задание не найдено");
      const parts = partsOf(a);
      const single = parts.length === 1;
      const which = part || (single ? parts[0] : null);
      if (!which) return partsChooserHtml(a, subs);
      if (which === "quiz") return quizPartHtml(a, subs, get, single);
      const mine = subs.filter((s) => s.assignment_id === id && (s.kind || "notebook") === "notebook");
      const closed = !a.is_active || (a.deadline && new Date(a.deadline) < new Date());
      const left_ = a.max_attempts - a.attempts_used;
      const pending = mine.find((s) => s.status === "queued" || s.status === "checking");
      const chips = `<div class="row wrap" style="gap:6px">
        ${a.deadline ? `<span class="chip big ${closed ? "bad" : "accent"}">${ic("clock", 14, 2)}${closed ? "закрыто" : "до"} ${msk(a.deadline)}</span>` : `<span class="chip big">без дедлайна</span>`}
        <span class="chip big">${a.attempts_used} из ${a.max_attempts} ${plural(a.max_attempts, "попытки", "попыток", "попыток")}</span>
        <span class="chip big">${a.task_count} ${plural(a.task_count, "задание", "задания", "заданий")}${single ? "" : ` · ${a.task_count * 10} баллов`}</span>
        ${!a.published ? `<span class="chip big warn">появится ${msk(a.publish_at)}</span>` : ""}</div>`;
      const steps = pending ? progressCard(pending) : "";
      const upload = closed || left_ <= 0 ? "" : `
        <label class="drop" id="drop">
          <input type="file" accept=".ipynb" id="hw-file" onchange="pickFile(this,'#drop-name');$('#submit-btn').disabled=!this.files.length">
          <span class="dot">${ic("upload", 22, 2)}</span>
          <span class="grow stack" style="gap:2px"><span class="h3" id="drop-name">Выбери файл .ipynb</span>
            <span class="small muted">Перетащи сюда или нажми. Проверка займёт пару минут.</span></span>
        </label>`;
      const attempts = mine.length ? `<div class="stack"><span class="h3">Мои попытки</span>
        ${mine.map((s) => subRow(s)).join("")}</div>` : "";
      const ref = a.reference_available
        ? `<button class="card tight press" style="border:0;text-align:left;flex-direction:row;align-items:center;gap:12px" onclick="show(referenceRoute(${a.id}))">
            <span class="avatar plain" style="color:var(--good)">${ic("eye", 20, 2)}</span>
            <span class="grow stack" style="gap:0"><span class="h3">Эталонное решение</span><span class="small muted">Дедлайн прошёл — можно сравнить со своим кодом</span></span>${ic("chev", 16)}</button>`
        : a.deadline ? `<div class="card tight" style="flex-direction:row;align-items:center;gap:12px">
            <span class="avatar plain muted">${ic("lock", 20, 2)}</span>
            <span class="grow stack" style="gap:0"><span class="h3">Эталон закрыт</span><span class="small muted">Откроется после дедлайна, ${msk(a.deadline)}</span></span></div>` : "";
      const cta = closed || left_ <= 0
        ? (a.reference_available ? `<div class="cta"><button class="btn dark" onclick="show(referenceRoute(${a.id}))">${ic("eye", 18, 2)}Открыть эталон</button></div>`
          : left_ <= 0 && !closed ? `<div class="cta"><button class="btn" disabled>Попытки закончились</button></div>` : "")
        : `<div class="cta"><button class="btn primary" id="submit-btn" disabled onclick="submitWork(${a.id}, this)">${ic("upload", 18, 2)}Сдать работу · попытка ${a.attempts_used + 1} из ${a.max_attempts}</button></div>`;
      return `${single ? `${topbar("Задание")}<h1 class="h1">${esc(a.title)}</h1>` : `${topbar(esc(a.title))}<h1 class="h1">Ноутбук</h1>`}${chips}
        ${single && a.description ? `<div class="card tight pre" style="font-size:14px">${esc(a.description)}</div>` : ""}
        <div class="row wrap" style="gap:8px">
          ${ME.bot_enabled ? `<button class="btn sm press" onclick="sendFile('/assignments/${a.id}/template/send','Шаблон отправлен в чат с ботом')">${ic("send", 16, 2)}Шаблон в чат</button>` : ""}
          <button class="btn sm press" onclick="download('/assignments/${a.id}/template','template.ipynb')">${ic("download", 16, 2)}Скачать шаблон</button>
        </div>
        ${steps}${upload}${attempts}${ref}${cta}`;
    },
    after() {
      const d = $("#drop");
      if (!d) return;
      d.addEventListener("dragover", (e) => { e.preventDefault(); d.classList.add("over"); });
      d.addEventListener("dragleave", () => d.classList.remove("over"));
      d.addEventListener("drop", () => d.classList.remove("over"));
    },
  };
  return route;
}

function subRow(s, withTitle = false) {
  const st = s.status === "done" ? scoreKind(s.score, s.max_score) : s.status === "error" ? "bad" : "accent";
  const quiz = s.kind === "quiz";
  const label = (quiz ? "Тест · " : "") + (s.attempt ? `${quiz ? "попытка" : "Попытка"} №${s.attempt}` : "не засчитана");
  const flags = [s.edited_at && "изменено", s.organizer_comment && "комментарий",
    (s.status === "queued" || s.status === "checking") && "проверяется…", s.status === "error" && "проверка не удалась"].filter(Boolean);
  const score = s.status === "done" ? `${s.score}/${s.max_score}` : s.status === "error" ? "—" : "";
  return `<a class="capsule round" href="#" onclick="event.preventDefault();hap();openSubmission(${s.id}, '${quiz ? "quiz" : "notebook"}')">
    <span class="grow stack" style="gap:2px"><span class="h3 ellipsis">${withTitle ? esc(s.assignment_title) + " · " : ""}${label}</span>
      <span class="small muted ellipsis">${when(s.created_at)}${flags.length ? " · " + flags.join(" · ") : ""}</span></span>
    <span class="score c-${st === "neutral" ? "accent" : st}">${score}</span><span class="chev">${ic("chev", 16)}</span></a>`;
}

function queueText(q) {
  if (!q || q.ahead < 1) return "обычно 1–3 минуты";
  const eta = q.eta_seconds ? `, примерно ${Math.max(1, Math.round(q.eta_seconds / 60))} мин` : "";
  return `перед тобой в очереди ${q.ahead}${eta}`;
}

function progressCard(s) {
  const now = s.status === "queued" ? 0 : 1;
  const step = (i, label) => `<span class="s ${i < now ? "done" : i === now ? "now" : ""}"><i>${i < now ? "✓" : i + 1}</i>${label}</span>`;
  return `<div class="card">
    <div class="steps">${step(0, "В очереди")}<span class="line"></span>${step(1, "Проверяется")}<span class="line"></span>${step(2, "Готово")}</div>
    <span class="small muted">Попытка №${s.attempt ?? "—"} · ${queueText(s.queue)}. Отзыв придёт и в чат с ботом.</span></div>`;
}

async function submitWork(id, btn) {
  const input = $("#hw-file");
  if (!input?.files.length) { toast("Выбери файл .ipynb", "err"); return; }
  const fd = new FormData(); fd.append("file", input.files[0]);
  btn.disabled = true;
  const old = btn.innerHTML;
  btn.innerHTML = `<span class="spinner"></span>Отправляем…`;
  hap("medium");
  try {
    const sub = await api(`/assignments/${id}/submissions`, { method: "POST", body: fd });
    $("#drop")?.removeAttribute("data-dirty");
    toast(`Работа отправлена, попытка №${sub.attempt ?? "—"}. Пришлём оценку в чат.`);
    replace(resultRoute(sub.id));
  } catch (e) { toast(e.message, "err"); btn.disabled = false; btn.innerHTML = old; }
}

function openSubmission(id, kind) { show(ME.is_admin ? reviewRoute(id) : kind === "quiz" ? quizResultRoute(id) : resultRoute(id)); }

function resultRoute(id) {
  const route = {
    async render(get) {
      const s = await get(`/submissions/${id}`);
      const list = await get("/assignments").catch(() => []);
      const a = list.find((x) => x.id === s.assignment_id);
      const sub = `${esc(s.assignment_title)} · ${s.attempt ? `попытка №${s.attempt}` : "не засчитана"}`;
      let html = topbar("Результат проверки", sub);
      if (s.status === "queued" || s.status === "checking") {
        // Далеко в очереди — спрашиваем реже, чтобы сотня ждущих не нагружала сервер зря
        later(() => revalidate(route, seq, ""), (s.queue?.ahead ?? 0) > 10 ? 15000 : 4000);
        return html + `<div class="state" style="min-height:30vh"><span class="ill"><span class="spinner" style="width:36px;height:36px;border-width:3px"></span></span>
          <span class="h2">Проверяем работу</span><p>Агент сравнивает решение с эталоном. Можно закрыть приложение — отзыв придёт в чат.</p></div>${progressCard(s)}`;
      }
      if (s.status === "error") {
        return html + `<div class="state err" style="min-height:40vh"><span class="ill">${ic("alert", 40, 1.6)}</span>
          <span class="h2">Не получилось проверить</span><p>Попытка не засчитана, можно сдать ещё раз. Если повторится — напиши организаторам.</p>
          ${a && a.attempts_used < a.max_attempts ? `<button class="btn primary" onclick="replace(assignmentRoute(${a.id}, 'notebook'))">Сдать ещё раз</button>` : ""}</div>`;
      }
      const r = s.result;
      const kind = scoreKind(r.score, r.max_score);
      const bar = `<div class="bar thick">${r.tasks.map((t) => `<i class="${scoreKind(t.score, 10)}"></i>`).join("")}</div>`;
      const edited = s.auto_score !== null && s.auto_score !== s.score
        ? `<div class="row small" style="gap:8px"><span class="avatar sm">${esc(initial(s.edited_by || "О"))}</span>
            <span><b>${esc(s.edited_by || "Организатор")}</b> <span class="muted">изменил оценку: ${s.auto_score} → ${s.score}</span></span></div>` : "";
      html += `<div class="card" style="gap:14px">
        <div class="score-hero"><span class="hero-num">${r.score}<small>/${r.max_score}</small></span>
          <span class="chip big ${kind}">${verdictLabel(r.score, r.max_score)}</span></div>${bar}${edited}
        ${r.summary ? `<span style="font-size:14px">${esc(r.summary)}</span>` : ""}</div>`;
      html += `<div class="stack">${r.tasks.map((t, i) => taskAcc(t, i === r.tasks.findIndex((x) => x.score < 10))).join("")}</div>`;
      if (s.organizer_comment) html += commentCard(s);
      html += `<button class="btn block press" onclick="show(codeRoute(${s.id}))">${ic("code", 18, 2)}Мой код${s.reference_available ? " и эталон" : ""}</button>`;
      if (a && a.is_active && a.attempts_used < a.max_attempts && !(a.deadline && new Date(a.deadline) < new Date()))
        html += `<div class="cta"><button class="btn primary" onclick="replace(assignmentRoute(${a.id}, 'notebook'))">${ic("upload", 18, 2)}Сдать ещё раз</button></div>`;
      return html;
    },
  };
  return route;
}

function taskAcc(t, open) {
  const k = scoreKind(t.score, 10);
  const issues = (t.issues || []).filter((x) => x.text && x.text.length > 1);
  return `<details class="acc"${open ? " open" : ""} ontoggle="this.open&&hap('select')">
    <summary><span class="n" style="background:var(--${k === "neutral" ? "neutral" : k}-soft);color:var(--${k === "neutral" ? "muted" : k})">${t.number}</span>
      <span class="grow h3">${esc(t.title)}</span>
      <span class="num c-${k}">${t.score}</span><span class="chev">${ic("chev", 16)}</span></summary>
    <div class="body">
      ${t.auto_score !== null && t.auto_score !== undefined ? `<span class="chip accent" style="align-self:flex-start">агент поставил ${t.auto_score}, организатор — ${t.score}</span>` : ""}
      ${t.feedback ? `<span>${esc(t.feedback)}</span>` : ""}
      ${issues.map((x) => `<span class="issue ${esc(x.severity)}">${esc(x.text)}</span>`).join("")}
      ${(t.improvements || []).map((x) => `<span class="tip">${ic("bulb", 16, 2)}<span>${esc(x)}</span></span>`).join("")}
    </div></details>`;
}

function commentCard(s) {
  const by = s.comment_by || "Организатор";
  return `<div class="comment"><div class="row"><span class="avatar sm">${esc(initial(by))}</span>
    <span class="grow stack" style="gap:0"><span class="h3">${esc(by)}</span><span class="small muted">организатор${s.comment_at ? " · " + when(s.comment_at) : ""}</span></span></div>
    <span class="pre" style="font-size:14px">${esc(s.organizer_comment)}</span></div>`;
}

let CODE_STATE = { task: 0, view: "mine" };
function codeRoute(id) {
  CODE_STATE = { task: 0, view: "mine" };
  return {
    async render(get) {
      const [s, code] = await Promise.all([get(`/submissions/${id}`), get(`/submissions/${id}/code`)]);
      const hasRef = code.tasks.some((t) => t.reference !== null);
      const tasks = code.tasks;
      const res = s.result?.tasks || [];
      const i = Math.min(CODE_STATE.task, tasks.length - 1);
      const t = tasks[i];
      const rt = res.find((x) => x.number === t?.number);
      if (!hasRef) CODE_STATE.view = "mine";
      return `${topbar(hasRef ? "Мой код и эталон" : "Мой код", `${esc(s.assignment_title)} · попытка №${s.attempt ?? "—"}`)}
        <div class="pills">${tasks.map((x, k) => { const r = res.find((y) => y.number === x.number); const sk = r ? scoreKind(r.score, 10) : "neutral";
          return `<button class="pill${k === i ? " on" : ""}" onclick="CODE_STATE.task=${k};hap('select');reload()">Задание ${x.number}${r ? ` <span class="num c-${sk}" style="font-size:11px">${r.score}</span>` : ""}</button>`; }).join("")}</div>
        ${t ? `<span class="h3">${esc(t.title)}</span>
        ${hasRef ? `<div class="seg" role="tablist">
          <button class="${CODE_STATE.view === "mine" ? "on" : ""}" onclick="CODE_STATE.view='mine';hap('select');reload()">Мой код</button>
          <button class="${CODE_STATE.view === "ref" ? "on" : ""}" onclick="CODE_STATE.view='ref';hap('select');reload()">Эталон</button></div>`
          : `<div class="card tight row small muted" style="flex-direction:row">${ic("lock", 16, 2)}Эталон откроется после дедлайна.</div>`}
        ${CODE_STATE.view === "ref" ? codeBlock(t.reference, "эталон") : codeBlock(t.code, "твой код")}
        ${rt ? `<div class="card tight"><span class="small muted" style="font-weight:600">Что сказал агент</span>
          ${rt.feedback ? `<span style="font-size:14px">${esc(rt.feedback)}</span>` : ""}
          ${(rt.issues || []).filter((x) => x.text?.length > 1).map((x) => `<span class="issue ${esc(x.severity)}">${esc(x.text)}</span>`).join("")}
          ${(rt.improvements || []).map((x) => `<span class="tip">${ic("bulb", 16, 2)}<span>${esc(x)}</span></span>`).join("")}</div>` : ""}`
          : emptyState("code", "Код не найден", "Не удалось сопоставить ячейки ноутбука с заданиями.")}`;
    },
  };
}

let REF_TASK = 0;
function referenceRoute(aid) {
  REF_TASK = 0;
  return {
    async render(get) {
      const [tasks, list] = await Promise.all([get(`/assignments/${aid}/reference`), get("/assignments")]);
      const a = list.find((x) => x.id === aid);
      const i = Math.min(REF_TASK, tasks.length - 1);
      const t = tasks[i];
      return `${topbar("Эталонное решение", esc(a?.title || ""))}
        <div class="pills">${tasks.map((x, k) => `<button class="pill${k === i ? " on" : ""}" onclick="REF_TASK=${k};hap('select');reload()">Задание ${x.number}</button>`).join("")}</div>
        ${t ? `<span class="h3">${esc(t.title)}</span>${codeBlock(t.reference, "эталон")}` : ""}
        <span class="small muted">Сравнить со своим кодом: «Сдачи» → попытка → «Мой код и эталон».</span>
        <div class="row wrap" style="gap:8px">
          ${ME.bot_enabled ? `<button class="btn sm" onclick="sendFile('/assignments/${aid}/reference/send','Эталон отправлен в чат')">${ic("send", 16, 2)}Ноутбук в чат</button>` : ""}
          <button class="btn sm" onclick="download('/assignments/${aid}/reference/file','reference.ipynb')">${ic("download", 16, 2)}Скачать ноутбук</button></div>`;
    },
  };
}

let HIST_FILTER = "all";
function historyRoute() {
  return {
    async render(get) {
      const [subs, alist] = await Promise.all([get("/submissions"), get("/assignments").catch(() => [])]);
      const head = `<h1 class="h1">Мои сдачи</h1>`;
      if (!subs.length) return head + emptyState("upload", "Пока ни одной сдачи", "Открой задание: пройди тест или пришли .ipynb — агент проверит его за пару минут.",
        `<button class="btn primary" onclick="openTab('home')">К заданиям</button>`);
      const withC = subs.filter((s) => s.organizer_comment).length;
      const list = HIST_FILTER === "comment" ? subs.filter((s) => s.organizer_comment) : subs;
      const groups = new Map();
      for (const s of list) {
        if (!groups.has(s.assignment_id)) groups.set(s.assignment_id, { title: s.assignment_title, items: [] });
        groups.get(s.assignment_id).items.push(s);
      }
      const pills = `<div class="pills">
        <button class="pill${HIST_FILTER === "all" ? " on" : ""}" onclick="HIST_FILTER='all';hap('select');reload()">Все · ${subs.length}</button>
        <button class="pill${HIST_FILTER === "comment" ? " on" : ""}" onclick="HIST_FILTER='comment';hap('select');reload()">С комментарием${withC ? " · " + withC : ""}</button></div>`;
      const body = [...groups.values()].map((g) => {
        const done = g.items.filter((s) => s.status === "done");
        const best = done.length ? Math.max(...done.map((s) => s.score)) : null;
        const pending = g.items.some((s) => s.status === "queued" || s.status === "checking");
        const av = alist.find((x) => x.id === g.items[0].assignment_id);
        const total = av && av.best_score !== null ? `итог ${av.best_score}/${av.max_score}` : best !== null ? `лучший ${best}/${done[0].max_score}` : "";
        return `<div class="stack" style="gap:10px"><div class="row between" style="align-items:baseline">
            <span class="h3">${esc(g.title)}</span><span class="small muted">${pending ? "проверяется" : total}</span></div>
          <div>${g.items.map((s) => {
            const k = s.status === "done" ? scoreKind(s.score, s.max_score) : s.status === "error" ? "neutral" : "accent";
            return `<div class="tl"><div class="rail"><i style="background:var(--${k === "neutral" ? "muted" : k === "accent" ? "accent" : k})"></i><b></b></div>
              <div class="grow">${subRow(s)}</div></div>`; }).join("")}</div></div>`;
      }).join("");
      return head + pills + (body || `<p class="muted" style="text-align:center">Комментариев пока нет</p>`);
    },
  };
}

// ---------- хакатоны ----------
const HSUB = {};
const FILE_NAMES = { train: "train.csv", test: "test.csv", task: "Условие", baseline: "baseline", solution: "Ответы (скрыто)" };
function hackListRoute() {
  return {
    async render(get) {
      const list = (await get("/hackathons")).filter((h) => h.is_active || ME.is_admin);
      const head = `<div class="row between"><h1 class="h1">Хакатоны</h1>
        ${ME.is_admin ? `<button class="btn sm primary" onclick="show(hackFormRoute(null))">${ic("plus", 16, 2.2)}Новый</button>` : ""}</div>`;
      const running = list.filter((h) => h.status === "running");
      const upcoming = list.filter((h) => h.status === "upcoming");
      const finished = list.filter((h) => h.status === "finished").reverse();
      if (!running.length && !upcoming.length && !finished.length)
        return head + emptyState("trophy", "Ближайших хакатонов нет", "Когда организаторы объявят хакатон, бот напишет в чат.");
      let html = head;
      for (const h of running) html += await hackBody(h, get);
      for (const h of upcoming) html += upcomingCard(h);
      if (finished.length) html += `<span class="h3">Прошедшие</span><div class="list-grid">${finished.map((h) => `
        <a class="capsule" href="#" onclick="event.preventDefault();hap();show(hackRoute(${h.id}))">
          <span class="dot">${ic("trophy", 20, 2)}</span>
          <span class="grow stack" style="gap:0"><span class="h3 ellipsis">${esc(h.title)}</span>
            <span class="small muted">${dm(h.starts_at)} – ${dm(h.ends_at)}${h.my_best !== null ? ` · твой лучший ${num(h.my_best)}` : ""}</span></span>
          <span class="chev">${ic("chev", 16)}</span></a>`).join("")}</div>`;
      return html;
    },
    after() { every(tickCountdowns, 30000); },
  };
}
function upcomingCard(h) {
  return `<div class="card inverse" style="border-radius:32px;padding:22px 20px;gap:14px">
    <span class="chip solid big" style="align-self:flex-start">Ближайший хакатон</span>
    <span class="h2" style="font-size:28px">${esc(h.title)}</span>
    <span style="font-size:14px">Пройдёт с <b>${msk(h.starts_at)}</b> по <b>${msk(h.ends_at)}</b>.</span>
    <div class="grid-3" data-countdown="${esc(h.starts_at)}">${countdownTiles(h.starts_at)}</div>
    <span class="small" style="opacity:.75">Условие и данные появятся здесь в момент старта. Бот напишет, когда хакатон начнётся.</span>
    ${adminHackActions(h, true)}</div>`;
}
function countdownTiles(iso) {
  let m = Math.max(0, Math.round((new Date(iso) - Date.now()) / 60000));
  const d = Math.floor(m / 1440); m -= d * 1440; const h = Math.floor(m / 60); m -= h * 60;
  const t = (v, l) => `<div class="tile" style="background:var(--inverse-soft)"><span class="v" style="font-size:22px">${v}</span><span class="k" style="color:inherit;opacity:.7">${l}</span></div>`;
  return t(d, plural(d, "день", "дня", "дней")) + t(h, plural(h, "час", "часа", "часов")) + t(m, plural(m, "минута", "минуты", "минут"));
}
function tickCountdowns() {
  $$("[data-countdown]").forEach((el) => { el.innerHTML = countdownTiles(el.dataset.countdown); });
  $$("[data-left]").forEach((el) => { el.textContent = left(el.dataset.left); });
}
function adminHackActions(h, onInverse = false) {
  if (!ME.is_admin) return "";
  const cls = onInverse ? "btn xs" : "btn xs";
  const st = onInverse ? ' style="background:var(--inverse-soft);color:inherit"' : "";
  return `<div class="row wrap" style="gap:6px">
    <button class="${cls}"${st} onclick="show(hackFormRoute(${h.id}))">${ic("edit", 14, 2)}Изменить</button>
    <button class="${cls}"${st} onclick="show(hackAllSubsRoute(${h.id}))">Все решения</button>
    ${h.status === "upcoming" ? `<button class="${cls}"${st} onclick="show(hackRoute(${h.id}))">Предпросмотр</button>` : ""}
    ${h.is_active ? "" : `<span class="chip warn">скрыт от участников</span>`}</div>`;
}
function hackRoute(id) {
  return {
    async render(get) { return topbar("Хакатон") + (await hackBody(await get(`/hackathons/${id}`), get)); },
    after() { every(tickCountdowns, 30000); },
  };
}
async function hackBody(h, get) {
  const sub = HSUB[h.id] || "lb";
  const [lb, mine] = await Promise.all([get(`/hackathons/${h.id}/leaderboard`), get(`/hackathons/${h.id}/submissions`)]);
  const me = lb.find((r) => r.is_me);
  const status = h.status === "running"
    ? `<span class="chip solid big">Идёт · ещё <span data-left="${esc(h.ends_at)}">${left(h.ends_at)}</span></span>`
    : h.status === "upcoming" ? `<span class="chip accent big">Скоро · старт ${msk(h.starts_at)}</span>` : `<span class="chip big">Завершён</span>`;
  const files = Object.keys(FILE_NAMES).filter((k) => h.files[k]).map((k) => `<span class="row" style="gap:2px">
    <button class="pill sm" onclick="download('/hackathons/${h.id}/files/${k}', ${esc(JSON.stringify(h.files[k]))})">${ic(k === "task" ? "doc" : k === "baseline" ? "code" : k === "solution" ? "lock" : "download", 14, 2)}${FILE_NAMES[k]}</button>
    ${ME.bot_enabled ? `<button class="pill sm" style="padding:0 10px" aria-label="Отправить в чат" onclick="sendFile('/hackathons/${h.id}/files/${k}/send','Файл отправлен в чат')">${ic("send", 14, 2)}</button>` : ""}</span>`).join("");
  const tiles = `<div class="grid-3">
    <div class="tile"><span class="k">лучший</span><span class="v mono">${num(h.my_best)}</span></div>
    <div class="tile"><span class="k">место</span><span class="v">${me ? me.place : "—"}<span class="small muted"> / ${lb.length}</span></span></div>
    <div class="tile"><span class="k">сегодня</span><span class="v">${h.used_today}<span class="small muted"> / ${h.daily_limit}</span></span></div></div>`;
  const submit = h.status === "running" ? `<label class="btn primary block press" style="position:relative;overflow:hidden">
      ${ic("upload", 18, 2)}<span id="hsend-${h.id}">Отправить .csv</span>
      <input type="file" accept=".csv,.txt" style="position:absolute;inset:0;opacity:0;cursor:pointer" onchange="submitHack(${h.id}, this)"></label>
    <span class="small muted">Колонки <b class="mono">${esc(h.id_column)}</b> и <b class="mono">${esc(h.target_column)}</b>, строка на каждый id из test. Ошибочные файлы не тратят попытку. Можно прислать .csv и прямо в чат с ботом.</span>` : "";
  const seg = `<div class="seg" role="tablist">
    <button class="${sub === "mine" ? "on" : ""}" onclick="HSUB[${h.id}]='mine';hap('select');reload()">Мои решения</button>
    <button class="${sub === "lb" ? "on" : ""}" onclick="HSUB[${h.id}]='lb';hap('select');reload()">Лидерборд</button></div>`;
  const board = lb.length ? `<div class="stack" style="gap:6px">${lb.map((r) => `
      <div class="lb-row${r.is_me ? " me" : ""}${r.place <= 3 ? " top" : ""}"><span class="place">${r.place}</span>
        <span class="grow stack" style="gap:0"><span class="h3 ellipsis" style="font-size:14px">${esc(r.name)}${r.is_me ? " (ты)" : ""}</span>
          ${r.user ? `<span class="small" style="opacity:.7">${esc(r.user.fio || "ФИО не указано")}${r.user.username ? " · @" + esc(r.user.username) : ""}</span>` : ""}</span>
        <span class="small" style="opacity:.7">${r.attempts} ${plural(r.attempts, "попытка", "попытки", "попыток")}</span><span class="sc">${num(r.score)}</span></div>`).join("")}</div>
      <span class="small muted">В зачёт идёт лучший результат. При равенстве выше тот, кто отправил раньше.</span>`
    : `<p class="muted" style="text-align:center">Пока нет ни одного решения</p>`;
  const mineList = mine.length ? `<div class="stack" style="gap:6px">${[...mine].reverse().map((x) => `
      <div class="capsule round" style="padding:6px 16px 6px 6px"><span class="avatar sm plain" style="font:600 12px var(--font)">№${x.number}</span>
        <span class="grow stack" style="gap:0"><span class="h3 ellipsis" style="font-size:14px">${esc(x.file_name)}</span>
          <span class="small muted">${when(x.created_at)}${x.status === "done" && x.score === h.my_best ? " · лучший" : ""}${x.status !== "done" ? " · " + esc(x.error || "ошибка") : ""}</span></span>
        <span class="mono small ${x.status === "done" ? (x.score === h.my_best ? "c-good" : "") : "c-bad"}" style="font-size:13px">${x.status === "done" ? num(x.score) : "ошибка"}</span></div>`).join("")}</div>`
    : `<p class="muted" style="text-align:center">Решений пока нет</p>`;
  return `<div class="stack" style="gap:14px">
    <div class="stack" style="gap:6px"><div class="row between">${status}<span class="small muted">${h.status === "running" ? "до " + msk(h.ends_at) : ""}</span></div>
      <span class="h1">${esc(h.title)}</span>
      <span class="small muted">Метрика: ${esc(h.metric_title)}, ${h.higher_is_better ? "больше" : "меньше"} — лучше</span></div>
    ${h.description ? `<details class="acc"${h.my_count ? "" : " open"}><summary><span class="n" style="background:var(--bg)">${ic("doc", 18, 2)}</span><span class="grow h3">Условие</span><span class="chev">${ic("chev", 16)}</span></summary>
      <div class="body pre" style="padding-left:16px">${esc(h.description)}</div></details>` : ""}
    ${files ? `<div class="row wrap" style="gap:6px">${files}</div>` : ""}
    ${tiles}${submit}${seg}${sub === "lb" ? board : mineList}${adminHackActions(h)}</div>`;
}
async function submitHack(id, input) {
  if (!input.files.length) return;
  const fd = new FormData(); fd.append("file", input.files[0]);
  const lbl = $(`#hsend-${id}`);
  lbl.innerHTML = `<span class="spinner" style="display:inline-block;vertical-align:-3px"></span> Считаем метрику…`;
  hap("medium");
  try {
    const r = await api(`/hackathons/${id}/submissions`, { method: "POST", body: fd });
    r.status === "done" ? toast(`Решение №${r.number}: ${num(r.score)}`) : toast(`Решение №${r.number} не принято: ${r.error}`, "err");
    HSUB[id] = "mine";
    reload();
  } catch (e) { toast(e.message, "err"); lbl.textContent = "Отправить .csv"; input.value = ""; }
}

// ---------- профиль ----------
function profileRoute() {
  return {
    fresh: true,
    async render(get) {
      const [me, subs, list] = await Promise.all([get("/me"), get("/submissions"), get("/assignments")]);
      ME = me;
      const p = me.profile;
      const done = subs.filter((s) => s.status === "done").length;
      const avg = avgScore(list);
      const pref = themePref();
      const opt = (k, label, icon) => `<button data-theme-opt="${k}" class="${pref === k ? "on" : ""}" onclick="setTheme('${k}')">${ic(icon, 16, 2)}${label}</button>`;
      return `${stack.length > 1 ? topbar("Профиль") : `<h1 class="h1">Профиль</h1>`}
        <div class="capsule" style="padding:10px 18px 10px 10px">
          <span class="avatar lg">${esc(initial(p.name))}</span>
          <span class="grow stack" style="gap:2px"><span class="h3" style="font-size:17px">${esc(p.name)}</span>
            <span class="small muted">${p.username ? "@" + esc(p.username) + " · " : ""}${me.is_admin ? "организатор" : "участник"}</span></span></div>
        ${me.is_admin ? "" : `<div class="grid-3">
          <div class="tile"><span class="v">${subs.length}</span><span class="k">сдано</span></div>
          <div class="tile"><span class="v c-good">${done}</span><span class="k">проверено</span></div>
          <div class="tile"><span class="v c-accent">${avg ?? "—"}</span><span class="k">средний</span></div></div>`}
        <form class="card" id="profile-form" oninput="this.setAttribute('data-dirty','');$('#profile-save').disabled=false">
          <span class="small muted" style="font-weight:600">Как тебя зовут</span>
          <label class="field"><span>Ник в боте — его видят другие</span>
            <input class="input" name="nickname" maxlength="40" value="${esc(p.nickname || "")}" placeholder="${esc(p.tg_name)}"></label>
          <label class="field"><span>ФИО — видят только организаторы</span>
            <input class="input" name="fio" maxlength="150" value="${esc(p.fio || "")}" placeholder="Иванов Иван Иванович" autocomplete="name"></label>
          ${!p.fio && !me.is_admin ? `<span class="small c-warn" style="padding:0 6px">ФИО нужно, чтобы засчитать домашки.</span>` : ""}
          <button class="btn primary" id="profile-save" type="submit" disabled>Сохранить</button></form>
        <div class="stack"><span class="small muted" style="font-weight:600;padding-left:4px">Тема</span>
          <div class="seg sm">${opt("dark", "Тёмная", "moon")}${opt("light", "Светлая", "sun")}${opt("tg", "Как в TG", "plane")}</div></div>
        ${tg?.close ? `<button class="btn ghost block" onclick="tg.close()">Закрыть приложение</button>` : ""}`;
    },
    after() {
      $("#profile-form").onsubmit = async (ev) => {
        ev.preventDefault();
        const fd = new FormData(ev.target);
        const btn = $("#profile-save"); btn.disabled = true;
        try {
          ME.profile = await api("/profile", json("PATCH", { nickname: fd.get("nickname"), fio: fd.get("fio") }));
          ev.target.removeAttribute("data-dirty");
          toast("Сохранено");
          reload();
        } catch (e) { toast(e.message, "err"); btn.disabled = false; }
      };
    },
  };
}
function openProfile() {
  hap();
  if (tabs().some((t) => t[0] === "profile")) openTab("profile");
  else show(profileRoute());
}

// =====================================================================
// ОРГАНИЗАТОР
// =====================================================================
let ADMIN_AID = null;
let SUB_FILTER = "all";
let SUB_KIND = "all";
const KIND_OF = new Map(); // id сдачи → notebook | quiz (переживает сброс кеша после сохранения)
let SELECTED_SID = null;
const SEARCH = { subs: "", people: "" };

function adminSubsRoute() {
  return {
    wideSplit: true,
    async render(get) {
      const list = await get("/assignments");
      const head = `<div class="row between"><h1 class="h1">Сдачи</h1><span class="chip accent big">организатор</span></div>`;
      if (!list.length) return head + emptyState("review", "Заданий пока нет", "Сдачи появятся, когда участники начнут присылать работы.");
      if (!list.some((a) => a.id === ADMIN_AID)) ADMIN_AID = (list.find((a) => a.published && a.is_active) || list[0]).id;
      const cur = list.find((a) => a.id === ADMIN_AID);
      const both = partsOf(cur).length > 1;
      if (!both) SUB_KIND = "all";
      const allSubs = await get(`/admin/assignments/${ADMIN_AID}/submissions`);
      const subs = allSubs.filter((s) => SUB_KIND === "all" || (s.kind || "notebook") === SUB_KIND);
      const counts = {
        all: subs.length,
        queue: subs.filter((s) => s.status === "queued" || s.status === "checking").length,
        error: subs.filter((s) => s.status === "error").length,
        comment: subs.filter((s) => s.organizer_comment).length,
      };
      const shown = subs.filter((s) => SUB_FILTER === "all" || (SUB_FILTER === "queue" ? s.status === "queued" || s.status === "checking"
        : SUB_FILTER === "error" ? s.status === "error" : !!s.organizer_comment));
      const asel = `<div class="pills">${list.map((a) => `<button class="pill${a.id === ADMIN_AID ? " on" : ""}" onclick="ADMIN_AID=${a.id};SELECTED_SID=null;hap('select');reload()">${esc(a.title)}</button>`).join("")}</div>`;
      const kinds = both ? `<div class="seg sm">${[["all", "Все части"], ["quiz", `Тест · ${allSubs.filter((s) => s.kind === "quiz").length}`], ["notebook", `Ноутбук · ${allSubs.filter((s) => (s.kind || "notebook") === "notebook").length}`]].map(([k, l]) =>
        `<button class="${SUB_KIND === k ? "on" : ""}" onclick="SUB_KIND='${k}';hap('select');reload()">${l}</button>`).join("")}</div>` : "";
      const fpills = `<div class="pills">${[["all", "Все"], ["queue", "В очереди"], ["error", "Ошибки"], ["comment", "С комментарием"]].map(([k, l]) =>
        `<button class="pill sm${SUB_FILTER === k ? " accent" : ""}" onclick="SUB_FILTER='${k}';hap('select');reload()">${l} · ${counts[k]}</button>`).join("")}</div>`;
      const search = `<label class="row input" style="gap:10px;color:var(--muted)">${ic("search", 18, 2)}
        <input class="grow" style="border:0;background:none;outline:none;height:100%" placeholder="Ник, ФИО, @username или id сдачи"
          value="${esc(SEARCH.subs)}" oninput="SEARCH.subs=this.value;filterRows('#subs-list',this.value)"></label>`;
      allSubs.forEach((x) => KIND_OF.set(x.id, x.kind || "notebook"));
      const rows = shown.map((s) => {
        const k = s.status === "done" ? scoreKind(s.score, s.max_score) : s.status === "error" ? "bad" : "accent";
        const u = s.user || {};
        const flags = [s.kind === "quiz" && `<span class="chip solid">тест</span>`, s.edited_at && `<span class="chip accent">изменено${s.edited_by ? " · " + esc(s.edited_by) : ""}</span>`,
          s.organizer_comment && `<span class="chip">комментарий</span>`, s.status === "error" && `<span class="chip bad">ошибка проверки</span>`].filter(Boolean).join("");
        const key = [u.name, u.fio, u.username, s.id, u.tg_id].join(" ").toLowerCase();
        return `<a class="capsule round${s.id === SELECTED_SID ? " sel" : ""}" href="#" data-key="${esc(key)}" data-sid="${s.id}" onclick="event.preventDefault();hap();openReview(${s.id})">
          <span class="avatar${k === "accent" ? "" : " plain"}">${esc(initial(u.name))}</span>
          <span class="grow stack" style="gap:2px">
            <span class="row" style="gap:6px;align-items:baseline"><span class="h3">${esc(u.name)}</span><span class="small ellipsis ${u.fio ? "muted" : "c-bad"}">${u.fio ? esc(u.fio) : "ФИО не указано"}</span></span>
            <span class="small muted mono">№${s.attempt ?? "—"} · id ${s.id} · ${when(s.created_at)}</span>
            ${flags ? `<span class="row wrap" style="gap:4px;margin-top:2px">${flags}</span>` : ""}</span>
          <span class="score c-${k}">${s.status === "done" ? `${s.score}/${s.max_score}` : s.status === "error" ? "—" : "…"}</span></a>`;
      }).join("");
      const listHtml = `<div class="stack" id="subs-list">${rows || `<p class="muted" style="text-align:center">Сдач нет</p>`}</div>`;
      return `${head}${asel}${kinds}${search}${fpills}
        <div class="split"><div class="split-list">${listHtml}</div>
          <div class="split-pane" id="pane">${emptyState("review", "Выбери сдачу", "Код участника и эталон откроются здесь рядом со списком.")}</div></div>`;
    },
    after() {
      if (SEARCH.subs) filterRows("#subs-list", SEARCH.subs);
      if (isWide() && SELECTED_SID && $(`[data-sid="${SELECTED_SID}"]`)) loadPane(SELECTED_SID);
    },
  };
}
function filterRows(sel, q) {
  q = q.trim().toLowerCase();
  $$(`${sel} [data-key]`).forEach((el) => el.classList.toggle("hide", !!q && !el.dataset.key.includes(q)));
}
function openReview(sid) {
  SELECTED_SID = sid;
  if (isWide() && $("#pane")) {
    $$("#subs-list .capsule").forEach((el) => el.classList.toggle("sel", +el.dataset.sid === sid));
    loadPane(sid);
  } else show(reviewRoute(sid));
}
async function loadPane(sid) {
  const pane = $("#pane");
  pane.innerHTML = `<div class="stack"><span class="skel" style="height:64px;border-radius:999px"></span><span class="skel" style="height:220px;border-radius:20px"></span></div>`;
  try {
    const html = await reviewHtml(sid, loader("network"), true);
    if (SELECTED_SID !== sid) return;
    pane.innerHTML = html;
    pane.firstElementChild?.classList.add("enter");
    bindReview(pane, sid);
  } catch (e) { pane.innerHTML = errorState(e, false); }
}

let REV = null;
const SEVERITY = { critical: "критичное", major: "серьёзное", minor: "мелкое" };
function reviewRoute(sid) {
  return {
    fresh: true,
    wideSplit: true,
    render: (get) => reviewHtml(sid, get, false),
    after() { bindReview($("#view"), sid); },
  };
}
async function reviewHtml(sid, get, inPane) {
  // У попытки теста нет кода: если уже знаем, что это тест, лишний запрос не делаем
  const isQuiz = KIND_OF.get(sid) === "quiz";
  const noCode = { error: null, tasks: [], cells: [] };
  const [s, code] = await Promise.all([get(`/submissions/${sid}`),
    isQuiz ? noCode : get(`/admin/submissions/${sid}/code`).catch((e) => ({ error: e.message, tasks: [], cells: [] }))]);
  KIND_OF.set(sid, s.kind || "notebook");
  if (s.kind === "quiz") return quizReviewHtml(s, inPane);
  const u = s.user || {};
  const r = s.result;
  REV = { sid, task: 0, scores: {}, auto: {} };
  for (const t of r?.tasks || []) { REV.scores[t.number] = t.score; REV.auto[t.number] = t.auto_score ?? t.score; }
  const head = inPane ? "" : topbar(esc(s.assignment_title), `<span class="mono">попытка №${s.attempt ?? "—"} · id ${s.id} · ${when(s.created_at)}</span>`);
  const who = `<div class="capsule full" style="background:var(--inverse);color:var(--on-inverse);padding:10px 18px 10px 10px">
    <span class="avatar">${esc(initial(u.name))}</span>
    <span class="grow stack" style="gap:0"><span class="h3 ellipsis">${esc(u.name)} · ${u.fio ? esc(u.fio) : `<span style="color:var(--bad)">ФИО не указано</span>`}</span>
      <span class="small mono" style="opacity:.7">${u.username ? "@" + esc(u.username) + " · " : ""}id ${u.tg_id}${inPane ? ` · №${s.attempt ?? "—"} · сдача ${s.id}` : ""}</span></span>
    ${r ? `<span class="stack" style="gap:0;align-items:flex-end"><span class="num" style="font-size:20px"><span id="rev-total">${r.score}</span><span class="small" style="opacity:.6">/${r.max_score}</span></span>
      <span class="small" style="opacity:.7">агент: ${s.auto_score ?? r.score}</span></span>` : ""}</div>`;
  const tools = `<div class="row wrap full" style="gap:6px">
    <button class="btn xs" onclick="download('/admin/submissions/${s.id}/file','submission_${s.id}.ipynb')">${ic("download", 14, 2)}Скачать .ipynb</button>
    <button class="btn xs" onclick="recheck(${s.id})">${ic("refresh", 14, 2)}Перепроверить агентом</button></div>`;
  if (!r || s.status !== "done") {
    const pending = s.status === "queued" || s.status === "checking";
    if (pending && !inPane) later(reload, 5000);
    return `${head}<div class="review">${who}${pending ? progressCard(s)
      : `<div class="card soft-bad"><span class="h3">Проверка не удалась</span><span class="small pre">${esc(s.error || "Неизвестная ошибка")}</span></div>`}${tools}${notebookDetails(code)}</div>`;
  }
  const tasks = code.tasks.length ? code.tasks : r.tasks.map((t) => ({ number: t.number, title: t.title, code: "", reference: "" }));
  const taskPills = `<div class="pills" id="rev-pills">${tasks.map((t, i) => `<button class="pill${i === 0 ? " on" : ""}" data-i="${i}">Зад. ${t.number} · <span data-score="${t.number}">${REV.scores[t.number] ?? "—"}</span></button>`).join("")}</div>`;
  const panes = tasks.map((t, i) => {
    const rt = r.tasks.find((x) => x.number === t.number);
    return `<div class="stack rev-task${i ? " hide" : ""}" data-i="${i}" style="gap:12px">
      <span class="h3">${esc(t.title)}</span>
      <div class="seg sm only-narrow"><button class="on" data-show="mine">Код участника</button><button data-show="ref">Эталон</button></div>
      <div class="code-pair" data-show="mine"><div class="mine">${codeBlock(t.code, "участник")}</div><div class="ref">${codeBlock(t.reference, "эталон")}</div></div>
      ${rt ? `<div class="card tight"><span class="small muted" style="font-weight:600">Агент · ${rt.auto_score ?? rt.score}/10</span>
        ${rt.feedback ? `<span style="font-size:14px">${esc(rt.feedback)}</span>` : ""}
        ${(rt.issues || []).filter((x) => x.text?.length > 1).map((x) => `<span class="issue ${esc(x.severity)}"><span>${esc(x.text)} <span class="muted small">· ${SEVERITY[x.severity] || esc(x.severity)}</span></span></span>`).join("")}
        ${(rt.improvements || []).map((x) => `<span class="tip">${ic("bulb", 16, 2)}<span>${esc(x)}</span></span>`).join("")}</div>` : ""}</div>`;
  }).join("");
  const scores = `<div class="card"><span class="row between"><span class="small muted" style="font-weight:600">Баллы за задания</span><span class="small muted">0–10</span></span>
    ${r.tasks.map((t) => `<div class="task-score"><span class="grow stack" style="gap:0"><span class="h3 ellipsis" style="font-size:14px">${t.number}. ${esc(t.title)}</span>
        <span class="small muted">агент: ${REV.auto[t.number]}</span></span>
      <span class="stepper"><button type="button" data-step="-1" data-n="${t.number}" data-max="10" aria-label="Меньше">−</button>
        <span class="v" data-v="${t.number}">${t.score}</span>
        <button type="button" data-step="1" data-n="${t.number}" data-max="10" aria-label="Больше">+</button></span></div>`).join("")}</div>`;
  const myName = ME.profile.name;
  const comment = `<div class="card"><span class="small muted">Комментарий участнику · подпишем как «${esc(myName)}»</span>
    <textarea class="textarea" id="rev-comment" rows="4" placeholder="Что получилось, что поправить…">${esc(s.organizer_comment || "")}</textarea>
    ${s.organizer_comment && s.comment_by ? `<span class="small muted">Сейчас: ${esc(s.comment_by)}${s.comment_at ? ", " + when(s.comment_at) : ""}</span>` : ""}</div>`;
  const warnings = r.warnings?.length ? `<div class="card tight soft-warn small">${r.warnings.map(esc).join("<br>")}</div>` : "";
  return `${head}<div class="review">${who}
    <div class="review-main">${taskPills}${panes}${warnings}${tools}${notebookDetails(code)}</div>
    <div class="review-side">${scores}${comment}
      <div class="cta"><button class="btn primary" id="rev-save">${ic("send", 18, 2)}Сохранить и уведомить</button></div></div></div>`;
}
function notebookDetails(code) {
  if (code.error) return `<div class="card tight soft-bad small">${esc(code.error)}</div>`;
  if (!code.cells?.length) return "";
  return `<details class="acc full"><summary><span class="n" style="background:var(--bg)">${ic("doc", 18, 2)}</span><span class="grow h3">Весь ноутбук · ${code.cells.length} ${plural(code.cells.length, "ячейка", "ячейки", "ячеек")}</span><span class="chev">${ic("chev", 16)}</span></summary>
    <div class="body stack" style="padding:4px 12px 12px">${code.cells.map((c) => c.type === "code" ? codeBlock(c.source, `ячейка ${c.index}`) : `<div class="cell-md">${esc(c.source)}</div>`).join("")}</div></details>`;
}
function bindReview(root, sid) {
  root.querySelectorAll("#rev-pills .pill").forEach((b) => b.addEventListener("click", () => {
    const i = b.dataset.i;
    hap("select");
    root.querySelectorAll("#rev-pills .pill").forEach((x) => x.classList.toggle("on", x === b));
    root.querySelectorAll(".rev-task").forEach((p) => p.classList.toggle("hide", p.dataset.i !== i));
  }));
  root.querySelectorAll(".seg [data-show]").forEach((b) => b.addEventListener("click", () => {
    hap("select");
    const task = b.closest(".rev-task");
    task.querySelectorAll(".seg [data-show]").forEach((x) => x.classList.toggle("on", x === b));
    task.querySelector(".code-pair").dataset.show = b.dataset.show;
  }));
  root.querySelectorAll("[data-step]").forEach((b) => b.addEventListener("click", () => {
    const n = b.dataset.n;
    const v = Math.max(0, Math.min(Number(b.dataset.max || 10), REV.scores[n] + Number(b.dataset.step)));
    if (v === REV.scores[n]) { hap("warning"); return; }
    REV.scores[n] = v;
    hap("select");
    root.querySelector(`[data-v="${n}"]`).textContent = v;
    const pill = root.querySelector(`[data-score="${n}"]`); if (pill) pill.textContent = v;
    const total = Object.values(REV.scores).reduce((a, x) => a + x, 0);
    const tot = root.querySelector("#rev-total"); if (tot) tot.textContent = total;
    root.querySelector(".review")?.setAttribute("data-dirty", "");
  }));
  root.querySelectorAll("#qrev-nav [data-jump]").forEach((b) => b.addEventListener("click", () => {
    hap("select");
    root.querySelector(`#qrev-${b.dataset.jump}`)?.scrollIntoView({ block: "start", behavior: "smooth" });
  }));
  root.querySelector("#rev-comment")?.addEventListener("input", () => root.querySelector(".review")?.setAttribute("data-dirty", ""));
  const save = root.querySelector("#rev-save");
  if (save) save.onclick = () => saveReview(sid, root);
}
async function saveReview(sid, root) {
  const btn = root.querySelector("#rev-save");
  btn.disabled = true;
  const old = btn.innerHTML;
  btn.innerHTML = `<span class="spinner"></span>Сохраняем…`;
  try {
    await api(`/admin/submissions/${sid}/review`, json("PATCH", { task_scores: REV.scores, comment: root.querySelector("#rev-comment").value }));
    root.querySelector(".review")?.removeAttribute("data-dirty");
    toast("Сохранено, участник получил уведомление");
    if (root.id === "pane") { reload(); } else reload();
  } catch (e) { toast(e.message, "err"); btn.disabled = false; btn.innerHTML = old; }
}
async function recheck(id) {
  try {
    await api(`/admin/submissions/${id}/recheck`, { method: "POST" });
    toast("Отправлено на перепроверку");
    reload();
  } catch (e) { toast(e.message, "err"); }
}

function peopleRoute() {
  return {
    async render(get) {
      const data = await get("/admin/participants");
      const ps = data.participants, as = data.assignments;
      const noFio = ps.filter((p) => !p.fio).length;
      const head = `<div class="row between"><div class="stack" style="gap:4px"><h1 class="h1">Участники</h1>
          <span class="small muted">${ps.length} ${plural(ps.length, "человек", "человека", "человек")}${noFio ? ` · ${noFio} без ФИО` : ""}</span></div>
        ${ps.length ? `<button class="btn sm" onclick="exportPeople()">${ic("download", 16, 2)}CSV</button>` : ""}</div>`;
      if (!ps.length) return head + emptyState("users", "Участников пока нет", "Они появятся, когда кто-нибудь откроет бота.");
      const legend = as.length ? `<div class="people-legend">${as.map((a, i) => `<span><b class="mono" style="color:var(--text)">${i + 1}</b> ${esc(a.title)}</span>`).join("")}</div>` : "";
      const cards = ps.map((p) => {
        const total = as.reduce((s, a) => s + (p.assignments[a.id]?.best_score || 0), 0);
        const key = [p.name, p.username, p.fio, p.tg_id].join(" ").toLowerCase();
        return `<div class="card" style="padding:12px 14px 12px 12px" data-key="${esc(key)}">
          <div class="row"><span class="avatar plain" style="width:40px;height:40px;font-size:13px">${esc(initial(p.name))}</span>
            <span class="grow stack" style="gap:0"><span class="h3 ellipsis">${esc(p.name)} <span class="small muted" style="font-weight:400">${p.username ? "@" + esc(p.username) : ""}</span></span>
              <span class="small ${p.fio ? "muted" : "c-bad"}">${p.fio ? esc(p.fio) : "ФИО не указано"}</span></span>
            <span class="num c-accent" style="font-size:14px">${total}</span></div>
          ${as.length ? `<div class="score-chips">${as.map((a, i) => { const x = p.assignments[a.id];
            return `<span class="${x ? (x.best_score / x.max_score >= 0.8 ? "good" : "warn") : ""}" title="${esc(a.title)}">${x ? x.best_score : "—"}</span>`; }).join("")}</div>` : ""}</div>`;
      }).join("");
      return `${head}<label class="row input" style="gap:10px;color:var(--muted)">${ic("search", 18, 2)}
          <input class="grow" style="border:0;background:none;outline:none;height:100%" placeholder="Ник, @username или ФИО" value="${esc(SEARCH.people)}"
            oninput="SEARCH.people=this.value;filterRows('#people',this.value)"></label>${legend}
        <div class="list-grid" id="people">${cards}</div>`;
    },
    after() { if (SEARCH.people) filterRows("#people", SEARCH.people); },
  };
}
function exportPeople() {
  const data = CACHE.get("/admin/participants");
  if (!data) return;
  const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const rows = [["tg_id", "username", "nickname", "fio", ...data.assignments.map((a) => a.title)].map(q).join(",")];
  for (const p of data.participants)
    rows.push([p.tg_id, p.username, p.name, p.fio, ...data.assignments.map((a) => p.assignments[a.id]?.best_score ?? "")].map(q).join(","));
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob(["\ufeff" + rows.join("\n")], { type: "text/csv" }));
  a.download = "participants.csv"; a.click();
  hap("success");
}

let MANAGE_KIND = "a";
function manageRoute() {
  return {
    async render(get) {
      const isA = MANAGE_KIND === "a";
      const seg = `<div class="seg"><button class="${isA ? "on" : ""}" onclick="MANAGE_KIND='a';hap('select');reload()">Задания</button>
        <button class="${isA ? "" : "on"}" onclick="MANAGE_KIND='h';hap('select');reload()">Хакатоны</button></div>`;
      const add = `<button class="btn primary block" onclick="show(${isA ? "assignmentFormRoute(null)" : "hackFormRoute(null)"})">${ic("plus", 18, 2.2)}${isA ? "Новое задание" : "Новый хакатон"}</button>`;
      let cards;
      if (isA) {
        const list = await get("/assignments");
        cards = list.length ? list.map((a) => {
          const st = !a.published ? ["accent", `Запланировано · ${dm(a.publish_at)}`] : a.is_active ? ["good", "Открыто"] : ["", "Закрыто"];
          return `<div class="card" style="padding:14px 14px 12px 16px">
            <div class="row" style="align-items:flex-start"><span class="grow stack" style="gap:4px"><span class="h3" style="font-size:16px">${esc(a.title)}</span>
              <span class="small muted">${a.deadline ? "до " + msk(a.deadline) : "без дедлайна"} · ${partsLine(a)}</span></span>
              <span class="chip ${st[0]}">${st[1]}</span></div>
            <div class="row wrap" style="gap:6px">
              <button class="btn xs" style="background:var(--bg)" onclick="ADMIN_AID=${a.id};SELECTED_SID=null;openTab('subs')">Сдачи</button>
              <button class="btn xs" style="background:var(--bg)" onclick="download('/admin/assignments/${a.id}/export.csv','results_${a.id}.csv')">CSV</button>
              <button class="btn xs" style="background:var(--bg)" onclick="show(assignmentFormRoute(${a.id}))">${ic("edit", 14, 2)}Изменить</button>
              <span class="grow"></span>
              <button class="row small muted" style="background:none;border:0;gap:8px;padding:0" onclick="toggleActive(${a.id}, ${!a.is_active}, this)" aria-label="Приём работ">
                приём<span class="switch${a.is_active ? " on" : ""}"></span></button></div></div>`;
        }).join("") : "";
        if (!cards) cards = emptyState("tasks", "Заданий нет", "Создай первое — участники получат уведомление.");
      } else {
        const list = await get("/hackathons");
        const S = { running: ["solid", "Идёт"], upcoming: ["accent", "Запланирован"], finished: ["", "Завершён"] };
        cards = list.length ? [...list].reverse().map((h) => `<div class="card" style="padding:14px 14px 12px 16px">
            <div class="row" style="align-items:flex-start"><span class="grow stack" style="gap:4px"><span class="h3" style="font-size:16px">${esc(h.title)}</span>
              <span class="small muted">${msk(h.starts_at)} – ${msk(h.ends_at)} · ${esc(h.metric_title)}</span></span>
              <span class="chip ${S[h.status][0]}">${S[h.status][1]}</span></div>
            <div class="row wrap" style="gap:6px">
              <button class="btn xs" style="background:var(--bg)" onclick="show(hackAllSubsRoute(${h.id}))">Решения</button>
              <button class="btn xs" style="background:var(--bg)" onclick="show(hackRoute(${h.id}))">Лидерборд</button>
              <button class="btn xs" style="background:var(--bg)" onclick="show(hackFormRoute(${h.id}))">${ic("edit", 14, 2)}Изменить</button>
              ${h.is_active ? "" : `<span class="chip warn">скрыт</span>`}</div></div>`).join("")
          : emptyState("trophy", "Хакатонов нет", "Создай хакатон — участники получат анонс.");
      }
      return `<h1 class="h1">Управление</h1>${seg}${add}<div class="list-grid">${cards}</div>`;
    },
  };
}
async function toggleActive(id, active, btn) {
  const sw = btn.querySelector(".switch");
  sw.classList.toggle("on", active); // сразу показываем, сервер догонит
  hap("select");
  try { await api(`/admin/assignments/${id}`, json("PATCH", { is_active: active })); toast(active ? "Приём работ открыт" : "Приём работ закрыт"); reload(); }
  catch (e) { sw.classList.toggle("on", !active); toast(e.message, "err"); }
}

function fileRow(name, label, accept, current, required, hidden = false) {
  return `<label class="file-row${hidden ? " hidden-file" : ""}">
    <span class="dot">${ic(hidden ? "lock" : "file", 18, 2)}</span>
    <span class="grow stack" style="gap:0"><span class="small muted">${label}</span>
      <span class="mono small ellipsis" id="fn-${name}" style="font-size:13px">${current ? esc(current) : required ? "выбери файл" : "необязательно"}</span></span>
    <span class="btn xs" style="background:var(--bg)">${current ? "Заменить" : "Выбрать"}</span>
    <input type="file" name="${name}" accept="${accept}" ${required && !current ? "required" : ""} onchange="pickFile(this,'#fn-${name}')"></label>`;
}

function assignmentFormRoute(id) {
  return {
    fresh: true,
    async render(get) {
      const a = id ? (await get("/assignments")).find((x) => x.id === id) : null;
      if (id && !a) throw new Error("Задание не найдено");
      const parts = a ? partsOf(a) : ["notebook"];
      const quiz = a?.quiz ? await get(`/admin/assignments/${id}/quiz`) : null;
      QB = { attempts: quiz?.attempts || 1, questions: (quiz?.questions || []).map(qbFrom), had: !!quiz };
      if (!QB.questions.length) QB.questions.push(qbBlank());
      await needMath(...quizTexts(QB.questions));
      const hasNb = parts.includes("notebook");
      const later_ = !!a?.publish_at && !a.published;
      const label = a ? "Сохранить" : "Создать задание";
      const partBtn = (key, icon, title, sub, on, locked) => `<button type="button" class="part-toggle${on ? " on" : ""}" data-part="${key}" ${locked ? 'aria-disabled="true"' : ""}>
          <span class="part-ic">${ic(icon, 22, 2)}</span>
          <span class="grow stack" style="gap:0"><span class="h3">${title}</span><span class="small muted">${sub}</span></span>
          <span class="switch${on ? " on" : ""}"></span></button>`;
      return `${topbar(a ? "Изменить задание" : "Новое задание", a ? esc(a.title) : "")}
        <form id="a-form" class="stack narrow" style="gap:12px" novalidate oninput="this.setAttribute('data-dirty','')">
          <label class="field"><span>Название</span><input class="input" name="title" value="${esc(a?.title || "")}" placeholder="Например, Линейные модели"></label>
          <label class="field"><span>Описание для участников</span><textarea class="textarea" name="description" rows="3">${esc(a?.description || "")}</textarea></label>
          <div class="field"><span>Из чего состоит задание</span>
            <div class="parts-pick">
              ${partBtn("quiz", "quiz", "Тест", "вопросы с вариантами или своим ответом", parts.includes("quiz"), false)}
              ${partBtn("notebook", "notebook", "Ноутбук", hasNb && a ? "уже в задании, убрать нельзя" : "шаблон .ipynb, проверяет агент", hasNb, hasNb && !!a)}
            </div></div>
          <label class="field"><span>Дедлайн, МСК</span><input class="input" name="deadline" type="datetime-local" value="${toInput(a?.deadline)}"></label>
          <button type="button" class="toggle-row" onclick="toggleLater(this)">
            <span class="grow stack" style="gap:0"><span class="h3" style="font-size:14px">Опубликовать позже</span><span class="small muted">участники получат уведомление в это время</span></span>
            <span class="switch${later_ ? " on" : ""}"></span></button>
          <label class="field${later_ ? "" : " hide"}" id="publish-field"><span>Публикация, МСК</span><input class="input" name="publish_at" type="datetime-local" value="${toInput(a?.publish_at)}"></label>

          <section class="form-sec${parts.includes("quiz") ? "" : " hide"}" id="sec-quiz">
            <div class="row between" style="align-items:baseline"><span class="h2 row" style="gap:8px">${ic("quiz", 20, 2)}Тест</span><span class="small muted" id="qb-sum"></span></div>
            ${qbHtml(a?.quiz ? id : null)}</section>

          <section class="form-sec${hasNb ? "" : " hide"}" id="sec-nb">
            <div class="row between" style="align-items:baseline"><span class="h2 row" style="gap:8px">${ic("notebook", 20, 2)}Ноутбук</span>${hasNb && a ? `<span class="small muted">${a.task_count} ${plural(a.task_count, "задание", "задания", "заданий")}</span>` : ""}</div>
            <label class="field"><span>Попыток на ноутбук</span><input class="input" name="max_attempts" type="number" min="1" value="${hasNb ? a?.max_attempts ?? "" : ""}" placeholder="по умолчанию"></label>
            ${fileRow("template", hasNb && a ? "Новый шаблон — только если нужно заменить" : "Шаблон для участников (.ipynb)", ".ipynb", "", false)}
            ${fileRow("reference", hasNb && a ? "Новый эталон — только если нужно заменить" : "Эталонное решение (.ipynb)", ".ipynb", "", false)}</section>
          <div id="a-result"></div>
          <div class="cta"><button class="btn primary" type="submit" id="a-save" data-label="${label}">${later_ ? "Сохранить и запланировать" : label}</button></div></form>`;
    },
    after() {
      qbSummary();
      qbBind();
      $$(".part-toggle").forEach((b) => b.addEventListener("click", () => {
        if (b.getAttribute("aria-disabled") === "true") { hap("warning"); toast("Ноутбук нельзя убрать из уже созданного задания", "err"); return; }
        const on = !b.classList.contains("on");
        b.classList.toggle("on", on);
        b.querySelector(".switch").classList.toggle("on", on);
        const sec = $(b.dataset.part === "quiz" ? "#sec-quiz" : "#sec-nb");
        sec.classList.toggle("hide", !on);
        if (on) { sec.classList.remove("enter"); void sec.offsetWidth; sec.classList.add("enter"); }
        $("#a-form").setAttribute("data-dirty", "");
        hap("select");
      }));
      $("#a-form").onsubmit = async (ev) => {
        ev.preventDefault();
        const f = ev.target;
        const fd = new FormData(f);
        const quizOn = $('[data-part="quiz"]').classList.contains("on");
        const nbOn = $('[data-part="notebook"]').classList.contains("on");
        const a = id ? CACHE.get("/assignments")?.find((x) => x.id === id) : null;
        const hadNb = !!a && partsOf(a).includes("notebook");
        if (!String(fd.get("title") || "").trim()) { toast("Укажи название задания", "err"); f.elements.title.focus(); return; }
        if (!quizOn && !nbOn) { toast("Выбери хотя бы одну часть: тест или ноутбук", "err"); return; }
        if (nbOn && !hadNb && !(fd.get("template")?.name && fd.get("reference")?.name)) { toast("Для ноутбука нужны оба файла: шаблон и эталон", "err"); return; }
        if (quizOn) {
          const bad = qbValidate();
          if (bad) { toast(bad.msg, "err"); if (bad.i >= 0) qbFlag(bad.i); return; }
          fd.set("quiz", JSON.stringify(qbPayload()));
        } else if (QB.had) fd.set("quiz", "null");
        if (!nbOn || !fd.get("max_attempts")) fd.delete("max_attempts");
        for (const k of ["template", "reference"]) if (!nbOn || !fd.get(k)?.name) fd.delete(k);
        const pubHidden = $("#publish-field").classList.contains("hide");
        if (id) {
          // При правке пустое поле значит «убрать»: сервер ждёт для этого "null"
          if (!fd.get("deadline")) fd.set("deadline", "null");
          if (pubHidden || !fd.get("publish_at")) fd.set("publish_at", "null");
        } else {
          if (!fd.get("deadline")) fd.delete("deadline");
          if (pubHidden || !fd.get("publish_at")) fd.delete("publish_at");
        }
        const btn = $("#a-save"); btn.disabled = true;
        const old = btn.innerHTML; btn.innerHTML = `<span class="spinner"></span>Сохраняем…`;
        try {
          const r = await api(id ? `/admin/assignments/${id}/edit` : "/admin/assignments", { method: "POST", body: fd });
          f.removeAttribute("data-dirty");
          $$(".file-row", f).forEach((x) => x.removeAttribute("data-dirty"));
          const regraded = QB.had && quizOn;
          QB.had = quizOn;
          const tasks = r.tasks?.length ? `<span class="h3">Задания в ноутбуке</span><ol style="margin:4px 0;padding-left:20px">${r.tasks.map((t) => `<li>${esc(t.title)}</li>`).join("")}</ol>` : "";
          const qline = quizOn ? `<span class="h3">Тест: ${QB.questions.length} ${qWord(QB.questions.length)}, ${qbPoints()} ${ptsWord(qbPoints())}</span>` : "";
          const warn = r.warnings?.length ? `<span class="small c-warn">${r.warnings.map(esc).join("<br>")}</span>` : "";
          if (!id) {
            toast(fd.get("publish_at") ? "Задание запланировано" : "Задание создано, участникам ушло уведомление");
            $("#a-result").innerHTML = `<div class="card tight soft-accent">${qline}${tasks}${warn}</div>`;
            setTimeout(() => { if (stack.length > 1) goBack(); }, warn ? 4000 : 1600);
          } else {
            toast(regraded ? "Сохранено, попытки теста пересчитаны" : "Сохранено");
            $("#a-result").innerHTML = r.files_changed ? `<div class="card tight soft-warn"><span class="row" style="align-items:flex-start">${ic("warn", 18, 2)}
              <span class="grow">Файлы ноутбука заменены, старые оценки остались прежними. Заданий в ноутбуке: ${r.tasks.length}.</span></span>${warn}
              <button type="button" class="btn sm dark" onclick="recheckAll(${id}, this)">${ic("refresh", 16, 2)}Перепроверить все сдачи</button></div>` : warn;
          }
        } catch (e) {
          toast(e.message, "err");
          const m = /^Вопрос (\d+)/.exec(e.message);
          if (m) qbFlag(+m[1] - 1);
        } finally { btn.disabled = false; btn.innerHTML = old; }
      };
    },
  };
}
function toggleLater(btn) {
  const sw = btn.querySelector(".switch");
  const on = !sw.classList.contains("on");
  sw.classList.toggle("on", on);
  $("#publish-field").classList.toggle("hide", !on);
  $("#a-save").textContent = on ? "Сохранить и запланировать" : $("#a-save").dataset.label || "Сохранить";
  hap("select");
}
async function recheckAll(id, btn) {
  if (!confirm("Перепроверить все сдачи по заданию? Участники получат новые отзывы.")) return;
  btn.disabled = true;
  try { const r = await api(`/admin/assignments/${id}/recheck`, { method: "POST" }); toast(`Отправлено на перепроверку: ${r.queued}`); }
  catch (e) { toast(e.message, "err"); btn.disabled = false; }
}

let METRICS = null;
function hackFormRoute(id) {
  return {
    fresh: true,
    async render(get) {
      METRICS = METRICS || (await get("/metrics"));
      const h = id ? await get(`/hackathons/${id}`) : null;
      const v = (k, d = "") => esc(h ? h[k] ?? d : d);
      const cur = h?.metric || METRICS[0]?.name;
      const m = METRICS.find((x) => x.name === cur);
      return `${topbar(h ? "Изменить хакатон" : "Новый хакатон", h ? esc(h.title) : "")}
        <form id="h-form" class="stack narrow" style="gap:12px" oninput="this.setAttribute('data-dirty','')">
          <label class="field"><span>Название</span><input class="input" name="title" required value="${v("title")}"></label>
          <label class="field"><span>Условие текстом${h ? "" : " (и/или файлом .md ниже)"}</span><textarea class="textarea" name="description" rows="4">${v("description")}</textarea></label>
          ${fileRow("task", "Условие файлом (.md / .txt), добавится к тексту", ".md,.txt", h?.files.task, false)}
          <div class="field-grid">
            <label class="field"><span>Старт, МСК</span><input class="input" name="starts_at" type="datetime-local" required value="${h ? toInput(h.starts_at) : ""}"></label>
            <label class="field"><span>Конец, МСК</span><input class="input" name="ends_at" type="datetime-local" required value="${h ? toInput(h.ends_at) : ""}"></label></div>
          <div class="field"><span>Метрика · <b id="m-hint">${m ? (m.higher_is_better ? "больше — лучше" : "меньше — лучше") : ""}</b></span>
            <input type="hidden" name="metric" value="${esc(cur)}">
            <div class="row wrap" style="gap:6px">${METRICS.map((x) => `<button type="button" class="pill sm${x.name === cur ? " on" : ""}" data-metric="${esc(x.name)}" data-hib="${x.higher_is_better ? 1 : 0}">${esc(x.title)}</button>`).join("")}</div></div>
          <div class="field-grid three">
            <label class="field"><span>id-колонка</span><input class="input mono" name="id_column" required value="${v("id_column", "id")}"></label>
            <label class="field"><span>ответ</span><input class="input mono" name="target_column" required value="${v("target_column", "target")}"></label>
            <label class="field"><span>в день</span><input class="input mono" name="daily_limit" type="number" min="1" value="${v("daily_limit", 5)}"></label></div>
          ${fileRow("train", "train.csv — с ответами", ".csv,.zip", h?.files.train, !h)}
          ${fileRow("test", "test.csv — без ответов", ".csv", h?.files.test, !h)}
          ${fileRow("solution", "Ответы на test — скрыты, по ним считается метрика", ".csv", h?.files.solution, !h, true)}
          ${fileRow("baseline", "Бейзлайн (.ipynb / .py)", "", h?.files.baseline, false)}
          ${h ? `<button type="button" class="toggle-row" onclick="this.querySelector('.switch').classList.toggle('on');hap('select')" id="h-active">
            <span class="grow stack" style="gap:0"><span class="h3" style="font-size:14px">Виден участникам</span><span class="small muted">выключи, чтобы спрятать хакатон</span></span>
            <span class="switch${h.is_active ? " on" : ""}"></span></button>` : ""}
          <span class="small muted" style="padding:0 6px">Время — по Москве. ${h ? "Если заменить ответы, метрику или колонки, все решения пересчитаются." : "Участники получат анонс сразу, а в момент старта — сообщение, что хакатон начался."}</span>
          <div class="cta"><button class="btn primary" type="submit" id="h-save">${h ? "Сохранить" : "Создать хакатон"}</button></div></form>`;
    },
    after() {
      $$("[data-metric]").forEach((b) => b.addEventListener("click", () => {
        $$("[data-metric]").forEach((x) => x.classList.toggle("on", x === b));
        $('#h-form [name="metric"]').value = b.dataset.metric;
        $("#m-hint").textContent = b.dataset.hib === "1" ? "больше — лучше" : "меньше — лучше";
        $("#h-form").setAttribute("data-dirty", "");
        hap("select");
      }));
      $("#h-form").onsubmit = async (ev) => {
        ev.preventDefault();
        const f = ev.target;
        const fd = new FormData(f);
        for (const k of ["train", "test", "solution", "baseline", "task"]) if (!fd.get(k)?.name) fd.delete(k);
        if (id) fd.set("is_active", $("#h-active .switch").classList.contains("on") ? "true" : "false");
        const btn = $("#h-save"); btn.disabled = true;
        const old = btn.innerHTML; btn.innerHTML = `<span class="spinner"></span>Сохраняем…`;
        try {
          const r = await api(id ? `/admin/hackathons/${id}/edit` : "/admin/hackathons", { method: "POST", body: fd });
          f.removeAttribute("data-dirty");
          $$(".file-row", f).forEach((x) => x.removeAttribute("data-dirty"));
          toast(id ? `Сохранено${r.rescored ? `, пересчитано решений: ${r.rescored}` : ""}` : "Хакатон создан");
          goBack();
        } catch (e) { toast(e.message, "err"); btn.disabled = false; btn.innerHTML = old; }
      };
    },
  };
}

function hackAllSubsRoute(id) {
  return {
    async render(get) {
      const [h, subs] = await Promise.all([get(`/hackathons/${id}`), get(`/admin/hackathons/${id}/submissions`)]);
      return `${topbar("Все решения", esc(h.title))}
        ${subs.length ? `<div class="list-grid">${[...subs].reverse().map((x) => `<div class="capsule round">
          <span class="avatar sm plain">${esc(initial(x.user.name))}</span>
          <span class="grow stack" style="gap:1px"><span class="h3 ellipsis" style="font-size:14px">${esc(x.user.name)} <span class="small muted" style="font-weight:400">${esc(x.user.fio || "ФИО —")}</span></span>
            <span class="small muted mono">№${x.number} · id ${x.id} · ${when(x.created_at)}</span>
            ${x.status !== "done" ? `<span class="small c-bad">${esc(x.error)}</span>` : ""}</span>
          <span class="mono small ${x.status === "done" ? "" : "c-bad"}" style="font-size:13px">${x.status === "done" ? num(x.score) : "ошибка"}</span></div>`).join("")}</div>`
          : emptyState("trophy", "Решений пока нет", "Здесь появятся все отправки участников.")}
        <button class="btn sm" onclick="download('/hackathons/${id}/files/solution','solution.csv')">${ic("lock", 16, 2)}Скачать ответы</button>`;
    },
  };
}

// =====================================================================
// ВКЛАДКИ И ЗАПУСК
// =====================================================================
function tabs() {
  if (ME.is_admin) return [
    ["home", "Задания", "tasks", homeRoute], ["hack", "Хакатоны", "trophy", hackListRoute],
    ["subs", "Сдачи", "review", adminSubsRoute], ["people", "Участники", "users", peopleRoute], ["manage", "Управление", "sliders", manageRoute],
  ];
  return [["home", "Задания", "tasks", homeRoute], ["hack", "Хакатоны", "trophy", hackListRoute],
    ["mine", "Сдачи", "history", historyRoute], ["profile", "Профиль", "user", profileRoute]];
}
let CUR_TAB = "home";
function renderTabs() {
  const t = tabs();
  $("#tabs").innerHTML = `<div class="brand"><span class="avatar sm">D</span><b>Домашки DSC</b></div>` +
    t.map(([k, label, icon]) => `<button class="tab${k === CUR_TAB ? " on" : ""}" data-tab="${k}" aria-label="${label}" onclick="openTab('${k}')">${ic(icon)}<span class="lbl">${label}</span></button>`).join("") +
    (ME.is_admin ? `<span class="spacer"></span><button class="tab wide-only${CUR_TAB === "profile" ? " on" : ""}" data-tab="profile" aria-label="Профиль" onclick="openProfileTab()">${ic("user")}<span class="lbl">Профиль</span></button>` : "");
}
function openProfileTab() { CUR_TAB = "profile"; markTabs(); stack = []; show(profileRoute()); }
function markTabs() { $$("#tabs .tab").forEach((b) => b.classList.toggle("on", b.dataset.tab === CUR_TAB)); }
function openTab(key) {
  const tab = tabs().find((t) => t[0] === key);
  if (!tab) return;
  if (key !== CUR_TAB) hap("select");
  CUR_TAB = key;
  markTabs();
  stack = [];
  show(tab[3]());
}

(async () => {
  applyTheme();
  tg?.ready?.();
  tg?.expand?.();
  try { tg?.disableVerticalSwipes?.(); } catch {}
  tg?.onEvent?.("themeChanged", () => themePref() === "tg" && applyTheme());
  try {
    ME = await api("/me");
    TZ = ME.timezone || TZ;
    renderTabs();
    openTab("home");
  } catch (e) {
    paint(errorState(e, false), "fade");
    $("#view").querySelector(".btn")?.setAttribute("onclick", "location.reload()");
  }
})();
