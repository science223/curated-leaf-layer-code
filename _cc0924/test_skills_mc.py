#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Skill-layer real-MC tests. No LLM. Ports 25590/3010 only. Do not touch A/A 25610–25612 / 3110–3112.

Usage: python _cc0924/test_skills_mc.py
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import time
import traceback
import uuid
import hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
JAVA = os.environ.get("JAVA17") or "/usr/lib/jvm/java-17-openjdk-amd64/bin/java"  # slac002: toolchain java17
MC_PORT = int(os.environ.get("V30_MC_PORT", "25590"))
BR_PORT = int(os.environ.get("V30_BR_PORT", "3010"))
AA_MC = {25610, 25611, 25612}
AA_BR = {3110, 3111, 3112}
SEEDS = [20260902099, 20260902001, 20260902002, 20260902003, 20260902004]
VANILLA_JSONL = (
    "/mnt/e/homework/research/voyager/_协作工作区/reports/v29_0922/"
    "zeroshot_results_96.jsonl"
)
REQUEST_TIMEOUT = 280
RESULT_PATH = os.path.join(HERE, "skills_mc_result.json")
LOG_PATH = os.path.join(HERE, "skills_mc.log")
WORK_ROOT = os.path.join(HERE, "skills_mc_work")
CONTRACT_RE = re.compile(r"\[contract\]\s+(\S+)\s+(ok|fail)\s+(\S+)")
BOT_NAME = "bot"

sys.path.insert(0, REPO)
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

OUR_PIDS = []
_CLEANED = False

SCENES = [
    dict(
        id="diamond",
        call='await mineOre(bot, "diamond", 3);',
        give={"iron_pickaxe": 4, "iron_shovel": 1, "cobblestone": 64, "cooked_beef": 8},
        item="diamond",
        need=3,
        subgoal=re.compile(r"mine\s+\d+\s+diamond", re.I),
    ),
    dict(
        id="gold",
        call='await mineOre(bot, "raw_gold", 2);',
        give={"iron_pickaxe": 4, "iron_shovel": 1, "cobblestone": 64, "cooked_beef": 8},
        item="raw_gold",
        need=2,
        subgoal=re.compile(r"mine\s+\d+\s+gold", re.I),
    ),
    dict(
        id="redstone",
        call='await mineOre(bot, "redstone", 1);',
        give={"iron_pickaxe": 4, "iron_shovel": 1, "cobblestone": 64, "cooked_beef": 8},
        item="redstone",
        need=1,
        subgoal=re.compile(r"mine\s+\d+\s+redstone", re.I),
    ),
    dict(
        id="iron",
        call='await mineOre(bot, "iron", 3);',
        give={"iron_pickaxe": 3, "stone_pickaxe": 2, "iron_shovel": 1, "cobblestone": 64, "cooked_beef": 8},
        item="raw_iron",
        need=3,
        subgoal=re.compile(r"mine\s+3\s+iron", re.I),
    ),
    dict(
        id="lava",
        call='await collectFluid(bot, "lava");',
        give={"bucket": 1, "iron_pickaxe": 2, "iron_shovel": 1, "cobblestone": 64, "cooked_beef": 8},
        item="lava_bucket",
        need=1,
        subgoal=re.compile(r"(collect\s+1\s+lava|find\s+lava|use\s+bucket.*lava)", re.I),
    ),
]


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


def offline_uuid(name):
    h = bytearray(hashlib.md5(f"OfflinePlayer:{name}".encode()).digest())
    h[6] = (h[6] & 0x0F) | 0x30
    h[8] = (h[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(h)))


