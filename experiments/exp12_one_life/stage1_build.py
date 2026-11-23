#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage one (预注册_从零积累阶段一_2026-09-28.md §4): rebuild a long life's skill library as it was
at action iteration T, harvest it with the UNCHANGED gov_pool rules, and write one union candidate
pool for the tournament plus a manifest of which candidates each (life, T) holds.

Offline and read-only on the source checkpoints. No Minecraft, no API.
Run in WSL:  python3 stage1_build.py [--selftest]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

GOV_DIR = os.path.expanduser("~/wt_v30_gov/_cc0924")
sys.path.insert(0, GOV_DIR)
sys.path.insert(0, os.path.dirname(GOV_DIR))
os.chdir(GOV_DIR)
import gov_pool as GP  # noqa: E402
from gov_harvest import RejectCounts  # noqa: E402

SRC = "/mnt/e/project/_audit_0911/v24_final/slac002_v26"
LIVES = {
    "w1": dict(ckpt="ckpt_A_flash_off_t1_v26OLD", checkpoints=[40, 80, 160], rows=102, succ=84, files=84,
               skills=57, md5_skills="5cef4f0992c40fadf6ef7395ef16d77d",
               md5_iters="a1e5eccb6517dc594045b451b98750a7"),
    "w2": dict(ckpt="ckpt_A_flash_off_t2_v26OLD", checkpoints=[40, 80, 161], rows=121, succ=106, files=102,
               skills=61, md5_skills="04590b66e31f83b58e49d69428b22629",
               md5_iters="a74b82c1653ec59e2a2a698f0fb0c56d"),
}
OUT = os.path.expanduser("~/stage1_0928")
DEPOSIT_PREFIX = "Deposit useless items into the chest"  # Voyager never saves these as skills
VER_RE = re.compile(r"^(?P<base>.+?)V(?P<k>\d+)$")
EXEC_RE = re.compile(r"await\s+([A-Za-z_$][\w$]*)\s*\(")
TS_TOL_S = 120


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as fp:
        h.update(fp.read())
    return h.hexdigest()


def load_jsonl(path):
    with open(path, encoding="utf-8") as fp:
        return [json.loads(line) for line in fp if line.strip()]


