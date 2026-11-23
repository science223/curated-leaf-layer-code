#!/usr/bin/env bash
# slac002: set up the ONLINE governance lineage experiment (pre-registration v7). No LLM, no cost.
set -eu
D=~/V30_eval_online
[ -e $D ] && { echo "$D exists"; exit 1; }
mkdir -p $D && cd ~/V30_eval_sys && tar cf - --exclude=./_cc0924/obtain_mc_work --exclude=./_cc0924/obtain_mc_keep \
  --exclude="./_cc0924/eval_*" --exclude="./_cc0924/smoke_*" --exclude=./_gov/ledger_eval --exclude=./_gov/ledger_smoke . | (cd $D && tar xf -)
cd $D
# 1) ledger shared along a lineage (GOV_LEDGER_LINEAGE) instead of per seed; unchanged when the variable is unset
python3 - <<'PY'
p = "_cc0924/test_obtain_mc.py"; s = open(p).read()
old = ('def ledger_file(worker_id, seed):\n    return os.path.join(\n'
       '        GOV_LEDGER_DIR, EXPERIMENT_ID, leaf_condition_name(), str(seed), f"w{worker_id}.json"\n    )\n')
new = ('def ledger_file(worker_id, seed):\n'
       '    # pre-registration v7 (online lineage): one ledger shared by all games of a lineage when GOV_LEDGER_LINEAGE is set\n'
       '    lineage = os.environ.get("GOV_LEDGER_LINEAGE", "").strip()\n'
       '    if lineage:\n'
       '        return os.path.join(GOV_LEDGER_DIR, EXPERIMENT_ID, leaf_condition_name(), f"lineage_{lineage}.json")\n'
       '    return os.path.join(\n'
       '        GOV_LEDGER_DIR, EXPERIMENT_ID, leaf_condition_name(), str(seed), f"w{worker_id}.json"\n    )\n')
assert s.count(old) == 1, "ledger_file anchor"
open(p, "w").write(s.replace(old, new)); print("harness patched")
PY
# 2) pool_online: innate first + all 12 DeepSeek candidates per key, shuffled with a fixed seed
python3 - <<'PY'
import json, os, random, sys, importlib.util
sys.path.insert(0, os.path.expanduser("~"))
spec = importlib.util.spec_from_file_location("gsel", os.path.expanduser("~/V30_tourn_preview/_cc0924/gov_select.py"))
gsel = importlib.util.module_from_spec(spec); spec.loader.exec_module(gsel)
spec2 = importlib.util.spec_from_file_location("bp", os.path.expanduser("~/build_pools.py"))
bp = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(bp)
full = json.load(open(os.path.expanduser("~/V30_tourn_preview/_gov/pool_full.json")))
cands = full.get("candidates") or full
rng = random.Random(20260927)
out = {}
for key in bp.KEYS:
    recs = sorted(list(cands.get(key) or []), key=lambda c: str(c.get("code_hash") or ""))
    rng.shuffle(recs)
    out[key] = [bp.innate(key)] + [gsel.as_v4_record(c) for c in recs]
json.dump(out, open(os.path.expanduser("~/V30_eval_online/_gov/pool_online.json"), "w"), ensure_ascii=False, indent=1)
print({k: len(v) for k, v in out.items()}, [out["diamond"][i]["id"] for i in range(4)])
PY
# 3) lineage orders: 15 cells each, own shuffle per lineage
python3 - <<'PY'
import random, os
cells = [(s, t) for s in ("20260902007", "20260902008", "20260902009", "20260902012", "20260902013")
         for t in ("diamond_pickaxe", "golden_sword", "compass")]
for i in (1, 2, 3):
    c = list(cells); random.Random(20260927 + i).shuffle(c)
    open(os.path.expanduser(f"~/V30_eval_online/_gov/lineage_{i}.txt"), "w").write("\n".join(f"{s} {t}" for s, t in c) + "\n")
print("lineage orders written")
PY
cat > $D/_cc0924/run_lineage.sh <<'SH'
#!/usr/bin/env bash
# run one online-governance lineage: its 15 cells in order, one shared ledger. Usage: run_lineage.sh L
L=$1; cd ~/V30_eval_online || exit 2
export JAVA17=/home/user/Voyager_0911/toolchain/java-17-openjdk-amd64/bin/java OBTAIN_WORKERS=1 OBTAIN_WORKER_ID=0 OBTAIN_MAX_CALLS=8 GOV_RETIRE_K=2
export OBTAIN_LEAF_SOURCE=gov GOV_ONLINE=1 GOV_CANDIDATES=$HOME/V30_eval_online/_gov/pool_online.json GOV_LEDGER_DIR=$HOME/V30_eval_online/_gov/ledger_online
export OBTAIN_EXPERIMENT_ID=online0927_L$L GOV_LEDGER_LINEAGE=L$L OBTAIN_RESULT_JSONL=$HOME/V30_eval_online/_cc0924/online_L$L.jsonl
export OBTAIN_MC_PORT=$((25670 + L)) OBTAIN_BR_PORT=$((3170 + L))
n=0
while read -r SEED SCENE; do
  [ -z "$SEED" ] && continue
  n=$((n + 1)); if [ $n -gt 1 ]; then export GOV_RESUME=1; fi
  echo "$(date '+%F %T') L$L game $n $SEED $SCENE" >> ~/online_lineages.log
  OBTAIN_SEEDS=$SEED OBTAIN_SCENES=$SCENE ~/V30_eval_online/venv/bin/python _cc0924/test_obtain_mc.py --phase full --worker 0 >> _cc0924/online_L${L}.stdout 2>&1
done < _gov/lineage_$L.txt
echo "$(date '+%F %T') L$L done" >> ~/online_lineages.log
SH
chmod +x $D/_cc0924/run_lineage.sh; bash -n $D/_cc0924/run_lineage.sh && echo runner_ok
head -3 $D/_gov/lineage_1.txt
