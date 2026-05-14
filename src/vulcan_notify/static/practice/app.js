"use strict";

/* ============================================================
 * Kids practice — single-page app, vanilla JS.
 * State machine: welcome -> set-picker -> running -> summary.
 * Per-question state: pending | attempt1-wrong | passed | failed.
 * Persistence: localStorage, keyed by student + setId.
 * ============================================================ */

const STUDENTS = ["Solomiia", "Yarema"];
const SETS = [
  { id: "ulamki-zwykle-1", url: "./sets/ulamki-zwykle-1.json" }
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
    wrap.appendChild(el("h1", {}, "Cześć! 👋"));
    wrap.appendChild(el("p", {}, "Wybierz uczennicę/ucznia:"));
    for (const name of STUDENTS) {
      wrap.appendChild(el("button", {
        class: "student-btn",
        onclick: () => { State.student = name; localStorage.setItem("practice.student", name); render(); }
      }, name));
    }
    root.appendChild(wrap);
    return;
  }

  // Set picker
  const wrap = el("div", { class: "welcome" });
  wrap.appendChild(el("h1", {}, `Hej, ${State.student}!`));
  wrap.appendChild(el("p", {}, "Wybierz zestaw ćwiczeń:"));
  const list = el("div", { class: "set-list" });
  // Preload set metadata for each
  for (const s of SETS) {
    const btn = el("button", { class: "set-btn", onclick: () => startSet(s) });
    const title = el("div", { class: "set-title" }, "…");
    const sub = el("div", { class: "set-sub" });
    const progBar = el("div", { class: "set-progress" });
    const progFill = el("div", { class: "set-progress-fill", style: "width: 0%" });
    progBar.appendChild(progFill);
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
    onclick: () => { State.student = null; localStorage.removeItem("practice.student"); render(); }
  }, "Zmień użytkownika");
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
  else answerArea = el("div", {}, "Nieobsługiwany typ pytania");

  card.appendChild(answerArea);
  root.appendChild(card);
  renderMath(card);

  // Nav arrows at bottom
  const nav = el("div", { class: "nav" });
  nav.appendChild(el("button", {
    class: "btn btn-secondary",
    onclick: () => { if (State.currentIdx > 0) { State.currentIdx--; State.save(); render(); } }
  }, "← Wstecz"));
  nav.appendChild(el("button", {
    class: "btn btn-secondary",
    onclick: () => {
      if (State.currentIdx < State.setData.questions.length - 1) { State.currentIdx++; State.save(); render(); }
      else if (State.isComplete()) { State.view = "summary"; render(); }
    }
  }, "Dalej →"));
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
  const input = el("input", {
    type: "text",
    inputmode: "text",
    autocomplete: "off",
    autocorrect: "off",
    spellcheck: "false",
    placeholder: q.answer.includes("/") ? "np. 1/2" : "wpisz odpowiedź",
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
  wrap.appendChild(input);
  wrap.appendChild(el("div", { class: "hint-help" },
    "Ułamek wpisz jako np. 1/2. Liczba mieszana: 1 1/2."
  ));

  const submitBar = el("div", { class: "feedback" });
  const checkBtn = el("button", {
    class: "btn btn-primary",
    disabled: locked,
    onclick: () => {
      const raw = input.value.trim();
      if (!raw) return;
      const correct = answersEqual(raw, q.answer);
      if (!correct) {
        input.classList.remove("correct");
        input.classList.add("wrong");
        setTimeout(() => input.classList.remove("wrong"), 600);
      }
      handleAnswer(q, qState, correct, raw, q.answer);
    }
  }, "Sprawdź");
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); checkBtn.click(); }
  });
  submitBar.appendChild(checkBtn);
  wrap.appendChild(submitBar);
  // Auto-focus when not locked
  if (!locked) setTimeout(() => input.focus(), 100);
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
  banner.appendChild(el("div", { class: "feedback-title" }, "✓ Świetnie!"));
  card.appendChild(banner);
  fireConfettiSmall();
}

function showRetry(q) {
  const card = $(".card-wrap");
  const banner = el("div", { class: "feedback fail feedback-result" });
  banner.appendChild(el("div", { class: "feedback-title" }, "Spróbuj jeszcze raz"));
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
  banner.appendChild(el("div", { class: "feedback-title" }, "✗ Nieprawidłowo"));
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
  }, "Dalej →");
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
  let title, emoji;
  if (pct === 100) { title = "Mistrz!"; emoji = "🏆"; }
  else if (pct >= 80) { title = "Świetna robota!"; emoji = "⭐"; }
  else if (pct >= 60) { title = "Dobrze!"; emoji = "👍"; }
  else { title = "Trzeba poćwiczyć"; emoji = "💪"; }

  wrap.appendChild(el("div", { style: "font-size: 80px;" }, emoji));
  wrap.appendChild(el("h1", {}, title));
  wrap.appendChild(el("div", { class: "score-big" }, `${passed}/${total}`));
  wrap.appendChild(el("div", { class: "score-line" }, `${pct}% poprawnych (za pierwszym lub drugim razem)`));

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
  }, "Spróbuj ponownie");
  wrap.appendChild(retry);

  const back = el("button", {
    class: "btn btn-secondary",
    onclick: () => { State.view = "welcome"; render(); }
  }, "Wróć do menu");
  wrap.appendChild(back);

  root.appendChild(wrap);
  renderMath(wrap);
  if (pct >= 80) fireConfettiBig();
}

/* ============================================================
 * Boot
 * ============================================================ */
window.addEventListener("DOMContentLoaded", () => {
  // Wait for KaTeX
  const tryRender = () => {
    if (window.renderMathInElement) render();
    else setTimeout(tryRender, 50);
  };
  tryRender();
});
