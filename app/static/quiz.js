"use strict";
// Тесты: разметка с формулами (KaTeX), выбор части задания, прохождение, результат,
// конструктор теста и проверка попыток организатором. Подключается после app.js и пользуется его помощниками.

Object.assign(I, {
  quiz: "M10 6h10M10 12h10M10 18h10M3.5 6l1.5 1.5L7.5 5M3.5 12l1.5 1.5 2.5-2.5M3.5 18l1.5 1.5 2.5-2.5",
  notebook: "M6 3h12a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H6zM9.5 3v18M13 8h3M13 12h3",
  up: "M12 19V5M5 12l7-7 7 7",
  down: "M12 5v14M19 12l-7 7-7-7",
  trash: "M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v5M14 11v5",
  next: "M5 12h14M13 6l6 6-6 6",
  prev: "M19 12H5M11 6l-6 6 6 6",
});

// =====================================================================
// РАЗМЕТКА И ФОРМУЛЫ
// =====================================================================
let KATEX_P = null;
function loadKatex() {
  if (window.katex) return Promise.resolve(true);
  if (!KATEX_P) {
    KATEX_P = new Promise((resolve) => {
      const css = document.createElement("link");
      css.rel = "stylesheet"; css.href = "/static/vendor/katex/katex.min.css";
      const js = document.createElement("script");
      js.src = "/static/vendor/katex/katex.min.js"; js.async = true;
      let n = 0;
      const done = () => { if (++n === 2) resolve(!!window.katex); };
      css.onload = css.onerror = done; js.onload = js.onerror = done;
      document.head.append(css, js);
    }).then(async (ok) => {
      if (!ok) { KATEX_P = null; return false; }
      // Основные шрифты заранее, чтобы формулы не «прыгали» при первой отрисовке
      try { await Promise.race([Promise.all(["1em KaTeX_Main", "italic 1em KaTeX_Math"].map((f) => document.fonts.load(f))), sleep(1500)]); } catch {}
      return true;
    });
  }
  // Не держим экран дольше 5 секунд: без KaTeX формулы покажутся исходным текстом
  return Promise.race([KATEX_P, sleep(5000).then(() => false)]);
}
const HAS_MATH = /\$|\\\(|\\\[/;
async function needMath(...texts) {
  if (texts.some((t) => t && HAS_MATH.test(t))) await loadKatex();
}

const TEX_CACHE = new Map();
function tex(src, display) {
  const key = (display ? "D" : "I") + src;
  let html = TEX_CACHE.get(key);
  if (!html && window.katex) {
    try {
      html = katex.renderToString(src, { displayMode: display, throwOnError: false, strict: "ignore", trust: false, maxExpand: 300 });
      TEX_CACHE.set(key, html);
    } catch { html = null; }
  }
  if (!html) html = `<code class="tex">${esc(display ? `$$${src}$$` : `$${src}$`)}</code>`;
  return display ? `<span class="math-block">${html}</span>` : `<span class="math">${html}</span>`;
}

function mdCode(code, lang) {
  const py = !lang || /^(py|python|python3|ipython)$/i.test(lang);
  const lines = String(code).replace(/\s+$/, "").split("\n");
  return `<div class="code md-code${lines.length < 3 ? " no-ln" : ""}"><pre>${lines.map((l) => `<span class="ln">${(py ? hl(l) : esc(l)) || " "}</span>`).join("")}</pre></div>`;
}

function mdInline(s) {
  return s
    .replace(/\*\*(?=\S)([^*]+?)\*\*/g, "<b>$1</b>")
    .replace(/(^|[^\w])__(?=\S)([^_]+?)__(?!\w)/g, "$1<b>$2</b>")
    .replace(/(^|[^*\w])\*(?=\S)([^*\n]+?)\*(?![*\w])/g, "$1<i>$2</i>")
    .replace(/(^|[^\w])_(?=\S)([^_\n]+?)_(?!\w)/g, "$1<i>$2</i>")
    .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}

// Markdown организатора → безопасный HTML. Поддерживаем то, что встречается в вопросах:
// абзацы, списки, жирный/курсив, `код`, блоки кода, ссылки и формулы $…$, $$…$$, \(…\), \[…\].
function md(src, inline = false) {
  if (src === null || src === undefined || src === "") return "";
  const keep = [];
  const put = (html) => `\u0001${keep.push(html) - 1}\u0002`;
  const lines = String(src).replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(/^\s*(```+|~~~+)\s*([\w+#.-]*)/);
    if (!m) { out.push(lines[i]); continue; }
    const body = [];
    for (i++; i < lines.length && !lines[i].trim().startsWith(m[1].slice(0, 3)); i++) body.push(lines[i]);
    out.push(put(mdCode(body.join("\n"), m[2])));
  }
  let s = out.join("\n");
  s = s.replace(/`([^`\n]+)`/g, (_, c) => put(`<code>${esc(c)}</code>`));
  s = s.replace(/\$\$([\s\S]+?)\$\$/g, (_, t) => put(tex(t.trim(), !inline)));
  s = s.replace(/\\\[([\s\S]+?)\\\]/g, (_, t) => put(tex(t.trim(), !inline)));
  s = s.replace(/\\\((.+?)\\\)/g, (_, t) => put(tex(t, false)));
  s = s.replace(/(^|[^\\$])\$(?!\s)([^$\n]+?)\$(?!\d)/g, (all, pre, t) => (/\s$/.test(t) ? all : pre + put(tex(t, false))));
  s = esc(s).replace(/\\\$/g, "$");
  const restore = (x) => x.replace(/\u0001(\d+)\u0002/g, (_, n) => keep[+n]);
  if (inline) return restore(mdInline(s).replace(/\n/g, "<br>"));

  const blocks = [];
  let para = [], list = null;
  const flushPara = () => { if (para.length) blocks.push(`<div class="p">${mdInline(para.join("\n")).replace(/\n/g, "<br>")}</div>`); para = []; };
  const flushList = () => { if (list) blocks.push(`<${list.tag}>${list.items.map((x) => `<li>${mdInline(x)}</li>`).join("")}</${list.tag}>`); list = null; };
  for (const line of s.split("\n")) {
    const t = line.trim();
    let m;
    if (!t) { flushPara(); flushList(); continue; }
    if (/^\u0001\d+\u0002$/.test(t)) { flushPara(); flushList(); blocks.push(t); continue; }
    if ((m = t.match(/^[-*+]\s+(.*)$/)) || (m = t.match(/^\d{1,3}[.)]\s+(.*)$/))) {
      const tag = /^\d/.test(t) ? "ol" : "ul";
      flushPara();
      if (!list || list.tag !== tag) { flushList(); list = { tag, items: [] }; }
      list.items.push(m[1]);
      continue;
    }
    if (list && /^\s{2,}/.test(line)) { list.items[list.items.length - 1] += " " + t; continue; }
    flushList();
    if ((m = t.match(/^#{1,6}\s+(.*)$/))) { flushPara(); blocks.push(`<div class="md-h">${mdInline(m[1])}</div>`); continue; }
    if ((m = t.match(/^&gt;\s?(.*)$/))) { flushPara(); blocks.push(`<blockquote>${mdInline(m[1])}</blockquote>`); continue; }
    para.push(t);
  }
  flushPara(); flushList();
  return restore(blocks.join(""));
}
// Первая строка условия — для списков и заголовков аккордеонов
function mdLine(src) {
  const text = String(src || "").split(/\n\s*\n|```|~~~/)[0].replace(/\$\$([\s\S]+?)\$\$/g, (_, t) => `$${t.trim()}$`);
  return md(text.replace(/\s*\n\s*/g, " "), true);
}
// Пока открыто вступление, в свободное время грузим KaTeX и заранее рисуем формулы по одному вопросу за раз
function warmQuiz(questions) {
  const idle = window.requestIdleCallback || ((f) => setTimeout(f, 50));
  if (!quizTexts(questions).some((t) => t && HAS_MATH.test(t))) return;
  idle(async () => {
    if (!(await loadKatex())) return;
    let i = 0;
    const next = () => idle(() => {
      const q = questions[i++];
      if (!q) return;
      const html = md(q.text) + (q.options || []).map((o) => md(o.text, true)).join("");
      // Первый вопрос ещё и вставляем невидимым: браузер заранее подгрузит шрифты формул и посчитает стили
      if (i === 1) {
        const ghost = document.createElement("div");
        ghost.className = "card q-card md";
        ghost.style.cssText = "position:fixed;left:-10000px;top:0;width:358px;pointer-events:none";
        ghost.setAttribute("aria-hidden", "true");
        ghost.innerHTML = html;
        document.body.append(ghost);
        setTimeout(() => ghost.remove(), 1500);
      }
      next();
    });
    next();
  });
}
const quizTexts = (qs) => qs.flatMap((q) => [q.text, q.explanation, ...(q.options || []).map((o) => o.text), ...(q.answers || [])]);

// =====================================================================
// УЧАСТНИК
// =====================================================================
const partsOf = (a) => a.parts || ["notebook"];
const isClosed = (a) => !a.is_active || (a.deadline && new Date(a.deadline) < new Date());
const qWord = (n) => plural(n, "вопрос", "вопроса", "вопросов");
const ptsWord = (n) => plural(n, "балл", "балла", "баллов");
const attWord = (n) => plural(n, "попытки", "попыток", "попыток");

// Задание из двух частей: сначала выбираем часть
function partsChooserHtml(a, subs) {
  const mine = subs.filter((s) => s.assignment_id === a.id);
  const closed = isClosed(a);
  const pendingNb = mine.some((s) => (s.kind || "notebook") === "notebook" && (s.status === "queued" || s.status === "checking"));
  const card = (n, key, icon, title, meta, best, max, status) => {
    const k = best === null ? (status[0] === "accent" ? "accent" : "neutral") : scoreKind(best, max);
    const pct = best === null ? 0 : Math.round((best / max) * 100);
    return `<button class="part press" onclick="hap();show(assignmentRoute(${a.id},'${key}'))">
      <span class="row" style="gap:14px;align-items:flex-start">
        <span class="part-ic k-${k}">${ic(icon, 26, 1.9)}</span>
        <span class="grow stack" style="gap:2px"><span class="small muted">Часть ${n}</span><span class="h2">${title}</span><span class="small muted">${meta}</span></span>
        <span class="chev">${ic("chev", 18)}</span></span>
      <span class="row between" style="align-items:flex-end">
        <span class="chip big ${status[0]}">${status[1]}</span>
        <span class="part-score">${best === null ? `<small>из ${max}</small>` : `<b class="c-${k}">${best}</b><small>/${max}</small>`}</span></span>
      <span class="part-bar"><i class="${k}" style="transform:scaleX(${pct / 100})"></i></span></button>`;
  };
  const q = a.quiz, nb = a.notebook;
  const qLeft = q.max_attempts - q.attempts_used;
  const nbLeft = nb.max_attempts - nb.attempts_used;
  const qStatus = q.best_score !== null ? ["good", qLeft > 0 && !closed ? `пройден · ещё ${qLeft} ${attWord(qLeft)}` : "пройден"]
    : closed ? ["bad", "не пройден"] : hasDraft(a.id) ? ["accent", "начат"] : ["", "не начат"];
  const nbStatus = pendingNb ? ["accent", "проверяется"] : nb.best_score !== null ? ["good", "проверено"]
    : closed ? ["bad", "не сдан"] : ["", "не сдан"];
  const total = a.best_score;
  return `${topbar("Задание")}
    <h1 class="h1">${esc(a.title)}</h1>
    <div class="row wrap" style="gap:6px">
      ${a.deadline ? `<span class="chip big ${closed ? "bad" : "accent"}">${ic("clock", 14, 2)}${closed ? "закрыто" : "до"} ${msk(a.deadline)}</span>` : `<span class="chip big">без дедлайна</span>`}
      <span class="chip big">2 части · ${a.max_score} ${ptsWord(a.max_score)}</span>
      ${!a.published ? `<span class="chip big warn">появится ${msk(a.publish_at)}</span>` : ""}</div>
    ${a.description ? `<div class="card tight pre" style="font-size:14px">${esc(a.description)}</div>` : ""}
    <div class="parts">
      ${card(1, "quiz", "quiz", "Тест", `${q.questions} ${qWord(q.questions)} · ${q.attempts_used} из ${q.max_attempts} ${attWord(q.max_attempts)}`, q.best_score, q.max_score, qStatus)}
      ${card(2, "notebook", "notebook", "Ноутбук", `${nb.task_count} ${plural(nb.task_count, "задание", "задания", "заданий")} · ${nb.attempts_used} из ${nb.max_attempts} ${attWord(nb.max_attempts)}`, nb.best_score, nb.max_score, nbStatus)}
    </div>
    <div class="card tight row" style="flex-direction:row;align-items:center">
      <span class="grow stack" style="gap:0"><span class="h3">Итог задания</span><span class="small muted">лучший тест + лучший ноутбук</span></span>
      <span class="num" style="font-size:22px">${total ?? "—"}<span class="small muted">/${a.max_score}</span></span></div>`;
}

// ---------- черновик ответов (на устройстве) ----------
const draftKey = (aid) => `dsc-quiz-${ME?.id}-${aid}`;
function loadDraft(aid) {
  try { return JSON.parse(localStorage.getItem(draftKey(aid)) || "null"); } catch { return null; }
}
function saveDraft(aid, data) {
  try { localStorage.setItem(draftKey(aid), JSON.stringify({ ...data, ts: Date.now() })); } catch {}
}
function dropDraft(aid) { try { localStorage.removeItem(draftKey(aid)); } catch {} }
const isAnswered = (v) => (Array.isArray(v) ? v.length > 0 : v !== undefined && v !== null && String(v).trim() !== "");
function hasDraft(aid) {
  const d = loadDraft(aid);
  return !!d && Object.values(d.answers || {}).some(isAnswered);
}

// ---------- экран теста (вступление и попытки) ----------
async function quizPartHtml(a, subs, get, single) {
  const qv = await get(`/assignments/${a.id}/quiz`);
  const mine = subs.filter((s) => s.assignment_id === a.id && s.kind === "quiz");
  const closed = isClosed(a);
  const left_ = qv.max_attempts - qv.attempts_used;
  const n = qv.questions.length;
  const draft = loadDraft(a.id);
  const answered = draft ? qv.questions.filter((q) => isAnswered(draft.answers?.[q.id])).length : 0;
  const canTake = !closed && (left_ > 0 || ME.is_admin);
  const done = mine.filter((s) => s.status === "done");
  const best = done.length ? done.reduce((b, s) => (s.score > b.score ? s : b)) : null;
  const chips = `<div class="row wrap" style="gap:6px">
    ${a.deadline ? `<span class="chip big ${closed ? "bad" : "accent"}">${ic("clock", 14, 2)}${closed ? "закрыто" : "до"} ${msk(a.deadline)}</span>` : `<span class="chip big">без дедлайна</span>`}
    <span class="chip big">${n} ${qWord(n)} · ${qv.max_score} ${ptsWord(qv.max_score)}</span>
    <span class="chip big">${qv.attempts_used} из ${qv.max_attempts} ${attWord(qv.max_attempts)}</span>
    ${!a.published ? `<span class="chip big warn">появится ${msk(a.publish_at)}</span>` : ""}</div>`;
  const bestCard = best ? `<button class="card press quiz-best" onclick="hap();show(quizResultRoute(${best.id}))">
      <span class="row between" style="align-items:flex-end"><span class="stack" style="gap:2px"><span class="small muted">${done.length > 1 ? "Лучшая попытка" : "Результат"}</span>
        <span class="hero-num sm">${best.score}<small>/${best.max_score}</small></span></span>
        <span class="chip big ${scoreKind(best.score, best.max_score)}">${verdictLabel(best.score, best.max_score)}</span></span>
      ${qBar(best.result)}
      <span class="row between small"><span>${esc(best.result?.auto_comment || "")}</span><span class="row muted" style="gap:4px">разбор${ic("chev", 14)}</span></span></button>` : "";
  const rules = !mine.length && canTake ? `<div class="card tight rules">
      <span class="row">${ic("quiz", 18, 2)}<span>Вопросы идут по одному, к любому можно вернуться до отправки.</span></span>
      <span class="row">${ic("check", 18, 2)}<span>Ответы сохраняются на устройстве: можно закрыть приложение и продолжить.</span></span>
      <span class="row">${ic("eye", 18, 2)}<span>Баллы увидишь сразу. ${a.deadline ? `Правильные ответы и пояснения — после дедлайна, ${msk(a.deadline)}.` : "Правильные ответы организаторы покажут позже."}</span></span></div>` : "";
  const draftCard = canTake && answered ? `<div class="card tight soft-accent row" style="flex-direction:row;align-items:center;gap:12px">
      <span class="avatar sm">${answered}</span><span class="grow stack" style="gap:0"><span class="h3">Есть незаконченная попытка</span>
      <span class="small muted">отвечено ${answered} из ${n}, ответы сохранены</span></span></div>` : "";
  const attempts = mine.length ? `<div class="stack"><span class="h3">Мои попытки</span>${mine.map((s) => subRow(s)).join("")}</div>` : "";
  const keys = qv.keys_visible && !ME.is_admin ? `<button class="card tight press" style="border:0;text-align:left;flex-direction:row;align-items:center;gap:12px" onclick="hap();show(quizKeyRoute(${a.id}))">
      <span class="avatar plain" style="color:var(--good)">${ic("eye", 20, 2)}</span>
      <span class="grow stack" style="gap:0"><span class="h3">Правильные ответы</span><span class="small muted">Дедлайн прошёл — можно посмотреть ответы и пояснения</span></span>${ic("chev", 16)}</button>` : "";
  const label = answered ? `Продолжить · ${answered} из ${n}` : mine.length ? `Пройти ещё раз · попытка ${qv.attempts_used + 1} из ${qv.max_attempts}` : "Начать тест";
  const cta = canTake ? `<div class="cta"><button class="btn primary" onclick="hap('medium');show(quizTakeRoute(${a.id}))">${ic(answered ? "next" : "quiz", 18, 2)}${label}</button></div>`
    : !closed && left_ <= 0 ? `<div class="cta"><button class="btn" disabled>Попытки закончились</button></div>` : "";
  // KaTeX разбираем заранее, пока человек читает вступление: первый вопрос откроется без задержки
  if (canTake) warmQuiz(qv.questions);
  const head = single ? `${topbar("Задание")}<h1 class="h1">${esc(a.title)}</h1>` : `${topbar(esc(a.title))}<h1 class="h1">Тест</h1>`;
  return `${head}${chips}${single && a.description ? `<div class="card tight pre" style="font-size:14px">${esc(a.description)}</div>` : ""}
    ${bestCard}${draftCard}${rules}${attempts}${keys}${cta}`;
}
function qBar(r) {
  const qs = r?.questions || [];
  return `<div class="bar thick">${qs.map((q) => `<i class="${q.score >= q.points ? "good" : q.score > 0 ? "warn" : "bad"}"></i>`).join("")}</div>`;
}

// ---------- прохождение ----------
let QZ = null;
const QTYPE_HINT = { single: "один ответ", multiple: "несколько ответов", text: "впиши ответ", number: "впиши число" };
function quizTakeRoute(aid) {
  return {
    fresh: true,
    async render(get) {
      const [qv, list] = await Promise.all([get(`/assignments/${aid}/quiz`), get("/assignments")]);
      const a = list.find((x) => x.id === aid);
      if (!qv.questions.length) throw new Error("В тесте нет вопросов");
      await needMath(...quizTexts(qv.questions));
      const draft = loadDraft(aid);
      QZ = { aid, qv, a, answers: draft?.answers || {}, i: Math.min(draft?.i ?? 0, qv.questions.length) };
      return `${topbar("Тест", esc(a?.title || ""), `<span class="chip big" id="qz-count"></span>`)}
        <div class="quiz-layout">
          <aside class="quiz-side"><div class="qnav" id="qz-nav" role="tablist" aria-label="Вопросы"></div>
            <div class="quiz-side-meta small muted" id="qz-meta"></div></aside>
          <div class="quiz-main" id="qz-body"></div></div>
        <div class="cta" id="qz-cta"></div>`;
    },
    after() {
      qzPaint(null);
      const body = $("#qz-body");
      body.addEventListener("click", (e) => {
        const o = e.target.closest(".opt");
        if (o) { qzPick(o); return; }
        const j = e.target.closest("[data-go]");
        if (j) qzGo(+j.dataset.go);
      });
      body.addEventListener("input", (e) => {
        if (e.target.id !== "qz-input") return;
        const q = QZ.qv.questions[QZ.i];
        QZ.answers[q.id] = e.target.value;
        qzSave(); qzNav();
      });
      body.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && e.target.id === "qz-input") { e.preventDefault(); qzGo(QZ.i + 1); }
      });
      $("#qz-nav").addEventListener("click", (e) => { const b = e.target.closest("[data-go]"); if (b) qzGo(+b.dataset.go); });
    },
  };
}
function qzSave() { saveDraft(QZ.aid, { answers: QZ.answers, i: QZ.i }); }
function qzGo(i) {
  const n = QZ.qv.questions.length;
  i = Math.max(0, Math.min(n, i));
  if (i === QZ.i) return;
  const dir = i > QZ.i ? "push" : "pop";
  QZ.i = i;
  qzSave();
  hap("select");
  qzPaint(dir);
}
function qzPick(btn) {
  const q = QZ.qv.questions[QZ.i];
  const id = btn.dataset.o;
  if (q.type === "single") {
    QZ.answers[q.id] = QZ.answers[q.id] === id ? null : id;
  } else {
    const cur = new Set(QZ.answers[q.id] || []);
    cur.has(id) ? cur.delete(id) : cur.add(id);
    QZ.answers[q.id] = q.options.map((o) => o.id).filter((x) => cur.has(x));
  }
  const ans = QZ.answers[q.id];
  $$("#qz-body .opt").forEach((b) => {
    const on = q.type === "single" ? ans === b.dataset.o : (ans || []).includes(b.dataset.o);
    b.classList.toggle("on", on);
    b.setAttribute("aria-checked", on);
  });
  hap("select");
  qzSave(); qzNav();
}
function qzNav() {
  const qs = QZ.qv.questions;
  const n = qs.length;
  const answered = qs.filter((q) => isAnswered(QZ.answers[q.id])).length;
  $("#qz-nav").innerHTML = qs.map((q, k) => `<button class="${k === QZ.i ? "on" : isAnswered(QZ.answers[q.id]) ? "done" : ""}" data-go="${k}" role="tab"
      aria-selected="${k === QZ.i}" aria-label="Вопрос ${k + 1}">${k + 1}</button>`).join("") +
    `<button class="fin${QZ.i === n ? " on" : ""}" data-go="${n}" aria-label="Проверить ответы">${ic("check", 16, 2.4)}</button>`;
  $("#qz-count").textContent = QZ.i < n ? `${QZ.i + 1} / ${n}` : `${answered} / ${n}`;
  $("#qz-meta").textContent = `Отвечено ${answered} из ${n} · ответы сохраняются на устройстве`;
  // Прокрутку полосы номеров делаем после отрисовки кадра, чтобы не заставлять браузер считать раскладку заранее
  requestAnimationFrame(() => setTimeout(() => {
    const nav = $("#qz-nav"), on = nav?.querySelector(".on");
    if (!on || nav.scrollWidth <= nav.clientWidth) return;
    nav.scrollTo({ left: on.offsetLeft - nav.clientWidth / 2 + on.offsetWidth / 2, behavior: "smooth" });
  }, 0));
}
function qzPaint(dir) {
  const qs = QZ.qv.questions;
  const n = qs.length;
  const body = $("#qz-body");
  body.innerHTML = QZ.i < n ? qzQuestionHtml(qs[QZ.i], n) : qzReviewHtml();
  body.classList.remove("enter", "push", "pop");
  if (dir) { void body.offsetWidth; body.classList.add("enter", dir); }
  qzNav();
  const last = QZ.i === n - 1;
  $("#qz-cta").innerHTML = QZ.i < n
    ? `<div class="cta-row"><button class="btn icon" ${QZ.i === 0 ? "disabled" : ""} onclick="qzGo(QZ.i-1)" aria-label="Предыдущий вопрос">${ic("prev", 20, 2)}</button>
        <button class="btn ${last ? "dark" : "primary"} grow" onclick="qzGo(QZ.i+1)">${last ? "К проверке ответов" : "Дальше"}${ic("next", 18, 2)}</button></div>`
    : `<div class="cta-row"><button class="btn icon" onclick="qzGo(QZ.i-1)" aria-label="Последний вопрос">${ic("prev", 20, 2)}</button>
        <button class="btn primary grow" id="qz-send" onclick="qzSubmit(this)">${ic("send", 18, 2)}Отправить ответы</button></div>`;
  if (dir) window.scrollTo({ top: 0, behavior: "smooth" });
}
function qzQuestionHtml(q, n) {
  const ans = QZ.answers[q.id];
  let input;
  if (q.type === "single" || q.type === "multiple") {
    input = `<div class="opts" role="${q.type === "single" ? "radiogroup" : "group"}">${q.options.map((o, k) => {
      const on = q.type === "single" ? ans === o.id : (ans || []).includes(o.id);
      return `<button type="button" class="opt ${q.type}${on ? " on" : ""}" data-o="${esc(o.id)}" role="${q.type === "single" ? "radio" : "checkbox"}" aria-checked="${on}">
        <span class="mark">${ic("check", 14, 3)}</span><span class="grow md">${md(o.text, true)}</span><span class="opt-l">${String.fromCharCode(65 + k)}</span></button>`;
    }).join("")}</div>`;
  } else {
    const isNum = q.type === "number";
    input = `<div class="stack" style="gap:6px"><input class="input big${isNum ? " mono" : ""}" id="qz-input" ${isNum ? 'inputmode="decimal"' : ""} autocomplete="off"
        autocapitalize="off" spellcheck="false" enterkeyhint="next" placeholder="${isNum ? "Например, 3.14" : "Твой ответ"}" value="${esc(ans || "")}">
      <span class="small muted" style="padding-left:18px">${isNum ? "Дробную часть можно писать через точку или запятую, можно дробью: 1/2" : "Регистр и лишние пробелы не важны"}</span></div>`;
  }
  return `<div class="card q-card">
      <div class="row between"><span class="chip big accent">Вопрос ${q.number} из ${n}</span>
        <span class="small muted">${QTYPE_HINT[q.type]} · ${q.points} ${ptsWord(q.points)}</span></div>
      <div class="md q-text">${md(q.text)}</div></div>${input}`;
}
function answerSummary(q, ans) {
  if (!isAnswered(ans)) return "";
  if (q.type === "single") return String.fromCharCode(65 + q.options.findIndex((o) => o.id === ans));
  if (q.type === "multiple") return ans.map((id) => String.fromCharCode(65 + q.options.findIndex((o) => o.id === id))).join(", ");
  return esc(String(ans).trim());
}
function qzReviewHtml() {
  const qs = QZ.qv.questions;
  const answered = qs.filter((q) => isAnswered(QZ.answers[q.id])).length;
  return `<div class="stack" style="gap:6px"><h2 class="h2">Проверь ответы</h2>
      <span class="small muted">Отвечено ${answered} из ${qs.length}. Нажми на вопрос, чтобы вернуться к нему.</span></div>
    <div class="stack" style="gap:8px">${qs.map((q, k) => {
      const a = answerSummary(q, QZ.answers[q.id]);
      return `<button type="button" class="capsule round press q-sum" data-go="${k}">
        <span class="n ${a ? "good" : "warn"}">${k + 1}</span>
        <span class="grow ellipsis md">${mdLine(q.text)}</span>
        ${a ? `<span class="ans mono ellipsis">${a}</span>` : `<span class="chip warn">нет ответа</span>`}</button>`;
    }).join("")}</div>
    ${answered < qs.length ? `<div class="card tight soft-warn small">Вопросы без ответа засчитаются как неверные.</div>` : ""}`;
}
function ask(text) {
  return new Promise((resolve) => {
    try { if (tg?.showConfirm && tg.isVersionAtLeast?.("6.2")) { tg.showConfirm(text, (ok) => resolve(!!ok)); return; } } catch {}
    resolve(window.confirm(text));
  });
}
async function qzSubmit(btn) {
  const qs = QZ.qv.questions;
  const missing = qs.filter((q) => !isAnswered(QZ.answers[q.id])).length;
  if (missing && !(await ask(`Без ответа ${missing} ${qWord(missing)}. Отправить всё равно?`))) return;
  const answers = {};
  for (const q of qs) if (isAnswered(QZ.answers[q.id])) answers[q.id] = Array.isArray(QZ.answers[q.id]) ? QZ.answers[q.id] : String(QZ.answers[q.id]);
  btn.disabled = true;
  const old = btn.innerHTML;
  btn.innerHTML = `<span class="spinner"></span>Проверяем…`;
  hap("medium");
  try {
    const sub = await api(`/assignments/${QZ.aid}/quiz`, json("POST", { answers }));
    dropDraft(QZ.aid);
    toast(`Тест отправлен: ${sub.score} из ${sub.max_score}`);
    replace(quizResultRoute(sub.id));
  } catch (e) { toast(e.message, "err"); btn.disabled = false; btn.innerHTML = old; }
}

// ---------- результат ----------
function quizResultRoute(sid) {
  return {
    async render(get) {
      const s = await get(`/submissions/${sid}`);
      const list = await get("/assignments").catch(() => []);
      const a = list.find((x) => x.id === s.assignment_id);
      const r = s.result;
      await needMath(...quizTexts(r.questions));
      const reveal = !r.keys_hidden;
      const n = r.questions.length;
      const correct = r.questions.filter((q) => q.score >= q.points).length;
      const edited = s.auto_score !== null && s.auto_score !== s.score
        ? `<div class="row small" style="gap:8px"><span class="avatar sm">${esc(initial(s.edited_by || "О"))}</span>
            <span><b>${esc(s.edited_by || "Организатор")}</b> <span class="muted">изменил оценку: ${s.auto_score} → ${s.score}</span></span></div>` : "";
      const firstWrong = r.questions.findIndex((q) => q.score < q.points);
      let html = topbar("Результат теста", `${esc(s.assignment_title)} · попытка №${s.attempt ?? "—"}`);
      html += `<div class="card" style="gap:14px">
        <div class="score-hero"><span class="hero-num">${r.score}<small>/${r.max_score}</small></span>
          <span class="chip big ${scoreKind(r.score, r.max_score)}">${verdictLabel(r.score, r.max_score)}</span></div>
        ${qBar(r)}
        <div class="row" style="gap:12px"><span class="avatar ${correct === n ? "" : "plain"}" style="width:40px;height:40px">${ic(correct === n ? "check" : "bulb", 20, 2.2)}</span>
          <span class="grow stack" style="gap:0"><span class="h3">${esc(r.auto_comment || "")}</span>
            <span class="small muted">верно ${correct} из ${n}${r.answered < n ? ` · без ответа ${n - r.answered}` : ""}</span></span></div>${edited}</div>`;
      if (s.organizer_comment) html += commentCard(s);
      if (!reveal) html += `<div class="card tight row" style="flex-direction:row;align-items:center;gap:12px">
          <span class="avatar plain muted" style="width:40px;height:40px">${ic("lock", 18, 2)}</span>
          <span class="grow small muted">${a?.deadline ? `Правильные ответы и пояснения откроются после дедлайна, ${msk(a.deadline)}.` : "Правильные ответы и пояснения организаторы покажут позже."}</span></div>`;
      html += `<div class="stack">${r.questions.map((q, i) => quizAcc(q, reveal, i === firstWrong)).join("")}</div>`;
      const qa = a?.quiz;
      if (a && qa && !isClosed(a) && qa.attempts_used < qa.max_attempts)
        html += `<div class="cta"><button class="btn primary" onclick="replace(quizTakeRoute(${a.id}))">${ic("refresh", 18, 2)}Пройти ещё раз · попытка ${qa.attempts_used + 1} из ${qa.max_attempts}</button></div>`;
      return html;
    },
  };
}
function quizAcc(q, reveal, open) {
  const k = q.score >= q.points ? "good" : q.score > 0 ? "warn" : "bad";
  return `<details class="acc"${open ? " open" : ""} ontoggle="this.open&&hap('select')">
    <summary><span class="n" style="background:var(--${k}-soft);color:var(--${k})">${q.number}</span>
      <span class="grow stack" style="gap:0;min-width:0"><span class="h3">Вопрос ${q.number}</span><span class="small muted ellipsis md">${mdLine(q.text)}</span></span>
      <span class="num c-${k}">${q.score}<span class="small muted">/${q.points}</span></span><span class="chev">${ic("chev", 16)}</span></summary>
    <div class="body q-body">${qBodyHtml(q, reveal, "Твой ответ")}</div></details>`;
}
// Условие, ответ и (если можно) правильный ответ с пояснением
function qBodyHtml(q, reveal, whoLabel) {
  const edited = q.auto_score !== null && q.auto_score !== undefined
    ? `<span class="chip accent" style="align-self:flex-start">система: ${q.auto_score}, организатор: ${q.score}</span>` : "";
  let ans;
  if (q.type === "single" || q.type === "multiple") {
    const chosen = q.type === "single" ? [q.answer] : q.answer || [];
    ans = `<div class="opts sm">${q.options.map((o, k) => {
      const mine = chosen.includes(o.id);
      const right = reveal && (q.correct || []).includes(o.id);
      let cls = "";
      if (reveal) cls = mine ? (right ? "right" : "wrong") : right ? "missed" : "";
      else if (mine) cls = q.type === "single" ? (q.is_correct ? "right" : "wrong") : "on";
      const tag = cls === "missed" ? `<span class="chip good">верный</span>` : mine ? `<span class="small muted">${whoLabel === "Твой ответ" ? "твой" : "выбран"}</span>` : "";
      return `<div class="opt ${q.type} static ${cls}"><span class="mark">${ic(cls === "wrong" ? "close" : "check", 14, 3)}</span>
        <span class="grow md">${md(o.text, true)}</span>${tag}<span class="opt-l">${String.fromCharCode(65 + k)}</span></div>`;
    }).join("")}</div>`;
    if (!q.answer || (Array.isArray(q.answer) && !q.answer.length)) ans = `<span class="chip warn" style="align-self:flex-start">нет ответа</span>` + ans;
  } else {
    const key = q.type === "text" ? (q.answers || []).map((x) => md(x, true)).join(" <span class=\"muted\">или</span> ")
      : q.value !== undefined ? `<span class="mono">${esc(String(q.value))}${q.tolerance ? ` ± ${esc(String(q.tolerance))}` : ""}</span>` : "";
    ans = `<div class="ans-row ${q.answer === null || q.answer === undefined ? "none" : q.is_correct ? "right" : "wrong"}">
        <span class="mark">${ic(q.is_correct ? "check" : "close", 14, 3)}</span>
        <span class="grow stack" style="gap:0"><span class="small muted">${whoLabel}</span><span class="mono">${q.answer === null || q.answer === undefined ? "нет ответа" : esc(q.answer)}</span></span></div>
      ${reveal && key ? `<div class="ans-row key"><span class="mark">${ic("check", 14, 3)}</span>
        <span class="grow stack" style="gap:0"><span class="small muted">Правильный ответ</span><span>${key}</span></span></div>` : ""}`;
  }
  const expl = reveal && q.explanation ? `<span class="tip">${ic("bulb", 16, 2)}<span class="md">${md(q.explanation)}</span></span>` : "";
  return `${edited}<div class="md q-text">${md(q.text)}</div>${ans}${expl}`;
}

// Правильные ответы для тех, кто смотрит после дедлайна
function quizKeyRoute(aid) {
  return {
    async render(get) {
      const [qv, list] = await Promise.all([get(`/assignments/${aid}/quiz`), get("/assignments")]);
      const a = list.find((x) => x.id === aid);
      await needMath(...quizTexts(qv.questions));
      return `${topbar("Правильные ответы", esc(a?.title || ""))}
        <div class="stack">${qv.questions.map((q) => `<div class="card q-body">
          <div class="row between"><span class="chip big">Вопрос ${q.number}</span><span class="small muted">${q.points} ${ptsWord(q.points)}</span></div>
          ${qKeyHtml(q)}</div>`).join("")}</div>`;
    },
  };
}
function qKeyHtml(q) {
  let ans;
  if (q.options) ans = `<div class="opts sm">${q.options.map((o, k) => {
    const right = (q.correct || []).includes(o.id);
    return `<div class="opt ${q.type} static ${right ? "right" : ""}"><span class="mark">${ic("check", 14, 3)}</span><span class="grow md">${md(o.text, true)}</span><span class="opt-l">${String.fromCharCode(65 + k)}</span></div>`;
  }).join("")}</div>`;
  else ans = `<div class="ans-row key"><span class="mark">${ic("check", 14, 3)}</span><span class="grow stack" style="gap:0"><span class="small muted">Правильный ответ</span>
    <span>${q.type === "text" ? (q.answers || []).map((x) => md(x, true)).join(' <span class="muted">или</span> ') : `<span class="mono">${esc(String(q.value))}${q.tolerance ? ` ± ${esc(String(q.tolerance))}` : ""}</span>`}</span></span></div>`;
  return `<div class="md q-text">${md(q.text)}</div>${ans}${q.explanation ? `<span class="tip">${ic("bulb", 16, 2)}<span class="md">${md(q.explanation)}</span></span>` : ""}`;
}

// =====================================================================
// ОРГАНИЗАТОР: проверка попытки теста
// =====================================================================
async function quizReviewHtml(s, inPane) {
  const u = s.user || {};
  const r = s.result;
  await needMath(...quizTexts(r.questions));
  REV = { sid: s.id, task: 0, scores: {}, auto: {} };
  for (const q of r.questions) { REV.scores[q.number] = q.score; REV.auto[q.number] = q.auto_score ?? q.score; }
  const head = inPane ? "" : topbar(esc(s.assignment_title), `<span class="mono">тест · попытка №${s.attempt ?? "—"} · id ${s.id} · ${when(s.created_at)}</span>`);
  const who = `<div class="capsule full" style="background:var(--inverse);color:var(--on-inverse);padding:10px 18px 10px 10px">
    <span class="avatar">${esc(initial(u.name))}</span>
    <span class="grow stack" style="gap:0"><span class="h3 ellipsis">${esc(u.name)} · ${u.fio ? esc(u.fio) : `<span style="color:var(--bad)">ФИО не указано</span>`}</span>
      <span class="small mono" style="opacity:.7">${u.username ? "@" + esc(u.username) + " · " : ""}id ${u.tg_id}${inPane ? ` · тест №${s.attempt ?? "—"} · сдача ${s.id}` : ""}</span></span>
    <span class="stack" style="gap:0;align-items:flex-end"><span class="num" style="font-size:20px"><span id="rev-total">${r.score}</span><span class="small" style="opacity:.6">/${r.max_score}</span></span>
      <span class="small" style="opacity:.7">система: ${s.auto_score ?? r.score}</span></span></div>`;
  const correct = r.questions.filter((q) => q.is_correct).length;
  const summary = `<div class="card tight row" style="flex-direction:row;align-items:center;gap:12px">
    <span class="chip big accent">Тест</span><span class="grow small">Система: «${esc(r.auto_comment || "")}» · верно ${correct} из ${r.questions.length}${r.answered < r.questions.length ? ` · без ответа ${r.questions.length - r.answered}` : ""}</span></div>`;
  const strip = `<div class="pills" id="qrev-nav">${r.questions.map((q) => {
    const k = q.score >= q.points ? "good" : q.score > 0 ? "warn" : "bad";
    return `<button class="pill sm qpill ${k}" data-jump="${q.number}">№${q.number} · <span class="num" data-score="${q.number}">${q.score}</span></button>`;
  }).join("")}</div>`;
  const cards = r.questions.map((q) => {
    const k = q.score >= q.points ? "good" : q.score > 0 ? "warn" : "bad";
    return `<div class="card q-body" id="qrev-${q.number}">
      <div class="row between"><span class="row" style="gap:8px"><span class="n-dot" style="background:var(--${k}-soft);color:var(--${k})">${q.number}</span>
        <span class="small muted">${QTYPE_HINT[q.type]} · ${q.points} ${ptsWord(q.points)}</span></span>
        <span class="chip ${q.is_correct ? "good" : "bad"}">${q.is_correct ? "верно" : q.answer === null || q.answer === undefined ? "нет ответа" : "неверно"}</span></div>
      ${qBodyHtml(q, true, "Ответ участника")}</div>`;
  }).join("");
  const scores = `<div class="card"><span class="row between"><span class="small muted" style="font-weight:600">Баллы за вопросы</span><span class="small muted">0 – вес вопроса</span></span>
    ${r.questions.map((q) => `<div class="task-score"><span class="grow stack" style="gap:0"><span class="h3 ellipsis md" style="font-size:14px">${q.number}. ${mdLine(q.text)}</span>
        <span class="small muted">система: ${REV.auto[q.number]} из ${q.points}</span></span>
      <span class="stepper"><button type="button" data-step="-1" data-n="${q.number}" data-max="${q.points}" aria-label="Меньше">−</button>
        <span class="v" data-v="${q.number}">${q.score}</span>
        <button type="button" data-step="1" data-n="${q.number}" data-max="${q.points}" aria-label="Больше">+</button></span></div>`).join("")}</div>`;
  const comment = `<div class="card"><span class="small muted">Комментарий участнику · подпишем как «${esc(ME.profile.name)}»</span>
    <textarea class="textarea" id="rev-comment" rows="4" placeholder="Что получилось, что повторить…">${esc(s.organizer_comment || "")}</textarea>
    ${s.organizer_comment && s.comment_by ? `<span class="small muted">Сейчас: ${esc(s.comment_by)}${s.comment_at ? ", " + when(s.comment_at) : ""}</span>` : ""}</div>`;
  return `${head}<div class="review">${who}
    <div class="review-main">${summary}${strip}${cards}</div>
    <div class="review-side">${scores}${comment}
      <div class="cta"><button class="btn primary" id="rev-save">${ic("send", 18, 2)}Сохранить и уведомить</button></div></div></div>`;
}

// =====================================================================
// ОРГАНИЗАТОР: конструктор теста
// =====================================================================
let QB = null;
const QTYPES = [["single", "Один"], ["multiple", "Несколько"], ["text", "Текст"], ["number", "Число"]];
const uid = (p) => p + Math.random().toString(16).slice(2, 10);
function qbBlank(type = "single") {
  return { id: uid("q"), type, text: "", points: 1, options: [{ id: uid("o"), text: "" }, { id: uid("o"), text: "" }],
    correct: [], answers: [""], value: "", tolerance: "", explanation: "", showExpl: false };
}
function qbFrom(q) {
  return { ...qbBlank(q.type), ...q, options: (q.options?.length ? q.options : qbBlank().options).map((o) => ({ ...o })),
    correct: [...(q.correct || [])], answers: q.answers?.length ? [...q.answers] : [""],
    value: q.value ?? "", tolerance: q.tolerance ? q.tolerance : "", explanation: q.explanation || "", showExpl: !!q.explanation };
}
const qbPoints = () => QB.questions.reduce((s, q) => s + (+q.points || 0), 0);

function qbHtml(aid) {
  return `<div class="stack" id="qb" style="gap:10px">
    <div class="row wrap" style="gap:6px">
      <label class="btn sm press file-btn">${ic("upload", 16, 2)}Загрузить .md<input type="file" accept=".md,.markdown,.txt,text/markdown" onchange="qbImport(this)"></label>
      ${aid ? `<button type="button" class="btn sm" onclick="download('/admin/assignments/${aid}/quiz.md','quiz.md')">${ic("download", 16, 2)}Скачать .md</button>` : ""}
      <span class="grow"></span>
      <span class="toggle-row compact"><span class="small muted">Попыток</span>
        <span class="stepper"><button type="button" data-act="att" data-v="-1" aria-label="Меньше">−</button><span class="v" id="qb-att">${QB.attempts}</span>
          <button type="button" data-act="att" data-v="1" aria-label="Больше">+</button></span></span></div>
    <details class="acc help"><summary><span class="n" style="background:var(--bg)">${ic("doc", 18, 2)}</span><span class="grow h3">Как оформить .md с вопросами</span><span class="chev">${ic("chev", 16)}</span></summary>
      <div class="body" style="padding-left:16px">${QB_HELP}</div></details>
    <div id="qb-import"></div>
    <div class="stack" id="qb-list" style="gap:10px">${QB.questions.map(qbCardHtml).join("")}</div>
    <button type="button" class="btn block dashed press" data-act="add">${ic("plus", 18, 2.2)}Добавить вопрос</button></div>`;
}
const QB_HELP = `<span>Каждый вопрос начинается с заголовка <code>## </code>. Формулы — как в Jupyter: <code>$x^2$</code> или <code>$$\\sum_i x_i$$</code>.</span>
  ${mdCodeRaw(`## Вопрос 1 (2 балла)
Чему равно $\\langle a, b \\rangle$ для ортогональных векторов?

- [ ] $\\|a\\|\\,\\|b\\|$
- [x] $0$
- [ ] $1$

Пояснение: косинус прямого угла равен нулю.

## Вопрос 2
Какие матрицы всегда квадратные?
- [x] Единичная
- [x] Матрица Грама $X^\\top X$
- [ ] Матрица признаков $X$

## Вопрос 3
Найдите $\\det \\begin{pmatrix} 2 & 1 \\\\ 1 & 2 \\end{pmatrix}$.
Ответ: 3

## Вопрос 4
Как называется операция $A \\mapsto A^\\top$?
Ответ: транспонирование | транспонирования`)}
  <span class="small muted"><code>- [x]</code> отмечает верный вариант (два и больше — вопрос с несколькими ответами).
  <code>Ответ:</code> без вариантов — свободный ответ: число (можно с допуском <code>1.41 ± 0.01</code>) или текст, варианты через <code>|</code>.
  Ещё можно указать <code>Баллы: 2</code> и <code>Пояснение: …</code>. Варианты вида <code>A)</code> <code>B)</code> с строкой <code>Ответ: B</code> тоже подойдут.</span>`;
function mdCodeRaw(code) {
  return `<div class="code md-code"><pre>${code.split("\n").map((l) => `<span class="ln">${esc(l) || " "}</span>`).join("")}</pre></div>`;
}

function qbCardHtml(q, i) {
  const n = QB.questions.length;
  let body = "";
  if (q.type === "single" || q.type === "multiple") {
    body = `<div class="stack qb-opts" style="gap:6px">${q.options.map((o, k) => {
      const right = q.correct.includes(o.id);
      return `<div class="qb-opt${right ? " right" : ""}">
        <button type="button" class="mark ${q.type}" data-act="correct" data-o="${k}" aria-label="Отметить верным" aria-pressed="${right}">${ic("check", 14, 3)}</button>
        <span class="grow stack" style="gap:2px"><input class="input" data-f="opt" data-o="${k}" value="${esc(o.text)}" placeholder="Вариант ${String.fromCharCode(65 + k)}">
          <span class="qb-mini md${HAS_MATH.test(o.text) ? "" : " hide"}" data-mini="${k}">${HAS_MATH.test(o.text) ? md(o.text, true) : ""}</span></span>
        <button type="button" class="icon-btn sm" data-act="delopt" data-o="${k}" aria-label="Убрать вариант" ${q.options.length <= 2 ? "disabled" : ""}>${ic("close", 14, 2.2)}</button></div>`;
    }).join("")}
      <div class="row wrap" style="gap:8px"><button type="button" class="btn xs" data-act="addopt">${ic("plus", 14, 2.2)}Вариант</button>
        <span class="small muted">Нажми на кружок у верного варианта${q.type === "multiple" ? " (можно несколько)" : ""}</span></div></div>`;
  } else if (q.type === "text") {
    body = `<div class="stack" style="gap:6px"><span class="small muted" style="padding-left:14px">Правильный ответ — любой из вариантов, регистр и лишние пробелы не важны</span>
      ${q.answers.map((x, k) => `<div class="row" style="gap:6px"><input class="input grow" data-f="ans" data-o="${k}" value="${esc(x)}" placeholder="${k ? "Ещё вариант ответа" : "Правильный ответ"}">
        <button type="button" class="icon-btn sm" data-act="delans" data-o="${k}" aria-label="Убрать" ${q.answers.length <= 1 ? "disabled" : ""}>${ic("close", 14, 2.2)}</button></div>`).join("")}
      <button type="button" class="btn xs" style="align-self:flex-start" data-act="addans">${ic("plus", 14, 2.2)}Ещё вариант ответа</button></div>`;
  } else {
    body = `<div class="field-grid"><label class="field"><span>Правильный ответ</span><input class="input mono" data-f="value" inputmode="decimal" value="${esc(String(q.value))}" placeholder="3.14"></label>
      <label class="field"><span>Допуск ±</span><input class="input mono" data-f="tolerance" inputmode="decimal" value="${esc(String(q.tolerance))}" placeholder="0"></label></div>`;
  }
  return `<div class="qb-card" data-q="${i}" id="qb-${q.id}">
    <div class="row" style="gap:8px"><span class="n-dot">${i + 1}</span><span class="grow small muted">Вопрос ${i + 1}</span>
      <button type="button" class="icon-btn sm" data-act="up" aria-label="Выше" ${i === 0 ? "disabled" : ""}>${ic("up", 16, 2)}</button>
      <button type="button" class="icon-btn sm" data-act="down" aria-label="Ниже" ${i === n - 1 ? "disabled" : ""}>${ic("down", 16, 2)}</button>
      <button type="button" class="icon-btn sm danger" data-act="del" aria-label="Удалить вопрос">${ic("trash", 16, 2)}</button></div>
    <div class="seg sm qb-type">${QTYPES.map(([t, l]) => `<button type="button" class="${q.type === t ? "on" : ""}" data-act="type" data-v="${t}">${l}</button>`).join("")}</div>
    <textarea class="textarea" data-f="text" rows="3" placeholder="Условие. Формулы: $x^2$ или $$\\sum_i x_i$$">${esc(q.text)}</textarea>
    <div class="qb-preview md${q.text.trim() ? "" : " hide"}" data-preview>${q.text.trim() ? md(q.text) : ""}</div>
    ${body}
    <label class="field${q.showExpl ? "" : " hide"}"><span>Пояснение — участники увидят его после дедлайна</span>
      <textarea class="textarea" data-f="explanation" rows="2" placeholder="Почему верен именно этот ответ">${esc(q.explanation)}</textarea></label>
    <div class="row qb-foot" style="gap:8px">
      <span class="stepper"><button type="button" data-act="pts" data-v="-1" aria-label="Меньше баллов">−</button><span class="v">${q.points}</span>
        <button type="button" data-act="pts" data-v="1" aria-label="Больше баллов">+</button></span><span class="small muted grow">${ptsWord(q.points)}</span>
      <button type="button" class="btn xs${q.showExpl ? " on" : ""}" data-act="expl">${ic("bulb", 14, 2)}Пояснение</button></div></div>`;
}

function qbSummary() {
  const el = $("#qb-sum");
  if (el) el.textContent = QB.questions.length ? `${QB.questions.length} ${qWord(QB.questions.length)} · ${qbPoints()} ${ptsWord(qbPoints())}` : "пока без вопросов";
}
function qbDirty() { $("#a-form")?.setAttribute("data-dirty", ""); qbSummary(); }
function qbAutosize(root) {
  root.querySelectorAll("textarea").forEach((t) => { if (t.offsetParent) { t.style.height = "auto"; t.style.height = t.scrollHeight + 3 + "px"; } });
}
function qbList(focusId) {
  $("#qb-list").innerHTML = QB.questions.map(qbCardHtml).join("");
  qbAutosize($("#qb-list"));
  qbSummary();
  if (focusId) {
    const card = $(`#qb-${focusId}`);
    card?.classList.add("enter");
    card?.scrollIntoView({ block: "center", behavior: "smooth" });
  }
}
function qbCard(i) {
  const old = $(`#qb-list [data-q="${i}"]`);
  if (!old) return;
  const tmp = document.createElement("div");
  tmp.innerHTML = qbCardHtml(QB.questions[i], i);
  const card = tmp.firstElementChild;
  old.replaceWith(card);
  qbAutosize(card);
}

const PREVIEW_T = new Map();
function qbBind() {
  const root = $("#qb");
  qbAutosize(root);
  root.addEventListener("input", (e) => {
    const el = e.target, f = el.dataset.f;
    const card = el.closest(".qb-card");
    if (!f || !card) return;
    if (el.tagName === "TEXTAREA") { el.style.height = "auto"; el.style.height = el.scrollHeight + 3 + "px"; }
    const i = +card.dataset.q, q = QB.questions[i], k = +el.dataset.o;
    if (f === "opt") q.options[k].text = el.value;
    else if (f === "ans") q.answers[k] = el.value;
    else q[f] = el.value;
    qbDirty();
    if (f === "text" || f === "opt") {
      clearTimeout(PREVIEW_T.get(el));
      PREVIEW_T.set(el, setTimeout(async () => {
        await needMath(el.value);
        if (f === "text") {
          const p = card.querySelector("[data-preview]");
          p.innerHTML = md(q.text); p.classList.toggle("hide", !q.text.trim());
        } else {
          const p = card.querySelector(`[data-mini="${k}"]`);
          const show = HAS_MATH.test(el.value);
          p.innerHTML = show ? md(el.value, true) : ""; p.classList.toggle("hide", !show);
        }
      }, 220));
    }
  });
  root.addEventListener("click", (e) => {
    const b = e.target.closest("[data-act]");
    if (!b || b.disabled) return;
    const act = b.dataset.act;
    if (act === "att") {
      QB.attempts = Math.max(1, Math.min(100, QB.attempts + +b.dataset.v));
      $("#qb-att").textContent = QB.attempts; hap("select"); qbDirty(); return;
    }
    if (act === "add") {
      const last = QB.questions[QB.questions.length - 1];
      const q = qbBlank(last?.type || "single");
      QB.questions.push(q); hap("light"); qbDirty(); qbList(q.id);
      setTimeout(() => $(`#qb-${q.id} textarea`)?.focus({ preventScroll: true }), 260);
      return;
    }
    const card = b.closest(".qb-card");
    if (!card) return;
    const i = +card.dataset.q, q = QB.questions[i], k = +b.dataset.o;
    hap("select");
    switch (act) {
      case "type":
        if (q.type === b.dataset.v) return;
        q.type = b.dataset.v;
        if (q.type === "single" && q.correct.length > 1) q.correct = q.correct.slice(0, 1);
        qbCard(i); break;
      case "pts": q.points = Math.max(1, Math.min(100, q.points + +b.dataset.v)); qbCard(i); break;
      case "expl": q.showExpl = !q.showExpl; qbCard(i); if (q.showExpl) $(`#qb-${q.id} [data-f="explanation"]`)?.focus(); break;
      case "correct": {
        const id = q.options[k].id;
        if (q.type === "single") q.correct = q.correct[0] === id ? [] : [id];
        else q.correct = q.correct.includes(id) ? q.correct.filter((x) => x !== id) : q.options.map((o) => o.id).filter((x) => x === id || q.correct.includes(x));
        qbCard(i); break;
      }
      case "addopt": q.options.push({ id: uid("o"), text: "" }); qbCard(i); $(`#qb-${q.id} [data-o="${q.options.length - 1}"][data-f="opt"]`)?.focus(); break;
      case "delopt": q.correct = q.correct.filter((x) => x !== q.options[k].id); q.options.splice(k, 1); qbCard(i); break;
      case "addans": q.answers.push(""); qbCard(i); $(`#qb-${q.id} [data-f="ans"][data-o="${q.answers.length - 1}"]`)?.focus(); break;
      case "delans": q.answers.splice(k, 1); qbCard(i); break;
      case "up": case "down": {
        const j = act === "up" ? i - 1 : i + 1;
        [QB.questions[i], QB.questions[j]] = [QB.questions[j], QB.questions[i]];
        qbList(); $(`#qb-${q.id}`)?.scrollIntoView({ block: "nearest", behavior: "smooth" }); break;
      }
      case "del":
        if ((q.text.trim() || q.options.some((o) => o.text.trim())) && !confirm(`Удалить вопрос ${i + 1}?`)) return;
        QB.questions.splice(i, 1); qbList(); break;
    }
    qbDirty();
  });
}

async function qbImport(input) {
  const f = input.files[0];
  input.value = "";
  if (!f) return;
  const fd = new FormData(); fd.append("file", f);
  const box = $("#qb-import");
  box.innerHTML = `<div class="card tight row" style="flex-direction:row;align-items:center;gap:10px"><span class="spinner"></span>Разбираем ${esc(f.name)}…</div>`;
  try {
    const r = await api("/admin/quiz/parse", { method: "POST", body: fd });
    await needMath(...quizTexts(r.questions));
    const qs = r.questions.map(qbFrom);
    const hasOwn = QB.questions.some((q) => q.text.trim());
    const titleInput = $('#a-form [name="title"]');
    if (r.title && titleInput && !titleInput.value.trim()) titleInput.value = r.title;
    const warn = r.warnings.length ? `<div class="stack small" style="gap:2px">${r.warnings.map((w) => `<span class="row" style="gap:6px;align-items:flex-start">${ic("warn", 14, 2)}<span>${esc(w)}</span></span>`).join("")}</div>` : "";
    const head = `<span class="h3">Нашёл ${qs.length} ${qWord(qs.length)} в «${esc(f.name)}»</span>`;
    const apply = (mode) => {
      QB.questions = mode === "replace" ? qs : [...QB.questions, ...qs];
      qbList(); qbDirty(); hap("success");
      box.innerHTML = `<div class="card tight ${r.warnings.length ? "soft-warn" : "soft-accent"}">${head}
        <span class="small">${r.warnings.length ? "Проверь отмеченное ниже — эти вопросы нужно поправить перед сохранением." : "Проверь вопросы и отмеченные ответы, потом сохрани задание."}</span>${warn}</div>`;
    };
    if (!hasOwn) { apply("replace"); return; }
    window.__qbApply = apply;
    box.innerHTML = `<div class="card tight soft-accent">${head}${warn}<span class="small">В тесте уже есть вопросы. Что сделать с новыми?</span>
      <div class="row wrap" style="gap:6px"><button type="button" class="btn xs dark" onclick="__qbApply('replace')">Заменить все</button>
        <button type="button" class="btn xs" onclick="__qbApply('append')">Добавить в конец</button>
        <button type="button" class="btn xs" onclick="$('#qb-import').innerHTML=''">Отмена</button></div></div>`;
  } catch (e) {
    box.innerHTML = `<div class="card tight soft-bad"><span class="h3">Не получилось разобрать файл</span><span class="small">${esc(e.message)}</span></div>`;
    hap("error");
  }
}

// Проверка перед отправкой: те же правила, что на сервере, но с подсветкой нужного вопроса
function qbValidate() {
  if (!QB.questions.length) return { i: -1, msg: "Добавь в тест хотя бы один вопрос" };
  for (const [i, q] of QB.questions.entries()) {
    const where = `Вопрос ${i + 1}`;
    if (!q.text.trim()) return { i, msg: `${where}: пустое условие` };
    if (q.type === "single" || q.type === "multiple") {
      if (q.options.some((o) => !o.text.trim())) return { i, msg: `${where}: заполни все варианты или убери пустые` };
      if (q.options.length < 2) return { i, msg: `${where}: нужно хотя бы два варианта` };
      if (!q.correct.length) return { i, msg: `${where}: отметь правильный вариант` };
    } else if (q.type === "text") {
      if (!q.answers.some((x) => x.trim())) return { i, msg: `${where}: укажи правильный ответ` };
    } else {
      if (!/^-?\d+([.,]\d+)?(\/\d+)?$/.test(String(q.value).trim().replace(/\s/g, ""))) return { i, msg: `${where}: правильный ответ должен быть числом` };
      if (String(q.tolerance).trim() && !/^\d+([.,]\d+)?$/.test(String(q.tolerance).trim())) return { i, msg: `${where}: допуск должен быть неотрицательным числом` };
    }
  }
  return null;
}
function qbFlag(i) {
  const card = $(`#qb-list [data-q="${i}"]`);
  if (!card) return;
  card.scrollIntoView({ block: "center", behavior: "smooth" });
  card.classList.remove("flag"); void card.offsetWidth; card.classList.add("flag");
}
function qbPayload() {
  return {
    attempts: QB.attempts,
    questions: QB.questions.map((q) => {
      const out = { id: q.id, type: q.type, text: q.text, points: q.points, explanation: q.showExpl ? q.explanation : "" };
      if (q.type === "single" || q.type === "multiple") { out.options = q.options; out.correct = q.correct; }
      else if (q.type === "text") out.answers = q.answers.filter((x) => x.trim());
      else { out.value = String(q.value).replace(",", "."); out.tolerance = String(q.tolerance || "0").replace(",", "."); }
      return out;
    }),
  };
}

// Краткое описание частей для карточки в «Управлении»
function partsLine(a) {
  const xs = [];
  if (a.quiz) xs.push(`тест: ${a.quiz.questions} ${qWord(a.quiz.questions)}, ${a.quiz.max_attempts} ${plural(a.quiz.max_attempts, "попытка", "попытки", "попыток")}`);
  if (partsOf(a).includes("notebook")) xs.push(`ноутбук: ${a.task_count} ${plural(a.task_count, "задание", "задания", "заданий")}, ${a.max_attempts} ${plural(a.max_attempts, "попытка", "попытки", "попыток")}`);
  return xs.join(" · ");
}
