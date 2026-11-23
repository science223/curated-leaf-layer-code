#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""obtain(bot, item, 1) real-MC unit tests. No LLM.

Ports: worker0 25590/3010, worker1 25591/3011. -Xmx1G.
Do not touch A/A 25610–25612 / 3110–3112. Kill only our PIDs.

Usage:
  python _cc0924/test_obtain_mc.py
  python _cc0924/test_obtain_mc.py --worker 0
"""
from __future__ import annotations

import fcntl
import json
import os
import signal
import socket
import statistics
import subprocess
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, REPO)

import test_skills_mc as SM  # noqa: E402
from voyager.zs_noise import ZS_SPAWN  # noqa: E402

JAVA = SM.JAVA
AA_MC = SM.AA_MC
AA_BR = SM.AA_BR
SEEDS = [20260902099, 20260902001, 20260902002]
# 09-26: OBTAIN_SEEDS="a,b,c" overrides (slac002 extra seeds); default unchanged.
if os.environ.get("OBTAIN_SEEDS"):
    SEEDS = [int(x) for x in os.environ["OBTAIN_SEEDS"].split(",") if x.strip()]
ITEMS = [
    dict(id="diamond_pickaxe", item="diamond_pickaxe", call='await obtain(bot, "diamond_pickaxe", 1);'),
    dict(id="golden_sword", item="golden_sword", call='await obtain(bot, "golden_sword", 1);'),
    dict(id="compass", item="compass", call='await obtain(bot, "compass", 1);'),
]
# 09-26 r3fix_4: OBTAIN_SCENES="diamond_pickaxe,compass" overrides; default all three.
if os.environ.get("OBTAIN_SCENES"):
    _wanted = [x.strip() for x in os.environ["OBTAIN_SCENES"].split(",") if x.strip()]
    _by_id = {s["id"]: s for s in ITEMS}
    _missing = [x for x in _wanted if x not in _by_id]
    if _missing:
        raise SystemExit(f"OBTAIN_SCENES unknown: {_missing}; known={[s['id'] for s in ITEMS]}")
    ITEMS = [_by_id[x] for x in _wanted]
HARNESS_CHECK_SEEDS = [20260902099, 20260902010]
HARNESS_CHECK_SCENES = [
    dict(id="harness_a", item="_none", call="await bot.waitForTicks(1);"),
    dict(id="harness_b", item="_none", call="await bot.waitForTicks(1);"),
]
SHORT_ITEMS = [
    dict(id="crafting_table", item="crafting_table", call='await obtain(bot, "crafting_table", 1);'),
    dict(id="wooden_pickaxe", item="wooden_pickaxe", call='await obtain(bot, "wooden_pickaxe", 1);'),
    dict(id="stone_pickaxe", item="stone_pickaxe", call='await obtain(bot, "stone_pickaxe", 1);'),
]
DEBUG_ITEMS = [
    dict(id="diamond_pickaxe", item="diamond_pickaxe", call='await obtain(bot, "diamond_pickaxe", 1);'),
]
VANILLA_MEDIAN = {
    "diamond_pickaxe": 17.0,
    "golden_sword": 19.5,
    "compass": 19.9,
}
CRITERION_LOCKED = {
    "written_at": "2026-09-25T before collecting this round's obtain MC results",
    "success_rate_min": 0.90,
    "limits": {k: v / 2.0 for k, v in VANILLA_MEDIAN.items()},
    "vanilla_median": dict(VANILLA_MEDIAN),
    "note": "median calls_needed <= A/A median / 2; success >= 90%",
}
MAX_CALLS = 12
REQUEST_TIMEOUT = 600  # 09-25 upstream Voyager default 600 s; obtain budget 540 s
RESULT_JSONL = os.path.join(HERE, "obtain_mc_runs_r3fix.jsonl")
RESULT_PATH = os.path.join(HERE, "obtain_mc_result_r3.json")
LOG_PATH = os.path.join(HERE, "obtain_mc_r3fix.log")
WORK_ROOT = os.path.join(HERE, "obtain_mc_work")
KEEP_ROOT = os.path.join(HERE, "obtain_mc_keep")
ZS_SEED = 20260902099
# This continuation: only 1 test server (25590/3010). Do not open 25591.
def _seed_at(i):
    return [SEEDS[i]] if i < len(SEEDS) else []


WORKERS = tuple(  # 09-26 slac002 hand_b: 5 extra-seed workers on ports 25630-25634 / 3130-3134 (ports only; obtain code unchanged)
    {"id": i, "mc": 25630 + i, "br": 3130 + i, "seeds": _seed_at(i)} for i in range(5)
)
TECH = (
    "oak_log", "spruce_log", "birch_log", "jungle_log", "acacia_log", "dark_oak_log", "mangrove_log",
    "oak_planks", "spruce_planks", "birch_planks", "stick", "crafting_table",
    "wooden_pickaxe", "cobblestone", "stone_pickaxe", "raw_iron", "iron_ingot",
    "furnace", "iron_pickaxe", "diamond", "raw_gold", "gold_ingot", "redstone",
    "diamond_pickaxe", "golden_sword", "compass",
)

os.environ.update(
    MC_HOST="127.0.0.1",
    RUNTIME_CALL_PROBE="1",
    PYTHONDONTWRITEBYTECODE="1",
    TOKENIZERS_PARALLELISM="false",
    TZ="UTC",
)
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(k, None)
os.environ["no_proxy"] = os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1"


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as fp:
        fp.write(line + "\n")


def append_jsonl(row):
    os.makedirs(HERE, exist_ok=True)
    with open(RESULT_JSONL, "a", encoding="utf-8") as fp:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
        fp.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        fp.flush()
        fcntl.flock(fp.fileno(), fcntl.LOCK_UN)


def load_jsonl():
    rows = []
    if not os.path.isfile(RESULT_JSONL):
        return rows
    with open(RESULT_JSONL, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def parse_obtain_contract(obs):
    """Prefer the last [contract] obtain line so nested craft/gather/smelt cannot look like the call result."""
    if obs is None:
        return None
    lines = [c for c in SM.chats(obs) if "[contract]" in c]
    if not lines:
        return None
    obtain_lines = [c for c in lines if "[contract] obtain " in c]
    raw = obtain_lines[-1] if obtain_lines else lines[-1]
    m = SM.CONTRACT_RE.search(raw)
    if not m:
        return {"raw": raw, "name": None, "ok": None, "reason": None}
    return {"raw": raw, "name": m.group(1), "ok": m.group(2) == "ok", "reason": m.group(3)}


def parse_phase_seconds(chats):
    for c in reversed(chats or []):
        s = str(c)
        if "[obtain] phase_seconds " in s:
            raw = s.split("[obtain] phase_seconds ", 1)[-1].strip()
            try:
                return json.loads(raw)
            except Exception:
                return None
    return None


def parse_strategies(chats):
    for c in reversed(chats or []):
        s = str(c)
        if "[obtain] strategies " in s:
            raw = s.split("[obtain] strategies ", 1)[-1].strip()
            try:
                return json.loads(raw)
            except Exception:
                return None
    return None


def tech_progress(before, after):
    n = 0
    for k in TECH:
        if int((after or {}).get(k, 0) or 0) > int((before or {}).get(k, 0) or 0):
            n += 1
    return n


def save_logs(work, tag):
    import shutil

    dst = os.path.join(KEEP_ROOT, tag)
    os.makedirs(dst, exist_ok=True)
    for name in ("minecraft.log",):
        src = os.path.join(work, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(dst, name))
    latest = os.path.join(work, "server", "logs", "latest.log")
    if os.path.isfile(latest):
        shutil.copy2(latest, os.path.join(dst, "server_latest.log"))
    mf = os.path.join(work, "mineflayer")
    if os.path.isdir(mf):
        shutil.copytree(mf, os.path.join(dst, "mineflayer"), dirs_exist_ok=True)
    log(f"kept logs in {dst}")


def hold_unpaused(env):
    """obtain() can run 240s. If /step starts while the world is frozen,
    vanilla kicks the bot at ~30s (disconnect.timeout) and the bridge
    restarts. Keep the test world ticking for the whole call."""
    try:
        env.unpause()
    except Exception as e:
        log(f"unpause: {e}")
    env.server_paused = False
    env.pause = lambda: False


class HarnessSpawnError(Exception):
    """Test script failed to place the bot on a standable spawn. Not an obtain call."""


JS_HARNESS_WORLD_SPAWN = """
async function __harnessWorldSpawn() {
    const start = bot.entity.position.floored();
    const isAir = (b) => !b || b.name === "air" || b.name === "cave_air" || b.name === "void_air" || b.boundingBox === "empty";
    const isSolid = (b) => !!(b && b.boundingBox === "block");
    let found = null;
    for (let dy = 0; dy <= 96; dy++) {
        const yy = start.y + dy;
        const below = bot.blockAt(new Vec3(start.x, yy - 1, start.z));
        const feet = bot.blockAt(new Vec3(start.x, yy, start.z));
        const head = bot.blockAt(new Vec3(start.x, yy + 1, start.z));
        if (isSolid(below) && isAir(feet) && isAir(head)) {
            found = { x: start.x, y: yy, z: start.z, below: below && below.name };
            break;
        }
    }
    const sp = found || { x: start.x, y: start.y, z: start.z, below: "?" };
    const msg = `[harness] world_spawn open=${start.x},${start.y},${start.z} standable=${sp.x},${sp.y},${sp.z} below=${sp.below}`;
    try { console.log(msg); } catch (err) {}
    try { await bot.chat(msg); } catch (err) {}
}
await __harnessWorldSpawn();
"""

JS_HARNESS_STANDABLE = """
async function __harnessStandable() {
    const p = bot.entity.position.floored();
    const below = bot.blockAt(p.offset(0, -1, 0));
    const feet = bot.blockAt(p);
    const head = bot.blockAt(p.offset(0, 1, 0));
    const isAir = (b) => !b || b.name === "air" || b.name === "cave_air" || b.name === "void_air" || b.boundingBox === "empty";
    const isSolid = (b) => !!(b && b.boundingBox === "block");
    const ok = isSolid(below) && isAir(feet) && isAir(head);
    const msg = `[harness] standable=${ok} pos=${p.x},${p.y},${p.z} below=${below && below.name} feet=${feet && feet.name} head=${head && head.name}`;
    try { console.log(msg); } catch (err) {}
    try { await bot.chat(msg); } catch (err) {}
}
await __harnessStandable();
"""


def js_empty_bag(x, y, z):
    return (
        f'await bot.chat("/tp @s {int(x)} {int(y)} {int(z)}"); await bot.waitForTicks(20);\n'
        "try { await bot.waitForChunksToLoad(); } catch (err) {}\n"
        "await bot.waitForTicks(20);\n"
        """
