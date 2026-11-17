"use strict";

// Every string from the API (titles, summaries, filenames, chunk text) may come from an imported
// bundle, so it only ever reaches the DOM through textContent.
function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}

const app = document.getElementById("app");

async function getJSON(url) {
  const res = await fetch(url);
  let body = null;
  try { body = await res.json(); } catch (_) { /* non-JSON failure body */ }
  if (!res.ok || (body && body.error)) {
    throw new Error((body && (body.detail || body.error)) || "request failed (" + res.status + ")");
  }
  return body;
}

function api(subject, rest) {
  return "/api/subjects/" + encodeURIComponent(subject) + rest;
}

function pageURL(subject, rest) {
  return "/s/" + encodeURIComponent(subject) + (rest || "");
}

function button(text, cls, onClick) {
  const b = el("button", text, cls);
  b.type = "button";
  if (onClick) b.addEventListener("click", onClick);
  return b;
}

// How far a topic has got, in words. A parent counts its mastered subtopics; a leaf its last
// ten graded answers. An answer awaiting review counts nowhere until it is reviewed.
function progressLine(p) {
  if (!p || p.state === "not_started") return "Not started";
  if (p.state === "mastered") return "Mastered";
  if (p.subtopics != null) return p.subtopics_mastered + " of " + p.subtopics + " subtopics mastered";
  if (p.answered >= 10) return p.recent_correct + " of last 10 correct";
  return p.answered + " answered";
}

function askForQuestions(title, subject) {
  const note = el("p", "", "notice");
  // The title may come from an imported bundle, so it reaches the host quoted, as data.
  note.append("Say to your agent: ", el("code", "Write questions on \"" + title + "\" for " + subject + "."));
  return note;
}

function backLink() {
  const back = el("a", "All subjects", "back");
  back.href = "/";
  return back;
}

function brand() {
  const h1 = el("h1", "Groundly", "brand");
  const logo = document.createElement("img");
  logo.src = "/static/claude-code.svg";
  logo.alt = "";
  h1.prepend(logo);
  return h1;
}

function showError(message) {
  app.replaceChildren(brand(), el("p", message, "notice"));
}

async function showSubjects() {
  app.replaceChildren(brand(), el("p", "Pick a subject to see its topic map.", "muted"));
  let subjects;
  try { subjects = await getJSON("/api/subjects"); } catch (e) { return showError(e.message); }
  if (!subjects.length) {
    const none = el("p", "No subjects yet. Index one with ", "notice");
    none.append(el("code", "groundly index"), ".");
    app.append(none);
    return;
  }
  const list = el("ul", undefined, "subjects");
  for (const s of subjects) {
    const item = el("li");
    const link = el("a", s.title || s.subject);
    link.href = "/s/" + encodeURIComponent(s.subject);
    const status = s.error ? s.error : s.has_tree ? "topic map ready" : "no map yet";
    item.append(link, el("span", " — " + (s.title ? s.subject + " · " : "") + status, "muted"));
    list.append(item);
  }
  app.append(list);
}

function sourceItem(subject, src) {
  const item = el("li");
  const filename = String(src.filename);
  const label = filename + (src.page != null ? " · p." + Number(src.page) : "");
  if (src.pdf === true) {
    const link = el("a", label);
    link.href = "/s/" + encodeURIComponent(subject) + "/materials/" + Number(src.material_id) +
      (src.page != null ? "#page=" + Number(src.page) : "");
    link.target = "_blank";
    link.rel = "noopener";
    item.append(link);
  } else {
    const text = el("pre", src.text);
    text.tabIndex = 0; // it scrolls, and a keyboard can only scroll what takes focus
    text.setAttribute("aria-label", "Text from " + label);
    item.append(el("div", label), text);
  }
  return item;
}

function testChooser(subject, topicId, questions) {
  const box = el("div", undefined, "start");
  const label = el("label", "Questions ");
  const select = el("select");
  // A test never asks for more than the bank holds; the smallest length always starts one.
  let previous = 0;
  for (const n of [5, 10, 20]) {
    const option = el("option", String(n));
    option.value = String(n);
    option.disabled = previous >= questions;
    previous = n;
    select.append(option);
  }
  select.value = questions > 5 ? "10" : "5";
  label.append(select);
  const start = el("a", "Start test", "primary");
  const update = () => {
    start.href = pageURL(subject, "/test?" + (topicId != null ? "topic=" + topicId + "&" : "") + "n=" + select.value);
  };
  select.addEventListener("change", update);
  update();
  box.append(label, start);
  return box;
}

