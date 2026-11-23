#!/usr/bin/env python3
"""slac002, after the stage-one + E2 tournament (预注册_从零积累阶段一 §5, v2): build one frozen pool per
evaluation group with gov_select.select_screened (wins desc -> mean success ms asc -> code_hash; top 3 per key),
the same function that produced the original pool_screened.json. No MC, no LLM."""
import hashlib
import json
import os
import sys

T = os.path.expanduser("~/V30_s1_tourn")
sys.path.insert(0, os.path.join(T, "_cc0924"))
sys.path.insert(0, T)
os.chdir(os.path.join(T, "_cc0924"))
import gov_select as GS  # noqa: E402

KEYS = ["log", "cobblestone", "coal", "raw_iron", "raw_gold", "redstone", "diamond"]
OUT = os.path.expanduser("~/V30_s1_pools")
CELLS = {"w1t40": "w1@40", "w1t80": "w1@80", "w1t160": "w1@160", "w2t40": "w2@40", "w2t80": "w2@80", "w2t161": "w2@161"}
FILL = {"w1t40g": "w1t40", "w1t80g": "w1t80", "w1t160g": "w1t160", "w2t40g": "w2t40"}


def md5(path):
    return hashlib.md5(open(path, "rb").read()).hexdigest()


def main():
    os.makedirs(OUT, exist_ok=True)
    pool = json.load(open(os.path.join(T, "_gov", "pool_s1_combined.json")))["candidates"]
    rows = GS.load_jsonl(os.path.join(T, "_gov", "tournament_s1.jsonl"))
    manifest = json.load(open(os.path.join(T, "_gov", "stage1_manifest.json")))["manifest"]
    by_id = {c["id"]: (k, c) for k in KEYS for c in pool.get(k, [])}
    # every candidate must have exactly 2 tournament results (seeds 020, 021) before pools are frozen
    n_res = {}
    for r in rows:
        if r.get("event") and r.get("event") != "result":
            continue
        if "success" in r:
            n_res[(r.get("key"), r.get("code_hash"))] = n_res.get((r.get("key"), r.get("code_hash")), 0) + 1
    missing = [c["id"] for k in KEYS for c in pool.get(k, []) if n_res.get((k, c["code_hash"]), 0) != 2]
    if missing:
        raise SystemExit(f"tournament incomplete for {len(missing)} candidates, e.g. {missing[:5]}")
    # evaluation copies with gov_ ids (the executor only runs fn names starting with "gov_"); same code up to the id
    gov_by_tid = {c["tourn_id"]: c for k in KEYS for c in
                  json.load(open(os.path.join(T, "_gov", "pool_s1_combined_govids.json")))["candidates"].get(k, [])}
    assert len(gov_by_tid) == len(by_id), "govids pool does not cover the tournament pool"
    groups, rankings = {}, {}
    for g, cell in CELLS.items():
        sub = {k: [by_id[i][1] for i in manifest[cell].get(k, [])] for k in KEYS}
        groups[g], rankings[g] = GS.select_screened({"candidates": sub}, rows)
    gen_sub = {k: [c for c in pool.get(k, []) if c["id"].startswith("gen_")] for k in KEYS}
    groups["gen"], rankings["gen"] = GS.select_screened({"candidates": gen_sub}, rows)
    for g, base in FILL.items():
        groups[g] = {k: list(groups[base][k] if groups[base][k] else groups["gen"][k]) for k in KEYS}
        rankings[g] = {"filled_from_gen": [k for k in KEYS if not groups[base][k]]}
    for g, p in groups.items():   # swap every picked tournament record for its gov_ evaluation copy
        for k in KEYS:
            swapped = []
            for c in p[k]:
                e = dict(gov_by_tid[c["id"]])
                e["tournament_wins"], e["tournament_mean_success_ms"] = c.get("tournament_wins"), c.get("tournament_mean_success_ms")
                assert e["norm_hash"] == c["norm_hash"] and e["tourn_code_hash"] == c["code_hash"]
                swapped.append(e)
            p[k] = swapped
    info = {}
    for g, p in groups.items():
        path = os.path.join(OUT, f"pool_{g}.json")
        json.dump(p, open(path, "w"), ensure_ascii=False, indent=1)
        info[g] = {"md5": md5(path), "per_key": {k: [c["id"] for c in p[k]] for k in KEYS},
                   "wins": {k: [c.get("tournament_wins") for c in p[k]] for k in KEYS}}
    json.dump({"groups": info, "rankings": rankings}, open(os.path.join(OUT, "select_report.json"), "w"), ensure_ascii=False, indent=1)
    for g, v in info.items():
        print(g, v["md5"][:8], {k: f"{len(v['per_key'][k])}:{v['wins'][k]}" for k in KEYS})


if __name__ == "__main__":
    main()
