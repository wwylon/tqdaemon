"""Behavior tests for components/cfgmodal.js + limittree.js (Settings dialog, limits tree, JSON helpers)."""

from __future__ import annotations

import json

from tests.test_js_app import run_js
from tests.test_js_smoke import EV

PRE = """
const { g, doc, S } = await setup({ app: true, routes: ROUTES });
await g.flush();
for (const id of ['cfg-modal', 'cfg-tabs', 'cfg-body', 'cfg-dirty', 'cfg-discard']) if (!doc.getElementById(id)) doc.add(id);
const r = {};
"""

_F = ["max_tasks", "max_cpu_cores", "max_ram_gib", "max_gpu_vram_gib", "max_gpu_hours_day", "min_tasks"]


def _n(nid, kind, path, parent, own, running, eff_from, eff_val, children):
    return {
        "id": nid,
        "kind": kind,
        "path": path,
        "label": path or "all",
        "parent": parent,
        "own": own,
        "fields": _F[:4] if kind == "root" else _F,
        "usage": {"running": running},
        "effective": {"max_tasks": {"value": eff_val, "from": eff_from}},
        "children": children,
    }


TREE = {
    "root": "all",
    "nodes": {
        "all": _n("all", "root", "", None, {"max_tasks": 6}, 3, "all", 6, ["set:bots", "project:solo"]),
        "set:bots": _n("set:bots", "set", "bots", "all", {"max_tasks": 2}, 2, "set:bots", 2, ["project:b1"]),
        "project:b1": _n("project:b1", "project", "b1", "set:bots", {}, 2, "set:bots", 2, []),
        "project:solo": _n("project:solo", "project", "solo", "all", {"max_tasks": 1}, 1, "project:solo", 1, []),
    },
}
SCHEMA = {
    "sections": ["scheduling", "memory", "advanced"],
    "keys": [
        {
            "key": "gpu_first",
            "section": "scheduling",
            "help": "GPU first",
            "type": "bool",
            "min": 0,
            "max": 1,
            "enum": None,
            "default": 1,
            "secret": False,
        },
        {
            "key": "ram_limit_pct",
            "section": "memory",
            "help": "Soft RAM",
            "type": "int",
            "min": 10,
            "max": 100,
            "enum": None,
            "default": 80,
            "secret": False,
        },
        {
            "key": "frozen_action",
            "section": "stopping",
            "help": "Frozen",
            "type": "enum",
            "min": None,
            "max": None,
            "enum": ["log", "restart", "stop"],
            "default": "log",
            "secret": False,
        },
    ],
}
ROUTES = json.dumps({"/api/limits/tree": TREE, "/api/config/schema": SCHEMA, "/api/": {"ok": True}})


def _js(body: str, routes: str = ROUTES) -> str:
    return EV + PRE.replace("ROUTES", routes) + body + "\nconsole.log('RESULT:' + JSON.stringify(r));\n"


def test_send_json_returns_data_and_raises_on_http_or_ok_false():
    out = run_js(
        _js(
            """
const seen = [];
let resp = { ok: true, status: 200, json: async () => ({ ok: true, n: 1 }) };
g.fetch = async (url, o) => { seen.push([url, o.method, o.headers['Content-Type'], o.body]); return resp; };
r.ok = await g._postJson('/x', { a: 1 });
r.seen = seen[0];
await g._sendJson('DELETE', '/y');
r.noBody = seen[1][3] === undefined && seen[1][1] === 'DELETE';
const fail = async () => { try { await g._sendJson('POST', '/z', {}); return 'none'; } catch (e) { return e.message; } };
resp = { ok: false, status: 500, json: async () => ({ error: 'boom' }) };
r.httpErr = await fail();
resp = { ok: false, status: 502, json: async () => { throw new Error('bad'); } };
r.httpNoBody = await fail();
resp = { ok: true, status: 200, json: async () => ({ ok: false, error: 'denied' }) };
r.okFalseMsg = await fail();
""",
            "{}",
        )
    )
    assert out["ok"] == {"ok": True, "n": 1}
    assert out["seen"] == ["/x", "POST", "application/json", '{"a":1}']
    assert out["noBody"] is True
    assert out["httpErr"] == "boom" and out["httpNoBody"] == "502 /z"
    assert out["okFalseMsg"] == "denied"


def test_open_renders_tree_with_effective_caps_and_selects_node():
    out = run_js(
        _js(
            """
await g.openCfg('project:b1');
const body = doc.getElementById('cfg-body').innerHTML;
r.open = doc.getElementById('cfg-modal').classList.has('open');
r.tabs = doc.getElementById('cfg-tabs').innerHTML.includes('settings.tab.memory');
r.tree = [body.includes('lim-node lim-root'), body.includes('lim-node lim-set'), body.includes('2/2')];
r.selected = body.includes('lim-node lim-project sel');
r.placeholder = /data-field="max_tasks" value="" placeholder="limits.capFrom"/.test(body);
r.parentSelect = body.includes('<option value="bots" selected>');
"""
        )
    )
    assert out["open"] and out["tabs"]
    assert out["tree"] == [True, True, True]
    assert out["selected"] and out["placeholder"] and out["parentSelect"]