function renderPanel(panel, subject, node, onClose) {
  const close = el("button", "Close", "close");
  close.type = "button";
  close.addEventListener("click", onClose);
  const head = el("div", undefined, "panel-head");
  head.append(el("h2", node.title), close);
  panel.replaceChildren(head, el("p", progressLine(node.progress), "muted"));
  const count = node.progress ? node.progress.questions : 0;
  if (count) {
    panel.append(el("p", count + (count === 1 ? " question" : " questions")), testChooser(subject, node.id, count));
  } else {
    panel.append(askForQuestions(node.title, subject));
  }
  if (node.summary) panel.append(el("p", node.summary, "summary"));
  panel.append(el("h3", "Sources"));
  if (!node.sources.length) {
    panel.append(el("p", "sources removed — ask your agent to update the map", "muted"));
    return;
  }
  const list = el("ul");
  const linked = new Set();
  for (const src of node.sources) {
    if (src.pdf === true) {
      // chunks of one page are separate sources, but one link opens the page
      const label = src.filename + "\u0000" + src.page;
      if (linked.has(label)) continue;
      linked.add(label);
    }
    list.append(sourceItem(subject, src));
  }
  panel.append(list);
}

async function showTree(subject) {
  document.title = subject + " · Groundly";
  let tree, title;
  try { ({ tree, title } = await getJSON("/api/subjects/" + encodeURIComponent(subject) + "/tree")); }
  catch (e) {
    showError(e.message);
    app.prepend(backLink());
    return;
  }

  document.title = (title || subject) + " · Groundly";
  app.replaceChildren(backLink(), el("h1", title || subject));
  // A bundle chooses its own title, so the page always names the subject it really is.
  if (title) app.append(el("p", subject, "muted"));
  if (tree.some((t) => t.progress && t.progress.questions)) {
    const whole = el("a", "Test the whole course", "primary");
    whole.href = pageURL(subject, "/test?n=10");
    app.append(whole);
  }

  if (!tree.length) {
    const note = el("div", undefined, "notice");
    const say = el("p", "No map yet. In Claude Code, say: ");
    say.append(el("code", "map my " + subject + " course."));
    const skill = el("p", "This needs the Groundly skill: run ", "muted");
    skill.append(el("code", "groundly skill install"), " first.");
    note.append(say, skill);
    app.append(note);
    return;
  }

  const panel = el("aside", undefined, "panel");
  const placeholder = el("p", "Select a topic to see its summary and sources.", "muted");
  panel.setAttribute("aria-label", "Topic details");
  panel.append(placeholder);

  let selected = null;
  function closePanel() {
    const node = selected;
    selected = null;
    node.setAttribute("aria-pressed", "false");
    panel.classList.remove("open");
    panel.replaceChildren(placeholder);
    node.focus();
  }
  panel.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && selected) closePanel();
  });

  function node(n, cls) {
    const b = el("button", n.title, "node " + cls + (n.sources.length ? "" : " nosrc"));
    if (!n.sources.length) b.append(el("span", "sources removed", "flag"));
    if (n.progress && n.progress.state !== "not_started") {
      b.append(el("span", progressLine(n.progress), "flag state-" + n.progress.state));
    }
    b.type = "button";
    b.setAttribute("aria-pressed", "false");
    b.addEventListener("click", () => {
      if (selected) selected.setAttribute("aria-pressed", "false");
      selected = b;
      b.setAttribute("aria-pressed", "true");
      renderPanel(panel, subject, n, closePanel);
      panel.classList.add("open");
      panel.scrollTop = 0;
      b.scrollIntoView({ block: "nearest" });
      // The panel follows the whole tree in the DOM: focus takes keyboard and screen-reader users
      // straight to it, and Escape or Close brings them back to this node.
      const heading = panel.querySelector("h2");
      heading.tabIndex = -1;
      heading.focus({ preventScroll: true });
    });
    return b;
  }

  // role="list": Safari drops list semantics from a list styled with list-style: none.
  const spine = el("ul", undefined, "tree");
  spine.setAttribute("role", "list");
  for (const topic of tree) {
    const row = el("li", undefined, "topic");
    row.append(node(topic, "big"));
    if (topic.subtopics.length) {
      const chips = el("ul", undefined, "chips");
      chips.setAttribute("role", "list");
      chips.setAttribute("aria-label", "Subtopics of " + topic.title);
      for (const sub of topic.subtopics) {
        const item = el("li");
        item.append(node(sub, "chip"));
        chips.append(item);
      }
      row.append(chips);
    }
    spine.append(row);
  }
  const layout = el("div", undefined, "layout");
  layout.append(spine, panel);
  app.append(layout);
  showHistory(subject);
}

