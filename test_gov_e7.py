"""Unit tests for the E7 patch v2 (tools_0929/patch_e7.py): measurement fences and ledger conditions of the governed leaf.
Run inside the patched repo: python -m pytest -q test_gov_e7.py (needs node and the mineflayer node_modules)."""
import importlib
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from test_gov_leaf import _write_obtain_harness  # noqa: E402

BOT = r"""
function botOf(box) {
  const handlers = {};
  const bot = {
    inventory: { items: () => Object.keys(box.inv).filter((k) => box.inv[k] > 0)
      .map((k) => ({name: k, count: box.inv[k], type: mcData.itemsByName[k] ? mcData.itemsByName[k].id : 999})) },
    chat() {},
    entity: { id: 1, position: { x:0, y:64, z:0 } },
    entities: {},
    health: 20,
    once(ev, fn) { handlers[ev] = handlers[ev] || []; handlers[ev].push(fn); },
    on(ev, fn) { handlers[ev] = handlers[ev] || []; handlers[ev].push(fn); },
    removeListener(ev, fn) { handlers[ev] = (handlers[ev] || []).filter((f) => f !== fn); },
    off(ev, fn) { handlers[ev] = (handlers[ev] || []).filter((f) => f !== fn); },
    emit(ev, ...a) { for (const f of (handlers[ev] || []).slice()) f(...a); },
    count(ev) { return (handlers[ev] || []).length; },
    pathfinder: { setGoal(){}, stop(){}, isMoving(){ return false; } },
    stopDigging() {},
    collectBlock: { targets: { clear() {} }, cancelTask() { return Promise.resolve(); } },
    findBlock() { return null; },
    findBlocks() { return []; },
    blockAt() { return null; },
    waitForTicks() { return Promise.resolve(); },
  };
  return bot;
}
for (const n of ["oak_planks", "iron_ingot", "raw_iron"]) { if (!mcData.itemsByName[n]) add(n, 50 + Object.keys(mcData.itemsByName).length); }
const box = { inv: { oak_log: 0 } };
const bot = botOf(box);
let primHit = 0;
_obtainPrimLeaf = async function() { primHit += 1; return {ok:false, reason:"no_progress"}; };
globalThis.__leafSource = "gov";
globalThis.__govNonce = "nx";
globalThis.__govHangMs = 400;
globalThis.__obtainInCall = true;
globalThis.__primMinStartMs = 0; globalThis.__primExploreMarginMs = 0;
globalThis.__obtainDeadline = Date.now() + 60000;
const LEAF = {kind:"gather", name:"log", n:1};
const lines = [];
const _log = console.log;
console.log = (...a) => { lines.push(a.join(" ")); _log(...a); };
function has(s) { return lines.some((l) => l.indexOf(s) >= 0); }
function fail(msg) { console.error(msg); process.exit(2); }
function oldEntity(id, name, count) { const e = { id: id, name: "item", _n: count, getDroppedItem() { return { name: name, count: this._n }; } }; bot.entities[id] = e; return e; }
"""


def run_js(body, timeout=30):
    tmp = _write_obtain_harness(BOT + body)
    env = dict(os.environ)
    env["NODE_PATH"] = os.path.join(ROOT, "voyager", "env", "mineflayer", "node_modules")
    proc = subprocess.run(["node", tmp], cwd=ROOT, capture_output=True, text=True, env=env, timeout=timeout)
    os.unlink(tmp)
    assert proc.returncode == 0, proc.stderr[-2000:] + proc.stdout[-2000:]
    return proc.stdout


TWO = r"""
globalThis.__govLeaves = { log: [
  {id:"gov_log_0", fn:"gov_log_0__a", code_hash:"h0", target_n:1},
  {id:"gov_log_1", fn:"gov_log_1__b", code_hash:"h1", target_n:5},
]};
let secondRuns = 0;
globalThis.gov_log_1__b = async function(bot) { secondRuns += 1; box.inv.oak_log += 1; };
globalThis.__govOnline = false;
"""


