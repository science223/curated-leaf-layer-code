# E7 v4 mechanism check (no outcomes): per lineage, per finished game, ledger trail vs cumulative tries - unquiet,
# step timeouts, env retries, stale lines. Prints no success/failure or scores.
python3 - <<'PY'
import glob, json, os, re
log = open(os.path.expanduser("~/e7_lineages.log")).read().splitlines()
bad = 0
for C in ("full", "off", "retire", "reorder", "quar"):
    for L in (1, 2, 3):
        D = os.path.expanduser(f"~/V30_e7_{C}_L{L}")
        f = f"{D}/_cc0924/e7_{C}_L{L}.jsonl"
        if not os.path.exists(f):
            continue
        rows = [json.loads(x)["row"] for x in open(f) if x.strip() and '"row"' in x]
        trail = {int(m.group(1)): m.group(2) for m in (re.search(rf"E7 {C} L{L} game (\d+) end ledger_calls=(\S+)", x) for x in log) if m}
        cum, out = 0, []
        for n, w in enumerate(rows, 1):
            cum += int(w.get("gov_tries") or 0) - int(w.get("gov_unquiet") or 0)
            t = trail.get(n)
            ok = t is not None and t.isdigit() and int(t) == cum
            bad += not ok
            out.append(f"g{n}:{t}/{cum}{'' if ok else '!'}")
        calls = sum(1 for g in glob.glob(f"{D}/_cc0924/obtain_mc_e7_*_g*.log") for x in open(g) if re.search(r"call \d/8", x))
        sto = sum(1 for g in glob.glob(f"{D}/_cc0924/obtain_mc_e7_*_g*.log") for x in open(g) if re.search(r"call \d/8", x) and "StepExecutionTimeout" in x)
        stale = sum(open(m, errors="replace").read().count(" stale ") for m in glob.glob(f"{D}/_cc0924/obtain_mc_*/**/mineflayer/*.log", recursive=True))
        retries = sum(1 for x in log if f"E7 {C} L{L} game" in x and " RETRY " in x)
        print(f"{C} L{L}: trail(ledger/cum) {' '.join(out) or '-'} | calls {calls} step_timeouts {sto} stale_lines {stale} env_retries {retries}")
print("IDENTITY MISMATCHES:", bad)
PY
