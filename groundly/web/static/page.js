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
    throw new Error((body && body.error) || "request failed (" + res.status + ")");
  }
  return body;
}

function backLink() {
  const back = el("a", "All subjects", "back");
  back.href = "/";
  return back;
}

function showError(message) {
  app.replaceChildren(el("h1", "Groundly"), el("p", message, "notice"));
}

async function showSubjects() {
  app.replaceChildren(el("h1", "Groundly"), el("p", "Pick a subject to see its topic map.", "muted"));
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
    const link = el("a", s.subject);
    link.href = "/s/" + encodeURIComponent(s.subject);
    item.append(link, el("span", " — " + (s.error ? s.error : s.has_tree ? "topic map ready" : "no map yet"), "muted"));
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

function renderPanel(panel, subject, node, onClose) {
  const close = el("button", "Close", "close");
  close.type = "button";
  close.addEventListener("click", onClose);
  const head = el("div", undefined, "panel-head");
  head.append(el("h2", node.title), close);
  panel.replaceChildren(head, el("p", "Not started", "muted"));
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
  let tree;
  try { tree = (await getJSON("/api/subjects/" + encodeURIComponent(subject) + "/tree")).tree; }
  catch (e) {
    showError(e.message);
    app.prepend(backLink());
    return;
  }

  app.replaceChildren(backLink(), el("h1", subject));

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
}

const parts = location.pathname.split("/");
if (parts[1] === "s" && parts[2]) showTree(decodeURIComponent(parts[2]));
else showSubjects();
