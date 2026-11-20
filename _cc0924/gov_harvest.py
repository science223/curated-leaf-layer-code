#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline collector: turn stopped OLD zeroshot rounds into gov leaf candidates.

Read-only on the two stopped run trees. Never opens the live pilot tree.
Does not call LLM / API.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from gov_leaf_alias import (  # noqa: E402
    EVAL_SEEDS,
    OBTAIN_CLASS_VERBS,
    OBTAIN_LEAF_ALIAS,
    SCENE_LEAF_KEYS,
    equiv_from_inv,
    leaf_alias,
)
from voyager.verify import HOLD_VERBS, parse_task  # noqa: E402
from voyager.v30_flags import V30_NAMED_ACTIONS  # noqa: E402

HARVEST_ROOTS = [
    os.path.expanduser("~/wt_v30_aa/runs_0911/zs_aa_0925"),
    os.path.expanduser("~/wt_v30_pilot0926_v1_stopped/runs_0911/zs_ruleA_pilot_0926"),
]
FORBIDDEN_READ = os.path.expanduser("~/wt_v30_pilot0926/runs_0911")
LIB_SKILLS = (
    "/mnt/e/homework/research/voyager/_audit_0911/v24_final/"
    "slac002_v26/ckpt_A_flash_off_t1_v26OLD/skill/skills.json"
)
REWRITE_JS = os.path.join(HERE, "gov_js_rewrite.js")
OUT_JSON = os.path.join(REPO, "_gov", "candidates.json")

SLASH_WORDS = (
    "/give", "/tp", "/teleport", "/setblock", "/summon", "/fill", "/gamemode",
    "/locate", "/loot", "/item", "/effect", "/time", "/weather",
)
GIVE_CHAT_RE = re.compile(
    r"/give\s+@s\s+(?:minecraft:)?([a-z0-9_]+)\s+(\d+)",
    re.I,
)
GIVEN_RE = re.compile(
    r"\bGiven\s+\[(?:minecraft:)?([a-z0-9_]+)\]",
    re.I,
)
GAVE_RE = re.compile(
    r"\bGave\s+(\d+)\s+\[(?:minecraft:)?([^\]]+)\]",
    re.I,
)

ABORT_SNIPPET = 'if (globalThis.__govActive !== "'


def _forbidden_path(path):
    path = os.path.abspath(path)
    forbid = os.path.abspath(FORBIDDEN_READ)
    return path == forbid or path.startswith(forbid + os.sep)


def primitive_names():
    root = os.path.join(REPO, "voyager", "control_primitives")
    return {f[:-3] for f in os.listdir(root) if f.endswith(".js")}


def obtain_internal_names():
    text = open(
        os.path.join(REPO, "voyager", "control_primitives_extra", "obtain.js"),
        encoding="utf-8",
    ).read()
    names = set(re.findall(r"(?:async\s+)?function\s+(_obtain\w+)", text))
    names.update(re.findall(r"const\s+(_obtain\w+)\s*=", text))
    return names


def load_library():
    if not os.path.isfile(LIB_SKILLS):
        return {}
    with open(LIB_SKILLS, encoding="utf-8") as fp:
        data = json.load(fp)
    out = {}
    if isinstance(data, dict):
        for name, rec in data.items():
            if isinstance(rec, dict) and rec.get("code"):
                out[str(name)] = str(rec["code"])
    return out


def node_rewrite(payload, timeout=20):
    env = dict(os.environ)
    env["NODE_PATH"] = os.pathsep.join(
        [
            os.path.join(REPO, "voyager", "utils", "node_modules"),
            os.path.join(REPO, "voyager", "env", "mineflayer", "node_modules"),
            env.get("NODE_PATH", ""),
        ]
    )
    proc = subprocess.run(
        ["node", REWRITE_JS],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=REPO,
        env=env,
        timeout=timeout,
    )
    if proc.returncode != 0:
        return {"error": (proc.stderr or proc.stdout or "node_fail")[:400]}
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return {"error": "bad_json:" + (proc.stdout or "")[:200]}


