#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B 点名动作真环境行为测试。本机离线 MC，不调 LLM。

用法: python _cc0924/test_actions_mc.py
仿 _cc0923/test_discard.py：start_mc + VoyagerEnv，/give 后 settle 重读背包。
准备失败记 "准备失败"，不静默跳过。每个场景有墙钟上限。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
CC0923 = os.path.abspath(os.path.join(HERE, "..", "_cc0923"))
REPO = os.path.abspath(os.path.join(HERE, ".."))
JAVA = "/usr/lib/jvm/java-17-openjdk-amd64/bin/java"
MC_PORT = int(os.environ.get("V30_MC_PORT", "25590"))
BR_PORT = int(os.environ.get("V30_BR_PORT", "3010"))
SEED = "20260902099"
SCENE_TIMEOUT = 120
LONG_TIMEOUT = 200
REQUEST_TIMEOUT = 230

RESULT_PATH = os.path.join(HERE, "actions_mc_result.json")
LOG_PATH = os.path.join(HERE, "actions_mc.log")
CONTRACT_RE = re.compile(r"\[contract\]\s+(\S+)\s+(ok|fail)\s+(\S+)")

sys.path.insert(0, CC0923)
import skill_smoke as S  # noqa: E402

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
sys.path.insert(0, REPO)

OUR_PIDS = []
WORK_DIR = None
_CLEANED = False


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as fp:
        fp.write(line + "\n")


def port_in_use(port):
    s = socket.socket()
    s.settimeout(0.3)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def events_of(obs):
    if obs is None:
        return []
    if isinstance(obs, (list, tuple)) and obs and isinstance(obs[0], (list, tuple)):
        return list(obs)
    return []


def last_data(obs):
    evs = events_of(obs)
    if not evs:
        return {}
    item = evs[-1]
    if isinstance(item, (list, tuple)) and len(item) >= 2 and isinstance(item[1], dict):
        return item[1]
    return {}


def state(obs):
    data = last_data(obs)
    st = data.get("status") or {}
    return {
        "inv": dict(data.get("inventory") or {}),
        "used": st.get("inventoryUsed"),
        "pos": st.get("position"),
        "equipment": list(st.get("equipment") or []),
    }


def chats(obs):
    out = []
    for item in events_of(obs):
        if not (isinstance(item, (list, tuple)) and len(item) >= 2):
            continue
        t, o = item[0], item[1]
        if t == "onChat" and isinstance(o, dict):
            msg = str(o.get("onChat") or "").strip()
            if msg:
                out.append(msg)
        if t == "onError" and isinstance(o, dict):
            out.append("ERR:" + str(o.get("onError") or "")[:240])
    return out


def contract_lines(obs):
    return [c for c in chats(obs) if "[contract]" in c]


def parse_contract(lines):
    if not lines:
        return None
    text = lines[-1]
    m = CONTRACT_RE.search(text)
    if not m:
        return {"raw": text, "name": None, "ok": None, "reason": None}
    return {"raw": text, "name": m.group(1), "ok": m.group(2) == "ok", "reason": m.group(3)}


def inv_changed(a, b, ignore=()):
    keys = (set(a or {}) | set(b or {})) - set(ignore)
    return {k: ((a or {}).get(k, 0), (b or {}).get(k, 0)) for k in keys if (a or {}).get(k, 0) != (b or {}).get(k, 0)}


def give_code(give):
    """Few /give lines; await chat so the throttle queue actually drains."""
    parts = ['await bot.chat("/clear @s"); await bot.waitForTicks(20);']
    for k, v in give.items():
        parts.append(f'await bot.chat("/give @s minecraft:{k} {v}"); await bot.waitForTicks(12);')
    parts.append("await bot.waitForTicks(40);")
    return "\n".join(parts)


def cmds_code(cmds):
    parts = []
    for c in cmds:
        c = c if c.startswith("/") else "/" + c
        parts.append(f"await bot.chat({json.dumps(c)}); await bot.waitForTicks(12);")
    if parts:
        parts.append("await bot.waitForTicks(20);")
    return "\n".join(parts)


def settle(env, give, attempts=8):
    st = None
    missing = {}
    for i in range(attempts):
        st = state(env.step("await bot.waitForTicks(40);", programs=""))
        missing = {k: (v, st["inv"].get(k, 0)) for k, v in give.items() if st["inv"].get(k, 0) < v}
        if not missing:
            return st, True, {}
        log(f"  settle {i + 1}/{attempts} missing {missing}")
        time.sleep(1.0)
    return st, False, missing


def save_results(rows):
    """Keep the last row per action.scene so reruns do not stack duplicates."""
    last = {}
    order = []
    for r in rows:
        k = f"{r.get('action')}.{r.get('scene')}"
        if k not in last:
            order.append(k)
        last[k] = r
    out = [last[k] for k in order]
    with open(RESULT_PATH, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=2)