def start_mc(work, mc_port, seed):
    """Own server. -Xmx1G as specified. Never reuse A/A dirs."""
    if mc_port in AA_MC:
        raise SystemExit(f"refusing A/A mc port {mc_port}")
    lane = os.path.join(work, "server")
    if os.path.exists(lane):
        shutil.rmtree(lane)
    shutil.copytree(os.path.join(REPO, "mc_template"), lane)
    props = os.path.join(lane, "server.properties")
    patch = {
        "server-port": str(mc_port),
        "server-ip": "127.0.0.1",
        "level-seed": str(seed),
        "enable-rcon": "false",
        "query.port": str(mc_port),
        "difficulty": "peaceful",
        "spawn-monsters": "false",
    }
    with open(props, encoding="utf-8") as fp:
        lines = [l for l in fp.read().splitlines() if l.split("=", 1)[0] not in patch]
    with open(props, "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines + [f"{k}={v}" for k, v in patch.items()]) + "\n")
    with open(os.path.join(lane, "ops.json"), "w", encoding="utf-8") as fp:
        json.dump(
            [{"uuid": offline_uuid(BOT_NAME), "name": BOT_NAME, "level": 4, "bypassesPlayerLimit": False}],
            fp,
        )
    log_path = os.path.join(work, "minecraft.log")
    fh = open(log_path, "ab", buffering=0)
    srv = subprocess.Popen(
        [JAVA, "-Duser.timezone=UTC", "-Xms1G", "-Xmx1G", "-jar", "fabric-server-launch.jar", "nogui"],
        cwd=lane,
        stdin=subprocess.DEVNULL,
        stdout=fh,
        stderr=fh,
        start_new_session=True,
        env={**os.environ, "TZ": "UTC"},
    )
    for _ in range(180):
        time.sleep(2)
        if srv.poll() is not None:
            raise SystemExit(f"Minecraft exited early, see {log_path}")
        with open(log_path, encoding="utf-8", errors="replace") as fp:
            if "Done (" in fp.read()[-24000:]:
                return srv
    srv.terminate()
    raise SystemExit(f"Minecraft not ready in 360s, see {log_path}")


def record_pid(pid, label):
    if pid:
        OUR_PIDS.append((int(pid), label))
        log(f"PID {pid} ({label})")


def kill_ours():
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
    for p in sorted(AA_MC | AA_BR):
        log(f"A/A port {p} {'still IN_USE' if port_in_use(p) else 'unexpectedly free'}")


import atexit

atexit.register(kill_ours)


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
        "health": st.get("health"),
        "food": st.get("food"),
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


def parse_contract(obs):
    lines = [c for c in chats(obs) if "[contract]" in c]
    if not lines:
        return None
    m = CONTRACT_RE.search(lines[-1])
    if not m:
        return {"raw": lines[-1], "name": None, "ok": None, "reason": None}
    return {"raw": lines[-1], "name": m.group(1), "ok": m.group(2) == "ok", "reason": m.group(3)}


def give_code(give):
    parts = ['await bot.chat("/clear @s"); await bot.waitForTicks(20);']
    for k, v in give.items():
        parts.append(f'await bot.chat("/give @s minecraft:{k} {v}"); await bot.waitForTicks(12);')
    parts.append("await bot.waitForTicks(30);")
    return "\n".join(parts)


def boot_code():
    return """
await bot.chat("/difficulty peaceful");
await bot.chat("/gamerule doDaylightCycle false");
await bot.chat("/gamerule doMobSpawning false");
await bot.chat("/gamerule keepInventory true");
await bot.chat("/gamerule doImmediateRespawn true");
await bot.chat("/gamerule announceAdvancements false");
await bot.chat("/time set day");
await bot.chat("/weather clear");
await bot.waitForTicks(40);
"""


def clear_skill_state():
    return """
bot._mineOreState = {};
bot._collectFluidState = {};
await bot.waitForTicks(5);
"""


def median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    return statistics.median(xs)


# Locked 2026-09-25 before this round's MC results (previous json had empty gates).
# vanilla medians from zeroshot_results_96.jsonl: diamond 7, lava 31, gold/iron/redstone < 3.
GATE_SUCCESS_RATE_MIN = 0.90
GATE_HARD_VANILLA_MEDIAN = 3.0
CRITERION_LOCKED = {
    "written_at": "2026-09-25T before collecting this round's MC results",
    "success_rate_min": GATE_SUCCESS_RATE_MIN,
    "hard_vanilla_median": GATE_HARD_VANILLA_MEDIAN,
    "hard_rule": "vanilla_median >= 3 → median(calls_needed) <= vanilla_median/2",
    "easy_rule": "vanilla_median < 3 → median(calls_needed) <= vanilla_median",
    "segments_hard": ["diamond", "lava"],
    "segments_easy": ["gold", "iron", "redstone"],
}


def calls_needed_limit(vanilla_median):
    """Return (limit, criterion_text). None limit if vanilla median missing."""
    if vanilla_median is None:
        return None, "missing vanilla median"
    vm = float(vanilla_median)
    if vm >= GATE_HARD_VANILLA_MEDIAN:
        lim = vm / 2.0
        return lim, (
            f"vanilla_median>={GATE_HARD_VANILLA_MEDIAN:g}: "
            f"median calls_needed <= vanilla_median/2 ({vm:g}/2={lim:g})"
        )
    return vm, (
        f"vanilla_median<{GATE_HARD_VANILLA_MEDIAN:g}: "
        f"median calls_needed <= vanilla_median ({vm:g})"
    )


def is_row_complete(row):
    if not row:
        return False
    if row.get("success"):
        return True
    if row.get("prep_ok") is False:
        return True
    calls = row.get("calls") or []
    if any(c.get("hung") for c in calls):
        return True
    max_c = int(row.get("max_calls") or 8)
    if len(calls) >= max_c:
        return True
    return False


def vanilla_subgoal_rounds():
    """Median vanilla rounds per subgoal from the 96-run jsonl. Failures count."""
    out = {}
    if not os.path.isfile(VANILLA_JSONL):
        return {"error": f"missing {VANILLA_JSONL}"}
    by = {s["id"]: [] for s in SCENES}
    n_rows = 0
    n_empty = 0
    with open(VANILLA_JSONL, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            n_rows += 1
            arm = row.get("arm") or row.get("preset") or ""
            if str(arm).upper() in {"EMPTY", "OLD"} or row.get("empty_library"):
                n_empty += 1
            results = row.get("subgoal_results") or []
            grouped = {}
            for sg in results:
                name = str(sg.get("subgoal") or "")
                steps = sg.get("steps")
                if steps is None:
                    steps = max(0, int(sg.get("end_round") or 0) - int(sg.get("start_round") or 0))
                for scene in SCENES:
                    if scene["subgoal"].search(name):
                        grouped.setdefault(scene["id"], 0)
                        grouped[scene["id"]] += int(steps or 0)
            for sid, total in grouped.items():
                by[sid].append(total)
    for sid, vals in by.items():
        half = None
        med = median(vals) if vals else None
        if med is not None:
            half = med / 2.0
        out[sid] = {
            "n": len(vals),
            "median": med,
            "mean": (sum(vals) / len(vals)) if vals else None,
            "half": half,
            "note": "50-round cap on the parent task can truncate long segments",
        }
    out["_meta"] = {"n_rows": n_rows, "n_empty_or_old": n_empty, "jsonl": VANILLA_JSONL}
    return out


def save_payload(payload):
    with open(RESULT_PATH, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)


def settle_give(env, give, attempts=8):
    st = None
    missing = {}
    for i in range(attempts):
        st = state(env.step("await bot.waitForTicks(30);", programs=""))
        missing = {k: (v, st["inv"].get(k, 0)) for k, v in give.items() if st["inv"].get(k, 0) < v}
        if not missing:
            return st, True, {}
        log(f"  settle {i + 1}/{attempts} missing {missing}")
        time.sleep(1.0)
    return st, False, missing


def run_scene(env, programs, seed, scene, vanilla, payload):
    max_calls = 8
    half = (vanilla.get(scene["id"]) or {}).get("half")
    med = (vanilla.get(scene["id"]) or {}).get("median")
    if med is not None:
        max_calls = max(8, int(math.ceil(med)) + 1)
        max_calls = min(max_calls, 18)
    env.step(clear_skill_state() + boot_code() + give_code(scene["give"]), programs="")
    st, ok, missing = settle_give(env, scene["give"])
    spawn = (st or {}).get("pos") or {}
    row = {
        "seed": seed,
        "scene": scene["id"],
        "call": scene["call"],
        "item": scene["item"],
        "need": scene["need"],
        "prep_ok": ok,
        "prep_missing": missing,
        "spawn": spawn,
        "calls": [],
        "calls_needed": None,
        "success": False,
        "hurt": False,
        "died": False,
        "fail_reasons": [],
        "elapsed_s_total": 0,
        "vanilla_median": med,
        "vanilla_half": half,
        "max_calls": max_calls,
        "first_call_success": False,
    }
    payload["runs"].append(row)
    save_payload(payload)
    if not ok:
        row["fail_reasons"].append("prep")
        save_payload(payload)
        return row
    start_have = int((st["inv"] or {}).get(scene["item"], 0))
    for i in range(1, max_calls + 1):
        try:
            before = state(env.step("await bot.waitForTicks(10);", programs=""))
        except Exception as e:
            log(f"  before-step failed {type(e).__name__}: {str(e)[:180]}")
            row["fail_reasons"].append("bridge_timeout")
            row["calls"].append({"i": i, "error": f"{type(e).__name__}: {str(e)[:300]}", "hung": True})
            save_payload(payload)
            break
        health0 = before.get("health")
        t0 = time.time()
        obs = None
        err = None
        hung = False
        try:
            obs = env.step(scene["call"], programs=programs)
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:300]}"
            hung = "Timeout" in type(e).__name__ or "timeout" in str(e).lower()
            obs = None
            if not hung:
                try:
                    obs = env.step("await bot.waitForTicks(5);", programs="")
                except Exception:
                    obs = None
        elapsed = round(time.time() - t0, 1)
        after = state(obs) if obs is not None else {}
        have = int((after.get("inv") or {}).get(scene["item"], 0))
        gained = have - start_have
        health1 = after.get("health")
        died = health1 is not None and health1 <= 0
        hurt = False
        if health0 is not None and health1 is not None and health1 < health0:
            hurt = True
        c = parse_contract(obs)
        call_row = {
            "i": i,
            "elapsed_s": elapsed,
            "have": have,
            "gained": gained,
            "health_before": health0,
            "health_after": health1,
            "pos": after.get("pos"),
            "contract": c,
            "error": err,
            "chats_tail": chats(obs)[-8:] if obs is not None else [],
            "hurt": hurt,
            "died": died,
        }
        row["calls"].append(call_row)
        row["elapsed_s_total"] = round(row["elapsed_s_total"] + elapsed, 1)
        row["hurt"] = row["hurt"] or hurt
        row["died"] = row["died"] or died
        reason = (c or {}).get("reason") if c else (err or "no_contract")
        if not (c and c.get("ok")):
            row["fail_reasons"].append(reason)
        ychats = [c for c in (call_row.get("chats_tail") or []) if "[mineOre] y=" in c]
        log(
            f"  {scene['id']} call {i}/{max_calls} have={have}/{scene['need']} "
            f"contract={c} elapsed={elapsed}s health={health1} died={died} "
            f"pos={after.get('pos')} {ychats[-1] if ychats else ''}"
        )
        save_payload(payload)
        if gained >= scene["need"] or have - start_have >= scene["need"]:
            row["success"] = True
            row["calls_needed"] = i
            row["first_call_success"] = i == 1
            break
        if died:
            row["fail_reasons"].append("death")
            try:
                env.step(give_code(scene["give"]), programs="")
            except Exception:
                hung = True
        if (c or {}).get("reason") == "missing_tool":
            try:
                env.step(give_code(scene["give"]), programs="")
            except Exception:
                hung = True
        if (c or {}).get("reason") == "missing_bucket":
            try:
                env.step(give_code(scene["give"]), programs="")
            except Exception:
                hung = True
        if hung:
            row["fail_reasons"].append("hung")
            save_payload(payload)
            log("  hung — hard reset env (do not wait on dead step)")
            try:
                env.reset(options={"mode": "hard", "wait_ticks": 20})
                proc = getattr(getattr(env, "mineflayer", None), "process", None)
                record_pid(getattr(proc, "pid", None), "mineflayer-node")
                env.step(boot_code(), programs="")
            except Exception as e:
                log(f"reset after hung failed: {e}")
            break
    if not row["success"]:
        row["calls_needed"] = None
    save_payload(payload)
    return row


