#!/usr/bin/env python3
"""The executor only runs governed candidates whose function name starts with "gov_" (obtain.js _obtainGovLeaf:
/^gov_/). The tournament calls cand.fn directly, so it ran the s1_/gen_ ids fine, but the evaluation would skip them.
Re-instrument every candidate of pool_s1_combined.json with id "gov_" + id using the unchanged instrument_bundle,
and prove that nothing but the id changed. Output: ~/stage1_0928/out/pool_s1_combined_govids.json. Run in WSL."""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_leaves as G  # noqa: E402  (sets up gov_pool from ~/wt_v30_gov)
from gov_harvest import RejectCounts  # noqa: E402

GP = G.GP
OUT = os.path.expanduser("~/stage1_0928/out")


def stage1_hits():
    hits = {}
    for path in glob.glob(os.path.expanduser("~/stage1_0928/snap/w*_T*/*/skill/skills.json")):
        if "_T999" in path:
            continue
        rej, exc, sh, raw = RejectCounts(), RejectCounts(), {}, []
        GP.ingest_skills(path, GP.banned_names(), rej, exc, sh, raw)
        for h in raw:
            hits.setdefault((h["key"], h["hash"]), h)
    return hits


def gen_hits():
    rows = [json.loads(l) for l in open(os.path.join(OUT, "gen_raw.jsonl")) if l.strip()]
    hits, _, _ = G.gate_rows(rows)
    return {(h["key"], h["hash"]): h for h in hits}


def main():
    GP.install_rewrite_cache()
    pool = json.load(open(os.path.join(OUT, "pool_s1_combined.json")))["candidates"]
    hits = stage1_hits()
    hits.update(gen_hits())
    rejects = RejectCounts()
    out = {k: [] for k in pool}
    for key, recs in pool.items():
        for c in recs:
            h = hits.get((key, c["norm_hash"]))
            assert h is not None, f"no source hit for {c['id']}"
            new_id = "gov_" + c["id"]
            built = GP.instrument_bundle(h["norm"], h.get("norm_libs") or [], h.get("lib_names") or [], new_id, key, rejects)
            assert built, f"instrument failed for {new_id}"
            # only the id may differ: undo the rename and compare with the tournament version byte for byte
            assert built["code"].replace(new_id, c["id"]) == c["code"], f"{new_id}: code differs beyond the id"
            assert built["fn"] == "gov_" + c["fn"], (built["fn"], c["fn"])
            rec = dict(c)
            rec.update(id=new_id, fn=built["fn"], code=built["code"], code_hash=GP.sha256_text(built["code"]),
                       tourn_id=c["id"], tourn_code_hash=c["code_hash"])
            out[key].append(rec)
    path = os.path.join(OUT, "pool_s1_combined_govids.json")
    json.dump({"candidates": out, "note": "pool_s1_combined.json re-instrumented with gov_ ids for evaluation"},
              open(path, "w"), ensure_ascii=False, indent=1)
    n = sum(len(v) for v in out.values())
    print(f"re-instrumented {n} candidates; every code identical to the tournament version up to the id; wrote {path}")


if __name__ == "__main__":
    main()