async function reallyClear() {
    for (let i = 0; i < 16; i++) {
        await bot.chat("/clear @s");
        await bot.waitForTicks(20);
        for (const it of bot.inventory.items().slice()) {
            try { await bot.tossStack(it); } catch (err) {}
        }
        if (bot.inventory.items().length === 0) return;
    }
    try {
        const slots = bot.inventory.slots || [];
        for (let i = 0; i < slots.length; i++) {
            if (slots[i]) bot.inventory.updateSlot(i, null);
        }
    } catch (err) {}
}
await reallyClear();
"""
        + SM.boot_code()
        + "\nbot._obtainGlobal = null; bot._obtainState = { atY: {}, heading: 0 };\n"
        "globalThis.__gatherMaxDistance = 32; globalThis.__gatherDirShift = 0;\n"
        "await bot.waitForTicks(10);"
    )


def js_start_check(x, y, z):
    return """
async function __harnessStartCheck() {
    const sx = %d, sy = %d, sz = %d;
    const p = bot.entity.position;
    const fl = p.floored();
    const dist = Math.hypot(p.x - sx, p.y - sy, p.z - sz);
    const invItems = bot.inventory.items();
    const inv = invItems.map((i) => i.name + ":" + i.count).join(",");
    const below = bot.blockAt(fl.offset(0, -1, 0));
    const feet = bot.blockAt(fl);
    const head = bot.blockAt(fl.offset(0, 1, 0));
    const isAir = (b) => !b || b.name === "air" || b.name === "cave_air" || b.name === "void_air" || b.boundingBox === "empty";
    const isSolid = (b) => !!(b && b.boundingBox === "block");
    const standable = isSolid(below) && isAir(feet) && isAir(head);
    const empty = invItems.length === 0;
    const near = dist <= 2.0;
    const ok = empty && near && standable;
    const msg = "[harness] start ok=" + ok + " empty=" + empty + " dist=" + dist.toFixed(2) + " standable=" + standable + " pos=" + p.x.toFixed(2) + "," + p.y.toFixed(2) + "," + p.z.toFixed(2) + " below=" + (below && below.name) + " inv=" + (inv || "-");
    try { console.log(msg); } catch (err) {}
    try { await bot.chat(msg); } catch (err) {}
}
await __harnessStartCheck();
""" % (int(x), int(y), int(z))


def harness_js_snippets():
    """Every JS fragment the harness sends to env.step, with dummy coords. For node --check."""
    dummy = js_empty_bag(-368, 73, -16)
    return [
        ("world_spawn", JS_HARNESS_WORLD_SPAWN),
        ("standable", JS_HARNESS_STANDABLE),
        ("empty_bag", dummy),
        ("start_check", js_start_check(-368, 73, -16)),
        ("wait_10", "await bot.waitForTicks(10);"),
        ("wait_20", "await bot.waitForTicks(20);"),
        ("wait_30", "await bot.waitForTicks(30);"),
        ("ping", 'await bot.chat("[obtain-harness] ping"); await bot.waitForTicks(40);'),
        ("boot", SM.boot_code()),
        ("obtain_diamond", 'await obtain(bot, "diamond_pickaxe", 1);'),
        ("obtain_sword", 'await obtain(bot, "golden_sword", 1);'),
        ("obtain_compass", 'await obtain(bot, "compass", 1);'),
        ("wait_1", "await bot.waitForTicks(1);"),
    ]


def capture_world_spawn(env, seed):
    """ZS_SPAWN is the official 099 spawn. Other seeds: this world's own surface spawn."""
    if int(seed) == ZS_SEED:
        log(f"[harness] seed={seed} spawn=ZS_SPAWN {ZS_SPAWN}")
        return dict(ZS_SPAWN)
    obs = env.step(JS_HARNESS_WORLD_SPAWN, programs="")
    chats = SM.chats(obs)
    line = next((c for c in chats if str(c).startswith("[harness] world_spawn")), None)
    st = SM.state(obs)
    pos = (st or {}).get("pos") or {}
    spawn = {
        "x": int(pos.get("x") if pos.get("x") is not None else 0),
        "y": int(pos.get("y") if pos.get("y") is not None else 64),
        "z": int(pos.get("z") if pos.get("z") is not None else 0),
    }
    if line and "standable=" in line:
        try:
            part = line.split("standable=", 1)[1].split(" ")[0]
            xs, ys, zs = part.split(",")
            spawn = {"x": int(float(xs)), "y": int(float(ys)), "z": int(float(zs))}
        except Exception as e:
            log(f"[harness] parse world_spawn failed {e} line={line}")
    log(f"[harness] seed={seed} world_spawn={spawn} line={line} pos={pos}")
    return spawn