def regex_gates(code):
    """Conservative regex gates (also used when AST is unavailable)."""
    reasons = []
    for w in SLASH_WORDS:
        if w in code:
            reasons.append("slash_cmd:" + w)
    if re.search(r"(^|[^\.\w])eval\s*\(", code):
        reasons.append("eval")
    if re.search(r"(^|[^\.\w])Function\s*\(", code):
        reasons.append("Function(")
    if re.search(r"\bfromCharCode\b", code):
        reasons.append("fromCharCode")
    if re.search(r"\bbot\.creative\b", code):
        reasons.append("bot.creative")
    if re.search(r"\bwhile\s*\(\s*true\s*\)", code):
        reasons.append("while(true)")
    if re.search(r"\bfor\s*\(\s*;\s*;\s*\)", code):
        reasons.append("for(;;)")
    for name in ("obtain", "gather", "mineOre", "craft", "smelt", "collectFluid"):
        if re.search(r"(^|[^\.\w])" + name + r"\s*\(", code):
            reasons.append("v30:" + name)
    if re.search(r"""bot\.chat\s*\(\s*(?:[^"'`]|/)|bot\.chat\s*\(\s*(['"`])[^'"`]*\/""", code):
        # non-literal or slash in literal; the second alt catches "/..."
        pass
    for m in re.finditer(r"bot\.chat\s*\(", code):
        rest = code[m.end():]
        if not rest.lstrip().startswith(("'", '"', "`")):
            reasons.append("bot.chat_non_literal")
            break
        q = rest.lstrip()[0]
        end = rest.lstrip().find(q, 1)
        if end < 0:
            reasons.append("bot.chat_non_literal")
            break
        lit = rest.lstrip()[1:end]
        if q == "`" and lit.startswith("${"):
            reasons.append("bot.chat_non_literal")
            break
        if lit.lstrip().startswith("/"):
            reasons.append("bot.chat_slash_literal")
            break
    if re.search(r"""["']\s*\+\s*["']/|["']/\s*\+\s*["']""", code) or re.search(
        r"""["']/["']\s*\+\s*["']give""", code
    ):
        reasons.append("slash_concat")
    for m in re.finditer(r"\bfindBlocks?\s*\(", code):
        snippet = code[m.end():m.end() + 240]
        md = re.search(r"maxDistance\s*:\s*(\d+)", snippet)
        if not md:
            if "maxDistance" in snippet[: snippet.find("}") + 1] if "}" in snippet else snippet:
                reasons.append("findBlock_maxDistance_missing")
            else:
                reasons.append("findBlock_maxDistance_missing")
        elif int(md.group(1)) > 32:
            reasons.append("findBlock_maxDistance_gt32")
    for m in re.finditer(r"maxDistance\s*:\s*(\d+)", code):
        if int(m.group(1)) > 32:
            reasons.append("maxDistance_gt32")
    for ctor in ("Vec3", "GoalBlock", "GoalNear", "GoalXZ"):
        for m in re.finditer(r"\bnew\s+" + ctor + r"\s*\(([^)]*)\)", code):
            args = m.group(1)
            nums = [int(x) for x in re.findall(r"-?\d+", args)]
            if any(abs(n) > 1 for n in nums):
                reasons.append("abs_coord:" + ctor)
    return sorted(set(reasons))


def abort_snippet(cand_id):
    return 'if (globalThis.__govActive !== "' + str(cand_id) + '") return;'


def static_gates(code):
    ast = node_rewrite({"op": "gates", "code": code})
    if ast.get("error") or ast.get("parser") is None:
        return ["parser_unavailable"], ast
    reasons = list(ast.get("reasons") or [])
    return sorted(set(reasons)), ast


def instrument_code(code, cand_id, item_key):
    ast = node_rewrite({
        "op": "instrument",
        "code": code,
        "cand_id": cand_id,
        "item_key": item_key,
    })
    if ast.get("error") or not ast.get("code"):
        return None, ast
    return ast, ast


