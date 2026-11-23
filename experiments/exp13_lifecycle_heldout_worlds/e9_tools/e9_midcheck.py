#!/usr/bin/env python3
"""E9 mid-run problem check (预注册_循环内完整生命周期; 10-03 asked whether anything is wrong with the run, not for
outcomes). Runs the preregistered per-lineage health checks of e9_analyze.py on the LIFE lineages and adds run counters.
Prints problems and mechanism counters only: never success, rounds, scores or per-game call counts.
Run on slac002: python3 ~/e9_tools/e9_midcheck.py"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e9_analyze as A  # noqa: E402

HOME = os.path.expanduser("~")
data = A.load(os.path.join(HOME, "wt_v30_e9"), "n", os.path.join(HOME, "e9_lineages.log"))
runs = os.path.realpath(os.path.join(HOME, "wt_v30_e9", "runs_0911"))
for L in A.LIN:
    lin = data["life"][L]
    bad = A.lineage_health(L, "n", lin)
    d = os.path.join(runs, f"zs_e9n_life_L{L}")
    replays = {t: k for t, k in lin["retries"].items() if k}
    grows = [x for n in sorted(lin["grow"]) for x in (lin["grow"][n] or [])]
    added = sum(len(x.get("added") or []) for x in grows)
    empty_pool_games = sum(1 for n, (pre, post, fp) in lin["pools"].items() if post == {})
    kicks = disc = tb = benign = 0
    for f in glob.glob(os.path.join(d, "*_minecraft.log")):
        t = open(f, errors="replace").read()
        kicks += t.count("out_of_order_chat")
    for f in glob.glob(os.path.join(d, "ZS_*.log")):
        if f.endswith("_minecraft.log"):
            continue
        t = open(f, errors="replace").read()
        disc += t.count("disconnected")
        tb += t.count("Traceback (most recent call last)")
        benign += t.count("_cuda_bindings_redirector.pth:")
    quar = sum(int(r.get("gov_quarantined") or 0) for r in lin["rows"])
    missing = sum(int(r.get("gov_missing") or 0) for r in lin["rows"])
    print(f"LIFE L{L}: games with a result row {len(lin['rows'])}/15; launcher replays {sum(replays.values())} {replays or ''}; "
          f"grow records {len(grows)}, leaves added {added}; games on an empty pool {empty_pool_games}; quarantines {quar}; "
          f"missing functions {missing}; server kicks {kicks}; 'disconnected' in job logs {disc}; "
          f"non-benign tracebacks {max(0, tb - benign)}; slash commands in model code {len(lin['slash'])}")
    for b in bad:
        print("   health:", b)
print("SYS: not started yet (its health is checked after it ends)")