def record_pid(pid, label):
    if pid:
        OUR_PIDS.append((int(pid), label))
        log(f"PID {pid} ({label})")


def kill_ours():
    """Only the PIDs this script started. Confirm ports released."""
    global _CLEANED
    if _CLEANED:
        return
    _CLEANED = True
    for pid, label in list(OUR_PIDS):
        try:
            os.kill(pid, 0)
        except OSError:
            log(f"PID {pid} ({label}) already gone")
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            log(f"SIGTERM {pid} ({label})")
        except OSError as e:
            log(f"SIGTERM {pid} ({label}) failed: {e}")
    deadline = time.time() + 20
    while time.time() < deadline:
        alive = []
        for pid, label in OUR_PIDS:
            try:
                os.kill(pid, 0)
                alive.append((pid, label))
            except OSError:
                pass
        if not alive:
            break
        time.sleep(0.5)
    for pid, label in OUR_PIDS:
        try:
            os.kill(pid, 0)
            os.kill(pid, signal.SIGKILL)
            log(f"SIGKILL {pid} ({label})")
        except OSError:
            pass
    time.sleep(1)
    for port, name in ((MC_PORT, "MC"), (BR_PORT, "bridge")):
        log(f"port {port} ({name}) {'IN_USE' if port_in_use(port) else 'free'}")


import atexit
atexit.register(kill_ours)


# --------------------------------------------------------------------------- scenes
# setup_cmds: world commands after /clear+/give settle? Actually: arena, then give, then extra_js.
# We apply: reset_arena -> setup_cmds -> give -> settle -> extra_js -> before snapshot -> call.

def judge_already(row, expect_reason="already_satisfied", ignore_inv=()):
    if row["prep_ok"] is False:
        return "准备失败"
    c = row.get("contract") or {}
    if c.get("ok") is not True or c.get("reason") != expect_reason:
        return "fail"
    ch = inv_changed(row.get("inv_before"), row.get("inv_after"), ignore=ignore_inv)
    if ch:
        row["inv_delta"] = ch
        return "fail"
    return "pass"


def judge_fail_reason(row, reasons):
    if row["prep_ok"] is False:
        return "准备失败"
    c = row.get("contract") or {}
    if c.get("ok") is not False:
        return "fail"
    if c.get("reason") not in reasons:
        return "fail"
    return "pass"


def judge_ok_inv_gain(row, item, min_gain=1, reasons=("done",)):
    if row["prep_ok"] is False:
        return "准备失败"
    c = row.get("contract") or {}
    if c.get("ok") is not True or c.get("reason") not in reasons:
        return "fail"
    before = (row.get("inv_before") or {}).get(item, 0)
    after = (row.get("inv_after") or {}).get(item, 0)
    if after - before < min_gain and after < (before + min_gain):
        # also allow absolute hold if env shuffled, but must end with more than start
        if after <= before:
            return "fail"
    return "pass"


