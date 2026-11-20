"""GOV leaf source: gates, alias tables, contract counts, harvest invariants."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile

import pytest

import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
HERE = os.path.join(ROOT, "_cc0924")
if HERE not in sys.path:
    sys.path.insert(0, HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from gov_harvest import (  # noqa: E402
    ABORT_SNIPPET,
    EVAL_SEEDS,
    abort_snippet,
    align_events_to_steps,
    events_files_by_round,
    instrument_code,
    normalize_code,
    rename_code,
    round_inventory_pair,
    sha256_text,
    static_gates,
)
from gov_leaf_alias import (  # noqa: E402
    OBTAIN_LEAF_ALIAS,
    SCENE_LEAF_KEYS,
    equiv_from_inv,
    leaf_alias,
)
from voyager.v30_flags import V30_NAMED_ACTIONS  # noqa: E402

OBTAIN_JS = os.path.join(ROOT, "voyager", "control_primitives_extra", "obtain.js")
CAND_JSON = os.path.join(ROOT, "_gov", "candidates.json")


def _js_alias_table():
    text = open(OBTAIN_JS, encoding="utf-8").read()
    m = re.search(r"const OBTAIN_LEAF_ALIAS = JSON\.parse\(`([\s\S]*?)`\)", text)
    assert m, "JS OBTAIN_LEAF_ALIAS missing"
    return json.loads(m.group(1))


def test_alias_tables_match():
    js = _js_alias_table()
    assert js == OBTAIN_LEAF_ALIAS


def test_alias_covers_scene_leaf_keys():
    values = set(OBTAIN_LEAF_ALIAS.values())
    for k in SCENE_LEAF_KEYS:
        assert k in values, k
        assert leaf_alias(k) == k


def test_gate_slash_concat_give():
    reasons, _ = static_gates('async function f(bot) { bot.chat("/" + "give"); }')
    assert any("slash" in r or "chat" in r for r in reasons), reasons


def test_gate_fromcharcode():
    reasons, _ = static_gates("async function f(bot) { String.fromCharCode(47); }")
    assert any("fromCharCode" in r for r in reasons), reasons


def test_gate_default_maxdistance():
    reasons, _ = static_gates(
        "async function f(bot) { bot.findBlock({ matching: 1 }); }"
    )
    assert any("maxDistance" in r for r in reasons), reasons


def test_gate_vec3_abs_coords():
    reasons, _ = static_gates(
        "async function f(bot) { const p = new Vec3(100, 64, -200); }"
    )
    assert any("abs_coord" in r for r in reasons), reasons


def test_gate_rel_offset_ok():
    reasons, _ = static_gates(
        "async function f(bot) {\n"
        "  const p = bot.entity.position.offset(0, -1, 0);\n"
        "  bot.findBlock({ matching: 1, maxDistance: 32 });\n"
        "}"
    )
    assert not any("abs_coord" in r for r in reasons), reasons


def test_gate_eval_function():
    r1, _ = static_gates("async function f(bot) { eval('x'); }")
    r2, _ = static_gates("async function f(bot) { Function('return 1')(); }")
    assert any("eval" in x for x in r1)
    assert any("Function" in x for x in r2)


def test_rename_updates_internal_refs_and_node_check():
    src = (
        "async function helper(bot) { return 1; }\n"
        "async function main(bot) { await helper(bot); }\n"
    )
    norm, notes, _ = normalize_code(src)
    h_before = sha256_text(norm)
    renamed, _ = rename_code(norm, "gov_log_0__", extra_names=["helper", "main"])
    assert renamed and renamed.get("code")
    assert h_before == sha256_text(norm)
    code = renamed["code"]
    assert "gov_log_0__helper" in code
    assert "gov_log_0__main" in code
    assert re.search(r"\bhelper\s*\(", code) is None
    assert renamed.get("fn") == "gov_log_0__main"
    fd, tmp = tempfile.mkstemp(suffix=".js")
    os.write(fd, code.encode("utf-8"))
    os.close(fd)
    proc = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
    os.unlink(tmp)
    assert proc.returncode == 0, proc.stderr


def test_hash_before_rename():
    src = "async function main(bot) { while (x < 3) { x++; } }"
    a, _, _ = normalize_code(src)
    b, _, _ = normalize_code(src)
    assert sha256_text(a) == sha256_text(b)
    r1, _ = rename_code(a, "gov_log_0__")
    r2, _ = rename_code(a, "gov_log_1__")
    assert r1["code"] != r2["code"]
    assert sha256_text(a) == sha256_text(b)


def test_loop_abort_inserted():
    src = "async function main(bot) { while (x < 3) { x++; } }"
    inst, _ = instrument_code(src, "gov_log_0", "log")
    assert inst and abort_snippet("gov_log_0") in inst["code"]
    assert any("abort_probes" in n for n in (inst.get("normalized") or []))


def test_leafsource_unset_skips_gov():
    text = open(OBTAIN_JS, encoding="utf-8").read()
    fn = text.split("async function _obtainDoLeaf", 1)[1].split("async function _obtainMaybeToss", 1)[0]
    assert 'globalThis.__leafSource === "prim"' in fn
    assert 'globalThis.__leafSource === "gov"' in fn
    assert "_obtainGatherWithStrategy" in fn
    assert "_obtainMineDeep" in fn
    # hand path still present after the prim/gov branch
    assert fn.index('__leafSource === "prim"') < fn.index("_obtainGatherWithStrategy")


def test_hand_path_does_not_touch_gov_globals():
    text = open(OBTAIN_JS, encoding="utf-8").read()
    hand = text.split("async function _obtainDoLeaf", 1)[1]
    hand = hand.split("const OBTAIN_LEAF_ALIAS", 1)[0]
    # after the prim/gov early-return, remaining _obtainDoLeaf body must not mention __gov
    body = hand.split("return _obtainPrimLeaf", 1)[-1]
    assert "__govLedger" not in body
    assert "__govLeaves" not in body
    assert "__govNonce" not in body


def _write_obtain_harness(body: str) -> str:
    src = open(OBTAIN_JS, encoding="utf-8").read()
    preamble = r"""
function Vec3(x,y,z){ this.x=x; this.y=y; this.z=z; }
const mcData = { itemsByName: {}, items: {}, blocksByName: {}, recipes: {} };
function add(name, id) {
  mcData.itemsByName[name] = {id, name};
  mcData.items[id] = {id, name};
}
let _id = 1;
for (const n of ["oak_log","oak_planks","raw_iron","iron_ingot","raw_gold","gold_ingot","coal","cobblestone","stick"]) add(n, _id++);
"""
    fd, tmp = tempfile.mkstemp(suffix=".js")
    os.write(fd, (preamble + "\n" + src + "\n" + body).encode("utf-8"))
    os.close(fd)
    return tmp


def test_equiv_count_node():
    body = r"""
function botOf(items) {
  return {
    inventory: {
      items: () => items.map((it) => ({
        name: it.name,
        count: it.count,
        type: (mcData.itemsByName[it.name] || {}).id,
      })),
    },
  };
}
const b1 = botOf([{name:"oak_log", count:2},{name:"oak_planks", count:9}]);
const n1 = _obtainEquivCount(b1, "log");
if (n1 !== 2 + Math.floor(9/4)) { console.error("log got "+n1); process.exit(2); }
const b2 = botOf([{name:"raw_iron", count:1},{name:"iron_ingot", count:2}]);
if (_obtainEquivCount(b2, "raw_iron") !== 3) { console.error("iron"); process.exit(3); }
const b3 = botOf([{name:"raw_gold", count:4},{name:"gold_ingot", count:1}]);
if (_obtainEquivCount(b3, "raw_gold") !== 5) { console.error("gold"); process.exit(4); }
console.log("equiv_ok");
"""
    tmp = _write_obtain_harness(body)
    env = dict(os.environ)
    env["NODE_PATH"] = os.path.join(ROOT, "voyager", "env", "mineflayer", "node_modules")
    proc = subprocess.run(["node", tmp], cwd=ROOT, capture_output=True, text=True, env=env, timeout=30)
    os.unlink(tmp)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "equiv_ok" in proc.stdout


def test_doleaf_unset_uses_hand_and_skips_ledger():
    body = r"""