def gate_summary(payload):
    """Per-scene gates. Criterion locked in CRITERION_LOCKED; do not edit to fit results."""
    runs = [r for r in (payload.get("runs") or []) if is_row_complete(r)]
    by = {}
    for r in runs:
        by.setdefault(r["scene"], []).append(r)
    gates = {
        "criterion_locked": dict(CRITERION_LOCKED),
        "partial": False,
    }
    all_ok = True
    n_success = sum(1 for r in runs if r.get("success"))
    n_first = sum(1 for r in runs if r.get("calls_needed") == 1)
    rate = (n_success / len(runs)) if runs else 0.0
    first_rate = (n_first / len(runs)) if runs else 0.0
    rate_ok = bool(runs) and rate >= GATE_SUCCESS_RATE_MIN
    gates["success_rate"] = {
        "value": rate,
        "n": len(runs),
        "ok": rate_ok,
        "criterion": f">={GATE_SUCCESS_RATE_MIN} (all segments)",
    }
    gates["first_call_success_rate"] = {
        "value": first_rate,
        "n_first": n_first,
        "n": len(runs),
        "note": "recorded; not a pass/fail gate",
    }
    if not rate_ok:
        all_ok = False
    scene_ids = [s["id"] for s in SCENES]
    for sid in scene_ids:
        rows = by.get(sid) or []
        needed = [r["calls_needed"] for r in rows if r.get("success") and r.get("calls_needed")]
        med = median(needed) if needed else None
        vmeta = (payload.get("vanilla") or {}).get(sid) or {}
        vmed = vmeta.get("median")
        half = vmeta.get("half")
        limit, crit = calls_needed_limit(vmed)
        n = len(rows)
        n_ok = sum(1 for r in rows if r.get("success"))
        n_first_s = sum(1 for r in rows if r.get("calls_needed") == 1)
        scene_rate = (n_ok / n) if n else 0.0
        rate_ok_s = n > 0 and scene_rate >= GATE_SUCCESS_RATE_MIN
        calls_ok = med is not None and limit is not None and med <= limit
        ok = rate_ok_s and calls_ok
        if not rows:
            ok = False
        fail_reasons = []
        for r in rows:
            fail_reasons.extend(r.get("fail_reasons") or [])
        elapsed = [r.get("elapsed_s_total") for r in rows if r.get("elapsed_s_total") is not None]
        gates[sid] = {
            "calls_needed_median": med,
            "calls_needed_limit": limit,
            "vanilla_median": vmed,
            "vanilla_half": half,
            "n_success": n_ok,
            "n": n,
            "success_rate": scene_rate,
            "success_rate_ok": rate_ok_s,
            "first_call_success_rate": (n_first_s / n) if n else 0.0,
            "elapsed_s_median": median(elapsed) if elapsed else None,
            "n_hurt": sum(1 for r in rows if r.get("hurt")),
            "n_died": sum(1 for r in rows if r.get("died")),
            "fail_reasons": fail_reasons,
            "ok": ok,
            "criterion": crit,
        }
        if not ok:
            all_ok = False
    expected = len(payload.get("seeds") or []) * len(payload.get("scenes") or [])
    if expected and len(runs) < expected:
        gates["partial"] = True
        all_ok = False
    gates["all_ok"] = all_ok
    return gates


