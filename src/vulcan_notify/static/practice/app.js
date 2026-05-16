"use strict";

/* ============================================================
 * Kids practice — single-page app, vanilla JS.
 * State machine: welcome -> set-picker -> running -> summary.
 * Per-question state: pending | attempt1-wrong | passed | failed.
 * Persistence: localStorage, keyed by student + setId.
 * ============================================================ */

const STUDENTS = ["Solomiia", "Yarema"];
const SKINS = [
  { id: "stage",  name: "Stage",  tagline: "K-pop · pink + violet",  swatches: ["#ff2d87", "#6a3df5", "#2de2b6", "#ffd23f"] },
  { id: "forest", name: "Forest", tagline: "Ghibli · moss + amber",  swatches: ["#d96b3b", "#2f6b3d", "#6ba84e", "#e8b338"] },
  { id: "berry",  name: "Berry",  tagline: "Pastel · purple + pink", swatches: ["#d9438f", "#8e5cd1", "#5fb89e", "#f5d971"] },
  { id: "cosmic", name: "Cosmic", tagline: "Dark mode · neon",       swatches: ["#ff5d9c", "#6ad9ff", "#5fd9a0", "#ffd54a"] },
];
const SKIN_IDS = new Set(SKINS.map(s => s.id));
const skinKey = (student) => `practice.skin.${student}`;
function getSkin(student) {
  const v = student && localStorage.getItem(skinKey(student));
  return SKIN_IDS.has(v) ? v : "stage";
}
function setSkin(student, id) {
  if (!student || !SKIN_IDS.has(id)) return;
  localStorage.setItem(skinKey(student), id);
  applySkin(id);
}
function applySkin(id) {
  // Remove any existing skin-* class, then apply the chosen one.
  document.body.classList.forEach(c => { if (c.startsWith("skin-")) document.body.classList.remove(c); });
  document.body.classList.add(`skin-${id}`);
}

const SETS = [
  { id: "ulamki-zwykle-1",     url: "./sets/ulamki-zwykle-1.json",     trackLabel: "TRACK 01 · LV.1" },
  { id: "ulamki-zwykle-2",     url: "./sets/ulamki-zwykle-2.json",     trackLabel: "TRACK 02 · LV.2" },
  { id: "ulamki-dziesietne-1", url: "./sets/ulamki-dziesietne-1.json", trackLabel: "TRACK 03 · LV.1" },
  { id: "dzialania-pisemne-1", url: "./sets/dzialania-pisemne-1.json", trackLabel: "TRACK 04 · LV.1" },
  { id: "demo-typy-pytan",     url: "./sets/demo-typy-pytan.json",     trackLabel: "DEMO · NEW Q" }
];

const $ = (sel, root = document) => root.querySelector(sel);
// Boolean HTML attributes: their *presence* (regardless of value) means "true".
// Pass `true` to set them, `false`/`null`/`undefined` to omit. Never set with a
// non-empty value like "false" or "null" — the browser would still treat it as set.
const BOOL_ATTRS = new Set(["disabled", "checked", "readonly", "multiple", "required", "autofocus", "hidden"]);
const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;  // skip null / undefined / false attrs
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2).toLowerCase(), v);
    else if (BOOL_ATTRS.has(k)) { if (v) node.setAttribute(k, ""); }
    else node.setAttribute(k, v);
  }
  for (const c of children) {
    if (c == null) continue;
    if (typeof c === "string") node.appendChild(document.createTextNode(c));
    else node.appendChild(c);
  }
  return node;
};

const renderMath = (root) => {
  if (window.renderMathInElement) {
    window.renderMathInElement(root, {
      delimiters: [
        { left: "$$", right: "$$", display: true },
        { left: "$", right: "$", display: false }
      ],
      throwOnError: false
    });
  }
};

/* ============================================================
 * Numeric answer parser.
 * Accepts: "1/2", " 1 / 2 ", "0.5", "½", "1 1/2", "1½", "1 i 1/2",
 *          and pure integers ("4"). Normalizes to fraction string
 *          (e.g. "1/2") or integer string for comparison.
 * Returns canonical form, or null if not parseable.
 * ============================================================ */
const VULGAR_FRAC = {
  "½": "1/2", "⅓": "1/3", "⅔": "2/3", "¼": "1/4", "¾": "3/4",
  "⅕": "1/5", "⅖": "2/5", "⅗": "3/5", "⅘": "4/5",
  "⅙": "1/6", "⅚": "5/6", "⅛": "1/8", "⅜": "3/8", "⅝": "5/8", "⅞": "7/8"
};

function gcd(a, b) { return b ? gcd(b, a % b) : a; }

function reduceFraction(num, den) {
  if (den === 0) return null;
  const sign = (num < 0) !== (den < 0) ? -1 : 1;
  num = Math.abs(num); den = Math.abs(den);
  const g = gcd(num, den) || 1;
  return { num: sign * num / g, den: den / g };
}

function parseAnswer(raw) {
  if (raw == null) return null;
  let s = String(raw).trim().toLowerCase();
  if (!s) return null;
  // Replace vulgar fractions
  for (const [u, asc] of Object.entries(VULGAR_FRAC)) s = s.replaceAll(u, " " + asc);
  // Replace polish " i " between whole and frac ("1 i 1/2")
  s = s.replace(/\bi\b/g, " ");
  // Collapse whitespace
  s = s.replace(/\s+/g, " ").trim();
  // Pure integer
  if (/^-?\d+$/.test(s)) {
    const n = parseInt(s, 10);
    return reduceFraction(n, 1);
  }
  // Decimal
  if (/^-?\d+\.\d+$/.test(s)) {
    const f = parseFloat(s);
    // Limit denominator to 1000 for reasonable comparison
    const denom = 1000;
    return reduceFraction(Math.round(f * denom), denom);
  }
  // Mixed: "a b/c"
  let m = s.match(/^(-?\d+)\s+(\d+)\s*\/\s*(\d+)$/);
  if (m) {
    const whole = parseInt(m[1], 10);
    const num = parseInt(m[2], 10);
    const den = parseInt(m[3], 10);
    const sign = whole < 0 ? -1 : 1;
    const total = Math.abs(whole) * den + num;
    return reduceFraction(sign * total, den);
  }
  // Simple fraction: "a/b"
  m = s.match(/^(-?\d+)\s*\/\s*(\d+)$/);
  if (m) {
    return reduceFraction(parseInt(m[1], 10), parseInt(m[2], 10));
  }
  return null;
}

