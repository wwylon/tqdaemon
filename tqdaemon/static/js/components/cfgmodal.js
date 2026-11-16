// @ts-check
// Settings dialog: tabs (Limits tree + schema-driven settings), every change staged, one Save.
// Uses app.js globals at call time (i18n, openAlert, openConfirm, refresh).
import { S } from '../core/state.js';
import { escHtml } from '../util/format.js';
import { act } from '../core/actions.js';
import { showToast } from './modals.js';
import { limitsTabHtml, formHtml, treeHtml } from './limittree.js';

const TABS = ['limits', 'scheduling', 'memory', 'stopping', 'notifications', 'server', 'retention', 'advanced'];

/** Settings state: server copies + staged edits. Exported for tests. */
export const C = {
  tab: 'limits',
  schema: /** @type {any} */ (null),
  tree: /** @type {any} */ (null),
  sel: 'all',
  filter: '',
  draftCfg: /** @type {Record<string, any>} */ ({}),
  draftNodes: /** @type {Record<string, Record<string, any>>} */ ({}),
  moves: /** @type {Record<string, string>} */ ({}),
  newSets: /** @type {string[]} */ ([]),
  delSets: /** @type {string[]} */ ([]),
};

const t = (k, p) => (globalThis.i18n ? globalThis.i18n.t(k, p) : k);
const $ = id => document.getElementById(id);

function _reset() {
  C.draftCfg = {};
  C.draftNodes = {};
  C.moves = {};
  C.newSets = [];
  C.delSets = [];
  C.filter = '';
}

/** Number of staged changes (shown in the footer). */
export function cfgDirtyCount() {
  const nodeFields = Object.values(C.draftNodes).reduce((n, d) => n + Object.keys(d).length, 0);
  return Object.keys(C.draftCfg).length + nodeFields + Object.keys(C.moves).length + C.newSets.length + C.delSets.length;
}

async function _getJson(url) {
  try {
    const r = await fetch(url);
    return r.ok ? await r.json() : null;
  } catch (_) {
    return null;
  }
}

/** Open Settings; ``node`` (e.g. "project:med_xray") selects that node on the Limits tab. */
export async function openCfg(node) {
  _reset();
  const [schema, tree] = await Promise.all([_getJson('/api/config/schema'), _getJson('/api/limits/tree')]);
  C.schema = schema;
  C.tree = tree && tree.nodes ? tree : null;
  if (typeof node === 'string' && node) {
    C.tab = 'limits';
    C.sel = C.tree && C.tree.nodes[node] ? node : 'all';
  } else if (!C.tree || !C.tree.nodes[C.sel]) {
    C.sel = 'all';
  }
  _render();
  $('cfg-modal').classList.add('open');
}

/** Shortcut used by the project toolbar / set chips. */
export function openCfgAt(kind, name) {
  return openCfg(kind === 'set' ? 'set:' + name : kind === 'project' ? 'project:' + name : 'all');
}

/** Project toolbar "Limits…": Settings → Limits with the selected project. */
export function tqOpenProjectLimits() {
  const p = S.selectedProject;
  return openCfg(p && p !== 'all' ? 'project:' + p : 'all');
}

