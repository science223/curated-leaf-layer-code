#!/usr/bin/env python3
"""E11 world selection (预注册_E11_恶劣出生点孤立评测_2026-10-06 §2), applied to the screening records of all 240 seeds.
Categories by the spawn biome; per category the 2 smallest seeds; a category with < 2 is filled from the others in turn
(category order desert, badlands, snowy, water; smallest seed first); fewer than 6 in all -> E11 does not run.
Writes ~/e11_tools/selected_seeds.json and prints the table. Usage: python3 e11_select.py"""
import glob
import json
import os

H = os.path.expanduser("~/e11_tools")
SNOWY = {"snowy_plains", "snowy_taiga", "snowy_beach", "ice_spikes", "grove", "snowy_slopes", "frozen_peaks", "jagged_peaks",
         "frozen_river", "frozen_ocean", "deep_frozen_ocean"}
ORDER = ["desert", "badlands", "snowy", "water"]


def category(b):
    if not b or b == "?":
        return None
    if b == "desert":
        return "desert"
    if "badlands" in b:
        return "badlands"
    if b in SNOWY:
        return "snowy"
    if "ocean" in b or b in ("beach", "stony_shore", "river"):
        return "water"
    return None


recs = {}
for f in sorted(glob.glob(os.path.join(H, "screen_w*.jsonl"))):
    for line in open(f):
        r = json.loads(line)
        recs[r["seed"]] = r
want = set(range(20260903000, 20260903240))
missing = sorted(want - set(recs))
errors = sorted(s for s, r in recs.items() if r.get("error") or not r.get("biome"))
print(f"screened {len(recs)} of 240; missing {len(missing)}; errors {len(errors)} {errors[:10]}")
by = {c: sorted(s for s, r in recs.items() if not r.get("error") and category(r.get("biome")) == c) for c in ORDER}
for c in ORDER:
    print(f"  {c:8s} {len(by[c]):3d}: {[str(s)[-3:] for s in by[c]]}")
pick = {c: by[c][:2] for c in ORDER}
short = sum(2 - len(v) for v in pick.values())
k = 0
while short > 0 and any(len(by[c]) > len(pick[c]) for c in ORDER):
    c = ORDER[k % 4]
    k += 1
    if len(by[c]) > len(pick[c]):
        pick[c].append(by[c][len(pick[c])])
        short -= 1
sel = [{"seed": s, "category": c, "biome": recs[s]["biome"], "spawn": recs[s]["spawn"], "below": recs[s].get("below"),
        "logs32": recs[s].get("logs32"), "water16": recs[s].get("water16")} for c in ORDER for s in pick[c]]
if missing:
    raise SystemExit("screening incomplete; nothing selected")
if len(sel) < 6:
    raise SystemExit(f"only {len(sel)} worlds in the four categories: E11 does not run (预注册 §2)")
json.dump(sel, open(os.path.join(H, "selected_seeds.json"), "w"), indent=1)
print("selected:")
for x in sel:
    print(f"  {x['seed']} {x['category']:8s} biome={x['biome']} spawn={x['spawn']} below={x['below']} logs32={x['logs32']} water16={x['water16']}")
