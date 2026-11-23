#!/usr/bin/env bash
# E10 runner (预注册_E10_循环只用筛选叶子_2026-10-06): plays one plan of ~/wt_e10 on this machine's "local" lanes with
# the direct API. SCR plans (smoke, scr) get the E7 switches of the E9 LIFE arm; the SYS drift plan gets none (as E9 SYS).
# Every switch is set or unset explicitly; nothing is inherited. E10_LAUNCH_ARGS adds launcher arguments (e.g. --trials-max 1).
# Usage: [E10_HOST=slac002 E10_LZ_MD5=<patched md5>] run_e10.sh <smoke|drift|scr>
set -u
P=$1
R=${E10_REPO:-$HOME/wt_e10}
PY=${E10_PY:-$HOME/Voyager_0911/venv/bin/python}
LOG=${E10_LOG:-$HOME/e10.log}
log() { echo "$(date '+%F %T') E10 $P $*" >> "$LOG"; }
unset GOV_QUIESCE_MS GOV_QUIESCE_CAP_MS GOV_LATE_FENCE GOV_LEDGER_MODE GOV_RETIRE_MODE GOV_FALLBACK
unset GOV_LEDGER_FILE GOV_LEDGER_LINEAGE_ID GOV_LEDGER_LINEAGE GOV_ALLOW_EMPTY_POOL GOV_RESUME GOV_POOL_APPEND_ONLY
unset ZS_EGRESS_PROXY V30_EGRESS_PROXY HTTPS_PROXY HTTP_PROXY https_proxy http_proxy
case $P in
  smoke|scr) export GOV_QUIESCE_MS=1500 GOV_QUIESCE_CAP_MS=6000 GOV_LATE_FENCE=1 GOV_LEDGER_MODE=full \
                    GOV_RETIRE_MODE=quarantine GOV_FALLBACK=on ;;
  drift) ;;
  *) echo "plan must be smoke, drift or scr"; exit 2 ;;
esac
# E10_HOST=slac002 (修订 1): slac002's own lanes and machine detection, as E9 there; default: this machine's "local" lanes
H=${E10_HOST:-local}
export ZS_LANE_HOST=$H
if [ "$H" = local ]; then MF="--machine local --ignore-machine"; else MF=""; fi
m() { md5sum < "$R/$1" | cut -c1-8; }
[ "$(m voyager/control_primitives_extra/obtain.js)" = b3f48363 ] || { log "STOPPED: obtain.js is $(m voyager/control_primitives_extra/obtain.js)"; exit 2; }
[ "$(m _gov/pool_screened.json)" = 06cdfe20 ] || { log "STOPPED: pool_screened.json is $(m _gov/pool_screened.json)"; exit 2; }
case "$(m _cc0911/launch_zs.py)" in e2c5de01|${E10_LZ_MD5:-none}) ;; *) log "STOPPED: launch_zs.py is $(m _cc0911/launch_zs.py)"; exit 2 ;; esac
code=$(curl -s -m 20 -o /dev/null -w '%{http_code}' https://api.deepseek.com/v1/models)
case $code in 000|'') log "STOPPED: api.deepseek.com unreachable (direct)"; exit 2 ;; esac
log "start plan zs_e10n_$P (switches: ${GOV_RETIRE_MODE:-none})"
(cd "$R" && "$PY" -B _cc0911/launch_zs.py --plan "zs_e10n_$P" $MF --job-timeout-h 12 ${E10_LAUNCH_ARGS:-} \
   >> "$HOME/e10_$P.out" 2>&1)
rc=$?
log "end plan zs_e10n_$P rc=$rc $(cd "$R" && "$PY" -B _cc0911/launch_zs.py --plan "zs_e10n_$P" $MF --status 2>/dev/null | awk '{print $NF}' | sort | uniq -c | tr '\n' ' ')"
exit $rc