def empty_bag(env, spawn=None):
    sp = dict(spawn or ZS_SPAWN)
    x, y, z = int(sp["x"]), int(sp["y"]), int(sp["z"])
    env.step(js_empty_bag(x, y, z), programs="")


def assert_standable(env, seed, scene_id, spawn=None):
    """Feet on a solid block, feet+head air. Fail the harness, not obtain."""
    obs = env.step(JS_HARNESS_STANDABLE, programs="")
    chats = SM.chats(obs)
    line = next((c for c in chats if str(c).startswith("[harness] standable=")), None)
    st = SM.state(obs)
    pos = (st or {}).get("pos")
    if not line or "standable=true" not in line:
        raise HarnessSpawnError(
            f"seed={seed} scene={scene_id} spawn={spawn or ZS_SPAWN} pos={pos} chats={chats[-8:]}"
        )
    return pos


def _pos_xyz(pos):
    if not pos:
        return None
    try:
        return (float(pos.get("x")), float(pos.get("y")), float(pos.get("z")))
    except Exception:
        return None


def _dist_to_spawn(pos, spawn):
    xyz = _pos_xyz(pos)
    if xyz is None:
        return 999.0
    return (
        (xyz[0] - float(spawn["x"])) ** 2
        + (xyz[1] - float(spawn["y"])) ** 2
        + (xyz[2] - float(spawn["z"])) ** 2
    ) ** 0.5


