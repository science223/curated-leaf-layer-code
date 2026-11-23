#!/usr/bin/env bash
# E11 queue, local variant (预注册_E11 修订 3, 's machine): slac002's e11_queue.sh with two changes only:
#   1. Java 17 is the system one (JAVA17 below);
#   2. a brake: while ~/e11_brake exists no new job is launched, and the queue ends once the running jobs end.
# Plays the jobs of ~/e11_jobs.txt whose index is in [$1, $2), at most MAXJ at a time, each job = one arm on one world
# (three tasks). Logs "start job=<j>" / "end job=<j>" to ~/e11_queue.log (the analysis takes the job intervals for the
# clock check from it). Usage: e11_queue.sh <from> <to>
Q=~/e11_jobs.txt; MAXJ=${MAXJ:-4}; LOG=~/e11_queue.log; FROM=$1; TO=$2
exec 8>~/e11_queue.lock; flock -n 8 || { echo "e11 queue already running"; exit 3; }
export JAVA17=/usr/lib/jvm/java-17-openjdk-amd64/bin/java OBTAIN_WORKERS=1 OBTAIN_WORKER_ID=0 OBTAIN_MAX_CALLS=8 GOV_RETIRE_K=2
unset GOV_QUIESCE_MS GOV_QUIESCE_CAP_MS GOV_LATE_FENCE GOV_LEDGER_MODE GOV_RETIRE_MODE GOV_FALLBACK GOV_LEDGER_LINEAGE GOV_RESUME GOV_POOL_APPEND_ONLY GOV_LEDGER_FILE GOV_LEDGER_LINEAGE_ID GOV_ALLOW_EMPTY_POOL
mapfile -t ALL < <(grep -v '^#' $Q | grep -v '^$')
JOBS=("${ALL[@]:$FROM:$((TO - FROM))}")
declare -A SLOTPID SLOTJOB; i=0
echo "$(date '+%F %T') e11 queue start jobs=${#JOBS[@]} from=$FROM to=$TO maxj=$MAXJ" >> $LOG
while [ $i -lt ${#JOBS[@]} ] || [ ${#SLOTPID[@]} -gt 0 ]; do
  for s in "${!SLOTPID[@]}"; do if ! kill -0 "${SLOTPID[$s]}" 2>/dev/null; then echo "$(date '+%F %T') end job=${SLOTJOB[$s]} slot=$s" >> $LOG; unset "SLOTPID[$s]" "SLOTJOB[$s]"; fi; done
  if [ -e ~/e11_brake ] && [ $i -lt ${#JOBS[@]} ]; then
    echo "$(date '+%F %T') brake: not launching jobs $((FROM + i))..$((TO - 1))" >> $LOG; JOBS=("${JOBS[@]:0:$i}"); continue
  fi
  avail=$(awk '/MemAvailable/{print int($2/104857.6)}' /proc/meminfo)
  if [ $i -lt ${#JOBS[@]} ] && [ ${#SLOTPID[@]} -lt $MAXJ ] && [ "$avail" -ge 50 ]; then
    for s in 0 1 2 3 4 5 6 7; do [ -z "${SLOTPID[$s]:-}" ] && break; done
    J=${JOBS[$i]}; i=$((i+1)); IFS=':' read -r DIR ARM MODE ONLINE POOL SEED <<< "$J"
    (
      cd ~/$DIR || exit 2
      if [ "$ONLINE" = 1 ]; then EID=e11_${ARM}_${SEED: -3}; else EID=e11_$ARM; fi
      export OBTAIN_SEEDS=$SEED OBTAIN_LEAF_SOURCE=$MODE GOV_ONLINE=$ONLINE OBTAIN_EXPERIMENT_ID=$EID
      export OBTAIN_MC_PORT=$((25730 + s)) OBTAIN_BR_PORT=$((3230 + s))
      export OBTAIN_RESULT_JSONL=$HOME/$DIR/_cc0924/eval_$ARM.jsonl GOV_LEDGER_DIR=$HOME/$DIR/_gov/ledger_${SEED: -3}
      export OBTAIN_LOG_PATH=$HOME/$DIR/_cc0924/obtain_mc_${ARM}_${SEED: -3}.log
      [ -n "$POOL" ] && export GOV_CANDIDATES=$HOME/$DIR/_gov/$POOL
      exec ~/$DIR/venv/bin/python _cc0924/test_obtain_mc.py --phase full --worker 0 > _cc0924/eval_${ARM}_${SEED: -3}.stdout 2>&1
    ) &
    SLOTPID[$s]=$!; SLOTJOB[$s]=$J; echo "$(date '+%F %T') start job=$J slot=$s avail=$avail" >> $LOG; sleep 60
  else sleep 30; fi
done
echo "$(date '+%F %T') e11 queue done from=$FROM to=$TO" >> $LOG