def test_barrier_credits_a_delayed_pickup():
    out = run_js(TWO + r"""
globalThis.gov_log_0__a = async function(bot) { setTimeout(() => { box.inv.oak_log += 1; }, 150); };
globalThis.__govQuiesceMs = 300; globalThis.__govQuiesceCapMs = 3000;
(async () => {
  const r = await _obtainGovLeaf(bot, LEAF, () => false);
  if (!r.ok || secondRuns !== 0 || !has("delivered_by=candidate:gov_log_0")) fail("not credited to gov_log_0");
  console.log("e7_barrier_ok");
})().catch((e) => { console.error(e); process.exit(1); });
""")
    assert "e7_barrier_ok" in out


def test_without_barrier_the_delayed_pickup_is_misattributed():
    out = run_js(TWO + r"""
globalThis.gov_log_0__a = async function(bot) { setTimeout(() => { box.inv.oak_log += 1; }, 150); };
(async () => {
  await _obtainGovLeaf(bot, LEAF, () => false);
  if (secondRuns !== 1 || has("delivered_by=candidate:gov_log_0") || !has("try gov_log_0 item=log ok=false")) fail("released behaviour changed");
  console.log("e7_nobarrier_ok");
})().catch((e) => { console.error(e); process.exit(1); });
""")
    assert "e7_nobarrier_ok" in out


@pytest.mark.parametrize("fence,credited", [(False, True), (True, False)])
def test_late_fence_is_off_by_default(fence, credited):
    out = run_js(TWO + r"""
const old = oldEntity(7, "oak_log", 2);
globalThis.gov_log_0__a = async function(bot) { bot.emit("playerCollect", bot.entity, old); box.inv.oak_log += 2; };
globalThis.__govLateFence = FENCE;
(async () => {
  await _obtainGovLeaf(bot, LEAF, () => false);
  console.log("RESULT " + JSON.stringify({credited: has("delivered_by=candidate:gov_log_0"), late: has("late_pickup gov_log_0 item=log n=2")}));
})().catch((e) => { console.error(e); process.exit(1); });
""".replace("FENCE", "true" if fence else "false"))
    got = json.loads([l for l in out.splitlines() if l.startswith("RESULT ")][-1][7:])
    assert got == {"credited": credited, "late": fence}, got


@pytest.mark.parametrize("key,item,count,late", [("log", "oak_planks", 4, 1), ("raw_iron", "iron_ingot", 1, 1)])
def test_late_fence_uses_the_executors_categories(key, item, count, late):
    out = run_js(r"""
box.inv = { KEYITEM: 0 };
const old = oldEntity(9, "ITEM", COUNT);
globalThis.__govLeaves = { KEY: [ {id:"gov_k_0", fn:"gov_k_0__a", code_hash:"k0", target_n:1} ]};
globalThis.gov_k_0__a = async function(bot) { bot.emit("playerCollect", bot.entity, old); box.inv.ITEM = (box.inv.ITEM || 0) + COUNT; };
globalThis.__govOnline = false; globalThis.__govLateFence = true;
(async () => {
  await _obtainGovLeaf(bot, {kind:"gather", name:"KEY", n:1}, () => false);
  if (!has("late_pickup gov_k_0 item=KEY n=LATE") || !has("try gov_k_0 item=KEY ok=false delta=0")) fail("category not fenced");
  console.log("e7_cat_ok");
})().catch((e) => { console.error(e); process.exit(1); });
""".replace("KEYITEM", "raw_iron" if key == "raw_iron" else "oak_log").replace("KEY", key).replace("ITEM", item)
        .replace("COUNT", str(count)).replace("LATE", str(late)))
    assert "e7_cat_ok" in out


def test_a_merged_old_stack_is_fenced_only_up_to_its_starting_count():
    out = run_js(TWO + r"""
const old = oldEntity(7, "oak_log", 2);
globalThis.gov_log_0__a = async function(bot) { old._n = 3; bot.emit("playerCollect", bot.entity, old); box.inv.oak_log += 3; };
globalThis.__govLateFence = true;
(async () => {
  await _obtainGovLeaf(bot, LEAF, () => false);
  if (!has("late_pickup gov_log_0 item=log n=2") || !has("try gov_log_0 item=log ok=true delta=1")) fail("merged stack");
  console.log("e7_merge_ok");
})().catch((e) => { console.error(e); process.exit(1); });
""")
    assert "e7_merge_ok" in out


