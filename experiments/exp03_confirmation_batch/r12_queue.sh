#!/usr/bin/env bash
# E6 queue (预注册_匹配重跑与新种子_2026-09-29). Job line: DIR:ARM:MODE:ONLINE:POOL:SEED; one job = one seed x 3 scenes,
# one single-worker process; at most MAXJ at once and only while >= 5 GB is free.
# Ports 25720+s / 3220+s (stage one 25700-25705, other session 25620/3120, demo 25790/3790/3791/8790).
# Online jobs get one experiment id and one ledger directory per seed.
Q=~/r12_jobs.txt; MAXJ=${MAXJ:-6}; LOG=~/r12_queue.log
exec 8>~/r12_queue.lock; flock -n 8 || { echo "r12 queue already running"; exit 3; }
export JAVA17=/home/user/Voyager_0911/toolchain/java-17-openjdk-amd64/bin/java OBTAIN_WORKERS=1 OBTAIN_WORKER_ID=0 OBTAIN_MAX_CALLS=8 GOV_RETIRE_K=2
mapfile -t JOBS < <(grep -v '^#' $Q | grep -v '^$')
declare -A SLOTPID; i=0
echo "$(date '+%F %T') r12 queue start jobs=${#JOBS[@]} maxj=$MAXJ" >> $LOG
while [ $i -lt ${#JOBS[@]} ] || [ ${#SLOTPID[@]} -gt 0 ]; do
  for s in "${!SLOTPID[@]}"; do if ! kill -0 "${SLOTPID[$s]}" 2>/dev/null; then echo "$(date '+%F %T') end slot=$s" >> $LOG; unset "SLOTPID[$s]"; fi; done
  avail=$(awk '/MemAvailable/{print int($2/104857.6)}' /proc/meminfo)
  if [ $i -lt ${#JOBS[@]} ] && [ ${#SLOTPID[@]} -lt $MAXJ ] && [ "$avail" -ge 50 ]; then
    for s in 0 1 2 3 4 5 6 7; do [ -z "${SLOTPID[$s]}" ] && break; done
    J=${JOBS[$i]}; i=$((i+1)); IFS=':' read -r DIR ARM MODE ONLINE POOL SEED <<< "$J"
    (
      cd ~/$DIR || exit 2
      if [ "$ONLINE" = 1 ]; then EID=r12_${ARM}_${SEED: -3}; else EID=r12_$ARM; fi
      export OBTAIN_SEEDS=$SEED OBTAIN_LEAF_SOURCE=$MODE GOV_ONLINE=$ONLINE OBTAIN_EXPERIMENT_ID=$EID
      export OBTAIN_MC_PORT=$((25720 + s)) OBTAIN_BR_PORT=$((3220 + s))
      export OBTAIN_RESULT_JSONL=$HOME/$DIR/_cc0924/eval_$ARM.jsonl GOV_LEDGER_DIR=$HOME/$DIR/_gov/ledger_${SEED: -3}
      export OBTAIN_LOG_PATH=$HOME/$DIR/_cc0924/obtain_mc_${ARM}_${SEED: -3}.log
      [ -n "$POOL" ] && export GOV_CANDIDATES=$HOME/$DIR/_gov/$POOL
      exec ~/$DIR/venv/bin/python _cc0924/test_obtain_mc.py --phase full --worker 0 > _cc0924/eval_${ARM}_${SEED: -3}.stdout 2>&1
    ) &
    SLOTPID[$s]=$!; echo "$(date '+%F %T') start slot=$s $J avail=$avail" >> $LOG; sleep 60
  else sleep 30; fi
done
echo "$(date '+%F %T') r12 queue done" >> $LOG