async function showHistory(subject) {
  let attempts;
  try { attempts = await getJSON(api(subject, "/attempts")); } catch (e) { return; }
  if (!attempts.length) return;
  const list = el("ul", undefined, "history");
  for (const a of attempts) {
    const item = el("li");
    const link = el("a", a.submitted_at + " · " + (a.scope_title || "Whole course"));
    link.href = pageURL(subject, "/attempts/" + Number(a.id));
    item.append(link, el("span", " — " + a.score.correct + " / " + a.score.graded + " correct" +
      (a.provisional ? ", awaiting review" : ""), "muted"));
    list.append(item);
  }
  app.append(el("h3", "Past tests"), list);
}

// The scope's title for a test or result header: the whole course, or a topic in the tree.
async function scopeInfo(subject, topicId) {
  const { tree, title } = await getJSON(api(subject, "/tree"));
  let scope = "Whole course";
  for (const t of tree) {
    for (const n of [t, ...t.subtopics]) if (n.id === topicId) scope = n.title;
  }
  return { course: title || subject, scope };
}

function shuffled(items) {
  const out = items.slice();
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

function answered(q, answer) {
  return q.kind === "open" ? typeof answer === "string" && answer.trim() !== "" : Array.isArray(answer) && answer.length > 0;
}

// sessionStorage can be missing or full (private windows, quotas): the test still works, it
// just does not survive a refresh.
function loadTest(key) {
  try { return JSON.parse(sessionStorage.getItem(key)); } catch (_) { return null; }
}
function saveTest(key, test) {
  try { sessionStorage.setItem(key, JSON.stringify(test)); } catch (_) { /* not kept */ }
}
function dropTest(key) {
  try { sessionStorage.removeItem(key); } catch (_) { /* nothing kept */ }
}

async function showTest(subject) {
  const params = new URLSearchParams(location.search);
  const topicId = params.has("topic") ? Number(params.get("topic")) : null;
  const n = Number(params.get("n") || 10);
  const key = "groundly-test:" + subject + ":" + (topicId == null ? "all" : topicId);
  const back = el("a", "Back to the map", "back");
  back.href = pageURL(subject);

  let info;
  try { info = await scopeInfo(subject, topicId); } catch (e) { showError(e.message); app.prepend(back); return; }
  document.title = "Test · " + info.course + " · Groundly";

  let test = loadTest(key);
  if (!test || test.n !== n || !Array.isArray(test.questions)) {
    let drawn;
    try {
      drawn = await getJSON(api(subject, "/test?" + (topicId != null ? "topic=" + topicId + "&" : "") + "n=" + n));
    } catch (e) { showError(e.message); app.prepend(back); return; }
    test = {
      n,
      token: crypto.randomUUID(),
      questions: drawn.questions.map((q) => ({ ...q, options: shuffled(q.options) })),
      answers: {},
      current: 0,
    };
    if (test.questions.length) saveTest(key, test);
  }
  if (!test.questions.length) {
    app.replaceChildren(back, el("h1", info.course), el("p", info.scope, "muted"),
      askForQuestions(info.scope === "Whole course" ? info.course : info.scope, subject));
    return;
  }

  const total = test.questions.length;
  let error = null;
  let confirming = false;
  let sending = false;

  // Set once Submit is refused for a removed question: that test can never be submitted,
  // so nothing may save it again.
  let dead = false;
  function store() { if (!dead) saveTest(key, test); }

  function go(i) { test.current = i; confirming = false; error = null; store(); render(); }

  function questionView(q) {
    const fieldset = el("fieldset", undefined, "question");
    fieldset.append(el("legend", q.stem));
    if (q.code) fieldset.append(el("pre", q.code, "code"));
    const answer = test.answers[q.id];
    if (q.kind === "open") {
      const area = el("textarea");
      area.maxLength = 4000;
      area.rows = 6;
      area.setAttribute("aria-label", "Your answer");
      area.value = typeof answer === "string" ? answer : "";
      area.addEventListener("input", () => { test.answers[q.id] = area.value; store(); renderDots(); });
      fieldset.append(area);
      return fieldset;
    }
    const chosen = new Set(Array.isArray(answer) ? answer : []);
    if (q.kind === "multiple") fieldset.append(el("p", "Choose every answer that applies.", "muted"));
    for (const o of q.options) {
      const label = el("label", undefined, "option");
      const input = el("input");
      input.type = q.kind === "single" ? "radio" : "checkbox";
      input.name = "q" + q.id;
      input.checked = chosen.has(o.id);
      input.addEventListener("change", () => {
        if (q.kind === "single") test.answers[q.id] = [o.id];
        else {
          const set = new Set(test.answers[q.id] || []);
          if (input.checked) set.add(o.id); else set.delete(o.id);
          test.answers[q.id] = [...set];
        }
        store();
        renderDots();
      });
      label.append(input, el("span", o.text));
      fieldset.append(label);
    }
    return fieldset;
  }

  const dots = el("nav", undefined, "dots");
  dots.setAttribute("aria-label", "Questions");
  function renderDots() {
    dots.replaceChildren();
    test.questions.forEach((q, i) => {
      const done = answered(q, test.answers[q.id]);
      const b = button(String(i + 1), "dot" + (done ? " answered" : ""), () => go(i));
      b.setAttribute("aria-label", "Question " + (i + 1) + (done ? ", answered" : ""));
      if (i === test.current) b.setAttribute("aria-current", "step");
      dots.append(b);
    });
  }

  async function submit() {
    const unanswered = test.questions.filter((q) => !answered(q, test.answers[q.id])).length;
    if (unanswered && !confirming) { confirming = unanswered; render(); return; }
    sending = true;
    error = null;
    render();
    const answers = test.questions.map((q) => q.kind === "open"
      ? { question_id: q.id, text: test.answers[q.id] || "" }
      : { question_id: q.id, selected: test.answers[q.id] || [] });
    let res, body = null;
    try {
      res = await fetch(api(subject, "/attempts"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: test.token, topic_id: topicId, answers }),
      });
      try { body = await res.json(); } catch (_) { /* non-JSON failure body */ }
    } catch (_) {
      res = null;
    }
    sending = false;
    if (res && res.status === 201 && body) {
      dropTest(key);
      location.assign(pageURL(subject, "/attempts/" + Number(body.id)));
      return;
    }
    confirming = false;
    // A removed question can never be submitted again: drop the stored test so a refresh or
    // "Start test" draws a new one, and keep this one on screen with the server's reason.
    if (body && body.error === "unknown_question") {
      dead = true;
      dropTest(key);
    }
    error = (body && (body.detail || body.error)) || "the test could not be submitted — try Submit again";
    render();
  }

  function render() {
    const q = test.questions[test.current];
    const last = test.current === total - 1;
    renderDots();
    const nav = el("div", undefined, "actions");
    const prev = button("Back", "", () => go(test.current - 1));
    prev.disabled = test.current === 0;
    nav.append(prev);
    if (!last) nav.append(button("Next", "primary", () => go(test.current + 1)));
    else {
      const send = button(sending ? "Submitting…" : "Submit", "primary", submit);
      send.disabled = sending;
      nav.append(send);
    }
    const parts = [back, el("h1", info.course), el("p", info.scope, "muted"),
      el("p", "Question " + (test.current + 1) + " of " + total, "counter"), dots];
    if (error) parts.push(el("p", error, "notice bad"));
    parts.push(questionView(q), nav);
    if (confirming) {
      const warn = el("div", undefined, "notice wait");
      warn.setAttribute("role", "alertdialog");
      warn.append(el("p", confirming + (confirming === 1 ? " question is" : " questions are") +
        " unanswered and will count as wrong"));
      const anyway = button("Submit anyway", "primary", submit);
      anyway.disabled = sending;
      warn.append(anyway, button("Go back", "", () => { confirming = false; render(); }));
      parts.push(warn);
    }
    app.replaceChildren(...parts);
  }
  render();
}

