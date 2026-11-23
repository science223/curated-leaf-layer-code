#!/usr/bin/env bash
# E7 setup (预注册_账本消融_2026-09-29): one directory per (condition, lineage), copied from ~/V30_eval_online_L1 (the
# online arm: pool_online.json fa4b078b and the lineage orders), with the repaired executor and harness copied from
# ~/V30_e7_smoke (checked by md5), then the v4 executor from ~/e7_v4_src/obtain.js (late events rejected, b3f48363).
# Never overwrites. Writes the lineage job list ~/e7_jobs.txt (shuffled).
set -eu
SRC=~/V30_eval_online_L1
FIX=~/V30_e7_smoke
V4=~/e7_v4_src/obtain.js
[ "$(md5sum < $V4 | cut -c1-8)" = b3f48363 ] || { echo "v4 obtain.js md5 differs"; exit 4; }
[ "$(md5sum < $FIX/_cc0924/test_obtain_mc.py | cut -c1-8)" = 41485cf8 ] || { echo "repaired harness md5 differs"; exit 4; }
[ "$(md5sum < $SRC/_gov/pool_online.json | cut -c1-8)" = fa4b078b ] || { echo "pool_online md5 differs"; exit 4; }
for C in full off retire reorder quar; do for L in 1 2 3; do
  [ -e ~/V30_e7_${C}_L$L ] && { echo "~/V30_e7_${C}_L$L exists"; exit 1; }
done; done
for C in full off retire reorder quar; do for L in 1 2 3; do
  D=~/V30_e7_${C}_L$L
  mkdir -p $D && (cd $SRC && tar cf - --exclude=./_cc0924/obtain_mc_work --exclude=./_cc0924/obtain_mc_keep \
    --exclude="./_cc0924/eval_*" --exclude="./_cc0924/online_*" --exclude="./_cc0924/*.jsonl" --exclude="./_cc0924/*.stdout" \
    --exclude="./_cc0924/*.log" --exclude="./_gov/ledger_*" .) | (cd $D && tar xf -)
  cp $V4 $D/voyager/control_primitives_extra/obtain.js
  cp $FIX/_cc0924/test_obtain_mc.py $D/_cc0924/test_obtain_mc.py
  # the analysis takes lineage L's order from G's own L directory (Codex r3): the copied order file must equal it
  cmp -s $D/_gov/lineage_$L.txt ~/V30_eval_online_L$L/_gov/lineage_$L.txt || { echo "$C L$L order differs from G L$L"; exit 5; }
  echo "$C L$L obtain=$(md5sum < $D/voyager/control_primitives_extra/obtain.js | cut -c1-8) harness=$(md5sum < $D/_cc0924/test_obtain_mc.py | cut -c1-8) pool=$(md5sum < $D/_gov/pool_online.json | cut -c1-8) order=$(md5sum < $D/_gov/lineage_$L.txt | cut -c1-8)"
done; done
python3 - <<'PY'
import os, random
jobs = [f"{c}:{l}" for c in ("full", "off", "retire", "reorder", "quar") for l in (1, 2, 3)]
random.Random(20260929).shuffle(jobs)
open(os.path.expanduser("~/e7_jobs.txt"), "w").write("\n".join(jobs) + "\n")
print(len(jobs), "lineage jobs:", jobs)
PY
