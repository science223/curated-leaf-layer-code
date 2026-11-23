#!/usr/bin/env bash
# E11 修订 2 rerun pass, local variant (修订 3, 's machine): slac002's e11_rerun.sh with three changes only:
#   1. Java 17 is the system one;
#   2. a job already replayed (an "end job=<j> slot=0 [stopped=1 ]rerun=1" line in the queue log) is not replayed again,
#      so the pass can continue on the next night; a replay is played once, never retried (修订 2: the harness refuses an
#      existing ledger and skips games already won, so a retry would not be a clean replay); and a line cut by an abrupt
#      end does not count as a game row;
#   3. the brake: while ~/e11_brake exists no further job is replayed and the pass stops (it logs "rerun pass paused").
# After the two rounds, replay once every job that wrote fewer than 3 game rows (an environment failure, or a job stopped
# when needed the machine). Counts rows only (mechanism), never reads a success flag. Replays write to
# eval_<arm>_rerun.jsonl; the analysis takes the last row per (world, task).
LOG=~/e11_queue.log; Q=~/e11_jobs.txt
exec 8>~/e11_queue.lock; flock -n 8 || { echo "e11 queue already running"; exit 3; }
export JAVA17=/usr/lib/jvm/java-17-openjdk-amd64/bin/java OBTAIN_WORKERS=1 OBTAIN_WORKER_ID=0 OBTAIN_MAX_CALLS=8 GOV_RETIRE_K=2
unset GOV_QUIESCE_MS GOV_QUIESCE_CAP_MS GOV_LATE_FENCE GOV_LEDGER_MODE GOV_RETIRE_MODE GOV_FALLBACK GOV_LEDGER_LINEAGE GOV_RESUME GOV_POOL_APPEND_ONLY GOV_LEDGER_FILE GOV_LEDGER_LINEAGE_ID GOV_ALLOW_EMPTY_POOL
n=0
while IFS=':' read -r DIR ARM MODE ONLINE POOL SEED; do
  [ -z "$DIR" ] && continue
  J="$DIR:$ARM:$MODE:$ONLINE:$POOL:$SEED"
  grep -qF -e " end job=$J slot=0 rerun=1" -e " end job=$J slot=0 stopped=1 rerun=1" $LOG 2>/dev/null && continue
  rows=$(python3 - ~/$DIR/_cc0924/eval_$ARM.jsonl $SEED <<'PY'
import json, os, sys
n = 0
if os.path.exists(sys.argv[1]):
    for l in open(sys.argv[1]):
        try:
            r = json.loads(l)
        except ValueError:
            continue   # a line cut by an abrupt end is not a game row
        if r.get("event") == "job_done" and str((r.get("row") or {}).get("seed")) == sys.argv[2]:
            n += 1
print(n)
PY
)
  [ "${rows:-0}" -ge 3 ] && continue
  if [ -e ~/e11_brake ]; then echo "$(date '+%F %T') e11 rerun pass paused (brake): $n jobs replayed so far" >> $LOG; exit 0; fi
  n=$((n + 1))
  echo "$(date '+%F %T') start job=$J slot=0 rerun=1 rows_before=$rows" >> $LOG
  (
    cd ~/$DIR || exit 2
    if [ "$ONLINE" = 1 ]; then EID=e11_${ARM}_${SEED: -3}; else EID=e11_$ARM; fi
    export OBTAIN_SEEDS=$SEED OBTAIN_LEAF_SOURCE=$MODE GOV_ONLINE=$ONLINE OBTAIN_EXPERIMENT_ID=$EID
    export OBTAIN_MC_PORT=25730 OBTAIN_BR_PORT=3230
    export OBTAIN_RESULT_JSONL=$HOME/$DIR/_cc0924/eval_${ARM}_rerun.jsonl GOV_LEDGER_DIR=$HOME/$DIR/_gov/ledger_${SEED: -3}_rerun
    export OBTAIN_LOG_PATH=$HOME/$DIR/_cc0924/obtain_mc_${ARM}_${SEED: -3}_rerun.log
    [ -n "$POOL" ] && export GOV_CANDIDATES=$HOME/$DIR/_gov/$POOL
    ~/$DIR/venv/bin/python _cc0924/test_obtain_mc.py --phase full --worker 0 > _cc0924/eval_${ARM}_${SEED: -3}_rerun.stdout 2>&1 < /dev/null
  )
  echo "$(date '+%F %T') end job=$J slot=0 rerun=1" >> $LOG
done < <(grep -v '^#' $Q | grep -v '^$')
echo "$(date '+%F %T') e11 rerun pass done: $n jobs replayed" >> $LOG
