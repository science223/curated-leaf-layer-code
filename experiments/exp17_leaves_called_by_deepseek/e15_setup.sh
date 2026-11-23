#!/usr/bin/env bash
# E15 setup on slac002 (预注册_E15_DeepSeek调用小模型叶子_2026-10-16): ~/wt_e15 = ~/wt_e10 without its run records,
# the six final lineage pools of E12/E13 copied into _gov/, the launcher patched (patch_e15_launch.py), a dry-run status.
# Never overwrites. Writes ~/e15_tools/lz_md5.txt (the patched launcher's md5, checked by run_e15.sh).
set -eu
T=~/e15_tools; mkdir -p $T
[ -e ~/wt_e15 ] && { echo "~/wt_e15 exists"; exit 1; }
m() { md5sum < "$1" | cut -c1-8; }
[ "$(m ~/wt_e10/_cc0911/launch_zs.py)" = 8e46d0c1 ] || { echo "wt_e10 launcher differs"; exit 4; }
for E in e12 e13; do for L in 1 2 3; do
  P=~/wt_$E/runs_0911/${E}n_life_L$L/_gov
  cmp -s $P/pool_e3.json $P/pools/g15.json || { echo "$E L$L: pool_e3.json differs from the pool after game 15"; exit 5; }
done; done
mkdir -p ~/wt_e15
(cd ~/wt_e10 && tar cf - --exclude=./runs_0911 --exclude='*/__pycache__' .) | (cd ~/wt_e15 && tar xf -)
mkdir -p ~/wt_e15/runs_0911
for E in e12 e13; do for L in 1 2 3; do
  cp ~/wt_$E/runs_0911/${E}n_life_L$L/_gov/pool_e3.json ~/wt_e15/_gov/pool_e15_${E}_L$L.json
done; done
echo "copied: obtain=$(m ~/wt_e15/voyager/control_primitives_extra/obtain.js) inject=$(m ~/wt_e15/voyager/governance/leaf_inject.py)" \
  "zs=$(m ~/wt_e15/run_zeroshot.py) lz=$(m ~/wt_e15/_cc0911/launch_zs.py) runs=$(ls ~/wt_e15/runs_0911 | wc -l)"
for f in ~/wt_e15/_gov/pool_e15_*.json; do echo "$(basename $f) $(m $f)"; done
~/Voyager_0911/venv/bin/python -B $T/patch_e15_launch.py ~/wt_e15
m ~/wt_e15/_cc0911/launch_zs.py > $T/lz_md5.txt
echo "lz_md5=$(cat $T/lz_md5.txt)"
cd ~/wt_e15
for P in zs_e15_smoke zs_e15_main; do
  ZS_LANE_HOST=slac002 ~/Voyager_0911/venv/bin/python -B _cc0911/launch_zs.py --plan $P --dry-run 2>&1 | grep -E "PLAN|lanes|parallel" | head -3
done
rm -rf ~/wt_e15/runs_0911/zs_e15_smoke ~/wt_e15/runs_0911/zs_e15_main   # the dry-runs' empty plan directories
ls ~/wt_e15/runs_0911 | wc -l
