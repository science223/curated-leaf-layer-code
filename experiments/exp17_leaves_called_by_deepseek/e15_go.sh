#!/usr/bin/env bash
# E15 main batch on slac002 (预注册_E15 §5, §7, §8). Requires the smoke to have passed; checks the DeepSeek balance
# (>= CNY 10) and the clock (twice at most 10 min apart), plays plan zs_e15_main (90 games, lanes 1, 2, 3, 8), then runs
# the frozen analysis into ~/e15_analysis.txt. Never reboots, never kills, never reads a result row before the analysis.
T=~/e15_tools; LOG=~/e15_go.log
exec 7>~/e15_go.lock; flock -n 7 || { echo "$(date '+%F %T') go: another E15 step is running" >> $LOG; exit 3; }
say() { echo "$(date '+%F %T') $*" >> $LOG; }
grep -q "^== smoke PASS" ~/e15_smoke.txt 2>/dev/null || { say "go HALT: the smoke has not passed"; exit 4; }
bal() { ( set -a; . ~/Voyager_0911/.env >/dev/null 2>&1; set +a; curl -s -m 20 -H "Authorization: Bearer $DEEPSEEK_API_KEY" https://api.deepseek.com/user/balance \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(sum(float(b.get('total_balance') or 0) for b in d.get('balance_infos', []) if b.get('currency') == 'CNY'))" 2>/dev/null ); }
b=$(bal)
python3 -c "import sys; sys.exit(0 if float(sys.argv[1]) >= 10 else 1)" "${b:-0}" || { say "go HALT: balance ${b:-unreadable} < CNY 10; raw: $( ( set -a; . ~/Voyager_0911/.env >/dev/null 2>&1; set +a; curl -s -m 20 -H "Authorization: Bearer $DEEPSEEK_API_KEY" https://api.deepseek.com/user/balance ) | head -c 200)"; exit 4; }
say "go: balance CNY $b"
c=$(bash $T/e10_clock_ok.sh)
case $c in OK*) ;; *) say "go: clock check 1: $c"; sleep 600; c=$(bash $T/e10_clock_ok.sh) ;; esac
case $c in OK*) say "go: clock $c" ;; *) say "go HALT: clock check 2: $c"; exit 5 ;; esac
say "go: start main"
bash $T/run_e15.sh main
say "go: launcher rc=$? balance after CNY $(bal)"
(cd $T && ~/Voyager_0911/venv/bin/python -B $T/e15_analyze.py ~/wt_e15 ~/wt_e12 --e13 ~/wt_e13) > ~/e15_analysis.txt 2>&1
say "go: analysis rc=$? -> ~/e15_analysis.txt"
say "go done"