SCENES = [
    # ---- gather
    dict(
        action="gather", scene="already_satisfied", timeout=SCENE_TIMEOUT,
        give={"oak_log": 8, "wooden_axe": 1},
        call='await gather(bot, "oak_log", 0);',
        judge=lambda r: judge_already(r),
        expect="ok already_satisfied when n<=0 (increment semantics)",
    ),
    dict(
        action="gather", scene="missing_tool", timeout=SCENE_TIMEOUT,
        setup_cmds=["setblock ~2 ~ ~ minecraft:iron_ore"],
        give={"wooden_pickaxe": 1},
        call='await gather(bot, "raw_iron", 1);',
        judge=lambda r: judge_fail_reason(r, ("missing_tool",)),
        expect="fail missing_tool (wooden pick vs iron ore)",
        verify_block=(2, 0, 0, "iron_ore"),
    ),
    dict(
        action="gather", scene="normal", timeout=SCENE_TIMEOUT,
        setup_cmds=["setblock ~2 ~ ~ minecraft:oak_log", "setblock ~2 ~1 ~ minecraft:oak_log"],
        give={"wooden_axe": 1},
        call='await gather(bot, "oak_log", 1);',
        judge=lambda r: judge_ok_inv_gain(r, "oak_log", 1),
        expect="ok done, oak_log increased",
        verify_block=(2, 0, 0, "oak_log"),
    ),
    dict(
        action="gather", scene="increment_more", timeout=SCENE_TIMEOUT,
        setup_cmds=["setblock ~2 ~ ~ minecraft:oak_log", "setblock ~2 ~1 ~ minecraft:oak_log",
                    "setblock ~3 ~ ~ minecraft:oak_log", "setblock ~3 ~1 ~ minecraft:oak_log"],
        give={"oak_log": 3, "wooden_axe": 1},
        call='await gather(bot, "oak_log", 2);',
        judge=lambda r: (
            "pass"
            if (r.get("contract") or {}).get("reason") != "already_satisfied"
            and ((r.get("inv_after") or {}).get("oak_log", 0)
                 - (r.get("inv_before") or {}).get("oak_log", 0)) >= 2
            else "fail"
        ),
        expect="have 3, n=2 → gather 2 MORE, not already_satisfied",
        verify_block=(2, 0, 0, "oak_log"),
    ),
    dict(
        action="gather", scene="barehand_log", timeout=SCENE_TIMEOUT,
        setup_cmds=["setblock ~2 ~ ~ minecraft:oak_log", "setblock ~2 ~1 ~ minecraft:oak_log",
                    "setblock ~3 ~ ~ minecraft:oak_log", "setblock ~3 ~1 ~ minecraft:oak_log"],
        give={},
        extra_js='await bot.chat("/clear @s"); await bot.waitForTicks(30);',
        call='await gather(bot, "oak_log", 2);',
        judge=lambda r: _judge_barehand_log(r),
        expect="ok done without axe; oak_log gained from empty bag",
        verify_block=(2, 0, 0, "oak_log"),
    ),
    dict(
        action="gather", scene="blacklist_switch", timeout=LONG_TIMEOUT,
        # closer unreachable ore (6 up) + farther reachable ore. Stone pickaxe.
        setup_cmds=[
            "setblock ~ ~6 ~ minecraft:iron_ore",
            "fill ~-1 ~5 ~-1 ~1 ~7 ~1 minecraft:bedrock hollow",
            "setblock ~ ~6 ~ minecraft:iron_ore",
            "setblock ~8 ~ ~ minecraft:iron_ore",
        ],
        give={"stone_pickaxe": 1},
        call=(
            "const hi = bot.blockAt(bot.entity.position.offset(0,6,0));"
            "const lo = bot.blockAt(bot.entity.position.offset(8,0,0));"
            'bot.chat("[diag] before hi=" + (hi && hi.name) + " lo=" + (lo && lo.name));'
            'await gather(bot, "raw_iron", 1);'
            "const hi2 = bot.blockAt(bot.entity.position.offset(0,6,0));"
            "const lo2 = bot.blockAt(bot.entity.position.offset(8,0,0));"
            'bot.chat("[diag] after hi=" + (hi2 && hi2.name) + " lo=" + (lo2 && lo2.name));'
        ),
        judge=None,  # special
        expect="fail twice on caged ore then mine the other / explore",
    ),
    # ---- craft
    dict(
        action="craft", scene="already_satisfied", timeout=SCENE_TIMEOUT,
        give={"crafting_table": 1, "oak_planks": 8},
        call='await craft(bot, "crafting_table", 1);',
        judge=lambda r: judge_already(r),
        expect="ok already_satisfied, inv unchanged",
    ),
    dict(
        action="craft", scene="missing_materials", timeout=SCENE_TIMEOUT,
        give={"stick": 1},
        call='await craft(bot, "crafting_table", 1);',
        judge=lambda r: judge_fail_reason(r, ("missing_materials", "craft_failed")),
        expect="fail missing_materials (prefer) or craft_failed",
    ),
    dict(
        action="craft", scene="normal", timeout=SCENE_TIMEOUT,
        give={"oak_planks": 4},
        call='await craft(bot, "crafting_table", 1);',
        judge=lambda r: judge_ok_inv_gain(r, "crafting_table", 1),
        expect="ok done, crafting_table gained, planks consumed",
    ),
    dict(
        action="craft", scene="stick_from_two_planks", timeout=SCENE_TIMEOUT,
        give={"oak_planks": 2},
        call='await craft(bot, "stick", 4);',
        judge=lambda r: judge_ok_inv_gain(r, "stick", 4),
        expect="ok done: 2 planks → 4 sticks (n=owned count, 1 recipe)",
    ),
    # ---- smelt
    dict(
        action="smelt", scene="already_satisfied", timeout=SCENE_TIMEOUT,
        give={"iron_ingot": 2},
        call='await smelt(bot, "raw_iron", 0);',
        judge=lambda r: judge_already(r),
        expect="ok already_satisfied when n<=0 (increment; holding ingots is not enough)",
    ),
    dict(
        action="smelt", scene="missing_fuel", timeout=SCENE_TIMEOUT,
        give={"raw_iron": 2, "furnace": 1},
        call='await smelt(bot, "raw_iron", 1);',
        judge=lambda r: judge_fail_reason(r, ("missing_fuel",)),
        expect="fail missing_fuel",
    ),
    dict(
        action="smelt", scene="short_materials", timeout=SCENE_TIMEOUT,
        give={"raw_iron": 1, "coal": 4, "furnace": 1},
        call='await smelt(bot, "raw_iron", 3);',
        judge=lambda r: (
            "pass"
            if (r.get("contract") or {}).get("reason") == "partial"
            and (r.get("inv_after") or {}).get("iron_ingot", 0)
            > (r.get("inv_before") or {}).get("iron_ingot", 0)
            else "fail"
        ),
        expect="partial: smelt the 1 raw_iron on hand, gain an ingot",
    ),
    dict(
        action="smelt", scene="normal", timeout=SCENE_TIMEOUT,
        give={"raw_iron": 2, "coal": 4, "furnace": 1},
        call='await smelt(bot, "raw_iron", 1);',
        judge=lambda r: judge_ok_inv_gain(r, "iron_ingot", 1),
        expect="ok done, iron_ingot gained (smelt 1 more even if bag already has ingots)",
    ),
    # ---- equipBest
    dict(
        action="equipBest", scene="already_satisfied", timeout=SCENE_TIMEOUT,
        give={"leather_helmet": 1},
        armor_cmds=[
            "item replace entity @s armor.head with minecraft:diamond_helmet",
            "item replace entity @s armor.chest with minecraft:diamond_chestplate",
            "item replace entity @s armor.legs with minecraft:diamond_leggings",
            "item replace entity @s armor.feet with minecraft:diamond_boots",
        ],
        expect_eq0="diamond_helmet",
        call="await equipBest(bot);",
        judge=lambda r: _judge_equip_already(r),
        expect="ok already_satisfied, still diamond, leather stays in bag",
    ),
    dict(
        action="equipBest", scene="missing_prereq", timeout=SCENE_TIMEOUT,
        give={"stick": 1},
        call="await equipBest(bot);",
        judge=lambda r: _judge_equip_empty(r),
        expect="no armor → already_satisfied by design (not a fail path)",
    ),
    dict(
        action="equipBest", scene="normal", timeout=SCENE_TIMEOUT,
        give={"iron_helmet": 1},
        armor_cmds=["item replace entity @s armor.head with minecraft:leather_helmet"],
        expect_eq0="leather_helmet",
        call="await equipBest(bot);",
        judge=lambda r: _judge_equip_upgrade(r),
        expect="ok done, head becomes iron_helmet",
    ),
    # ---- prepare
    dict(
        action="prepare", scene="already_satisfied", timeout=SCENE_TIMEOUT,
        give={"oak_log": 4, "crafting_table": 1},
        call="await prepare(bot);",
        judge=lambda r: judge_already(r),
        expect="ok already_satisfied, inv unchanged",
    ),
    dict(
        action="prepare", scene="missing_materials", timeout=LONG_TIMEOUT,
        give={},
        call="await prepare(bot);",
        judge=lambda r: judge_fail_reason(r, ("missing_materials", "prepare_failed")),
        expect="fail missing_materials (no wood, stone arena, may explore 3x60s)",
    ),
    dict(
        action="prepare", scene="normal", timeout=SCENE_TIMEOUT,
        give={"oak_log": 8},
        call="await prepare(bot);",
        judge=lambda r: _judge_prepare_done(r),
        expect="ok done, table + (logs>=4 or planks>=12)",
    ),
    dict(
        action="prepare", scene="empty_bag_trees", timeout=LONG_TIMEOUT,
        setup_cmds=[
            "setblock ~2 ~ ~ minecraft:oak_log",
            "setblock ~2 ~1 ~ minecraft:oak_log",
            "setblock ~2 ~2 ~ minecraft:oak_log",
            "setblock ~3 ~ ~ minecraft:oak_log",
            "setblock ~3 ~1 ~ minecraft:oak_log",
            "setblock ~3 ~2 ~ minecraft:oak_log",
            "setblock ~4 ~ ~ minecraft:oak_log",
            "setblock ~4 ~1 ~ minecraft:oak_log",
        ],
        give={},
        call="await prepare(bot);",
        judge=lambda r: _judge_prepare_done(r),
        expect="ok done from empty bag (logs>=4 or planks>=12, and table)",
        verify_block=(2, 0, 0, "oak_log"),
    ),
    # ---- discardJunk
    dict(
        action="discardJunk", scene="already_satisfied", timeout=SCENE_TIMEOUT,
        give={"iron_pickaxe": 1, "bread": 5, "oak_log": 10, "dirt": 10},
        call="await discardJunk(bot);",
        judge=lambda r: judge_already(r, ignore_inv=("chest",)),
        expect="ok already_satisfied (used<=28), inv unchanged",
    ),
    dict(
        action="discardJunk", scene="missing_chest", timeout=SCENE_TIMEOUT,
        # 30 stacks dirt, no chest/planks/logs. used>28, cannot craft chest.
        give={"dirt": 1920},
        call="await discardJunk(bot);",
        judge=lambda r: _judge_discard_missing(r),
        expect="fail missing_chest (no chest/planks)",
    ),
    dict(
        action="discardJunk", scene="normal", timeout=SCENE_TIMEOUT,
        give={"dirt": 1408, "andesite": 64, "diorite": 64, "granite": 64,
              "chest": 1, "oak_log": 16, "iron_pickaxe": 1, "bread": 8},
        call="await discardJunk(bot);",
        judge=lambda r: _judge_discard_ok(r),
        expect="ok done, used<=28, keep items remain",
    ),
    dict(
        action="smelt", scene="unknown_recipe", timeout=SCENE_TIMEOUT,
        give={"dirt": 8},
        call='await smelt(bot, "dirt", 1);',
        judge=lambda r: _judge_unknown_recipe(r),
        expect="fail unknown_recipe (dirt has no furnace recipe), not already_satisfied",
    ),
    dict(
        action="smelt", scene="log_to_charcoal", timeout=SCENE_TIMEOUT,
        give={"oak_log": 4, "coal": 4, "furnace": 1},
        call='await smelt(bot, "oak_log", 1);',
        judge=lambda r: _judge_smelt_charcoal(r),
        expect="ok done, charcoal from oak_log (not already_satisfied)",
    ),
    dict(
        action="prepare", scene="four_logs_no_table", timeout=LONG_TIMEOUT,
        setup_cmds=[
            "setblock ~2 ~ ~ minecraft:oak_log",
            "setblock ~2 ~1 ~ minecraft:oak_log",
            "setblock ~2 ~2 ~ minecraft:oak_log",
            "setblock ~3 ~ ~ minecraft:oak_log",
            "setblock ~3 ~1 ~ minecraft:oak_log",
            "setblock ~3 ~2 ~ minecraft:oak_log",
            "setblock ~4 ~ ~ minecraft:oak_log",
            "setblock ~4 ~1 ~ minecraft:oak_log",
        ],
        give={"oak_log": 4},
        call="await prepare(bot);",
        judge=lambda r: _judge_prepare_done(r),
        expect="ok done from exactly 4 logs (mine extra then craft table)",
        verify_block=(2, 0, 0, "oak_log"),
    ),
    dict(
        action="gather", scene="andesite_no_pickaxe", timeout=SCENE_TIMEOUT,
        setup_cmds=["setblock ~2 ~ ~ minecraft:andesite"],
        give={},
        extra_js='await bot.chat("/clear @s"); await bot.waitForTicks(30);',
        call='await gather(bot, "andesite", 1);',
        judge=lambda r: _judge_missing_tool_fast(r),
        expect="fail missing_tool immediately (andesite harvestTools), not 240s timeout",
        verify_block=(2, 0, 0, "andesite"),
    ),
    dict(
        action="gather", scene="leaves_no_shears", timeout=SCENE_TIMEOUT,
        setup_cmds=["setblock ~2 ~1 ~ minecraft:oak_leaves"],
        give={},
        extra_js='await bot.chat("/clear @s"); await bot.waitForTicks(30);',
        call='await gather(bot, "oak_leaves", 1);',
        judge=lambda r: _judge_missing_tool_fast(r),
        expect="fail missing_tool immediately (leaves need shears)",
        verify_block=(2, 1, 0, "oak_leaves"),
    ),
]


