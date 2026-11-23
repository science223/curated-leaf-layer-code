#!/usr/bin/env bash
# E6 replacement merge (预注册_匹配重跑与新种子_2026-09-29, "替换规则"): after the reruns of the 23 degraded jobs have
# finished, build ~/V30_r12m_<group>/_cc0924/ with every line of the original results except those of degraded jobs, plus
# every line of the reruns; the same for the per-job stdout files; write ~/r12m_jobs.txt (the original job list with the
# directories renamed) and copies of the preregistered health and analysis scripts that read the merged directories
# (only the path prefix / job-list name differ). Prints counts only, never outcomes. Usage: r12_merge.sh [--dry-run]
set -eu
DRY=${1:-}
if [ -z "$DRY" ]; then
  pgrep -f "[r]12r_queue.sh" > /dev/null && { echo "the rerun queue is still running"; exit 3; }
  grep -q "r12r queue done" ~/r12r_queue.log || { echo "the rerun queue has not finished"; exit 3; }
fi
python3 - "$DRY" <<'PY'
import collections, glob, json, os, shutil, sys
dry = sys.argv[1] == "--dry-run"
H = os.path.expanduser("~")
orig = [l.strip() for l in open(f"{H}/r12_jobs.txt") if l.strip() and not l.startswith("#")]
rer = [l.strip() for l in open(f"{H}/r12r_jobs.txt") if l.strip()]
bad = collections.defaultdict(set)                     # group -> degraded seeds (full seed strings)
for j in rer:
    d, arm, mode, online, pool, seed = j.split(":")
    bad[arm].add(seed)
def seed_of(obj):
    s = obj.get("seed")
    if s is None and isinstance(obj.get("row"), dict):
        s = obj["row"].get("seed")
    return None if s is None else str(s)
summary = {}
for arm in sorted({j.split(":")[1] for j in orig}):
    src = f"{H}/V30_r12_{arm}/_cc0924"
    rr = f"{H}/V30_r12r_{arm}/_cc0924"
    dst = f"{H}/V30_r12m_{arm}/_cc0924"
    kept, dropped, added, unknown = [], 0, [], 0
    for line in open(f"{src}/eval_{arm}.jsonl"):
        try:
            obj = json.loads(line)
        except Exception:
            unknown += 1
            continue
        s = seed_of(obj)
        if s is not None and s in bad[arm]:
            dropped += 1
        else:
            kept.append(line)
    if bad[arm]:
        f = f"{rr}/eval_{arm}.jsonl"
        if os.path.exists(f):
            for line in open(f):
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                s = seed_of(obj)
                if s is None or s in bad[arm]:
                    added.append(line)
    done = lambda lines: collections.Counter(seed_of(json.loads(x)) for x in lines if json.loads(x).get("event") == "job_done")
    kd, ad = done(kept), done(added)
    missing = [s for s in bad[arm] if ad.get(s, 0) != 3]
    summary[arm] = {"orig_lines_kept": len(kept), "orig_lines_dropped": dropped, "rerun_lines": len(added),
                    "degraded_seeds": len(bad[arm]), "rerun_seeds_with_3_games": len(bad[arm]) - len(missing),
                    "unparsed": unknown}
    if not dry:
        if missing:
            sys.exit(f"{arm}: reruns incomplete for seeds {sorted(missing)}")
        os.makedirs(dst, exist_ok=True)
        with open(f"{dst}/eval_{arm}.jsonl", "w") as fp:
            fp.writelines(kept + added)
        for f in glob.glob(f"{src}/eval_{arm}_*.stdout"):
            if not any(f.endswith(f"_{s[-3:]}.stdout") for s in bad[arm]):
                shutil.copy(f, dst)
        for s in bad[arm]:
            shutil.copy(f"{rr}/eval_{arm}_{s[-3:]}.stdout", dst)
for arm, v in summary.items():
    print(arm, v)
if not dry:
    with open(f"{H}/r12m_jobs.txt", "w") as fp:
        fp.write("\n".join(j.replace("V30_r12_", "V30_r12m_", 1) for j in orig) + "\n")
PY
[ -n "$DRY" ] && exit 0
sed -e 's#os.path.join(HOME, "r12_jobs.txt")#os.path.join(HOME, "r12m_jobs.txt")#' ~/r12_health.py > ~/r12m_health.py
sed -e 's#f"~/V30_r12_{a}/_cc0924/eval_{a}.jsonl"#f"~/V30_r12m_{a}/_cc0924/eval_{a}.jsonl"#' ~/r12_analyze.py > ~/r12m_analyze.py
grep -q '"r12m_jobs.txt"' ~/r12m_health.py && grep -q 'V30_r12m_' ~/r12m_analyze.py || { echo "script copies failed"; exit 7; }
echo "r12m_health.py $(md5sum < ~/r12m_health.py | cut -c1-8) r12m_analyze.py $(md5sum < ~/r12m_analyze.py | cut -c1-8)"
diff <(sed 's#r12m_jobs#r12_jobs#' ~/r12m_health.py) ~/r12_health.py > /dev/null && diff <(sed 's#V30_r12m_#V30_r12_#' ~/r12m_analyze.py) ~/r12_analyze.py > /dev/null \
  && echo "the copies differ from the preregistered scripts only in the paths"
