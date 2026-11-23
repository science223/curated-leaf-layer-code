#!/usr/bin/env bash
# Start the E9 SYS arm on slac002 (预注册_循环内完整生命周期, freeze 10-02): plan zs_e9n_sys = hand-written leaves, the 15
# cells x 3, lanes 1-3 (parallel 3), direct API (no proxy), slac002 lanes. Start it by WMI only after the three LIFE
# lineages are done and the clock was re-checked. Usage: bash ~/e9_tools/go_e9_sys.sh
cd "$HOME/wt_v30_e9" || exit 2
export ZS_LANE_HOST=slac002
unset ZS_EGRESS_PROXY V30_EGRESS_PROXY HTTPS_PROXY HTTP_PROXY https_proxy http_proxy
exec "$HOME/Voyager_0911/venv/bin/python" -B _cc0911/launch_zs.py --plan zs_e9n_sys --job-timeout-h 12 >> "$HOME/e9_sys.out" 2>&1