/** Project toolbar "Archive…": preview, then archive finished tasks older than N days (YAML moved to queue_dir/archive/). */
export async function tqArchiveProject() {
  const p = S.selectedProject;
  if (!p || p === 'all') return;
  const tr = (k, a) => (globalThis.i18n ? globalThis.i18n.t(k, a) : k);
  const om = globalThis.openModal;
  if (typeof om !== 'function') return;
  const body = document.createElement('div');
  body.innerHTML = `<p>${escHtml(tr('archive.body'))}</p>`
    + `<label class="lim-row"><span class="lim-lbl">${escHtml(tr('archive.olderThan'))}</span><input id="arch-days" type="number" min="0" step="1" value="14"></label>`
    + `<label class="lim-row"><span class="lim-lbl">${escHtml(tr('archive.statuses'))}</span><span>`
    + ['completed', 'failed', 'interrupted', 'disabled'].map(s => `<label style="margin-right:8px"><input type="checkbox" class="arch-st" value="${s}"${s === 'completed' ? ' checked' : ''}> ${escHtml(s)}</label>`).join('')
    + `</span></label>`
    + `<label class="lim-row"><span class="lim-lbl">${escHtml(tr('archive.taskGlob'))}</span><input id="arch-glob" type="text" placeholder="localize_*" style="width:14em"></label>`
    + `<p id="arch-preview" class="lim-note"></p>`;
  const req = (dry) => ({
    projects: [p],
    older_than_days: Number(/** @type {HTMLInputElement} */ (body.querySelector('#arch-days')).value || 0),
    statuses: [...body.querySelectorAll('.arch-st:checked')].map(x => /** @type {HTMLInputElement} */ (x).value),
    dry_run: dry,
    task_glob: /** @type {HTMLInputElement} */ (body.querySelector('#arch-glob')).value.trim() || undefined,
    // A real run can outlast the HTTP timeout: the daemon answers {job} and we poll it.
    async: dry ? undefined : true,
  });
  const preview = async () => {
    const el = body.querySelector('#arch-preview');
    try {
      const r = await _postJson('/api/cli/archive', req(true));
      el.textContent = tr('archive.preview', { n: (r && r.archived) || 0, project: p })
        + (r && r.kept_as_dependency ? ' ' + tr('archive.kept', { n: r.kept_as_dependency }) : '');
    } catch (e) { el.textContent = String((e && e.message) || e); }
  };
  body.addEventListener('input', preview);
  om({
    title: tr('archive.title', { project: p }),
    body,
    actions: [
      { label: tr('btn.cancel') },
      { label: tr('archive.run'), primary: true, onClick: () => toastResult(_postJson('/api/cli/archive', req(false)).then(r => (r && r.job ? _pollJob(r.job) : r)), null, (r) => {
        const toast = typeof window !== 'undefined' && typeof window.showToast === 'function' ? window.showToast : showToast;
        toast(tr('archive.done', { n: (r && r.archived) || 0, project: p }), 'ok');
        const rfn = typeof window !== 'undefined' ? window.refresh : null;
        if (typeof rfn === 'function') rfn();
      }) },
    ],
  });
  preview();
}

/** Project toolbar "Rename…": preview (dry run), then rename the project id with its history. */
export async function tqRenameProject() {
  const p = S.selectedProject;
  if (!p || p === 'all') return;
  const tr = (k, a) => (globalThis.i18n ? globalThis.i18n.t(k, a) : k);
  const om = globalThis.openModal;
  if (typeof om !== 'function') return;
  const body = document.createElement('div');
  body.innerHTML = `<p>${escHtml(tr('rename.body'))}</p>`
    + `<label class="lim-row"><span class="lim-lbl">${escHtml(tr('rename.newName'))}</span><input id="ren-new" type="text" value="${escHtml(p)}" style="width:16em"></label>`
    + `<label class="lim-row"><span class="lim-lbl">${escHtml(tr('rename.alias'))}</span><input id="ren-alias" type="checkbox" checked></label>`
    + `<p id="ren-preview" class="lim-note"></p>`;
  const val = () => /** @type {HTMLInputElement} */ (body.querySelector('#ren-new')).value.trim();
  const req = (dry) => ({
    new: val(),
    dry_run: dry,
    alias: /** @type {HTMLInputElement} */ (body.querySelector('#ren-alias')).checked,
  });
  const url = '/api/project/rename/' + encodeURIComponent(p);
  const preview = async () => {
    const el = body.querySelector('#ren-preview');
    if (!val() || val() === p) { el.textContent = ''; return; }
    try {
      const r = await _postJson(url, req(true));
      const rows = r && r.rows ? Object.values(r.rows).reduce((a, b) => a + Number(b || 0), 0) : 0;
      el.textContent = tr('rename.preview', { n: rows, keys: ((r && r.config_keys) || []).length, project: p, name: val() })
        + ((r && r.stale_yaml_mentions && r.stale_yaml_mentions.length) ? ' ' + tr('rename.stale', { n: r.stale_yaml_mentions.length }) : '');
    } catch (e) { el.textContent = String((e && e.message) || e); }
  };
  body.addEventListener('input', preview);
  om({
    title: tr('rename.title', { project: p }),
    body,
    actions: [
      { label: tr('btn.cancel') },
      { label: tr('rename.run'), primary: true, onClick: () => toastResult(_postJson(url, req(false)), null, () => {
        const toast = typeof window !== 'undefined' && typeof window.showToast === 'function' ? window.showToast : showToast;
        toast(tr('rename.done', { project: p, name: val() }), 'ok');
        const sp = typeof window !== 'undefined' ? window.setProject : null;
        if (typeof sp === 'function') sp(val());
        const rfn = typeof window !== 'undefined' ? window.refresh : null;
        if (typeof rfn === 'function') rfn();
      }) },
    ],
  });
}