def test_a_death_during_the_barrier_is_a_death():
    out = run_js(TWO + r"""
globalThis.gov_log_0__a = async function(bot) { setTimeout(() => { box.inv.oak_log += 1; bot.emit("death"); }, 150); };
globalThis.__govOnline = true; globalThis.__govLedger = {}; globalThis.__govRetireK = 2;
globalThis.__govQuiesceMs = 500; globalThis.__govQuiesceCapMs = 3000;
(async () => {
  await _obtainGovLeaf(bot, LEAF, () => false);
  if (has("delivered_by=candidate:gov_log_0") || !has("try gov_log_0 item=log ok=false delta=0")) fail("death in the barrier credited");
  const rec = globalThis.__govLedger["h0"];
  if (!rec || rec.fail !== 1 || rec.lastReason !== "died") fail("not booked as a death " + JSON.stringify(rec));
  console.log("e7_death_ok");
})().catch((e) => { console.error(e); process.exit(1); });
""")
    assert "e7_death_ok" in out


def test_the_epoch_listener_is_always_removed():
    out = run_js(r"""
globalThis.__govLeaves = { log: [
  {id:"gov_log_0", fn:"gov_log_0__a", code_hash:"h0", target_n:5},
  {id:"gov_log_1", fn:"gov_log_1__b", code_hash:"h1", target_n:5},
  {id:"gov_log_2", fn:"gov_log_2__c", code_hash:"h2", target_n:5},
]};
globalThis.gov_log_0__a = async function(bot) { throw new Error("boom"); };               // rejects
globalThis.gov_log_1__b = async function(bot) { bot.emit("death"); };                   // dies
globalThis.gov_log_2__c = async function(bot) { await new Promise(() => {}); };          // never settles
globalThis.__govOnline = false; globalThis.__govLateFence = true; globalThis.__govQuiesceMs = 200;
globalThis.__obtainDeadline = Date.now() + 3000;
(async () => {
  await _obtainGovLeaf(bot, LEAF, () => false);
  if (bot.count("playerCollect") !== 0) fail("listeners left: " + bot.count("playerCollect"));
  console.log("e7_cleanup_ok");
})().catch((e) => { console.error(e); process.exit(1); });
""", timeout=60)
    assert "e7_cleanup_ok" in out


def test_barrier_stops_when_the_outer_call_aborts():
    out = run_js(r"""
globalThis.__govLeaves = { log: [ {id:"gov_log_0", fn:"gov_log_0__a", code_hash:"h0", target_n:1} ]};
let tick = null;
globalThis.gov_log_0__a = async function(bot) { tick = setInterval(() => { box.inv.oak_log += 1; }, 50); };
globalThis.__govOnline = false; globalThis.__govQuiesceMs = 300; globalThis.__govQuiesceCapMs = 6000;
setTimeout(() => { globalThis.__obtainAborted = true; }, 400);
(async () => {
  const t0 = Date.now();
  await _obtainGovLeaf(bot, LEAF, () => false);
  clearInterval(tick);
  if (Date.now() - t0 > 2500) fail("barrier kept waiting after abort: " + (Date.now() - t0));
  if (!has("unquiet gov_log_0")) fail("aborted barrier not reported as unquiet");
  console.log("e7_abort_ok");
})().catch((e) => { console.error(e); process.exit(1); });
""")
    assert "e7_abort_ok" in out