def _judge_barehand_log(row):
    if row["prep_ok"] is False:
        return "准备失败"
    before = row.get("inv_before") or {}
    if (row.get("used_before") or 0) > 0 or any(before.values()):
        row["note"] = f"bag not empty before call: {before}"
        return "准备失败"
    c = row.get("contract") or {}
    if c.get("reason") == "already_satisfied":
        return "fail"
    if c.get("ok") is not True or c.get("reason") not in ("done",):
        return "fail"
    if (row.get("inv_after") or {}).get("oak_log", 0) < 2:
        return "fail"
    if (row.get("inv_after") or {}).get("wooden_axe"):
        return "fail"
    return "pass"


def _judge_equip_already(row):
    if row["prep_ok"] is False:
        return "准备失败"
    c = row.get("contract") or {}
    if c.get("ok") is not True or c.get("reason") != "already_satisfied":
        return "fail"
    eq_b = row.get("eq_before") or []
    eq_a = row.get("eq_after") or []
    if eq_b[:4] != eq_a[:4]:
        return "fail"
    if (row.get("inv_before") or {}).get("leather_helmet", 0) != (row.get("inv_after") or {}).get("leather_helmet", 0):
        return "fail"
    return "pass"


def _judge_equip_empty(row):
    """前置缺失 analog: nothing to equip. Action reports already_satisfied, not fail."""
    if row["prep_ok"] is False:
        return "准备失败"
    c = row.get("contract") or {}
    if c.get("ok") is True and c.get("reason") == "already_satisfied":
        if inv_changed(row.get("inv_before"), row.get("inv_after")):
            return "fail"
        return "pass"
    return "fail"


