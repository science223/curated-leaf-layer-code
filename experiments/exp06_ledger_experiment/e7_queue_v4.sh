#!/usr/bin/env bash
# E7 lineage queue with a load threshold, v4: takes over a running batch (预注册_账本消融_2026-09-29, "门槛④修订", 09-30).
# The v3 queue (e7_queue_thr.sh 59bdf6db) stays alive but retired (its pause file ~/e7_pause is left in place for good: it
# must not be killed while its lineages run, they live in its WSL session). This queue runs beside it and
#   - starts only the lineages of ~/e7_jobs.txt that have not begun (no "E7 <C> L<L> game 1" line in ~/e7_lineages.log
#     after that lineage's last VOID marker), in the original order;
#   - reads the running lineages from the process table: a slot whose run_e7_lineage.sh is alive is busy whoever started
#     it (never reused; counted for the cap);
#   - uses gate 4 as amended: Windows free >= 7 GB, or Windows free >= 6 GB with >= 5 GB MemFree inside WSL.
# A new lineage starts only when ALL hold:
#   every E6 rerun queue (r12r, r12r2) has started all its jobs, or is not running (E6 reruns go first)
#   E7 lineages running < MAXJ (8); E6 rerun processes + E7 lineages running < 8 before the start; all obtain processes < 8
#   WSL MemAvailable >= 5 GB; gate 4 (above); >= 300 s since the last start of any of our queues
#   over the last 60 min of our calls (E6 reruns + E7): < 15 calls or step timeouts <= 5 %
#   no ~/e7b_pause file; no unacknowledged non-zero lineage exit (exit lines with rc != 0 in the log > number in ~/e7_nonzero_ack)
# While held, a hold line with the reasons is logged every 10 min and whenever the set of reasons changes.
# The VOID marker of a lineage rebuilt while this queue runs takes effect only at the next start of this queue: after
# "e7 queue done" run it with E7_DRY=1 and start it again if it would still start something.
# E7_DRY=1: print the lineages it would start, the busy slots and the gate, then exit (takes no lock, writes nothing).
Q=~/e7_jobs.txt; MAXJ=${MAXJ:-8}; [ "$MAXJ" -gt 8 ] && MAXJ=8; LOG=~/e7_queue.log; LIN=~/e7_lineages.log
WINFREE_MIN_KB=$((7 * 1024 * 1024)); WINFREE_FLOOR_KB=$((6 * 1024 * 1024)); GFREE_MIN_MB=5120
GAP_S=${E7_TEST_GAP_S:-300}; ROLL_MIN_CALLS=15; ROLL_MAX_PCT=5; ALL_MAX=8
TICK=${E7_TEST_TICK:-60}   # E7_TEST_* are for the stub test only (test_e7_queue_v4.sh); never set in a real run
PS=/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe
winfree() { timeout 30 $PS -NoProfile -Command "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory" < /dev/null 2>/dev/null | tr -dc '0-9'; }
roll() {   # "calls sto" over our calls of the last 60 minutes
  local since; since=$(date -d '-60 min' '+%F %T')
  cat ~/V30_r12r*/_cc0924/obtain_mc_*.log ~/V30_e7_{full,off,retire,reorder,quar}_L[123]/_cc0924/obtain_mc_e7_*.log \
      ~/V30_e7_void_0930/*/_cc0924/obtain_mc_e7_*.log 2>/dev/null \
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
begun() {  # has lineage $1 L$2 begun since its last VOID marker? (a VOID line must not contain the word "game")
  [ -r $LIN ] || return 1
  awk -v k="E7 $1 L$2 " 'index($0, k) { if ($0 ~ / VOID /) b = 0; else if ($0 ~ / game 1 /) b = 1 } END { exit b ? 0 : 1 }' $LIN
}
busy_slots() {  # the slots whose lineage runner is alive, whoever started it
  local s out=""
  for s in 0 1 2 3 4 5 6 7; do pgrep -f "[r]un_e7_lineage.sh [a-z]+ [0-9]+ ${s}\$" > /dev/null; [ $? -ne 1 ] && out="$out $s"; done   # a pgrep error counts as busy
  echo $out
}
nonzero() { grep -ac "exit .* rc=[1-9]" $LOG 2>/dev/null; }
acked() {  # the first field of ~/e7_nonzero_ack, digits only (anything else reads as 0)
  local a=""; [ -r ~/e7_nonzero_ack ] && read -r a _ < ~/e7_nonzero_ack
  a=$(printf '%s' "$a" | tr -d '\r'); case $a in ''|*[!0-9]*) a=0 ;; esac; echo "$a"
}
gate() {   # prints "<reasons that block a start, empty = go>|<gate values>"; needs the number of running E7 lineages in $1
  local why="" now wf rc rs allp e6p e6s avail gfree nz ack
  now=$(date +%s); avail=$(awk '/MemAvailable/{print int($2/104857.6)}' /proc/meminfo); gfree=$(awk '/MemFree/{print int($2/1024)}' /proc/meminfo)
  allp=$(pgrep -fc "[t]est_obtain_mc.py --phase full")
  e6p=$(pgrep -fc "/V30_r12r[^/]*/venv/bin/python _cc0924/[t]est_obtain_mc.py")
  e6s=$(e6_state); nz=$(nonzero); nz=${nz:-0}; ack=$(acked)
  [ "$e6s" = pending ] && why="e6-pending"
  [ "$1" -ge $MAXJ ] && why="${why:+$why,}e7-cap"
  { [ $((e6p + $1)) -ge $ALL_MAX ] || [ "$allp" -ge $ALL_MAX ]; } && why="${why:+$why,}all=$((e6p + $1))/$allp"
  [ "$avail" -ge 50 ] || why="${why:+$why,}wsl_avail=${avail}00MB"
  [ $((now - $(laststart))) -ge $GAP_S ] || why="${why:+$why,}gap"
  [ -e ~/e7b_pause ] && why="${why:+$why,}pause-file"
  [ "$nz" -gt "$ack" ] && why="${why:+$why,}nonzero-exit=$nz>ack$ack"
  wf=$(winfree); read -r rc rs <<< "$(roll)"
  [ -z "$wf" ] && why="${why:+$why,}winfree-unreadable"
  if [ -n "$wf" ] && [ "$wf" -lt $WINFREE_MIN_KB ]; then   # below 7 GB: only if the job fits in memory the VM already holds
    { [ "$wf" -ge $WINFREE_FLOOR_KB ] && [ "$gfree" -ge $GFREE_MIN_MB ]; } || why="${why:+$why,}winfree=$((wf / 1024))MB/gfree=${gfree}MB"
  fi
  [ "$rc" -ge $ROLL_MIN_CALLS ] && [ $((rs * 100)) -gt $((rc * ROLL_MAX_PCT)) ] && why="${why:+$why,}roll=$rs/$rc"
  echo "$why|winfree_mb=$(( ${wf:-0} / 1024 )) gfree_mb=$gfree roll=$rs/$rc e6=$e6s e6procs=$e6p e7=$1 allprocs=$allp avail=$avail nonzero=$nz/ack$ack"
}
mapfile -t ALL < <(grep -v '^#' $Q | grep -v '^$')
JOBS=(); SKIP=()
for J in "${ALL[@]}"; do IFS=':' read -r C L <<< "$J"; if begun "$C" "$L"; then SKIP+=("$J"); else JOBS+=("$J"); fi; done
if [ "${E7_DRY:-}" = 1 ]; then
  cur=$(busy_slots); g=$(gate $(echo $cur | wc -w)); w=${g%%|*}
  echo "would start (${#JOBS[@]}): ${JOBS[*]}"; echo "skipped as begun (${#SKIP[@]}): ${SKIP[*]}"; echo "busy slots: ${cur:-none}"
  echo "gate: ${w:-GO} | ${g#*|}"; exit 0
