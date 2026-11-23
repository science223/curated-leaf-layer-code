#!/usr/bin/env bash
# E6 (预注册_匹配重跑与新种子_2026-09-29): one directory per group copied from ~/V30_eval_scr (obtain.js 6f13da60),
# the three pools copied from the 09-27 five-arm evaluation (md5 checked), and a shuffled job list. Never overwrites.
set -eu
SRC=~/V30_eval_scr
[ "$(md5sum < $SRC/voyager/control_primitives_extra/obtain.js | cut -c1-8)" = 6f13da60 ] || { echo "source obtain.js md5 differs"; exit 4; }
for A in hand_m hand_n rand_n scr_n sys_n; do
  D=~/V30_r12_$A; [ -e $D ] && { echo "$D exists"; exit 1; }
done
for A in hand_m hand_n rand_n scr_n sys_n; do
  D=~/V30_r12_$A
  mkdir -p $D && (cd $SRC && tar cf - --exclude=./_cc0924/obtain_mc_work --exclude=./_cc0924/obtain_mc_keep \
    --exclude="./_cc0924/eval_*" --exclude="./_cc0924/smoke_*" --exclude="./_gov/ledger_*" .) | (cd $D && tar xf -)
  O=$(md5sum < $D/voyager/control_primitives_extra/obtain.js | cut -c1-8)
  [ "$O" = 6f13da60 ] || { echo "$D obtain.js md5 $O != 6f13da60"; exit 6; }
  echo "$A obtain=$O"
done
cp ~/V30_eval_rand/_gov/pool_random.json ~/V30_r12_rand_n/_gov/pool_random.json
cp ~/V30_eval_scr/_gov/pool_screened.json ~/V30_r12_scr_n/_gov/pool_screened.json
cp ~/V30_eval_sys/_gov/pool_system.json ~/V30_r12_sys_n/_gov/pool_system.json
check() { m=$(md5sum < "$1" | cut -c1-8); [ "$m" = "$2" ] || { echo "$1 md5 $m != $2"; exit 5; }; echo "$1 $m"; }
check ~/V30_r12_rand_n/_gov/pool_random.json 95076f30
check ~/V30_r12_scr_n/_gov/pool_screened.json 06cdfe20
check ~/V30_r12_sys_n/_gov/pool_system.json 83852953
python3 - <<'PY'
import os, random
orig = ["20260902007", "20260902008", "20260902009", "20260902012", "20260902013"]
new = ("20260902169 20260902250 20260902342 20260902397 20260902474 20260902491 20260902546 20260902574 "
       "20260902593 20260902619 20260902630 20260902714 20260902781 20260902853 20260902937").split()
assert len(new) == 15 and not set(new) & set(orig)
jobs = [f"V30_r12_hand_m:hand_m:hand:0::{s}" for s in orig]
spec = {"hand_n": ("hand", "0", ""), "rand_n": ("gov", "0", "pool_random.json"),
        "scr_n": ("gov", "0", "pool_screened.json"), "sys_n": ("gov", "1", "pool_system.json")}
for a, (mode, online, pool) in spec.items():
    jobs += [f"V30_r12_{a}:{a}:{mode}:{online}:{pool}:{s}" for s in new]
random.Random(20260929).shuffle(jobs)
open(os.path.expanduser("~/r12_jobs.txt"), "w").write("\n".join(jobs) + "\n")
print(len(jobs), "jobs; first:", jobs[:3])
PY
