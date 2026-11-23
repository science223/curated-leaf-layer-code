#!/usr/bin/env bash
# Start the E13 SYS arm on slac002 after the three LIFE lineages (as go_e12_sys.sh, with the local Qwen3.8-27B).
cd "$HOME/wt_e13" || exit 2
GW=$(ip route | awk '/default/{print $3; exit}')
export ZS_LANE_HOST=slac002 E13_API_BASE=http://$GW:11434/v1 OLLAMA_API_KEY=ollama ZS_E13_MODEL_SWAP=1   # revision 2
export NO_PROXY=$GW,localhost,127.0.0.1 no_proxy=$GW,localhost,127.0.0.1
unset ZS_EGRESS_PROXY V30_EGRESS_PROXY HTTPS_PROXY HTTP_PROXY https_proxy http_proxy
unset GOV_QUIESCE_MS GOV_QUIESCE_CAP_MS GOV_LATE_FENCE GOV_LEDGER_MODE GOV_RETIRE_MODE GOV_FALLBACK GOV_LEDGER_FILE GOV_LEDGER_LINEAGE_ID GOV_ALLOW_EMPTY_POOL
exec "$HOME/Voyager_0911/venv/bin/python" -B _cc0911/launch_zs.py --plan zs_e13n_sys --job-timeout-h 12 >> "$HOME/e13_sys.out" 2>&1
