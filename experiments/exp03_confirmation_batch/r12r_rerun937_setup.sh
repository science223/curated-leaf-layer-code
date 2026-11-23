#!/usr/bin/env bash
# E6 round-3 rerun of the voided job hand_n 20260902937 (预注册_匹配重跑与新种子_2026-09-29, 10-01 05:57 entry):
# archive its rows / stdout / obtain log to ~/r12r3_void_937/ (counts only, no outcome field is printed), write
# ~/r12r2_jobs.txt with this one job, check that ~/r12r2_queue.sh is the reviewed 694d1bdb, remove the E6 brake
# (~/r12r_pause; the E7 brake ~/e7b_pause stays), check ports. Does NOT start the queue (that is done by WMI).
set -eu
H=$HOME; D=$H/V30_r12r_hand_n/_cc0924; A=$H/r12r3_void_937; SEED=20260902937
JOB="V30_r12r_hand_n:hand_n:hand:0::$SEED"
grep -qx "$JOB" $H/r12r_jobs.txt || { echo "job line not in r12r_jobs.txt"; exit 2; }
[ "$(pgrep -fc '[t]est_obtain_mc.py')" = 0 ] || { echo "obtain processes still running"; exit 2; }
[ "$(md5sum < $H/r12r2_queue.sh | cut -c1-8)" = 694d1bdb ] || { echo "r12r2_queue.sh md5 mismatch"; exit 2; }
[ -e $A ] && { echo "$A exists already"; exit 2; }
mkdir -p $A
python3 - "$D/eval_hand_n.jsonl" "$A/eval_hand_n_937_rows.jsonl" "$SEED" <<'PY'
import json, sys
src, dst, seed = sys.argv[1], sys.argv[2], sys.argv[3]
def seed_of(o):
    s = o.get("seed")
    if s is None and isinstance(o.get("row"), dict):
        s = o["row"].get("seed")
    return None if s is None else str(s)
keep, moved = [], []
for line in open(src):
    try:
        o = json.loads(line)
    except Exception:
        keep.append(line)
        continue
    (moved if seed_of(o) == seed else keep).append(line)
open(dst, "w").writelines(moved)
open(src, "w").writelines(keep)
print(f"rows moved {len(moved)} (job_done {sum(1 for x in moved if json.loads(x).get('event') == 'job_done')}), rows kept {len(keep)}")
PY
for f in eval_hand_n_937.stdout obtain_mc_hand_n_937.log; do [ -e $D/$f ] && mv $D/$f $A/ && echo "moved $f"; done
echo "files still named *937* in the directory: $(ls $D | grep -c 937 || true)"
echo "$JOB" > $H/r12r2_jobs.txt
rm -f $H/r12r_pause
echo "brakes: r12r_pause=$([ -e $H/r12r_pause ] && echo on || echo off) e7b_pause=$([ -e $H/e7b_pause ] && echo on || echo off)"
echo "listeners on 25720/3220: $(ss -ltn | grep -cE ':(25720|3220) ' || true)"
echo "setup done $(date '+%F %T')"
