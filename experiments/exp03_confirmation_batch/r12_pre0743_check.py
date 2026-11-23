#!/usr/bin/env python3
"""E6 additional check kept by the replacement rule (预注册_匹配重跑与新种子_2026-09-29.md, "降级时段" item 2 and
"替换规则"): recompute N1, N2 and M1 using only the jobs of the ORIGINAL batch that had finished before 2026-09-29 07:43:00
(job intervals from ~/r12_queue.log; data from ~/V30_r12_<arm>, which for these jobs equal the merged data).
Uses the preregistered analysis' own functions (~/r12_analyze.py -> ~/eval_analyze.py); prints the comparable seeds/cells."""
import collections
import importlib.util
import os
import re
import sys

HOME = os.path.expanduser("~")
spec = importlib.util.spec_from_file_location("ra", os.path.join(HOME, "r12_analyze.py"))
ra = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ra)
CUT = "2026-09-29 07:43:00"

opened, pre = {}, collections.defaultdict(set)
for x in open(os.path.join(HOME, "r12_queue.log"), errors="replace"):
    m = re.search(r" start slot=(\d+) (\S+)", x)
    if m:
        opened[m.group(1)] = m.group(2)
        continue
    m = re.search(r" end slot=(\d+)", x)
    if m and m.group(1) in opened:
        job = opened.pop(m.group(1))
        if x[:19] < CUT:
            d, arm, mode, online, pool, seed = job.split(":")
            pre[arm].add(seed[-3:])
print("jobs finished before", CUT, {a: len(s) for a, s in sorted(pre.items())}, "total", sum(len(s) for s in pre.values()))
for a, s in sorted(pre.items()):
    print(f"  {a}: {sorted(s)}")
D = ra.load_all()
out = print
n1 = sorted(pre["scr_n"] & pre["rand_n"])
n2 = sorted(pre["sys_n"] & pre["hand_n"])
m1 = sorted(pre["hand_m"])
print(f"\n== additional check: only jobs finished before 07:43 (N1 seeds {len(n1)}, N2 seeds {len(n2)}, M1 seeds {len(m1)})")
ra.report_seeds("N1 screened_n vs random_n", D["scr_n"], D["rand_n"], "scr_n", "rand_n", n1, out)
ra.report_cells("N1 cells", D["scr_n"], D["rand_n"], "scr_n", "rand_n", n1, out)
ra.report_seeds("N2 system_n vs hand_n", D["sys_n"], D["hand_n"], "sys_n", "hand_n", n2, out)
ra.report_cells("N2 cells", D["sys_n"], D["hand_n"], "sys_n", "hand_n", n2, out)
ra.report_cells("M1 system (09-27) vs hand_m", D["sys"], D["hand_m"], "sys", "hand_m", m1, out)
