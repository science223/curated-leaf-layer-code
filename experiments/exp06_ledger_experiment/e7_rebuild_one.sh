#!/usr/bin/env bash
# E7: void one stopped lineage and rebuild its directory for a replay from game 1 (预注册_账本消融_2026-09-29, "中断怎么办" /
# "中断后重打的步骤"). Usage: e7_rebuild_one.sh <full|off|retire|reorder|quar> <L>
# Refuses while that lineage's runner is alive. Moves ~/V30_e7_<C>_L<L> to ~/V30_e7_void_0930/<C>_L<L>_run<k>, rebuilds the
# directory with the same three steps and md5 checks as e7_setup.sh (4fcfb5d6), and appends a VOID marker to
# ~/e7_lineages.log so that the v4 queue treats the lineage as not begun. The lineage's old lines stay in the log while
# the batch runs (other lineages append to it); they are moved to the archive before the analysis. Prints md5s only.
set -eu
C=$1; L=$2
case $C in full|off|retire|reorder|quar) ;; *) echo "unknown condition $C"; exit 2 ;; esac
case $L in 1|2|3) ;; *) echo "unknown lineage $L"; exit 2 ;; esac
pgrep -f "[r]un_e7_lineage.sh $C $L [0-9]+\$" > /dev/null && { echo "lineage $C L$L is running; not touching it"; exit 3; }
pgrep -f "V30_e7_${C}_L$L/venv/bin/python _cc0924/[t]est_obtain_mc.py" > /dev/null && { echo "a harness of $C L$L is running; not touching it"; exit 3; }
SRC=~/V30_eval_online_L1; FIX=~/V30_e7_smoke; V4=~/e7_v4_src/obtain.js; D=~/V30_e7_${C}_L$L
[ "$(md5sum < $V4 | cut -c1-8)" = b3f48363 ] || { echo "v4 obtain.js md5 differs"; exit 4; }
[ "$(md5sum < $FIX/_cc0924/test_obtain_mc.py | cut -c1-8)" = 41485cf8 ] || { echo "repaired harness md5 differs"; exit 4; }
[ "$(md5sum < $SRC/_gov/pool_online.json | cut -c1-8)" = fa4b078b ] || { echo "pool_online md5 differs"; exit 4; }
[ -d $D ] || { echo "$D does not exist"; exit 1; }
A=~/V30_e7_void_0930; mkdir -p $A; k=1; while [ -e $A/${C}_L${L}_run$k ]; do k=$((k + 1)); done
mv $D $A/${C}_L${L}_run$k
mkdir -p $D && (cd $SRC && tar cf - --exclude=./_cc0924/obtain_mc_work --exclude=./_cc0924/obtain_mc_keep \
  --exclude="./_cc0924/eval_*" --exclude="./_cc0924/online_*" --exclude="./_cc0924/*.jsonl" --exclude="./_cc0924/*.stdout" \
  --exclude="./_cc0924/*.log" --exclude="./_gov/ledger_*" .) | (cd $D && tar xf -)
cp $V4 $D/voyager/control_primitives_extra/obtain.js
cp $FIX/_cc0924/test_obtain_mc.py $D/_cc0924/test_obtain_mc.py
cmp -s $D/_gov/lineage_$L.txt ~/V30_eval_online_L$L/_gov/lineage_$L.txt || { echo "$C L$L order differs from G L$L"; exit 5; }
[ ! -e $D/_cc0924/e7_${C}_L$L.jsonl ] && [ ! -e $D/_gov/ledger_e7 ] || { echo "rebuilt directory is not clean"; exit 6; }
echo "$C L$L rebuilt: obtain=$(md5sum < $D/voyager/control_primitives_extra/obtain.js | cut -c1-8) harness=$(md5sum < $D/_cc0924/test_obtain_mc.py | cut -c1-8) pool=$(md5sum < $D/_gov/pool_online.json | cut -c1-8) order=$(md5sum < $D/_gov/lineage_$L.txt | cut -c1-8); old run in $A/${C}_L${L}_run$k"
echo "$(date '+%F %T') E7 $C L$L VOID run$k archived to $A/${C}_L${L}_run$k; replay from the first one" >> ~/e7_lineages.log