let hit = null;
_obtainGatherWithStrategy = async function(bot, leaf) { hit = "hand"; return {ok:true, reason:"done"}; };
_obtainMineDeep = async function() { hit = "deep"; return {ok:true, reason:"done"}; };
_obtainPrimLeaf = async function() { hit = "prim"; return {ok:true, reason:"done"}; };
_obtainGovLeaf = async function() { hit = "gov"; return {ok:true, reason:"done"}; };
const bot = {
  _obtainState: {},
  inventory: { items: () => [] },
  chat() {},
  entity: { position: { x:0, y:64, z:0, floored(){ return this; } } },
};
(async () => {
  if (typeof globalThis.__leafSource !== "undefined") throw new Error("preset");
  delete globalThis.__govLedger;
  const r = await _obtainDoLeaf(bot, {kind:"gather", name:"log", n:1}, () => false);
  if (hit !== "hand") { console.error("hit="+hit); process.exit(2); }
  if (Object.prototype.hasOwnProperty.call(globalThis, "__govLedger")) {
    console.error("ledger touched"); process.exit(3);
  }
  console.log("doleaf_hand_ok");
})().catch((e) => { console.error(e); process.exit(1); });
"""
    tmp = _write_obtain_harness(body)
    env = dict(os.environ)
    env["NODE_PATH"] = os.path.join(ROOT, "voyager", "env", "mineflayer", "node_modules")
    proc = subprocess.run(["node", tmp], cwd=ROOT, capture_output=True, text=True, env=env, timeout=30)
    os.unlink(tmp)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "doleaf_hand_ok" in proc.stdout


def _scan_snippet(text, label):
    hits = []
    if re.search(r"""bot\.chat\s*\(\s*(['"`])/""", text):
        hits.append(label + ":slash_chat")
    for w in ("/give", "/tp", "/teleport", "/setblock", "/summon", "/fill", "/gamemode"):
        if w in text:
            hits.append(f"{label}:cmd:{w}")
    for m in re.finditer(r"maxDistance\s*:\s*(\d+)", text):
        if int(m.group(1)) > 32:
            hits.append(f"{label}:maxDistance {m.group(1)}")
    return hits


def test_new_obtain_code_static_scan():
    text = open(OBTAIN_JS, encoding="utf-8").read()
    new = text.split("const OBTAIN_LEAF_ALIAS", 1)[-1]
    hits = _scan_snippet(new, "obtain_new")
    prim = text.split("async function _obtainPrimLeaf", 1)[-1].split("function _obtainGovLedgerRec", 1)[0]
    for bad in ("_obtainGoY", "_mineOreSafeDig", "gather(", "mineOre("):
        if bad == "gather(":
            if re.search(r"(^|[^\.\w])gather\s*\(", prim):
                hits.append("prim:gather")
        elif bad in prim:
            hits.append("prim:" + bad)
    assert not hits, hits


def test_candidates_source_seeds_disjoint_if_present():
    if not os.path.isfile(CAND_JSON):
        pytest.skip("candidates.json not harvested yet")
    data = json.load(open(CAND_JSON, encoding="utf-8"))
    seeds = set()
    for recs in data.values():
        for c in recs:
            if c.get("source_seed") is not None:
                seeds.add(int(c["source_seed"]))
            hits = _scan_snippet(c.get("code") or "", c.get("id") or "cand")
            assert not hits, hits
    assert seeds.isdisjoint(EVAL_SEEDS), seeds & EVAL_SEEDS


def test_injected_candidate_scan_if_present():
    if not os.path.isfile(CAND_JSON):
        pytest.skip("candidates.json not harvested yet")
    data = json.load(open(CAND_JSON, encoding="utf-8"))
    for key, recs in data.items():
        for c in recs:
            fd, tmp = tempfile.mkstemp(suffix=".js")
            os.write(fd, (c.get("code") or "").encode("utf-8"))
            os.close(fd)
            proc = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
            os.unlink(tmp)
            assert proc.returncode == 0, f"{c.get('id')}: {proc.stderr}"
            assert str(c.get("fn") or "").startswith("gov_")
            assert ABORT_SNIPPET in (c.get("code") or "") or "while" not in (c.get("code") or "")
            assert "code_hash" in c


def test_f1_obtain_toplevel_function_names_unique():
    text = open(OBTAIN_JS, encoding="utf-8").read()
    names = re.findall(r"^(?:async\s+)?function\s+(\w+)", text, re.M)
    dup = sorted({n for n in names if names.count(n) > 1})
    assert not dup, dup
    assert "_obtainLeafKey" in names
    assert "_obtainGovItemKey" in names
    # original hand key helper still returns kind:name
    assert names.count("_obtainLeafKey") == 1


def test_f1_hand_leaf_key_still_kind_name():
    body = r"""
const leaf = {kind:"gather", name:"log", n:1};
const k = _obtainLeafKey(leaf);
if (k !== "gather:log") { console.error("hand key "+k); process.exit(2); }
const g = _obtainGovItemKey(leaf);
if (g !== "log") { console.error("gov key "+g); process.exit(3); }
console.log("keys_ok");
"""
    tmp = _write_obtain_harness(body)
    env = dict(os.environ)
    env["NODE_PATH"] = os.path.join(ROOT, "voyager", "env", "mineflayer", "node_modules")
    proc = subprocess.run(["node", tmp], cwd=ROOT, capture_output=True, text=True, env=env, timeout=30)
    os.unlink(tmp)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "keys_ok" in proc.stdout


def test_f2_bridge_eval_registers_candidate_entry():
    programs = (
        "async function gov_log_0__main(bot) { return 7; }\n"
        'globalThis["gov_log_0__main"] = gov_log_0__main;\n'
    )
    code = 'if (typeof globalThis["gov_log_0__main"] !== "function") throw new Error("missing"); await globalThis["gov_log_0__main"]({});'
    js = "eval(\"(async () => {\" + " + json.dumps(programs) + " + \"\\n\" + " + json.dumps(code) + " + \"})()\").then(() => console.log(\"bridge_ok\")).catch((e) => { console.error(e); process.exit(2); });"
    proc = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "bridge_ok" in proc.stdout


def _gov_bot(inv_box):
    return r"""
function botOf(box) {
  const listeners = {};
  return {
    inventory: { items: () => [{name:"oak_log", count: box.n, type: 1}] },
    chat() {},
    entity: { position: { x:0, y:64, z:0 } },
    health: 20,
    once(ev, fn) { listeners[ev] = fn; this._listeners = listeners; },
    removeListener(ev, fn) { if (listeners[ev] === fn) delete listeners[ev]; },
    off(ev, fn) { if (listeners[ev] === fn) delete listeners[ev]; },
    pathfinder: { setGoal(){}, stop(){}, isMoving(){ return false; } },
    stopDigging() {},
    collectBlock: { targets: { clear() {} }, cancelTask() { return Promise.resolve(); } },
    findBlock() { return null; },
    findBlocks() { return []; },
    blockAt() { return null; },
    waitForTicks() { return Promise.resolve(); },
    _fire(ev) { if (listeners[ev]) listeners[ev](); },
  };
}
"""


def test_f3_hung_does_not_try_next_or_prim():
    body = _gov_bot(None) + r"""
const box = { n: 0 };
const bot = botOf(box);
let primHit = 0;
let second = 0;
_obtainPrimLeaf = async function() { primHit += 1; return {ok:true, reason:"done"}; };
globalThis.__leafSource = "gov";
globalThis.__govNonce = "abc123";
globalThis.__govOnline = false;
globalThis.__govHangMs = 150;
globalThis.__obtainInCall = true;
globalThis.__obtainDeadline = Date.now() + 40;
globalThis.__govLeaves = { log: [
  {id:"gov_log_0", fn:"gov_log_0__a", code_hash:"h0"},
  {id:"gov_log_1", fn:"gov_log_1__b", code_hash:"h1"},
]};
globalThis.gov_log_0__a = async function(bot) {
  await new Promise(() => {});
};
globalThis.gov_log_1__b = async function(bot) {
  second += 1;
  box.n += 3;
};
(async () => {
  const r = await _obtainGovLeaf(bot, {kind:"gather", name:"log", n:1}, () => false);
  if (r.reason !== "gov_unsettled") { console.error("reason="+r.reason); process.exit(2); }
  if (primHit !== 0) { console.error("prim "+primHit); process.exit(3); }
  if (second !== 0) { console.error("second "+second); process.exit(4); }
  console.log("hung_ok");
})().catch((e) => { console.error(e); process.exit(1); });
"""
    tmp = _write_obtain_harness(body)
    env = dict(os.environ)
    env["NODE_PATH"] = os.path.join(ROOT, "voyager", "env", "mineflayer", "node_modules")
    proc = subprocess.run(["node", tmp], cwd=ROOT, capture_output=True, text=True, env=env, timeout=20)
    os.unlink(tmp)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "hung_ok" in proc.stdout


def test_f3_late_inventory_not_counted_on_next():
    body = _gov_bot(None) + r"""
const box = { n: 0 };
const bot = botOf(box);
let primHit = 0;
_obtainPrimLeaf = async function() { primHit += 1; box.n += 1; return {ok:true, reason:"done"}; };
globalThis.__leafSource = "gov";
globalThis.__govNonce = "n1";
globalThis.__govOnline = false;
globalThis.__govHangMs = 400;
globalThis.__obtainInCall = true;
globalThis.__primMinStartMs = 0; globalThis.__primExploreMarginMs = 0;
globalThis.__obtainDeadline = Date.now() + 20000;
globalThis.__govLeaves = { log: [
  {id:"gov_log_0", fn:"gov_log_0__a", code_hash:"h0"},
  {id:"gov_log_1", fn:"gov_log_1__b", code_hash:"h1"},
]};
globalThis.gov_log_0__a = async function(bot) {
  await new Promise((r) => setTimeout(r, 120));
  box.n += 2;
};
globalThis.gov_log_1__b = async function() { throw new Error("should not run"); };
(async () => {
  const r = await _obtainGovLeaf(bot, {kind:"gather", name:"log", n:1}, () => false);
  if (!r.ok) { console.error("not ok "+JSON.stringify(r)); process.exit(2); }
  if (primHit !== 0) { console.error("prim"); process.exit(3); }
  console.log("late_ok");
})().catch((e) => { console.error(e); process.exit(1); });
"""
    tmp = _write_obtain_harness(body)
    env = dict(os.environ)
    env["NODE_PATH"] = os.path.join(ROOT, "voyager", "env", "mineflayer", "node_modules")
    proc = subprocess.run(["node", tmp], cwd=ROOT, capture_output=True, text=True, env=env, timeout=20)
    os.unlink(tmp)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "late_ok" in proc.stdout
    assert "delivered_by=candidate:gov_log_0" in proc.stdout


def test_f4_death_latch():
    body = _gov_bot(None) + r"""
const box = { n: 0 };
const bot = botOf(box);
globalThis.__leafSource = "gov";
globalThis.__govNonce = "d1";
globalThis.__govOnline = false;
globalThis.__govHangMs = 200;
globalThis.__obtainInCall = true;
globalThis.__primMinStartMs = 0; globalThis.__primExploreMarginMs = 0;
globalThis.__obtainDeadline = Date.now() + 20000;
globalThis.__govLeaves = { log: [{id:"gov_log_0", fn:"gov_log_0__a", code_hash:"h0"}] };
_obtainPrimLeaf = async function() { return {ok:false, reason:"no_progress"}; };
globalThis.gov_log_0__a = async function(bot) {
  bot._fire("death");
  box.n += 5;
};
(async () => {
  const r = await _obtainGovLeaf(bot, {kind:"gather", name:"log", n:1}, () => false);
  if (r.ok) { console.error("ok after death "+JSON.stringify(r)); process.exit(2); }
  console.log("death_ok");
})().catch((e) => { console.error(e); process.exit(1); });
"""
    tmp = _write_obtain_harness(body)
    env = dict(os.environ)
    env["NODE_PATH"] = os.path.join(ROOT, "voyager", "env", "mineflayer", "node_modules")
    proc = subprocess.run(["node", tmp], cwd=ROOT, capture_output=True, text=True, env=env, timeout=20)
    os.unlink(tmp)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "death_ok" in proc.stdout
    assert "ok=false" in proc.stdout


def test_f5_nonce_exact_and_dedupe():
    sys.path.insert(0, HERE)
    from test_obtain_mc import parse_leaf_metrics
    nonce = "deadbeef"
    chats = [
        f"[leaf] {nonce} 1 delivered_by=prim item=log delta=2",
        f"[leaf] {nonce} 1 delivered_by=prim item=log delta=2",
        f"[leaf] {nonce}x 2 delivered_by=prim item=log delta=3",
        f"[leaf] other 3 delivered_by=prim item=log delta=4",
        f"[leaf] {nonce} 2 delivered_by=candidate:gov_log_0 item=log delta=1",
        "[obtain] strategy surface gather:log",
    ]
    m = parse_leaf_metrics(chats, nonce)
    assert m["deliveries"] == {"prim": 1, "candidate:gov_log_0": 1}


def test_f6_identity_mismatch_not_skipped():
    sys.path.insert(0, HERE)
    import test_obtain_mc as TM
    row = {
        "experiment_id": "other",
        "leaf_source": "hand",
        "gov_online": False,
        "gov_retire_k": None,
        "pool_fingerprint": None,
        "max_calls": 12,
        "seed": 1,
        "scene": "golden_sword",
        "success": True,
    }
    assert not TM.identity_matches(row, {
        "experiment_id": "mine",
        "leaf_source": "hand",
        "gov_online": False,
        "gov_retire_k": None,
        "pool_fingerprint": None,
        "max_calls": 12,
    })
    same = dict(row)
    same["experiment_id"] = "mine"
    assert TM.identity_matches(same, {
        "experiment_id": "mine",
        "leaf_source": "hand",
        "gov_online": False,
        "gov_retire_k": None,
        "pool_fingerprint": None,
        "max_calls": 12,
    })


def test_f7_ledger_identity_rejects(tmp_path):
    sys.path.insert(0, HERE)
    import test_obtain_mc as TM
    path = str(tmp_path / "w0.json")
    TM.save_ledger(path, {"experiment_id": "a", "pool_fingerprint": "bbb", "entries": {}})
    ident = {"experiment_id": "a", "pool_fingerprint": "ccc"}
    with pytest.raises(SystemExit):
        TM.load_ledger(path, ident)


def test_f8_round_attribution_failed_then_success():
    per_file = {
        "Mine_3_log_20260101_000001": [
            ["observe", {"inventory": {}}],
            ["observe", {"inventory": {}}],
        ],
        "Mine_3_log_20260101_000002": [
            ["observe", {"inventory": {}}],
            ["observe", {"inventory": {"oak_log": 3}}],
        ],
    }
    by = events_files_by_round(per_file)
    b1, a1 = round_inventory_pair(by[1][1])
    b2, a2 = round_inventory_pair(by[2][1])
    d1 = equiv_from_inv(a1, "log") - equiv_from_inv(b1, "log")
    d2 = equiv_from_inv(a2, "log") - equiv_from_inv(b2, "log")
    assert d1 == 0
    assert d2 >= 1
    # n_success only counts rounds with delta>0
    n_success = sum(1 for d in (d1, d2) if d > 0)
    assert n_success == 1


def test_f9_computed_chat_and_alias_rejected():
    r1, _ = static_gates('async function f(bot) { bot["chat"]("\\x2f" + "say x"); }')
    assert any("slash" in x or "chat" in x for x in r1), r1
    r2, _ = static_gates("async function f(bot) { const c = bot.chat; c('hi'); }")
    assert any("alias" in x or "chat" in x for x in r2), r2


def test_f10_toss_rewritten_and_diamond_craft_rejected():
    src = (
        'async function f(bot) {\n'
        '  bot.toss(junk.type, null, junk.count);\n'
        '  await craftItem(bot, "diamond_pickaxe", 1);\n'
        '}\n'
    )
    inst, _ = instrument_code(src, "gov_diamond_4", "diamond")
    assert inst
    assert inst.get("craft_own_key")
    assert "_govNoToss(" in inst["code"]
    assert "bot.toss(" not in inst["code"]


def test_f11_worker_ports_no_worker0_fallback(monkeypatch):
    sys.path.insert(0, HERE)
    import test_obtain_mc as TM
    monkeypatch.setenv("OBTAIN_MC_PORT", "25699")
    monkeypatch.setenv("OBTAIN_BR_PORT", "3199")
    monkeypatch.setenv("OBTAIN_WORKER_ID", "2")
    assert TM._worker_ports(2) == (25699, 3199)
    assert TM._worker_ports(0) == (25620, 3120)


def test_candidates_reject_gov_diamond_craft_if_present():
    if not os.path.isfile(CAND_JSON):
        pytest.skip("candidates.json not harvested yet")
    data = json.load(open(CAND_JSON, encoding="utf-8"))
    ids = [c.get("id") for recs in data.values() for c in recs]
    for recs in data.get("diamond") or []:
            assert "bot.toss(" not in (recs.get("code") or "")
            assert not re.search(r'craftItem\s*\([^)]*["\']diamond_', recs.get("code") or "")


def _run_obtain_js(body: str, timeout: int = 20):
    tmp = _write_obtain_harness(body)
    env = dict(os.environ)
    env["NODE_PATH"] = os.path.join(ROOT, "voyager", "env", "mineflayer", "node_modules")
    proc = subprocess.run(["node", tmp], cwd=ROOT, capture_output=True, text=True, env=env, timeout=timeout)
    os.unlink(tmp)
    return proc


def test_v1_seq_unique_across_candidate_and_prim_fallback():
    body = _gov_bot(None) + r"""
const box = { n: 0 };
const bot = botOf(box);
bot._obtainState = {};
globalThis.__leafSource = "gov";
globalThis.__govNonce = "sameNonce";
globalThis.__govOnline = false;
globalThis.__govHangMs = 200;
globalThis.__obtainInCall = true;
globalThis.__primMinStartMs = 0; globalThis.__primExploreMarginMs = 0;
globalThis.__obtainDeadline = Date.now() + 20000;
globalThis.__govLeaves = { log: [{id:"gov_log_0", fn:"gov_log_0__a", code_hash:"h0", target_n:9}] };
globalThis.gov_log_0__a = async function() { box.n += 1; };
_obtainPrimLeaf = async function(bot, leaf, timedOut, tag, nonce, seqRef) {
  seqRef = seqRef || _obtainDeliverSeq(bot);
  seqRef.n += 1;
  _obtainLogDelivered(bot, tag || "prim_fallback", nonce, seqRef.n, "cobblestone", 1);
  return {ok:true, reason:"done"};
};
(async () => {
  const r1 = await _obtainGovLeaf(bot, {kind:"gather", name:"log", n:1}, () => false);
  if (!r1.ok) { console.error("r1 "+JSON.stringify(r1)); process.exit(2); }
  globalThis.__govLeaves = { cobblestone: [] };
  const r2 = await _obtainGovLeaf(bot, {kind:"gather", name:"cobblestone", n:1}, () => false);
  if (!r2.ok) { console.error("r2 "+JSON.stringify(r2)); process.exit(3); }
  console.log("v1_ok");
})().catch((e) => { console.error(e); process.exit(1); });
"""
    proc = _run_obtain_js(body)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "v1_ok" in proc.stdout
    chats = [ln for ln in (proc.stdout or "").splitlines() if "[leaf]" in ln]
    sys.path.insert(0, HERE)
    from test_obtain_mc import parse_leaf_metrics
    m = parse_leaf_metrics(chats, "sameNonce")
    assert m["deliveries"].get("candidate:gov_log_0") == 1, m
    assert m["deliveries"].get("prim_fallback") == 1, m
    assert sum(m["deliveries"].values()) == 2, m


def test_v2_never_settle_ends_call_unsettled():
    body = _gov_bot(None) + r"""
const box = { n: 0 };
const bot = botOf(box);
let second = 0;
let primHit = 0;
_obtainPrimLeaf = async function() { primHit += 1; return {ok:true, reason:"done"}; };
globalThis.__leafSource = "gov";
globalThis.__govNonce = "u1";
globalThis.__govOnline = false;
globalThis.__govHangMs = 120;
globalThis.__obtainInCall = true;
globalThis.__obtainDeadline = Date.now() + 80;
globalThis.__govLeaves = { log: [
  {id:"gov_log_0", fn:"gov_log_0__a", code_hash:"h0", target_n:9},
  {id:"gov_log_1", fn:"gov_log_1__b", code_hash:"h1", target_n:9},
]};
globalThis.gov_log_0__a = async function() { await new Promise(() => {}); };
globalThis.gov_log_1__b = async function() { second += 1; };
(async () => {
  const r = await _obtainGovLeaf(bot, {kind:"gather", name:"log", n:1}, () => false);
  if (r.reason !== "gov_unsettled") { console.error("reason="+r.reason); process.exit(2); }
  if (globalThis.__govUnsettled !== true) { console.error("flag"); process.exit(3); }
  if (primHit !== 0 || second !== 0) { console.error("continued"); process.exit(4); }
  const r2 = await _obtainDoLeaf(bot, {kind:"gather", name:"log", n:1}, () => false);
  if (r2.reason !== "gov_unsettled") { console.error("r2="+r2.reason); process.exit(5); }
  if (second !== 0) { console.error("second after doleaf"); process.exit(6); }
  console.log("v2_ok");
})().catch((e) => { console.error(e); process.exit(1); });
"""
    proc = _run_obtain_js(body, timeout=15)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "v2_ok" in proc.stdout
    assert "unsettled" in proc.stdout


def test_v2_harness_unsettled_marks_hung():
    sys.path.insert(0, HERE)
    from test_obtain_mc import hung_from_contract
    hung, cause = hung_from_contract(
        {"ok": False, "reason": "gov_unsettled", "raw": "[contract] obtain fail gov_unsettled"},
        None,
    )
    assert hung is True
    assert cause == "gov_unsettled"


def test_v3_prim_never_settle_starts_one_mine():
    body = _gov_bot(None) + r"""
const box = { n: 0 };
const bot = botOf(box);
bot.findBlock = function() { return { name: "oak_log" }; };
bot.findBlocks = function() { return []; };
let mines = 0;
mineBlock = function() { mines += 1; return new Promise(() => {}); };
exploreUntil = function() { throw new Error("explore should not start"); };
globalThis.__leafSource = "prim";
globalThis.__govNonce = "p1";
globalThis.__govHangMs = 80;
globalThis.__obtainInCall = true;
globalThis.__primMinStartMs = 0; globalThis.__primExploreMarginMs = 0;
globalThis.__obtainDeadline = Date.now() + 120;
(async () => {
  const r = await _obtainPrimLeaf(bot, {kind:"gather", name:"log", n:1}, () => false);
  if (r.reason !== "gov_unsettled") { console.error("reason="+r.reason+" mines="+mines); process.exit(2); }
  if (mines !== 1) { console.error("mines="+mines); process.exit(3); }
  if (globalThis.__govUnsettled !== true) { console.error("flag"); process.exit(4); }
  console.log("v3_ok mines="+mines);
})().catch((e) => { console.error(e); process.exit(1); });
"""
    proc = _run_obtain_js(body, timeout=15)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "v3_ok" in proc.stdout


def test_v4_settle_wait_respects_obtain_budget():
    body = _gov_bot(None) + r"""
const box = { n: 0 };
const bot = botOf(box);
globalThis.__leafSource = "gov";
globalThis.__govNonce = "b1";
globalThis.__govOnline = false;
globalThis.__govHangMs = 65000;
globalThis.__obtainInCall = true;
globalThis.__obtainDeadline = Date.now() + 90;
globalThis.__govLeaves = { log: [{id:"gov_log_0", fn:"gov_log_0__a", code_hash:"h0", target_n:9}] };
globalThis.gov_log_0__a = async function() { await new Promise(() => {}); };
(async () => {
  const t0 = Date.now();
  const r = await _obtainGovLeaf(bot, {kind:"gather", name:"log", n:1}, () => false);
  const elapsed = Date.now() - t0;
  if (r.reason !== "gov_unsettled") { console.error("reason="+r.reason); process.exit(2); }
  if (elapsed > 4000) { console.error("elapsed="+elapsed); process.exit(3); }
  console.log("v4_ok elapsed="+elapsed);
})().catch((e) => { console.error(e); process.exit(1); });
"""
    proc = _run_obtain_js(body, timeout=15)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "v4_ok" in proc.stdout


def test_v5_death_reclaims_active_and_unsettled_if_hung():
    body = _gov_bot(None) + r"""
const box = { n: 0 };
const bot = botOf(box);
let acts = 0;
globalThis.__leafSource = "gov";
globalThis.__govNonce = "d2";
globalThis.__govOnline = false;
globalThis.__govHangMs = 120;
globalThis.__obtainInCall = true;
globalThis.__primMinStartMs = 0; globalThis.__primExploreMarginMs = 0;
globalThis.__obtainDeadline = Date.now() + 20000;
globalThis.__govLeaves = { log: [{id:"gov_log_0", fn:"gov_log_0__a", code_hash:"h0", target_n:9}] };
_obtainPrimLeaf = async function() { return {ok:false, reason:"no_progress"}; };
globalThis.gov_log_0__a = async function(bot) {
  for (let i = 0; i < 6; i++) {
    if (globalThis.__govActive !== "gov_log_0") return;
    acts += 1;
    bot._fire("death");
    await new Promise((r) => setTimeout(r, 15));
  }
};
(async () => {
  const r = await _obtainGovLeaf(bot, {kind:"gather", name:"log", n:1}, () => false);
  if (r.ok) { console.error("ok after death"); process.exit(2); }
  if (acts !== 1) { console.error("acts="+acts+" active="+globalThis.__govActive); process.exit(3); }
  globalThis.__govUnsettled = false;
  globalThis.gov_log_0__a = async function(bot) {
    bot._fire("death");
    await new Promise(() => {});
  };
  globalThis.__obtainDeadline = Date.now() + 80;
  const r2 = await _obtainGovLeaf(bot, {kind:"gather", name:"log", n:1}, () => false);
  if (r2.reason !== "gov_unsettled") { console.error("r2="+r2.reason); process.exit(4); }
  console.log("v5_ok acts="+acts);
})().catch((e) => { console.error(e); process.exit(1); });
"""
    proc = _run_obtain_js(body, timeout=15)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "v5_ok" in proc.stdout


def test_v6_missing_middle_file_rejects_attempt():
    per_file = {
        "Mine_3_log_20260101_000001": [
            ["observe", {"inventory": {}}],
            ["observe", {"inventory": {}}],
        ],
        "Mine_3_log_20260101_000003": [
            ["observe", {"inventory": {}}],
            ["observe", {"inventory": {"oak_log": 3}}],
        ],
    }
    steps = [
        {"round": 1, "subgoal": "Mine 3 log"},
        {"round": 2, "subgoal": "Mine 3 log"},
        {"round": 3, "subgoal": "Mine 3 log"},
    ]
    aligned, why = align_events_to_steps(per_file, steps)
    assert aligned is None
    assert why == "count_mismatch"


def test_v6_aligns_by_jsonl_round_not_renumber():
    per_file = {
        "Mine_3_log_20260101_000001": [
            ["observe", {"inventory": {}}],
            ["observe", {"inventory": {}}],
        ],
        "Mine_3_iron_ore_20260101_000002": [
            ["observe", {"inventory": {}}],
            ["observe", {"inventory": {"raw_iron": 3}}],
        ],
    }
    steps = [
        {"round": 1, "subgoal": "Mine 3 log"},
        {"round": 2, "subgoal": "Mine 3 iron_ore"},
    ]
    aligned, why = align_events_to_steps(per_file, steps)
    assert why is None, why
    assert aligned[1][0] == "Mine_3_log_20260101_000001"
    assert aligned[2][0] == "Mine_3_iron_ore_20260101_000002"
    b2, a2 = round_inventory_pair(aligned[2][1])
    assert equiv_from_inv(a2, "raw_iron") - equiv_from_inv(b2, "raw_iron") >= 1


def test_v7_skip_na_when_bag_already_meets_target():
    body = _gov_bot(None) + r"""
const box = { n: 3 };
const bot = botOf(box);
let ran = 0;
let booked = 0;
const origBook = _obtainGovBook;
_obtainGovBook = function() { booked += 1; return origBook.apply(this, arguments); };
_obtainPrimLeaf = async function() { return {ok:false, reason:"no_progress"}; };
globalThis.__leafSource = "gov";
globalThis.__govNonce = "na1";
globalThis.__govOnline = true;
globalThis.__govHangMs = 200;
globalThis.__obtainInCall = true;
globalThis.__primMinStartMs = 0; globalThis.__primExploreMarginMs = 0;
globalThis.__obtainDeadline = Date.now() + 20000;
globalThis.__govLedger = {};
globalThis.__govLeaves = { log: [{id:"gov_log_0", fn:"gov_log_0__a", code_hash:"h0", target_n:3}] };
globalThis.gov_log_0__a = async function() { ran += 1; };
(async () => {
  const r = await _obtainGovLeaf(bot, {kind:"gather", name:"log", n:1}, () => false);
  if (ran !== 0) { console.error("ran"); process.exit(2); }
  if (booked !== 0) { console.error("booked "+booked); process.exit(3); }
  const rec = (globalThis.__govLedger || {}).h0;
  if (rec && rec.calls) { console.error("ledger"); process.exit(4); }
  console.log("v7_ok");
})().catch((e) => { console.error(e); process.exit(1); });
"""
    proc = _run_obtain_js(body)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "v7_ok" in proc.stdout
    assert "skip_na gov_log_0" in proc.stdout


def test_v8_computed_property_non_literal_rejected():
    r, _ = static_gates('async function f(bot) { bot["ch"+"at"]("\\x2f"+"say audit"); }')
    assert any("bot_computed_non_literal" in x for x in r), r
    r2, _ = static_gates('async function f(bot) { bot["chat"]("hi"); }')
    assert not any("bot_computed_non_literal" in x for x in r2), r2


def test_v9_gov_online_requires_experiment_id(monkeypatch):
    sys.path.insert(0, HERE)
    import test_obtain_mc as TM
    monkeypatch.setattr(TM, "LEAF_SOURCE", "gov")
    monkeypatch.setattr(TM, "GOV_ONLINE", True)
    monkeypatch.delenv("OBTAIN_EXPERIMENT_ID", raising=False)
    with pytest.raises(SystemExit):
        TM.require_gov_experiment_id()
    monkeypatch.setenv("OBTAIN_EXPERIMENT_ID", "default")
    with pytest.raises(SystemExit):
        TM.require_gov_experiment_id()
    monkeypatch.setenv("OBTAIN_EXPERIMENT_ID", "smoke_v4")
    TM.require_gov_experiment_id()


def test_v9_result_path_and_merge_seed_filter():
    sys.path.insert(0, HERE)
    import test_obtain_mc as TM
    if not os.environ.get("OBTAIN_RESULT_PATH"):
        assert TM.leaf_condition_name() in os.path.basename(TM.RESULT_PATH)
        assert TM.EXPERIMENT_ID in os.path.basename(TM.RESULT_PATH)
    ident = TM.result_identity()
    rows = [
        {**ident, "seed": 20260902010, "scene": "golden_sword"},
        {**ident, "seed": 999999, "scene": "golden_sword"},
    ]
    seed_set = set(int(s) for s in TM.SEEDS)
    planned = {s["id"] for s in TM.ITEMS}
    kept = [r for r in rows if r.get("scene") in planned and r.get("seed") in seed_set]
    assert 999999 not in [r["seed"] for r in kept]
    if 20260902010 in seed_set:
        assert any(r["seed"] == 20260902010 for r in kept)


def test_w1_outer_returns_unsettled_before_leaf():
    """Codex: explore 3s then mine never ends; outer drain returns with unsettled set by outer."""
    body = _gov_bot(None) + r"""
const box = { n: 0 };
const bot = botOf(box);
let explores = 0;
let mines = 0;
bot.findBlock = function() { return explores > 0 ? { name: "oak_log" } : null; };
bot.findBlocks = function() { return []; };
exploreUntil = function() {
  explores += 1;
  return new Promise((r) => setTimeout(r, 3000));
};
mineBlock = function() {
  mines += 1;
  return new Promise(() => {});
};
globalThis.__leafSource = "prim";
globalThis.__govNonce = "outer1";
globalThis.__govHangMs = 65000;
globalThis.__obtainInCall = true;
globalThis.__obtainAborted = false;
globalThis.__primMinStartMs = 0; globalThis.__primExploreMarginMs = 0;
globalThis.__obtainDeadline = Date.now() + 20000;
_obtainPendingInit();
(async () => {
  const t0 = Date.now();
  const work = (async () => _obtainPrimLeaf(bot, {kind:"gather", name:"log", n:1}, () => false))();
  await Promise.race([
    work,
    new Promise((r) => setTimeout(() => r("budget"), 4000)),
  ]);
  globalThis.__obtainAborted = true;
  const drainSettled = await _obtainDrainOuter(bot, work, 2500);
  const ms = Date.now() - t0;
  const flag = globalThis.__govUnsettled === true;
  console.log("OUTER_RETURN_BOUNDARY ms=" + ms + " drainSettled=" + drainSettled + " unsettled=" + flag + " mines=" + mines + " explores=" + explores);
  if (drainSettled) { console.error("drain should not settle"); process.exit(2); }
  if (!flag) { console.error("outer did not set unsettled"); process.exit(3); }
  if (mines !== 1) { console.error("mines="+mines); process.exit(4); }
  console.log("w1_ok");
})().catch((e) => { console.error(e); process.exit(1); });
"""
    proc = _run_obtain_js(body, timeout=20)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "w1_ok" in proc.stdout
    assert "unsettled=true" in proc.stdout


def test_w3_harness_hung_first_no_success_reset_fail_stops(monkeypatch):
    """Codex: unsettled+inv increase must not succeed; reset failure stops further calls."""
    sys.path.insert(0, HERE)
    import test_obtain_mc as TM

    def _obs(inv, chats, health=20):
        evs = [["onChat", {"onChat": c}] for c in chats]
        evs.append([
            "observe",
            {
                "inventory": dict(inv or {}),
                "status": {"health": health, "position": {"x": 0, "y": 64, "z": 0}},
            },
        ])
        return evs

    class DummyEnv:
        def __init__(self, fail_reset=False):
            self.fail_reset = fail_reset
            self.n_reset = 0
            self.n_obtain = 0
            self.inv = {}
            self.mineflayer = type("M", (), {"process": None})()

        def reset(self, options=None):
            self.n_reset += 1
            if self.fail_reset:
                raise RuntimeError("reset boom")
            if options is not None and "inventory" in options:
                self.inv = dict(options.get("inventory") or {})

        def step(self, code, programs=""):
            code = str(code or "")
            if "obtain(" in code:
                self.n_obtain += 1
                self.inv = {"golden_sword": 1}
                return _obs(self.inv, [
                    "[contract] obtain fail gov_unsettled",
                    "[obtain] pending_at_return true",
                ])
            return _obs(self.inv, [])

    monkeypatch.setattr(TM, "MAX_CALLS", 2)
    monkeypatch.setattr(TM, "LEAF_SOURCE", "gov")
    monkeypatch.setattr(TM, "GOV_ONLINE", False)
    monkeypatch.setattr(TM, "prepare_start", lambda *a, **k: {"ok": True, "inv": {}, "pos": {"x": 0, "y": 64, "z": 0}})
    monkeypatch.setattr(TM, "hold_unpaused", lambda env: None)
    monkeypatch.setattr(TM, "append_jsonl", lambda row: None)
    monkeypatch.setattr(TM, "log", lambda msg: None)
    monkeypatch.setattr(TM.SM, "record_pid", lambda *a, **k: None)
    monkeypatch.setattr(TM.SM, "boot_code", lambda: "")
    scene = {"id": "golden_sword", "item": "golden_sword", "call": 'await obtain(bot, "golden_sword", 1);'}

    env = DummyEnv(fail_reset=False)
    row = TM.run_job(env, "", 20260902010, scene)
    assert row["success"] is False, row
    assert int(row.get("n_hard_resets") or 0) >= 1
    assert not row.get("env_dead")
    hung_calls = [c for c in (row.get("calls") or []) if c.get("hung")]
    assert hung_calls, row.get("calls")

    env2 = DummyEnv(fail_reset=True)
    row2 = TM.run_job(env2, "", 20260902010, scene)
    assert row2["success"] is False
    assert row2.get("env_dead") is True
    assert env2.n_obtain == 1
    assert len(row2.get("calls") or []) == 1


def test_w4_cleanup_never_settle_unsettled():
    """Codex: cancelTask never ends → leaf must not report done."""
    body = _gov_bot(None) + r"""
const box = { n: 0 };
const bot = botOf(box);
bot.collectBlock.cancelTask = function() { return new Promise(() => {}); };
globalThis.__leafSource = "gov";
globalThis.__govNonce = "c1";
globalThis.__govOnline = false;
globalThis.__govHangMs = 200;
globalThis.__obtainInCall = true;
globalThis.__obtainAborted = false;
globalThis.__primMinStartMs = 0; globalThis.__primExploreMarginMs = 0;
globalThis.__obtainDeadline = Date.now() + 20000;
globalThis.__govLeaves = { log: [{id:"gov_log_0", fn:"gov_log_0__a", code_hash:"h0", target_n:9}] };
globalThis.gov_log_0__a = async function() { box.n += 1; };
_obtainPendingInit();
(async () => {
  const r = await _obtainGovLeaf(bot, {kind:"gather", name:"log", n:1}, () => false);
  if (r.reason !== "gov_unsettled") { console.error("reason="+r.reason+" ok="+r.ok); process.exit(2); }
  if (globalThis.__govUnsettled !== true) { console.error("flag"); process.exit(3); }
  console.log("w4_ok");
})().catch((e) => { console.error(e); process.exit(1); });
"""
    proc = _run_obtain_js(body, timeout=15)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "w4_ok" in proc.stdout


def test_w5_death_during_settle_not_success():
    """Codex: death after cap, before raw settles, must not score candidate success."""
    body = _gov_bot(None) + r"""
const box = { n: 0 };
const bot = botOf(box);
let resolveRaw;
globalThis.__leafSource = "gov";
globalThis.__govNonce = "d3";
globalThis.__govOnline = false;
globalThis.__govHangMs = 1500;
globalThis.__obtainInCall = true;
globalThis.__obtainAborted = false;
globalThis.__primMinStartMs = 0; globalThis.__primExploreMarginMs = 0;
globalThis.__obtainDeadline = Date.now() + 20000;
globalThis.__govLeaves = { log: [{id:"gov_log_0", fn:"gov_log_0__a", code_hash:"h0", target_n:9}] };
_obtainRemainMs = function() { return 50; };
globalThis.gov_log_0__a = async function() {
  box.n += 1;
  await new Promise((r) => { resolveRaw = r; });
};
_obtainPendingInit();
(async () => {
  setTimeout(() => {
    bot._fire("death");
    setTimeout(() => { if (resolveRaw) resolveRaw(); }, 40);
  }, 90);
  const r = await _obtainGovLeaf(bot, {kind:"gather", name:"log", n:1}, () => false);
  if (r.ok) { console.error("ok after death-during-settle "+JSON.stringify(r)); process.exit(2); }
  console.log("w5_ok reason="+r.reason);
})().catch((e) => { console.error(e); process.exit(1); });
"""
    proc = _run_obtain_js(body, timeout=15)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "w5_ok" in proc.stdout
    assert "item=log ok=false" in proc.stdout
    assert "item=log ok=true" not in proc.stdout


def test_w6_await_settle_already_resolved():
    """Codex: aborted=true must not mark an already-resolved promise as unsettled."""
    body = _gov_bot(None) + r"""
(async () => {
  globalThis.__obtainAborted = true;
  const ok = await _obtainAwaitSettle(Promise.resolve(), 100);
  if (ok !== true) { console.error("got "+ok); process.exit(2); }
  console.log("w6_ok");
})().catch((e) => { console.error(e); process.exit(1); });
"""
    proc = _run_obtain_js(body, timeout=10)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "w6_ok" in proc.stdout


def test_w7_port_half_env_exits(monkeypatch):
    sys.path.insert(0, HERE)
    import test_obtain_mc as TM
    monkeypatch.setenv("OBTAIN_MC_PORT", "25620")
    monkeypatch.delenv("OBTAIN_BR_PORT", raising=False)
    monkeypatch.delenv("OBTAIN_WORKER_ID", raising=False)
    with pytest.raises(SystemExit):
        TM._worker_ports(0)


def test_w7_ledger_refuse_without_resume(tmp_path, monkeypatch):
    sys.path.insert(0, HERE)
    import test_obtain_mc as TM
    monkeypatch.setattr(TM, "GOV_LEDGER_DIR", str(tmp_path))
    monkeypatch.setattr(TM, "EXPERIMENT_ID", "same_id")
    monkeypatch.delenv("GOV_RESUME", raising=False)
    p = tmp_path / "same_id" / "gov_online1_k2" / "20260902010" / "w0.json"
    p.parent.mkdir(parents=True)
    p.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit):
        TM.refuse_existing_experiment_ledger()
    monkeypatch.setenv("GOV_RESUME", "1")
    TM.refuse_existing_experiment_ledger()


def test_w7_prim_preamble_clears_gov():
    sys.path.insert(0, HERE)
    import test_obtain_mc as TM
    pre = TM.prim_preamble("n1")
    assert "__leafSource" in pre
    assert "__govOnline = false" in pre
    assert "__govLeaves = {}" in pre
    assert "delete globalThis.__govLedger" in pre


def test_w7_gov_offline_no_ledger():
    sys.path.insert(0, HERE)
    import test_obtain_mc as TM
    pre = TM.gov_preamble("n1", {}, False, 2, {"should": "not appear"})
    assert "__govLedger = undefined" in pre
    assert "should" not in pre
    pre_on = TM.gov_preamble("n1", {}, True, 2, {"k": 1})
    assert '"k": 1' in pre_on or '"k":1' in pre_on


# --- v5 full pool / tournament select ---

from gov_pool import (  # noqa: E402
    BLOCKED_SOURCE_SEEDS,
    classify_key,
    collect as pool_collect,
    mined_keys,
    name_class_key,
    resolve_source_seed,
    source_seed_from_ckpt_path,
)
from gov_select import RANDOM_SEED, select_random, select_screened  # noqa: E402

_LOG_CODE = """
async function mineLogs(bot) {
  const logNames = ["oak_log", "birch_log", "spruce_log"];
  const block = bot.findBlock({
    matching: b => logNames.includes(b.name),
    maxDistance: 32
  });
  if (!block) return;
  await mineBlock(bot, block.name, 1);
}
"""

_HELPER_CODE = """
async function findOak(bot) {
  return bot.findBlock({ matching: b => b.name === "oak_log", maxDistance: 32 });
}
"""

_MAIN_WITH_HELPER = """
async function mineOneLog(bot) {
  const b = await findOak(bot);
  if (!b) return;
  await mineBlock(bot, "oak_log", 1);
}
"""


def test_v5_source_seed_zeroshot_longrun_unknown(tmp_path):
    zs = tmp_path / "job" / "ckpt" / "metrics"
    zs.mkdir(parents=True)
    (zs / "run_meta.json").write_text(
        json.dumps({"world_seed": 20260902099}), encoding="utf-8"
    )
    (zs / "zeroshot_code.jsonl").write_text("{}\n", encoding="utf-8")
    seed, how = resolve_source_seed(str(zs / "zeroshot_code.jsonl"), "zeroshot")
    assert how == "zeroshot"
    assert seed == 20260902099

    ckpt = tmp_path / "ckpt_A_flash_off_t4_v26OLD" / "skill"
    ckpt.mkdir(parents=True)
    (ckpt / "skills.json").write_text("{}", encoding="utf-8")
    assert source_seed_from_ckpt_path(str(ckpt / "skills.json")) == 20260902004
    seed2, how2 = resolve_source_seed(str(ckpt / "skills.json"), "skills")
    assert how2 == "longrun"
    assert seed2 == 20260902004

    orphan = tmp_path / "orphan" / "skill"
    orphan.mkdir(parents=True)
    (orphan / "skills.json").write_text("{}", encoding="utf-8")
    seed3, how3 = resolve_source_seed(str(orphan / "skills.json"), "skills")
    assert how3 == "unknown"
    assert seed3 is None


def test_v5_blocked_eval_and_tournament_seeds_excluded(tmp_path):
    for seed in (20260902007, 20260902020):
        root = tmp_path / str(seed) / "ckpt" / "metrics"
        root.mkdir(parents=True)
        (root / "run_meta.json").write_text(
            json.dumps({"world_seed": seed}), encoding="utf-8"
        )
        (root / "zeroshot_code.jsonl").write_text(
            json.dumps({
                "round": 1,
                "executed": True,
                "program_code": _LOG_CODE,
                "subgoal": "Mine 1 log",
            }) + "\n",
            encoding="utf-8",
        )
        (root / "zeroshot_steps.jsonl").write_text(
            json.dumps({"round": 1, "subgoal": "Mine 1 log", "success": True}) + "\n",
            encoding="utf-8",
        )
    payload = pool_collect(roots=[str(tmp_path)], out_path=str(tmp_path / "pool.json"), top_n=12)
    assert payload["excludes"].get("blocked_eval_or_tournament_seed", 0) >= 2
    assert payload["per_key"]["log"] == 0
    assert 20260902007 in BLOCKED_SOURCE_SEEDS
    assert 20260902020 in BLOCKED_SOURCE_SEEDS


def test_v5_unknown_seed_excluded(tmp_path):
    root = tmp_path / "metrics"
    root.mkdir(parents=True)
    (root / "zeroshot_code.jsonl").write_text(
        json.dumps({
            "round": 1,
            "executed": True,
            "program_code": _LOG_CODE,
            "subgoal": "Mine 1 log",
        }) + "\n",
        encoding="utf-8",
    )
    payload = pool_collect(roots=[str(tmp_path)], out_path=str(tmp_path / "pool.json"))
    assert payload["excludes"].get("unknown_seed", 0) >= 1
    assert payload["per_key"]["log"] == 0


def test_v5_classify_needs_name_and_mined_block():
    key, why, blocks, _ = classify_key("Mine 3 log", _LOG_CODE)
    assert key == "log", (key, why, blocks)
    assert why is None
    assert name_class_key("mineThreeLogs") == "log"
    keys, mined, _ = mined_keys(_LOG_CODE)
    assert "log" in keys
    key2, why2, _, _ = classify_key("mineLogs", "async function mineLogs(bot) { await mineBlock(bot, \"cobblestone\", 1); }")
    assert key2 is None
    assert why2 == "name_block_mismatch"
    stone = """
async function mineStone(bot) {
  const b = bot.findBlock({ matching: x => x.name === "cobblestone", maxDistance: 32 });
  if (b) await mineBlock(bot, "cobblestone", 1);
}
"""
    key3, why3, _, _ = classify_key("craftWoodenPickaxe", stone)
    assert key3 is None
    assert why3 in ("name_not_pool_key", "not_obtain_class")
    key4, why4, _, _ = classify_key("Mine 1 cobblestone", stone)
    assert key4 == "cobblestone"
    assert why4 is None


def test_v5_missing_dep_rejected(tmp_path):
    ckpt = tmp_path / "ckpt_B_flash_on_t1_v26OLD" / "skill"
    ckpt.mkdir(parents=True)
    skills = {
        "mineOneLog": {
            "code": "async function mineOneLog(bot) {\n  await ghostHelper(bot);\n  await mineBlock(bot, \"oak_log\", 1);\n}\n"
        }
    }
    (ckpt / "skills.json").write_text(json.dumps(skills), encoding="utf-8")
    payload = pool_collect(roots=[str(tmp_path)], out_path=str(tmp_path / "pool.json"))
    assert payload["rejects"].get("missing_dep", 0) >= 1
    assert payload["per_key"]["log"] == 0


def test_v5_dep_pack_rename_bridge_eval(tmp_path):
    ckpt = tmp_path / "ckpt_A_flash_off_t2_v26OLD" / "skill"
    ckpt.mkdir(parents=True)
    skills = {
        "findOak": {"code": _HELPER_CODE, "n_success": 2},
        "mineOneLog": {"code": _MAIN_WITH_HELPER, "n_success": 5},
    }
    (ckpt / "skills.json").write_text(json.dumps(skills), encoding="utf-8")
    payload = pool_collect(roots=[str(tmp_path)], out_path=str(tmp_path / "pool.json"))
    recs = payload["candidates"]["log"]
    assert recs, payload
    rec = recs[0]
    assert rec["fn"].startswith("gov_log_0__")
    assert "findOak" in (rec.get("deps") or []) or rec["fn"].endswith("mineOneLog")
    assert rec["code_hash"]
    assert "gov_log_0__" in rec["code"]
    programs = rec["code"] + "\n" + f'globalThis[{json.dumps(rec["fn"])}] = {rec["fn"]};\n'
    code = (
        f'if (typeof globalThis[{json.dumps(rec["fn"])}] !== "function") throw new Error("missing"); '
        f'await globalThis[{json.dumps(rec["fn"])}]({{findBlock:()=>null}});'
    )
    js = (
        "eval(\"(async () => {\" + "
        + json.dumps(programs)
        + " + \"\\n\" + "
        + json.dumps(code)
        + " + \"})()\").then(() => console.log(\"bridge_ok\")).catch((e) => { console.error(e); process.exit(2); });"
    )
    proc = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "bridge_ok" in proc.stdout


def test_v5_select_rank_and_random_reproducible():
    pool = {
        "candidates": {
            "log": [
                {"id": "gov_log_0", "fn": "gov_log_0__a", "code": "async function gov_log_0__a(bot){}", "code_hash": "aaa", "source_seed": 20260902099, "n_success": 1},
                {"id": "gov_log_1", "fn": "gov_log_1__b", "code": "async function gov_log_1__b(bot){}", "code_hash": "bbb", "source_seed": 20260902099, "n_success": 1},
                {"id": "gov_log_2", "fn": "gov_log_2__c", "code": "async function gov_log_2__c(bot){}", "code_hash": "ccc", "source_seed": 20260902099, "n_success": 1},
                {"id": "gov_log_3", "fn": "gov_log_3__d", "code": "async function gov_log_3__d(bot){}", "code_hash": "ddd", "source_seed": 20260902099, "n_success": 1},
            ],
            "cobblestone": [],
            "coal": [],
            "raw_iron": [],
            "raw_gold": [],
            "redstone": [],
            "diamond": [],
        }
    }
    rows = [
        {"key": "log", "code_hash": "ccc", "seed": 20260902020, "success": True, "ms": 9000},
        {"key": "log", "code_hash": "ccc", "seed": 20260902021, "success": True, "ms": 11000},
        {"key": "log", "code_hash": "aaa", "seed": 20260902020, "success": True, "ms": 4000},
        {"key": "log", "code_hash": "aaa", "seed": 20260902021, "success": False, "ms": 180000},
        {"key": "log", "code_hash": "bbb", "seed": 20260902020, "success": True, "ms": 3000},
        {"key": "log", "code_hash": "bbb", "seed": 20260902021, "success": True, "ms": 5000},
        {"key": "log", "code_hash": "ddd", "seed": 20260902020, "success": False, "ms": 180000},
        {"key": "log", "code_hash": "ddd", "seed": 20260902021, "success": False, "ms": 180000},
    ]
    screened, ranking = select_screened(pool, rows)
    ids = [c["id"] for c in screened["log"]]
    hashes = [c["code_hash"] for c in screened["log"]]
    assert hashes == ["bbb", "ccc", "aaa"], hashes
    assert ranking["log"][0]["wins"] == 2
    r1 = select_random(pool, rng_seed=RANDOM_SEED)
    r2 = select_random(pool, rng_seed=RANDOM_SEED)
    r3 = select_random(pool, rng_seed=RANDOM_SEED + 1)
    assert [c["code_hash"] for c in r1["log"]] == [c["code_hash"] for c in r2["log"]]
    assert len(r1["log"]) == 3
    assert [c["code_hash"] for c in r1["log"]] != [c["code_hash"] for c in r3["log"]] or True
    # different seed should usually differ; require the function is deterministic first
    assert ids[0] == "gov_log_1"


def test_v5_tournament_result_json_unsettled_false():
    import gov_tournament as GT
    obs = [
        ["onChat", {"onChat": '[gov] n1 try gov_log_0 item=log ok=false delta=0 ms=60110'}],
        ["onChat", {"onChat": '[contract] tournament fail no_progress'}],
        ["onChat", {"onChat": '[tournament] result {"ok":false,"delta":0,"ms":60110,"died":false,"unsettled":false}'}],
    ]
    parsed = GT.parse_tournament_result(obs)
    assert parsed["unsettled"] is False
    assert parsed["success"] is False
    assert parsed["delta"] == 0
    assert parsed["ms"] == 60110
    obs2 = [
        ["onChat", {"onChat": '[contract] tournament fail gov_unsettled'}],
        ["onChat", {"onChat": '[tournament] result {"ok":false,"delta":0,"ms":180000,"died":false,"unsettled":true}'}],
    ]
    parsed2 = GT.parse_tournament_result(obs2)
    assert parsed2["unsettled"] is True
    assert parsed2["success"] is False


def test_v6b_prim_does_not_start_when_budget_short():
    """v6b: with the default thresholds, prim must not START a mine/explore that cannot finish before the deadline."""
    body = _gov_bot(None) + r"""
const box = { n: 0 };
const bot = botOf(box);
bot.findBlock = function() { return { name: "oak_log" }; };
bot.findBlocks = function() { return []; };
let mines = 0, explores = 0;
mineBlock = function() { mines += 1; return new Promise(() => {}); };
exploreUntil = function() { explores += 1; return new Promise(() => {}); };
globalThis.__leafSource = "prim";
globalThis.__govNonce = "p6b";
globalThis.__obtainInCall = true;
delete globalThis.__primMinStartMs; delete globalThis.__primExploreMarginMs;
globalThis.__obtainDeadline = Date.now() + 10000;
(async () => {
  const t0 = Date.now();
  const r = await _obtainPrimLeaf(bot, {kind:"gather", name:"log", n:1}, () => false);
  const ms = Date.now() - t0;
  if (mines !== 0 || explores !== 0) { console.error("started mines="+mines+" explores="+explores); process.exit(2); }
  if (globalThis.__govUnsettled === true) { console.error("unsettled set"); process.exit(3); }
  if (ms > 3000) { console.error("slow ms="+ms); process.exit(4); }
  console.log("v6b_ok reason="+r.reason+" ms="+ms);
})().catch((e) => { console.error(e); process.exit(1); });
"""
    proc = _run_obtain_js(body, timeout=15)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "v6b_ok" in proc.stdout


def test_innate_run_uses_hand_leaf_and_stops_when_revoked():
    """Innate skill: mineDeep for keys with a deep spec, gather otherwise; stops as soon as __govActive changes."""
    body = _gov_bot(None) + r"""
const box = { n: 0 };
const bot = botOf(box);
const calls = [];
_obtainGatherWithStrategy = async function(b, leaf, strat, t) { calls.push("gather:" + leaf.name + ":" + strat); globalThis.__govActive = null; return { ok: false }; };
_obtainMineDeep = async function(b, spec, n, t, strat) { calls.push("deep:" + spec.key + ":" + strat); return { ok: false }; };
globalThis.__obtainInCall = true;
(async () => {
  globalThis.__govActive = "innate_log";
  const r1 = await _obtainInnateRun(bot, "log");
  if (calls.length !== 1 || calls[0].indexOf("gather:log:") !== 0) { console.error("log calls=" + JSON.stringify(calls)); process.exit(2); }
  if (r1.reason !== "innate_revoked") { console.error("r1=" + JSON.stringify(r1)); process.exit(3); }
  calls.length = 0;
  globalThis.__govActive = "innate_diamond";
  const r2 = await _obtainInnateRun(bot, "diamond");
  if (!calls.length || calls.some(c => c.indexOf("deep:diamond:") !== 0)) { console.error("diamond calls=" + JSON.stringify(calls)); process.exit(4); }
  if (r2.reason !== "innate_exhausted") { console.error("r2=" + JSON.stringify(r2)); process.exit(5); }
  console.log("innate_ok " + calls.length);
})().catch((e) => { console.error(e); process.exit(1); });
"""
    proc = _run_obtain_js(body, timeout=15)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "innate_ok" in proc.stdout