/** Legacy entry point (header set chips): select the set node. */
export function tqCfgFocusSet(path) {
  C.sel = path ? 'set:' + path : 'all';
  if ($('cfg-modal') && $('cfg-modal').classList.contains('open')) _render();
}

export function closeCfg(force) {
  if (force !== true && cfgDirtyCount() > 0 && typeof globalThis.openConfirm === 'function') {
    globalThis.openConfirm(t('settings.discardConfirm', { n: cfgDirtyCount() }), () => {
      _reset();
      $('cfg-modal').classList.remove('open');
    }, { title: t('settings.title'), confirmLabel: t('settings.discard') });
    return;
  }
  _reset();
  $('cfg-modal').classList.remove('open');
}

export function tqCfgDiscard() {
  _reset();
  _render();
}

export function tqCfgTab(name) {
  C.tab = TABS.includes(name) ? name : 'limits';
  _render();
}

function _renderFooter() {
  const n = cfgDirtyCount();
  const el = $('cfg-dirty');
  if (el) {
    el.textContent = n ? t('settings.unsaved', { n }) : '';
    el.classList.toggle('on', n > 0);
  }
  const d = $('cfg-discard');
  if (d) /** @type {HTMLButtonElement} */ (d).disabled = n === 0;
}

function _render() {
  const tabs = $('cfg-tabs');
  if (tabs) {
    tabs.innerHTML = TABS.map(k => `<button type="button" class="cfg-tab${C.tab === k ? ' active' : ''}" data-click="${act([['tqCfgTab', k]])}">${escHtml(t('settings.tab.' + k))}</button>`).join('');
  }
  const body = $('cfg-body');
  if (body) body.innerHTML = C.tab === 'limits' ? limitsTabHtml(C) : _settingsHtml(C.tab);
  _renderFooter();
}

/* ---------------- schema-driven settings tabs ---------------- */

function _serverValue(key) {
  const cfg = S.cfg || {};
  if (key in cfg) return cfg[key];
  const k = C.schema && C.schema.keys.find(x => x.key === key);
  return k ? k.default : undefined;
}

function _value(key) {
  return key in C.draftCfg ? C.draftCfg[key] : _serverValue(key);
}

function _inputHtml(k) {
  const v = _value(k.key);
  const on = act([['tqCfgSet', k.key, { $: 'this' }]]);
  if (k.type === 'bool') {
    return `<input type="checkbox" data-key="${escHtml(k.key)}"${Number(v) ? ' checked' : ''} data-change="${on}">`;
  }
  if (k.type === 'enum') {
    return `<select data-key="${escHtml(k.key)}" data-change="${on}">`
      + (k.enum || []).map(o => `<option value="${escHtml(o)}"${o === v ? ' selected' : ''}>${escHtml(o)}</option>`).join('')
      + '</select>';
  }
  if (k.type === 'int' || k.type === 'float') {
    const step = k.type === 'float' ? ' step="any"' : ' step="1"';
    return `<input type="number" data-key="${escHtml(k.key)}" min="${k.min}" max="${k.max}"${step} value="${escHtml(v == null ? '' : String(v))}" data-input="${on}">`;
  }
  const type = k.secret ? 'password' : 'text';
  const shown = k.secret ? '' : (v == null ? '' : String(v));
  const ph = k.secret && v ? ' placeholder="••••••"' : '';
  return `<input type="${type}" data-key="${escHtml(k.key)}" value="${escHtml(shown)}"${ph} data-input="${on}">`;
}

