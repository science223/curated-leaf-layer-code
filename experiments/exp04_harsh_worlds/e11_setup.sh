#!/usr/bin/env bash
# E11 (预注册_E11_恶劣出生点孤立评测_2026-10-06, 修订 1) on slac002: one directory per arm copied from ~/V30_eval_scr
# (obtain.js 6f13da60, as E6's r12_setup.sh), the two pools copied from the 09-27 evaluation (md5 checked), and the
# shuffled job list of 8 worlds x 3 arms (each job plays the three tasks of one world). Never overwrites.
set -eu
SRC=~/V30_eval_scr
[ "$(md5sum < $SRC/voyager/control_primitives_extra/obtain.js | cut -c1-8)" = 6f13da60 ] || { echo "source obtain.js md5 differs"; exit 4; }
for A in hand scr sys; do D=~/V30_e11_$A; [ -e $D ] && { echo "$D exists"; exit 1; }; done
for A in hand scr sys; do
  D=~/V30_e11_$A
  mkdir -p $D && (cd $SRC && tar cf - --exclude=./_cc0924/obtain_mc_work --exclude=./_cc0924/obtain_mc_keep \
    --exclude="./_cc0924/eval_*" --exclude="./_cc0924/smoke_*" --exclude="./_gov/ledger_*" .) | (cd $D && tar xf -)
  echo "$A obtain=$(md5sum < $D/voyager/control_primitives_extra/obtain.js | cut -c1-8) harness=$(md5sum < $D/_cc0924/test_obtain_mc.py | cut -c1-8)"
done
cp ~/V30_eval_scr/_gov/pool_screened.json ~/V30_e11_scr/_gov/pool_screened.json
cp ~/V30_eval_sys/_gov/pool_system.json ~/V30_e11_sys/_gov/pool_system.json
check() { m=$(md5sum < "$1" | cut -c1-8); [ "$m" = "$2" ] || { echo "$1 md5 $m != $2"; exit 5; }; echo "$1 $m"; }
check ~/V30_e11_scr/_gov/pool_screened.json 06cdfe20
check ~/V30_e11_sys/_gov/pool_system.json 83852953
python3 - <<'PY'
import json, os, random
sel = json.load(open(os.path.expanduser("~/e11_tools/selected_seeds.json")))
seeds = [str(x["seed"]) for x in sel]
assert len(seeds) >= 6
spec = {"hand": ("hand", "0", ""), "scr": ("gov", "0", "pool_screened.json"), "sys": ("gov", "1", "pool_system.json")}
jobs = [f"V30_e11_{a}:{a}:{m}:{o}:{p}:{s}" for a, (m, o, p) in spec.items() for s in seeds]
random.Random(20261006).shuffle(jobs)
open(os.path.expanduser("~/e11_jobs.txt"), "w").write("\n".join(jobs) + "\n")
print(len(jobs), "jobs; first:", jobs[:3])
PY