function answersEqual(userRaw, correctRaw) {
  // Special-case for non-reduced answers: e.g. question asks for "with denominator 10",
  // expecting "5/10", not the reduced "1/2". We treat them as equal IF the reduced form matches,
  // OR if the literal denominator matches (when the prompt locks denominator).
  const u = parseAnswer(userRaw);
  const c = parseAnswer(correctRaw);
  if (!u || !c) return false;
  // Both already in lowest terms via reduceFraction. Equal iff num & den match.
  return u.num === c.num && u.den === c.den;
}

/* ============================================================
 * State & persistence
 * ============================================================ */
const State = {
  view: "welcome",
  student: localStorage.getItem("practice.student") || null,
  set: null,
  setData: null,
  currentIdx: 0,
  questionState: {}, // qId -> { status, attempts, answer }
  startTime: null,

  storageKey() {
    return `practice.progress.${this.student}.${this.set?.id}`;
  },
  load() {
    if (!this.student || !this.set) return;
    try {
      const saved = JSON.parse(localStorage.getItem(this.storageKey()) || "{}");
      this.questionState = saved.questionState || {};
      this.currentIdx = saved.currentIdx || 0;
    } catch (e) { this.questionState = {}; this.currentIdx = 0; }
  },
  save() {
    if (!this.student || !this.set) return;
    localStorage.setItem(this.storageKey(), JSON.stringify({
      questionState: this.questionState,
      currentIdx: this.currentIdx,
    }));
  },
  reset() {
    if (!this.student || !this.set) return;
    localStorage.removeItem(this.storageKey());
    this.questionState = {};
    this.currentIdx = 0;
  },
  qState(qId) {
    if (!this.questionState[qId]) {
      this.questionState[qId] = { status: "pending", attempts: 0 };
    }
    return this.questionState[qId];
  },
  passedCount() {
    return Object.values(this.questionState).filter(s => s.status === "passed").length;
  },
  failedCount() {
    return Object.values(this.questionState).filter(s => s.status === "failed").length;
  },
  isComplete() {
    if (!this.setData) return false;
    return this.setData.questions.every(q => {
      const s = this.questionState[q.id];
      return s && (s.status === "passed" || s.status === "failed");
    });
  },
};

/* ============================================================
 * Reset confirm — modal overlay, two buttons.
 * ============================================================ */
function confirmReset() {
  // Don't stack overlays
  if ($(".modal-backdrop")) return;
  const backdrop = el("div", { class: "modal-backdrop" });
  const dialog = el("div", { class: "modal" },
    el("div", { class: "modal-title" }, "restart stage?"),
    el("div", { class: "modal-body" },
      "Cały postęp w tym zestawie zostanie wyzerowany. Tej operacji nie cofniesz."
    ),
    el("div", { class: "modal-actions" },
      el("button", {
        class: "btn btn-secondary",
        onclick: () => backdrop.remove()
      }, "Anuluj"),
      el("button", {
        class: "btn btn-danger",
        onclick: () => {
          State.reset();
          State.view = "running";
          State.currentIdx = 0;
          backdrop.remove();
          render();
        }
      }, "Tak, zeruj")
    )
  );
  backdrop.appendChild(dialog);
  // Tap on backdrop (outside dialog) cancels
  backdrop.addEventListener("click", (e) => { if (e.target === backdrop) backdrop.remove(); });
  document.body.appendChild(backdrop);
}

/* ============================================================
 * Rendering
 * ============================================================ */
function render() {
  const root = $("#app");
  root.innerHTML = "";
  if (State.view === "welcome") return renderWelcome(root);
  if (State.view === "running") return renderRunning(root);
  if (State.view === "summary") return renderSummary(root);
}

function renderWelcome(root) {
  if (!State.student) {
    const wrap = el("div", { class: "welcome" });
    wrap.appendChild(el("div", { class: "tagline" }, "PRESS ★ START"));
    wrap.appendChild(el("h1", {}, "STAGE ☆ math"));
    wrap.appendChild(el("p", {}, "Kto gra?"));
    for (const name of STUDENTS) {
      wrap.appendChild(el("button", {
        class: "student-btn",
        onclick: () => {
          State.student = name;
          localStorage.setItem("practice.student", name);
          applySkin(getSkin(name));
          render();
        }
      }, name));
    }
    root.appendChild(wrap);
    return;
  }

  // Set picker
  const wrap = el("div", { class: "welcome" });
  wrap.appendChild(el("div", { class: "tagline" }, `★ player ${State.student.toLowerCase()} ★`));

  // Skin picker — 4 small cards with name + tagline + swatches.
  const currentSkin = getSkin(State.student);
  const skinRow = el("div", { class: "skin-picker" });
  for (const skin of SKINS) {
    const isCurrent = skin.id === currentSkin;
    const card = el("button", {
      class: "skin-card" + (isCurrent ? " is-current" : ""),
      "aria-pressed": isCurrent ? "true" : "false",
      "aria-label": `Skin ${skin.name}`,
      onclick: () => { setSkin(State.student, skin.id); render(); }
    });
    const swatches = el("div", { class: "skin-swatches" });
    for (const c of skin.swatches) swatches.appendChild(el("span", { class: "skin-swatch", style: `background:${c}` }));
    card.appendChild(swatches);
    card.appendChild(el("div", { class: "skin-name" }, skin.name));
    card.appendChild(el("div", { class: "skin-tag" }, skin.tagline));
    skinRow.appendChild(card);
  }
  wrap.appendChild(skinRow);

  wrap.appendChild(el("h1", {}, "wybierz set"));
  wrap.appendChild(el("p", {}, "Każdy zestaw to nowy stage. Hearts = ile pomyłek możesz mieć. Powodzenia!"));
  const list = el("div", { class: "set-list" });
  // Preload set metadata for each
  for (const s of SETS) {
    const btn = el("button", { class: "set-btn", onclick: () => startSet(s) });
    const title = el("div", { class: "set-title" }, "…");
    const sub = el("div", { class: "set-sub" });
    const trackBadge = el("div", { class: "set-track" }, s.trackLabel || "TRACK");
    const progBar = el("div", { class: "set-progress" });
    const progFill = el("div", { class: "set-progress-fill", style: "width: 0%" });
    progBar.appendChild(progFill);
    btn.appendChild(trackBadge);
    btn.appendChild(title); btn.appendChild(sub); btn.appendChild(progBar);
    list.appendChild(btn);

    fetch(s.url).then(r => r.json()).then(d => {
      title.textContent = d.title;
      sub.textContent = d.subtitle;
      // Read saved progress
      try {
        const saved = JSON.parse(localStorage.getItem(`practice.progress.${State.student}.${s.id}`) || "{}");
        const states = saved.questionState || {};
        const done = Object.values(states).filter(q => q.status === "passed" || q.status === "failed").length;
        const pct = Math.round((done / d.questions.length) * 100);
        progFill.style.width = pct + "%";
        if (done > 0) sub.textContent += ` · ${done}/${d.questions.length} zrobione`;
      } catch (e) {}
    });
  }
  wrap.appendChild(list);

  const switchBtn = el("button", {
    class: "set-btn",
    style: "margin-top: 24px; text-align: center; font-size: 14px; color: var(--gray-600); padding: 12px;",
    onclick: () => {
      State.student = null;
      localStorage.removeItem("practice.student");
      applySkin("stage");  // back to default until a player is picked again
      render();
    }
  }, "switch player ⇆");
  wrap.appendChild(switchBtn);

  root.appendChild(wrap);
}

