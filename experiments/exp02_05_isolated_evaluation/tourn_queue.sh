#!/usr/bin/env bash
# slac002: memory-gated queue for the tournament PREVIEW (v5 code, not used for selection). Job line: KEY:SEED
Q=~/V30_tourn_preview/_gov/tourn_jobs.txt; MAXJ=${MAXJ:-6}; LOG=~/tourn_queue.log
exec 8>~/tourn_queue.lock; flock -n 8 || { echo "tournament queue already running"; exit 3; }
export JAVA17=/home/user/Voyager_0911/toolchain/java-17-openjdk-amd64/bin/java OBTAIN_WORKERS=8 OBTAIN_EXPERIMENT_ID=tourn_preview_v5
mapfile -t JOBS < <(grep -v '^#' $Q | grep -v '^$')
declare -A SLOTPID; i=0
echo "$(date '+%F %T') tourn queue start jobs=${#JOBS[@]}" >> $LOG
while [ $i -lt ${#JOBS[@]} ] || [ ${#SLOTPID[@]} -gt 0 ]; do
  for s in "${!SLOTPID[@]}"; do if ! kill -0 "${SLOTPID[$s]}" 2>/dev/null; then echo "$(date '+%F %T') end slot=$s" >> $LOG; unset "SLOTPID[$s]"; fi; done
  avail=$(awk '/MemAvailable/{print int($2/104857.6)}' /proc/meminfo)
  if [ $i -lt ${#JOBS[@]} ] && [ ${#SLOTPID[@]} -lt $MAXJ ] && [ "$avail" -ge 26 ]; then
    for s in 0 1 2 3 4 5 6 7; do [ -z "${SLOTPID[$s]}" ] && break; done
    J=${JOBS[$i]}; i=$((i+1)); IFS=':' read -r KEY SEED <<< "$J"
    ( cd ~/V30_tourn_preview && export OBTAIN_WORKER_ID=$s OBTAIN_MC_PORT=$((25640 + s)) OBTAIN_BR_PORT=$((3140 + s)) && exec ~/V30_tourn_preview/venv/bin/python _cc0924/gov_tournament.py --keys $KEY --max-cands 12 --seeds $SEED --worker $s > _cc0924/tourn_${KEY}_${SEED: -3}.stdout 2>&1 ) &
    SLOTPID[$s]=$!; echo "$(date '+%F %T') start slot=$s $J avail=$avail" >> $LOG; sleep 75
  else sleep 30; fi
done
echo "$(date '+%F %T') tourn queue done" >> $LOG