def probe_start(env, spawn):
    """Read empty-bag / distance / standable after empty_bag. Returns (ok, info)."""
    sp = dict(spawn)
    obs = env.step(js_start_check(sp["x"], sp["y"], sp["z"]), programs="")
    chats = SM.chats(obs)
    line = next((c for c in chats if str(c).startswith("[harness] start ")), None)
    st = SM.state(obs) or {}
    pos = st.get("pos") or {}
    inv = dict(st.get("inv") or {})
    standable = bool(line and "standable=true" in line)
    empty = not inv
    dist = _dist_to_spawn(pos, sp)
    if line:
        try:
            if "empty=true" in line:
                empty = True
            elif "empty=false" in line:
                empty = False
            if "standable=true" in line:
                standable = True
            elif "standable=false" in line:
                standable = False
            if "dist=" in line:
                dist = float(line.split("dist=", 1)[1].split(" ")[0])
        except Exception as e:
            log(f"[harness] parse start line failed {e} line={line}")
    near = dist <= 2.0
    ok = bool(empty and near and standable)
    info = {
        "ok": ok,
        "empty": empty,
        "near": near,
        "standable": standable,
        "dist": round(dist, 2),
        "pos": pos,
        "inv": inv,
        "line": line,
    }
    log(f"[harness] probe_start {info}")
    return ok, info


def prepare_start(env, seed, scene_id, spawn):
    """empty_bag + start assertions. Retry once. Raises HarnessSpawnError."""
    last = None
    for attempt in (1, 2):
        empty_bag(env, spawn=spawn)
        ok, info = probe_start(env, spawn)
        last = info
        if ok:
            return info
        log(f"[harness] start attempt {attempt} failed seed={seed} scene={scene_id} {info}")
    raise HarnessSpawnError(
        f"seed={seed} scene={scene_id} spawn={spawn} last={last}"
    )


def _harness_fail_row(seed, scene, spawn, err, start_info=None):
    info = start_info or {}
    row = {
        "seed": seed,
        "scene": scene["id"],
        "item": scene["item"],
        "call": scene.get("call"),
        "decision_calls": 0,
        "calls": [],
        "calls_needed": None,
        "success": False,
        "hurt": False,
        "died": False,
        "fail_reasons": ["harness_fail"],
        "elapsed_s_total": 0,
        "subgoals_advanced": [],
        "vanilla_median": VANILLA_MEDIAN.get(scene["id"]),
        "limit": CRITERION_LOCKED["limits"].get(scene["id"]),
        "max_calls": MAX_CALLS,
        "spawn": dict(spawn or {}),
        "start_inv": dict(info.get("inv") or {}),
        "start_pos": info.get("pos"),
        "fresh_world": True,
        "harness_fail": True,
        "harness_error": str(err),
    }
    append_jsonl({"event": "harness_fail", "seed": seed, "scene": scene["id"], "error": str(err)})
    append_jsonl({"event": "job_done", "row": row})
    return row


