#!/usr/bin/env bash
set -eu
for L in 1 2 3; do
  D=~/V30_eval_online_L$L; [ -e $D ] && { echo "$D exists"; exit 1; }
  mkdir -p $D && cd ~/V30_eval_online && tar cf - --exclude=./_cc0924/_invalid_shared_workdir_0927 --exclude=./_cc0924/obtain_mc_work --exclude=./_cc0924/obtain_mc_keep . | (cd $D && tar xf -)
  cat > $D/_cc0924/run_lineage.sh <<'SH'
#!/usr/bin/env bash
# one online-governance lineage in its OWN directory (no shared world dirs): 15 cells in order, one shared ledger
L=$1; D=~/V30_eval_online_L$L; cd $D || exit 2
export JAVA17=/home/user/Voyager_0911/toolchain/java-17-openjdk-amd64/bin/java OBTAIN_WORKERS=1 OBTAIN_WORKER_ID=0 OBTAIN_MAX_CALLS=8 GOV_RETIRE_K=2
export OBTAIN_LEAF_SOURCE=gov GOV_ONLINE=1 GOV_CANDIDATES=$D/_gov/pool_online.json GOV_LEDGER_DIR=$D/_gov/ledger_online
export OBTAIN_EXPERIMENT_ID=online0927_L$L GOV_LEDGER_LINEAGE=L$L OBTAIN_RESULT_JSONL=$D/_cc0924/online_L$L.jsonl
export OBTAIN_MC_PORT=$((25670 + L)) OBTAIN_BR_PORT=$((3170 + L))
n=0
while read -r SEED SCENE; do
  [ -z "$SEED" ] && continue
  n=$((n + 1)); if [ $n -gt 1 ]; then export GOV_RESUME=1; fi
  echo "$(date '+%F %T') L$L game $n $SEED $SCENE" >> ~/online_lineages.log
  OBTAIN_SEEDS=$SEED OBTAIN_SCENES=$SCENE $D/venv/bin/python _cc0924/test_obtain_mc.py --phase full --worker 0 >> _cc0924/online_L${L}.stdout 2>&1
done < _gov/lineage_$L.txt
echo "$(date '+%F %T') L$L done" >> ~/online_lineages.log
SH
  chmod +x $D/_cc0924/run_lineage.sh; bash -n $D/_cc0924/run_lineage.sh
  echo "$D ok $(md5sum $D/_gov/pool_online.json | cut -c1-8) $(head -1 $D/_gov/lineage_$L.txt)"
done
