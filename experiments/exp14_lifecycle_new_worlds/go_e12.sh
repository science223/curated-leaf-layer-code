#!/usr/bin/env bash
# Start one E12 lineage on slac002 (预注册_E12 revision: moved off the local machine 2026-10-06).
# Usage (by WMI, no terminal): bash ~/e12_tools/go_e12.sh <L>   (L = 1, 2, 3; 9 = the two-game smoke)
export E9_DIRECT=1 ZS_LANE_HOST=slac002 E9_REPO=$HOME/wt_e12 E9_LOG=$HOME/e12_lineages.log
unset E9_LAUNCH_ARGS
exec bash "$HOME/e12_tools/tools_0930/run_e12_lineage.sh" n "$1" >> "$HOME/e12_L$1.out" 2>&1
