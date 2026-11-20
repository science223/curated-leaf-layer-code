#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Execution tournament: one candidate × one seed = one fresh world.

Reuses v4 cancel / settle / death / equiv-count / delivery helpers in obtain.js.
Does not call obtain() and does not fall back to prim. No LLM.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import test_obtain_mc as TM  # noqa: E402
import test_skills_mc as SM  # noqa: E402
from gov_pool import POOL_KEYS, TOURNAMENT_SEEDS, load_pool  # noqa: E402

PRE_TOOLS = {
    "log": {},
    "cobblestone": {"wooden_pickaxe": 1},
    "coal": {"wooden_pickaxe": 1},
    "raw_iron": {"stone_pickaxe": 1},
    "raw_gold": {"iron_pickaxe": 1},
    "redstone": {"iron_pickaxe": 1},
    "diamond": {"iron_pickaxe": 1},
}
CAP_MS = 180000
OUT_JSONL = os.path.join(REPO, "_gov", "tournament_results.jsonl")
POOL_PATH = os.path.join(REPO, "_gov", "pool_full.json")
WORK_ROOT = os.path.join(HERE, "obtain_mc_work")

TOURNAMENT_HELPER = r"""
async function __govTournamentCall(bot, key, cand) {
    const nonce = String(globalThis.__govNonce || "");
    try { globalThis.__govUnsettled = false; } catch (e) {}
    try { globalThis.__obtainInCall = true; } catch (e) {}
    try { globalThis.__obtainAborted = false; } catch (e) {}
    try { globalThis.__obtainDeadline = Date.now() + 180000; } catch (e) {}
    try { if (typeof _obtainPendingInit === "function") _obtainPendingInit(); } catch (e) {}
    bot._obtainState = bot._obtainState || { atY: {}, heading: 0 };
    bot._obtainState.deliverSeq = { n: 0 };
    const before = _obtainEquivCount(bot, key);
    const t0 = Date.now();
    const capMs = _obtainRemainMs(180000);
    const latch = _obtainDeathLatch(bot);
    globalThis.__govActive = String(cand.id);
    let raw;
    try { raw = Promise.resolve(globalThis[cand.fn](bot)); }
    catch (e) { raw = Promise.resolve(); }
    try { _obtainTrackPending(raw); } catch (e) {}
    try {
        await _obtainRunCappedRaw(bot, raw, capMs, latch);
    } finally {
        try { globalThis.__govActive = null; } catch (e2) {}
    }
    const settled = await _obtainSettleRaw(bot, raw, latch);
    _obtainDeathUnlatch(bot, latch);
    const died = !!(latch && latch.died);
    const ms = Date.now() - t0;
    if (!settled) {
        _obtainMarkUnsettled(bot, nonce);
        _obtainChat(bot, "[contract] tournament fail gov_unsettled", { contract: true, force: true });
        _obtainGovPrint(bot, "[tournament] result " + JSON.stringify({
            ok: false, delta: 0, ms: ms, died: died, unsettled: true
        }));
        return;
    }
    if (died) {
        try { await _obtainWaitRespawn(bot, () => false); } catch (e) {}
        _obtainChat(bot, "[contract] tournament fail died", { contract: true, force: true });
        _obtainGovPrint(bot, "[tournament] result " + JSON.stringify({
            ok: false, delta: 0, ms: ms, died: true, unsettled: false
        }));
        return;
    }
    const after = _obtainEquivCount(bot, key);
    const delta = after - before;
    const ok = delta >= 1;
    _obtainGovPrint(bot, "[gov] " + nonce + " try " + cand.id + " item=" + key + " ok=" + ok + " delta=" + delta + " ms=" + ms);
    if (ok) {
        const seqRef = _obtainDeliverSeq(bot);
        seqRef.n += 1;
        _obtainLogDelivered(bot, "candidate:" + cand.id, nonce, seqRef.n, key, delta);
        _obtainChat(bot, "[contract] tournament ok done", { contract: true, force: true });
    } else {
        _obtainChat(bot, "[contract] tournament fail no_progress", { contract: true, force: true });
    }
    _obtainGovPrint(bot, "[tournament] result " + JSON.stringify({
        ok: ok, delta: delta, ms: ms, died: false, unsettled: false
    }));
}
"""