function _settingsHtml(tab) {
  if (!C.schema) return `<p class="lim-note">${escHtml(t('settings.schemaUnavailable'))}</p>`;
  let keys = C.schema.keys.filter(k => (tab === 'advanced' ? true : k.section === tab));
  let head = '';
  if (tab === 'advanced') {
    head = `<input type="search" class="cfg-filter" placeholder="${escHtml(t('settings.filter'))}" value="${escHtml(C.filter)}" data-input="${act([['tqCfgFilter', { $: 'this' }]])}">`;
    const f = C.filter.toLowerCase();
    if (f) keys = keys.filter(k => k.key.includes(f) || (k.help || '').toLowerCase().includes(f));
  }
  if (tab === 'memory' || tab === 'scheduling') head += `<p class="lim-note">${escHtml(t('settings.capsInLimits'))}</p>`;
  const rows = keys.map(k => {
    const changed = k.key in C.draftCfg ? ' dirty' : '';
    const meta = tab === 'advanced' ? `<span class="cfg-meta">${escHtml(k.section)} · ${escHtml(t('settings.default', { v: k.default == null ? '–' : String(k.default) }))}</span>` : '';
    return `<div class="cfg-srow${changed}"><label><span class="cfg-key">${escHtml(k.key)}</span>${meta}`
      + `<span class="cfg-help">${escHtml(k.help || '')}</span></label>${_inputHtml(k)}</div>`;
  });
  return head + `<div class="cfg-settings" id="cfg-settings-rows">${rows.join('') || escHtml(t('settings.none'))}</div>`;
}

export function tqCfgFilter(el) {
  C.filter = String((el && el.value) || '');
  const rows = $('cfg-settings-rows');
  if (rows && C.tab === 'advanced') {
    const tmp = _settingsHtml('advanced');
    const m = tmp.match(/<div class="cfg-settings" id="cfg-settings-rows">([\s\S]*)<\/div>$/);
    rows.innerHTML = m ? m[1] : '';
  }
}

/** Stage one setting. Reverting to the server value un-stages it. */
export function tqCfgSet(key, el) {
  const k = C.schema && C.schema.keys.find(x => x.key === key);
  if (!k || !el) return;
  let v;
  if (k.type === 'bool') v = el.checked ? 1 : 0;
  else if (k.type === 'int') v = el.value === '' ? '' : parseInt(el.value, 10);
  else if (k.type === 'float') v = el.value === '' ? '' : parseFloat(el.value);
  else v = String(el.value);
  if (k.secret && v === '') {
    delete C.draftCfg[key];
  } else if (v === '' || (typeof v === 'number' && Number.isNaN(v))) {
    delete C.draftCfg[key];
  } else if (String(v) === String(_serverValue(key))) {
    delete C.draftCfg[key];
  } else {
    C.draftCfg[key] = v;
  }
  _renderFooter();
}

/* ---------------- limits tree actions ---------------- */

function _rerenderLimits(keepForm) {
  const left = document.querySelector('.lim-left');
  if (left) left.innerHTML = treeHtml(C);
  if (!keepForm) {
    const form = $('lim-form');
    if (form) form.innerHTML = formHtml(C);
  }
  _renderFooter();
}

export function tqLimSelect(id) {
  C.sel = id;
  _rerenderLimits(false);
}

/** Stage a cap field on a node ('' clears it). */
export function tqLimSet(id, field, el) {
  const raw = el ? String(el.value).trim() : '';
  const n = C.tree && C.tree.nodes[id];
  const stored = n && n.own && n.own[field] ? String(n.own[field]) : '';
  const d = C.draftNodes[id] || (C.draftNodes[id] = {});
  if (raw === stored || (raw === '' && stored === '') || (raw !== '' && Number(raw) === Number(stored || NaN))) {
    delete d[field];
  } else {
    d[field] = raw === '' ? 0 : Number(raw);
  }
  if (!Object.keys(d).length) delete C.draftNodes[id];
  _rerenderLimits(true);
}

/** Stage moving a project to another set ('' = no set). */
export function tqLimParent(id, el) {
  const path = el ? String(el.value) : '';
  const n = C.tree.nodes[id];
  const orig = n.parent === 'all' ? '' : String(n.parent).slice(4);
  if (path === orig) delete C.moves[id];
  else C.moves[id] = path;
  _rerenderLimits(false);
}

