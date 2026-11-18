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

async function getJSON(url, init) {
  const res = await fetch(url, init);
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

function testQuery(topicId, n) {
  return "/test?" + (topicId != null ? "topic=" + topicId + "&" : "") + "n=" + n;
}

function button(text, cls, onClick) {
  const b = el("button", text, cls);
  b.type = "button";
  if (onClick) b.addEventListener("click", onClick);
  return b;
}

function postJSON(url, body) {
  return getJSON(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

// A page run is the student's own Claude Code, started headless for one chore (decision 46).
// One strip serves the whole page: each view places it, the poll updates it in place, and the
// run's steps, summary and error reach it as text only.
const POLL_MS = 2000;
const KIND_WORDS = { write: "Writing questions", grade: "Grading written answers" };
// `watched` is the run this tab started or re-attached to; `message` is the last outcome shown.
// `target`, from the server: a topic with fewer questions than this is worth a Write click.
const host = { available: false, target: 0, run: null, watched: null, starting: false, message: null, onEnd: null, timer: null };

const strip = el("section", undefined, "run");
strip.setAttribute("aria-label", "Agent run");
const stripTitle = el("h2");
const stripBar = el("progress"); // indeterminate while the run works; not a live region
stripBar.setAttribute("aria-label", "Working");
const stripSteps = el("ol", undefined, "steps");
const stripStop = button("Stop", "", stopRun);
const stripOutcome = el("p", "", "outcome");
stripOutcome.setAttribute("role", "status"); // announces the outcome once, politely
strip.append(stripTitle, stripBar, stripSteps, stripStop, stripOutcome);

// The run state, or null when the page server cannot be reached.
async function runInfo() {
  try { return await getJSON("/api/runs"); } catch (_) { return null; }
}

function busy() {
  return host.starting || Boolean(host.run && host.run.status === "running");
}

function watchedRun() {
  return host.run && host.run.id === host.watched ? host.run : null;
}

function renderStrip() {
  const r = watchedRun();
  const working = r ? r.status === "running" : host.starting;
  strip.hidden = !r && !host.starting && !host.message;
  stripTitle.textContent = r ? KIND_WORDS[r.kind] + " · " + r.subject : host.starting ? "Starting your agent" : "Agent run";
  stripBar.hidden = !working;
  stripStop.hidden = !(r && working);
  stripSteps.replaceChildren(...(r ? r.steps.slice(-6).map((s) => el("li", s)) : []));
  for (const b of document.querySelectorAll("button.run-action")) b.disabled = busy();
}

function showOutcome(text) {
  host.message = text;
  stripOutcome.textContent = text;
  renderStrip();
}

function outcomeText(r) {
  if (r.status === "done") return r.summary || "Done.";
  if (r.status === "stopped") return "Stopped. What was already stored stays.";
  return "Failed: " + (r.error ? r.error.detail : "the run ended without a result");
}

function poll(delay = POLL_MS) {
  clearTimeout(host.timer);
  host.timer = setTimeout(async () => {
    const info = await runInfo();
    if (!info) {
      host.run = null;
      return showOutcome("Lost the page server — reload the page when it is back.");
    }
    host.run = info.run;
    const r = watchedRun();
    if (r && r.status === "running") { renderStrip(); return poll(); }
    renderStrip();
    // Re-render first, so the outcome lands in a status region that is already on the page.
    if (host.onEnd) await host.onEnd();
    showOutcome(r ? outcomeText(r) : "Lost track of the run — the page server restarted.");
  }, delay);
}

// Places the strip for a view: `info` is runInfo()'s answer, `onEnd` re-renders the view when
// the run ends. A run that is working when the view loads is re-attached to.
function runStrip(info, onEnd) {
  host.available = Boolean(info && info.available);
  if (info) host.target = info.target;
  host.onEnd = onEnd;
  if (info) host.run = info.run;
  if (info && info.run && info.run.status === "running" && info.run.id !== host.watched) {
    host.watched = info.run.id;
    poll();
  }
  renderStrip();
  return strip;
}

async function startRun(subject, body) {
  host.starting = true;
  host.watched = null;
  host.message = null;
  stripOutcome.textContent = "";
  renderStrip();
  try {
    host.run = await postJSON(api(subject, "/runs"), body);
    host.watched = host.run.id;
    poll();
  } catch (e) {
    host.message = "Could not start: " + e.message;
    stripOutcome.textContent = host.message;
    // Refused because another tab started a run? Attach to it, so its strip and Stop show here.
    const info = await runInfo();
    if (info) runStrip(info, host.onEnd);
  }
  host.starting = false;
  renderStrip();
}

async function stopRun() {
  stripStop.disabled = true;
  try { await postJSON("/api/runs/stop", {}); } catch (e) { showOutcome("Could not stop: " + e.message); }
  stripStop.disabled = false;
  poll(0);
}

function runButton(text, subject, body) {
  const b = button(text, "run-action", () => startRun(subject, body));
  b.disabled = busy();
  return b;
}

// Write is offered while any topic in scope (a leaf, or a big topic's subtopics) is thin.
function writeButton(subject, node) {
  const leaves = node.subtopics.length ? node.subtopics : [node];
  if (!host.available || !leaves.some((l) => (l.progress ? l.progress.questions : 0) < host.target)) return null;
  const label = node.subtopics.length ? "Write questions for its subtopics" : "Write questions";
  return runButton(label, subject, { kind: "write", topic_id: node.id });
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
    link.href = pageURL(s.subject);
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
    link.href = pageURL(subject, "/materials/" + Number(src.material_id) +
      (src.page != null ? "#page=" + Number(src.page) : ""));
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
    start.href = pageURL(subject, testQuery(topicId, select.value));
  };
  select.addEventListener("change", update);
  update();
  box.append(label, start);
  return box;
}

function renderPanel(panel, subject, node, onClose) {
  const close = button("Close", "close", onClose);
  const head = el("div", undefined, "panel-head");
  head.append(el("h2", node.title), close);
  panel.replaceChildren(head, el("p", progressLine(node.progress), "muted"));
  const count = node.progress ? node.progress.questions : 0;
  if (count) {
    panel.append(el("p", count + (count === 1 ? " question" : " questions")), testChooser(subject, node.id, count));
  }
  const write = writeButton(subject, node);
  if (write) panel.append(write);
  else if (!count) panel.append(askForQuestions(node.title, subject));
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

// `reopen` is the id of a topic whose panel opens again, after a run's end re-renders the map.
async function showTree(subject, reopen) {
  document.title = subject + " · Groundly";
  let tree, title;
  try { ({ tree, title } = await getJSON(api(subject, "/tree"))); }
  catch (e) {
    showError(e.message);
    app.prepend(backLink());
    return;
  }
  let openId = null;
  const runBox = runStrip(await runInfo(), () => showTree(subject, openId));

  document.title = (title || subject) + " · Groundly";
  app.replaceChildren(backLink(), el("h1", title || subject));
  // A bundle chooses its own title, so the page always names the subject it really is.
  if (title) app.append(el("p", subject, "muted"));
  app.append(runBox);
  if (tree.some((t) => t.progress && t.progress.questions)) {
    const whole = el("a", "Test the whole course", "primary");
    whole.href = pageURL(subject, "/test?n=10");
    app.append(whole);
  }

  if (!tree.length) {
    const note = el("div", undefined, "notice");
    const say = el("p", "No map yet. In Claude Code, say: ");
    say.append(el("code", "map my " + subject + " course."));
    const skill = el("p", "This needs the Groundly skills: run ", "muted");
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
    openId = null;
    node.setAttribute("aria-pressed", "false");
    panel.classList.remove("open");
    panel.replaceChildren(placeholder);
    node.focus();
  }
  panel.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && selected) closePanel();
  });

  let reopened = null;
  function node(n, cls) {
    const b = el("button", n.title, "node " + cls + (n.sources.length ? "" : " nosrc"));
    if (n.id === reopen) reopened = b;
    if (!n.sources.length) b.append(el("span", "sources removed", "flag"));
    if (n.progress && n.progress.state !== "not_started") {
      b.append(el("span", progressLine(n.progress), "flag state-" + n.progress.state));
    }
    b.type = "button";
    b.setAttribute("aria-pressed", "false");
    b.addEventListener("click", () => {
      if (selected) selected.setAttribute("aria-pressed", "false");
      selected = b;
      openId = n.id;
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
  if (reopened) reopened.click();
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
  // A scope a later re-map removed is still a topic test, never the whole course.
  let scope = topicId == null ? "Whole course" : "Removed topic";
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
      drawn = await getJSON(api(subject, testQuery(topicId, n)));
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

// With `canGrade`, the result offers Grade now, so an awaiting card needs no phrase to say.
function answerView(subject, a, i, canGrade) {
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
    card.append(el("h4", "Your answer"), el("p", a.text && a.text.trim() ? a.text : "No answer", "said"),
      el("h4", "Reference answer"), el("p", a.reference_answer, "said"));
    if (a.state === "awaiting") {
      if (!canGrade) {
        const wait = el("p", "Awaiting review — say ", "muted");
        wait.append(el("code", "grade my test"), " to your agent.");
        card.append(wait);
      }
    } else if (a.comment) {
      card.append(el("h4", "Review"), el("p", a.comment, "said"));
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
  const runBox = runStrip(await runInfo(), () => showResult(subject, attemptId));
  document.title = "Result · " + info.course + " · Groundly";
  const s = result.score;
  const parts = [back, el("h1", info.course), el("p", info.scope + " · " + result.submitted_at, "muted"),
    runBox, el("p", s.correct + " / " + s.graded + " correct", "score")];
  const canGrade = result.provisional && host.available;
  if (result.provisional) {
    const row = el("div", undefined, "start");
    row.append(el("p", "provisional, " + s.awaiting + " awaiting review", "muted"));
    if (canGrade) row.append(runButton("Grade now", subject, { kind: "grade" }));
    parts.push(row);
  }
  result.answers.forEach((a, i) => parts.push(answerView(subject, a, i, canGrade)));
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