def normalize_code(code):
    ast = node_rewrite({"op": "normalize", "code": code})
    if ast.get("error") or ast.get("parser") is None or not ast.get("code"):
        return None, ["parser_unavailable"], ast
    return ast["code"], list(ast.get("normalized") or []), ast


def rename_code(code, prefix, extra_names=None):
    ast = node_rewrite({"op": "rename", "code": code, "prefix": prefix, "names": extra_names or []})
    if ast.get("error") or not ast.get("code"):
        return None, ast
    return ast, ast


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def iter_attempts(root):
    work = os.path.join(root, "work")
    if not os.path.isdir(work):
        return
    for job in sorted(os.listdir(work)):
        job_dir = os.path.join(work, job)
        if not os.path.isdir(job_dir):
            continue
        for att in sorted(os.listdir(job_dir)):
            att_dir = os.path.join(job_dir, att)
            result = os.path.join(att_dir, "ckpt", "metrics", "zeroshot_result.json")
            if os.path.isfile(result):
                yield att_dir, result


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fp:
            return json.load(fp)
    except Exception:
        return default


def load_jsonl(path):
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def load_events_file(path):
    try:
        with open(path, encoding="utf-8") as fp:
            return json.load(fp)
    except Exception:
        return []


def event_inventories(events):
    invs = []
    for item in events or []:
        if not (isinstance(item, (list, tuple)) and len(item) >= 2):
            continue
        et, ev = item[0], item[1]
        if isinstance(ev, dict) and "inventory" in ev:
            invs.append((et, dict(ev.get("inventory") or {})))
    return invs


def event_text(ev):
    if not isinstance(ev, dict):
        return ""
    parts = [str(ev.get(k) or "") for k in ("onChat", "onSave", "chat", "onError")]
    return " ".join(parts)


def parse_gives_from_events(events):
    found = []
    located = False
    for item in events or []:
        if not (isinstance(item, (list, tuple)) and len(item) >= 2):
            continue
        blob = event_text(item[1])
        if "/give" in blob or "Given [" in blob or re.search(r"\bGave\s+\d+", blob):
            located = True
        for m in GIVE_CHAT_RE.finditer(blob):
            found.append((leaf_alias(m.group(1)), int(m.group(2))))
        for m in GIVEN_RE.finditer(blob):
            found.append((leaf_alias(m.group(1)), 1))
        for m in GAVE_RE.finditer(blob):
            raw = re.sub(r"[^a-z0-9_]+", "_", m.group(2).lower()).strip("_")
            found.append((leaf_alias(raw), int(m.group(1))))
    return found, located


def collect_attempt_events(att_dir):
    ev_dir = os.path.join(att_dir, "ckpt", "events")
    all_ev = []
    per_file = {}
    if not os.path.isdir(ev_dir):
        return all_ev, per_file
    for name in sorted(os.listdir(ev_dir)):
        path = os.path.join(ev_dir, name)
        if not os.path.isfile(path):
            continue
        ev = load_events_file(path)
        per_file[name] = ev
        all_ev.extend(ev)
    return all_ev, per_file


def events_files_by_round(per_file):
    """DEPRECATED: timestamp-order remapping. Use align_events_to_steps."""
    rows = []
    for name, ev in (per_file or {}).items():
        m = re.search(r"_(\d{8})_(\d{6})$", name)
        if not m:
            continue
        rows.append((m.group(1) + m.group(2), name, ev))
    rows.sort()
    return {i + 1: (name, ev) for i, (_, name, ev) in enumerate(rows)}


_EVENT_FILENAME_SANITIZE = re.compile(r'[\\/:"*?<>| ]')


def task_key_from_text(task):
    """Same sanitizing EventRecorder uses for the event filename stem."""
    return _EVENT_FILENAME_SANITIZE.sub("_", task or "")


def event_file_task_key(name):
    base = os.path.basename(name)
    if base.endswith(".json"):
        base = base[:-5]
    m = re.search(r"_(\d{8})_(\d{6})$", base)
    if not m:
        return None
    return base[: m.start()]


