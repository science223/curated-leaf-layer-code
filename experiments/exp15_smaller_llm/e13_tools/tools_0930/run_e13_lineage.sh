#!/usr/bin/env bash
# E13 (预注册_E13_E9新世界复现_2026-10-06): run_e9_lineage.sh (v30, 83bd7f8f) with plan, run dir, lineage id,
# candidate prefix and log tags renamed e9 -> e13 (mk_run_e13.py); everything else line for line the same.
# One E9 lineage (预注册_循环内完整生命周期_2026-09-29), v3 after Codex review 2. Variant V: n = Rule A without grants
# (as E8), g = with grants (as E4). Lineage L = 1..3 plays the 15 jobs of plan zs_e9V_life_L in ~/wt_v30_e9, one at a
# time, in the plan's order (the online arm's lineage order):
#   1. e9_grow.py: every leaf key the task needs with no candidate on duty in this world gets 3 leaves written by the
#      loop's action-agent prompt (E2/E3 generator, unchanged gates). An API failure stops the lineage (infrastructure);
#      programs that fail the gates are an outcome: the game is played with whatever the pool holds, even nothing
#      (GOV_ALLOW_EMPTY_POOL=1, the executor then uses its primitives). Each game's growth has its own log grow_g<n>.jsonl.
#   2. launch_zs.py --only-jobs <tag> plays the game (the launcher's own replays on environment failures, 12 h cap).
#   3. Game-level rollback: an attempt books into its own working copy of the ledger; after the game ends DONE,
#      e9_commit.py checks that it started from the previous commit and commits its working copy (all working copies
#      are kept under _gov/attempts/g<n>/). A game that does not end DONE stops the lineage.
# The pool is snapshotted before and after growth (_gov/pools/g<n>.pre.json, g<n>.json); the start and every commit are
# logged with the ledger and pool hashes. A rerun resumes only from a contiguous DONE prefix: a game DONE but not yet
# committed is committed against the previous commit; a growth interrupted before its game is undone from its pre-growth
# snapshot and redone; any other difference between the files and the last logged state stops the lineage.
# E7 executor switches: quiescence 1500 ms (cap 6000), late-pickup fence, full ledger, per-world quarantine (archived
# after 3 worlds, 6 calls, success < 0.3), fallback on. E9_DIRECT=1 (v3.1): no proxy (slac002 reaches the API directly).
# E9_REPO / E9_GROW / E9_PLAY / E9_LOG / E9_SKIP_PRECHECK override
# (2026-10-05, for other machines: E9_LAUNCH_ARGS is appended to the launcher's arguments, e.g.
#  "--machine local --ignore-machine"; unset, the script is exactly the one the experiment ran.)
# the repo, the two commands, the log and the start checks (tests). Usage: run_e9_lineage.sh <n|g> <L>   (L=9: smoke)
set -u
V=$1; L=$2
case $V in n|g) ;; *) echo "variant must be n or g"; exit 2 ;; esac
case $L in 1|2|3) NG=15 ;; 9) NG=2 ;; *) echo "lineage must be 1, 2 or 3 (9 = the two-game smoke)"; exit 2 ;; esac
R=${E9_REPO:-$HOME/wt_e13}
PY=${E9_PY:-$HOME/Voyager_0911/venv/bin/python}
T=$(cd "$(dirname "$0")" && pwd)
PLAN=zs_e13${V}_life_L$L
G=$R/runs_0911/e13${V}_life_L$L/_gov
OUT=$R/runs_0911/$PLAN
mkdir -p "$G/pools" "$G/attempts" "$OUT"
exec 9>"$G/e9.lock"; flock -n 9 || { echo "E13 $V L$L already running"; exit 3; }
LOGF=${E9_LOG:-$HOME/e13_lineages.log}
touch "$LOGF"
POOL=$G/pool_e3.json; LEDGER=$G/ledger.json
log() { echo "$(date '+%F %T') E13 $V L$L $*" >> "$LOGF"; }
GW=$(ip route | awk '/default/ {print $3; exit}')
PROXY=http://$GW:7897
# v3.1 (10-02, slac002): E9_DIRECT=1 = the machine reaches the API directly; no proxy is checked, exported or set
[ -n "${E9_DIRECT:-}" ] && PROXY=""
if [ -z "${E9_SKIP_PRECHECK:-}" ]; then
  if [ -n "$PROXY" ]; then
    [ -n "$GW" ] || { log "STOPPED: no default gateway"; exit 2; }
    code=$(curl -s -m 20 -o /dev/null -w '%{http_code}' -x "$PROXY" https://api.deepseek.com/v1/models)
    case $code in 000|'') log "STOPPED: proxy $PROXY unreachable"; exit 2 ;; esac
  else
    # E13: the model is served by Ollama on this machine (E13_API_BASE, set by go_e13.sh)
    curl -s -m 20 "${E13_API_BASE:?}/models" | grep -q '"qwen3.8:27b"' || { log "STOPPED: qwen3.8:27b not served at $E13_API_BASE"; exit 2; }
  fi
  om=$(md5sum < "$R/voyager/control_primitives_extra/obtain.js" | cut -c1-8)
  [ "$om" = b3f48363 ] || { log "STOPPED: obtain.js is $om, not b3f48363 (E7 v4)"; exit 2; }