def run_job(env, programs, seed, scene, payload_hook=None, spawn=None):
    spawn = dict(spawn or ZS_SPAWN)
    try:
        start_info = prepare_start(env, seed, scene["id"], spawn)
    except HarnessSpawnError as e:
        log(f"HARNESS FAIL {e}")
        return _harness_fail_row(seed, scene, spawn, e)
    st_inv = dict(start_info.get("inv") or {})
    st_pos = start_info.get("pos")
    row = {
        "seed": seed,
        "scene": scene["id"],
        "item": scene["item"],
        "call": scene["call"],
        "decision_calls": 0,
        "calls": [],
        "calls_needed": None,
        "success": False,
        "hurt": False,
        "died": False,
        "fail_reasons": [],
        "elapsed_s_total": 0,
        "subgoals_advanced": [],
        "vanilla_median": VANILLA_MEDIAN.get(scene["id"]),
        "limit": CRITERION_LOCKED["limits"].get(scene["id"]),
        "max_calls": MAX_CALLS,
        "spawn": dict(spawn),
        "start_inv": st_inv,
        "start_pos": st_pos,
        "fresh_world": True,
    }
    append_jsonl({"event": "job_start", **{k: row[k] for k in ("seed", "scene")}})
    start_have = int((st_inv or {}).get(scene["item"], 0) or 0)
    for i in range(1, MAX_CALLS + 1):
        try:
            before = SM.state(env.step("await bot.waitForTicks(10);", programs=""))
        except Exception as e:
            row["fail_reasons"].append("bridge_timeout")
            row["calls"].append({"i": i, "error": f"{type(e).__name__}: {str(e)[:300]}", "hung": True})
            break
        health0 = before.get("health")
        t0 = time.time()
        obs = None
        err = None
        hung = False
        try:
            code = programs() if callable(programs) else programs
            obs = env.step(scene["call"], programs=code)
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:300]}"
            hung = "Timeout" in type(e).__name__ or "timeout" in str(e).lower()
            obs = None
        elapsed = round(time.time() - t0, 1)
        after = SM.state(obs) if obs is not None else {}
        have = int((after.get("inv") or {}).get(scene["item"], 0) or 0)
        health1 = after.get("health")
        died = health1 is not None and health1 <= 0
        hurt = health0 is not None and health1 is not None and health1 < health0
        c = parse_obtain_contract(obs)
        if (
            c
            and c.get("ok")
            and c.get("reason") == "done"
            and have < 1
            and scene.get("item")
        ):
            c = dict(c)
            c["ok"] = False
            c["reason"] = "inv_mismatch"
        advanced = tech_progress(before.get("inv"), after.get("inv"))
        inv_after = dict((after.get("inv") or {}))
        reason = (c or {}).get("reason") if c else (err or "no_contract")
        chats_tail = SM.chats(obs)[-20:] if obs is not None else []
        phase_seconds = parse_phase_seconds(SM.chats(obs) if obs is not None else [])
        strategies = parse_strategies(SM.chats(obs) if obs is not None else [])
        call_row = {
            "i": i,
            "elapsed_s": elapsed,
            "have": have,
            "health_before": health0,
            "health_after": health1,
            "pos": after.get("pos"),
            "contract": c,
            "reason": reason,
            "error": err,
            "chats_tail": chats_tail,
            "hurt": hurt,
            "died": died,
            "hung": hung,
            "subgoals_advanced": advanced,
            "inv": inv_after,
            "inv_keys": sorted(inv_after.keys()),
            "phase_seconds": phase_seconds,
            "strategies": strategies,
        }
        row["calls"].append(call_row)
        row["decision_calls"] = i
        row["elapsed_s_total"] = round(row["elapsed_s_total"] + elapsed, 1)
        row["hurt"] = row["hurt"] or hurt
        row["died"] = row["died"] or died
        row["subgoals_advanced"].append(advanced)
        if not (c and c.get("ok")) and have <= start_have:
            row["fail_reasons"].append(reason)
        log(
            f"  {scene['id']} seed={seed} call {i}/{MAX_CALLS} have={have} "
            f"adv={advanced} contract={c} elapsed={elapsed}s health={health1} "
            f"reason={reason} died={died} phase={phase_seconds} strategies={strategies} inv={inv_after}"
        )
        append_jsonl(
            {
                "event": "call",
                "task": scene["id"],
                "seed": seed,
                "scene": scene["id"],
                "i": i,
                "have": have,
                "elapsed_s": elapsed,
                "contract": c,
                "reason": reason,
                "subgoals_advanced": advanced,
                "advanced": advanced,
                "inv": inv_after,
                "pos": after.get("pos"),
                "died": died,
                "hung": hung,
                "error": err,
                "chats_tail": call_row["chats_tail"],
                "phase_seconds": phase_seconds,
                "strategies": strategies,
            }
        )
        if have - start_have >= 1:
            row["success"] = True
            row["calls_needed"] = i
            break
        if hung:
            # as zeroshot.py _recover on StepExecutionTimeout: hard reset and RESTORE the pre-call bag
            row["fail_reasons"].append("hung")
            try:
                inv0 = dict((before or {}).get("inv") or {})
                env.reset(options={"mode": "hard", "wait_ticks": 20, "inventory": inv0})
                proc = getattr(getattr(env, "mineflayer", None), "process", None)
                SM.record_pid(getattr(proc, "pid", None), "mineflayer-node")
                hold_unpaused(env)
                env.step(SM.boot_code(), programs="")
                log(f"[harness] hung -> hard reset, restored pre-call bag {inv0}")
            except Exception as e:
                log(f"reset after hung failed: {e}")
            continue
        if died:
            # keepInventory=true, as in the pilot: the bot respawns with its bag; the harness does not clear it
            row["fail_reasons"].append("death")
    if not row["success"]:
        row["calls_needed"] = None
    append_jsonl({"event": "job_done", "row": row})
    return row


