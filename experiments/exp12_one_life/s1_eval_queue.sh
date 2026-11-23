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
