#!/usr/bin/env python3
"""E6 health checks (预注册_匹配重跑与新种子_2026-09-29.md §6), run BEFORE r12_analyze.py.

Reads only health fields (env_dead, died, leak_events, n_hard_resets, gov_unsettled, gov_tries, leaf_source,
pool_fingerprint, experiment_id, seed, scene) and infrastructure errors in the stdout files; never success or calls.
Run on slac002: python3 ~/r12_health.py [--partial]   (--partial: mid-run look, missing games are not a failure)
"""
import collections
import glob
import json
import os
import re
import sys

HOME = os.path.expanduser("~")
JOBS = os.path.join(HOME, "r12_jobs.txt")
FP = {"rand_n": "d60321d6483e", "scr_n": "d6f93d800bcb", "sys_n": "6b4dd13ed21e"}
ERR = re.compile(r"ECONNREFUSED|port in use|already in use|refusing forbidden|Killed|MemoryError")
TASKS = ["diamond_pickaxe", "golden_sword", "compass"]
BENIGN_TB = "_cuda_bindings_redirector.pth"   # two harmless tracebacks at every venv start
KEYS = ("env_dead", "died", "leak_events", "n_hard_resets", "gov_unsettled", "gov_tries", "leaf_source",
        "pool_fingerprint", "experiment_id", "seed", "scene")


def jobs():
    out = collections.OrderedDict()
    for line in open(JOBS):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        d, arm, mode, online, pool, seed = line.split(":")
        out.setdefault(arm, {"dir": d, "mode": mode, "online": online, "seeds": []})["seeds"].append(seed[-3:])
    return out


def rows(d, arm):
    out = []
    f = os.path.join(HOME, d, "_cc0924", f"eval_{arm}.jsonl")
    if not os.path.exists(f):
        return out
    for line in open(f):
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("event") == "job_done":
            w = r.get("row") or {}
            out.append({k: w.get(k) for k in KEYS})
    return out


def infra_errors(d, arm):
    n = 0
    for f in glob.glob(os.path.join(HOME, d, "_cc0924", f"eval_{arm}_*.stdout")):
        text = open(f, errors="replace").read().splitlines()
        n += sum(1 for line in text if ERR.search(line))
        tb = sum(1 for line in text if "Traceback (most recent call last)" in line)
        benign = sum(1 for line in text if line.startswith("Error processing line") and BENIGN_TB in line)
        n += max(0, tb - benign)
    return n


def main(partial=False):
    bad = []
    print(f"{'arm':7} {'mode':5} games env_dead died leak_games resets unsettled gov_tries(games>0) errs fp / eids / src")
    for arm, j in jobs().items():
        R = rows(j["dir"], arm)
        exp = 3 * len(j["seeds"])
        cells = collections.Counter((str(r["seed"])[-3:], r["scene"]) for r in R)
        dead = sum(1 for r in R if r["env_dead"])
        died = sum(1 for r in R if r["died"])
        leak = sum(1 for r in R if r["leak_events"])
        resets = sum(int(r["n_hard_resets"] or 0) for r in R)
        uns = sum(int(r["gov_unsettled"] or 0) for r in R)
        tries = sum(int(r["gov_tries"] or 0) for r in R)
        tried = sum(1 for r in R if (r["gov_tries"] or 0) > 0)
        srcs = sorted({str(r["leaf_source"]) for r in R})
        fps = sorted({str(r["pool_fingerprint"]) for r in R})
        eids = sorted({str(r["experiment_id"]) for r in R})
        errs = infra_errors(j["dir"], arm)
        print(f"{arm:7} {j['mode']:5} {len(R):2}/{exp:2} {dead:8} {died:4} {leak:10} {resets:6} {uns:9} {tries:5}({tried:2}) "
              f"{errs:4} {','.join(fps)} / {len(eids)} eids / {','.join(srcs)}")
        if any(c != 1 for c in cells.values()):
            bad.append(f"{arm}: duplicate cells {[k for k, c in cells.items() if c != 1]}")
        planned = {(s, t) for s in j["seeds"] for t in TASKS}
        stray = sorted(set(cells) - planned)
        if stray:
            bad.append(f"{arm}: cells outside the job list {stray[:5]}")
        if len(R) != exp and not partial:
            bad.append(f"{arm}: {len(R)} games, {exp} expected; missing {sorted(planned - set(cells))[:5]}")
        if exp and dead > 0.2 * exp:
            # 预注册 §6.2: flag red and list separately, the decision still goes ahead
            print(f"  WARN {arm}: env_dead {dead} > 20% of {exp} (flagged red; decision goes ahead)")
        if errs:
            bad.append(f"{arm}: {errs} infrastructure error lines")
        if j["mode"] == "hand":
            if tries or (R and srcs != ["hand"]):
                bad.append(f"{arm}: hand arm shows gov activity or leaf_source {srcs}")
        else:
            if R and srcs != ["gov"]:
                bad.append(f"{arm}: leaf_source {srcs}")
            if R and fps != [FP[arm]]:
                bad.append(f"{arm}: pool fingerprints {fps} != {FP[arm]}")
            if R and tried < 0.8 * len(R):
                bad.append(f"{arm}: gov_tries>0 in only {tried}/{len(R)} games")
        if j["online"] == "1":
            per_seed = collections.defaultdict(set)
            for r in R:
                per_seed[str(r["seed"])[-3:]].add(str(r["experiment_id"]))
            shared = [s for s, e in per_seed.items() if len(e) != 1]
            if shared or len(set(e for es in per_seed.values() for e in es)) != len(per_seed):
                bad.append(f"{arm}: experiment ids are not one per seed")
    print()
    print("HEALTH " + ("PASS" if not bad else "FAIL") + (" (partial look)" if partial else ""))
    for b in bad:
        print("  -", b)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main(partial="--partial" in sys.argv))