fi
exec 8>~/e7_queue.v4.lock; flock -n 8 || { echo "$(date '+%F %T') e7 queue v4 NOT started: lock held" >> $LOG; exit 3; }
echo $$ > ~/e7_queue.pid
i=0; lasthold=0; lastkey=""; MINE=""; idle=0; prev=$(busy_slots)
echo "$(date '+%F %T') e7 queue start jobs=${#JOBS[@]} maxj=$MAXJ v4 takeover: skipped as begun=${#SKIP[@]}, busy slots at start:${prev:+ $prev} gate=e6-first,all<=$ALL_MAX,winfree>=7GB|(>=6GB&gfree>=5GB),gap>=${GAP_S}s,roll<=${ROLL_MAX_PCT}%" >> $LOG
while :; do
  cur=$(busy_slots); n=$(echo $cur | wc -w)
  for s in $MINE; do   # the end of a lineage this queue started (the retired queue logs the ends of its own)
    case " $cur " in *" $s "*) ;; *) echo "$(date '+%F %T') end slot=$s q=v4" >> $LOG; MINE=$(echo " $MINE " | sed "s/ $s / /") ;; esac
  done
  if [ $i -ge ${#JOBS[@]} ] && [ "$n" -eq 0 ]; then idle=$((idle + 1)); [ $idle -ge 2 ] && break; else idle=0; fi
  if [ $i -lt ${#JOBS[@]} ]; then
    g=$(gate $n); why=${g%%|*}; info=${g#*|}
    if [ -z "$why" ]; then   # re-check what may have changed while the gate was being read
      cur2=$(busy_slots); [ "$cur2" = "$cur" ] || why="slots-changed"   # a lineage began or ended meanwhile: next tick
      [ -e ~/e7b_pause ] && why="${why:+$why,}pause-file"
      [ "$(nonzero)" -gt "$(acked)" ] 2>/dev/null && why="${why:+$why,}nonzero-exit"
    fi
    if [ -z "$why" ]; then
      s=""; for t in 0 1 2 3 4 5 6 7; do case " $cur $MINE " in *" $t "*) ;; *) s=$t; break ;; esac; done
      J=${JOBS[$i]}; IFS=':' read -r C L <<< "$J"
      if [ -z "$s" ]; then why="no-free-slot"
      elif begun "$C" "$L"; then i=$((i+1)); echo "$(date '+%F %T') skip $J: it has begun meanwhile" >> $LOG; continue
      fi
    fi
    if [ -z "$why" ]; then
      i=$((i+1))
      (
        bash ~/run_e7_lineage.sh "$C" "$L" "$s" < /dev/null; rc=$?
        echo "$(date '+%F %T') exit $C:L$L slot=$s rc=$rc q=v4" >> $LOG
      ) &
      MINE="$MINE $s"; echo "$(date '+%F %T') start slot=$s $J running=$((n + 1)) $info q=v4" >> $LOG; sleep $TICK
    else
      now=$(date +%s); key=$(echo "$why" | sed -E 's/=[^,]*//g')   # one hold line per 10 min, or when the reasons change
      if [ $((now - lasthold)) -ge 600 ] || [ "$key" != "$lastkey" ]; then
        echo "$(date '+%F %T') hold running=$n next=$((i + 1)) $why | $info q=v4" >> $LOG; lasthold=$now; lastkey=$key
      fi
      sleep $TICK
    fi
  else sleep $((TICK / 2 + 1)); fi
done
echo "$(date '+%F %T') e7 queue done" >> $LOG