def parse_ts(ts):
    import datetime as dt
    return dt.datetime.strptime(str(ts)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp()


def code_files(code_dir):
    """base name -> {version k: file stem}. NV{k}.js is version k of N only if N.js exists (Voyager's rewrite naming)."""
    stems = sorted(f[:-3] for f in os.listdir(code_dir) if f.endswith(".js"))
    present = set(stems)
    out = {}
    for s in stems:
        m = VER_RE.match(s)
        if m and m.group("base") in present:
            out.setdefault(m.group("base"), {})[int(m.group("k"))] = s
        else:
            out.setdefault(s, {})[1] = s
    return out


def map_versions(life):
    """Pair every saved code version with the successful task that produced it. Hard-fails on any mismatch."""
    cfg = LIVES[life]
    ck = os.path.join(SRC, cfg["ckpt"])
    its = load_jsonl(os.path.join(ck, "metrics", "iterations.jsonl"))
    probes = load_jsonl(os.path.join(ck, "metrics", "call_probe_compare.jsonl"))
    lib = json.load(open(os.path.join(ck, "skill", "skills.json"), encoding="utf-8"))
    code_dir = os.path.join(ck, "skill", "code")
    problems = []
    if len(its) != cfg["rows"] or len(probes) != cfg["rows"]:
        problems.append(f"rows its={len(its)} probes={len(probes)} expect={cfg['rows']}")
    for i, (r, p) in enumerate(zip(its, probes)):
        if int(p["iter"]) != int(r["iter"]) - int(r["n_steps"]) or bool(p["success"]) != bool(r["success"]):
            problems.append(f"row {i}: probe/iteration misaligned")
    succ = [(i, r, p) for i, (r, p) in enumerate(zip(its, probes)) if r.get("success")]
    if len(succ) != cfg["succ"]:
        problems.append(f"successes {len(succ)} expect {cfg['succ']}")
    saved = []
    skipped_deposit = []
    for i, r, p in succ:
        if str(r.get("task", "")).startswith(DEPOSIT_PREFIX):
            skipped_deposit.append((int(r["iter"]), r["task"]))
            continue
        m = EXEC_RE.search(str(p.get("exec_code") or ""))
        if not m:
            problems.append(f"row {i} iter {r['iter']}: no main function in exec_code")
            continue
        saved.append(dict(row=i, iter=int(r["iter"]), task=r["task"], name=m.group(1), ts=r.get("ts"),
                          exec_code=str(p.get("exec_code") or ""),
                          program_code=str(p.get("program_code") or ""), program_len=int(p.get("program_code_len") or 0)))
    files = code_files(code_dir)
    n_files = sum(len(v) for v in files.values())
    if n_files != cfg["files"]:
        problems.append(f"code files {n_files} expect {cfg['files']}")
    by_name = {}
    for s in saved:
        by_name.setdefault(s["name"], []).append(s)
    versions = {}
    max_dt = 0.0
    for name in sorted(set(by_name) | set(files)):
        tasks = sorted(by_name.get(name, []), key=lambda s: (s["iter"], s["row"]))
        vers = files.get(name, {})
        ks = sorted(vers)
        if ks != list(range(1, len(ks) + 1)) or len(ks) != len(tasks):
            problems.append(f"{name}: versions {ks} vs successful tasks {[t['iter'] for t in tasks]}")
            continue
        for k, t in zip(ks, tasks):
            stem = vers[k]
            fpath = os.path.join(code_dir, stem + ".js")
            with open(fpath, encoding="utf-8") as fp:
                content = fp.read()
            # the probe logs the executed program = saved code + newline + exec line
            # (e.g. "await mineOneLog(bot);"), cut at 4000 characters
            full = content + "\n" + t["exec_code"]
            ok = t["program_len"] == len(full) and t["program_code"] == full[:4000]
            if not ok:
                problems.append(f"{stem}: content differs from task iter {t['iter']} program_code")
            dt_s = abs(os.path.getmtime(fpath) - parse_ts(t["ts"])) if t.get("ts") else float("inf")
            max_dt = max(max_dt, dt_s)
            if dt_s > TS_TOL_S:
                problems.append(f"{stem}: file mtime vs task ts differ by {dt_s:.0f}s")
            versions.setdefault(name, []).append(dict(k=k, stem=stem, entry_iter=t["iter"], task=t["task"],
                                                      truncated_probe=t["program_len"] > 4000, code=content))
    extra_skills = sorted(set(lib) - set(versions))
    if extra_skills:
        problems.append(f"skills.json names without versions: {extra_skills}")
    return dict(life=life, ck=ck, lib=lib, versions=versions, problems=problems, max_ts_dev_s=max_dt,
                skipped_deposit=skipped_deposit, n_saved=len(saved), n_files=n_files)


def snapshot(versions, T):
    """S_T: per base name the latest version whose producing task ended at iteration <= T."""
    snap = {}
    for name, vs in versions.items():
        ok = [v for v in vs if v["entry_iter"] <= T]
        if ok:
            v = max(ok, key=lambda v: v["k"])
            snap[name] = {"code": v["code"], "stage1_version": v["k"], "stage1_entry_iter": v["entry_iter"]}
    return snap


def write_snapshot(life, T, snap, root="snap"):
    # keep the source ckpt directory name so gov_pool resolves the source seed exactly as for the original file
    d = os.path.join(OUT, root, f"{life}_T{T:03d}", LIVES[life]["ckpt"], "skill")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "skills.json")
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(snap, fp, ensure_ascii=False, indent=1)
    return path


def harvest(path):
    rejects, excludes, seed_hist, raw = RejectCounts(), RejectCounts(), {}, []
    GP.ingest_skills(path, GP.banned_names(), rejects, excludes, seed_hist, raw)
    return raw, dict(rejects), dict(excludes), seed_hist


def per_key_hashes(raw):
    out = {k: set() for k in GP.POOL_KEYS}
    for h in raw:
        out[h["key"]].add(h["hash"])
    return out


