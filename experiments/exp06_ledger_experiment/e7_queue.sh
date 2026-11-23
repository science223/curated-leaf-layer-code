#!/usr/bin/env bash
# E7 lineage queue with a load threshold (预注册_账本消融_2026-09-29, "阈值运行", 09-30), v3. Job line: COND:L. One job =
# one lineage of 15 games (run_e7_lineage.sh). Same jobs and order as e7_queue.sh 20beea1f; the start gate, the cap, the
# exit-status logging and the circuit breaker differ. A new lineage starts only when ALL hold:
#   every E6 rerun queue (r12r, r12r2) has started all its jobs, or is not running (E6 reruns go first)
#   E7 lineages running < MAXJ (8)
#   E6 rerun processes + E7 lineages running < 8 before the start (<= 8 after), and all obtain processes < 8
#   WSL MemAvailable >= 5 GB                   Windows free physical memory >= 7 GB
#   >= 300 s since the last start of any of our queues
#   over the last 60 min of our calls (E6 reruns + E7): < 15 calls or step timeouts <= 5 %      no ~/e7_pause file
# While held, a hold line with the reasons is logged every 10 min and whenever the set of reasons changes.
# A lineage that exits non-zero is logged with its status and sets ~/e7_pause (no new starts until someone looks).
# E7_DRY=1: evaluate the gate once, print it and exit.
Q=~/e7_jobs.txt; MAXJ=${MAXJ:-8}; [ "$MAXJ" -gt 8 ] && MAXJ=8; LOG=~/e7_queue.log
WINFREE_MIN_KB=$((7 * 1024 * 1024)); GAP_S=${E7_TEST_GAP_S:-300}; ROLL_MIN_CALLS=15; ROLL_MAX_PCT=5; ALL_MAX=8
TICK=${E7_TEST_TICK:-60}   # E7_TEST_* are for the stub test only (test_e7_queue_thr.sh); never set in a real run
PS=/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe
winfree() { timeout 30 $PS -NoProfile -Command "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory" < /dev/null 2>/dev/null | tr -dc '0-9'; }
roll() {   # "calls sto" over our calls of the last 60 minutes
  local since; since=$(date -d '-60 min' '+%F %T')
  cat ~/V30_r12r*/_cc0924/obtain_mc_*.log ~/V30_e7_{full,off,retire,reorder,quar}_L[123]/_cc0924/obtain_mc_e7_*.log 2>/dev/null \
    | grep -a "call [0-9]/8" | awk -v s="$since" '($1" "$2) >= s {c++; if ($0 ~ /StepExecutionTimeout/) t++} END {print c+0, t+0}'
}
laststart() {  # epoch seconds of the latest start line in any of our queue logs (0 if none)
  local t; t=$(cat ~/r12r_queue.log ~/r12r2_queue.log ~/e7_queue.log 2>/dev/null | grep -a "start slot" | awk '{print $1" "$2}' | sort | tail -1)
  [ -n "$t" ] && date -d "$t" +%s || echo 0
}
e6_state() {  # "pending" while an E6 rerun queue runs and has not started all its jobs; else "started" or "absent"
  local q n st out=absent
  for q in r12r r12r2; do
    pgrep -f "/${q}_[q]ueue.sh" > /dev/null || continue
    [ -r ~/${q}_jobs.txt ] || { echo pending; return; }   # a live queue whose job list cannot be read: wait
    n=$(grep -v '^#' ~/${q}_jobs.txt | grep -cv '^$')
    st=$(awk '/queue start/{c=0} /start slot/{c++} END{print c+0}' ~/${q}_queue.log 2>/dev/null)   # starts of the current invocation
    if [ "${st:-0}" -lt "${n:-0}" ]; then echo pending; return; fi
    out=started
  done
  echo $out
}
gate() {   # prints "<reasons that block a start, empty = go>|<gate values>"; needs the number of running E7 lineages in $1
  local why="" now wf rc rs allp e6p e6s avail
  now=$(date +%s); avail=$(awk '/MemAvailable/{print int($2/104857.6)}' /proc/meminfo)
  allp=$(pgrep -fc "[t]est_obtain_mc.py --phase full")
  e6p=$(pgrep -fc "/V30_r12r[^/]*/venv/bin/python _cc0924/[t]est_obtain_mc.py")
  e6s=$(e6_state)
  [ "$e6s" = pending ] && why="e6-pending"
  [ "$1" -ge $MAXJ ] && why="${why:+$why,}e7-cap"
  { [ $((e6p + $1)) -ge $ALL_MAX ] || [ "$allp" -ge $ALL_MAX ]; } && why="${why:+$why,}all=$((e6p + $1))/$allp"
  [ "$avail" -ge 50 ] || why="${why:+$why,}wsl_avail=${avail}00MB"
  [ $((now - $(laststart))) -ge $GAP_S ] || why="${why:+$why,}gap"
  [ -e ~/e7_pause ] && why="${why:+$why,}pause-file"
  wf=$(winfree); read -r rc rs <<< "$(roll)"
  [ -z "$wf" ] && why="${why:+$why,}winfree-unreadable"
  [ -n "$wf" ] && [ "$wf" -lt $WINFREE_MIN_KB ] && why="${why:+$why,}winfree=$((wf / 1024))MB"
  [ "$rc" -ge $ROLL_MIN_CALLS ] && [ $((rs * 100)) -gt $((rc * ROLL_MAX_PCT)) ] && why="${why:+$why,}roll=$rs/$rc"
  echo "$why|winfree_mb=$(( ${wf:-0} / 1024 )) roll=$rs/$rc e6=$e6s e6procs=$e6p e7=$1 allprocs=$allp avail=$avail"
}
if [ "${E7_DRY:-}" = 1 ]; then g=$(gate 0); w=${g%%|*}; echo "gate: ${w:-GO} | ${g#*|}"; exit 0; fi
exec 8>~/e7_queue.lock; flock -n 8 || { echo "$(date '+%F %T') e7 queue NOT started: lock held" >> $LOG; exit 3; }
echo $$ > ~/e7_queue.pid
mapfile -t JOBS < <(grep -v '^#' $Q | grep -v '^$')
declare -A SLOTPID; i=0; lasthold=0; lastkey=""
echo "$(date '+%F %T') e7 queue start jobs=${#JOBS[@]} maxj=$MAXJ gate=e6-first,all<=$ALL_MAX,winfree>=7GB,gap>=${GAP_S}s,roll<=${ROLL_MAX_PCT}%" >> $LOG
while [ $i -lt ${#JOBS[@]} ] || [ ${#SLOTPID[@]} -gt 0 ]; do
  for s in "${!SLOTPID[@]}"; do if ! kill -0 "${SLOTPID[$s]}" 2>/dev/null; then echo "$(date '+%F %T') end slot=$s" >> $LOG; unset "SLOTPID[$s]"; fi; done
  if [ $i -lt ${#JOBS[@]} ]; then
    g=$(gate ${#SLOTPID[@]}); why=${g%%|*}; info=${g#*|}
    [ -z "$why" ] && [ -e ~/e7_pause ] && why="pause-file"   # set while the gate was being read
    if [ -z "$why" ]; then
      for s in 0 1 2 3 4 5 6 7; do [ -z "${SLOTPID[$s]}" ] && break; done
      J=${JOBS[$i]}; i=$((i+1)); IFS=':' read -r C L <<< "$J"
      (
        bash ~/run_e7_lineage.sh "$C" "$L" "$s" < /dev/null; rc=$?
        echo "$(date '+%F %T') exit $C:L$L slot=$s rc=$rc" >> $LOG
        if [ $rc -ne 0 ]; then touch ~/e7_pause; echo "$(date '+%F %T') pause set: $C:L$L exited rc=$rc" >> $LOG; fi
      ) &
      SLOTPID[$s]=$!; echo "$(date '+%F %T') start slot=$s $J running=${#SLOTPID[@]} $info" >> $LOG; sleep $TICK
    else
      now=$(date +%s); key=$(echo "$why" | sed -E 's/=[^,]*//g')   # one hold line per 10 min, or when the reasons change
      if [ $((now - lasthold)) -ge 600 ] || [ "$key" != "$lastkey" ]; then
        echo "$(date '+%F %T') hold running=${#SLOTPID[@]} next=$((i + 1)) $why | $info" >> $LOG; lasthold=$now; lastkey=$key
      fi
      sleep $TICK
    fi
  else sleep $((TICK / 2 + 1)); fi
done
echo "$(date '+%F %T') e7 queue done" >> $LOG
