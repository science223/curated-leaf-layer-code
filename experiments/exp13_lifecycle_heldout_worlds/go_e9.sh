#!/usr/bin/env bash
# Start one E9 lineage on slac002 (预注册_循环内完整生命周期_2026-09-29, freeze 10-02): direct API (no proxy) and
# slac002's lanes. Usage (by WMI, no terminal): bash ~/e9_tools/go_e9.sh <n|g> <L>   (L=9: the two-game smoke)
export E9_DIRECT=1 ZS_LANE_HOST=slac002
exec bash "$HOME/e9_tools/run_e9_lineage.sh" "$1" "$2" >> "$HOME/e9_L$2.out" 2>&1
