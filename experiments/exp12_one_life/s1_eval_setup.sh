#!/usr/bin/env bash
# slac002, after s1_select.py froze the pools: one directory per group (the G lesson: never share world dirs),
# copied from ~/V30_eval_scr (obtain.js 6f13da60), + a shuffled job list. 预注册_从零积累阶段一 §6, v2.
set -eu
ARMS="prim_re w1t40 w1t80 w1t160 w2t40 w2t80 w2t161 scr_re gen w1t40g w1t80g w1t160g w2t40g"
P=~/V30_s1_pools
# the screened pool the five-arm evaluation actually ran (09-27 official tournament); the local WSL copy d8b6c4c6 was stale
[ "$(md5sum < ~/V30_eval_scr/_gov/pool_screened.json | cut -c1-8)" = 06cdfe20 ] || { echo "pool_screened md5 differs"; exit 4; }
cp ~/V30_eval_scr/_gov/pool_screened.json $P/pool_scr_re.json
for A in $ARMS; do
  D=~/V30_s1e_$A; [ -e $D ] && { echo "$D exists"; exit 1; }
  mkdir -p $D && (cd ~/V30_eval_scr && tar cf - --exclude=./_cc0924/obtain_mc_work --exclude=./_cc0924/obtain_mc_keep \
    --exclude="./_cc0924/eval_*" --exclude="./_cc0924/smoke_*" --exclude="./_gov/ledger_*" .) | (cd $D && tar xf -)
  [ "$A" != prim_re ] && cp $P/pool_$A.json $D/_gov/pool_$A.json
  O=$(md5sum < $D/voyager/control_primitives_extra/obtain.js | cut -c1-8); [ "$O" = 6f13da60 ] || { echo "$D obtain.js md5 $O != 6f13da60"; exit 6; }
  echo "$A obtain=$O pool=$( [ -f $D/_gov/pool_$A.json ] && md5sum < $D/_gov/pool_$A.json | cut -c1-8)"
done
python3 - <<'PY'
import random, os
arms = "prim_re w1t40 w1t80 w1t160 w2t40 w2t80 w2t161 scr_re gen w1t40g w1t80g w1t160g w2t40g".split()
jobs = []
for a in arms:
    mode, pool = ("prim", "") if a == "prim_re" else ("gov", f"pool_{a}.json")
    for s in ("20260902007", "20260902008", "20260902009", "20260902012", "20260902013"):
        jobs.append(f"V30_s1e_{a}:{a}:{mode}:0:{pool}:{s}")
random.Random(20260928).shuffle(jobs)
open(os.path.expanduser("~/s1_eval_jobs.txt"), "w").write("\n".join(jobs) + "\n")
print(len(jobs), "jobs; first:", jobs[:3])
PY
cat > ~/s1_eval_queue.sh <<'SH'
#!/usr/bin/env bash
# stage one + E2 evaluation queue. Job line: DIR:ARM:MODE:ONLINE:POOL:SEED; one job = one seed x 3 scenes, one
# single-worker process; at most 6 at once and only while >= 5 GB is free (leaves room for the demo selftest).
# Ports 25700+s / 3200+s (demo: 25790/3790/3791/8790; G: 25671-3/3171-3; old eval: 25650+s).
Q=~/s1_eval_jobs.txt; MAXJ=${MAXJ:-6}; LOG=~/s1_eval_queue.log
exec 8>~/s1_eval_queue.lock; flock -n 8 || { echo "s1 eval queue already running"; exit 3; }
export JAVA17=/home/user/Voyager_0911/toolchain/java-17-openjdk-amd64/bin/java OBTAIN_WORKERS=1 OBTAIN_WORKER_ID=0 OBTAIN_MAX_CALLS=8 GOV_RETIRE_K=2
mapfile -t JOBS < <(grep -v '^#' $Q | grep -v '^$')
declare -A SLOTPID; i=0
echo "$(date '+%F %T') s1 eval queue start jobs=${#JOBS[@]} maxj=$MAXJ" >> $LOG
while [ $i -lt ${#JOBS[@]} ] || [ ${#SLOTPID[@]} -gt 0 ]; do
  for s in "${!SLOTPID[@]}"; do if ! kill -0 "${SLOTPID[$s]}" 2>/dev/null; then echo "$(date '+%F %T') end slot=$s" >> $LOG; unset "SLOTPID[$s]"; fi; done
  avail=$(awk '/MemAvailable/{print int($2/104857.6)}' /proc/meminfo)
  if [ $i -lt ${#JOBS[@]} ] && [ ${#SLOTPID[@]} -lt $MAXJ ] && [ "$avail" -ge 50 ]; then
    for s in 0 1 2 3 4 5 6 7; do [ -z "${SLOTPID[$s]}" ] && break; done
    J=${JOBS[$i]}; i=$((i+1)); IFS=':' read -r DIR ARM MODE ONLINE POOL SEED <<< "$J"
    (
      cd ~/$DIR || exit 2
      export OBTAIN_SEEDS=$SEED OBTAIN_LEAF_SOURCE=$MODE GOV_ONLINE=$ONLINE OBTAIN_EXPERIMENT_ID=s1eval_$ARM
      export OBTAIN_MC_PORT=$((25700 + s)) OBTAIN_BR_PORT=$((3200 + s))
      export OBTAIN_RESULT_JSONL=$HOME/$DIR/_cc0924/eval_$ARM.jsonl GOV_LEDGER_DIR=$HOME/$DIR/_gov/ledger_eval
      [ -n "$POOL" ] && export GOV_CANDIDATES=$HOME/$DIR/_gov/$POOL
      exec ~/$DIR/venv/bin/python _cc0924/test_obtain_mc.py --phase full --worker 0 > _cc0924/eval_${ARM}_${SEED: -3}.stdout 2>&1
    ) &
    SLOTPID[$s]=$!; echo "$(date '+%F %T') start slot=$s $J avail=$avail" >> $LOG; sleep 60
  else sleep 30; fi
done
echo "$(date '+%F %T') s1 eval queue done" >> $LOG
SH
chmod +x ~/s1_eval_queue.sh; bash -n ~/s1_eval_queue.sh && echo queue_ok
for p in 25700 25701 25702 25703 25704 25705 3200 3201 3202 3203 3204 3205; do (ss -ltn | grep -q ":$p ") && echo "PORT_BUSY $p"; done; echo ports_checked