def _sum_phases(rows):
    keys = ("surface", "descend", "search_ore", "mine", "smelt", "craft", "path_fail", "escape")
    tot = {k: 0.0 for k in keys}
    n = 0
    for r in rows:
        for c in r.get("calls") or []:
            ph = c.get("phase_seconds") or {}
            if not ph:
                continue
            n += 1
            for k in keys:
                tot[k] += float(ph.get(k) or 0)
    if not n:
        return None
    return {k: round(v, 1) for k, v in tot.items()}


def gate_summary(runs):
    harness_n = sum(1 for r in runs if r.get("harness_fail"))
    complete = [
        r
        for r in runs
        if not r.get("harness_fail")
        and (r.get("success") or (r.get("calls") and len(r["calls"]) >= 1))
    ]
    gates = {"criterion_locked": dict(CRITERION_LOCKED), "partial": False}
    all_ok = True
    n_ok = sum(1 for r in complete if r.get("success"))
    rate = (n_ok / len(complete)) if complete else 0.0
    rate_ok = bool(complete) and rate >= 0.90
    gates["success_rate"] = {"value": rate, "n": len(complete), "ok": rate_ok, "criterion": ">=0.90"}
    if not rate_ok:
        all_ok = False
    for scene in ITEMS:
        sid = scene["id"]
        rows = [r for r in complete if r.get("scene") == sid]
        needed = [r["calls_needed"] for r in rows if r.get("success") and r.get("calls_needed")]
        med = statistics.median(needed) if needed else None
        limit = CRITERION_LOCKED["limits"][sid]
        n = len(rows)
        n_success = sum(1 for r in rows if r.get("success"))
        scene_rate = (n_success / n) if n else 0.0
        calls_ok = med is not None and med <= limit
        ok = n > 0 and scene_rate >= 0.90 and calls_ok
        gates[sid] = {
            "calls_needed_median": med,
            "calls_needed_limit": limit,
            "vanilla_median": VANILLA_MEDIAN[sid],
            "n_success": n_success,
            "n": n,
            "success_rate": scene_rate,
            "elapsed_s_median": statistics.median(
                [r["elapsed_s_total"] for r in rows if r.get("elapsed_s_total") is not None]
            )
            if rows
            else None,
            "subgoals_advanced_median": statistics.median(
                [statistics.mean(r["subgoals_advanced"]) for r in rows if r.get("subgoals_advanced")]
            )
            if any(r.get("subgoals_advanced") for r in rows)
            else None,
            "n_hurt": sum(1 for r in rows if r.get("hurt")),
            "n_died": sum(1 for r in rows if r.get("died")),
            "fail_reasons": [x for r in rows for x in (r.get("fail_reasons") or [])],
            "phase_seconds_sum": _sum_phases(rows),
            "ok": ok,
        }
        if not ok:
            all_ok = False
    expected = len(SEEDS) * len(ITEMS)
    gates["n_harness_fail"] = harness_n
    if harness_n:
        gates["partial"] = True
        all_ok = False
    if len(complete) < expected:
        gates["partial"] = True
        all_ok = False
    gates["all_ok"] = all_ok
    gates["met_prewritten"] = all_ok
    return gates


def save_payload(payload):
    with open(RESULT_PATH, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2, default=str)


def _wait_ports_free(mc_port, br_port, tag):
    for port, name in ((mc_port, "MC"), (br_port, "bridge")):
        for _ in range(24):
            if not SM.port_in_use(port):
                break
            time.sleep(0.5)
        log(f"{tag}: port {port} ({name}) {'IN_USE' if SM.port_in_use(port) else 'free'}")


def _close_scene(env, srv, work, tag):
    try:
        if env:
            env.close()
    except Exception as e:
        log(f"env.close: {e}")
    if srv is not None:
        try:
            srv.terminate()
            srv.wait(timeout=30)
        except Exception:
            try:
                srv.kill()
            except Exception:
                pass
    SM.kill_ours()
    SM.OUR_PIDS.clear()
    SM._CLEANED = False
    save_logs(work, tag)
    import shutil

    if os.path.isdir(work):
        shutil.rmtree(work, ignore_errors=True)