def log(msg):
    TM.log(msg)


def parse_tournament_result(obs):
    unsettled = False
    died = False
    ok = False
    delta = 0
    ms = None
    chats = SM.chats(obs) if obs is not None else []
    parsed = None
    for c in chats:
        s = str(c)
        if "[tournament] result " in s:
            raw = s.split("[tournament] result ", 1)[-1].strip()
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                continue
        if "[contract] tournament fail gov_unsettled" in s:
            unsettled = True
        if "[gov]" in s and s.rstrip().endswith(" unsettled"):
            unsettled = True
        if " tournament fail died" in s:
            died = True
        if "[contract] tournament ok " in s:
            ok = True
    if parsed:
        ok = bool(parsed.get("ok"))
        delta = int(parsed.get("delta") or 0)
        ms = parsed.get("ms")
        died = bool(parsed.get("died"))
        unsettled = bool(parsed.get("unsettled"))
    return {
        "success": bool(ok and delta >= 1 and not unsettled),
        "delta": delta,
        "ms": ms,
        "died": died,
        "unsettled": unsettled,
        "chats_tail": chats[-24:],
    }


def load_done(path):
    done = set()
    if not os.path.isfile(path):
        return done
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            key, h, seed = r.get("key"), r.get("code_hash"), r.get("seed")
            if key and h and seed is not None and "success" in r:
                done.add((str(key), str(h), int(seed)))
    return done


def append_result(path, row):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as fp:
        fp.write(json.dumps(row, ensure_ascii=False) + "\n")


def iter_jobs(pool, keys, seeds, max_cands):
    cands_by_key = pool.get("candidates") or pool
    jobs = []
    for key in keys:
        recs = list(cands_by_key.get(key) or [])
        if max_cands is not None:
            recs = recs[: int(max_cands)]
        for cand in recs:
            for seed in seeds:
                jobs.append((key, cand, int(seed)))
    return jobs


def shard_jobs(jobs, wid, n_workers):
    if n_workers <= 1:
        return jobs
    return [j for i, j in enumerate(jobs) if i % n_workers == int(wid)]


def build_programs(cand, nonce):
    from voyager.control_primitives import load_control_primitives
    from voyager.control_primitives_extra import load_extra_base_skills
    from voyager.v30_flags import V30_NAMED_ACTIONS

    extras = load_extra_base_skills(list(V30_NAMED_ACTIONS))
    base = "\n\n".join(load_control_primitives()) + "\n\n" + "\n\n".join(extras)
    code = cand.get("code") or ""
    fn = cand.get("fn")
    pre = (
        'globalThis.__leafSource = "gov";\n'
        "globalThis.__govOnline = false;\n"
        f"globalThis.__govNonce = {json.dumps(nonce)};\n"
        "globalThis.__govUnsettled = false;\n"
    )
    inject = code + "\n" + f"globalThis[{json.dumps(fn)}] = {fn};\n"
    return pre + "\n" + inject + "\n" + TOURNAMENT_HELPER + "\n" + base


