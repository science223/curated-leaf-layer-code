#!/usr/bin/env bash
# E14 setup on slac002 (预注册_E14_孤立评测谱系叶子_2026-10-13): one directory per source lineage, copied from
# ~/V30_e7_quar_L1 (E7's quarantine condition: obtain.js b3f48363, harness 41485cf8, the online arm's lineage orders),
# without its results, ledgers or world directories. Its candidate pool _gov/pool_online.json is replaced by the final
# pool of one lifecycle lineage: E12 (DeepSeek) or E13 (Qwen3.8-27B), lineage 1, 2 or 3. L9 is the smoke lineage: the
# pool of lineage 1 on the two development cells of the E12/E13 smoke. Never overwrites. Writes ~/e14_jobs.txt.
set -eu
SRC=~/V30_e7_quar_L1
[ "$(md5sum < $SRC/voyager/control_primitives_extra/obtain.js | cut -c1-8)" = b3f48363 ] || { echo "obtain.js differs"; exit 4; }
[ "$(md5sum < $SRC/_cc0924/test_obtain_mc.py | cut -c1-8)" = 41485cf8 ] || { echo "harness differs"; exit 4; }
for E in e12 e13; do for L in 1 2 3 9; do
  [ -e ~/V30_e14_${E}_L$L ] && { echo "~/V30_e14_${E}_L$L exists"; exit 1; }
done; done
for E in e12 e13; do for L in 1 2 3; do
  P=~/wt_$E/runs_0911/${E}n_life_L$L/_gov
  cmp -s $P/pool_e3.json $P/pools/g15.json || { echo "$E L$L: pool_e3.json differs from the pool after game 15"; exit 5; }
done; done
for E in e12 e13; do for L in 1 2 3 9; do
  S=$L; [ $L = 9 ] && S=1
  D=~/V30_e14_${E}_L$L
  mkdir -p $D && (cd $SRC && tar cf - --exclude=./_cc0924/obtain_mc_work --exclude=./_cc0924/obtain_mc_keep \
    --exclude="./_cc0924/e7_*" --exclude="./_cc0924/obtain_mc_e7_*" --exclude="./_cc0924/*.jsonl" \
    --exclude="./_cc0924/*.stdout" --exclude="./_cc0924/*.log" --exclude="./_gov/ledger_*" --exclude=./_gov/e7.lock .) \
    | (cd $D && tar xf -)
  cp ~/wt_$E/runs_0911/${E}n_life_L$S/_gov/pool_e3.json $D/_gov/pool_online.json
  if [ $L = 9 ]; then
    printf '20260902010 golden_sword\n20260902011 compass\n' > $D/_gov/lineage_9.txt
  else
    cmp -s $D/_gov/lineage_$L.txt ~/V30_eval_online_L$L/_gov/lineage_$L.txt || { echo "$E L$L order differs from G L$L"; exit 6; }
  fi
  echo "$E L$L obtain=$(md5sum < $D/voyager/control_primitives_extra/obtain.js | cut -c1-8)" \
    "harness=$(md5sum < $D/_cc0924/test_obtain_mc.py | cut -c1-8) pool=$(md5sum < $D/_gov/pool_online.json | cut -c1-8)" \
    "fp=$(sha256sum < $D/_gov/pool_online.json | cut -c1-12) order=$(md5sum < $D/_gov/lineage_$L.txt | cut -c1-8)"
done; done
python3 - <<'PY'
import os, random
jobs = [f"{e}:{l}" for e in ("e12", "e13") for l in (1, 2, 3)]
random.Random(20261013).shuffle(jobs)
open(os.path.expanduser("~/e14_jobs.txt"), "w").write("\n".join(jobs) + "\n")
print(len(jobs), "lineage jobs:", jobs)
PY