const STATE_WORDS = { correct: "Correct", wrong: "Wrong", awaiting: "Awaiting review", unreviewable: "Not reviewed" };

function answerView(subject, a, i) {
  const card = el("article", undefined, "answer state-" + a.state);
  const head = el("div", undefined, "answer-head");
  head.append(el("strong", (i + 1) + ". " + STATE_WORDS[a.state]), el("span", "Question #" + a.question_id, "muted"));
  card.append(head);
  if (a.removed) {
    card.append(el("p", "Question removed", "muted"));
    if (a.state === "unreviewable") card.append(el("p", "Cannot be reviewed — the question was removed.", "muted"));
    return card;
  }
  card.append(el("p", a.stem, "stem"));
  if (a.code) card.append(el("pre", a.code, "code"));
  if (a.kind === "open") {
    card.append(el("h4", "Your answer"), el("p", a.text && a.text.trim() ? a.text : "No answer", "said"));
    if (a.state === "awaiting") {
      card.append(el("h4", "Reference answer"), el("p", a.reference_answer, "said"));
      const wait = el("p", "Awaiting review — say ", "muted");
      wait.append(el("code", "grade my test"), " to your agent.");
      card.append(wait);
    } else {
      card.append(el("h4", "Reference answer"), el("p", a.reference_answer, "said"));
      if (a.comment) card.append(el("h4", "Review"), el("p", a.comment, "said"));
    }
  } else {
    const yours = a.options.filter((o) => o.selected).map((o) => o.text);
    const right = a.options.filter((o) => o.correct).map((o) => o.text);
    card.append(el("h4", "Your answer"), el("p", yours.length ? yours.join("; ") : "No answer", "said"),
      el("h4", "Correct answer"), el("p", right.join("; "), "said"));
  }
  if (a.sources.length) {
    const list = el("ul", undefined, "sources");
    for (const src of a.sources) list.append(sourceItem(subject, src));
    card.append(el("h4", "Sources"), list);
  }
  return card;
}