export function tqLimAddSet(parentId) {
  const base = parentId && parentId.startsWith('set:') ? parentId.slice(4) + '/' : '';
  const ask = typeof globalThis.prompt === 'function' ? globalThis.prompt(t('limits.newSetPrompt', { parent: base || t('limits.all') }), '') : null;
  const name = (ask || '').trim().replace(/^\/+|\/+$/g, '');
  if (!name) return;
  if (!/^[\w.-]+(\/[\w.-]+)*$/.test(name)) {
    showToast(t('limits.badSetName'), 'err');
    return;
  }
  const path = base + name;
  const id = 'set:' + path;
  C.delSets = C.delSets.filter(x => x !== id);
  if (!(C.tree.nodes[id]) && !C.newSets.includes(path)) C.newSets.push(path);
  C.sel = id;
  _rerenderLimits(false);
}

export function tqLimDelSet(id) {
  const path = id.slice(4);
  if (C.newSets.includes(path)) C.newSets = C.newSets.filter(p => p !== path);
  else if (!C.delSets.includes(id)) C.delSets.push(id);
  delete C.draftNodes[id];
  C.sel = 'all';
  _rerenderLimits(false);
}

/* ---------------- save ---------------- */

export function _postJson(url, body) { return _sendJson('POST', url, body); }

/** Poll GET /api/jobs/<id> until the background job finishes; resolves to its result, throws on error. */
export async function _pollJob(id, delayMs = 1500) {
  for (let i = 0; i < 2400; i++) {
    const j = await _sendJson('GET', '/api/jobs/' + encodeURIComponent(id));
    if (j.state === 'done') return j.result || {};
    if (j.state === 'error') throw new Error(j.error || 'job failed');
    if (delayMs > 0) await new Promise(res => setTimeout(res, delayMs));
  }
  throw new Error('job still running: ' + id);
}

/**
 * The common "request -> success toast -> follow-up, or error toast" chain. Takes the request promise
 * (e.g. `_postJson(url)`); resolves to the response data, or `false` after showing the error.
 */
export function toastResult(request, okMsg, after) {
  const toast = (m, k) => (typeof window !== 'undefined' && typeof window.showToast === 'function' ? window.showToast : showToast)(m, k);
  return Promise.resolve(request)
    .then(data => { if (okMsg) toast(okMsg, 'ok'); if (after) after(data); return data; })
    .catch(err => { toast(String((err && err.message) || err), 'err'); return false; });
}

/** Apply every staged change, in dependency order. Stops at the first error and keeps the dialog open. */
export async function saveCfg() {
  try {
    for (const path of C.newSets) await _postJson('/api/project-sets/node', { action: 'add', path });
    for (const [id, path] of Object.entries(C.moves)) {
      await _postJson(`/api/project/${encodeURIComponent(id.slice(8))}/set`, { set: path || null });
    }
    for (const [id, fields] of Object.entries(C.draftNodes)) {
      const n = (C.tree && C.tree.nodes[id]) || { kind: id === 'all' ? 'root' : id.split(':')[0], path: id.split(':').slice(1).join(':') };
      await _postJson('/api/limits/node', { kind: n.kind, path: n.kind === 'root' ? '' : n.path, fields });
    }
    if (Object.keys(C.draftCfg).length) await _postJson('/api/config', { ...C.draftCfg });
    for (const id of C.delSets) await _postJson('/api/project-sets/node', { action: 'remove', path: id.slice(4) });
  } catch (e) {
    const alertFn = typeof globalThis.openAlert === 'function' ? globalThis.openAlert : null;
    if (alertFn) alertFn(String(/** @type {any} */ (e).message || e), t('settings.title'));
    return;
  }
  const n = cfgDirtyCount();
  closeCfg(true);
  if (n) showToast(t('settings.saved', { n }), 'ok');
  try {
    const rfn = typeof window !== 'undefined' ? window.refresh : null;
    if (typeof rfn === 'function') await rfn();
  } catch (_) {}
}

export async function _sendJson(method, url, body) {
  const r = await fetch(url, {
    method,
    headers: {'Content-Type': 'application/json'},
    body: body == null ? undefined : JSON.stringify(body),
  });
  let data = {};
  try { data = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error(data.error || (r.status + ' ' + url));
  if (data.ok === false) throw new Error(data.error || 'request failed');
  return data;
}