async function startSet(setMeta) {
  const resp = await fetch(setMeta.url);
  State.setData = await resp.json();
  State.set = setMeta;
  State.load();
  // If complete, go straight to summary
  if (State.isComplete()) {
    State.view = "summary";
  } else {
    State.view = "running";
    // Find first pending if currentIdx is out of bounds or completed
    if (State.currentIdx >= State.setData.questions.length) State.currentIdx = 0;
    const cur = State.setData.questions[State.currentIdx];
    if (cur) {
      const st = State.qState(cur.id);
      if (st.status === "passed" || st.status === "failed") {
        const next = State.setData.questions.findIndex(q => {
          const s = State.questionState[q.id];
          return !s || (s.status !== "passed" && s.status !== "failed");
        });
        if (next >= 0) State.currentIdx = next;
      }
    }
  }
  render();
}

function renderRunning(root) {
  const q = State.setData.questions[State.currentIdx];
  if (!q) { State.view = "summary"; render(); return; }

  // Top bar
  const topbar = el("div", { class: "topbar" });
  topbar.appendChild(el("button", {
    class: "close",
    onclick: () => { State.view = "welcome"; render(); },
    title: "Zamknij"
  }, "×"));
  const progBar = el("div", { class: "progress" });
  const totalDone = State.passedCount() + State.failedCount();
  const pct = Math.round((totalDone / State.setData.questions.length) * 100);
  progBar.appendChild(el("div", { class: "progress-fill", style: `width: ${pct}%` }));
  topbar.appendChild(progBar);
  const lives = el("div", { class: "lives" },
    el("span", { class: "lives-icon" }, "❤️"),
    String(Math.max(0, State.setData.questions.length - State.failedCount()))
  );
  topbar.appendChild(lives);
  topbar.appendChild(el("button", {
    class: "topbar-reset",
    onclick: confirmReset,
    title: "Zacznij od nowa"
  }, "↻"));
  root.appendChild(topbar);

  // Dots
  const dots = el("div", { class: "dots" });
  State.setData.questions.forEach((qq, i) => {
    const s = State.questionState[qq.id];
    const status = s?.status || "pending";
    const cls = "dot " + (i === State.currentIdx ? "current " : "") + (status === "passed" ? "passed" : status === "failed" ? "failed" : "");
    const d = el("div", {
      class: cls,
      onclick: () => { State.currentIdx = i; render(); }
    }, String(i + 1));
    dots.appendChild(d);
  });
  root.appendChild(dots);

  // Card
  const card = el("div", { class: "card-wrap" });
  card.appendChild(el("div", { class: "section-label" }, q.section || ""));
  const prompt = el("div", { class: "prompt" });
  prompt.innerHTML = renderInlineMd(q.prompt);
  card.appendChild(prompt);

  const qState = State.qState(q.id);
  const locked = qState.status === "passed" || qState.status === "failed";

  let answerArea;
  if (q.type === "mcq") answerArea = renderMCQ(q, qState, locked);
  else if (q.type === "multi-mcq") answerArea = renderMultiMCQ(q, qState, locked);
  else if (q.type === "numeric") answerArea = renderNumeric(q, qState, locked);
  else if (q.type === "ordering") answerArea = renderOrdering(q, qState, locked);
  else if (q.type === "truefalse") answerArea = renderTrueFalse(q, qState, locked);
  else if (q.type === "matching") answerArea = renderMatching(q, qState, locked);
  else if (q.type === "numberline") answerArea = renderNumberLine(q, qState, locked);
  else answerArea = el("div", {}, "Nieobsługiwany typ pytania");

  card.appendChild(answerArea);
  root.appendChild(card);
  renderMath(card);

  // Nav arrows at bottom
  const nav = el("div", { class: "nav" });
  nav.appendChild(el("button", {
    class: "btn btn-secondary",
    onclick: () => { if (State.currentIdx > 0) { State.currentIdx--; State.save(); render(); } }
  }, "← prev"));
  nav.appendChild(el("button", {
    class: "btn btn-secondary",
    onclick: () => {
      if (State.currentIdx < State.setData.questions.length - 1) { State.currentIdx++; State.save(); render(); }
      else if (State.isComplete()) { State.view = "summary"; render(); }
    }
  }, "next →"));
  root.appendChild(nav);
}

