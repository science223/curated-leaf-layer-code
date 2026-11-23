#!/usr/bin/env python3
"""Stage-one health checks (预注册_从零积累阶段一_2026-09-28.md §8 items 6-7), run BEFORE s1_analyze.py.

Prints only health quantities per group: games found, env_dead, deaths, leak games, hard resets,
gov_unsettled, pending-at-return, treatment activation (gov_tries), leaf source, pool fingerprints,
experiment ids and infrastructure errors in the stdout files. It never reads success or calls.
Run on slac002: python3 ~/s1_health.py
"""
import collections
import glob
import hashlib
import json
import os
import re
import sys

HOME = os.path.expanduser("~")
JOBS = os.path.join(HOME, "s1_eval_jobs.txt")
POOLS = os.path.join(HOME, "V30_s1_pools")
TASKS = ["diamond_pickaxe", "golden_sword", "compass"]
ERR = re.compile(r"ECONNREFUSED|port in use|refusing forbidden|Killed|MemoryError")
# every venv start prints two harmless tracebacks from a .pth file (No module named 'cuda'); count only the others
BENIGN_TB = "_cuda_bindings_redirector.pth"
HEALTH_KEYS = ("env_dead", "died", "leak_events", "n_hard_resets", "gov_unsettled", "pending_at_return",
               "gov_tries", "leaf_source", "pool_fingerprint", "experiment_id", "seed", "scene")


def jobs():
    out = collections.OrderedDict()
    for line in open(JOBS):
        line = line.strip()
        if not line:
            continue
        d, group, src, _, pool, seed = line.split(":")
        out.setdefault(group, {"dir": d, "src": src, "pool": pool, "seeds": []})["seeds"].append(seed[-3:])
    return out


def rows(d, group):
    out = []
    for f in glob.glob(os.path.join(HOME, d, "_cc0924", f"eval_{group}.jsonl")):
        for line in open(f):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("event") != "job_done":
                continue
            w = r.get("row") or {}
            out.append({k: w.get(k) for k in HEALTH_KEYS})  # health fields only
    return out


def n_pending(v):
    if isinstance(v, list):
        return sum(1 for x in v if isinstance(x, dict) and x.get("pending"))
    return int(bool(v))


def main():
    J = jobs()
    bad = []
    print(f"{'group':9} {'src':5} games env_dead died leak_games resets unsettled pending gov_tries(games>0) errs fp")
    for g, j in J.items():
        R = rows(j["dir"], g)
        exp_games = 3 * len(j["seeds"])
        dead = sum(1 for r in R if r["env_dead"])
        died = sum(1 for r in R if r["died"])
        leak = sum(1 for r in R if r["leak_events"])
        resets = sum(int(r["n_hard_resets"] or 0) for r in R)
        uns = sum(int(r["gov_unsettled"] or 0) for r in R)
        pend = sum(n_pending(r["pending_at_return"]) for r in R)
        tries = sum(int(r["gov_tries"] or 0) for r in R)
        tried_games = sum(1 for r in R if (r["gov_tries"] or 0) > 0)
        srcs = sorted({str(r["leaf_source"]) for r in R})
        fps = sorted({str(r["pool_fingerprint"]) for r in R})
        cells = collections.Counter((str(r["seed"])[-3:], r["scene"]) for r in R)
        errs = 0
        for f in glob.glob(os.path.join(HOME, j["dir"], "_cc0924", f"eval_{g}_*.stdout")):
            text = open(f, errors="replace").read().splitlines()
            errs += sum(1 for line in text if ERR.search(line))
            n_tb = sum(1 for line in text if "Traceback (most recent call last)" in line)
            n_benign = sum(1 for line in text if line.startswith("Error processing line") and BENIGN_TB in line)
            errs += max(0, n_tb - n_benign)
        pool_md5 = ""
        pf = os.path.join(POOLS, j["pool"])
        if j["pool"] and os.path.isfile(pf):
            pool_md5 = hashlib.md5(open(pf, "rb").read()).hexdigest()[:12]
        print(f"{g:9} {j['src']:5} {len(R):2}/{exp_games:2} {dead:8} {died:4} {leak:10} {resets:6} {uns:9} {pend:7} "
              f"{tries:5}({tried_games:2}) {errs:4} {','.join(fps)} pool_md5={pool_md5 or '-'} src={','.join(srcs)}")
        # checks
        if len(R) != exp_games or any(c != 1 for c in cells.values()):
            bad.append(f"{g}: {len(R)} games for {exp_games} expected, duplicate cells={[k for k, c in cells.items() if c != 1]}")
        if dead >= 3:
            bad.append(f"{g}: env_dead {dead} >= 3 (flag red, list separately)")
        if errs:
            bad.append(f"{g}: {errs} infrastructure error lines in stdout")
        if j["src"] == "prim":
            if tries or srcs != ["prim"]:
                bad.append(f"{g}: primitive group shows gov activity (tries={tries}, src={srcs})")
        else:
            if srcs != ["gov"]:
                bad.append(f"{g}: leaf source {srcs}, expected gov")
            if len(fps) != 1:
                bad.append(f"{g}: {len(fps)} pool fingerprints")
            if len(R) and tried_games == 0:
                bad.append(f"{g}: treatment never activated (gov_tries 0 in all games)")
    print()
    print("HEALTH " + ("PASS" if not bad else "FAIL"))
    for b in bad:
        print("  -", b)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