def run_one_job(wid, mc_port, br_port, key, cand, seed, out_path):
    import shutil
    from voyager.env import VoyagerEnv

    cid = cand.get("id")
    chash = cand.get("code_hash")
    fn = cand.get("fn")
    give = dict(PRE_TOOLS.get(key) or {})
    scene_id = f"tour_{key}_{cid}"
    work = os.path.join(WORK_ROOT, f"w{wid}_{seed}_{scene_id}")
    if os.path.exists(work):
        shutil.rmtree(work)
    os.makedirs(work, exist_ok=True)
    log(f"=== tournament w{wid} key={key} id={cid} seed={seed} mc={mc_port} ===")
    srv = SM.start_mc(work, mc_port, seed)
    SM.record_pid(srv.pid, "minecraft-java")
    env = None
    tag = f"tour/w{wid}_{seed}_{scene_id}_{time.strftime('%H%M%S')}"
    row = {
        "key": key,
        "cand_id": cid,
        "code_hash": chash,
        "seed": int(seed),
        "success": False,
        "delta": 0,
        "ms": None,
        "died": False,
        "unsettled": False,
        "pre_tools": give,
        "fresh_world": True,
        "fn": fn,
    }
    t_wall0 = time.time()
    try:
        env = VoyagerEnv(
            mc_port=mc_port,
            server_port=br_port,
            request_timeout=300,
            log_path=work,
            pause_verify=True,
        )
        env.reset(options={"mode": "hard", "wait_ticks": 20})
        proc = getattr(getattr(env, "mineflayer", None), "process", None)
        SM.record_pid(getattr(proc, "pid", None), "mineflayer-node")
        TM.hold_unpaused(env)
        env.step(SM.boot_code(), programs="")
        time.sleep(3)
        spawn = TM.capture_world_spawn(env, seed)
        TM.prepare_start(env, seed, scene_id, spawn)
        if give:
            env.step(SM.give_code(give), programs="")
            env.step("await bot.waitForTicks(20);", programs="")
        nonce = uuid.uuid4().hex
        programs = build_programs(cand, nonce)
        call = (
            f"await __govTournamentCall(bot, {json.dumps(key)}, "
            f"{json.dumps({'id': cid, 'fn': fn})});"
        )
        t0 = time.time()
        obs = None
        err = None
        try:
            obs = env.step(call, programs=programs)
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:300]}"
        elapsed_ms = int(round((time.time() - t0) * 1000))
        parsed = parse_tournament_result(obs)
        inv_a = dict((SM.state(obs) or {}).get("inv") or {})
        leak = False
        leak_detail = None
        try:
            time.sleep(5)
            obs_b = env.step("await bot.waitForTicks(1);", programs="")
            inv_b = dict((SM.state(obs_b) or {}).get("inv") or {})
            if TM.inv_fingerprint(inv_a) != TM.inv_fingerprint(inv_b):
                leak = True
                leak_detail = {"a": inv_a, "b": inv_b}
        except Exception as e:
            leak_detail = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
        pending = TM.parse_pending_at_return(SM.chats(obs) if obs is not None else [])
        row.update(
            {
                "success": parsed["success"] and not leak,
                "delta": parsed["delta"],
                "ms": parsed["ms"] if parsed["ms"] is not None else elapsed_ms,
                "died": parsed["died"],
                "unsettled": parsed["unsettled"] or ("Timeout" in (err or "")),
                "error": err,
                "chats_tail": parsed.get("chats_tail") or [],
                "wall_ms": int(round((time.time() - t_wall0) * 1000)),
                "keep_tag": tag,
                "leak": leak,
                "leak_detail": leak_detail,
                "pending_at_return": pending,
            }
        )
        if err and not parsed.get("chats_tail"):
            row["unsettled"] = True
            row["success"] = False
    except TM.HarnessSpawnError as e:
        row["error"] = "harness_fail:" + str(e)[:240]
        row["harness_fail"] = True
    except Exception as e:
        row["error"] = f"{type(e).__name__}: {str(e)[:300]}"
        log(traceback.format_exc())
    finally:
        TM._close_scene(env, srv, work, tag)
        TM._wait_ports_free(mc_port, br_port, f"after tour {key}/{cid}/{seed}")
    append_result(out_path, row)
    log(
        f"  result key={key} id={cid} seed={seed} success={row.get('success')} "
        f"delta={row.get('delta')} ms={row.get('ms')} died={row.get('died')} "
        f"unsettled={row.get('unsettled')} leak={row.get('leak')} "
        f"pending_at_return={row.get('pending_at_return')} tools={give}"
    )
    return row


