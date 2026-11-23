#!/usr/bin/env bash
# Start the E12 SYS arm on slac002 after the three LIFE lineages (same shape as go_e9_sys.sh).
cd "$HOME/wt_e12" || exit 2
export ZS_LANE_HOST=slac002
unset ZS_EGRESS_PROXY V30_EGRESS_PROXY HTTPS_PROXY HTTP_PROXY https_proxy http_proxy
unset GOV_QUIESCE_MS GOV_QUIESCE_CAP_MS GOV_LATE_FENCE GOV_LEDGER_MODE GOV_RETIRE_MODE GOV_FALLBACK GOV_LEDGER_FILE GOV_LEDGER_LINEAGE_ID GOV_ALLOW_EMPTY_POOL
exec "$HOME/Voyager_0911/venv/bin/python" -B _cc0911/launch_zs.py --plan zs_e12n_sys --job-timeout-h 12 >> "$HOME/e12_sys.out" 2>&1
