#!/usr/bin/env bash
# E15 smoke on slac002 (预注册_E15 §8): clock check, plan zs_e15_smoke (FQ1 and FD1 on 010 golden sword, lanes 8, 9),
# then the mechanism check (e15_analyze.py --smoke) into ~/e15_smoke.txt. Started by WMI (e15_wmi.ps1 smoke).
T=~/e15_tools; LOG=~/e15_go.log
exec 7>~/e15_go.lock; flock -n 7 || { echo "$(date '+%F %T') smoke: another E15 step is running" >> $LOG; exit 3; }
say() { echo "$(date '+%F %T') $*" >> $LOG; }
c=$(bash $T/e10_clock_ok.sh)
case $c in OK*) say "smoke: clock $c" ;; *) say "smoke HALT: clock $c"; exit 5 ;; esac
say "smoke: start"
bash $T/run_e15.sh smoke
say "smoke: launcher rc=$?"
~/Voyager_0911/venv/bin/python -B $T/e15_analyze.py --smoke ~/wt_e15 > ~/e15_smoke.txt 2>&1
say "smoke: analysis rc=$? $(grep '== smoke' ~/e15_smoke.txt)"
