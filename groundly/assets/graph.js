
document.title = DATA.subject + ' — knowledge graph';

const nodeById = {};
DATA.nodes.forEach(n => { nodeById[n.id] = n; });

let currentLevel = DATA.default_level;
const hiddenCommunities = new Set();

function degreeSize(d) { return 8 + Math.sqrt(Math.max(d, 0)) * 3; }
function communitySize(s) { return 10 + Math.sqrt(Math.max(s, 1)) * 4; }

function colorFor(node, level) {
  if (DATA.aggregated) return node.color;
  const cid = node.communities[String(level)];
  if (cid === null || cid === undefined) return DATA.no_community_color;
  const levelColors = DATA.colors[String(level)] || {};
  return levelColors[String(cid)] || DATA.no_community_color;
}

function nodeCommunity(node, level) {
  return DATA.aggregated ? node.community : node.communities[String(level)];
}

const container = document.getElementById('graph');
const noticeEl = document.getElementById('notice');
if (DATA.aggregated) {
  noticeEl.textContent = DATA.total_entities + ' entities exceed the visualizer\'s node '
    + 'cap — showing ' + DATA.nodes.length + ' communities instead. Click a community for its report.';
  noticeEl.style.display = 'block';
}

const nodesDS = new vis.DataSet(DATA.nodes.map(n => {
  const color = colorFor(n, currentLevel);
  return {
    id: n.id,
    label: n.label,
    title: n.label,
    color: { background: color, border: color, highlight: { background: '#ffffff', border: color } },
    size: DATA.aggregated ? communitySize(n.size) : degreeSize(n.degree),
    font: { size: 0 },
  };
}));

const edgesDS = new vis.DataSet(DATA.edges.map((e, i) => ({
  id: i,
  from: e.from,
  to: e.to,
  width: Math.min(1 + Math.log2(1 + (e.weight || 1)), 6),
  color: { color: 'rgba(255,255,255,0.15)', highlight: 'rgba(255,255,255,0.5)' },
})));

const network = new vis.Network(container, { nodes: nodesDS, edges: edgesDS }, {
  physics: {
    enabled: true,
    solver: 'forceAtlas2Based',
    forceAtlas2Based: {
      gravitationalConstant: -60,
      centralGravity: 0.005,
      springLength: 120,
      springConstant: 0.08,
      damping: 0.4,
      avoidOverlap: 0.8,
    },
    stabilization: { iterations: 200, fit: true },
  },
  interaction: { hover: true, tooltipDelay: 100, hideEdgesOnDrag: true, navigationButtons: false },
  nodes: { shape: 'dot', borderWidth: 1.5 },
  edges: { smooth: { type: 'continuous', roundness: 0.2 } },
});

// Disabling physics once stabilization settles is what keeps a large graph draggable
// instead of endlessly writhing under its own force simulation.
network.once('stabilizationIterationsDone', () => {
  network.setOptions({ physics: { enabled: false } });
});

const infoContent = document.getElementById('info-content');

function clearChildren(el) { while (el.firstChild) el.removeChild(el.firstChild); }

function fieldEl(label, value) {
  const div = document.createElement('div');
  div.className = 'field';
  const b = document.createElement('b');
  b.textContent = label + ': ';
  div.appendChild(b);
  div.appendChild(document.createTextNode(value));
  return div;
}

function titleEl(text) {
  const div = document.createElement('div');
  div.className = 'field title';
  div.textContent = text;
  return div;
}

function descriptionEl(text) {
  const div = document.createElement('div');
  div.className = 'description';
  div.textContent = text;
  return div;
}

function subheadEl(text) {
  const div = document.createElement('div');
  div.className = 'subhead';
  div.textContent = text;
  return div;
}

function showEmpty(message) {
  clearChildren(infoContent);
  const span = document.createElement('span');
  span.className = 'empty';
  span.textContent = message;
  infoContent.appendChild(span);
}

function legendItem(cid, level) {
  return (DATA.legend[String(level)] || []).find(c => c.community === cid) || null;
}

