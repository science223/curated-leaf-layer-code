#!/usr/bin/env bash
# E7 batch 4, round 1 (预注册_账本消融_2026-09-29.md, 10-01 15:05 entry): archive batch 3 and rebuild the 15 lineage
# directories (e7_restart_v4.sh -> e7_setup.sh), keep the full job list as ~/e7_jobs_all.txt and put only the first 8
# lineages into ~/e7_jobs.txt, move batch 3's monitor log / ack / pid files into the archive, remove the E7 brake.
# Does NOT start the queue (that is done by WMI). Prints file states only.
set -eu
H=$HOME
[ "$(md5sum < $H/e7_restart_v4.sh | cut -c1-8)" = 04f5fae4 ] || { echo "e7_restart_v4.sh md5 mismatch"; exit 2; }
[ "$(md5sum < $H/e7_queue_v4.sh | cut -c1-8)" = 7bd382f5 ] || { echo "e7_queue_v4.sh md5 mismatch"; exit 2; }
[ "$(md5sum < $H/run_e7_lineage.sh | cut -c1-8)" = 94fa5e11 ] || { echo "run_e7_lineage.sh md5 mismatch"; exit 2; }
bash $H/e7_restart_v4.sh
A=$(ls -d $H/V30_e7_stopped_* | sort | tail -1)
for f in e7_monitor.log e7_nonzero_ack e7_queue.pid e7_queue_v3.pid; do [ -e $H/$f ] && mv $H/$f "$A/" && echo "moved $f"; done
[ -d $H/V30_e7_void_0930 ] && mv $H/V30_e7_void_0930 "$A/" && echo "moved V30_e7_void_0930"
cp $H/e7_jobs.txt $H/e7_jobs_all.txt
grep -v '^#' $H/e7_jobs_all.txt | grep -v '^$' | head -8 > $H/e7_jobs.txt
echo "round 1 lineages:"; cat $H/e7_jobs.txt
echo "all: $(grep -v '^#' $H/e7_jobs_all.txt | grep -vc '^$') lineages in e7_jobs_all.txt"
rm -f $H/e7b_pause
echo "brakes: e7b_pause=$([ -e $H/e7b_pause ] && echo on || echo off) e7_pause=$([ -e $H/e7_pause ] && echo on || echo off)"
echo "listeners on 25740-25747/3240-3247: $(ss -ltn | grep -cE ':(2574[0-7]|324[0-7]) ' || true)"
echo "setup done $(date '+%F %T')"
