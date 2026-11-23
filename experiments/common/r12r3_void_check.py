#!/usr/bin/env python3
"""E6 round-3 reruns (预注册_匹配重跑与新种子_2026-09-29.md, "第三轮重跑", 事后作废): mechanism-only check of the three
void rules for every rerun job, run on slac002 after "r12r queue done". A job is voided when ANY of these holds:
  1 its run interval overlaps a degraded clock hour (pooled E6-rerun + E7 calls of that hour >= 15 and
    StepExecutionTimeout > 10 %; a call belongs to the hour of its log-line timestamp; only lines from 09-30 on)
  2 its run interval overlaps a clock-anomaly minute of ~/clk_probe.log (a consecutive-sample difference < -150 ms, or a
    sample outside +-300 ms)
  3 it has a crash row (a fail reason starting "crash:" and no calls) or it did not write exactly 3 games
Job interval = its "start slot=" line to the next "end slot=" line of the same slot, after the last "r12r queue start".
Reads only: queue-log start/end lines, obtain-log call lines (timestamp + StepExecutionTimeout) and "job crashed" lines,
the probe log, and from result rows only event / seed / scene / env_dead / "is there a crash: reason" / "is calls empty".
Never reads or prints success, per-job call counts or any other fail reason.
Usage: python3 r12r3_void_check.py [--home DIR]
"""
import collections
import datetime as dt
import glob
import json
import os
import re
import sys

CONDS = ("full", "off", "retire", "reorder", "quar")
CALL_RE = re.compile(r"call [0-9]/8")
TS = "%Y-%m-%d %H:%M:%S"
BENIGN_TB = "_cuda_bindings_redirector.pth"


def ts(line):
    try:
        return dt.datetime.strptime(line[:19], TS)
    except ValueError:
        return None


def jobs_from_queue(H, q="r12r"):
    lines = open(f"{H}/{q}_queue.log", errors="replace").read().splitlines()
    k = max((i for i, x in enumerate(lines) if f"{q} queue start" in x), default=None)
    if k is None:
        sys.exit(f"no '{q} queue start' line in ~/{q}_queue.log")
    open_, out, done = {}, [], False
    for x in lines[k + 1:]:
        m = re.search(r" start slot=(\d+) (\S+)", x)
        if m:
            open_[m.group(1)] = (m.group(2), ts(x))
            continue
        m = re.search(r" end slot=(\d+)", x)
        if m and m.group(1) in open_:
            job, t0 = open_.pop(m.group(1))
            out.append((job, t0, ts(x)))
        if f"{q} queue done" in x:
            done = True
    for s, (job, t0) in open_.items():
        out.append((job, t0, None))
    return out, done


def hour_table(H):
    files = glob.glob(f"{H}/V30_r12r*/_cc0924/obtain_mc_*.log")
    for c in CONDS:
        files += glob.glob(f"{H}/V30_e7_{c}_L[123]/_cc0924/obtain_mc_e7_*.log")
    files += glob.glob(f"{H}/V30_e7_void_*/*/_cc0924/obtain_mc_e7_*.log")
    calls, sto = collections.Counter(), collections.Counter()
    for f in files:
        for x in open(f, errors="replace"):
            if not CALL_RE.search(x) or x[:10] < "2026-09-30":
                continue
            h = x[:13]
            calls[h] += 1
            if "StepExecutionTimeout" in x:
                sto[h] += 1
    return calls, sto, len(files)


def probe(H):
    """Returns (anomaly minutes {minute: why}, covered minutes set, sample count, worst step, min, max, gaps>30s)."""
    bad, cov = {}, set()
    n, worst, mn, mx, gaps = 0, 0, None, None, []
    day, prev_t, prev_v = None, None, None
    for x in open(f"{H}/clk_probe.log", errors="replace"):
        f = x.split()
        if len(f) >= 3 and re.match(r"\d{4}-\d{2}-\d{2}$", f[0]):   # header / end line of one probe run
            day = dt.datetime.strptime(f[0], "%Y-%m-%d").date()
            prev_t, prev_v = None, None
            continue
        if len(f) != 3 or day is None or not re.match(r"-?\d+$", f[1]) or not re.match(r"\d\d:\d\d:\d\d$", f[0]):
            continue
        t = dt.datetime.combine(day, dt.datetime.strptime(f[0], "%H:%M:%S").time())
        if prev_t is not None and t < prev_t - dt.timedelta(hours=12):   # midnight
            day += dt.timedelta(days=1)
            t += dt.timedelta(days=1)
        v = int(f[1])
        n += 1
        mn = v if mn is None else min(mn, v)
        mx = v if mx is None else max(mx, v)
        key = t.strftime("%Y-%m-%d %H:%M")
        cov.add(key)
        why = []
        if abs(v) > 300:
            why.append(f"offset={v}")
        if prev_v is not None:
            d = v - prev_v
            worst = min(worst, d)
            if d < -150:
                why.append(f"step={d}")
            if (t - prev_t).total_seconds() > 30:
                gaps.append((prev_t, t))
        if why:
            bad.setdefault(key, []).extend(why)
        prev_t, prev_v = t, v
    return bad, cov, n, worst, mn, mx, gaps