def test_death_listener_removed_on_an_exception_exit():
    out = run_js(r"""
globalThis.__govLeaves = { log: [ {id:"gov_log_0", fn:"gov_log_0__a", code_hash:"h0", target_n:1} ]};
globalThis.gov_log_0__a = async function(bot) {};
globalThis.__govOnline = false; globalThis.__govLateFence = true;
_obtainSettleRaw = async function() { throw new Error("injected settle failure"); };
(async () => {
  let threw = false;
  try { await _obtainGovLeaf(bot, LEAF, () => false); } catch (e) { threw = true; }
  if (!threw) fail("the injected failure did not propagate");
  if (bot.count("death") !== 0 || bot.count("playerCollect") !== 0) fail("listeners left death=" + bot.count("death") + " collect=" + bot.count("playerCollect"));
  console.log("e7_exc_cleanup_ok");
})().catch((e) => { console.error(e); process.exit(1); });
""")
    assert "e7_exc_cleanup_ok" in out


def test_unquiet_epoch_is_not_booked_and_tagged():
    out = run_js(r"""
globalThis.__govLeaves = { log: [ {id:"gov_log_0", fn:"gov_log_0__a", code_hash:"h0", target_n:1} ]};
let tick = null;
globalThis.gov_log_0__a = async function(bot) { tick = setInterval(() => { box.inv.oak_log += 1; }, 50); };
globalThis.__govOnline = true; globalThis.__govLedger = {}; globalThis.__govRetireK = 2;
globalThis.__govQuiesceMs = 300; globalThis.__govQuiesceCapMs = 500;
(async () => {
  await _obtainGovLeaf(bot, LEAF, () => false);
  clearInterval(tick);
  if (!has("unquiet gov_log_0")) fail("unquiet not reported");
  const rec = globalThis.__govLedger["h0"];
  if (rec && rec.calls) fail("unquiet epoch was booked: " + JSON.stringify(rec));
  if (has("delivered_by=candidate:gov_log_0") || !has("delivered_by=unquiet:gov_log_0")) fail("unquiet delivery tag");
  console.log("e7_unquiet_ok");
})().catch((e) => { console.error(e); process.exit(1); });
""")
    assert "e7_unquiet_ok" in out


def test_a_leftover_candidate_of_an_older_call_neither_books_nor_falls_back():
    # v4: the step of call 7 ended early; its candidate finishes after call 8 has started
    out = run_js(r"""
globalThis.__govLeaves = { log: [ {id:"gov_log_0", fn:"gov_log_0__a", code_hash:"h0", target_n:1} ]};
globalThis.__govOnline = true; globalThis.__govLedger = {}; globalThis.__govRetireK = 2;
globalThis.__obtainGen = 7;
globalThis.gov_log_0__a = async function(bot) {
  await new Promise((r) => setTimeout(r, 100)); globalThis.__obtainGen = 8; box.inv.oak_log += 1; };
(async () => {
  const r = await _obtainGovLeaf(bot, LEAF, () => false);
  if (r.ok || r.reason !== "aborted") fail("stale leaf did not stop: " + JSON.stringify(r));
  if (!has("stale gov_log_0")) fail("stale not reported");
  if (has("try gov_log_0")) fail("stale try reported as a try");
  const rec = globalThis.__govLedger["h0"];
  if (rec && rec.calls) fail("stale booking: " + JSON.stringify(globalThis.__govLedger));
  if (primHit !== 0) fail("stale leaf started the fallback");
  console.log("e7_stale_ok");
})().catch((e) => { console.error(e); process.exit(1); });
""")
    assert "e7_stale_ok" in out


def test_a_leftover_unsettled_candidate_does_not_mark_the_new_call():
    out = run_js(r"""
globalThis.__govLeaves = { log: [ {id:"gov_log_0", fn:"gov_log_0__a", code_hash:"h0", target_n:1} ]};
globalThis.__govOnline = true; globalThis.__govLedger = {}; globalThis.__govRetireK = 2;
globalThis.__obtainGen = 7;
globalThis.gov_log_0__a = function(bot) { globalThis.__obtainGen = 8; return new Promise(() => {}); };   // never settles
(async () => {
  const r = await _obtainGovLeaf(bot, LEAF, () => false);
  if (r.reason !== "aborted") fail("stale unsettled leaf: " + JSON.stringify(r));
  if (globalThis.__govUnsettled) fail("the new call was marked unsettled by a leftover");
  const rec = globalThis.__govLedger["h0"];
  if (rec && rec.calls) fail("stale booking: " + JSON.stringify(globalThis.__govLedger));
  console.log("e7_stale_unsettled_ok");
})().catch((e) => { console.error(e); process.exit(1); });
""", timeout=60)
    assert "e7_stale_unsettled_ok" in out