def rank_hits(raw, top_n=GP.TOP_N):
    """Same order as gov_pool.pick_candidates: n_success desc, code_len asc, hash; one entry per (key, hash)."""
    by = {}
    for h in raw:
        rec = by.setdefault((h["key"], h["hash"]), {"n_success": 0, "hit": h})
        rec["n_success"] += int(h.get("n_success_hit") or 0)
        if (h["code_len"], h["hash"]) < (rec["hit"]["code_len"], rec["hit"]["hash"]):
            rec["hit"] = h
    per_key = {k: [] for k in GP.POOL_KEYS}
    for (key, _h), rec in by.items():
        per_key[key].append(rec)
    for key in per_key:
        per_key[key].sort(key=lambda r: (-r["n_success"], r["hit"]["code_len"], r["hit"]["hash"]))
        per_key[key] = per_key[key][:top_n]
    return per_key


def build(args):
    GP.install_rewrite_cache()
    os.makedirs(os.path.join(OUT, "out"), exist_ok=True)
    report = {"sources": {}, "cells": {}, "problems": []}
    lives = {}
    for life, cfg in LIVES.items():
        ck = os.path.join(SRC, cfg["ckpt"])
        m_sk, m_it = md5(os.path.join(ck, "skill", "skills.json")), md5(os.path.join(ck, "metrics", "iterations.jsonl"))
        if (m_sk, m_it) != (cfg["md5_skills"], cfg["md5_iters"]):
            report["problems"].append(f"{life}: source md5 {m_sk}/{m_it} differs from the pre-registration")
        vm = map_versions(life)
        report["problems"] += [f"{life}: {p}" for p in vm["problems"]]
        report["sources"][life] = dict(md5_skills=m_sk, md5_iters=m_it, n_saved=vm["n_saved"], n_files=vm["n_files"],
                                       max_ts_dev_s=round(vm["max_ts_dev_s"], 1), skipped_deposit=vm["skipped_deposit"],
                                       n_names=len(vm["versions"]))
        lives[life] = vm
        with open(os.path.join(OUT, "out", f"versions_{life}.json"), "w", encoding="utf-8") as fp:
            json.dump({n: [{k: v[k] for k in ("k", "stem", "entry_iter", "task", "truncated_probe")} for v in vs]
                       for n, vs in vm["versions"].items()}, fp, ensure_ascii=False, indent=1)
    if report["problems"]:
        return report
    # snapshots + harvest
    union = {}   # (key, norm_hash) -> first hit (deterministic: shortest code, then hash)
    cells = {}
    for life, vm in lives.items():
        final = snapshot(vm["versions"], 10 ** 9)
        # §8.3 faithfulness: final snapshot == final skills.json, and identical harvest
        lib_final = vm["lib"]
        same_names = set(final) == set(lib_final)
        same_code = same_names and all(final[n]["code"] == lib_final[n]["code"] for n in lib_final)
        raw_final_file, _, _, _ = harvest(os.path.join(vm["ck"], "skill", "skills.json"))
        p_final = write_snapshot(life, 999, final)
        raw_final_snap, _, _, _ = harvest(p_final)
        same_harvest = per_key_hashes(raw_final_file) == per_key_hashes(raw_final_snap)
        report["sources"][life].update(final_snapshot_equals_skills_json=same_code, final_harvest_equal=same_harvest)
        if not (same_code and same_harvest):
            report["problems"].append(f"{life}: final snapshot not faithful (code {same_code}, harvest {same_harvest})")
        for T in LIVES[life]["checkpoints"]:
            snap = snapshot(vm["versions"], T)
            late = [n for n, r in snap.items() if r["stage1_entry_iter"] > T]
            if late:
                report["problems"].append(f"{life}@{T}: versions after T {late}")
            path = write_snapshot(life, T, snap)
            raw, rej, exc, sh = harvest(path)
            ranked = rank_hits(raw)
            cell = {"n_skills": len(snap), "rejects": rej, "excludes": exc, "seed_hist": sh, "keys": {}}
            for key, recs in ranked.items():
                cell["keys"][key] = []
                for rec in recs:
                    h = rec["hit"]
                    u = union.get((key, h["hash"]))
                    if u is None or (h["code_len"], h["hash"]) < (u["code_len"], u["hash"]):
                        union[(key, h["hash"])] = h
                    src = snap.get(h["source_name"], {})
                    cell["keys"][key].append({"norm_hash": h["hash"], "source_name": h["source_name"],
                                              "version": src.get("stage1_version"), "entry_iter": src.get("stage1_entry_iter"),
                                              "n_success": rec["n_success"], "code_len": h["code_len"]})
            cells[f"{life}@{T}"] = cell
    report["cells"] = {c: {k: len(v) for k, v in cell["keys"].items()} for c, cell in cells.items()}
    # union pool, one instrumented candidate per (key, norm_hash)
    rejects = RejectCounts()
    pool = {k: [] for k in GP.POOL_KEYS}
    id_of = {}
    for key in GP.POOL_KEYS:
        hs = sorted((h for (k, _), h in union.items() if k == key), key=lambda h: (h["code_len"], h["hash"]))
        for h in hs:
            cid = f"s1_{key}_{len(pool[key])}"
            built = GP.instrument_bundle(h["norm"], h.get("norm_libs") or [], h.get("lib_names") or [], cid, key, rejects)
            if not built:   # gov_pool.pick_candidates skips these silently too (e.g. smelt* bundles)
                report.setdefault("dropped_at_instrument", []).append(f"{key} {h['source_name']} {h['hash'][:12]}")
                continue
            code = built["code"]
            pool[key].append({
                "id": cid, "fn": built["fn"], "code": code, "code_hash": GP.sha256_text(code), "norm_hash": h["hash"],
                "source_path": h["source_path"], "source_seed": h["source_seed"], "source_kind": h.get("source_kind"),
                "source_name": h.get("source_name"), "round": None, "n_success": 1,
                "deps": list(h.get("lib_names") or []), "mined_blocks": list(h.get("mined_blocks") or []),
                "normalized": list(h.get("notes") or []) + list(built.get("normalized") or []),
                "rewrites": built.get("rewrites") or [], "target_n": 1,
            })
            id_of[(key, h["hash"])] = cid
    manifest = {c: {k: [id_of[(k, e["norm_hash"])] for e in v if (k, e["norm_hash"]) in id_of]
                    for k, v in cell["keys"].items()} for c, cell in cells.items()}
    with open(os.path.join(OUT, "out", "stage1_union_pool.json"), "w", encoding="utf-8") as fp:
        json.dump({"candidates": pool, "note": "stage one union pool (pre-registration 09-28 §4-5)"}, fp, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT, "out", "stage1_manifest.json"), "w", encoding="utf-8") as fp:
        json.dump({"manifest": manifest, "cells": cells}, fp, ensure_ascii=False, indent=1)
    report["cells"] = {c: {k: len(v) for k, v in m.items()} for c, m in manifest.items()}
    report["union_per_key"] = {k: len(v) for k, v in pool.items()}
    report["union_total"] = sum(len(v) for v in pool.values())
    return report