/* ============================================================
 * Inline-markdown-ish: just supports **bold**. Math handled by KaTeX.
 * ============================================================ */
function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function renderInlineMd(s) {
  // We keep KaTeX delimiters intact; escape rest then bold.
  // Split by $...$ blocks first
  const parts = s.split(/(\$[^$]+\$|\$\$[^$]+\$\$)/);
  return parts.map(part => {
    if (part.startsWith("$")) return part; // math, KaTeX renders later
    return escapeHtml(part).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  }).join("");
}

/* ============================================================
 * MCQ
 * ============================================================ */
function renderMCQ(q, qState, locked) {
  const wrap = el("div", { class: "choices" });
  const correctChoice = q.choices.find(c => c.correct);
  let selected = null;

  q.choices.forEach((c, i) => {
    const bullet = el("div", { class: "choice-bullet" }, String.fromCharCode(65 + i));
    const lbl = el("div", { class: "choice-label", html: renderInlineMd(c.label) });
    const btn = el("button", {
      class: "choice" + (locked ? " disabled" : ""),
      onclick: () => {
        if (locked) return;
        // Mark selected
        [...wrap.children].forEach(ch => ch.classList.remove("selected"));
        btn.classList.add("selected");
        selected = c;
        $(".js-check").disabled = false;
      }
    }, bullet, lbl);
    wrap.appendChild(btn);
  });

  // If locked, color them
  if (locked) {
    [...wrap.children].forEach((ch, i) => {
      if (q.choices[i].correct) ch.classList.add("correct");
      if (qState.lastWrong === q.choices[i].id) ch.classList.add("wrong");
    });
  }

  // Submit button
  const submitBar = el("div", { class: "feedback" });
  const checkBtn = el("button", {
    class: "btn btn-primary js-check",
    disabled: true,
    onclick: () => {
      if (!selected) return;
      handleAnswer(q, qState, selected.correct, selected.id, correctChoice);
    }
  }, "Sprawdź");
  submitBar.appendChild(checkBtn);
  wrap.appendChild(submitBar);

  return wrap;
}

/* ============================================================
 * Multi-select MCQ — all-or-nothing for now.
 * ============================================================ */
function renderMultiMCQ(q, qState, locked) {
  const wrap = el("div", { class: "choices" });
  const selected = new Set();

  q.choices.forEach((c, i) => {
    const bullet = el("div", { class: "choice-bullet" }, String.fromCharCode(65 + i));
    const lbl = el("div", { class: "choice-label", html: renderInlineMd(c.label) });
    const btn = el("button", {
      class: "choice" + (locked ? " disabled" : ""),
      onclick: () => {
        if (locked) return;
        if (selected.has(c.id)) { selected.delete(c.id); btn.classList.remove("selected"); }
        else { selected.add(c.id); btn.classList.add("selected"); }
        $(".js-check").disabled = selected.size === 0;
      }
    }, bullet, lbl);
    wrap.appendChild(btn);
  });

  if (locked) {
    [...wrap.children].forEach((ch, i) => {
      if (q.choices[i].correct) ch.classList.add("correct");
    });
  }

  const submitBar = el("div", { class: "feedback" });
  const checkBtn = el("button", {
    class: "btn btn-primary js-check",
    disabled: true,
    onclick: () => {
      const allCorrect = q.choices.every(c => c.correct === selected.has(c.id));
      handleAnswer(q, qState, allCorrect, [...selected].sort().join(","), q.choices.filter(c => c.correct).map(c => c.label).join(", "));
    }
  }, "Sprawdź");
  submitBar.appendChild(checkBtn);
  wrap.appendChild(submitBar);
  return wrap;
}

/* ============================================================
 * Numeric input
 * ============================================================ */