async function showResult(subject, attemptId) {
  const back = el("a", "Back to the map", "back");
  back.href = pageURL(subject);
  let result, info;
  try {
    result = await getJSON(api(subject, "/attempts/" + attemptId));
    info = await scopeInfo(subject, result.scope_topic_id);
  } catch (e) { showError(e.message); app.prepend(back); return; }
  document.title = "Result · " + info.course + " · Groundly";
  const s = result.score;
  const parts = [back, el("h1", info.course), el("p", info.scope + " · " + result.submitted_at, "muted"),
    el("p", s.correct + " / " + s.graded + " correct", "score")];
  if (result.provisional) parts.push(el("p", "provisional, " + s.awaiting + " awaiting review", "muted"));
  result.answers.forEach((a, i) => parts.push(answerView(subject, a, i)));
  if (result.topics.length) {
    const list = el("ul", undefined, "history");
    for (const t of result.topics) {
      const item = el("li");
      item.append(el("span", t.title), el("span", " — " + progressLine(t), "muted"));
      list.append(item);
    }
    parts.push(el("h3", "Topics"), list);
  }
  app.replaceChildren(...parts);
}

const parts = location.pathname.split("/");
if (parts[1] === "s" && parts[2] && parts[3] === "test") showTest(decodeURIComponent(parts[2]));
else if (parts[1] === "s" && parts[2] && parts[3] === "attempts" && parts[4]) {
  showResult(decodeURIComponent(parts[2]), Number(parts[4]));
} else if (parts[1] === "s" && parts[2]) showTree(decodeURIComponent(parts[2]));
else showSubjects();
