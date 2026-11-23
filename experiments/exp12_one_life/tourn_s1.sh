#!/usr/bin/env bash
# slac002: stage one + E2 tournament (95 candidates x seeds 020/021), obtain.js 6f13da60, 6 shards, ports 25680+w / 3180+w
cd ~/V30_s1_tourn || exit 2
exec 8>~/tourn_s1.lock; flock -n 8 || { echo "already running"; exit 3; }
export JAVA17=/home/user/Voyager_0911/toolchain/java-17-openjdk-amd64/bin/java OBTAIN_WORKERS=6 OBTAIN_EXPERIMENT_ID=tourn_s1_0928
export GOV_POOL=$HOME/V30_s1_tourn/_gov/pool_s1_combined.json TOURNAMENT_JSONL=$HOME/V30_s1_tourn/_gov/tournament_s1.jsonl
export TOURNAMENT_KEYS=log,cobblestone,coal,raw_iron,raw_gold,redstone,diamond TOURNAMENT_SEEDS=20260902020,20260902021
echo "$(date '+%F %T') tournament s1 start (6 shards)" >> ~/tourn_s1.log
for w in 0 1 2 3 4 5; do
  ( export OBTAIN_WORKER_ID=$w OBTAIN_MC_PORT=$((25680 + w)) OBTAIN_BR_PORT=$((3180 + w)); exec ~/V30_s1_tourn/venv/bin/python _cc0924/gov_tournament.py --worker $w > _cc0924/tourn_s1_w$w.stdout 2>&1 ) &
  sleep 20
done
wait
echo "$(date '+%F %T') tournament s1 done rows=$(wc -l < _gov/tournament_s1.jsonl)" >> ~/tourn_s1.log
