#!/usr/bin/env bash
# Print "OK" if the clock probe is fresh (<= 30 s) and its last 120 samples stay within +-150 ms with no step < -150 ms
# (the E9 check); otherwise "BAD <why>". Read-only.
f=~/clk_probe.log
[ -f "$f" ] || { echo "BAD no probe log"; exit 1; }
age=$(( $(date +%s) - $(stat -c %Y $f) ))
read -r back mn mx ns <<< "$(tail -120 $f | awk 'NF==3 && $2 ~ /^-?[0-9]+$/ { v=$2; if (have && v-p<wb) wb=v-p; if (!have||v>mx) mx=v; if (!have||v<mn) mn=v; p=v; have=1; n++ } END {print wb+0, mn+0, mx+0, n+0}')"
if [ "$age" -le 30 ] && [ "$ns" -ge 60 ] && [ "$back" -ge -150 ] && [ "$mn" -ge -150 ] && [ "$mx" -le 150 ]; then echo "OK offset $mn..$mx ms, worst step $back ms"; exit 0; fi
echo "BAD probe age ${age}s, samples $ns, offset $mn..$mx ms, worst step $back ms"; exit 1