def align_events_to_steps(per_file, step_rows):
    """Pair event files with zeroshot_steps.jsonl by recorder order + subgoal.

    Files are ordered by the timestamp in the filename (recorder dump order).
    That sequence is paired with step_rows ordered by their existing ``round``
    field — we do not invent new round numbers. Count mismatch, round gaps,
    duplicate rounds, or filename-task ≠ sanitized subgoal → unverifiable.
    Returns (by_round, None) or (None, reason).
    """
    files = []
    for name, ev in (per_file or {}).items():
        m = re.search(r"_(\d{8})_(\d{6})$", name)
        if not m:
            return None, "bad_event_name"
        files.append((m.group(1) + m.group(2), name, ev))
    files.sort()
    steps = []
    seen_rounds = set()
    for s in step_rows or []:
        try:
            rnd = int(s.get("round") or 0)
        except (TypeError, ValueError):
            continue
        if rnd <= 0:
            continue
        if rnd in seen_rounds:
            return None, "duplicate_round"
        seen_rounds.add(rnd)
        steps.append((rnd, s))
    steps.sort(key=lambda x: x[0])
    if len(files) != len(steps):
        return None, "count_mismatch"
    if not steps:
        return None, "no_steps"
    rounds = [r for r, _ in steps]
    expected = list(range(rounds[0], rounds[0] + len(rounds)))
    if rounds != expected:
        return None, "round_gap"
    by_round = {}
    for (_ts, name, ev), (rnd, step) in zip(files, steps):
        sub = step.get("subgoal") or ""
        file_key = event_file_task_key(name)
        want_key = task_key_from_text(sub) if sub else None
        if want_key and file_key and want_key != file_key:
            return None, "task_mismatch"
        if not file_key:
            return None, "bad_event_name"
        by_round[rnd] = (name, ev, step)
    return by_round, None


def round_inventory_pair(ev):
    invs = event_inventories(ev)
    if len(invs) < 2:
        return None, None
    return invs[0][1], invs[-1][1]


def source_seed_of(result, meta):
    for src in (result, (meta or {}).get("zeroshot") or {}, meta or {}):
        if not isinstance(src, dict):
            continue
        for k in ("world_seed", "seed"):
            if src.get(k) is not None:
                try:
                    return int(src[k])
                except (TypeError, ValueError):
                    pass
    return None


def obtain_class_key(subgoal):
    parsed = parse_task(subgoal)
    if parsed is None:
        return None
    verb = str(parsed.verb or "").lower()
    if verb not in OBTAIN_CLASS_VERBS:
        return None
    raw = str(parsed.raw_item or "")
    targets = list(parsed.targets or [])
    cand = raw or (targets[0] if targets else "")
    if not cand:
        return None
    key = leaf_alias(cand)
    if key not in OBTAIN_LEAF_ALIAS.values() and key not in SCENE_LEAF_KEYS:
        # still allow if it aliases to a known canonical
        if key not in set(OBTAIN_LEAF_ALIAS.values()):
            return None
    return key


class RejectCounts(dict):
    def add(self, reason, n=1):
        self[reason] = self.get(reason, 0) + n