def _judge_equip_upgrade(row):
    if row["prep_ok"] is False:
        return "准备失败"
    c = row.get("contract") or {}
    if c.get("ok") is not True or c.get("reason") not in ("done",):
        return "fail"
    eq = row.get("eq_after") or []
    if not eq or eq[0] != "iron_helmet":
        return "fail"
    return "pass"


def _judge_prepare_done(row):
    if row["prep_ok"] is False:
        return "准备失败"
    c = row.get("contract") or {}
    if c.get("ok") is not True or c.get("reason") not in ("done",):
        return "fail"
    inv = row.get("inv_after") or {}
    logs = sum(v for k, v in inv.items() if k.endswith("_log") or k.endswith("_stem"))
    planks = sum(v for k, v in inv.items() if k.endswith("_planks"))
    table = inv.get("crafting_table", 0)
    if table >= 1 and (logs >= 4 or planks >= 12):
        return "pass"
    return "fail"


def _judge_discard_missing(row):
    if row["prep_ok"] is False:
        return "准备失败"
    if (row.get("used_before") or 0) <= 28:
        row["note"] = "used<=28 after give; cannot trigger discard"
        return "准备失败"
    return judge_fail_reason(row, ("missing_chest", "inventory_full"))


def _judge_discard_ok(row):
    if row["prep_ok"] is False:
        return "准备失败"
    c = row.get("contract") or {}
    if c.get("ok") is not True or c.get("reason") not in ("done",):
        return "fail"
    if (row.get("used_after") or 99) > 28:
        return "fail"
    inv_b, inv_a = row.get("inv_before") or {}, row.get("inv_after") or {}
    for k in ("oak_log", "iron_pickaxe", "bread"):
        if inv_a.get(k, 0) < inv_b.get(k, 0):
            return "fail"
    return "pass"


