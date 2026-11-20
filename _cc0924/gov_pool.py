#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Full candidate pool for the 09-26 pre-registered tournament.

Offline, read-only, no API. Reuses v4 gates and source transforms.
Does not read the live pilot tree ~/wt_v30_pilot0926/runs_0911.
"""
from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from gov_harvest import (  # noqa: E402
    RejectCounts,
    instrument_code,
    load_json,
    load_jsonl,
    node_rewrite,
    normalize_code,
    obtain_internal_names,
    obtain_class_key,
    primitive_names,
    rename_code,
    sha256_text,
    source_seed_of,
    static_gates,
)
from gov_leaf_alias import SCENE_LEAF_KEYS, leaf_alias  # noqa: E402
from voyager.intent import infer_task_intent  # noqa: E402
from voyager.v30_flags import V30_NAMED_ACTIONS  # noqa: E402
from voyager.verify import parse_task  # noqa: E402

FORBIDDEN_READ = os.path.expanduser("~/wt_v30_pilot0926/runs_0911")
SCAN_ROOTS = [
    os.path.expanduser("~"),
    "/mnt/e/homework/research/voyager",
]
SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        "node_modules",
        "venv",
        ".venv",
        "__pycache__",
        ".cache",
        ".npm",
        ".local",
        ".cursor",
        ".claude",
        ".pytest_cache",
        "chroma",
        "chromadb",
        "obtain_mc_work",
        "obtain_mc_keep",
        "skills_mc_work",
        ".huggingface",
        ".nv",
    }
)
POOL_KEYS = (
    "log",
    "cobblestone",
    "coal",
    "raw_iron",
    "raw_gold",
    "redstone",
    "diamond",
)
EVAL_SEEDS = frozenset(
    {20260902007, 20260902008, 20260902009, 20260902012, 20260902013}
)
TOURNAMENT_SEEDS = frozenset({20260902020, 20260902021})
BLOCKED_SOURCE_SEEDS = EVAL_SEEDS | TOURNAMENT_SEEDS
SEED_BASE = 20260902000
CKPT_T_RE = re.compile(r"ckpt_[^/\\]*_t(\d+)", re.I)
TOP_N = 12
OUT_JSON = os.path.join(REPO, "_gov", "pool_full.json")


def forbidden_path(path):
    path = os.path.abspath(path)
    forbid = os.path.abspath(FORBIDDEN_READ)
    return path == forbid or path.startswith(forbid + os.sep)


def mined_blocks(code):
    ast = node_rewrite({"op": "mined_blocks", "code": code or ""})
    if ast.get("error"):
        return [], ast
    blocks = [str(x) for x in (ast.get("blocks") or []) if x]
    return blocks, ast


def mined_keys(code):
    blocks, ast = mined_blocks(code)
    keys = set()
    for b in blocks:
        k = leaf_alias(b)
        if k in POOL_KEYS:
            keys.add(k)
    return keys, blocks, ast


def name_class_key(text):
    """Classify from subgoal text or skill/function name. None if not a pool key."""
    text = str(text or "").strip()
    if not text:
        return None
    k = obtain_class_key(text)
    if k in POOL_KEYS:
        return k
    intent = infer_task_intent(text, source="skill_name")
    for item in intent.get("target_items") or []:
        aliased = leaf_alias(item)
        if aliased in POOL_KEYS:
            return aliased
    parsed = parse_task(text)
    if parsed is not None:
        raw = str(parsed.raw_item or "")
        targets = list(parsed.targets or [])
        cand = raw or (targets[0] if targets else "")
        aliased = leaf_alias(cand) if cand else None
        if aliased in POOL_KEYS:
            return aliased
    return None


def classify_key(name_text, code):
    """Name/subgoal AND mined/found block must agree on the same pool key."""
    name_key = name_class_key(name_text)
    keys, blocks, ast = mined_keys(code)
    if name_key is None and not keys:
        return None, "not_obtain_class", blocks, ast
    if name_key is None:
        return None, "name_not_pool_key", blocks, ast
    if name_key not in keys:
        return None, "name_block_mismatch", blocks, ast
    return name_key, None, blocks, ast


def source_seed_from_ckpt_path(path):
    m = CKPT_T_RE.search(str(path or "").replace("\\", "/"))
    if not m:
        return None
    try:
        n = int(m.group(1))
    except (TypeError, ValueError):
        return None
    return SEED_BASE + n


def run_meta_near(path):
    """Look for run_meta / zeroshot_result beside a metrics or skill file."""
    cur = os.path.abspath(path)
    if os.path.isfile(cur):
        cur = os.path.dirname(cur)
    for _ in range(6):
        metrics = os.path.join(cur, "metrics")
        ckpt = os.path.join(cur, "ckpt")
        candidates = [
            os.path.join(cur, "run_meta.json"),
            os.path.join(metrics, "run_meta.json"),
            os.path.join(ckpt, "metrics", "run_meta.json"),
            os.path.join(cur, "zeroshot_result.json"),
            os.path.join(metrics, "zeroshot_result.json"),
            os.path.join(ckpt, "metrics", "zeroshot_result.json"),
        ]
        result = None
        meta = None
        for p in candidates:
            if forbidden_path(p) or not os.path.isfile(p):
                continue
            data = load_json(p, None)
            if not isinstance(data, dict):
                continue
            if p.endswith("run_meta.json") and meta is None:
                meta = data
            else:
                result = data
        seed = source_seed_of(result or {}, meta or {})
        if seed is not None:
            return seed
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


def resolve_source_seed(path, kind):
    """Return (seed_or_None, how). how is zeroshot|longrun|unknown."""
    if kind == "zeroshot":
        seed = run_meta_near(path)
        if seed is not None:
            return int(seed), "zeroshot"
        return None, "unknown"
    seed = source_seed_from_ckpt_path(path)
    if seed is not None:
        return int(seed), "longrun"
    seed = run_meta_near(path)
    if seed is not None:
        return int(seed), "zeroshot"
    return None, "unknown"


def skill_n_success(rec):
    if not isinstance(rec, dict):
        return 1
    for k in ("n_success", "num_success", "successes", "success_count"):
        if rec.get(k) is not None:
            try:
                return max(1, int(rec[k]))
            except (TypeError, ValueError):
                pass
    return 1


def zeroshot_row_success(crow, step):
    for src in (step or {}, crow or {}):
        for k in ("success", "critic_success", "task_success"):
            v = src.get(k)
            if v is True:
                return True
            if v is False:
                return False
    status = str((step or {}).get("status") or (crow or {}).get("status") or "").lower()
    if status in ("success", "ok", "passed"):
        return True
    if status in ("fail", "failed", "error"):
        return False
    return bool((crow or {}).get("executed"))


def iter_source_files(roots=None):
    roots = list(roots or SCAN_ROOTS)
    seen = set()
    for root in roots:
        root = os.path.abspath(root)
        if not os.path.isdir(root):
            continue
        if forbidden_path(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            if forbidden_path(dirpath):
                dirnames[:] = []
                continue
            dirnames[:] = [
                d
                for d in dirnames
                if d not in SKIP_DIR_NAMES and not forbidden_path(os.path.join(dirpath, d))
            ]
            if "zeroshot_code.jsonl" in filenames:
                path = os.path.join(dirpath, "zeroshot_code.jsonl")
                ap = os.path.abspath(path)
                if ap not in seen and not forbidden_path(ap):
                    seen.add(ap)
                    yield "zeroshot", ap
            if os.path.basename(dirpath) == "skill" and "skills.json" in filenames:
                path = os.path.join(dirpath, "skills.json")
                ap = os.path.abspath(path)
                if ap not in seen and not forbidden_path(ap):
                    seen.add(ap)
                    yield "skills", ap


def load_skills_dict(path):
    data = load_json(path, {})
    out = {}
    if isinstance(data, dict):
        for name, rec in data.items():
            if isinstance(rec, dict) and rec.get("code"):
                out[str(name)] = rec
    return out


LOCAL_FN_RE = re.compile(
    r"(?:async\s+)?function\s+([A-Za-z_]\w*)"
    r"|(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*(?:async\s*)?(?:function|\([^)]*\)\s*=>|[A-Za-z_]\w*\s*=>)"
)
BUILTIN_CALLS = frozenset(
    {
        "parseInt",
        "parseFloat",
        "isNaN",
        "isFinite",
        "Number",
        "String",
        "Boolean",
        "Array",
        "Object",
        "Date",
        "Error",
        "TypeError",
        "JSON",
        "Promise",
        "Set",
        "Map",
        "WeakMap",
        "setTimeout",
        "setInterval",
        "clearTimeout",
        "clearInterval",
        "setImmediate",
        "clearImmediate",
        "require",
        "Buffer",
        "Vec3",
        "GoalBlock",
        "GoalNear",
        "GoalXZ",
        "GoalFollow",
        "GoalY",
        "encodeURIComponent",
        "decodeURIComponent",
        "parse",
        "stringify",
        "keys",
        "values",
        "assign",
        "from",
        "of",
        "min",
        "max",
        "floor",
        "ceil",
        "abs",
        "round",
        "random",
        "now",
        "log",
        "warn",
        "error",
        "info",
    }
)


def defined_fn_names(code, ast=None):
    names = set()
    if ast is None:
        ast = node_rewrite({"op": "calls", "code": code or ""})
    for fn in ast.get("fns") or []:
        if isinstance(fn, dict) and fn.get("name"):
            names.add(str(fn["name"]))
        elif isinstance(fn, str):
            names.add(fn)
    for m in LOCAL_FN_RE.finditer(code or ""):
        names.add(m.group(1) or m.group(2))
    return names


def collect_deps(code, lib, banned, rejects):
    """Direct+indirect calls into the same skills.json. Missing skill-like callees → None."""
    ast = node_rewrite({"op": "calls", "code": code or ""})
    own = defined_fn_names(code, ast)
    calls = list(ast.get("calls") or [])
    needed = []
    seen = set()
    stack = [c for c in calls if c in lib]
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        if name in banned:
            continue
        needed.append(name)
        rec = lib.get(name)
        lib_code = str((rec or {}).get("code") or "") if isinstance(rec, dict) else str(rec or "")
        lib_ast = node_rewrite({"op": "calls", "code": lib_code})
        lib_own = defined_fn_names(lib_code, lib_ast)
        for c2 in lib_ast.get("calls") or []:
            if c2 in lib and c2 not in seen:
                stack.append(c2)
            elif (
                c2 not in banned
                and c2 not in lib_own
                and c2 not in lib
                and c2 not in BUILTIN_CALLS
            ):
                rejects.add("missing_dep")
                return None
    missing = []
    for c in calls:
        if c in banned or c in own or c in lib or c in BUILTIN_CALLS:
            continue
        missing.append(c)
    if missing:
        rejects.add("missing_dep")
        return None
    return needed


def gate_bundle(code, lib_codes, rejects):
    reasons, ast = static_gates(code)
    if reasons:
        for r in reasons:
            rejects.add("gate:" + r.split(":")[0])
        return None
    for lc in lib_codes:
        lr, _ = static_gates(lc)
        if lr:
            for r in lr:
                rejects.add("lib_gate:" + r.split(":")[0])
            rejects.add("lib_static_fail")
            return None
    norm, notes, nast = normalize_code(code)
    if not norm:
        rejects.add("parser_unavailable")
        return None
    norm_libs = []
    lib_notes = []
    for lc in lib_codes:
        ln, lnotes, _ = normalize_code(lc)
        if not ln:
            rejects.add("parser_unavailable")
            return None
        norm_libs.append(ln)
        lib_notes.extend(lnotes)
    return {
        "norm": norm,
        "norm_libs": norm_libs,
        "notes": list(notes) + list(lib_notes),
        "ast": ast,
        "nast": nast,
    }


def instrument_bundle(norm, norm_libs, lib_names, cid, key, rejects):
    prefix = cid + "__"
    inst, _ = instrument_code(norm, cid, key)
    if not inst or not inst.get("code"):
        rejects.add("instrument_fail")
        return None
    if inst.get("craft_own_key"):
        rejects.add("craft_own_key")
        return None
    inst_libs = []
    for lc in norm_libs:
        li, _ = instrument_code(lc, cid, key)
        if not li or not li.get("code"):
            rejects.add("instrument_fail")
            return None
        if li.get("craft_own_key"):
            rejects.add("craft_own_key")
            return None
        inst_libs.append(li["code"])
    renamed, _ = rename_code(inst["code"], prefix, extra_names=list(lib_names or []))
    if not renamed or not renamed.get("code"):
        rejects.add("rename_fail")
        return None
    pieces = [renamed["code"]]
    rewrites = list(inst.get("normalized") or [])
    for lc in inst_libs:
        rr, _ = rename_code(lc, prefix, extra_names=list(lib_names or []))
        if not rr or not rr.get("code"):
            rejects.add("rename_fail")
            return None
        pieces.append(rr["code"])
        rewrites.extend(rr.get("normalized") or [])
    fn = renamed.get("fn")
    if not fn:
        rejects.add("no_main_fn")
        return None
    final_code = "\n\n".join(pieces)
    return {
        "code": final_code,
        "fn": fn,
        "rewrites": [x for x in rewrites if str(x).startswith("rewrite_")],
        "normalized": list(inst.get("normalized") or []) + rewrites,
    }


def banned_names():
    return primitive_names() | obtain_internal_names() | set(V30_NAMED_ACTIONS)


def install_rewrite_cache():
    """Same AST results, skip repeat node subprocesses across snapshot copies."""
    import gov_harvest as GH

    orig = GH.node_rewrite
    cache = {}

    def cached(payload, timeout=20):
        key = sha256_text(json.dumps(payload, sort_keys=True, default=str))
        if key in cache:
            return cache[key]
        out = orig(payload, timeout=timeout)
        cache[key] = out
        return out

    GH.node_rewrite = cached
    global node_rewrite
    node_rewrite = cached
    return cache


def ingest_zeroshot(path, banned, rejects, excludes, seed_hist, raw_hits):
    if forbidden_path(path):
        excludes.add("forbidden_live_pilot")
        return
    seed, how = resolve_source_seed(path, "zeroshot")
    seed_hist[how] = seed_hist.get(how, 0) + 1
    if seed is None:
        excludes.add("unknown_seed")
        return
    seed_hist["seed:" + str(seed)] = seed_hist.get("seed:" + str(seed), 0) + 1
    if int(seed) in BLOCKED_SOURCE_SEEDS:
        excludes.add("blocked_eval_or_tournament_seed")
        return
    code_rows = load_jsonl(path)
    parent = os.path.dirname(path)
    step_rows = load_jsonl(os.path.join(parent, "zeroshot_steps.jsonl"))
    steps_by_round = {}
    for s in step_rows:
        try:
            rnd = int(s.get("round") or 0)
        except (TypeError, ValueError):
            continue
        if rnd:
            steps_by_round[rnd] = s
    result = load_json(os.path.join(parent, "zeroshot_result.json"), {}) or {}
    skill_path = os.path.abspath(os.path.join(parent, "..", "skill", "skills.json"))
    lib = load_skills_dict(skill_path) if os.path.isfile(skill_path) and not forbidden_path(skill_path) else {}
    for crow in code_rows:
        code = crow.get("program_code") or ""
        if not str(code).strip():
            rejects.add("empty_code")
            continue
        rnd = 0
        try:
            rnd = int(crow.get("round") or 0)
        except (TypeError, ValueError):
            rnd = 0
        step = steps_by_round.get(rnd) or {}
        subgoal = step.get("subgoal") or crow.get("subgoal")
        if not subgoal:
            idx = crow.get("subgoal_index")
            sgs = result.get("subgoals") or []
            if idx is not None:
                try:
                    i = int(idx)
                    if 0 <= i < len(sgs):
                        subgoal = sgs[i]
                except (TypeError, ValueError):
                    pass
        key, why, blocks, _ast = classify_key(subgoal or "", code)
        if key is None:
            rejects.add(why or "not_obtain_class")
            continue
        deps = collect_deps(code, lib, banned, rejects)
        if deps is None:
            continue
        lib_codes = []
        for name in deps:
            rec = lib.get(name) or {}
            lc = rec.get("code") if isinstance(rec, dict) else rec
            if not lc:
                rejects.add("missing_dep")
                lib_codes = None
                break
            lib_codes.append(str(lc))
        if lib_codes is None:
            continue
        bundle = gate_bundle(code, lib_codes, rejects)
        if not bundle:
            continue
        ok = zeroshot_row_success(crow, step)
        combined = "\n\n".join([bundle["norm"]] + bundle["norm_libs"])
        raw_hits.append(
            {
                "key": key,
                "hash": sha256_text(combined),
                "norm": bundle["norm"],
                "norm_libs": bundle["norm_libs"],
                "lib_names": deps,
                "notes": bundle["notes"],
                "source_path": path,
                "source_seed": int(seed),
                "source_kind": how,
                "source_name": subgoal,
                "round": rnd,
                "mined_blocks": blocks,
                "n_success_hit": 1 if ok else 0,
                "code_len": len(combined),
            }
        )


def ingest_skills(path, banned, rejects, excludes, seed_hist, raw_hits):
    if forbidden_path(path):
        excludes.add("forbidden_live_pilot")
        return
    seed, how = resolve_source_seed(path, "skills")
    seed_hist[how] = seed_hist.get(how, 0) + 1
    if seed is None:
        excludes.add("unknown_seed")
        return
    seed_hist["seed:" + str(seed)] = seed_hist.get("seed:" + str(seed), 0) + 1
    if int(seed) in BLOCKED_SOURCE_SEEDS:
        excludes.add("blocked_eval_or_tournament_seed")
        return
    lib = load_skills_dict(path)
    for name, rec in lib.items():
        code = str((rec or {}).get("code") or "")
        if not code.strip():
            rejects.add("empty_code")
            continue
        key, why, blocks, _ast = classify_key(name, code)
        if key is None:
            rejects.add(why or "not_obtain_class")
            continue
        deps = collect_deps(code, lib, banned, rejects)
        if deps is None:
            continue
        deps = [d for d in deps if d != name]
        lib_codes = []
        missing = False
        for dep in deps:
            drec = lib.get(dep) or {}
            lc = drec.get("code") if isinstance(drec, dict) else drec
            if not lc:
                rejects.add("missing_dep")
                missing = True
                break
            lib_codes.append(str(lc))
        if missing:
            continue
        bundle = gate_bundle(code, lib_codes, rejects)
        if not bundle:
            continue
        combined = "\n\n".join([bundle["norm"]] + bundle["norm_libs"])
        raw_hits.append(
            {
                "key": key,
                "hash": sha256_text(combined),
                "norm": bundle["norm"],
                "norm_libs": bundle["norm_libs"],
                "lib_names": deps,
                "notes": bundle["notes"],
                "source_path": path,
                "source_seed": int(seed),
                "source_kind": how,
                "source_name": name,
                "round": None,
                "mined_blocks": blocks,
                "n_success_hit": skill_n_success(rec),
                "code_len": len(combined),
            }
        )


def pick_candidates(raw_hits, rejects, top_n=TOP_N):
    by_hash = {}
    for hit in raw_hits:
        rec = by_hash.setdefault(
            (hit["key"], hit["hash"]),
            {"n_success": 0, "hits": [], "hit": hit},
        )
        rec["n_success"] += int(hit.get("n_success_hit") or 0)
        rec["hits"].append(hit)
        if hit["code_len"] < rec["hit"]["code_len"]:
            rec["hit"] = hit
        elif hit["code_len"] == rec["hit"]["code_len"] and hit["hash"] < rec["hit"]["hash"]:
            rec["hit"] = hit
    per_key = {}
    for (key, _h), rec in by_hash.items():
        per_key.setdefault(key, []).append(rec)
    candidates = {k: [] for k in POOL_KEYS}
    for key in POOL_KEYS:
        recs = per_key.get(key) or []
        recs.sort(key=lambda r: (-r["n_success"], r["hit"]["code_len"], r["hit"]["hash"]))
        picked = []
        for rec in recs:
            if len(picked) >= top_n:
                break
            k_idx = len(picked)
            hit = rec["hit"]
            cid = f"gov_{key}_{k_idx}"
            built = instrument_bundle(
                hit["norm"],
                hit.get("norm_libs") or [],
                hit.get("lib_names") or [],
                cid,
                key,
                rejects,
            )
            if not built:
                continue
            notes = list(hit.get("notes") or []) + list(built.get("normalized") or [])
            picked.append(
                {
                    "id": cid,
                    "fn": built["fn"],
                    "code": built["code"],
                    "code_hash": sha256_text(built["code"]),
                    "norm_hash": hit["hash"],
                    "source_path": hit["source_path"],
                    "source_seed": hit["source_seed"],
                    "source_kind": hit.get("source_kind"),
                    "source_name": hit.get("source_name"),
                    "round": hit.get("round"),
                    "n_success": rec["n_success"],
                    "deps": list(hit.get("lib_names") or []),
                    "mined_blocks": list(hit.get("mined_blocks") or []),
                    "normalized": notes,
                    "rewrites": built.get("rewrites") or [],
                    "target_n": 1,
                }
            )
        candidates[key] = picked
    return candidates, len(by_hash)


def collect(roots=None, out_path=None, top_n=TOP_N):
    out_path = out_path or OUT_JSON
    install_rewrite_cache()
    rejects = RejectCounts()
    excludes = RejectCounts()
    seed_hist = {}
    raw_hits = []
    banned = banned_names()
    n_zs = 0
    n_sk = 0
    n_files = 0
    for kind, path in iter_source_files(roots):
        n_files += 1
        if n_files % 25 == 0:
            print(
                f"[gov_pool] files={n_files} zs={n_zs} skills={n_sk} hits={len(raw_hits)} last={path}",
                flush=True,
            )
        if kind == "zeroshot":
            n_zs += 1
            ingest_zeroshot(path, banned, rejects, excludes, seed_hist, raw_hits)
        else:
            n_sk += 1
            ingest_skills(path, banned, rejects, excludes, seed_hist, raw_hits)
    candidates, n_unique = pick_candidates(raw_hits, rejects, top_n=top_n)
    payload = {
        "candidates": candidates,
        "rejects": dict(sorted(rejects.items())),
        "excludes": dict(sorted(excludes.items())),
        "source_seed_hist": dict(sorted(seed_hist.items(), key=lambda kv: kv[0])),
        "n_zeroshot_files": n_zs,
        "n_skills_files": n_sk,
        "n_raw_hits": len(raw_hits),
        "n_unique": n_unique,
        "per_key": {k: len(candidates.get(k) or []) for k in POOL_KEYS},
        "blocked_source_seeds": sorted(BLOCKED_SOURCE_SEEDS),
        "out": out_path,
    }
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
    return payload


def load_pool(path=None):
    path = path or OUT_JSON
    with open(path, encoding="utf-8") as fp:
        data = json.load(fp)
    if isinstance(data, dict) and "candidates" in data:
        return data
    if isinstance(data, dict):
        return {"candidates": data, "rejects": {}, "excludes": {}}
    return {"candidates": {}, "rejects": {}, "excludes": {}}


def main():
    payload = collect()
    print(json.dumps({k: payload[k] for k in (
        "rejects", "excludes", "source_seed_hist", "n_zeroshot_files",
        "n_skills_files", "n_raw_hits", "n_unique", "per_key", "out",
    ) if k in payload}, ensure_ascii=False, indent=2))
    for key in POOL_KEYS:
        n = payload["per_key"].get(key, 0)
        print(f"key={key} n={n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