fi
if [ -n "$PROXY" ]; then export ZS_EGRESS_PROXY=$PROXY V30_EGRESS_PROXY=$PROXY; else unset ZS_EGRESS_PROXY V30_EGRESS_PROXY; fi
export GOV_LEDGER_FILE=$LEDGER GOV_LEDGER_LINEAGE_ID=e13${V}_L$L GOV_ALLOW_EMPTY_POOL=1
export GOV_QUIESCE_MS=1500 GOV_QUIESCE_CAP_MS=6000 GOV_LATE_FENCE=1 GOV_LEDGER_MODE=full GOV_RETIRE_MODE=quarantine GOV_FALLBACK=on
unset GOV_RESUME GOV_LEDGER_LINEAGE
state() { python3 "$T/e9_commit.py" state "$LEDGER" "$POOL"; }
fsha() { python3 -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest()[:16])" "$1"; }
grow() {
  if [ -n "${E9_GROW:-}" ]; then $E9_GROW "$@"; return $?; fi
  if [ -z "$PROXY" ]; then
    env -u HTTPS_PROXY -u HTTP_PROXY -u https_proxy -u http_proxy E2_REPO=$R "$PY" -B "$T/e9_grow.py" "$@"; return $?
  fi
  HTTPS_PROXY=$PROXY HTTP_PROXY=$PROXY https_proxy=$PROXY http_proxy=$PROXY NO_PROXY=localhost,127.0.0.1 \
    no_proxy=localhost,127.0.0.1 E2_REPO=$R "$PY" -B "$T/e9_grow.py" "$@"
}
play() {
  if [ -n "${E9_PLAY:-}" ]; then $E9_PLAY "$1"; return $?; fi
  (cd "$R" && "$PY" -B _cc0911/launch_zs.py --plan "$PLAN" --only-jobs "$1" --job-timeout-h 12 ${E9_LAUNCH_ARGS:-} >> "$OUT/e9_runner.stdout" 2>&1)
}
# the logged state after game m (m = 0: the start line) as "calls ledger_sha pool_sha", empty if not logged
logged() {
  if [ "$1" -eq 0 ]; then
    grep "E13 $V L$L start " "$LOGF" | tail -1 | sed -E 's/.* start ([0-9]+) ([0-9a-f]+) ([0-9a-f]+).*/\1 \2 \3/'
  else
    grep "E13 $V L$L game $1 .* commit " "$LOGF" | tail -1 | sed -E 's/.* commit ledger_calls=([0-9]+) ledger_sha=([0-9a-f]+) pool_sha=([0-9a-f]+).*/\1 \2 \3/'
  fi
}
commit_game() {  # commit_game <n> <tag> <expected_base_sha>
  local r
  r=$(python3 "$T/e9_commit.py" commit "$OUT/zeroshot_results.jsonl" "$2" "$LEDGER" "$G/attempts/g$1" "$3")
  [ "$r" = ok ] || { log "game $1 $2 STOPPED: $r"; exit 7; }
  [ "$(fsha "$POOL")" = "$(fsha "$G/pools/g$1.json")" ] || { log "game $1 $2 STOPPED: the pool differs from the one game $1 used"; exit 7; }
}
(cd "$R" && "$PY" -B _cc0911/launch_zs.py --plan "$PLAN" --status) > "$G/status.txt" || { log "STOPPED: --status failed"; exit 2; }
[ "$(wc -l < "$G/status.txt")" -eq $NG ] || { log "STOPPED: plan has $(wc -l < "$G/status.txt") jobs, $NG expected"; exit 8; }
k=$(awk '{print $3}' "$G/status.txt" | awk 'BEGIN{k=0; gap=0} {if ($1=="DONE" && !gap) k++; else gap=1} END{print k}')
bad=$(awk -v k="$k" 'NR>k && $3!="-" {print NR": "$1" "$3}' "$G/status.txt" | head -3)
[ -z "$bad" ] || { log "STOPPED: DONE games are not a contiguous prefix ($bad)"; exit 6; }
if [ -z "$(logged 0)" ]; then
  # a fresh lineage: no game may be DONE, and it starts from an empty pool and an empty ledger of its own
  [ "$k" -eq 0 ] || { log "STOPPED: $k games DONE but the lineage never logged its start"; exit 6; }
  [ -e "$POOL" ] && [ "$(tr -d ' \n' < "$POOL")" != "{}" ] && { log "STOPPED: a fresh lineage must start from an empty pool"; exit 6; }
  echo '{}' > "$POOL"
  echo "{\"lineage_id\": \"e13${V}_L$L\", \"entries\": {}}" > "$LEDGER"
  log "start $(state)"
