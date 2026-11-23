#!/usr/bin/env bash
# E11 修订 2 rerun pass, local variant (修订 3, 's machine). Differs from slac002's e11_rerun.sh only in scheduling:
#   1. Java 17 is the system one;
#   2. up to MAXJ replays run at the same time (slot s uses ports 25730+s / 3230+s, as the queue does), not one by one
#      (10-07: asked for it to finish sooner; the same machine, the same files and environment per replay);
#   3. a job already replayed (an "end job=<j> slot=<s> [stopped=1 ]rerun=1" line in the queue log) is not replayed
#      again: a replay is played once, never retried (修订 2: the harness refuses an existing ledger and skips games
#      already won, so a retry would not be a clean replay); a line cut by an abrupt end does not count as a game row;
#   4. the brake: while ~/e11_brake exists no further replay is started; the pass then ends once the running replays end
#      and logs "rerun pass paused".
# Replays every job that wrote fewer than 3 game rows (an environment failure, or a job stopped when needed the
# machine). Counts rows only (mechanism), never reads a success flag. Replays write to eval_<arm>_rerun.jsonl; the
# analysis takes the last row per (world, task). Test hook: E11_HARNESS replaces the harness command (tests only).
LOG=~/e11_queue.log; Q=~/e11_jobs.txt; MAXJ=${MAXJ:-3}
exec 8>~/e11_queue.lock; flock -n 8 || { echo "e11 queue already running"; exit 3; }
export JAVA17=/usr/lib/jvm/java-17-openjdk-amd64/bin/java OBTAIN_WORKERS=1 OBTAIN_WORKER_ID=0 OBTAIN_MAX_CALLS=8 GOV_RETIRE_K=2
unset GOV_QUIESCE_MS GOV_QUIESCE_CAP_MS GOV_LATE_FENCE GOV_LEDGER_MODE GOV_RETIRE_MODE GOV_FALLBACK GOV_LEDGER_LINEAGE GOV_RESUME GOV_POOL_APPEND_ONLY GOV_LEDGER_FILE GOV_LEDGER_LINEAGE_ID GOV_ALLOW_EMPTY_POOL
rows_of() {   # $1 = result jsonl, $2 = seed: number of game rows of that seed
  python3 - "$1" "$2" <<'PY'
import json, os, sys
n = 0
if os.path.exists(sys.argv[1]):
    for l in open(sys.argv[1]):
        try:
            r = json.loads(l)
        except ValueError:
            continue
        if r.get("event") == "job_done" and str((r.get("row") or {}).get("seed")) == sys.argv[2]:
            n += 1
print(n)
PY
}
# the jobs to replay, in the fixed order of the job list
TODO=(); ROWS=()
while IFS=':' read -r DIR ARM MODE ONLINE POOL SEED; do
  [ -z "$DIR" ] && continue
  J="$DIR:$ARM:$MODE:$ONLINE:$POOL:$SEED"
  grep -F " end job=$J slot=" $LOG 2>/dev/null | grep -q "rerun=1" && continue
  r=$(rows_of ~/$DIR/_cc0924/eval_$ARM.jsonl $SEED)
  [ "${r:-0}" -ge 3 ] && continue
  TODO+=("$J"); ROWS+=("$r")
done < <(grep -v '^#' $Q | grep -v '^$')
echo "$(date '+%F %T') e11 rerun pass start: ${#TODO[@]} jobs to replay, maxj=$MAXJ" >> $LOG
declare -A SLOTPID SLOTJOB; i=0; braked=0
while [ $i -lt ${#TODO[@]} ] || [ ${#SLOTPID[@]} -gt 0 ]; do
  for s in "${!SLOTPID[@]}"; do if ! kill -0 "${SLOTPID[$s]}" 2>/dev/null; then echo "$(date '+%F %T') end job=${SLOTJOB[$s]} slot=$s rerun=1" >> $LOG; unset "SLOTPID[$s]" "SLOTJOB[$s]"; fi; done
  if [ -e ~/e11_brake ] && [ $i -lt ${#TODO[@]} ]; then braked=1; TODO=("${TODO[@]:0:$i}"); continue; fi
  avail=$(awk '/MemAvailable/{print int($2/104857.6)}' /proc/meminfo)
  if [ $i -lt ${#TODO[@]} ] && [ ${#SLOTPID[@]} -lt $MAXJ ] && [ "$avail" -ge 50 ]; then
    for s in 0 1 2 3 4 5 6 7; do [ -z "${SLOTPID[$s]:-}" ] && break; done
    J=${TODO[$i]}; r=${ROWS[$i]}; i=$((i+1)); IFS=':' read -r DIR ARM MODE ONLINE POOL SEED <<< "$J"
    (
      cd ~/$DIR || exit 2
      if [ "$ONLINE" = 1 ]; then EID=e11_${ARM}_${SEED: -3}; else EID=e11_$ARM; fi
      export OBTAIN_SEEDS=$SEED OBTAIN_LEAF_SOURCE=$MODE GOV_ONLINE=$ONLINE OBTAIN_EXPERIMENT_ID=$EID
      export OBTAIN_MC_PORT=$((25730 + s)) OBTAIN_BR_PORT=$((3230 + s))
      export OBTAIN_RESULT_JSONL=$HOME/$DIR/_cc0924/eval_${ARM}_rerun.jsonl GOV_LEDGER_DIR=$HOME/$DIR/_gov/ledger_${SEED: -3}_rerun
      export OBTAIN_LOG_PATH=$HOME/$DIR/_cc0924/obtain_mc_${ARM}_${SEED: -3}_rerun.log
      [ -n "$POOL" ] && export GOV_CANDIDATES=$HOME/$DIR/_gov/$POOL
      exec ${E11_HARNESS:-~/$DIR/venv/bin/python _cc0924/test_obtain_mc.py --phase full --worker 0} > _cc0924/eval_${ARM}_${SEED: -3}_rerun.stdout 2>&1 < /dev/null
    ) &
    SLOTPID[$s]=$!; SLOTJOB[$s]=$J
    echo "$(date '+%F %T') start job=$J slot=$s rerun=1 rows_before=$r avail=$avail" >> $LOG; sleep ${E11_STAGGER:-60}
  else sleep ${E11_POLL:-30}; fi
done
if [ $braked = 1 ]; then echo "$(date '+%F %T') e11 rerun pass paused (brake): $i jobs replayed so far" >> $LOG
else echo "$(date '+%F %T') e11 rerun pass done: ${#TODO[@]} jobs replayed" >> $LOG; fi