def run_one_scene(wid, mc_port, br_port, seed, scene, load_programs, harness_only=False):
    """One seed×scene = one fresh world. Start MC, check spawn, optionally obtain, then tear down."""
    import shutil
    from voyager.env import VoyagerEnv

    work = os.path.join(WORK_ROOT, f"w{wid}_{seed}_{scene['id']}")
    if os.path.exists(work):
        shutil.rmtree(work)
    os.makedirs(work, exist_ok=True)
    log(f"=== worker {wid} seed {seed} scene {scene['id']} work={work} ===")
    srv = SM.start_mc(work, mc_port, seed)
    SM.record_pid(srv.pid, "minecraft-java")
    env = None
    tag = f"r3fix_4/w{wid}_{seed}_{scene['id']}_{time.strftime('%H%M%S')}"
    try:
        env = VoyagerEnv(
            mc_port=mc_port,
            server_port=br_port,
            request_timeout=REQUEST_TIMEOUT,
            log_path=work,
            pause_verify=True,
        )
        env.reset(options={"mode": "hard", "wait_ticks": 20})
        proc = getattr(getattr(env, "mineflayer", None), "process", None)
        SM.record_pid(getattr(proc, "pid", None), "mineflayer-node")
        hold_unpaused(env)
        env.step(SM.boot_code(), programs="")
        time.sleep(3)
        try:
            ping = env.step(
                'await bot.chat("[obtain-harness] ping"); await bot.waitForTicks(40);',
                programs="",
            )
            log("ping chats=" + str(SM.chats(ping)[-5:]))
            log("ping pos=" + str((SM.state(ping) or {}).get("pos")))
        except Exception as e:
            log(f"ping failed: {type(e).__name__}: {e}")
        spawn = capture_world_spawn(env, seed)
        log(f"[harness] seed={seed} scene={scene['id']} using spawn={spawn}")
        if harness_only:
            try:
                info = prepare_start(env, seed, scene["id"], spawn)
                row = {
                    "seed": seed,
                    "scene": scene["id"],
                    "item": scene.get("item"),
                    "success": True,
                    "calls": [],
                    "calls_needed": 0,
                    "start_inv": dict(info.get("inv") or {}),
                    "start_pos": info.get("pos"),
                    "spawn": dict(spawn),
                    "fresh_world": True,
                    "harness_fail": False,
                    "harness_only": True,
                }
                append_jsonl({"event": "job_done", "row": row})
                log(f"HARNESS CHECK ok seed={seed} scene={scene['id']} pos={info.get('pos')}")
                return row
            except HarnessSpawnError as e:
                log(f"HARNESS FAIL {e}")
                return _harness_fail_row(seed, scene, spawn, e)
        try:
            return run_job(env, load_programs, seed, scene, spawn=spawn)
        except Exception as e:
            log(f"job crashed {type(e).__name__}: {str(e)[:240]}")
            row = {
                "seed": seed,
                "scene": scene["id"],
                "success": False,
                "fail_reasons": [f"crash:{type(e).__name__}"],
                "calls": [],
                "fresh_world": True,
            }
            append_jsonl({"event": "job_done", "row": row})
            return row
    finally:
        _close_scene(env, srv, work, tag)
        _wait_ports_free(mc_port, br_port, f"after {seed}/{scene['id']}")


def worker_loop(wid, mc_port, br_port, seeds, scenes=None, max_calls=None, harness_only=False):
    scenes = list(scenes or ITEMS)
    if max_calls is not None:
        global MAX_CALLS
        MAX_CALLS = int(max_calls)
    SM.MC_PORT = mc_port
    SM.BR_PORT = br_port
    SM.LOG_PATH = LOG_PATH
    SM.OUR_PIDS.clear()
    SM._CLEANED = False
    log(f"worker {wid} mc={mc_port} br={br_port} seeds={seeds} scenes={[s['id'] for s in scenes]} harness_only={harness_only}")
    if mc_port in AA_MC or br_port in AA_BR:
        raise SystemExit("refusing A/A ports")
    for port, name in ((mc_port, "MC"), (br_port, "bridge")):
        if SM.port_in_use(port):
            raise SystemExit(f"port {port} ({name}) already in use — do not kill others")
    from voyager.control_primitives import load_control_primitives
    from voyager.control_primitives_extra import load_extra_base_skills
    from voyager.v30_flags import V30_NAMED_ACTIONS

    def load_programs():
        extras = load_extra_base_skills(list(V30_NAMED_ACTIONS))
        return "\n\n".join(load_control_primitives()) + "\n\n" + "\n\n".join(extras)

    os.makedirs(WORK_ROOT, exist_ok=True)
    done = set()
    if not harness_only:
        for r in load_jsonl():
            row = r.get("row") or {}
            if r.get("event") == "job_done" and row.get("success") and not row.get("harness_fail"):
                done.add((row.get("seed"), row.get("scene")))
        log(f"skip already-ok {sorted(done)}")
    runs = []
    try:
        for seed in seeds:
            pending = [s for s in scenes if (seed, s["id"]) not in done]
            if not pending:
                log(f"seed {seed} already complete")
                continue
            for scene in pending:
                log(f"-- {scene['id']} seed={seed} --")
                row = run_one_scene(
                    wid, mc_port, br_port, seed, scene, load_programs, harness_only=harness_only
                )
                runs.append(row)
    finally:
        SM.kill_ours()
        _wait_ports_free(mc_port, br_port, f"worker {wid} exit")
    return runs


