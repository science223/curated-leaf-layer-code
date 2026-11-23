#!/usr/bin/env bash
# E11 修订 3: one night of E11 on 's machine. Usage: e11_night.sh <brake_epoch> <stop_epoch>
# (launch with the absolute path: wsl.exe --exec starts no shell, so ~ is not expanded)
# Starts the clock probe unless it runs, requires the clock check (twice at most 10 min apart), then plays the jobs not
# started yet (MAXJ=3, in the fixed order of ~/e11_jobs.txt). From <brake_epoch> no new job is launched; at <stop_epoch>
# the jobs still running are stopped (an infrastructure interruption: their end is logged with stopped=1 and the rerun
# pass replays them). Once all 24 jobs have run: the rerun pass (修订 2), which starts a replay only while at least 4 h
# remain before <stop_epoch>, so that no replay is stopped in the morning (a replay is played once, never retried). Then
# the preregistered analysis. At the end of the night it stops the probe and terminates the WSL distro so the machine is
# 's again. Log: ~/e11_night.log.
BRAKE_AT=$1; STOP_AT=$2; LOG=~/e11_night.log; QLOG=~/e11_queue.log; PKG=/mnt/e/project/_e11_pkg
MAXJOB=14400   # longest job: 3 tasks x 8 obtain calls x 540 s, rounded up
say() { echo "$(date '+%F %T') $*" >> $LOG; }
exec 9>~/e11_night.lock; flock -n 9 || { say "another night script is running"; exit 3; }
finish() {   # $1 = what happened; leaves the machine free
  say "$1"
  pkill -f "[c]lk_probe.sh"; say "night over; terminating the WSL distro"
  /mnt/c/Windows/System32/wsl.exe --terminate Ubuntu
  exit 0
}
[[ $BRAKE_AT =~ ^[0-9]+$ && $STOP_AT =~ ^[0-9]+$ ]] && [ "$BRAKE_AT" -lt "$STOP_AT" ] || finish "HALT: bad arguments '$1' '$2'"
rm -f ~/e11_brake
say "start brake_at=$(date -d @$BRAKE_AT '+%F %T') stop_at=$(date -d @$STOP_AT '+%F %T')"
started() { grep " start job=" $QLOG 2>/dev/null | grep -vc "rerun=1"; }
close_open() {   # append an end line (stopped=1) for every job that started and has no end line
  [ -e $QLOG ] || return 0
  python3 - "$QLOG" <<'PY'
import re, sys, datetime as dt
log = sys.argv[1]; open_ = {}
for line in open(log):
    m = re.match(r"\S+ \S+ (start|end) job=(\S+) slot=(\S+)", line)
    if not m: continue
    key = (m.group(2), "rerun=1" in line)
    if m.group(1) == "start": open_[key] = m.group(3)
    else: open_.pop(key, None)
now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
with open(log, "a") as f:
    for (j, rr), slot in open_.items():
        f.write(f"{now} end job={j} slot={slot} stopped=1{' rerun=1' if rr else ''}\n")
        print("closed", j, "rerun" if rr else "")
PY
}
kill_jobs() {   # $1 = pid of the queue or rerun script
  kill $1 2>/dev/null
  pkill -f "[t]est_obtain_mc.py"; sleep 15
  for p in $(pgrep -f "[f]abric-server-launch|[m]ineflayer/index.js|[b]ridge.js|[t]est_obtain_mc.py"); do
    case $(readlink /proc/$p/cwd 2>/dev/null) in $HOME/V30_e11_*) kill -9 $p ;; esac
  done
  close_open >> $LOG
}
watch() {   # $1 = pid of a queue/rerun script in the background, $2 = brake time; returns its exit code, or 9 after a hard stop
  while kill -0 $1 2>/dev/null; do
    now=$(date +%s)
    if [ $now -ge $STOP_AT ]; then say "hard stop: stopping the running jobs"; kill_jobs $1; return 9; fi
    if [ $now -ge $2 ] && [ ! -e ~/e11_brake ]; then touch ~/e11_brake; say "brake on: nothing new is started"; fi
    sleep 30
  done
  wait $1
}
# a night that ended abruptly (machine off, distro killed) leaves jobs without an end line: close them now
o=$(close_open); [ -n "$o" ] && say "closed intervals left open by an abrupt end: $o"
if ! pgrep -f "[c]lk_probe.sh" > /dev/null; then
  setsid nohup bash ~/e10_tools/clk_probe.sh 40000 > /dev/null 2>&1 < /dev/null 8>&- 9>&- &
  say "clock probe started"; sleep 240
fi
c=$(bash ~/e10_tools/e10_clock_ok.sh)
case $c in OK*) ;; *) say "clock check 1: $c"; sleep 600; c=$(bash ~/e10_tools/e10_clock_ok.sh) ;; esac
case $c in OK*) say "clock $c" ;; *) finish "HALT: clock check 2: $c" ;; esac
[ "$(grep -cv '^#' ~/e11_jobs.txt)" = 24 ] || finish "HALT: ~/e11_jobs.txt does not have 24 jobs"
n=$(started)
if [ "$n" -lt 24 ]; then
  [ "$(date +%s)" -lt "$BRAKE_AT" ] || finish "past the brake time before any job started; nothing run"
  say "queue from job $n"
  MAXJ=3 bash ~/e11_tools/e11_queue.sh $n 24 >> $LOG 2>&1 &
  watch $! $BRAKE_AT; rc=$?
  [ $rc -eq 9 ] && finish "stopped for the morning after $(started) jobs started"
  [ $rc -eq 0 ] || finish "HALT: queue rc=$rc"
  [ -e ~/e11_brake ] && finish "paused by the brake after $(started) jobs started"
fi
[ "$(started)" -ge 24 ] || finish "HALT: only $(started) jobs started"
if ! grep -q "e11 rerun pass done" $QLOG; then
  RB=$((STOP_AT - MAXJOB)); [ $RB -lt $BRAKE_AT ] || RB=$BRAKE_AT
  [ "$(date +%s)" -lt "$RB" ] || finish "less than 4 h before the stop time; the rerun pass waits for the next night"
  say "rerun pass (no replay starts after $(date -d @$RB '+%F %T'))"
  bash ~/e11_tools/e11_rerun.sh >> $LOG 2>&1 &
  watch $! $RB; rc=$?
  [ $rc -eq 9 ] && finish "stopped for the morning during the rerun pass"
  grep -q "e11 rerun pass done" $QLOG || finish "rerun pass paused (rc=$rc); continues next night"
fi
say "orchestrator done"
mkdir -p ~/analysis_1006
( cd ~ && python3 ~/e11_tools/e11_analyze.py --home ~ ) > ~/analysis_1006/e11.txt 2>&1; rc=$?
cp ~/analysis_1006/e11.txt $PKG/e11_analysis.txt
finish "analysis rc=$rc -> _e11_pkg/e11_analysis.txt"