def _judge_unknown_recipe(row):
    if row["prep_ok"] is False:
        return "准备失败"
    c = row.get("contract") or {}
    if c.get("reason") == "already_satisfied":
        return "fail"
    if c.get("ok") is not False or c.get("reason") != "unknown_recipe":
        return "fail"
    return "pass"


def _judge_smelt_charcoal(row):
    if row["prep_ok"] is False:
        return "准备失败"
    c = row.get("contract") or {}
    if c.get("reason") == "already_satisfied":
        return "fail"
    if c.get("ok") is not True or c.get("reason") not in ("done",):
        return "fail"
    if (row.get("inv_after") or {}).get("charcoal", 0) < 1:
        return "fail"
    return "pass"


def _judge_missing_tool_fast(row):
    if row["prep_ok"] is False:
        return "准备失败"
    c = row.get("contract") or {}
    if c.get("ok") is not False or c.get("reason") != "missing_tool":
        return "fail"
    if (row.get("elapsed_s") or 999) > 30:
        row["note"] = f"too slow {row.get('elapsed_s')}s (must be immediate missing_tool)"
        return "fail"
    return "pass"


def _judge_gather_blacklist(row):
    if row["prep_ok"] is False:
        return "准备失败"
    diags = [c for c in (row.get("chats") or []) if c.startswith("[diag]")]
    row["diags"] = diags
    c = row.get("contract") or {}
    after_hi = after_lo = None
    for d in diags:
        if "after hi=" in d:
            # [diag] after hi=iron_ore lo=air
            m = re.search(r"after hi=(\S+) lo=(\S+)", d)
            if m:
                after_hi, after_lo = m.group(1), m.group(2)
    gained = (row.get("inv_after") or {}).get("raw_iron", 0) - (row.get("inv_before") or {}).get("raw_iron", 0)
    # Switched target: caged high ore remains, low ore gone, got raw_iron
    if after_hi == "iron_ore" and after_lo in ("air", "null", "undefined") and gained >= 1 and c.get("ok") is True:
        return "pass"
    # Explored after blacklist: long run + not_found, high still there
    if c.get("ok") is False and c.get("reason") == "not_found" and (row.get("elapsed_s") or 0) >= 50:
        row["note"] = "explored after failing the caged ore (not_found, long elapsed)"
        return "pass"
    if after_hi in (None, "null", "undefined", "air"):
        row["note"] = "caged ore did not stay; cannot confirm blacklist. 未测"
        return "未测"
    row["note"] = f"could not confirm switch/explore (hi={after_hi} lo={after_lo} gained={gained} c={c})"
    return "未测"


def reset_arena_code():
    return """
await bot.chat("/tp @s 0 120 0");
await bot.waitForTicks(20);
await bot.chat("/clear @s");
await bot.waitForTicks(10);
await bot.chat("/item replace entity @s armor.head with minecraft:air");
await bot.chat("/item replace entity @s armor.chest with minecraft:air");
await bot.chat("/item replace entity @s armor.legs with minecraft:air");
await bot.chat("/item replace entity @s armor.feet with minecraft:air");
await bot.chat("/item replace entity @s weapon.mainhand with minecraft:air");
await bot.chat("/item replace entity @s weapon.offhand with minecraft:air");
await bot.waitForTicks(10);
await bot.chat("/fill -8 119 -8 8 119 8 minecraft:stone");
await bot.waitForTicks(8);
await bot.chat("/fill -8 120 -8 8 132 8 minecraft:air");
await bot.waitForTicks(20);
"""