def job_rows(H, d, arm, seed):
    games, crash, dead, scenes = 0, 0, 0, []
    f = f"{H}/{d}/_cc0924/eval_{arm}.jsonl"
    if os.path.exists(f):
        for x in open(f, errors="replace"):
            try:
                r = json.loads(x)
            except Exception:
                continue
            if r.get("event") != "job_done":
                continue
            w = r.get("row") or {}
            if str(w.get("seed")) != seed:
                continue
            games += 1
            scenes.append(w.get("scene"))
            fr = w.get("fail_reasons") or []
            if any(str(y).startswith("crash:") for y in fr) and not w.get("calls"):
                crash += 1
            dead += 1 if w.get("env_dead") else 0
    kicked = 0
    lg = f"{H}/{d}/_cc0924/obtain_mc_{arm}_{seed[-3:]}.log"
    if os.path.exists(lg):
        kicked = sum(1 for x in open(lg, errors="replace") if "job crashed" in x)
    tb = None
    so = f"{H}/{d}/_cc0924/eval_{arm}_{seed[-3:]}.stdout"
    if os.path.exists(so):
        text = open(so, errors="replace").read().splitlines()
        tb = max(0, sum(1 for y in text if "Traceback (most recent call last)" in y)
                 - sum(1 for y in text if y.startswith("Error processing line") and BENIGN_TB in y))
    return games, crash, dead, kicked, tb, len(set(scenes))


def main(H, q="r12r"):
    jobs, done = jobs_from_queue(H, q)
    calls, sto, nfiles = hour_table(H)
    bad_min, cov, nsamp, worst, mn, mx, gaps = probe(H)
    degraded = {h for h in calls if calls[h] >= 15 and sto[h] * 10 > calls[h]}
    print(f"queue done line: {done}; jobs with a start line: {len(jobs)}; obtain logs read: {nfiles}")
    first = min((t0 for _, t0, _ in jobs if t0), default=None)
    print("\nhour            calls  sto  degraded   (pooled E6 reruns + E7, from the first job start)")
    for h in sorted(calls):
        if first and h < first.strftime("%Y-%m-%d %H"):
            continue
        print(f"{h}   {calls[h]:5} {sto[h]:4}  {'YES' if h in degraded else ('(<15 calls)' if calls[h] < 15 else '')}")
    print(f"\nprobe: {nsamp} samples, offset {mn}..{mx} ms, worst consecutive step {worst} ms, "
          f"anomaly minutes {len(bad_min)}, gaps >30 s {len(gaps)}")
    for k in sorted(bad_min)[:40]:
        print(f"  anomaly {k}: {','.join(bad_min[k][:4])}")
    for a, b in gaps[:20]:
        print(f"  gap {a:%F %T} -> {b:%F %T}")
    print("\narm     seed start               end                 min  degH anomM probe_cov games crash env_dead kicked tb  VOID")
    per_arm = collections.defaultdict(lambda: [0, 0, 0, 0])
    voids = []
    for job, t0, t1 in jobs:
        d, arm, mode, online, pool, seed = job.split(":")
        why = []
        if t1 is None:
            why.append("no-end-line")
            t1e = dt.datetime.max
        else:
            t1e = t1
        hrs = [h for h in degraded if t0 < dt.datetime.strptime(h, "%Y-%m-%d %H") + dt.timedelta(hours=1)
               and t1e >= dt.datetime.strptime(h, "%Y-%m-%d %H")]
        mins = [m for m in bad_min if t0 <= dt.datetime.strptime(m, "%Y-%m-%d %H:%M") + dt.timedelta(seconds=59)
                and t1e >= dt.datetime.strptime(m, "%Y-%m-%d %H:%M")]
        span = []
        if t1 is not None:
            m = t0.replace(second=0)
            while m <= t1:
                span.append(m.strftime("%Y-%m-%d %H:%M"))
                m += dt.timedelta(minutes=1)
        covered = sum(1 for m in span if m in cov)
        games, crash, dead, kicked, tb, nscene = job_rows(H, d, arm, seed)
        if hrs:
            why.append("degraded-hour " + ",".join(sorted(h[-2:] for h in hrs)))
        if mins:
            why.append(f"clock-anomaly-minutes={len(mins)}")
        if crash:
            why.append(f"crash-rows={crash}")
        if games != 3 or nscene != games:
            why.append(f"games={games}")
        if why:
            voids.append((arm, seed, why))
        pa = per_arm[arm]
        pa[0] += games
        pa[1] += crash
        pa[2] += dead
        pa[3] += kicked
        print(f"{arm:7} {seed[-3:]}  {t0:%F %T} {(f'{t1:%F %T}' if t1 else '-'):19} {len(span):4} {len(hrs):4} {len(mins):5} "
              f"{covered:4}/{len(span):<4} {games:5} {crash:5} {dead:8} {kicked:6} {str(tb):3} {'VOID: ' + '; '.join(why) if why else ''}")
    print("\nper arm: games crash_rows env_dead job_crashed_lines")
    for arm, v in sorted(per_arm.items()):
        print(f"  {arm:7} {v[0]:3} {v[1]:3} {v[2]:3} {v[3]:3}")
    uncovered = [(j, t0) for j, t0, t1 in jobs if t1 is not None]
    print(f"\njobs voided: {len(voids)} of {len(jobs)}")
    for arm, seed, why in voids:
        print(f"  {arm} {seed}: {'; '.join(why)}")
    return 0


if __name__ == "__main__":
    home = os.path.expanduser("~")
    if "--home" in sys.argv:
        home = sys.argv[sys.argv.index("--home") + 1]
    queue = "r12r"   # --queue r12r2: the single-job rerun queue (~/r12r2_queue.log, "r12r2 queue start/done")
    if "--queue" in sys.argv:
        queue = sys.argv[sys.argv.index("--queue") + 1]
    sys.exit(main(home, queue))