function renderNumeric(q, qState, locked) {
  const wrap = el("div", { class: "numeric-input" });

  // Optional fill-in-the-blank template. e.g. "2 + ___ = 5" or "$\\dfrac{1}{2} + $___$ = 1$".
  // Renders above the input. Blank live-replaces with the typed value.
  // We split on ___ FIRST so KaTeX can render each math segment cleanly without
  // an HTML span breaking the $...$ block.
  const hasTemplate = typeof q.template === "string" && q.template.includes("___");
  const tplEl = hasTemplate ? el("div", { class: "numeric-template" }) : null;
  const renderTemplate = (val) => {
    if (!tplEl) return;
    tplEl.innerHTML = "";
    const [before, after = ""] = q.template.split("___");
    const beforeEl = el("span", { html: renderInlineMd(before) });
    const fillEl = val
      ? el("span", { class: "numeric-template-fill" }, val)
      : el("span", { class: "numeric-template-blank" }, "___");
    const afterEl = el("span", { html: renderInlineMd(after) });
    tplEl.appendChild(beforeEl);
    tplEl.appendChild(fillEl);
    tplEl.appendChild(afterEl);
    renderMath(tplEl);
  };
  if (tplEl) wrap.appendChild(tplEl);

  // Display field — read-only on touch, all input via custom keypad below.
  // inputmode="none" tells iOS not to show the system keyboard even when focused.
  const input = el("input", {
    type: "text",
    inputmode: "none",
    autocomplete: "off",
    autocorrect: "off",
    spellcheck: "false",
    placeholder: q.answer.includes("/") ? "np. 1/2" : "wpisz odpowiedź",
    readonly: true,
    disabled: locked
  });
  if (locked) {
    if (qState.status === "passed") {
      input.value = qState.lastAnswer || q.answer;
      input.classList.add("correct");
    } else {
      input.value = qState.lastAnswer || "";
      input.classList.add("wrong");
    }
  }
  renderTemplate(input.value);
  wrap.appendChild(input);
  wrap.appendChild(el("div", { class: "hint-help" },
    "Ułamek wpisz jako np. 1/2. Liczba mieszana: 1 1/2."
  ));

  const submit = () => {
    const raw = input.value.trim();
    if (!raw) return;
    const correct = answersEqual(raw, q.answer);
    if (!correct) {
      input.classList.remove("correct");
      input.classList.add("wrong");
      setTimeout(() => input.classList.remove("wrong"), 600);
    }
    handleAnswer(q, qState, correct, raw, q.answer);
  };

  // Custom on-screen keypad. iOS native number pad has no '/', so we roll our own.
  // Calculator layout (7-8-9 top) to match iOS .numberPad. 56pt+ tap targets per HIG.
  const press = (fn) => (e) => {
    e.preventDefault();
    if (locked) return;
    if (navigator.vibrate) navigator.vibrate(8);
    fn();
  };
  const insert = (ch) => { input.value = input.value + ch; renderTemplate(input.value); };
  const backspace = () => { input.value = input.value.slice(0, -1); renderTemplate(input.value); };

  const mkKey = (label, kind, onPress, opts = {}) => {
    const cls = "kp-key kp-" + kind + (opts.wide ? " kp-wide" : "");
    return el("button", {
      type: "button",
      class: cls,
      disabled: locked,
      "aria-label": opts.ariaLabel || label,
      ontouchstart: press(onPress),
      onclick: (e) => { if (e.detail === 0 || !("ontouchstart" in window)) press(onPress)(e); }
    }, label);
  };

  // Calculator layout: digits 7-8-9 / 4-5-6 / 1-2-3 in left 3 cols, ops in 4th col,
  // bottom row: 0 (wide-2), then Sprawdź (wide-2). Backspace top-right, /  middle-right,
  // space bottom-right of the op column.
  const keypad = el("div", { class: "keypad", role: "group", "aria-label": "Klawiatura numeryczna" },
    mkKey("7", "num", () => insert("7")),
    mkKey("8", "num", () => insert("8")),
    mkKey("9", "num", () => insert("9")),
    mkKey("⌫", "del", backspace, { ariaLabel: "Cofnij" }),
    mkKey("4", "num", () => insert("4")),
    mkKey("5", "num", () => insert("5")),
    mkKey("6", "num", () => insert("6")),
    mkKey("/", "op", () => insert("/"), { ariaLabel: "Kreska ułamkowa" }),
    mkKey("1", "num", () => insert("1")),
    mkKey("2", "num", () => insert("2")),
    mkKey("3", "num", () => insert("3")),
    mkKey("␣", "op", () => insert(" "), { ariaLabel: "Spacja" }),
    mkKey("0", "num", () => insert("0"), { wide: true }),
    mkKey("Sprawdź", "check", submit, { ariaLabel: "Sprawdź odpowiedź", wide: true })
  );
  wrap.appendChild(keypad);

  // Hardware keyboard support kept for desktop testing (and external iPad keyboards).
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); submit(); }
  });
  return wrap;
}

/* ============================================================
 * Ordering — tap pool tokens to fill slots in order
 * ============================================================ */
function renderOrdering(q, qState, locked) {
  const wrap = el("div", { class: "ordering" });
  const slots = [];
  const placed = new Array(q.items.length).fill(null); // ids in order

  for (let i = 0; i < q.items.length; i++) {
    const slot = el("div", { class: "ordering-slot empty" },
      el("div", { class: "ordering-slot-num" }, String(i + 1)),
      el("div", { class: "ordering-slot-content" }, "—")
    );
    slot.addEventListener("click", () => {
      // Remove the token at this slot
      if (locked) return;
      if (placed[i]) {
        placed[i] = null;
        slot.classList.add("empty");
        $(".ordering-slot-content", slot).innerHTML = "—";
        renderTokens();
        updateCheckButton();
      }
    });
    wrap.appendChild(slot);
    slots.push(slot);
  }

  const pool = el("div", { class: "ordering-pool" });
  function renderTokens() {
    pool.innerHTML = "";
    q.items.forEach(item => {
      const used = placed.includes(item.id);
      const tok = el("div", {
        class: "ordering-token" + (used ? " used" : ""),
        html: renderInlineMd(item.label)
      });
      if (!used && !locked) {
        tok.addEventListener("click", () => {
          // Place into first empty slot
          const idx = placed.findIndex(p => p === null);
          if (idx < 0) return;
          placed[idx] = item.id;
          slots[idx].classList.remove("empty");
          $(".ordering-slot-content", slots[idx]).innerHTML = renderInlineMd(item.label);
          renderMath(slots[idx]);
          renderTokens();
          updateCheckButton();
        });
      }
      pool.appendChild(tok);
    });
    renderMath(pool);
  }
  wrap.appendChild(pool);
  renderTokens();

  function updateCheckButton() {
    $(".js-check").disabled = placed.some(p => p === null);
  }

  // Pre-populate if locked
  if (locked && qState.lastAnswer) {
    qState.lastAnswer.forEach((id, i) => {
      placed[i] = id;
      const item = q.items.find(it => it.id === id);
      slots[i].classList.remove("empty");
      $(".ordering-slot-content", slots[i]).innerHTML = renderInlineMd(item.label);
      const correctId = q.answer[i];
      slots[i].classList.add(id === correctId ? "correct" : "wrong");
    });
    renderTokens();
    renderMath(wrap);
  }

  const submitBar = el("div", { class: "feedback" });
  const checkBtn = el("button", {
    class: "btn btn-primary js-check",
    disabled: true,
    onclick: () => {
      const correct = placed.every((p, i) => p === q.answer[i]);
      // Color the slots
      placed.forEach((id, i) => {
        const correctId = q.answer[i];
        slots[i].classList.add(id === correctId ? "correct" : "wrong");
      });
      const correctOrderLabels = q.answer.map(id => q.items.find(it => it.id === id).label).join(" < ");
      handleAnswer(q, qState, correct, placed.slice(), correctOrderLabels);
    }
  }, "Sprawdź");
  submitBar.appendChild(checkBtn);
  wrap.appendChild(submitBar);
  return wrap;
}

/* ============================================================
 * True / False — q.answer is boolean. Two big buttons, instant submit.
 * ============================================================ */