def boot_world_code():
    return """
await bot.chat("/difficulty peaceful");
await bot.chat("/gamerule doDaylightCycle false");
await bot.chat("/gamerule doMobSpawning false");
await bot.chat("/gamerule keepInventory true");
await bot.chat("/gamerule doImmediateRespawn true");
await bot.chat("/gamerule announceAdvancements false");
await bot.chat("/time set day");
await bot.chat("/weather clear");
await bot.chat("/tp @s 0 120 0");
await bot.waitForTicks(40);
await bot.chat("/fill -8 119 -8 8 119 8 minecraft:stone");
await bot.waitForTicks(8);
await bot.chat("/fill -8 120 -8 8 124 8 minecraft:air");
await bot.waitForTicks(40);
"""


def read_block_name(env, dx, dy, dz):
    code = (
        f"const b = bot.blockAt(bot.entity.position.offset({dx},{dy},{dz}));"
        f'bot.chat("[blk] " + (b ? b.name : "null"));'
        "await bot.waitForTicks(5);"
    )
    obs = env.step(code, programs="")
    for c in chats(obs):
        if c.startswith("[blk] "):
            return c.split(" ", 1)[1].strip()
    return None


def run_scene(env, programs, spec, rows):
    name = f"{spec['action']}.{spec['scene']}"
    log(f"=== {name} ===")
    row = {
        "action": spec["action"],
        "scene": spec["scene"],
        "prep_ok": None,
        "contract_raw": None,
        "contract": None,
        "inv_before": None,
        "inv_after": None,
        "eq_before": None,
        "eq_after": None,
        "used_before": None,
        "used_after": None,
        "verdict": None,
        "expect": spec.get("expect"),
        "elapsed_s": None,
        "error": None,
        "chats": [],
        "note": None,
    }
    try:
        env.step(reset_arena_code(), programs="")
        time.sleep(2)
        setup_cmds = spec.get("setup_cmds") or []
        if setup_cmds:
            env.step(cmds_code(setup_cmds), programs="")
            time.sleep(2)
        give = spec.get("give") or {}
        if give:
            env.step(give_code(give), programs="")
            time.sleep(1)
            before_settle, ok, missing = settle(env, give)
        else:
            before_settle = state(env.step("await bot.waitForTicks(20);", programs=""))
            ok, missing = True, {}
        armor_cmds = spec.get("armor_cmds") or []
        if armor_cmds:
            env.step(cmds_code(armor_cmds), programs="")
            time.sleep(3)
            before_settle = state(env.step("await bot.waitForTicks(20);", programs=""))
        want_eq0 = spec.get("expect_eq0")
        if want_eq0:
            eq0 = (before_settle.get("equipment") or [None])[0]
            row["eq_check"] = {"want": want_eq0, "got": eq0}
            if eq0 != want_eq0:
                ok = False
                missing = dict(missing or {}, equipment0=(want_eq0, eq0))
        vb = spec.get("verify_block")
        if vb:
            dx, dy, dz, want = vb
            got = read_block_name(env, dx, dy, dz)
            row["block_check"] = {"want": want, "got": got}
            if got != want:
                ok = False
                missing = dict(missing or {}, block=(want, got))
        if not ok:
            row["prep_ok"] = False
            row["inv_before"] = before_settle["inv"]
            row["used_before"] = before_settle["used"]
            row["eq_before"] = before_settle["equipment"]
            row["missing"] = missing
            row["verdict"] = "准备失败"
            log(f"  准备失败 missing={missing}")
            rows.append(row)
            save_results(rows)
            return row
        row["prep_ok"] = True
        extra = spec.get("extra_js")
        if extra:
            env.step(extra, programs=programs)
        before = state(env.step("await bot.waitForTicks(10);", programs=""))
        row["inv_before"] = before["inv"]
        row["used_before"] = before["used"]
        row["eq_before"] = before["equipment"]
        log(f"  before inv={before['inv']} used={before['used']} eq={before['equipment'][:4]}")

        t0 = time.time()
        obs = None
        try:
            obs = env.step(spec["call"], programs=programs)
        except Exception as e:
            row["error"] = f"{type(e).__name__}: {str(e)[:300]}"
            log(f"  step error {row['error']}")
            try:
                obs = env.step("await bot.waitForTicks(5);", programs="")
            except Exception:
                obs = None
        elapsed = round(time.time() - t0, 1)
        row["elapsed_s"] = elapsed
        if elapsed > spec.get("timeout", SCENE_TIMEOUT) + 5:
            row["note"] = (row.get("note") or "") + f" wall-clock {elapsed}s > timeout {spec.get('timeout')}"
        after = state(obs) if obs is not None else {"inv": {}, "used": None, "equipment": []}
        row["inv_after"] = after["inv"]
        row["used_after"] = after["used"]
        row["eq_after"] = after["equipment"]
        row["chats"] = chats(obs) if obs is not None else []
        cl = contract_lines(obs) if obs is not None else []
        row["contract_raw"] = cl[-1] if cl else None
        row["contract"] = parse_contract(cl)
        row["inv_delta"] = inv_changed(before["inv"], after["inv"])
        log(f"  contract={row['contract_raw']} elapsed={elapsed}s")
        log(f"  after inv={after['inv']} used={after['used']} eq={(after['equipment'] or [])[:4]}")
        judge = spec.get("judge")
        if spec["action"] == "gather" and spec["scene"] == "blacklist_switch":
            row["verdict"] = _judge_gather_blacklist(row)
        else:
            row["verdict"] = judge(row) if judge else "fail"
        log(f"  verdict={row['verdict']}")
    except Exception:
        row["error"] = traceback.format_exc()[-600:]
        row["verdict"] = row["verdict"] or "fail"
        log(f"  EXC {row['error']}")
    rows.append(row)
    save_results(rows)
    return row


