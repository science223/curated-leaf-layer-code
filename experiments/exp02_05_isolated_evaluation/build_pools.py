#!/usr/bin/env python3
"""Build the system pool (innate first, then the DeepSeek top-3 in tournament order) and the innate-only tournament
pool (pre-registration v2). Usage: python3 build_pools.py <tournament_dir> ; reads <dir>/_gov/pool_screened.json."""
import hashlib
import json
import os
import sys

KEYS = ["log", "cobblestone", "coal", "raw_iron", "raw_gold", "redstone", "diamond"]


def innate(key):
    fn = f"gov_innate_{key}"
    code = f'async function {fn}(bot) {{ return await _obtainInnateRun(bot, "{key}"); }}'
    return {"id": f"innate_{key}", "fn": fn, "code": code,
            "code_hash": hashlib.sha256(code.encode("utf-8")).hexdigest(),
            "target_n": 10 ** 9, "source_kind": "innate", "n_success": None}


def main():
    d = sys.argv[1]
    scr = json.load(open(os.path.join(d, "_gov", "pool_screened.json"), encoding="utf-8"))
    scr = scr.get("candidates") or scr
    for k in KEYS:
        ids = [c.get("id") for c in scr.get(k) or []]
        if any(str(i).startswith("innate_") for i in ids):
            raise SystemExit(f"screened pool for {k} already contains an innate entry: {ids}")
    system = {k: [innate(k)] + list(scr.get(k) or []) for k in KEYS}
    json.dump(system, open(os.path.join(d, "_gov", "pool_system.json"), "w", encoding="utf-8"), ensure_ascii=False)
    json.dump({"candidates": {k: [innate(k)] for k in KEYS}},
              open(os.path.join(d, "_gov", "pool_innate_only.json"), "w", encoding="utf-8"), ensure_ascii=False)
    for k in KEYS:
        print(k, [c.get("id") for c in system[k]])


if __name__ == "__main__":
    main()