def selftest():
    """§8.4 test of the test: the truncation must exclude a planted future version, and a no-truncation
    mutant must be caught (w1 has no diamond leaf before iteration 89)."""
    vs = {"mineX": [{"k": 1, "entry_iter": 10, "code": "a"}, {"k": 2, "entry_iter": 41, "code": "b"}]}
    assert snapshot(vs, 40)["mineX"]["code"] == "a", "planted future version leaked into S_40"
    assert "mineX" not in snapshot(vs, 9), "version before its entry"
    vm = map_versions("w1")
    assert not vm["problems"], vm["problems"]
    GP.install_rewrite_cache()

    def diamond_leaves(snap_fn):
        path = write_snapshot("w1", 40, snap_fn(), root="snap_selftest")
        raw, _, _, _ = harvest(path)
        return [h["source_name"] for h in raw if h["key"] == "diamond"]
    good = diamond_leaves(lambda: snapshot(vm["versions"], 40))
    mutant = diamond_leaves(lambda: snapshot(vm["versions"], 10 ** 9))   # "no truncation" mutant
    assert good == [], f"S_40 of w1 has diamond leaves {good}"
    assert mutant, "mutant (no truncation) was not caught: final library shows no diamond leaves"
    print("selftest ok: planted future excluded; no-truncation mutant caught with", len(mutant), "diamond leaves")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
    else:
        rep = build(a)
        with open(os.path.join(OUT, "out", "stage1_build_report.json"), "w", encoding="utf-8") as fp:
            json.dump(rep, fp, ensure_ascii=False, indent=1)
        print(json.dumps({k: rep[k] for k in rep if k != "cells"}, ensure_ascii=False, indent=1)[:6000])
        print(json.dumps(rep.get("cells"), ensure_ascii=False))