def test_same_generation_books_as_before():
    out = run_js(r"""
globalThis.__govLeaves = { log: [ {id:"gov_log_0", fn:"gov_log_0__a", code_hash:"h0", target_n:1} ]};
globalThis.__govOnline = true; globalThis.__govLedger = {}; globalThis.__govRetireK = 2;
globalThis.__obtainGen = 7;
globalThis.gov_log_0__a = async function(bot) { box.inv.oak_log += 1; };
(async () => {
  const r = await _obtainGovLeaf(bot, LEAF, () => false);
  const rec = globalThis.__govLedger["h0"];
  if (!r.ok || !rec || rec.calls !== 1 || has("stale")) fail("same-generation booking changed: " + JSON.stringify(rec));
  console.log("e7_samegen_ok");
})().catch((e) => { console.error(e); process.exit(1); });
""")
    assert "e7_samegen_ok" in out


@pytest.mark.parametrize("mode,calls0,retired", [
    ("full", 1, False), ("reorder_only", 1, False), ("retire_only", 2, True), ("off", 3, False),
])
def test_ledger_modes(mode, calls0, retired):
    out = run_js(r"""
globalThis.__govLeaves = { log: [
  {id:"gov_log_0", fn:"gov_log_0__a", code_hash:"h0", target_n:1},
  {id:"gov_log_1", fn:"gov_log_1__b", code_hash:"h1", target_n:1},
]};
globalThis.gov_log_0__a = async function(bot) {};
globalThis.gov_log_1__b = async function(bot) { box.inv.oak_log += 1; };
globalThis.__govOnline = true; globalThis.__govLedger = {}; globalThis.__govRetireK = 2;
globalThis.__govLedgerMode = "MODE";
(async () => {
  for (let i = 0; i < 3; i++) { box.inv.oak_log = 0; await _obtainGovLeaf(bot, LEAF, () => false); }
  const r0 = globalThis.__govLedger["h0"];
  console.log("RESULT " + JSON.stringify({calls: r0.calls, retired: !!r0.retired}));
})().catch((e) => { console.error(e); process.exit(1); });
""".replace("MODE", mode))
    got = json.loads([l for l in out.splitlines() if l.startswith("RESULT ")][-1][7:])
    assert got == {"calls": calls0, "retired": retired}, (mode, got)


def test_quarantine_is_per_world_and_archives_only_with_exposure():
    out = run_js(r"""
globalThis.__govLeaves = { log: [ {id:"gov_log_0", fn:"gov_log_0__a", code_hash:"h0", target_n:1} ]};
let runs = 0;
globalThis.gov_log_0__a = async function(bot) { runs += 1; };
globalThis.__govOnline = true; globalThis.__govLedger = {}; globalThis.__govRetireK = 2;
globalThis.__govRetireMode = "quarantine";
(async () => {
  globalThis.__govContext = "009";
  for (let i = 0; i < 3; i++) { box.inv.oak_log = 0; await _obtainGovLeaf(bot, LEAF, () => false); }
  if (runs !== 2 || !has("quarantine gov_log_0 ctx=009") || !has("skip_quarantined gov_log_0")) fail("no quarantine in 009");
  if (globalThis.__govLedger["h0"].retired) fail("retired after one world");
  globalThis.__govContext = "007";
  box.inv.oak_log = 0; await _obtainGovLeaf(bot, LEAF, () => false);
  if (runs !== 3) fail("not eligible again in 007");
  box.inv.oak_log = 0; await _obtainGovLeaf(bot, LEAF, () => false);
  globalThis.__govContext = "012";
  for (let i = 0; i < 2; i++) { box.inv.oak_log = 0; await _obtainGovLeaf(bot, LEAF, () => false); }
  const rec = globalThis.__govLedger["h0"];
  if (!rec.retired || !has("archived_after_quarantine=3")) fail("not archived after 3 worlds " + JSON.stringify(rec));
  console.log("e7_quarantine_ok");
})().catch((e) => { console.error(e); process.exit(1); });
""")
    assert "e7_quarantine_ok" in out


