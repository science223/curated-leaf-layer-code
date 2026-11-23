#!/usr/bin/env bash
# One E14 lineage (预注册_E14_孤立评测谱系叶子_2026-10-13): run_e7_lineage.sh v3.2 for E7's quarantine condition, with
# the candidate pool of one lifecycle lineage. Source E (e12 | e13), lineage L (1-3; 9 = smoke), slot S.
# Plays the cells of _gov/lineage_L.txt in order with one fresh ledger shared across the lineage (GOV_LEDGER_LINEAGE),
# with the loop's curation switches (quiescence 1500/6000 ms, late fence, full ledger, quarantine, fallback on). Every
# switch is set explicitly. A game that exits non-zero is played again, at most twice, only when it wrote no result row,
# left the ledger unchanged and its output shows an environment disconnect; anything else stops the lineage.
# Usage: run_e14_lineage.sh <e12|e13> <L> <slot>     ports 25750+S / 3250+S
set -u
C=$1; L=$2; S=$3
case $C in e12|e13) ;; *) echo "unknown source $C"; exit 2 ;; esac
NG=15; [ "$L" = 9 ] && NG=2
D=~/V30_e14_${C}_L$L
cd $D || exit 2
exec 9>$D/_gov/e14.lock; flock -n 9 || { echo "E14 $C L$L already running"; exit 3; }
LOGF=~/e14_lineages.log
[ -e $D/_cc0924/e14_${C}_L$L.jsonl ] && { echo "$(date '+%F %T') E14 $C L$L REFUSED: result file exists" >> $LOGF; exit 4; }
unset GOV_RESUME OBTAIN_SEEDS OBTAIN_SCENES GOV_LEDGER_FILE GOV_LEDGER_LINEAGE_ID GOV_ALLOW_EMPTY_POOL
export JAVA17=/home/user/Voyager_0911/toolchain/java-17-openjdk-amd64/bin/java OBTAIN_WORKERS=1 OBTAIN_WORKER_ID=0 OBTAIN_MAX_CALLS=8
export OBTAIN_LEAF_SOURCE=gov GOV_ONLINE=1 GOV_RETIRE_K=2 GOV_CANDIDATES=$D/_gov/pool_online.json GOV_LEDGER_DIR=$D/_gov/ledger_e14
export OBTAIN_EXPERIMENT_ID=e14_1013_${C}_L$L GOV_LEDGER_LINEAGE=L$L OBTAIN_RESULT_JSONL=$D/_cc0924/e14_${C}_L$L.jsonl
export GOV_QUIESCE_MS=1500 GOV_QUIESCE_CAP_MS=6000 GOV_LATE_FENCE=1 GOV_LEDGER_MODE=full GOV_RETIRE_MODE=quarantine GOV_FALLBACK=on
export OBTAIN_MC_PORT=$((25750 + S)) OBTAIN_BR_PORT=$((3250 + S))
ledger_calls() {
  python3 - "$D/_gov/ledger_e14/$OBTAIN_EXPERIMENT_ID" "$L" <<'PY'
import glob, json, sys
fs = glob.glob(f"{sys.argv[1]}/*/lineage_L{sys.argv[2]}.json")
if len(fs) != 1:
    print("missing" if not fs else "ambiguous"); sys.exit(0)
e = json.load(open(fs[0])).get("entries") or {}
print(sum(int(v.get("calls") or 0) for v in e.values()))
PY
}
n=0
while read -r SEED SCENE; do
  [ -z "$SEED" ] && continue
  n=$((n + 1))
  if [ $n -gt 1 ]; then
    lc=$(ledger_calls)
    case $lc in missing|ambiguous) echo "$(date '+%F %T') E14 $C L$L game $n STOPPED: ledger $lc before the game" >> $LOGF; exit 6 ;; esac
    export GOV_RESUME=1
  fi
  echo "$(date '+%F %T') E14 $C L$L game $n $SEED $SCENE slot=$S" >> $LOGF
  k=0
  while :; do
    lc0=$(ledger_calls); rows0=$(cat $OBTAIN_RESULT_JSONL 2>/dev/null | wc -l)
    A=_cc0924/e14_${C}_L$L.g$n.a$k.stdout
    OBTAIN_SEEDS=$SEED OBTAIN_SCENES=$SCENE OBTAIN_LOG_PATH=$D/_cc0924/obtain_mc_e14_${C}_L${L}_g$n.log \
      $D/venv/bin/python _cc0924/test_obtain_mc.py --phase full --worker 0 > $A 2>&1 < /dev/null
    rc=$?
    if [ $rc -eq 0 ]; then cat $A >> _cc0924/e14_${C}_L$L.stdout && rm -f $A; break; fi
    lc1=$(ledger_calls); rows1=$(cat $OBTAIN_RESULT_JSONL 2>/dev/null | wc -l)
    envx=$(grep -o -m1 -E "out_of_order_chat|bot disconnected|Failed to step Minecraft server" $A | head -1)
    same=0; [ "$lc1" = "$lc0" ] && same=1
    [ $n -eq 1 ] && [ "$lc0" = missing ] && [ "$lc1" = 0 ] && same=1
    if [ $k -lt 2 ] && [ $same -eq 1 ] && [ "$rows1" -eq "$rows0" ] && [ -n "$envx" ]; then
      [ $n -eq 1 ] && rm -rf "$D/_gov/ledger_e14"
      mv $D/_cc0924/obtain_mc_e14_${C}_L${L}_g$n.log $D/_cc0924/obtain_mc_e14_${C}_L${L}_g$n.a$k.log 2>/dev/null
      k=$((k + 1))
      echo "$(date '+%F %T') E14 $C L$L game $n RETRY $k rc=$rc env=$envx ledger=$lc0 rows=$rows0" >> $LOGF
      continue
    fi
    echo "$(date '+%F %T') E14 $C L$L game $n FAILED rc=$rc attempt=$k ledger $lc0->$lc1 rows $rows0->$rows1 -> lineage stopped" >> $LOGF
    exit 5
  done
  lc=$(ledger_calls)
  echo "$(date '+%F %T') E14 $C L$L game $n end ledger_calls=$lc" >> $LOGF
  case $lc in ''|*[!0-9]*) echo "$(date '+%F %T') E14 $C L$L game $n STOPPED: ledger unreadable after the game ($lc)" >> $LOGF; exit 7 ;; esac
done < _gov/lineage_$L.txt
[ $n -eq $NG ] || { echo "$(date '+%F %T') E14 $C L$L STOPPED: $n games in the order file" >> $LOGF; exit 8; }
echo "$(date '+%F %T') E14 $C L$L done games=$n" >> $LOGF
