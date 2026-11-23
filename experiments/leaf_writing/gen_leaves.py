#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E2 (预注册_从零积累阶段一 v2): let the original Voyager action agent write leaf programs on demand.

Uses ActionAgent.render_system_message / render_human_message / process_ai_message unchanged, DeepSeek
official deepseek-v4-flash with thinking disabled, temperature 0.7, 12 samples per leaf key. Every parsed
program goes through the unchanged gov_pool classification and safety gates; survivors are instrumented
as tournament candidates (ids gen_<key>_<n>) and merged with the stage-one union pool.

Run in WSL from anywhere:  python3 gen_leaves.py [--dry-run] [--n 12]
Needs HTTPS proxy on this machine (set by the caller) and DEEPSEEK_API_KEY in ~/wt_v30_gov/.env.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import sys
import tempfile
import time

REPO = os.path.expanduser(os.environ.get("E2_REPO", "~/wt_v30_gov"))   # E3 on slac002 points this at a lineage dir
GOV_DIR = os.path.join(REPO, "_cc0924")
sys.path.insert(0, GOV_DIR)
sys.path.insert(0, REPO)
os.chdir(GOV_DIR)
import gov_pool as GP  # noqa: E402
from gov_harvest import RejectCounts  # noqa: E402

OUT = os.path.expanduser("~/stage1_0928/out")
# E13 (2026-10-09): overridable by GEN_LEAVES_MODEL / GEN_LEAVES_API_BASE; unset = as before
MODEL = os.environ.get("GEN_LEAVES_MODEL", "deepseek-v4-flash")
API_BASE = os.environ.get("GEN_LEAVES_API_BASE", "https://api.deepseek.com/v1")
TEMPERATURE = 0.7
TASKS = {
    "log": "Mine 3 wood logs",
    "cobblestone": "Mine 3 cobblestone",
    "coal": "Mine 3 coal ore",
    "raw_iron": "Mine 3 iron ore",
    "raw_gold": "Mine 3 gold ore",
    "redstone": "Mine 3 redstone ore",
    "diamond": "Mine 3 diamond ore",
}
# same prerequisite tools the tournament hands out (gov_tournament.PRE_TOOLS)
PRE_TOOLS = {
    "log": {}, "cobblestone": {"wooden_pickaxe": 1}, "coal": {"wooden_pickaxe": 1},
    "raw_iron": {"stone_pickaxe": 1}, "raw_gold": {"iron_pickaxe": 1},
    "redstone": {"iron_pickaxe": 1}, "diamond": {"iron_pickaxe": 1},
}


