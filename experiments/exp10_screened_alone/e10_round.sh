#!/usr/bin/env bash
# One E10 round on slac002: plan scr with --trials-max N, or the drift plan. Usage: e10_round.sh <scr N | drift>
export E10_HOST=slac002 E10_LZ_MD5=8e46d0c1
if [ "$1" = scr ]; then export E10_LAUNCH_ARGS="--trials-max $2"; fi
exec bash ~/e10_tools/run_e10.sh "$1"
