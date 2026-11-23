#!/usr/bin/env python3
"""E9 (方案_E9_循环内完整生命周期_2026-09-29): before each loop game of a lineage, every leaf key the game's task needs
that has no candidate on duty in this world gets K_NEW fresh leaves written on the spot by Voyager's own action agent
(the E2/E3 generator, gen_leaves.py). A candidate is on duty unless the lineage ledger has retired it or quarantined it
in the current world. Survivors of the unchanged gates are appended to the lineage pool; the ledger decides who stays.
Same logic as tools_0928/e3_grow.py (E3), with the pool, ledger, log, world and id prefix passed explicitly.
Usage (E2_REPO=<loop repo with .env>): e9_grow.py --pool P --ledger L --log G --lineage N --scene S --game K --world SEED
"""
import argparse
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools_0928"))
import gen_leaves as G  # noqa: E402  (sets up gov_pool from E2_REPO)

GP = G.GP
K_NEW = 3
NEEDS = {  # leaf keys the executor's recipe tree reaches for each evaluation task (as in E3)
    "diamond_pickaxe": ["log", "cobblestone", "coal", "raw_iron", "diamond"],
    "golden_sword": ["log", "cobblestone", "coal", "raw_iron", "raw_gold"],
    "compass": ["log", "cobblestone", "coal", "raw_iron", "redstone"],
}


def on_duty(cand, ledger, world):
    rec = ledger.get(cand["code_hash"]) or {}
    if rec.get("retired"):
        return False
    q = ((rec.get("ctx") or {}).get(str(world)) or {})
    return not q.get("quarantined")


def needed_keys(pool, ledger, scene, world):
    need = []
    for key in NEEDS[scene]:
        cands = pool.get(key) or []
        if not any(on_duty(c, ledger, world) for c in cands):
            need.append((key, "empty" if not cands else "none_on_duty"))
    return need


def main():
    ap = argparse.ArgumentParser()
    for a_ in ("--pool", "--ledger", "--log", "--scene", "--world", "--prefix"):
        ap.add_argument(a_, required=a_ != "--prefix")
    ap.add_argument("--lineage", type=int, required=True)
    ap.add_argument("--game", type=int, required=True)
    a = ap.parse_args()
    prefix = a.prefix or f"gov_e9L{a.lineage}"
    assert prefix.startswith("gov_"), "the executor only runs candidates whose fn starts with gov_"
    pool = json.load(open(a.pool))
    ledger = (json.load(open(a.ledger)).get("entries") or {}) if os.path.exists(a.ledger) else {}
    need = needed_keys(pool, ledger, a.scene, a.world)
    if not need:
        with open(a.log, "a") as fp:
            fp.write(json.dumps({"game": a.game, "scene": a.scene, "world": a.world, "grew": [], "ts": time.time()}) + "\n")
        print(f"[e9] game {a.game} {a.scene}: every needed key has a candidate on duty")
        return 0
    os.environ["OPENAI_API_KEY"] = G.load_key()
    os.environ["OPENAI_API_BASE"] = G.API_BASE
    agent = G.make_agent(tempfile.mkdtemp(prefix="e9gen_"))
    from gov_harvest import RejectCounts
    rejects = RejectCounts()
    infra_fail = []
    for key, why in need:
        t0 = time.time()
        rows = [G.one_call(agent, key, i) for i in range(K_NEW)]
        hits, rej, per_row = G.gate_rows(rows)
        added = []
        known = {c.get("norm_hash") for c in pool.get(key) or []}
        for h in sorted(hits, key=lambda h: (h["code_len"], h["hash"])):
            if h["hash"] in known:
                continue
            known.add(h["hash"])
            cid = f"{prefix}_{key}_{len(pool.get(key) or [])}"
            built = GP.instrument_bundle(h["norm"], [], [], cid, key, rejects)
            if not built:
                continue
            code = built["code"]
            pool.setdefault(key, []).append({
                "id": cid, "fn": built["fn"], "code": code, "code_hash": GP.sha256_text(code), "norm_hash": h["hash"],
                "source_path": "generated:e9_0929", "source_seed": None, "source_kind": "generated_online",
                "source_name": h["source_name"], "round": a.game, "n_success": 1, "deps": [],
                "mined_blocks": list(h.get("mined_blocks") or []),
                "normalized": list(h.get("notes") or []) + list(built.get("normalized") or []),
                "rewrites": built.get("rewrites") or [], "target_n": 1, "subgoal": h["source_name"],
                "source_attempt": "e9_0929", "inv_source": "e9_online",
            })
            added.append(cid)
        tmp = a.pool + ".tmp"
        json.dump(pool, open(tmp, "w"), ensure_ascii=False, indent=1)
        os.replace(tmp, a.pool)
        if rows and all(r.get("error") for r in rows):
            infra_fail.append(key)
        rec = {"game": a.game, "scene": a.scene, "world": a.world, "key": key, "why": why, "calls": len(rows), "model": G.MODEL, "api_base": G.API_BASE,
               "api_errors": sum(1 for r in rows if r.get("error")), "parse_errors": sum(1 for r in rows if r.get("parse_error")),
               "outcomes": [s for _, _, s in per_row], "added": added, "sec": round(time.time() - t0, 1), "ts": time.time(),
               "raw": [{k: r.get(k) for k in ("i", "program_name", "parse_error", "error", "content")} for r in rows]}
        with open(a.log, "a") as fp:
            fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"[e9] game {a.game} {a.scene}: {key} ({why}) -> {len(added)} added {added}")
    if infra_fail:
        print(f"[e9] game {a.game}: API failed for every call of {infra_fail} -> exit 2 (runner stops the lineage)")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
