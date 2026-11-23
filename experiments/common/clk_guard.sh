#!/usr/bin/env bash
# Clock and kick guard for the batches on slac002 (预注册 E6 "第三轮重跑" / E7 "第三批", 09-30). Start it by WMI.
# Every 60 s it reads the last 120 samples of ~/clk_probe.log ("<HH:MM:SS> <WSL minus Windows, ms> <n jobs>", written
# once per second by clk_probe.sh) and the obtain logs, and decides:
#   clock bad  = the probe is stale (file not written for > 30 s), or a consecutive-sample difference < -150 ms (the
#                clock was stepped back), or a sample outside +-300 ms
#   kicks bad  = >= 3 "job crashed" lines (a bot kicked in a game opening) in our obtain logs in the last 60 min
# When either is bad on two consecutive checks it touches ~/e7b_pause and ~/r12r_pause (no queue starts anything new)
# and logs the reason to ~/clk_guard.log; it never removes the pause files. It also restarts the probe when the probe
# has ended. One line per 10 min is logged while all is well. Runs until ~/clk_guard_stop exists. Max 60 h.
OUT=~/clk_guard.log; P=~/clk_probe.log
exec 6>~/clk_guard.lock; flock -n 6 || exit 3
echo "$(date '+%F %T') guard start" >> $OUT
bad=0; tripped=0; lastok=0; TICK=${CLK_GUARD_TEST_TICK:-60}   # the test hook is never set in a real run
for i in $(seq 1 3600); do
  [ -e ~/clk_guard_stop ] && { echo "$(date '+%F %T') guard stop (stop file)" >> $OUT; exit 0; }
  why=""
  age=$(( $(date +%s) - $(stat -c %Y $P 2>/dev/null || echo 0) ))
  if [ "$age" -gt 30 ]; then
    why="probe-stale(${age}s)"
    # the probe has ended or died: start a new one (it holds its own lock, so a second copy cannot run)
    ( setsid bash ~/clk_probe.sh 200000 < /dev/null > /dev/null 2>&1 & )
  else
    read -r mn mx st <<< "$(tail -120 $P | awk 'NF==3 && $2 ~ /^-?[0-9]+$/ { v=$2; if (n==0 || v<mn) mn=v; if (n==0 || v>mx) mx=v; if (n>0 && v-p<st) st=v-p; p=v; n++ } END { print mn+0, mx+0, st+0 }')"
    [ "$st" -lt -150 ] && why="step=${st}ms"
    { [ "$mn" -lt -300 ] || [ "$mx" -gt 300 ]; } && why="${why:+$why,}offset=${mn}..${mx}ms"
  fi
  since=$(date -d '-60 min' '+%F %T')
  cr=$(cat ~/V30_r12r*/_cc0924/obtain_mc_*.log ~/V30_e7_{full,off,retire,reorder,quar}_L[123]/_cc0924/obtain_mc_e7_*.log ~/V30_e7_void_*/*/_cc0924/obtain_mc_e7_*.log 2>/dev/null \
    | grep -a "job crashed" | awk -v s="$since" '($1" "$2) >= s' | wc -l)
  [ "$cr" -ge 3 ] && why="${why:+$why,}crashrows=$cr"
  if [ -n "$why" ]; then
    bad=$((bad + 1))
    if [ $bad -ge 2 ] && [ $tripped -eq 0 ]; then
      touch ~/e7b_pause ~/r12r_pause; tripped=1
      echo "$(date '+%F %T') TRIPPED: $why -> pause files set (new starts held; remove them by hand after checking)" >> $OUT
    elif [ $bad -ge 2 ]; then echo "$(date '+%F %T') still bad: $why" >> $OUT
    else echo "$(date '+%F %T') bad once: $why" >> $OUT; fi
  else
    [ $bad -gt 0 ] && echo "$(date '+%F %T') ok again (offset ${mn}..${mx} ms, worst step ${st} ms, crashrows $cr)" >> $OUT
    bad=0; tripped=0
    now=$(date +%s); if [ $((now - lastok)) -ge 600 ]; then echo "$(date '+%F %T') ok offset=${mn}..${mx}ms worst_step=${st}ms crashrows60=$cr" >> $OUT; lastok=$now; fi
  fi
  sleep $TICK
done