def collect(out_path=None):
    out_path = out_path or OUT_JSON
    rejects = RejectCounts()
    lib = load_library()
    prim = primitive_names()
    obtain_fn = obtain_internal_names()
    v30 = set(V30_NAMED_ACTIONS)
    banned_lib = prim | obtain_fn | v30

    raw_hits = []  # before per-key top5
    seen_attempts = 0
    discarded_attempts = 0

    for root in HARVEST_ROOTS:
        if _forbidden_path(root):
            raise SystemExit("refusing live pilot tree: " + root)
        if not os.path.isdir(root):
            rejects.add("missing_root:" + root)
            continue
        for att_dir, result_path in iter_attempts(root):
            if _forbidden_path(att_dir):
                continue
            result = load_json(result_path, {})
            lib_id = str(result.get("library_id") or "")
            if not lib_id.startswith("OLD"):
                rejects.add("skip_not_OLD")
                continue
            seen_attempts += 1
            meta = load_json(os.path.join(att_dir, "ckpt", "metrics", "run_meta.json"), {})
            seed = source_seed_of(result, meta)
            code_rows = load_jsonl(os.path.join(att_dir, "ckpt", "metrics", "zeroshot_code.jsonl"))
            step_rows = load_jsonl(os.path.join(att_dir, "ckpt", "metrics", "zeroshot_steps.jsonl"))
            all_ev, per_file = collect_attempt_events(att_dir)
            aligned, why = align_events_to_steps(per_file, step_rows)
            if aligned is None:
                rejects.add("attempt_unaligned")
                rejects.add("attempt_unaligned:" + (why or "unknown"))
                discarded_attempts += 1
                continue
            steps_by_round = {int(s.get("round") or 0): s for s in step_rows if s.get("round")}
            for crow in code_rows:
                if not crow.get("executed"):
                    continue
                code = crow.get("program_code") or ""
                if not code.strip():
                    rejects.add("empty_code")
                    continue
                subgoal = None
                rnd = int(crow.get("round") or 0)
                step = steps_by_round.get(rnd) or {}
                subgoal = step.get("subgoal") or crow.get("subgoal")
                if not subgoal:
                    idx = crow.get("subgoal_index")
                    sgs = result.get("subgoals") or []
                    if idx is not None and 0 <= int(idx) < len(sgs):
                        subgoal = sgs[int(idx)]
                key = obtain_class_key(subgoal or "")
                if not key:
                    rejects.add("not_obtain_class")
                    continue
                if rnd not in aligned:
                    rejects.add("no_round_events")
                    continue
                ev_name, ev, aligned_step = aligned[rnd]
                before, after = round_inventory_pair(ev)
                if before is None or after is None:
                    rejects.add("no_round_inv")
                    continue
                _gives, give_here = parse_gives_from_events(ev)
                if give_here or _gives:
                    rejects.add("round_give")
                    continue
                parsed = parse_task(subgoal or "")
                target_n = int(parsed.n) if parsed and parsed.n else 1
                delta = equiv_from_inv(after, key) - equiv_from_inv(before, key)
                if delta < 1:
                    rejects.add("delta_lt1")
                    continue
                reasons, ast = static_gates(code)
                if reasons:
                    for r in reasons:
                        rejects.add("gate:" + r.split(":")[0])
                    continue
                norm, notes, nast = normalize_code(code)
                if not norm:
                    rejects.add("parser_unavailable")
                    continue
                calls = list(nast.get("calls") or ast.get("calls") or [])
                needed = []
                seen = set()
                stack = [c for c in calls if c in lib]
                while stack:
                    name = stack.pop()
                    if name in seen:
                        continue
                    seen.add(name)
                    if name in banned_lib:
                        continue
                    needed.append(name)
                    lib_code = lib.get(name) or ""
                    lib_ast = node_rewrite({"op": "calls", "code": lib_code})
                    for c2 in lib_ast.get("calls") or []:
                        if c2 in lib and c2 not in seen:
                            stack.append(c2)
                lib_reject = False
                lib_codes = []
                for name in needed:
                    lc = lib.get(name) or ""
                    lr, _ = static_gates(lc)
                    if lr:
                        lib_reject = True
                        for r in lr:
                            rejects.add("lib_gate:" + r.split(":")[0])
                        break
                    lib_codes.append(lc)
                if lib_reject:
                    rejects.add("lib_static_fail")
                    continue
                combined = "\n\n".join([norm] + lib_codes)
                h = sha256_text(combined)
                raw_hits.append(
                    {
                        "key": key,
                        "hash": h,
                        "norm": norm,
                        "lib_codes": lib_codes,
                        "lib_names": needed,
                        "notes": notes,
                        "source_attempt": result.get("attempt_id") or os.path.basename(att_dir),
                        "source_seed": seed,
                        "round": rnd,
                        "subgoal": subgoal,
                        "target_n": target_n,
                        "delta": delta,
                        "code_len": len(combined),
                        "events_file": ev_name,
                    }
                )

    # group by hash, then rename
    by_hash = {}
    for hit in raw_hits:
        rec = by_hash.setdefault(
            (hit["key"], hit["hash"]),
            {"n_success": 0, "hits": [], "hit": hit},
        )
        rec["n_success"] += 1
        rec["hits"].append(hit)
        if hit["code_len"] < rec["hit"]["code_len"]:
            rec["hit"] = hit

    per_key = {}
    for (key, h), rec in by_hash.items():
        per_key.setdefault(key, []).append(rec)

    candidates = {}
    for key, recs in per_key.items():
        recs.sort(
            key=lambda r: (-r["n_success"], r["hit"]["code_len"], r["hit"]["hash"])
        )
        picked = []
        for rec in recs:
            if len(picked) >= 5:
                break
            k_idx = len(picked)
            hit = rec["hit"]
            cid = f"gov_{key}_{k_idx}"
            prefix = f"{cid}__"
            extra = list(hit.get("lib_names") or [])
            inst, _ = instrument_code(hit["norm"], cid, key)
            if not inst or not inst.get("code"):
                rejects.add("instrument_fail")
                continue
            if inst.get("craft_own_key"):
                rejects.add("craft_own_key")
                continue
            inst_libs = []
            lib_fail = False
            for lc in hit.get("lib_codes") or []:
                li, _ = instrument_code(lc, cid, key)
                if not li or not li.get("code"):
                    rejects.add("instrument_fail")
                    lib_fail = True
                    break
                if li.get("craft_own_key"):
                    rejects.add("craft_own_key")
                    lib_fail = True
                    break
                inst_libs.append(li["code"])
            if lib_fail:
                continue
            renamed, _ = rename_code(inst["code"], prefix, extra_names=extra)
            if not renamed or not renamed.get("code"):
                rejects.add("rename_fail")
                continue
            pieces = [renamed["code"]]
            rewrites = list(inst.get("normalized") or [])
            for lc in inst_libs:
                rr, _ = rename_code(lc, prefix, extra_names=extra)
                if rr and rr.get("code"):
                    pieces.append(rr["code"])
            final_code = "\n\n".join(pieces)
            fn = renamed.get("fn")
            if not fn:
                rejects.add("no_main_fn")
                continue
            notes = list(hit.get("notes") or []) + rewrites
            picked.append(
                {
                    "id": cid,
                    "fn": fn,
                    "code": final_code,
                    "code_hash": sha256_text(final_code),
                    "source_attempt": hit["source_attempt"],
                    "source_seed": hit["source_seed"],
                    "round": hit["round"],
                    "subgoal": hit["subgoal"],
                    "target_n": int(hit.get("target_n") or 1),
                    "delta": hit["delta"],
                    "n_success": rec["n_success"],
                    "normalized": notes,
                    "rewrites": [x for x in rewrites if str(x).startswith("rewrite_")],
                    "events_file": hit.get("events_file"),
                    "inv_source": (
                        "zeroshot_steps.jsonl.round <-> ckpt/events/<sanitized_subgoal>_<YYYYMMDD_HHMMSS> "
                        "(filename task key == sanitize(subgoal); file count == step count; "
                        "first/last inventory of that file)"
                    ),
                }
            )
        candidates[key] = picked

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(candidates, fp, ensure_ascii=False, indent=2)

    summary = {
        "attempts_OLD": seen_attempts,
        "attempts_unaligned": discarded_attempts,
        "n_raw_hits": len(raw_hits),
        "n_unique": len(by_hash),
        "per_key": {k: len(v) for k, v in candidates.items()},
        "empty_pool": {k: (len(v) == 0) for k, v in candidates.items()},
        "rejects": dict(sorted(rejects.items())),
        "eval_seed_overlap": sorted(
            {
                int(c["source_seed"])
                for recs in candidates.values()
                for c in recs
                if c.get("source_seed") in EVAL_SEEDS
            }
        ),
        "out": out_path,
    }
    return candidates, summary


def main():
    cands, summary = collect()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for key in sorted(set(list(SCENE_LEAF_KEYS) + list(cands.keys()))):
        n = len(cands.get(key) or [])
        print(f"key={key} n={n} empty={n == 0}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