def load_key():
    if os.environ.get("GEN_LEAVES_API_KEY_ENV"):   # E13: e.g. OLLAMA_API_KEY for the local server (any value)
        return os.environ[os.environ["GEN_LEAVES_API_KEY_ENV"]]
    with open(os.environ.get("E2_ENV_FILE", os.path.join(REPO, ".env")), encoding="utf-8") as fp:
        for line in fp:
            if line.startswith("DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("DEEPSEEK_API_KEY missing in .env")


def first_round_events(key):
    inv = dict(PRE_TOOLS[key])
    mainhand = next(iter(inv), None)
    return [("observe", {
        "status": {
            "biome": "plains", "timeOfDay": "day", "entities": {}, "health": 20.0, "food": 20.0,
            "position": {"x": 0.5, "y": 64.0, "z": 0.5},
            "equipment": [None, None, None, None, mainhand, None],
            "inventoryUsed": len(inv),
        },
        "voxels": ["grass_block", "dirt", "stone"],
        "inventory": inv,
    })]


def make_agent(tmp):
    from voyager.agents.action import ActionAgent
    return ActionAgent(model_name=MODEL, temperature=TEMPERATURE, request_timout=240, ckpt_dir=tmp,
                       thinking="disabled", v30_shared_fixes=True)


def messages(agent, key):
    system = agent.render_system_message(skills=[])
    from voyager.intent import infer_task_intent
    # same Task Intent line the real loop always sends (voyager.py: rollout_task_intent fallback), Qwen 09-28 suggestion 1
    human = agent.render_human_message(events=first_round_events(key), code="", task=TASKS[key],
                                       context="", critique="",
                                       task_intent=infer_task_intent(TASKS[key], source="task_fallback"))
    return [system, human]


def one_call(agent, key, i):
    t0 = time.time()
    msgs = messages(agent, key)
    for attempt in range(3):
        try:
            ai = agent.llm(msgs)
            break
        except Exception as e:  # network hiccup: retry the same request, then record the failure
            err = repr(e)[:300]
            time.sleep(5 * (attempt + 1))
    else:
        return {"key": key, "i": i, "error": err, "sec": round(time.time() - t0, 1)}
    parsed = agent.process_ai_message(ai, task=TASKS[key])
    row = {"key": key, "i": i, "task": TASKS[key], "sec": round(time.time() - t0, 1), "content": ai.content}
    if isinstance(parsed, dict):
        row.update(program_name=parsed.get("program_name"), program_code=parsed.get("program_code"),
                   exec_code=parsed.get("exec_code"))
    else:
        row["parse_error"] = str(parsed)[:500]
    return row


def gate_rows(rows):
    """Unchanged gov_pool classification + gates. Generated code may define helpers but may not call library skills."""
    GP.install_rewrite_cache()
    banned = GP.banned_names()
    rejects = RejectCounts()
    hits = []
    per_row = []
    for r in rows:
        code = r.get("program_code") or ""
        if not code.strip():
            rejects.add("no_program")
            per_row.append((r["key"], r["i"], "no_program"))
            continue
        key, why, blocks, _ast = GP.classify_key(r["task"], code)
        if key != r["key"]:
            rejects.add("wrong_key" if key else (why or "not_obtain_class"))
            per_row.append((r["key"], r["i"], f"class:{key or why}"))
            continue
        deps = GP.collect_deps(code, {}, banned, rejects)
        if deps is None or deps:
            per_row.append((r["key"], r["i"], "deps"))
            continue
        bundle = GP.gate_bundle(code, [], rejects)
        if not bundle:
            per_row.append((r["key"], r["i"], "gate"))
            continue
        combined = "\n\n".join([bundle["norm"]] + bundle["norm_libs"])
        hits.append({"key": key, "hash": GP.sha256_text(combined), "norm": bundle["norm"], "norm_libs": bundle["norm_libs"],
                     "lib_names": [], "notes": bundle["notes"], "source_path": "generated:e2_0928", "source_seed": None,
                     "source_kind": "generated", "source_name": r.get("program_name") or r["task"], "round": r["i"],
                     "mined_blocks": blocks, "code_len": len(combined)})
        per_row.append((r["key"], r["i"], "ok"))
    return hits, dict(rejects), per_row


def build_pool(hits):
    rejects = RejectCounts()
    pool = {k: [] for k in GP.POOL_KEYS}
    seen = set()
    dropped = []
    for key in GP.POOL_KEYS:
        for h in sorted((h for h in hits if h["key"] == key), key=lambda h: (h["code_len"], h["hash"])):
            if (key, h["hash"]) in seen:
                continue
            seen.add((key, h["hash"]))
            cid = f"gen_{key}_{len(pool[key])}"
            built = GP.instrument_bundle(h["norm"], [], [], cid, key, rejects)
            if not built:
                dropped.append(f"{key} {h['source_name']} {h['hash'][:12]}")
                continue
            code = built["code"]
            pool[key].append({
                "id": cid, "fn": built["fn"], "code": code, "code_hash": GP.sha256_text(code), "norm_hash": h["hash"],
                "source_path": h["source_path"], "source_seed": None, "source_kind": "generated",
                "source_name": h["source_name"], "round": h["round"], "n_success": 1, "deps": [],
                "mined_blocks": list(h.get("mined_blocks") or []),
                "normalized": list(h.get("notes") or []) + list(built.get("normalized") or []),
                "rewrites": built.get("rewrites") or [], "target_n": 1,
            })
    return pool, dropped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print the rendered prompt for one key, no API call")
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--regate", action="store_true", help="re-run gates on an existing gen_raw.jsonl, no API call")
    a = ap.parse_args()
    os.environ["OPENAI_API_KEY"] = load_key()
    os.environ["OPENAI_API_BASE"] = API_BASE
    os.makedirs(OUT, exist_ok=True)
    raw_path = os.path.join(OUT, "gen_raw.jsonl")
    tmp = tempfile.mkdtemp(prefix="e2gen_")
    agent = make_agent(tmp)
    if a.dry_run:
        msgs = messages(agent, "redstone")
        print("=== SYSTEM (first 1500 / last 1200 chars) ===")
        print(msgs[0].content[:1500], "\n...\n", msgs[0].content[-1200:])
        print("=== HUMAN ===")
        print(msgs[1].content)
        return 0
    if not a.regate:
        if os.path.exists(raw_path):
            raise SystemExit(f"{raw_path} exists; refusing to generate twice (use --regate)")
        jobs = [(k, i) for k in GP.POOL_KEYS for i in range(a.n)]
        rows = []
        with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
            futs = [ex.submit(one_call, agent, k, i) for k, i in jobs]
            for f in cf.as_completed(futs):
                r = f.result()
                rows.append(r)
                with open(raw_path, "a", encoding="utf-8") as fp:
                    fp.write(json.dumps(r, ensure_ascii=False) + "\n")
                print(f"[gen] {r['key']} #{r['i']} {r.get('program_name') or r.get('parse_error', '')[:60] or r.get('error', '')[:60]} {r['sec']}s", flush=True)
    rows = [json.loads(l) for l in open(raw_path, encoding="utf-8") if l.strip()]
    assert len(rows) == len(GP.POOL_KEYS) * a.n, f"gen_raw.jsonl has {len(rows)} rows, expected {len(GP.POOL_KEYS) * a.n}"
    hits, rejects, per_row = gate_rows(rows)
    pool, dropped = build_pool(hits)
    with open(os.path.join(OUT, "gen_pool.json"), "w", encoding="utf-8") as fp:
        json.dump({"candidates": pool, "note": "E2 generated leaves (pre-registration 09-28 v2)"}, fp, ensure_ascii=False, indent=1)
    summary = {
        "request": {"model": MODEL, "api_base": API_BASE, "temperature": TEMPERATURE, "thinking": "disabled",
                    "task_intent": "infer_task_intent(task, source='task_fallback')", "n_per_key": a.n},
        "calls": len(rows), "api_errors": sum(1 for r in rows if r.get("error")),
        "parse_errors": sum(1 for r in rows if r.get("parse_error")), "gate_rejects": rejects,
        "passed_per_key": {k: sum(1 for kk, _, s in per_row if kk == k and s == "ok") for k in GP.POOL_KEYS},
        "unique_per_key": {k: len(v) for k, v in pool.items()}, "dropped_at_instrument": dropped,
        "outcome_per_row": sorted(per_row),
    }
    with open(os.path.join(OUT, "gen_summary.json"), "w", encoding="utf-8") as fp:
        json.dump(summary, fp, ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in summary.items() if k != "outcome_per_row"}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
