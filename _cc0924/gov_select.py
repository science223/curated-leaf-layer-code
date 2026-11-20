#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Select screened (tournament rank) and random pools from pool_full.

Format matches v4 _gov/candidates.json so GOV_CANDIDATES can point at either file.
"""
from __future__ import annotations

import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from gov_pool import POOL_KEYS, load_pool  # noqa: E402

RANDOM_SEED = 20260926
PER_KEY = 3
POOL_FULL = os.path.join(REPO, "_gov", "pool_full.json")
OUT_SCREENED = os.path.join(REPO, "_gov", "pool_screened.json")
OUT_RANDOM = os.path.join(REPO, "_gov", "pool_random.json")
TOURNAMENT_JSONL = os.path.join(REPO, "_gov", "tournament_results.jsonl")

V4_FIELDS = (
    "id",
    "fn",
    "code",
    "code_hash",
    "source_attempt",
    "source_seed",
    "round",
    "subgoal",
    "target_n",
    "delta",
    "n_success",
    "normalized",
    "rewrites",
    "events_file",
    "inv_source",
    "source_path",
    "source_kind",
    "source_name",
    "deps",
    "norm_hash",
    "mined_blocks",
)


def load_jsonl(path):
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def as_v4_record(rec, extra=None):
    out = {}
    for k in V4_FIELDS:
        if k in rec:
            out[k] = rec[k]
    if extra:
        out.update(extra)
    if "source_attempt" not in out:
        out["source_attempt"] = rec.get("source_path") or rec.get("source_name")
    if "subgoal" not in out:
        out["subgoal"] = rec.get("source_name")
    if "target_n" not in out:
        out["target_n"] = 1
    if "inv_source" not in out:
        out["inv_source"] = "pool_full"
    return out


def score_rows(rows):
    """Group tournament rows by (key, code_hash)."""
    by = {}
    for r in rows:
        if r.get("event") and r.get("event") != "result":
            continue
        key = r.get("key")
        h = r.get("code_hash")
        if not key or not h:
            continue
        rec = by.setdefault((key, h), {"wins": 0, "times": [], "rows": []})
        rec["rows"].append(r)
        if r.get("success"):
            rec["wins"] += 1
            try:
                rec["times"].append(float(r.get("ms") or 0))
            except (TypeError, ValueError):
                rec["times"].append(0.0)
    return by


def mean_success_ms(rec):
    times = rec.get("times") or []
    if not times:
        return float("inf")
    return sum(times) / len(times)


def rank_key(cands, scores):
    """Return candidates sorted by wins desc, mean success ms asc, hash."""
    keyed = []
    for c in cands or []:
        h = c.get("code_hash")
        sc = scores.get((c.get("key") or "", h)) or scores.get((None, h))
        # scores keyed by (key, hash); fill key from candidate
        if sc is None:
            sc = {"wins": 0, "times": []}
        keyed.append((c, sc))
    keyed.sort(
        key=lambda pair: (
            -int(pair[1].get("wins") or 0),
            mean_success_ms(pair[1]),
            str(pair[0].get("code_hash") or ""),
        )
    )
    return keyed


def select_screened(pool, tournament_rows, per_key=PER_KEY):
    scores = score_rows(tournament_rows)
    # reindex with actual key
    by_kh = {}
    for (key, h), rec in scores.items():
        by_kh[(key, h)] = rec
    out = {}
    ranking = {}
    cands_by_key = pool.get("candidates") or pool
    for key in POOL_KEYS:
        recs = list(cands_by_key.get(key) or [])
        for c in recs:
            c = dict(c)
            c["key"] = key
        ranked = []
        for c in recs:
            sc = by_kh.get((key, c.get("code_hash"))) or {"wins": 0, "times": []}
            ranked.append((c, sc))
        ranked.sort(
            key=lambda pair: (
                -int(pair[1].get("wins") or 0),
                mean_success_ms(pair[1]),
                str(pair[0].get("code_hash") or ""),
            )
        )
        ranking[key] = [
            {
                "id": c.get("id"),
                "code_hash": c.get("code_hash"),
                "wins": int(sc.get("wins") or 0),
                "mean_success_ms": None if not sc.get("times") else round(mean_success_ms(sc), 1),
                "n_runs": len(sc.get("rows") or []),
            }
            for c, sc in ranked
        ]
        picked = []
        for i, (c, sc) in enumerate(ranked[:per_key]):
            rec = as_v4_record(
                c,
                extra={
                    "id": f"gov_{key}_{i}",
                    "tournament_wins": int(sc.get("wins") or 0),
                    "tournament_mean_success_ms": (
                        None if not sc.get("times") else round(mean_success_ms(sc), 1)
                    ),
                },
            )
            # keep original fn/code (already renamed to original id); rewrite id only for display?
            # Task: format matches v4 candidates.json. Re-index ids 0..2 like v4.
            # Changing id without renaming __govActive strings would break abort probes.
            # Keep original id/fn/code; do not re-number.
            rec["id"] = c.get("id")
            rec["fn"] = c.get("fn")
            rec["code"] = c.get("code")
            picked.append(rec)
        out[key] = picked
    return out, ranking


def select_random(pool, rng_seed=RANDOM_SEED, per_key=PER_KEY):
    rng = random.Random(rng_seed)
    cands_by_key = pool.get("candidates") or pool
    out = {}
    for key in POOL_KEYS:
        recs = list(cands_by_key.get(key) or [])
        recs.sort(key=lambda c: str(c.get("code_hash") or ""))
        n = min(per_key, len(recs))
        picked = rng.sample(recs, n) if n else []
        out[key] = [as_v4_record(c) for c in picked]
    return out


def dump_pool(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)


def print_ranking(ranking):
    for key in POOL_KEYS:
        rows = ranking.get(key) or []
        print(f"=== {key} ({len(rows)} ranked) ===")
        for i, r in enumerate(rows, 1):
            print(
                f"  {i:2d}. {r.get('id')} wins={r.get('wins')} "
                f"mean_ms={r.get('mean_success_ms')} hash={(r.get('code_hash') or '')[:12]}"
            )


def main(argv=None):
    argv = list(argv or sys.argv[1:])
    pool_path = POOL_FULL
    jsonl_path = TOURNAMENT_JSONL
    i = 0
    while i < len(argv):
        if argv[i] == "--pool" and i + 1 < len(argv):
            pool_path = argv[i + 1]
            i += 2
            continue
        if argv[i] == "--results" and i + 1 < len(argv):
            jsonl_path = argv[i + 1]
            i += 2
            continue
        i += 1
    pool = load_pool(pool_path) if os.path.isfile(pool_path) else {"candidates": {k: [] for k in POOL_KEYS}}
    rows = load_jsonl(jsonl_path)
    screened, ranking = select_screened(pool, rows)
    random_pool = select_random(pool)
    dump_pool(OUT_SCREENED, screened)
    dump_pool(OUT_RANDOM, random_pool)
    print_ranking(ranking)
    print("wrote", OUT_SCREENED, {k: len(screened.get(k) or []) for k in POOL_KEYS})
    print("wrote", OUT_RANDOM, {k: len(random_pool.get(k) or []) for k in POOL_KEYS})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
