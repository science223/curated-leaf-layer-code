#!/usr/bin/env python3
"""E3 (预注册_从零在线生命线_2026-09-28): before each game of a lineage, every leaf key the game's task needs that has
no candidate on duty (pool empty, or every candidate retired by the lineage ledger) gets K_NEW fresh leaves written on
the spot by the original Voyager action agent (same generator as E2). Survivors of the unchanged gates are appended to
the lineage pool (append-only); the ledger then decides who stays. Run from run_e3_lineage.sh with E2_REPO=<lineage dir>.
"""
import argparse
import glob
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gen_leaves as G  # noqa: E402  (sets up gov_pool from E2_REPO)

GP = G.GP
K_NEW = 3
NEEDS = {  # leaf keys the executor's recipe tree reaches for each evaluation task
    "diamond_pickaxe": ["log", "cobblestone", "coal", "raw_iron", "diamond"],
    "golden_sword": ["log", "cobblestone", "coal", "raw_iron", "raw_gold"],
    "compass": ["log", "cobblestone", "coal", "raw_iron", "redstone"],
}


def load_ledger(ledger_dir, lineage):
    files = glob.glob(os.path.join(ledger_dir, "**", f"lineage_L{lineage}.json"), recursive=True)
    if not files:
        return {}
    if len(files) > 1:
        raise SystemExit(f"more than one ledger for lineage {lineage}: {files}")
    return (json.load(open(files[0])) or {}).get("entries") or {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lineage", type=int, required=True)
    ap.add_argument("--scene", required=True)
    ap.add_argument("--game", type=int, required=True)
    a = ap.parse_args()
    repo = G.REPO
    pool_path = os.path.join(repo, "_gov", "pool_e3.json")
    log_path = os.path.join(repo, "_gov", "e3_grow.jsonl")
    pool = json.load(open(pool_path))
    ledger = load_ledger(os.path.join(repo, "_gov", "ledger_e3"), a.lineage)
    need = []
    for key in NEEDS[a.scene]:
        cands = pool.get(key) or []
        on_duty = [c for c in cands if not (ledger.get(c["code_hash"]) or {}).get("retired")]
        if not on_duty:
            need.append((key, "empty" if not cands else "all_retired"))
    if not need:
        with open(log_path, "a") as fp:
            fp.write(json.dumps({"game": a.game, "scene": a.scene, "grew": [], "ts": time.time()}) + "\n")
        print(f"[e3] game {a.game} {a.scene}: every needed key has a candidate on duty")
        return 0
    os.environ["OPENAI_API_KEY"] = G.load_key()
    os.environ["OPENAI_API_BASE"] = G.API_BASE
    agent = G.make_agent(tempfile.mkdtemp(prefix="e3gen_"))
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
            # the executor only runs governed candidates whose fn starts with "gov_" (obtain.js /^gov_/)
            cid = f"gov_e3L{a.lineage}_{key}_{len(pool.get(key) or [])}"
            built = GP.instrument_bundle(h["norm"], [], [], cid, key, rejects)
            if not built:
                continue
            code = built["code"]
            pool.setdefault(key, []).append({
                "id": cid, "fn": built["fn"], "code": code, "code_hash": GP.sha256_text(code), "norm_hash": h["hash"],
                "source_path": "generated:e3_0928", "source_seed": None, "source_kind": "generated_online",
                "source_name": h["source_name"], "round": a.game, "n_success": 1, "deps": [],
                "mined_blocks": list(h.get("mined_blocks") or []),
                "normalized": list(h.get("notes") or []) + list(built.get("normalized") or []),
                "rewrites": built.get("rewrites") or [], "target_n": 1, "subgoal": h["source_name"],
                "source_attempt": "e3_0928", "inv_source": "e3_online",
            })
            added.append(cid)
        # write the grown pool first (atomic), then the log line, so the log never claims more than the pool holds
        tmp = pool_path + ".tmp"
        json.dump(pool, open(tmp, "w"), ensure_ascii=False, indent=1)
        os.replace(tmp, pool_path)
        if rows and all(r.get("error") for r in rows):
            infra_fail.append(key)   # every call of this key failed at the API: infrastructure, not the model
        rec = {"game": a.game, "scene": a.scene, "key": key, "why": why, "calls": len(rows),
               "api_errors": sum(1 for r in rows if r.get("error")), "parse_errors": sum(1 for r in rows if r.get("parse_error")),
               "outcomes": [s for _, _, s in per_row], "added": added, "sec": round(time.time() - t0, 1), "ts": time.time(),
               "raw": [{k: r.get(k) for k in ("i", "program_name", "parse_error", "error", "content")} for r in rows]}
        with open(log_path, "a") as fp:
            fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"[e3] game {a.game} {a.scene}: {key} ({why}) -> {len(added)} added {added}")
    if infra_fail:
        print(f"[e3] game {a.game}: API failed for every call of {infra_fail} -> exit 2 (runner stops the lineage)")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
