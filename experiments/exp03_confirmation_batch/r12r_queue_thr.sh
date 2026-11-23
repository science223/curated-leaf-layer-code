#!/usr/bin/env bash
# E6 rerun queue with a load threshold (预注册_匹配重跑与新种子_2026-09-29, "阈值重跑", 09-30). Same jobs, directories,
# ports and job block as r12r_queue.sh 0ee4cf4a; only the start gate differs. A new job starts only when ALL hold:
#   running jobs < MAXJ (6, as the original queue)          WSL MemAvailable >= 5 GB (as the original queue)
#   Windows free physical memory >= 7 GB (new)               >= 300 s since the last start (new)
#   over the last 60 min of rerun calls: < 15 calls or step timeouts <= 5 % (new)      no ~/r12r_pause file (new)
# Every start line logs the gate values; while held, one "hold" line every 10 min says why.
# Ports 25720+s / 3220+s (stage one 25700-25705, other session 25620/3120, demo 25790/3790/3791/8790).
# Online jobs get one experiment id and one ledger directory per seed.
Q=~/r12r_jobs.txt; MAXJ=${MAXJ:-6}; LOG=~/r12r_queue.log
WINFREE_MIN_KB=$((7 * 1024 * 1024)); GAP_S=300; ROLL_MIN_CALLS=15; ROLL_MAX_PCT=5
PS=/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe
exec 8>~/r12r_queue.lock; flock -n 8 || { echo "r12r queue already running"; exit 3; }
export JAVA17=/home/user/Voyager_0911/toolchain/java-17-openjdk-amd64/bin/java OBTAIN_WORKERS=1 OBTAIN_WORKER_ID=0 OBTAIN_MAX_CALLS=8 GOV_RETIRE_K=2
winfree() { timeout 30 $PS -NoProfile -Command "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory" 2>/dev/null | tr -dc '0-9'; }
roll() {   # "calls sto" over the rerun calls of the last 60 minutes
  local since; since=$(date -d '-60 min' '+%F %T')
  cat ~/V30_r12r_*/_cc0924/obtain_mc_*.log 2>/dev/null | grep -a "call [0-9]/8" \
    | awk -v s="$since" '($1" "$2) >= s {c++; if ($0 ~ /StepExecutionTimeout/) t++} END {print c+0, t+0}'
}
mapfile -t JOBS < <(grep -v '^#' $Q | grep -v '^$')
declare -A SLOTPID; i=0; last=0; lasthold=0
echo "$(date '+%F %T') r12r queue start jobs=${#JOBS[@]} maxj=$MAXJ gate=winfree>=7GB,gap>=${GAP_S}s,roll<=${ROLL_MAX_PCT}%" >> $LOG
while [ $i -lt ${#JOBS[@]} ] || [ ${#SLOTPID[@]} -gt 0 ]; do
  for s in "${!SLOTPID[@]}"; do if ! kill -0 "${SLOTPID[$s]}" 2>/dev/null; then echo "$(date '+%F %T') end slot=$s" >> $LOG; unset "SLOTPID[$s]"; fi; done
  avail=$(awk '/MemAvailable/{print int($2/104857.6)}' /proc/meminfo)
  if [ $i -lt ${#JOBS[@]} ] && [ ${#SLOTPID[@]} -lt $MAXJ ] && [ "$avail" -ge 50 ]; then
    now=$(date +%s); why=""
    [ $((now - last)) -ge $GAP_S ] || why="gap"
    if [ -z "$why" ]; then
      wf=$(winfree); read -r rc rs <<< "$(roll)"
      [ -e ~/r12r_pause ] && why="pause-file"
      [ -z "$wf" ] && why="${why:+$why,}winfree-unreadable"
      [ -n "$wf" ] && [ "$wf" -lt $WINFREE_MIN_KB ] && why="${why:+$why,}winfree=$((wf / 1024))MB"
      [ "$rc" -ge $ROLL_MIN_CALLS ] && [ $((rs * 100)) -gt $((rc * ROLL_MAX_PCT)) ] && why="${why:+$why,}roll=$rs/$rc"
      if [ -n "$why" ] && [ $((now - lasthold)) -ge 600 ]; then
        echo "$(date '+%F %T') hold running=${#SLOTPID[@]} next=$((i + 1)) $why" >> $LOG; lasthold=$now
      fi
    fi
    if [ -z "$why" ]; then
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
      SLOTPID[$s]=$!; last=$(date +%s)
      echo "$(date '+%F %T') start slot=$s $J avail=$avail winfree_mb=$((wf / 1024)) roll=$rs/$rc running=${#SLOTPID[@]}" >> $LOG; sleep 60
    else sleep 30; fi
  else sleep 30; fi
done
echo "$(date '+%F %T') r12r queue done" >> $LOG