function showEntity(nodeId) {
  const n = nodeById[nodeId];
  if (!n) return;
  clearChildren(infoContent);
  infoContent.appendChild(titleEl(n.label));
  infoContent.appendChild(fieldEl('Type', n.type || 'unknown'));
  const cid = n.communities[String(currentLevel)];
  const community = cid === null || cid === undefined ? null : legendItem(cid, currentLevel);
  infoContent.appendChild(fieldEl('Community', community ? community.title : 'none'));
  infoContent.appendChild(fieldEl('Degree', String(n.degree)));
  if (n.description) infoContent.appendChild(descriptionEl(n.description));

  if (n.citations && n.citations.length) {
    infoContent.appendChild(subheadEl('Citations (' + n.citations.length + ')'));
    n.citations.forEach(c => {
      const div = document.createElement('div');
      div.className = 'citation';
      div.textContent = c;
      infoContent.appendChild(div);
    });
  }

  const neighborIds = network.getConnectedNodes(nodeId);
  if (neighborIds.length) {
    infoContent.appendChild(subheadEl('Neighbours (' + neighborIds.length + ')'));
    const list = document.createElement('div');
    list.id = 'neighbors-list';
    neighborIds.forEach(nid => {
      const nb = nodeById[nid];
      const link = document.createElement('div');
      link.className = 'neighbor-link';
      link.textContent = nb ? nb.label : nid;
      link.style.borderLeftColor = nb ? colorFor(nb, currentLevel) : DATA.no_community_color;
      link.addEventListener('click', () => focusNode(nid));
      list.appendChild(link);
    });
    infoContent.appendChild(list);
  }
}

function showCommunity(cid, level) {
  const item = legendItem(cid, level);
  if (!item) return;
  clearChildren(infoContent);
  infoContent.appendChild(titleEl(item.title));
  infoContent.appendChild(fieldEl('Size', item.size + ' entities'));
  if (item.rank !== null && item.rank !== undefined) infoContent.appendChild(fieldEl('Rank', String(item.rank)));
  if (item.rating_explanation) infoContent.appendChild(descriptionEl(item.rating_explanation));
  if (item.summary) infoContent.appendChild(descriptionEl(item.summary));
  if (item.findings && item.findings.length) {
    infoContent.appendChild(subheadEl('Findings (' + item.findings.length + ')'));
    item.findings.forEach(f => {
      const div = document.createElement('div');
      div.className = 'finding';
      const strong = document.createElement('div');
      strong.className = 'finding-title';
      strong.textContent = f.explanation;
      div.appendChild(strong);
      const p = document.createElement('div');
      p.textContent = f.summary;
      div.appendChild(p);
      infoContent.appendChild(div);
    });
  }
}

function handleNodeClick(nodeId) {
  if (DATA.aggregated) {
    const n = nodeById[nodeId];
    if (n) showCommunity(n.community, currentLevel);
  } else {
    showEntity(nodeId);
  }
}

function focusNode(nodeId) {
  network.focus(nodeId, { scale: 1.4, animation: true });
  network.selectNodes([nodeId]);
  handleNodeClick(nodeId);
}

network.on('click', params => {
  if (params.nodes.length > 0) handleNodeClick(params.nodes[0]);
  else showEmpty('Click a node to inspect it');
});

const searchInput = document.getElementById('search');
const searchResults = document.getElementById('search-results');
searchInput.addEventListener('input', () => {
  const q = searchInput.value.toLowerCase().trim();
  clearChildren(searchResults);
  if (!q) { searchResults.style.display = 'none'; return; }
  const matches = DATA.nodes.filter(n => n.label.toLowerCase().includes(q)).slice(0, 20);
  if (!matches.length) { searchResults.style.display = 'none'; return; }
  searchResults.style.display = 'block';
  matches.forEach(n => {
    const el = document.createElement('div');
    el.className = 'search-item';
    el.textContent = n.label;
    el.style.borderLeftColor = colorFor(n, currentLevel);
    el.addEventListener('click', () => {
      focusNode(n.id);
      searchResults.style.display = 'none';
      searchInput.value = '';
    });
    searchResults.appendChild(el);
  });
});
document.addEventListener('click', e => {
  if (!searchResults.contains(e.target) && e.target !== searchInput) searchResults.style.display = 'none';
});