def test_strict_has_no_fallback_and_no_skip_na():
    out = run_js(r"""
globalThis.__govLeaves = { log: [ {id:"gov_log_0", fn:"gov_log_0__a", code_hash:"h0", target_n:1} ]};
let runs = 0;
globalThis.gov_log_0__a = async function(bot) { runs += 1; };
globalThis.__govOnline = false; globalThis.__govFallback = "off";
(async () => {
  box.inv.oak_log = 5;
  const r = await _obtainGovLeaf(bot, {kind:"gather", name:"log", n:3}, () => false);
  if (runs !== 1 || primHit !== 0 || r.ok || !has("strict_nofallback log")) fail("strict " + JSON.stringify(r));
  console.log("e7_strict_ok");
})().catch((e) => { console.error(e); process.exit(1); });
""")
    assert "e7_strict_ok" in out


def _harness(monkeypatch, **env):
    for k in ("GOV_QUIESCE_MS", "GOV_QUIESCE_CAP_MS", "GOV_LATE_FENCE", "GOV_LEDGER_MODE", "GOV_RETIRE_MODE", "GOV_FALLBACK"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("OBTAIN_LEAF_SOURCE", "gov")
    sys.path.insert(0, os.path.join(ROOT, "_cc0924"))
    import test_obtain_mc as H
    return importlib.reload(H)


def test_harness_defaults_are_the_released_ones(monkeypatch):
    H = _harness(monkeypatch)
    assert H.E7_ACTIVE is False
    assert "gov_quiesce_ms" not in H.result_identity() and "gov_ledger_mode" not in H.result_identity()
    pre = H.gov_preamble("n1", {}, True, 2, {}, ctx="20260902009")
    assert "__govQuiesceMs" not in pre and "__govContext" not in pre and "__govLateFence" not in pre
    assert "__govQuiesceMs" not in H.prim_preamble("n1")
    assert H.leaf_condition_name() == "gov_online0_k2" or H.leaf_condition_name().startswith("gov_online")


def test_harness_names_preamble_identity_and_counters(monkeypatch):
    H = _harness(monkeypatch, GOV_QUIESCE_MS="1500", GOV_QUIESCE_CAP_MS="6000", GOV_LATE_FENCE="1", GOV_LEDGER_MODE="off",
                 GOV_RETIRE_MODE="quarantine", GOV_FALLBACK="off")
    assert H.E7_ACTIVE is True
    name = H.leaf_condition_name()
    assert name.endswith("_q1500c6000_lf_lmoff_rmquarantine_strict"), name
    ident = H.result_identity()
    assert (ident["gov_quiesce_ms"], ident["gov_quiesce_cap_ms"], ident["gov_late_fence"], ident["gov_fallback"]) == (1500, 6000, True, "off")
    pre = H.gov_preamble("n1", {}, True, 2, {}, ctx="20260902009")
    for s in ('__govQuiesceMs = 1500', '__govQuiesceCapMs = 6000', '__govLateFence = true', '__govLedgerMode = "off"',
              '__govRetireMode = "quarantine"', '__govFallback = "off"', '__govContext = "20260902009"'):
        assert s in pre, s
    chats = ["[gov] n1 late_pickup gov_log_0 item=log n=2", "[gov] n1 unquiet gov_log_1 waited=4000",
             "[gov] n1 quarantine gov_log_2 ctx=009", "[gov] n1 skip_quarantined gov_log_2",
             "[gov] n1 strict_nofallback log", "[gov] n2 late_pickup gov_log_0 item=log n=5"]
    m = H.parse_leaf_metrics(chats, "n1")
    assert (m["gov_late_pickups"], m["gov_unquiet"], m["gov_quarantined"], m["gov_skip_quarantined"],
            m["gov_strict_nofallback"]) == (2, 1, 1, 1, 1), m
