// @ts-check
// Settings → Limits: one tree (all > sets > projects) and one form for the selected node.
// Pure render helpers over the Settings draft state (cfgmodal.js owns state, staging and Save).
import { escHtml } from '../util/format.js';
import { act } from '../core/actions.js';

export const CAP_FIELDS = [
  ['max_tasks', '1'],
  ['max_cpu_cores', '1'],
  ['max_ram_gib', '0.5'],
  ['max_gpu_vram_gib', '0.5'],
  ['max_gpu_hours_day', '0.5'],
];
const USAGE_KEY = { max_tasks: 'running', max_cpu_cores: 'cpu', max_ram_gib: 'ram', max_gpu_vram_gib: 'vram', max_gpu_hours_day: 'gpu_hours_today' };

const t = (k, p) => (globalThis.i18n ? globalThis.i18n.t(k, p) : k);

/** Draft value of a node field: staged edit, else stored own value, else ''. */
export function nodeValue(C, id, field) {
  const d = C.draftNodes[id];
  if (d && field in d) return d[field];
  const n = C.tree && C.tree.nodes[id];
  const v = n && n.own ? n.own[field] : undefined;
  return v ? v : '';
}

/** Parent id of a node, honouring staged moves. */
export function nodeParent(C, id) {
  const n = C.tree.nodes[id];
  if (n.kind === 'project' && id in C.moves) return C.moves[id] ? 'set:' + C.moves[id] : 'all';
  return n.parent;
}

/** All node ids as a (parent → children) map after staged moves / new sets. */
function _children(C) {
  const kids = {};
  const nodes = C.tree.nodes;
  for (const id of Object.keys(nodes)) {
    if (C.delSets.includes(id)) continue;
    const p = nodeParent(C, id);
    if (p) (kids[p] = kids[p] || []).push(id);
  }
  for (const path of C.newSets) {
    const id = 'set:' + path;
    if (nodes[id]) continue;
    const parent = path.includes('/') ? 'set:' + path.split('/').slice(0, -1).join('/') : 'all';
    (kids[parent] = kids[parent] || []).push(id);
  }
  for (const k of Object.keys(kids)) {
    kids[k].sort((a, b) => {
      const ka = a.startsWith('set:') ? 0 : 1;
      const kb = b.startsWith('set:') ? 0 : 1;
      return ka - kb || _label(C, a).localeCompare(_label(C, b));
    });
  }
  return kids;
}

function _label(C, id) {
  const n = C.tree.nodes[id];
  if (n) return n.label;
  return id.replace(/^set:/, '').split('/').pop() || id;
}

function _node(C, id) {
  return C.tree.nodes[id] || { id, kind: 'set', path: id.slice(4), label: _label(C, id), own: {}, effective: {}, usage: {}, fields: ['max_tasks', 'max_cpu_cores', 'max_ram_gib', 'max_gpu_vram_gib', 'max_gpu_hours_day', 'min_tasks'], children: [], parent: null };
}

/** Effective cap after staged edits: tightest non-empty value on the node or its ancestors. */
export function effectiveCap(C, id, field) {
  let best = null;
  let cur = id;
  const seen = new Set();
  while (cur && !seen.has(cur)) {
    seen.add(cur);
    const v = Number(nodeValue(C, cur, field) || 0);
    if (v > 0 && (best === null || v < best.value)) best = { value: v, from: cur };
    const n = C.tree.nodes[cur];
    cur = n ? nodeParent(C, cur) : (cur.includes('/') ? 'set:' + cur.slice(4).split('/').slice(0, -1).join('/') : 'all');
  }
  return best;
}

function _bar(C, id) {
  const n = _node(C, id);
  const cap = effectiveCap(C, id, 'max_tasks');
  const run = Number((n.usage || {}).running || 0);
  if (!cap) return run ? `<span class="lim-use">${run}</span>` : '';
  const pct = Math.min(100, Math.round((run / cap.value) * 100));
  const full = run >= cap.value ? ' lim-full' : '';
  return `<span class="lim-use${full}" title="${escHtml(t('limits.runningOfCap'))}">${run}/${cap.value}</span>`
    + `<span class="lim-bar${full}"><i style="width:${pct}%"></i></span>`;
}

/** Left pane: the tree. */
export function treeHtml(C) {
  const kids = _children(C);
  const rows = [];
  const walk = (id, depth) => {
    const n = _node(C, id);
    const sel = C.sel === id ? ' sel' : '';
    const dirty = C.draftNodes[id] || (n.kind === 'project' && id in C.moves) ? ' dirty' : '';
    const icon = n.kind === 'root' ? '◎' : n.kind === 'set' ? '▸' : '•';
    rows.push(`<li class="lim-node lim-${n.kind}${sel}${dirty}" style="--depth:${depth}" data-click="${act([['tqLimSelect', id]])}">`
      + `<span class="lim-ico">${icon}</span><span class="lim-name">${escHtml(n.kind === 'root' ? t('limits.all') : n.label)}</span>${_bar(C, id)}</li>`);
    for (const c of kids[id] || []) walk(c, depth + 1);
  };
  walk('all', 0);
  return `<ul class="lim-tree">${rows.join('')}</ul>`
    + `<button type="button" class="cfg-btn lim-addset" data-click="${act([['tqLimAddSet', C.sel && !C.sel.startsWith('project:') ? C.sel : 'all']])}">${escHtml(t('limits.addSet'))}</button>`;
}