def test_staged_edits_save_in_order_then_close():
    out = run_js(
        _js(
            """
g.prompt = () => 'gpu';
await g.openCfg('project:b1');
g.tqLimSet('project:b1', 'max_tasks', { value: '1' });
g.tqLimSet('all', 'max_tasks', { value: '6' });
g.tqLimAddSet('set:bots');
g.tqLimParent('project:solo', { value: 'bots/gpu' });
g.tqCfgTab('scheduling');
g.tqCfgSet('gpu_first', { checked: false });
r.dirty = doc.getElementById('cfg-dirty').textContent;
g.fetches.length = 0;
await g.saveCfg();
r.posts = g.fetches.filter(f => f.opts && f.opts.method === 'POST').map(f => [f.url, JSON.parse(f.opts.body)]);
r.closed = !doc.getElementById('cfg-modal').classList.has('open');
"""
        )
    )
    assert out["dirty"] == "settings.unsaved"
    assert out["posts"] == [
        ["/api/project-sets/node", {"action": "add", "path": "bots/gpu"}],
        ["/api/project/solo/set", {"set": "bots/gpu"}],
        ["/api/limits/node", {"kind": "project", "path": "b1", "fields": {"max_tasks": 1}}],
        ["/api/config", {"gpu_first": 0}],
    ]
    assert out["closed"] is True


def test_save_error_keeps_dialog_open_and_discard_clears():
    out = run_js(
        _js(
            """
await g.openCfg();
g.tqLimSet('set:bots', 'max_tasks', { value: '' });
const alerts = []; g.openAlert = (m, t) => alerts.push(m);
g.fetch = async () => ({ ok: false, status: 400, json: async () => ({ error: 'bad cap' }) });
await g.saveCfg();
r.err = [alerts[0], doc.getElementById('cfg-modal').classList.has('open')];
g.tqCfgDiscard();
r.after = doc.getElementById('cfg-dirty').textContent;
"""
        )
    )
    assert out["err"] == ["bad cap", True]
    assert out["after"] == ""


def test_settings_tabs_render_from_schema_and_advanced_filter():
    out = run_js(
        _js(
            """
await g.openCfg();
S.cfg = { ram_limit_pct: 75 };
g.tqCfgTab('memory');
const mem = doc.getElementById('cfg-body').innerHTML;
r.mem = [mem.includes('ram_limit_pct'), mem.includes('value="75"'), mem.includes('gpu_first')];
g.tqCfgTab('advanced');
const adv = doc.getElementById('cfg-body').innerHTML;
r.adv = ['gpu_first', 'ram_limit_pct', 'frozen_action'].map(k => adv.includes(k));
g.tqCfgSet('ram_limit_pct', { value: '75' });
r.clean = doc.getElementById('cfg-dirty').textContent;
"""
        )
    )
    assert out["mem"] == [True, True, False]
    assert out["adv"] == [True, True, True]
    assert out["clean"] == ""


def test_poll_job_resolves_result_and_throws_on_error():
    out = run_js(
        _js(
            """
const states = [{ ok: true, state: 'running' }, { ok: true, state: 'done', result: { archived: 3 } }];
g.fetch = async () => ({ ok: true, status: 200, json: async () => states.shift() });
r.done = await g._pollJob('j1', 0);
g.fetch = async () => ({ ok: true, status: 200, json: async () => ({ ok: true, state: 'error', error: 'boom' }) });
try { await g._pollJob('j2', 0); r.err = null; } catch (e) { r.err = e.message; }
"""
        )
    )
    assert out["done"] == {"archived": 3}
    assert out["err"] == "boom"


def test_rename_project_dialog_previews_then_posts_and_switches_project():
    out = run_js(
        _js(
            """
S.selectedProject = 'old';
let modal = null;
g.openModal = (m) => { modal = m; };
const posts = [];
g.fetch = async (url, opts) => { posts.push([url, JSON.parse(opts.body)]); return { ok: true, status: 200, json: async () => ({ ok: true, rows: { 'tasks.project': 3 }, config_keys: ['k'] }) }; };
let switched = null; g.window.setProject = (n) => { switched = n; };
await g.tqRenameProject();
r.title = modal.title;
const input = modal.body.querySelector('#ren-new');
input.value = 'new';
await modal.actions[1].onClick();
await g.flush();
r.posts = posts.map(([u, b]) => [u, b.new, b.dry_run, b.alias]);
r.switched = switched;
"""
        )
    )
    assert out["title"] == "rename.title"
    assert out["posts"][-1] == ["/api/project/rename/old", "new", False, True]
    assert out["switched"] == "new"


def test_lifecycle_banner_shows_shutdown_stage_and_drain():
    out = run_js(
        _js(
            """
const mod = await import(new URL('./tqdaemon/static/js/core/refresh.js', 'file:///' + process.cwd().split(String.fromCharCode(92)).join('/') + '/').href);
if (!doc.getElementById('lifecycle-banner')) doc.add('lifecycle-banner');
const b = doc.getElementById('lifecycle-banner');
mod._setLifecycle({ shutting_down: true, keep_runs: true, draining: false }, 4);
r.handover = [b.style.display, b.textContent];
mod._setLifecycle({ shutting_down: true, keep_runs: false }, 2);
r.stopping = b.textContent;
mod._setLifecycle({ shutting_down: false, draining: true }, 3);
r.draining = b.textContent;
mod._setLifecycle({ shutting_down: false, draining: false }, 3);
r.hidden = [b.style.display, b.textContent];
"""
        )
    )
    assert out["handover"] == ["", "lifecycle.shutdownHandover"]
    assert out["stopping"] == "lifecycle.shutdownStopping" and out["draining"] == "lifecycle.draining"
    assert out["hidden"] == ["none", ""]