def merge_and_report():
    rows = []
    for rec in load_jsonl():
        if rec.get("event") == "job_done" and rec.get("row"):
            rows.append(rec["row"])
    # last row per (seed, scene)
    by = {}
    for r in rows:
        by[(r.get("seed"), r.get("scene"))] = r
    planned = {s["id"] for s in ITEMS}
    runs = [r for r in by.values() if r.get("scene") in planned]
    payload = {
        "seeds": SEEDS,
        "scenes": [s["id"] for s in ITEMS],
        "criterion_locked": CRITERION_LOCKED,
        "runs": runs,
        "gates": gate_summary(runs),
        "ended_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    save_payload(payload)
    log("GATES " + json.dumps(payload["gates"], ensure_ascii=False)[:2000])
    print(json.dumps(payload["gates"], ensure_ascii=False, indent=2))
    print("--- per-job wall clock ---")
    for r in sorted(runs, key=lambda x: (str(x.get("scene")), int(x.get("seed") or 0))):
        print(
            f"  {r.get('scene')} seed={r.get('seed')} success={r.get('success')} "
            f"calls_needed={r.get('calls_needed')} elapsed_s_total={r.get('elapsed_s_total')} "
            f"died={r.get('died')} fail={r.get('fail_reasons')}"
        )
        log(
            f"JOB {r.get('scene')} seed={r.get('seed')} success={r.get('success')} "
            f"calls_needed={r.get('calls_needed')} elapsed_s_total={r.get('elapsed_s_total')} "
            f"died={r.get('died')}"
        )
    return 0 if payload["gates"].get("all_ok") else 1


def run_harness_check(wid, mc_port, br_port):
    """099 and 010 × 2 games, 0 obtain calls. Print PASS/FAIL."""
    runs = worker_loop(
        wid, mc_port, br_port, HARNESS_CHECK_SEEDS,
        scenes=HARNESS_CHECK_SCENES, max_calls=0, harness_only=True,
    )
    fails = [r for r in runs if r.get("harness_fail") or not r.get("success")]
    expected = len(HARNESS_CHECK_SEEDS) * len(HARNESS_CHECK_SCENES)
    ok = len(runs) == expected and not fails
    for r in runs:
        log(
            f"harness_check seed={r.get('seed')} scene={r.get('scene')} "
            f"ok={not r.get('harness_fail')} start_pos={r.get('start_pos')} "
            f"start_inv={r.get('start_inv')} fresh={r.get('fresh_world')}"
        )
    print("HARNESS_CHECK", "PASS" if ok else "FAIL")
    print(json.dumps({
        "ok": ok,
        "n": len(runs),
        "expected": expected,
        "n_fail": len(fails),
        "rows": [
            {
                "seed": r.get("seed"),
                "scene": r.get("scene"),
                "harness_fail": r.get("harness_fail"),
                "start_pos": r.get("start_pos"),
                "start_inv": r.get("start_inv"),
                "fresh_world": r.get("fresh_world"),
                "error": r.get("harness_error"),
            }
            for r in runs
        ],
    }, ensure_ascii=False, indent=2, default=str))
    return 0 if ok else 1


def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", type=int, default=None)
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--merge", action="store_true", help="merge jsonl and print gates only")
    ap.add_argument(
        "--phase",
        choices=["debug", "short", "full", "harness_check"],
        default="full",
        help="debug=1 call diamond_pickaxe; short=table/wood/stone; full=3 seeds × 3 items; harness_check=099/010 × 2 start assertions",
    )
    args = ap.parse_args()
    if args.merge:
        return merge_and_report()
    if args.fresh and args.worker is None:
        for p in (RESULT_JSONL, RESULT_PATH, LOG_PATH):
            if os.path.isfile(p):
                os.remove(p)
        open(LOG_PATH, "w", encoding="utf-8").write("")
    phase_scenes = ITEMS
    phase_seeds = list(SEEDS)
    phase_max = None
    harness_only = False
    if args.phase == "debug":
        phase_scenes = DEBUG_ITEMS
        phase_seeds = [SEEDS[0]]
        phase_max = 1
    elif args.phase == "short":
        phase_scenes = SHORT_ITEMS
        phase_seeds = [SEEDS[0]]
        phase_max = 4
    elif args.phase == "harness_check":
        w = WORKERS[0 if args.worker is None else int(args.worker)]
        return run_harness_check(w["id"], w["mc"], w["br"])
    if args.worker is None:
        w = WORKERS[0]
        log(
            f"single server mc={w['mc']} br={w['br']} phase={args.phase} "
            f"seeds={phase_seeds} scenes={[s['id'] for s in phase_scenes]} jsonl={RESULT_JSONL}"
        )
        worker_loop(
            w["id"], w["mc"], w["br"], phase_seeds,
            scenes=phase_scenes, max_calls=phase_max, harness_only=harness_only,
        )
        if args.phase == "full":
            return merge_and_report()
        return 0
    w = WORKERS[int(args.worker)]
    worker_loop(
        w["id"], w["mc"], w["br"], w["seeds"] or phase_seeds,
        scenes=phase_scenes, max_calls=phase_max, harness_only=harness_only,
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log(traceback.format_exc())
        raise