function _setPaths(C) {
  const paths = Object.values(C.tree.nodes).filter(n => n.kind === 'set').map(n => n.path).filter(p => !C.delSets.includes('set:' + p));
  for (const p of C.newSets) if (!paths.includes(p)) paths.push(p);
  return paths.sort();
}

/** Right pane: the form for the selected node. */
export function formHtml(C) {
  const id = C.sel || 'all';
  const n = _node(C, id);
  const fields = n.kind === 'root' ? ['max_tasks', 'max_cpu_cores', 'max_ram_gib', 'max_gpu_vram_gib'] : n.fields || [];
  const kindLabel = t('limits.kind.' + n.kind);
  let html = `<div class="lim-head"><b>${escHtml(n.kind === 'root' ? t('limits.all') : n.path)}</b> <span class="lim-kind">${escHtml(kindLabel)}</span></div>`;
  html += `<p class="lim-note">${escHtml(t('limits.note.' + n.kind))}</p>`;
  html += '<div class="lim-fields">';
  for (const [f, step] of CAP_FIELDS) {
    if (!fields.includes(f)) continue;
    const val = nodeValue(C, id, f);
    const eff = effectiveCap(C, id, f);
    const inherited = eff && eff.from !== id ? t('limits.capFrom', { value: eff.value, node: eff.from === 'all' ? t('limits.all') : _label(C, eff.from) }) : t('limits.noCap');
    const u = (n.usage || {})[USAGE_KEY[f]];
    const use = u != null ? `<span class="lim-now">${escHtml(t('limits.now', { value: Math.round(Number(u) * 100) / 100 }))}</span>` : '';
    html += `<label class="lim-row"><span class="lim-lbl">${escHtml(t('limits.field.' + f))}</span>`
      + `<input type="number" min="0" step="${step}" data-field="${f}" value="${escHtml(String(val))}" placeholder="${escHtml(inherited)}"`
      + ` data-input="${act([['tqLimSet', id, f, { $: 'this' }]])}">${use}</label>`;
  }
  if (fields.includes('min_tasks')) {
    const val = nodeValue(C, id, 'min_tasks');
    const ph = n.effective && n.effective.min_tasks && n.effective.min_tasks.from !== id ? String(n.effective.min_tasks.value) : '';
    html += `<label class="lim-row"><span class="lim-lbl">${escHtml(t(n.kind === 'set' ? 'limits.field.min_tasks_set' : 'limits.field.min_tasks'))}</span>`
      + `<input type="number" min="0" step="1" data-field="min_tasks" value="${escHtml(String(val))}" placeholder="${escHtml(ph)}"`
      + ` data-input="${act([['tqLimSet', id, 'min_tasks', { $: 'this' }]])}"></label>`;
  }
  html += '</div>';
  if (n.kind === 'project') {
    const cur = nodeParent(C, id);
    const curPath = cur === 'all' ? '' : cur.slice(4);
    const opts = [`<option value="">${escHtml(t('limits.noSet'))}</option>`]
      .concat(_setPaths(C).map(p => `<option value="${escHtml(p)}"${p === curPath ? ' selected' : ''}>${escHtml(p)}</option>`));
    html += `<label class="lim-row"><span class="lim-lbl">${escHtml(t('limits.parent'))}</span>`
      + `<select data-change="${act([['tqLimParent', id, { $: 'this' }]])}">${opts.join('')}</select></label>`;
  }
  if (n.kind === 'set') {
    const empty = !(_children(C)[id] || []).length;
    html += '<div class="lim-actions">'
      + `<button type="button" class="cfg-btn" data-click="${act([['tqLimAddSet', id]])}">${escHtml(t('limits.addChild'))}</button>`
      + (empty ? ` <button type="button" class="cfg-btn" data-click="${act([['tqLimDelSet', id]])}">${escHtml(t('limits.deleteSet'))}</button>` : '')
      + '</div>';
  }
  return html;
}

export function limitsTabHtml(C) {
  if (!C.tree) return `<p class="lim-note">${escHtml(t('limits.unavailable'))}</p>`;
  return `<div class="lim-wrap"><div class="lim-left">${treeHtml(C)}</div><div class="lim-right" id="lim-form">${formHtml(C)}</div></div>`
    + `<p class="lim-foot">${escHtml(t('limits.footer'))}</p>`;
}
