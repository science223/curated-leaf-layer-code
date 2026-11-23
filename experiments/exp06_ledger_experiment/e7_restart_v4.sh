#!/usr/bin/env bash
# E7 restart on the v4 executor (预注册_账本消融_2026-09-29, "v4 修订"): archive the directories, job list and logs of
# the v3.1 run stopped at 03:41:34 (its games are void and never analysed), rebuild the 15 lineage directories with
# e7_setup.sh (checks obtain.js b3f48363, harness 41485cf8, pool fa4b078b and the lineage orders against G), and start a
# fresh lineage log. Does not start the queue (that is a separate WMI call). Refuses if any E7 lineage is running.
set -eu
if pgrep -f "[r]un_e7_lineage.sh (full|off|retire|reorder|quar) [123] " > /dev/null || pgrep -f "[e]7_queue.sh" > /dev/null; then
  echo "an E7 lineage or the queue is running; not touching anything"; exit 3
fi
A=~/V30_e7_stopped_$(date +%m%d_%H%M)
mkdir -p "$A"
for C in full off retire reorder quar; do for L in 1 2 3; do
  [ -e ~/V30_e7_${C}_L$L ] && mv ~/V30_e7_${C}_L$L "$A/"
done; done
for f in e7_jobs.txt e7_lineages.log e7_queue.log; do [ -e ~/$f ] && mv ~/$f "$A/$f"; done
echo "archived the stopped run to $A: $(ls "$A" | tr '\n' ' ')"
bash ~/e7_setup.sh
echo "$(date '+%F %T') E7 v4 lineage log starts here (previous run archived in $A)" >> ~/e7_lineages.log