const legendEl = document.getElementById('legend');
const selectAllCb = document.getElementById('select-all-cb');

function updateSelectAllState() {
  const total = (DATA.legend[String(currentLevel)] || []).length;
  const hidden = hiddenCommunities.size;
  selectAllCb.checked = hidden === 0;
  selectAllCb.indeterminate = hidden > 0 && hidden < total;
}

function setCommunityHidden(cid, hide, item) {
  if (hide) { hiddenCommunities.add(cid); item.classList.add('dimmed'); }
  else { hiddenCommunities.delete(cid); item.classList.remove('dimmed'); }
  const updates = DATA.nodes
    .filter(n => nodeCommunity(n, currentLevel) === cid)
    .map(n => ({ id: n.id, hidden: hide }));
  nodesDS.update(updates);
  updateSelectAllState();
}

function renderLegend(level) {
  clearChildren(legendEl);
  hiddenCommunities.clear();
  (DATA.legend[String(level)] || []).forEach(c => {
    const item = document.createElement('div');
    item.className = 'legend-item';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.className = 'legend-cb';
    cb.checked = true;
    cb.addEventListener('change', e => {
      e.stopPropagation();
      setCommunityHidden(c.community, !cb.checked, item);
    });
    const dot = document.createElement('div');
    dot.className = 'legend-dot';
    dot.style.background = c.color;
    const label = document.createElement('span');
    label.className = 'legend-label';
    label.textContent = c.title;
    const count = document.createElement('span');
    count.className = 'legend-count';
    count.textContent = String(c.size);
    item.appendChild(cb);
    item.appendChild(dot);
    item.appendChild(label);
    item.appendChild(count);
    item.addEventListener('click', e => {
      if (e.target === cb) return;
      showCommunity(c.community, level);
    });
    legendEl.appendChild(item);
  });
  updateSelectAllState();
  document.getElementById('stats').textContent =
    DATA.nodes.length + ' nodes \u00b7 ' + DATA.edges.length + ' edges \u00b7 '
    + (DATA.legend[String(level)] || []).length + ' communities';
}

selectAllCb.addEventListener('change', () => {
  const hide = !selectAllCb.checked;
  document.querySelectorAll('.legend-item').forEach((item, i) => {
    const c = (DATA.legend[String(currentLevel)] || [])[i];
    if (!c) return;
    hide ? item.classList.add('dimmed') : item.classList.remove('dimmed');
    const cb = item.querySelector('.legend-cb');
    if (cb) cb.checked = !hide;
  });
  hiddenCommunities.clear();
  if (hide) (DATA.legend[String(currentLevel)] || []).forEach(c => hiddenCommunities.add(c.community));
  nodesDS.update(DATA.nodes.map(n => ({ id: n.id, hidden: hide })));
  updateSelectAllState();
});

renderLegend(currentLevel);

// Level toggle recolours existing entity nodes and rebuilds the legend for the new
// level — cheap, since every node already carries its community for every level. Not
// offered in aggregated mode: there the nodes ARE the communities of one fixed level,
// so "switching level" would mean swapping the whole graph, not just its colours.
const levelWrap = document.getElementById('level-wrap');
if (!DATA.aggregated && DATA.levels.length > 1) {
  levelWrap.style.display = 'flex';
  DATA.levels.forEach(lvl => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = 'Level ' + lvl;
    btn.className = 'level-btn' + (lvl === currentLevel ? ' active' : '');
    btn.addEventListener('click', () => setLevel(lvl));
    levelWrap.appendChild(btn);
  });
}

function setLevel(lvl) {
  currentLevel = lvl;
  levelWrap.querySelectorAll('.level-btn').forEach((btn, i) => {
    btn.classList.toggle('active', DATA.levels[i] === lvl);
  });
  nodesDS.update(DATA.nodes.map(n => {
    const color = colorFor(n, lvl);
    return { id: n.id, color: { background: color, border: color, highlight: { background: '#ffffff', border: color } } };
  }));
  renderLegend(lvl);
  showEmpty('Click a node to inspect it');
}