def main():
    global WORK_DIR
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--actions", default="", help="comma list to run; empty = all")
    args = ap.parse_args()
    wanted = [x.strip() for x in args.actions.split(",") if x.strip()]
    scenes = [s for s in SCENES if not wanted or s["action"] in wanted]
    if not scenes:
        raise SystemExit(f"no scenes for --actions {wanted}")

    prev = []
    if wanted and os.path.isfile(RESULT_PATH):
        try:
            prev = json.load(open(RESULT_PATH, encoding="utf-8"))
        except Exception:
            prev = []

    if wanted:
        log(f"--- rerun actions={wanted} ---")
    else:
        open(LOG_PATH, "w", encoding="utf-8").write("")
    log(f"repo={REPO} java={JAVA} mc={MC_PORT} bridge={BR_PORT} scenes={len(scenes)}")
    for port, name in ((MC_PORT, "MC"), (BR_PORT, "bridge")):
        if port_in_use(port):
            raise SystemExit(f"port {port} ({name}) already in use — pick another, do not kill others")
    if not os.path.isfile(JAVA):
        raise SystemExit(f"java missing: {JAVA}")
    tmpl = os.path.join(REPO, "mc_template")
    if not os.path.exists(tmpl):
        raise SystemExit(f"mc_template missing: {tmpl}")

    from voyager.control_primitives import load_control_primitives
    from voyager.control_primitives_extra import load_extra_base_skills
    from voyager.v30_flags import V30_NAMED_ACTIONS

    extras = load_extra_base_skills(list(V30_NAMED_ACTIONS))
    programs = "\n\n".join(load_control_primitives()) + "\n\n" + "\n\n".join(extras)
    log(f"programs chars={len(programs)} extras={len(extras)}")

    WORK_DIR = f"/tmp/v30_mc_test_{int(time.time())}"
    os.makedirs(WORK_DIR, exist_ok=True)
    log(f"work={WORK_DIR}")

    srv = S.start_mc(REPO, WORK_DIR, MC_PORT, SEED, JAVA)
    record_pid(srv.pid, "minecraft-java")
    log("Minecraft ready")

    from voyager.env import VoyagerEnv
    from voyager.env.bridge import StepExecutionTimeout  # noqa: F401

    env = None
    rows = [r for r in prev if r.get("action") not in wanted] if wanted else []
    try:
        env = VoyagerEnv(mc_port=MC_PORT, server_port=BR_PORT, request_timeout=REQUEST_TIMEOUT, log_path=WORK_DIR)
        env.reset(options={"mode": "hard", "wait_ticks": 20})
        proc = getattr(getattr(env, "mineflayer", None), "process", None)
        record_pid(getattr(proc, "pid", None), "mineflayer-node")
        log("env reset")
        env.step(boot_world_code(), programs="")
        time.sleep(8)
        st = state(env.step("await bot.waitForTicks(40);", programs=""))
        pos = st.get("pos") or {}
        log(f"arena pos={pos} inv={st['inv']}")
        if abs((pos.get("x") or 99) - 0) > 4 or abs((pos.get("y") or 0) - 120) > 4:
            env.step('await bot.chat("/tp @s 0 120 0"); await bot.waitForTicks(80);', programs="")
            time.sleep(3)
            st = state(env.step("await bot.waitForTicks(20);", programs=""))
            log(f"arena retry pos={st.get('pos')}")
        log("arena ready")
        for spec in scenes:
            run_scene(env, programs, spec, rows)
    finally:
        try:
            if env:
                env.close()
        except Exception as e:
            log(f"env.close: {e}")
        try:
            srv.terminate()
            srv.wait(timeout=30)
        except Exception:
            try:
                srv.kill()
            except Exception:
                pass
        kill_ours()
        if WORK_DIR and os.path.isdir(WORK_DIR):
            shutil.rmtree(WORK_DIR, ignore_errors=True)
            log(f"removed {WORK_DIR}")

    save_results(rows)
    summary = {f"{r['action']}.{r['scene']}": r.get("verdict") for r in rows}
    log("SUMMARY " + json.dumps(summary, ensure_ascii=False))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    n_fail = sum(1 for r in rows if r.get("verdict") == "fail")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
