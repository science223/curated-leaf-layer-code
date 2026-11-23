#!/usr/bin/env bash
# E14 formal batch (预注册_E14_孤立评测谱系叶子_2026-10-13, section 6). Started by WMI (e14_wmi.ps1 go): the attached
# wsl.exe keeps the WSL instance up while the batch runs.
#   1. refuses unless ~/e14_smoke_pass reads PASS (written after the smoke health check passed and every must-fix item
#      of the review was resolved) and a clock probe is running;
#   2. clock gate: the last 10 minutes of the probe have |offset| <= 300 ms and no step below -150 ms; one more try
#      10 minutes later; otherwise HALT;
#   3. starts the six lineages of ~/e14_jobs.txt in slots 0-5, one every 300 s, each only while WSL MemAvailable >= 5 GB
#      and Windows has >= 6 GB free (checked every 60 s);
#   4. waits for all of them, then runs the frozen analysis into ~/e14_analysis.txt.
set -u
LOG=~/e14_queue.log
exec 8>~/e14_queue.lock; flock -n 8 || { echo "e14 queue already running"; exit 3; }
[ "$(tr -d '\r\n ' < ~/e14_smoke_pass 2>/dev/null)" = PASS ] || { echo "$(date '+%F %T') REFUSED: smoke not passed" >> $LOG; exit 4; }
pgrep -f "[c]lk_probe.sh" > /dev/null || { echo "$(date '+%F %T') REFUSED: no clock probe running" >> $LOG; exit 4; }
PS=/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe
winfree() { timeout 30 $PS -NoProfile -Command "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory" < /dev/null 2>/dev/null | tr -dc '0-9'; }
clock_ok() {
  tail -n 600 ~/clk_probe.log | python3 -c '
import re, sys
v = [int(x.split()[1]) for x in sys.stdin if re.match(r"^\d\d:\d\d:\d\d -?\d+ \S+$", x.strip())]
steps = [b - a for a, b in zip(v, v[1:])]
ok = len(v) >= 500 and max(abs(x) for x in v) <= 300 and min(steps or [0]) >= -150
print(("OK" if ok else "BAD"), len(v), (min(v), max(v)) if v else None, min(steps or [0]))'
}
c=$(clock_ok); echo "$(date '+%F %T') clock gate 1: $c" >> $LOG
if [ "${c%% *}" != OK ]; then
  sleep 600; c=$(clock_ok); echo "$(date '+%F %T') clock gate 2: $c" >> $LOG
  [ "${c%% *}" = OK ] || { echo "$(date '+%F %T') HALT: clock gate failed twice" >> $LOG; exit 5; }
fi
mapfile -t JOBS < <(grep -v '^#' ~/e14_jobs.txt | grep -v '^$')
echo "$(date '+%F %T') e14 queue start jobs=${#JOBS[@]}" >> $LOG
PIDS=(); NAMES=(); slot=0
for J in "${JOBS[@]}"; do
  IFS=: read -r C L <<< "$J"
  while :; do
    ga=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo); wf=$(winfree)
    [ "${ga:-0}" -ge 5120 ] && [ "${wf:-0}" -ge $((6 * 1024 * 1024)) ] && break
    echo "$(date '+%F %T') hold before $C L$L: WSL available ${ga} MB, Windows free ${wf} KB" >> $LOG; sleep 60
  done
  bash ~/e14_tools/run_e14_lineage.sh $C $L $slot > ~/e14_${C}_L$L.out 2>&1 &
  PIDS+=($!); NAMES+=("$C L$L")
  echo "$(date '+%F %T') start slot=$slot $C L$L pid=$! WSL available ${ga} MB" >> $LOG
  slot=$((slot + 1))
  [ $slot -lt ${#JOBS[@]} ] && sleep 300
done
for i in "${!PIDS[@]}"; do
  wait ${PIDS[$i]}; rc=$?
  echo "$(date '+%F %T') exit ${NAMES[$i]} rc=$rc" >> $LOG
done
echo "$(date '+%F %T') e14 queue done" >> $LOG
python3 ~/e14_tools/e14_analyze.py > ~/e14_analysis.txt 2>&1
echo "$(date '+%F %T') analysis rc=$?" >> $LOG