function renderTrueFalse(q, qState, locked) {
  const wrap = el("div", { class: "tf-wrap" });
  const labels = [
    { val: true,  text: "PRAWDA",   cls: "tf-true" },
    { val: false, text: "FAŁSZ",    cls: "tf-false" }
  ];
  for (const opt of labels) {
    const btn = el("button", {
      class: "tf-btn " + opt.cls + (locked && qState.lastAnswer === opt.val ? (qState.status === "passed" ? " correct" : " wrong") : "") +
             (locked && q.answer === opt.val ? " is-answer" : ""),
      disabled: locked,
      onclick: () => {
        if (locked) return;
        const correct = opt.val === q.answer;
        handleAnswer(q, qState, correct, opt.val, q.answer ? "PRAWDA" : "FAŁSZ");
      }
    }, opt.text);
    wrap.appendChild(btn);
  }
  return wrap;
}

/* ============================================================
 * Matching pairs — q.left[] and q.right[] each have id + label,
 * plus q.pairs[] = [[leftId, rightId], …]. Tap left then right
 * to pair; same color highlights both. Submit when all paired.
 * ============================================================ */
function renderMatching(q, qState, locked) {
  const wrap = el("div", { class: "matching-wrap" });
  // Inline how-to so kids know what to tap. Disappears once they make their first pairing.
  const hint = el("div", { class: "matching-hint" }, "Stuknij ułamek po lewej, potem jego parę po prawej.");
  wrap.appendChild(hint);
  const grid = el("div", { class: "matching" });
  // Pair colors cycle through these so each pair is visually distinct.
  const PAIR_COLORS = ["var(--hot)", "var(--ultra)", "var(--mint)", "var(--sun)", "#ff8a3d", "#3da6ff"];
  const leftEls = new Map();   // id -> button
  const rightEls = new Map();  // id -> button
  const pairings = new Map();  // leftId -> rightId
  let pendingLeft = null;

  const correctMap = new Map(q.pairs.map(([l, r]) => [l, r]));
  // Pre-fill from saved state (replay on locked)
  if (locked && qState.lastAnswer) {
    try {
      const saved = JSON.parse(qState.lastAnswer);
      for (const [l, r] of saved) pairings.set(l, r);
    } catch {}
  }

  const leftCol = el("div", { class: "matching-col" });
  const rightCol = el("div", { class: "matching-col" });
  const divider = el("div", { class: "matching-divider", "aria-hidden": "true" });
  const hasHeaders = !!(q.leftHeader || q.rightHeader);
  const leftHeaderEl = hasHeaders
    ? el("div", { class: "matching-header", html: renderInlineMd(q.leftHeader || "") })
    : null;
  const rightHeaderEl = hasHeaders
    ? el("div", { class: "matching-header", html: renderInlineMd(q.rightHeader || "") })
    : null;

  const colorFor = (leftId) => {
    const idx = [...pairings.keys()].indexOf(leftId);
    return idx >= 0 ? PAIR_COLORS[idx % PAIR_COLORS.length] : null;
  };

  const refresh = () => {
    leftEls.forEach((btn, id) => {
      const c = colorFor(id);
      btn.classList.toggle("paired", !!c);
      btn.classList.toggle("pending", pendingLeft === id);
      btn.style.setProperty("--pair-color", c || "transparent");
      // Locked feedback colors
      if (locked) {
        btn.classList.toggle("correct", pairings.get(id) === correctMap.get(id));
        btn.classList.toggle("wrong", pairings.get(id) && pairings.get(id) !== correctMap.get(id));
      }
    });
    rightEls.forEach((btn, id) => {
      const leftId = [...pairings.entries()].find(([, r]) => r === id)?.[0];
      const c = leftId ? colorFor(leftId) : null;
      btn.classList.toggle("paired", !!c);
      btn.style.setProperty("--pair-color", c || "transparent");
      if (locked) {
        const isCorrectTarget = leftId && correctMap.get(leftId) === id;
        btn.classList.toggle("correct", isCorrectTarget);
        btn.classList.toggle("wrong", leftId && !isCorrectTarget);
      }
    });
    checkBtn.disabled = locked || pairings.size !== q.left.length;
    // Hide instruction hint once the kid figures out the mechanic (first pair made).
    hint.classList.toggle("hidden", pairings.size > 0 || pendingLeft != null);
  };

  for (const item of q.left) {
    const btn = el("button", {
      class: "match-card match-left",
      disabled: locked,
      html: renderInlineMd(item.label),
      onclick: () => {
        if (locked) return;
        // Tapping an already-paired left clears it.
        if (pairings.has(item.id)) { pairings.delete(item.id); pendingLeft = null; refresh(); return; }
        pendingLeft = pendingLeft === item.id ? null : item.id;
        refresh();
      }
    });
    leftEls.set(item.id, btn);
    leftCol.appendChild(btn);
  }
  for (const item of q.right) {
    const btn = el("button", {
      class: "match-card match-right",
      disabled: locked,
      html: renderInlineMd(item.label),
      onclick: () => {
        if (locked) return;
        // Tapping an already-paired right clears it.
        const owner = [...pairings.entries()].find(([, r]) => r === item.id)?.[0];
        if (owner) { pairings.delete(owner); refresh(); return; }
        if (pendingLeft == null) return;
        pairings.set(pendingLeft, item.id);
        pendingLeft = null;
        refresh();
      }
    });
    rightEls.set(item.id, btn);
    rightCol.appendChild(btn);
  }

  // Explicit grid placement so the divider can span all rows in col 2 without a placeholder cell.
  if (hasHeaders) {
    leftHeaderEl.style.gridArea = "1 / 1";
    rightHeaderEl.style.gridArea = "1 / 3";
    leftCol.style.gridArea = "2 / 1";
    rightCol.style.gridArea = "2 / 3";
    divider.style.gridColumn = "2";
    divider.style.gridRow = "1 / 3";
    grid.appendChild(leftHeaderEl);
    grid.appendChild(rightHeaderEl);
  } else {
    leftCol.style.gridArea = "1 / 1";
    rightCol.style.gridArea = "1 / 3";
    divider.style.gridColumn = "2";
    divider.style.gridRow = "1";
  }
  grid.appendChild(leftCol);
  grid.appendChild(divider);
  grid.appendChild(rightCol);
  wrap.appendChild(grid);

  const submitBar = el("div", { class: "feedback" });
  const checkBtn = el("button", {
    class: "btn btn-primary",
    disabled: true,
    onclick: () => {
      const allCorrect = q.left.every(item => pairings.get(item.id) === correctMap.get(item.id));
      const userAnswer = JSON.stringify([...pairings.entries()]);
      const correctLabel = q.pairs.map(([l, r]) => {
        const ll = q.left.find(x => x.id === l)?.label || l;
        const rr = q.right.find(x => x.id === r)?.label || r;
        return `${ll} = ${rr}`;
      }).join(", ");
      handleAnswer(q, qState, allCorrect, userAnswer, correctLabel);
    }
  }, "Sprawdź");
  submitBar.appendChild(checkBtn);
  wrap.appendChild(submitBar);

  refresh();
  renderMath(wrap);
  return wrap;
}

