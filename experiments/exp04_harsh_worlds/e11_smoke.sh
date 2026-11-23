#!/usr/bin/env bash
# E11 smoke on 's machine (修订 3 第 5 条 = 修订 1 第 4 条): system arm directory, development seed 010 (not in the
# grid), short tasks, mechanism only. Same as slac002's e11_smoke.sh except the system Java 17 and that it prints no
# success flag. Output: ~/e11_tools/smoke_local.out
{
cd ~/V30_e11_sys || exit 2
export JAVA17=/usr/lib/jvm/java-17-openjdk-amd64/bin/java OBTAIN_WORKERS=1 OBTAIN_WORKER_ID=0 OBTAIN_MAX_CALLS=8 GOV_RETIRE_K=2
unset GOV_QUIESCE_MS GOV_QUIESCE_CAP_MS GOV_LATE_FENCE GOV_LEDGER_MODE GOV_RETIRE_MODE GOV_FALLBACK GOV_LEDGER_LINEAGE GOV_RESUME GOV_POOL_APPEND_ONLY GOV_LEDGER_FILE GOV_LEDGER_LINEAGE_ID GOV_ALLOW_EMPTY_POOL
export OBTAIN_SEEDS=20260902010 OBTAIN_LEAF_SOURCE=gov GOV_ONLINE=1 OBTAIN_EXPERIMENT_ID=e11_smoke_010
export OBTAIN_MC_PORT=25737 OBTAIN_BR_PORT=3237 OBTAIN_RESULT_JSONL=$HOME/V30_e11_sys/_cc0924/smoke_e11.jsonl
export GOV_LEDGER_DIR=$HOME/V30_e11_sys/_gov/ledger_smoke OBTAIN_LOG_PATH=$HOME/V30_e11_sys/_cc0924/smoke_e11.log
export GOV_CANDIDATES=$HOME/V30_e11_sys/_gov/pool_system.json
rm -rf $HOME/V30_e11_sys/_gov/ledger_smoke; rm -f $HOME/V30_e11_sys/_cc0924/smoke_e11.jsonl   # a rerun starts clean
date '+%F %T smoke start'
~/V30_e11_sys/venv/bin/python _cc0924/test_obtain_mc.py --phase short --worker 0 > _cc0924/smoke_e11.stdout 2>&1
echo "exit $?"; date '+%F %T smoke end'
python3 - <<'PY'
import json, os
for l in open(os.path.expanduser("~/V30_e11_sys/_cc0924/smoke_e11.jsonl")):
    r = json.loads(l)
    if r.get("event") == "job_done":
        w = r["row"]
        print({k: w.get(k) for k in ("seed", "scene", "leaf_source", "gov_online", "pool_fingerprint", "experiment_id", "spawn", "env_dead")})
PY
} > ~/e11_tools/smoke_local.out 2>&1
