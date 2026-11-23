#!/usr/bin/env bash
# E6 reruns (预注册_匹配重跑与新种子_2026-09-29, "替换规则"): every job that had not ended by 07:43:00 on 09-29 and had
# started before the degraded window ended (13:05:26, the last write of the sensor_cp experiments on slac002's Windows
# side; E6 step timeouts per hour were back to 0-2 % from 13:00) is played again in a fresh directory ~/V30_r12r_<group>,
# built exactly like r12_setup.sh (copied from ~/V30_eval_scr, obtain.js 6f13da60, pools md5-checked). Writes
# ~/r12r_jobs.txt (degraded jobs in their original order, directories renamed) and ~/r12r_queue.sh (r12_queue.sh with
# its own job list and log). Never overwrites; refuses while the sensor_cp experiments, E6 or E7 are running.
set -eu
CUT_END="2026-09-29 07:43:00"; CUT_START="2026-09-29 13:05:26"
pgrep -f "[r]12_queue.sh|[r]12r_queue.sh|[e]7_queue.sh|[r]un_e7_lineage" > /dev/null && { echo "E6/E7 still running"; exit 3; }
SRC=~/V30_eval_scr
[ "$(md5sum < $SRC/voyager/control_primitives_extra/obtain.js | cut -c1-8)" = 6f13da60 ] || { echo "source obtain.js md5 differs"; exit 4; }
python3 - "$CUT_END" "$CUT_START" <<'PY'
import os, re, sys
cut_end, cut_start = sys.argv[1], sys.argv[2]
start, end, slot_job = {}, {}, {}
for line in open(os.path.expanduser("~/r12_queue.log")):
    m = re.match(r"(\S+ \S+) start slot=(\d+) (\S+) avail=", line)
    if m:
        slot_job[m.group(2)] = m.group(3); start[m.group(3)] = m.group(1)
    m = re.match(r"(\S+ \S+) end slot=(\d+)", line)
    if m and m.group(2) in slot_job:
        end[slot_job.pop(m.group(2))] = m.group(1)
order = [l.strip() for l in open(os.path.expanduser("~/r12_jobs.txt")) if l.strip() and not l.startswith("#")]
assert len(order) == 65 and set(order) == set(start) == set(end), "queue log does not cover all 65 jobs"
bad = [j for j in order if end[j] >= cut_end and start[j] < cut_start]
jobs = [j.replace("V30_r12_", "V30_r12r_", 1) for j in bad]
open(os.path.expanduser("~/r12r_jobs.txt"), "w").write("\n".join(jobs) + "\n")
groups = sorted({j.split(":")[1] for j in bad})
open(os.path.expanduser("~/r12r_groups.txt"), "w").write(" ".join(groups) + "\n")
print(len(bad), "degraded jobs of 65; by group:", {g: sum(1 for j in bad if j.split(":")[1] == g) for g in groups})
PY
GRPS=$(cat ~/r12r_groups.txt)
for A in $GRPS; do D=~/V30_r12r_$A; [ -e $D ] && { echo "$D exists"; exit 1; }; done
for A in $GRPS; do
  D=~/V30_r12r_$A
  mkdir -p $D && (cd $SRC && tar cf - --exclude=./_cc0924/obtain_mc_work --exclude=./_cc0924/obtain_mc_keep \
    --exclude="./_cc0924/eval_*" --exclude="./_cc0924/smoke_*" --exclude="./_gov/ledger_*" .) | (cd $D && tar xf -)
  O=$(md5sum < $D/voyager/control_primitives_extra/obtain.js | cut -c1-8); H=$(md5sum < $D/_cc0924/test_obtain_mc.py | cut -c1-8)
  [ "$O" = 6f13da60 ] && [ "$H" = "$(md5sum < ~/V30_r12_$A/_cc0924/test_obtain_mc.py | cut -c1-8)" ] || { echo "$D obtain $O / harness $H differ from the original run"; exit 6; }
  echo "$A obtain=$O harness=$H"
done
check() { m=$(md5sum < "$1" | cut -c1-8); [ "$m" = "$2" ] || { echo "$1 md5 $m != $2"; exit 5; }; echo "$1 $m"; }
for A in $GRPS; do case $A in
  rand_n) cp ~/V30_eval_rand/_gov/pool_random.json ~/V30_r12r_rand_n/_gov/; check ~/V30_r12r_rand_n/_gov/pool_random.json 95076f30 ;;
  scr_n) cp ~/V30_eval_scr/_gov/pool_screened.json ~/V30_r12r_scr_n/_gov/; check ~/V30_r12r_scr_n/_gov/pool_screened.json 06cdfe20 ;;
  sys_n) cp ~/V30_eval_sys/_gov/pool_system.json ~/V30_r12r_sys_n/_gov/; check ~/V30_r12r_sys_n/_gov/pool_system.json 83852953 ;;
esac; done
sed -e 's#^Q=~/r12_jobs.txt; MAXJ=${MAXJ:-6}; LOG=~/r12_queue.log#Q=~/r12r_jobs.txt; MAXJ=${MAXJ:-6}; LOG=~/r12r_queue.log#' \
    -e 's#r12 queue start#r12r queue start#; s#r12 queue done#r12r queue done#' \
    -e 's#~/r12_queue.lock#~/r12r_queue.lock#; s#r12 queue already running#r12r queue already running#' ~/r12_queue.sh > ~/r12r_queue.sh
grep -q "Q=~/r12r_jobs.txt" ~/r12r_queue.sh && grep -q "LOG=~/r12r_queue.log" ~/r12r_queue.sh && grep -q "r12r_queue.lock" ~/r12r_queue.sh \
  || { echo "queue copy failed"; exit 7; }
echo "r12r_queue.sh $(md5sum < ~/r12r_queue.sh | cut -c1-8); jobs $(wc -l < ~/r12r_jobs.txt)"