else
  [ -e "$POOL" ] && [ -e "$LEDGER" ] || { log "STOPPED: the pool or the ledger is missing"; exit 6; }
  if [ "$k" -gt 0 ] && [ -z "$(logged "$k")" ]; then   # game k ended DONE but was not committed
    prev=$(logged $((k - 1)))
    [ -n "$prev" ] || { log "STOPPED: no logged state before game $k"; exit 6; }
    TAG=$(sed -n "${k}p" "$G/status.txt" | awk '{print $1}')
    commit_game "$k" "$TAG" "$(echo "$prev" | awk '{print $2}')"
    log "game $k $TAG commit $(state | awk '{print "ledger_calls="$1" ledger_sha="$2" pool_sha="$3}') (on resume)"
  fi
  want=$(logged "$k")
  if [ "$(state)" != "$want" ]; then
    n1=$((k + 1))
    # a growth for game k+1 interrupted before the game: undo it from its pre-growth snapshot, then grow again
    if [ -e "$G/pools/g$n1.pre.json" ] && [ "$(fsha "$G/pools/g$n1.pre.json")" = "$(echo "$want" | awk '{print $3}')" ] \
       && [ "$(state | awk '{print $1" "$2}')" = "$(echo "$want" | awk '{print $1" "$2}')" ]; then
      cp "$G/pools/g$n1.pre.json" "$POOL"
      [ -e "$G/grow_g$n1.jsonl" ] && mv "$G/grow_g$n1.jsonl" "$G/grow_g$n1.interrupted.$(date +%s).jsonl"
      log "restored the pool from before game $n1's interrupted growth"
    fi
    [ "$(state)" = "$want" ] || { log "STOPPED: the ledger/pool on disk ($(state)) differ from the state logged after game $k ($want)"; exit 6; }
  fi
  log "resume after game $k $(state)"
fi
n=0
while read -r TAG MACH STATE; do
  n=$((n + 1))
  [ "$n" -le "$k" ] && continue
  code=$(echo "$TAG" | sed -E 's/.*_g[0-9]+_([a-z]+)_s([0-9]+)_t[0-9]+$/\1/')
  sd=$(echo "$TAG" | sed -E 's/.*_g[0-9]+_([a-z]+)_s([0-9]+)_t[0-9]+$/\2/')
  case $code in dp) SCENE=diamond_pickaxe ;; gs) SCENE=golden_sword ;; cp) SCENE=compass ;;
    *) log "game $n $TAG STOPPED: cannot parse the task"; exit 2 ;; esac
  W=20260902$sd
  base=$(state | awk '{print $2}')
  cp "$POOL" "$G/pools/g$n.pre.json"
  rm -f "$G/grow_g$n.jsonl"
  grow --pool "$POOL" --ledger "$LEDGER" --log "$G/grow_g$n.jsonl" --lineage "$L" --scene "$SCENE" --game "$n" --world "$W" \
    --prefix "gov_e13${V}L$L" >> "$G/grow.stdout" 2>&1 < /dev/null
  rc=$?
  [ $rc -eq 0 ] || { log "game $n $TAG STOPPED: grow rc=$rc (2 = API failure)"; exit 9; }
  cp "$POOL" "$G/pools/g$n.json"
  pn=$(python3 -c "import json,sys; print(sum(len(v) for v in json.load(open(sys.argv[1])).values()))" "$POOL")
  [ "$pn" -gt 0 ] || log "game $n $TAG pool empty after growth: the game runs on the executor's primitives"
  log "game $n $TAG world=$W scene=$SCENE pool=$pn $(state | awk '{print "ledger_calls="$1" ledger_sha="$2}')"
  play "$TAG" < /dev/null
  rc=$?
  retries=$(cat "$OUT/$TAG.retries" 2>/dev/null || echo 0)
  if [ ! -e "$OUT/$TAG.DONE" ]; then log "game $n $TAG STOPPED: not DONE (launcher rc=$rc, retries=$retries)"; exit 5; fi
  commit_game "$n" "$TAG" "$base"
  log "game $n $TAG commit $(state | awk '{print "ledger_calls="$1" ledger_sha="$2" pool_sha="$3}') retries=$retries"
done < "$G/status.txt"
[ $n -eq $NG ] || { log "STOPPED: $n games"; exit 8; }
log "done games=$n"
