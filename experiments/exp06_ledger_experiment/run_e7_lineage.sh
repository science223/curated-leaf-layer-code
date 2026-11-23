#!/usr/bin/env bash
# one E7 lineage (预注册_账本消融_2026-09-29), v3.2. Condition C, lineage L, slot S. Plays the 15 cells of lineage_L.txt
# in order with one ledger shared across the lineage (GOV_LEDGER_LINEAGE), on the repaired executor (obtain.js 5217fc85,
# harness 41485cf8) with the quiescence barrier and the late-pickup fence on. Every switch is set explicitly (nothing
# inherited); a missing ledger before games 2-15 stops the lineage. v3.1 (smoke: 2 of 4 game starts kicked by the server
# for out_of_order_chat, a known environment failure also seen in G and stage one): a game that exits non-zero is played
# again, at most twice, only when it wrote no result row, left the ledger unchanged and its output shows an environment
# disconnect; each attempt's output goes to its own file and only the finished attempt joins the lineage stdout.
# Anything else that exits non-zero stops the lineage.
# Usage: run_e7_lineage.sh <full|off|retire|reorder|quar> <L> <slot>     ports 25740+S / 3240+S
set -u
C=$1; L=$2; S=$3
case $C in
  full)    LM=full;         RM=permanent ;;
  off)     LM=off;          RM=permanent ;;
  retire)  LM=retire_only;  RM=permanent ;;
  reorder) LM=reorder_only; RM=permanent ;;
  quar)    LM=full;         RM=quarantine ;;
  *) echo "unknown condition $C"; exit 2 ;;
esac
D=~/V30_e7_${C}_L$L
cd $D || exit 2
exec 9>$D/_gov/e7.lock; flock -n 9 || { echo "E7 $C L$L already running"; exit 3; }
LOGF=~/e7_lineages.log
[ -e $D/_cc0924/e7_${C}_L$L.jsonl ] && { echo "$(date '+%F %T') E7 $C L$L REFUSED: result file exists" >> $LOGF; exit 4; }
unset GOV_RESUME OBTAIN_SEEDS OBTAIN_SCENES
export JAVA17=/home/user/Voyager_0911/toolchain/java-17-openjdk-amd64/bin/java OBTAIN_WORKERS=1 OBTAIN_WORKER_ID=0 OBTAIN_MAX_CALLS=8
export OBTAIN_LEAF_SOURCE=gov GOV_ONLINE=1 GOV_RETIRE_K=2 GOV_CANDIDATES=$D/_gov/pool_online.json GOV_LEDGER_DIR=$D/_gov/ledger_e7
export OBTAIN_EXPERIMENT_ID=e7_0929_${C}_L$L GOV_LEDGER_LINEAGE=L$L OBTAIN_RESULT_JSONL=$D/_cc0924/e7_${C}_L$L.jsonl
export GOV_QUIESCE_MS=1500 GOV_QUIESCE_CAP_MS=6000 GOV_LATE_FENCE=1 GOV_LEDGER_MODE=$LM GOV_RETIRE_MODE=$RM GOV_FALLBACK=on
export OBTAIN_MC_PORT=$((25740 + S)) OBTAIN_BR_PORT=$((3240 + S))
ledger_calls() {
  python3 - "$D/_gov/ledger_e7/$OBTAIN_EXPERIMENT_ID" "$L" <<'PY'
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
    case $lc in missing|ambiguous) echo "$(date '+%F %T') E7 $C L$L game $n STOPPED: ledger $lc before the game" >> $LOGF; exit 6 ;; esac
    export GOV_RESUME=1
  fi
  echo "$(date '+%F %T') E7 $C L$L game $n $SEED $SCENE slot=$S" >> $LOGF
  k=0
  while :; do
    lc0=$(ledger_calls); rows0=$(cat $OBTAIN_RESULT_JSONL 2>/dev/null | wc -l)
    A=_cc0924/e7_${C}_L$L.g$n.a$k.stdout
    OBTAIN_SEEDS=$SEED OBTAIN_SCENES=$SCENE OBTAIN_LOG_PATH=$D/_cc0924/obtain_mc_e7_${C}_L${L}_g$n.log \
      $D/venv/bin/python _cc0924/test_obtain_mc.py --phase full --worker 0 > $A 2>&1 < /dev/null
    rc=$?
    if [ $rc -eq 0 ]; then cat $A >> _cc0924/e7_${C}_L$L.stdout && rm -f $A; break; fi
    lc1=$(ledger_calls); rows1=$(cat $OBTAIN_RESULT_JSONL 2>/dev/null | wc -l)
    envx=$(grep -o -m1 -E "out_of_order_chat|bot disconnected|Failed to step Minecraft server" $A | head -1)
    same=0; [ "$lc1" = "$lc0" ] && same=1
    # v3.2: the real harness saves an empty ledger when game 1 dies in its opening (missing -> 0 calls); that is still
    # "ledger unchanged", and the empty ledger is removed so the replay of game 1 starts without GOV_RESUME as before
    [ $n -eq 1 ] && [ "$lc0" = missing ] && [ "$lc1" = 0 ] && same=1
    if [ $k -lt 2 ] && [ $same -eq 1 ] && [ "$rows1" -eq "$rows0" ] && [ -n "$envx" ]; then
      [ $n -eq 1 ] && rm -rf "$D/_gov/ledger_e7"
      mv $D/_cc0924/obtain_mc_e7_${C}_L${L}_g$n.log $D/_cc0924/obtain_mc_e7_${C}_L${L}_g$n.a$k.log 2>/dev/null
      k=$((k + 1))
      echo "$(date '+%F %T') E7 $C L$L game $n RETRY $k rc=$rc env=$envx ledger=$lc0 rows=$rows0" >> $LOGF
      continue
    fi
    echo "$(date '+%F %T') E7 $C L$L game $n FAILED rc=$rc attempt=$k ledger $lc0->$lc1 rows $rows0->$rows1 -> lineage stopped" >> $LOGF
    exit 5
  done
  lc=$(ledger_calls)
  echo "$(date '+%F %T') E7 $C L$L game $n end ledger_calls=$lc" >> $LOGF
  case $lc in ''|*[!0-9]*) echo "$(date '+%F %T') E7 $C L$L game $n STOPPED: ledger unreadable after the game ($lc)" >> $LOGF; exit 7 ;; esac
done < _gov/lineage_$L.txt
[ $n -eq 15 ] || { echo "$(date '+%F %T') E7 $C L$L STOPPED: $n games in the order file" >> $LOGF; exit 8; }
echo "$(date '+%F %T') E7 $C L$L done games=$n" >> $LOGF
