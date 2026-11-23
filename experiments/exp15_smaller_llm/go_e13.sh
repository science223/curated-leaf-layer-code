#!/usr/bin/env bash
# Start one E13 lineage on slac002 (预注册_E13_换小模型复现_2026-10-09): the E12 lineage with the local Qwen3.8-27B served
# by Ollama on this machine, reached through the WSL gateway. Usage (by WMI, no terminal): bash ~/e13_tools/go_e13.sh <L>
# (L = 1, 2, 3; 9 = the two-game smoke)
GW=$(ip route | awk '/default/{print $3; exit}')
export E13_API_BASE=http://$GW:11434/v1 OLLAMA_API_KEY=ollama ZS_E13_MODEL_SWAP=1   # revision 2: exempt the three model keys
export GEN_LEAVES_MODEL=qwen3.8:27b GEN_LEAVES_API_BASE=http://$GW:11434/v1 GEN_LEAVES_API_KEY_ENV=OLLAMA_API_KEY
export NO_PROXY=$GW,localhost,127.0.0.1 no_proxy=$GW,localhost,127.0.0.1
export E9_DIRECT=1 ZS_LANE_HOST=slac002 E9_REPO=$HOME/wt_e13 E9_LOG=$HOME/e13_lineages.log
unset E9_LAUNCH_ARGS
exec bash "$HOME/e13_tools/tools_0930/run_e13_lineage.sh" n "$1" >> "$HOME/e13_L$1.out" 2>&1
