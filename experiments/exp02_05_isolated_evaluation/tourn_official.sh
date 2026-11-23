#!/usr/bin/env bash
# slac002: OFFICIAL tournament (DeepSeek candidates, v5 code), 6 shards. Preview rows archived, not reused.
cd ~/V30_tourn_preview || exit 2
exec 8>~/tourn_official.lock; flock -n 8 || { echo "already running"; exit 3; }
[ -f _gov/tournament_results.jsonl ] && mv _gov/tournament_results.jsonl _gov/tournament_results.preview_26rows.jsonl
export JAVA17=/home/user/Voyager_0911/toolchain/java-17-openjdk-amd64/bin/java OBTAIN_WORKERS=6 OBTAIN_EXPERIMENT_ID=tourn_official_v5code
export TOURNAMENT_KEYS=log,cobblestone,coal,raw_iron,raw_gold,redstone,diamond TOURNAMENT_SEEDS=20260902020,20260902021 TOURNAMENT_MAX_CANDS=12
echo "$(date '+%F %T') tournament official start (6 shards)" >> ~/tourn_queue.log
for w in 0 1 2 3 4 5; do
  ( export OBTAIN_WORKER_ID=$w OBTAIN_MC_PORT=$((25640 + w)) OBTAIN_BR_PORT=$((3140 + w)); exec ~/V30_tourn_preview/venv/bin/python _cc0924/gov_tournament.py --worker $w > _cc0924/tourn_official_w$w.stdout 2>&1 ) &
  sleep 20
done
wait
echo "$(date '+%F %T') tournament official done" >> ~/tourn_queue.log
