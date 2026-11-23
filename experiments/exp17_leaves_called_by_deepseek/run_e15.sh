#!/usr/bin/env bash
# E15 runner (预注册_E15_DeepSeek调用小模型叶子_2026-10-16) on slac002: plays plan zs_e15_<smoke|main> of ~/wt_e15 with
# the direct API and the E7 switches of the E10 SCR arm. Every switch is set or unset explicitly; nothing is inherited.
# Same as ~/e10_tools/run_e10.sh with E10_HOST=slac002, except the repo, the plan names and the six pools checked.
# Usage: run_e15.sh <smoke|main>
set -u
P=$1
R=$HOME/wt_e15
PY=$HOME/Voyager_0911/venv/bin/python
LOG=$HOME/e15.log
log() { echo "$(date '+%F %T') E15 $P $*" >> "$LOG"; }
unset GOV_QUIESCE_MS GOV_QUIESCE_CAP_MS GOV_LATE_FENCE GOV_LEDGER_MODE GOV_RETIRE_MODE GOV_FALLBACK
unset GOV_LEDGER_FILE GOV_LEDGER_LINEAGE_ID GOV_LEDGER_LINEAGE GOV_ALLOW_EMPTY_POOL GOV_RESUME GOV_POOL_APPEND_ONLY
unset ZS_EGRESS_PROXY V30_EGRESS_PROXY HTTPS_PROXY HTTP_PROXY https_proxy http_proxy
case $P in
  smoke|main) export GOV_QUIESCE_MS=1500 GOV_QUIESCE_CAP_MS=6000 GOV_LATE_FENCE=1 GOV_LEDGER_MODE=full \
                     GOV_RETIRE_MODE=quarantine GOV_FALLBACK=on ;;
  *) echo "plan must be smoke or main"; exit 2 ;;
esac
export ZS_LANE_HOST=slac002
m() { md5sum < "$R/$1" | cut -c1-8; }
[ "$(m voyager/control_primitives_extra/obtain.js)" = b3f48363 ] || { log "STOPPED: obtain.js is $(m voyager/control_primitives_extra/obtain.js)"; exit 2; }
[ "$(m voyager/governance/leaf_inject.py)" = 5583ebb4 ] || { log "STOPPED: leaf_inject.py is $(m voyager/governance/leaf_inject.py)"; exit 2; }
[ "$(m run_zeroshot.py)" = 59f6232e ] || { log "STOPPED: run_zeroshot.py is $(m run_zeroshot.py)"; exit 2; }
for x in e12_L1:86075713 e12_L2:fd932c5e e12_L3:8a56cbdf e13_L1:986216be e13_L2:05654e9b e13_L3:8c817a99; do
  [ "$(m _gov/pool_e15_${x%%:*}.json)" = "${x#*:}" ] || { log "STOPPED: pool_e15_${x%%:*}.json is $(m _gov/pool_e15_${x%%:*}.json)"; exit 2; }
done
[ "$(m _cc0911/launch_zs.py)" = "$(cat $HOME/e15_tools/lz_md5.txt 2>/dev/null)" ] || { log "STOPPED: launch_zs.py is $(m _cc0911/launch_zs.py)"; exit 2; }
code=$(curl -s -m 20 -o /dev/null -w '%{http_code}' https://api.deepseek.com/v1/models)
case $code in 000|'') log "STOPPED: api.deepseek.com unreachable (direct)"; exit 2 ;; esac
log "start plan zs_e15_$P (switches: $GOV_RETIRE_MODE)"
(cd "$R" && "$PY" -B _cc0911/launch_zs.py --plan "zs_e15_$P" --job-timeout-h 12 >> "$HOME/e15_$P.out" 2>&1)
rc=$?
log "end plan zs_e15_$P rc=$rc $(cd "$R" && "$PY" -B _cc0911/launch_zs.py --plan "zs_e15_$P" --status 2>/dev/null | awk '{print $NF}' | sort | uniq -c | tr '\n' ' ')"
exit $rc