def main():
    global _CLEANED
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="", help="comma seeds; empty = 5 dev seeds")
    ap.add_argument("--scenes", default="", help="comma scene ids")
    ap.add_argument("--fresh", action="store_true", help="ignore existing result file")
    args = ap.parse_args()
    seeds = [int(x) for x in args.seeds.split(",") if x.strip()] or list(SEEDS)
    wanted = [x.strip() for x in args.scenes.split(",") if x.strip()]
    scenes = [s for s in SCENES if not wanted or s["id"] in wanted]
    if 20260902099 not in seeds and not args.seeds:
        seeds = [20260902099] + [s for s in seeds if s != 20260902099]

    resume = (not args.fresh) and os.path.isfile(RESULT_PATH)
    if resume:
        with open(LOG_PATH, "a", encoding="utf-8") as fp:
            fp.write("\n")
        log("resume: appending to existing log/result")
    else:
        open(LOG_PATH, "w", encoding="utf-8").write("")
    log(f"repo={REPO} java={JAVA} mc={MC_PORT} bridge={BR_PORT} seeds={seeds} fresh={args.fresh}")
    if MC_PORT in AA_MC or BR_PORT in AA_BR:
        raise SystemExit("refusing A/A ports")
    for port, name in ((MC_PORT, "MC"), (BR_PORT, "bridge")):
        if port_in_use(port):
            raise SystemExit(f"port {port} ({name}) already in use — do not kill others")
    for p in sorted(AA_MC | AA_BR):
        log(f"A/A port {p} {'IN_USE (leave it)' if port_in_use(p) else 'free'}")
    if not os.path.isfile(JAVA):
        raise SystemExit(f"java missing: {JAVA}")
    tmpl = os.path.join(REPO, "mc_template")
    if not os.path.exists(tmpl):
        raise SystemExit(f"mc_template missing: {tmpl}")

    from voyager.control_primitives import load_control_primitives
    from voyager.control_primitives_extra import load_extra_base_skills
    from voyager.v30_flags import V30_NAMED_ACTIONS
    from voyager.knowledge_arm import write_knowledge_arm_text

    write_knowledge_arm_text()
    extras = load_extra_base_skills(list(V30_NAMED_ACTIONS))
    programs = "\n\n".join(load_control_primitives()) + "\n\n" + "\n\n".join(extras)
    vanilla = vanilla_subgoal_rounds()
    prev_runs = []
    dropped_incomplete = []
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if resume:
        try:
            with open(RESULT_PATH, encoding="utf-8") as fp:
                old = json.load(fp)
            for r in old.get("runs") or []:
                if is_row_complete(r):
                    prev_runs.append(r)
                else:
                    dropped_incomplete.append(
                        {"seed": r.get("seed"), "scene": r.get("scene"), "n_calls": len(r.get("calls") or [])}
                    )
            if old.get("started_at"):
                started_at = old["started_at"]
            log(
                f"resume kept {len(prev_runs)} complete rows, "
                f"drop incomplete {dropped_incomplete}"
            )
        except Exception as e:
            log(f"resume load failed ({e}); starting empty")
            prev_runs = []
            dropped_incomplete = []
    done_keys = {(r["seed"], r["scene"]) for r in prev_runs}
    payload = {
        "seeds": seeds,
        "scenes": [s["id"] for s in scenes],
        "vanilla": vanilla,
        "runs": list(prev_runs),
        "gates": {},
        "criterion_locked": dict(CRITERION_LOCKED),
        "started_at": started_at,
        "dropped_incomplete": dropped_incomplete,
    }
    payload["gates"] = gate_summary(payload)
    save_payload(payload)
    log("vanilla medians " + json.dumps({k: v for k, v in vanilla.items() if k != "_meta" and not isinstance(v, list)}, default=str)[:800])
    log("CRITERION_LOCKED " + json.dumps(CRITERION_LOCKED, ensure_ascii=False))

    os.makedirs(WORK_ROOT, exist_ok=True)
    from voyager.env import VoyagerEnv

    try:
        for seed in seeds:
            pending = [s for s in scenes if (seed, s["id"]) not in done_keys]
            if not pending:
                log(f"=== seed {seed} all {len(scenes)} scenes already complete — skip ===")
                continue
            _CLEANED = False
            OUR_PIDS.clear()
            work = os.path.join(WORK_ROOT, str(seed))
            if os.path.exists(work):
                shutil.rmtree(work)
            os.makedirs(work, exist_ok=True)
            log(f"=== seed {seed} work={work} pending={[s['id'] for s in pending]} ===")
            srv = start_mc(work, MC_PORT, seed)
            record_pid(srv.pid, "minecraft-java")
            log("Minecraft ready")
            env = None
            try:
                env = VoyagerEnv(
                    mc_port=MC_PORT,
                    server_port=BR_PORT,
                    request_timeout=REQUEST_TIMEOUT,
                    log_path=work,
                )
                env.reset(options={"mode": "hard", "wait_ticks": 20})
                proc = getattr(getattr(env, "mineflayer", None), "process", None)
                record_pid(getattr(proc, "pid", None), "mineflayer-node")
                env.step(boot_code(), programs="")
                time.sleep(3)
                st = state(env.step("await bot.waitForTicks(20);", programs=""))
                log(f"spawn pos={st.get('pos')} inv={st.get('inv')} health={st.get('health')}")
                for scene in pending:
                    log(f"-- {scene['id']} --")
                    try:
                        run_scene(env, programs, seed, scene, vanilla, payload)
                    except Exception as e:
                        log(f"scene {scene['id']} crashed: {type(e).__name__}: {str(e)[:240]}")
                        try:
                            env.reset(options={"mode": "hard", "wait_ticks": 20})
                            env.step(boot_code(), programs="")
                        except Exception as e2:
                            log(f"reset after crash failed: {e2}")
                            break
                    done_keys.add((seed, scene["id"]))
                    payload["gates"] = gate_summary(payload)
                    save_payload(payload)
                    log("GATES_PARTIAL " + json.dumps(payload["gates"], ensure_ascii=False)[:1200])
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
                OUR_PIDS.clear()
                _CLEANED = False
                for port, name in ((MC_PORT, "MC"), (BR_PORT, "bridge")):
                    for _ in range(20):
                        if not port_in_use(port):
                            break
                        time.sleep(0.5)
                    log(f"after seed {seed}: port {port} ({name}) {'IN_USE' if port_in_use(port) else 'free'}")
                if os.path.isdir(work):
                    shutil.rmtree(work, ignore_errors=True)
    finally:
        kill_ours()

    payload["gates"] = gate_summary(payload)
    payload["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save_payload(payload)
    log("GATES " + json.dumps(payload["gates"], ensure_ascii=False))
    print(json.dumps(payload["gates"], ensure_ascii=False, indent=2))
    return 0 if payload["gates"].get("all_ok") else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log(traceback.format_exc())
        raise