/* ============================================================
 * Number-line tap — q has min, max, step, answer (target value).
 * Tap on the line snaps to nearest tick. Submit validates.
 * ============================================================ */
function renderNumberLine(q, qState, locked) {
  const wrap = el("div", { class: "numline-wrap" });
  const min = q.min, max = q.max, step = q.step ?? 1;
  const ticks = [];
  for (let v = min; v <= max + 1e-9; v += step) ticks.push(Number(v.toFixed(6)));

  const line = el("div", { class: "numline" });
  const track = el("div", { class: "numline-track" });
  const marker = el("div", { class: "numline-marker hidden" });
  let chosen = null;

  // Restore prior selection on locked
  if (locked && qState.lastAnswer != null) {
    chosen = Number(qState.lastAnswer);
  }

  const tickBar = el("div", { class: "numline-ticks" });
  ticks.forEach((v, i) => {
    const t = el("div", { class: "numline-tick" });
    const pct = ((v - min) / (max - min)) * 100;
    t.style.left = pct + "%";
    const lbl = el("div", { class: "numline-tick-label" }, formatTickLabel(v));
    t.appendChild(lbl);
    tickBar.appendChild(t);
  });

  const positionMarker = (val) => {
    const pct = ((val - min) / (max - min)) * 100;
    marker.style.left = pct + "%";
    marker.classList.remove("hidden");
    if (locked) {
      marker.classList.toggle("correct", Math.abs(val - q.answer) < 1e-9);
      marker.classList.toggle("wrong",   Math.abs(val - q.answer) >= 1e-9);
    }
  };

  const onTap = (e) => {
    if (locked) return;
    const rect = track.getBoundingClientRect();
    const x = (e.touches?.[0]?.clientX ?? e.clientX) - rect.left;
    const ratio = Math.max(0, Math.min(1, x / rect.width));
    const raw = min + ratio * (max - min);
    // Snap to nearest tick
    chosen = ticks.reduce((best, v) => Math.abs(v - raw) < Math.abs(best - raw) ? v : best, ticks[0]);
    positionMarker(chosen);
    checkBtn.disabled = false;
  };
  track.addEventListener("click", onTap);
  track.addEventListener("touchstart", (e) => { e.preventDefault(); onTap(e); }, { passive: false });

  track.appendChild(tickBar);
  track.appendChild(marker);
  line.appendChild(track);
  wrap.appendChild(line);

  if (chosen != null) positionMarker(chosen);

  // If locked + wrong, also show the correct answer marker
  if (locked && chosen != null && Math.abs(chosen - q.answer) >= 1e-9) {
    const correctMarker = el("div", { class: "numline-marker numline-correct-ghost" });
    const pct = ((q.answer - min) / (max - min)) * 100;
    correctMarker.style.left = pct + "%";
    track.appendChild(correctMarker);
  }

  const submitBar = el("div", { class: "feedback" });
  const checkBtn = el("button", {
    class: "btn btn-primary",
    disabled: locked || chosen == null,
    onclick: () => {
      if (chosen == null) return;
      const correct = Math.abs(chosen - q.answer) < 1e-9;
      handleAnswer(q, qState, correct, String(chosen), formatTickLabel(q.answer));
    }
  }, "Sprawdź");
  submitBar.appendChild(checkBtn);
  wrap.appendChild(submitBar);
  return wrap;
}

function formatTickLabel(v) {
  // Show fractions for halves/quarters; otherwise plain.
  if (Number.isInteger(v)) return String(v);
  const r = Math.round(v * 4) / 4;
  if (Math.abs(r - v) < 1e-6) {
    if (r === 0.25) return "¼"; if (r === 0.5) return "½"; if (r === 0.75) return "¾";
    if (r === 1.25) return "1¼"; if (r === 1.5) return "1½"; if (r === 1.75) return "1¾";
  }
  return String(Math.round(v * 100) / 100);
}

/* ============================================================
 * Answer handler — common to all types.
 * ============================================================ */
function handleAnswer(q, qState, isCorrect, userAnswer, correctAnswerLabel) {
  qState.attempts = (qState.attempts || 0) + 1;
  qState.lastAnswer = userAnswer;

  const root = $("#app");
  // Remove any existing post-answer feedback
  $(".card-wrap")?.querySelectorAll(".feedback-result")?.forEach(n => n.remove());

  if (isCorrect) {
    qState.status = "passed";
    showSuccess();
    State.save();
    // Auto-advance after pause
    setTimeout(() => {
      moveNext();
    }, 1200);
    return;
  }

  // Wrong
  if (qState.attempts >= 2) {
    qState.status = "failed";
    qState.lastWrong = (q.type === "mcq" || q.type === "multi-mcq") ? userAnswer : null;
    State.save();
    showFailure(q, correctAnswerLabel);
  } else {
    // Allow another try
    showRetry(q);
  }
}

function showSuccess() {
  const card = $(".card-wrap");
  const banner = el("div", { class: "feedback success feedback-result pop" });
  banner.appendChild(el("div", { class: "feedback-title" }, "★ PERFECT ★"));
  card.appendChild(banner);
  fireConfettiSmall();
}

