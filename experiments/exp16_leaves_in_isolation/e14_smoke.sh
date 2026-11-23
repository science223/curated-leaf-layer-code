#!/usr/bin/env bash
# E14 smoke (预注册_E14_孤立评测谱系叶子_2026-10-13, section 6). Started by WMI (e14_wmi.ps1 smoke) after the clock probe.
# Plays the two smoke lineages (e12 L9, e13 L9: the pools of lineage 1 on development cells 010 golden_sword and
# 011 compass) in slots 6 and 7, a minute apart, then writes the smoke health check to ~/e14_smoke_health.txt.
set -u
LOG=~/e14_queue.log
exec 8>~/e14_smoke.lock; flock -n 8 || { echo "e14 smoke already running"; exit 3; }
pgrep -f "[c]lk_probe.sh" > /dev/null || { echo "$(date '+%F %T') smoke REFUSED: no clock probe running" >> $LOG; exit 4; }
echo "$(date '+%F %T') smoke start" >> $LOG
bash ~/e14_tools/run_e14_lineage.sh e12 9 6 > ~/e14_e12_L9.out 2>&1 &
A=$!
sleep 60
bash ~/e14_tools/run_e14_lineage.sh e13 9 7 > ~/e14_e13_L9.out 2>&1 &
B=$!
wait $A; ra=$?
wait $B; rb=$?
echo "$(date '+%F %T') smoke end rc e12=$ra e13=$rb" >> $LOG
python3 ~/e14_tools/e14_analyze.py --smoke > ~/e14_smoke_health.txt 2>&1
echo "$(date '+%F %T') smoke health rc=$?" >> $LOG
