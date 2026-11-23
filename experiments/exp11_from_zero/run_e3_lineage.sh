#!/usr/bin/env bash
# one E3 lineage (预注册_从零在线生命线_2026-09-28): before each game grow the pool for needed keys with no candidate
# on duty, then play the game. Usage: run_e3_lineage.sh <L|smoke>. Ports 25720+L / 3220+L (smoke: 25729/3229).
# Qwen 09-28 E3-2: a failed grow stops the lineage (never play a game without its grow); an existing result file
# refuses the start (a restart would replay finished games with a bigger pool).
L=$1; if [ "$L" = smoke ]; then D=~/V30_e3_smoke; N=1; P=9; MAXC=2; else D=~/V30_e3_L$L; N=$L; P=$L; MAXC=8; fi
cd $D || exit 2
exec 9>$D/_gov/e3.lock; flock -n 9 || { echo "lineage $L already running"; exit 3; }
[ -e $D/_cc0924/e3_L$L.jsonl ] && { echo "$(date '+%F %T') E3 L$L REFUSED: result file exists" >> ~/e3_lineages.log; exit 4; }
export JAVA17=/home/user/Voyager_0911/toolchain/java-17-openjdk-amd64/bin/java OBTAIN_WORKERS=1 OBTAIN_WORKER_ID=0 OBTAIN_MAX_CALLS=$MAXC GOV_RETIRE_K=2
export OBTAIN_LEAF_SOURCE=gov GOV_ONLINE=1 GOV_CANDIDATES=$D/_gov/pool_e3.json GOV_LEDGER_DIR=$D/_gov/ledger_e3 GOV_POOL_APPEND_ONLY=1
export OBTAIN_EXPERIMENT_ID=e3_0928_L$L GOV_LEDGER_LINEAGE=L$N OBTAIN_RESULT_JSONL=$D/_cc0924/e3_L$L.jsonl
export OBTAIN_MC_PORT=$((25720 + P)) OBTAIN_BR_PORT=$((3220 + P))
n=0
while read -r SEED SCENE; do
  [ -z "$SEED" ] && continue
  n=$((n + 1)); if [ $n -gt 1 ]; then export GOV_RESUME=1; fi
  echo "$(date '+%F %T') E3 L$L game $n $SEED $SCENE grow" >> ~/e3_lineages.log
  E2_REPO=$D E2_ENV_FILE=$HOME/V30/.env $D/venv/bin/python -B _cc0924/e3_grow.py --lineage $N --scene $SCENE --game $n >> _cc0924/e3_grow_L$L.stdout 2>&1
  rc=$?
  if [ $rc -ne 0 ]; then echo "$(date '+%F %T') E3 L$L game $n GROW_FAILED rc=$rc -> lineage stopped" >> ~/e3_lineages.log; exit 5; fi
  cnt=$(python3 -c "import json; p=json.load(open('_gov/pool_e3.json')); print(' '.join(f'{k}:{len(v)}' for k, v in p.items()))")
  echo "$(date '+%F %T') E3 L$L game $n $SEED $SCENE play pool=$(md5sum < _gov/pool_e3.json | cut -c1-8) [$cnt]" >> ~/e3_lineages.log
  OBTAIN_SEEDS=$SEED OBTAIN_SCENES=$SCENE $D/venv/bin/python _cc0924/test_obtain_mc.py --phase full --worker 0 >> _cc0924/e3_L$L.stdout 2>&1
done < _gov/lineage_$N.txt
echo "$(date '+%F %T') E3 L$L done" >> ~/e3_lineages.log