function showRetry(q) {
  const card = $(".card-wrap");
  const banner = el("div", { class: "feedback fail feedback-result" });
  banner.appendChild(el("div", { class: "feedback-title" }, "miss · spróbuj jeszcze raz"));
  if (q.hint) {
    const hint = el("div", { class: "feedback-hint" });
    hint.innerHTML = "💡 " + renderInlineMd(q.hint);
    banner.appendChild(hint);
    renderMath(banner);
  }
  card.appendChild(banner);
  // Lightly shake the whole card
  card.classList.add("shake");
  setTimeout(() => card.classList.remove("shake"), 500);
}

function showFailure(q, correctLabel) {
  const card = $(".card-wrap");
  const banner = el("div", { class: "feedback fail feedback-result" });
  banner.appendChild(el("div", { class: "feedback-title" }, "× game over · runda dalej"));
  const correctNode = el("div", { class: "feedback-hint" });
  correctNode.innerHTML = "Poprawna odpowiedź: <strong>" + renderInlineMd(String(correctLabel)) + "</strong>";
  banner.appendChild(correctNode);
  if (q.hint) {
    const hint = el("div", { class: "feedback-hint" });
    hint.innerHTML = "💡 " + renderInlineMd(q.hint);
    banner.appendChild(hint);
  }
  const nextBtn = el("button", {
    class: "btn btn-primary",
    onclick: () => moveNext()
  }, "next stage →");
  banner.appendChild(nextBtn);
  card.appendChild(banner);
  renderMath(banner);
}

function moveNext() {
  // Find next pending question; if none, summary
  const total = State.setData.questions.length;
  let next = -1;
  for (let i = 1; i <= total; i++) {
    const idx = (State.currentIdx + i) % total;
    const q = State.setData.questions[idx];
    const s = State.questionState[q.id];
    if (!s || (s.status !== "passed" && s.status !== "failed")) { next = idx; break; }
  }
  if (next < 0) {
    State.view = "summary";
    State.save();
    render();
    return;
  }
  State.currentIdx = next;
  State.save();
  render();
}

/* ============================================================
 * Confetti
 * ============================================================ */
function fireConfettiSmall() {
  const emojis = ["🎉", "⭐", "✨", "🌟"];
  for (let i = 0; i < 10; i++) {
    const c = document.createElement("div");
    c.className = "confetti";
    c.textContent = emojis[i % emojis.length];
    c.style.setProperty("--dx", `${(Math.random() - 0.5) * 400}px`);
    c.style.left = `${50 + (Math.random() - 0.5) * 30}%`;
    c.style.animationDelay = `${Math.random() * 0.3}s`;
    document.body.appendChild(c);
    setTimeout(() => c.remove(), 3000);
  }
}

function fireConfettiBig() {
  const emojis = ["🎉", "⭐", "✨", "🌟", "🏆", "💫"];
  for (let i = 0; i < 40; i++) {
    const c = document.createElement("div");
    c.className = "confetti";
    c.textContent = emojis[i % emojis.length];
    c.style.setProperty("--dx", `${(Math.random() - 0.5) * 600}px`);
    c.style.left = `${Math.random() * 100}%`;
    c.style.animationDelay = `${Math.random() * 0.8}s`;
    c.style.fontSize = `${20 + Math.random() * 16}px`;
    document.body.appendChild(c);
    setTimeout(() => c.remove(), 3500);
  }
}

/* ============================================================
 * Summary screen
 * ============================================================ */
function renderSummary(root) {
  const total = State.setData.questions.length;
  const passed = State.passedCount();
  const failed = State.failedCount();
  const pct = Math.round((passed / total) * 100);

  // Per-section breakdown
  const sections = {};
  State.setData.questions.forEach(q => {
    const sec = q.section || "Inne";
    if (!sections[sec]) sections[sec] = { passed: 0, total: 0 };
    sections[sec].total++;
    if (State.questionState[q.id]?.status === "passed") sections[sec].passed++;
  });

  const wrap = el("div", { class: "summary" });
  let title, rank, tagline;
  if (pct === 100) { title = "perfect run"; rank = "S+"; tagline = "전설 · legend status"; }
  else if (pct >= 80) { title = "stage clear!"; rank = "S"; tagline = "★ comeback win ★"; }
  else if (pct >= 60) { title = "stage clear"; rank = "A"; tagline = "good run · keep going"; }
  else { title = "respawn soon"; rank = "B"; tagline = "powtórka · znowu od początku"; }

  wrap.appendChild(el("div", { class: "tagline" }, tagline));
  wrap.appendChild(el("h1", {}, title));
  wrap.appendChild(el("div", { class: "score-big" }, `${passed}/${total}`));
  wrap.appendChild(el("div", { class: "score-line" }, `RANK ${rank} · ${pct}% Z PIERWSZEJ LUB DRUGIEJ PRÓBY`));

  const list = el("div", { class: "section-list" });
  for (const [sec, v] of Object.entries(sections)) {
    const row = el("div", { class: "section-row" });
    row.appendChild(el("span", {}, sec));
    row.appendChild(el("strong", {}, `${v.passed}/${v.total}`));
    list.appendChild(row);
  }
  wrap.appendChild(list);

  const retry = el("button", {
    class: "btn btn-primary",
    style: "margin-top: 24px;",
    onclick: () => {
      State.reset();
      State.view = "running";
      State.currentIdx = 0;
      render();
    }
  }, "↻ replay stage");
  wrap.appendChild(retry);

  const back = el("button", {
    class: "btn btn-secondary",
    onclick: () => { State.view = "welcome"; render(); }
  }, "← menu");
  wrap.appendChild(back);

  root.appendChild(wrap);
  renderMath(wrap);
  if (pct >= 80) fireConfettiBig();
}

/* ============================================================
 * Boot
 * ============================================================ */
window.addEventListener("DOMContentLoaded", () => {
  // Apply the saved skin for the current student (or default Stage if no student yet).
  applySkin(State.student ? getSkin(State.student) : "stage");
  // Wait for KaTeX
  const tryRender = () => {
    if (window.renderMathInElement) render();
    else setTimeout(tryRender, 50);
  };
  tryRender();
});