def run_tournament(
    keys=None,
    seeds=None,
    max_cands=None,
    pool_path=None,
    out_path=None,
    worker=None,
):
    keys = list(keys or POOL_KEYS)
    seeds = [int(s) for s in (seeds or sorted(TOURNAMENT_SEEDS))]
    pool_path = pool_path or os.environ.get("GOV_POOL") or POOL_PATH
    out_path = out_path or os.environ.get("TOURNAMENT_JSONL") or OUT_JSONL
    pool = load_pool(pool_path)
    jobs = iter_jobs(pool, keys, seeds, max_cands)
    wid = 0 if worker is None else int(worker)
    n_workers = TM._n_workers()
    jobs = shard_jobs(jobs, wid, n_workers)
    mc_port, br_port = TM._worker_ports(wid)
    SM.MC_PORT = mc_port
    SM.BR_PORT = br_port
    SM.LOG_PATH = TM.LOG_PATH
    SM.OUR_PIDS.clear()
    SM._CLEANED = False
    TM.assert_ports_unique_paired()
    if mc_port in TM.FORBIDDEN_MC or br_port in TM.FORBIDDEN_BR or mc_port in SM.AA_MC or br_port in SM.AA_BR:
        raise SystemExit(f"refusing forbidden ports mc={mc_port} br={br_port}")
    if SM.port_in_use(mc_port) or SM.port_in_use(br_port):
        raise SystemExit(f"port in use mc={mc_port} br={br_port} — do not kill others")
    done = load_done(out_path)
    log(f"tournament jobs={len(jobs)} done={len(done)} skip_keys={sorted(done)[:8]}")
    rows = []
    for key, cand, seed in jobs:
        trip = (str(key), str(cand.get("code_hash")), int(seed))
        if trip in done:
            log(f"skip {trip}")
            continue
        row = run_one_job(wid, mc_port, br_port, key, cand, seed, out_path)
        rows.append(row)
        done.add(trip)
    return rows


def parse_args(argv):
    keys = None
    seeds = None
    max_cands = None
    worker = None
    i = 0
    argv = list(argv)
    while i < len(argv):
        a = argv[i]
        if a == "--keys" and i + 1 < len(argv):
            keys = [x.strip() for x in argv[i + 1].split(",") if x.strip()]
            i += 2
            continue
        if a == "--seeds" and i + 1 < len(argv):
            seeds = [int(x) for x in argv[i + 1].split(",") if x.strip()]
            i += 2
            continue
        if a == "--max-cands" and i + 1 < len(argv):
            max_cands = int(argv[i + 1])
            i += 2
            continue
        if a == "--worker" and i + 1 < len(argv):
            worker = int(argv[i + 1])
            i += 2
            continue
        i += 1
    if os.environ.get("TOURNAMENT_KEYS"):
        keys = [x.strip() for x in os.environ["TOURNAMENT_KEYS"].split(",") if x.strip()]
    if os.environ.get("TOURNAMENT_SEEDS"):
        seeds = [int(x) for x in os.environ["TOURNAMENT_SEEDS"].split(",") if x.strip()]
    if os.environ.get("TOURNAMENT_MAX_CANDS"):
        max_cands = int(os.environ["TOURNAMENT_MAX_CANDS"])
    return keys, seeds, max_cands, worker


def main(argv=None):
    keys, seeds, max_cands, worker = parse_args(argv or sys.argv[1:])
    rows = run_tournament(keys=keys, seeds=seeds, max_cands=max_cands, worker=worker)
    print(json.dumps([
        {k: r.get(k) for k in (
            "key", "cand_id", "code_hash", "seed", "success", "delta", "ms",
            "died", "unsettled", "leak", "pending_at_return", "pre_tools", "fresh_world", "error",
        )}
        for r in rows
    ], ensure_ascii=False, indent=2, default=str)[:8000])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        log(traceback.format_exc())
        raise
