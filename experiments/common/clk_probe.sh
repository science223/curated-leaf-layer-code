#!/usr/bin/env bash
# Read-only clock probe inside WSL on slac002: once per second append "<WSL time> <WSL wall clock - Windows wall clock, ms>
# <number of obtain processes>" to ~/clk_probe.log, for $1 seconds (default 7200). Start it by WMI. Changes nothing.
N=${1:-7200}; OUT=~/clk_probe.log
PS=/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe
exec 7>~/clk_probe.lock; flock -n 7 || exit 3
echo "$(date '+%F %T') probe start n=$N boot=$(uptime -s) clocksource=$(cat /sys/devices/system/clocksource/clocksource0/current_clocksource)" >> $OUT
$PS -NoProfile -Command "1..$N | ForEach-Object { [Console]::Out.WriteLine([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()); [Console]::Out.Flush(); Start-Sleep -Milliseconds 1000 }" < /dev/null \
  | while read -r w; do
      w=$(echo "$w" | tr -dc '0-9'); l=$(date +%s%3N)
      echo "$(date '+%H:%M:%S') $((l - w)) $(pgrep -fc '[t]est_obtain_mc.py --phase full')" >> $OUT
    done
echo "$(date '+%F %T') probe end" >> $OUT
