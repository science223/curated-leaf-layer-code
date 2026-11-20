#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""obtain(bot, item, 1) real-MC unit tests. No LLM.

Ports: worker0 25590/3010, worker1 25591/3011. -Xmx1G.
Do not touch A/A 25610–25612 / 3110–3112. Kill only our PIDs.

Usage:
  python _cc0924/test_obtain_mc.py
  python _cc0924/test_obtain_mc.py --worker 0
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import signal
import socket
import statistics
import subprocess
import sys
import time
import traceback
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, REPO)

import test_skills_mc as SM  # noqa: E402
from voyager.zs_noise import ZS_SPAWN  # noqa: E402

JAVA = SM.JAVA
AA_MC = SM.AA_MC
AA_BR = SM.AA_BR
SEEDS = [20260902099, 20260902001, 20260902002]
# 09-26: OBTAIN_SEEDS="a,b,c" overrides (slac002 extra seeds); default unchanged.
if os.environ.get("OBTAIN_SEEDS"):
    SEEDS = [int(x) for x in os.environ["OBTAIN_SEEDS"].split(",") if x.strip()]
ITEMS = [
    dict(id="diamond_pickaxe", item="diamond_pickaxe", call='await obtain(bot, "diamond_pickaxe", 1);'),
    dict(id="golden_sword", item="golden_sword", call='await obtain(bot, "golden_sword", 1);'),
    dict(id="compass", item="compass", call='await obtain(bot, "compass", 1);'),
]
# 09-26 r3fix_4: OBTAIN_SCENES="diamond_pickaxe,compass" overrides; default all three.
if os.environ.get("OBTAIN_SCENES"):
    _wanted = [x.strip() for x in os.environ["OBTAIN_SCENES"].split(",") if x.strip()]
    _by_id = {s["id"]: s for s in ITEMS}
    _missing = [x for x in _wanted if x not in _by_id]
    if _missing:
        raise SystemExit(f"OBTAIN_SCENES unknown: {_missing}; known={[s['id'] for s in ITEMS]}")
    ITEMS = [_by_id[x] for x in _wanted]
HARNESS_CHECK_SEEDS = [20260902099, 20260902010]
HARNESS_CHECK_SCENES = [
    dict(id="harness_a", item="_none", call="await bot.waitForTicks(1);"),
    dict(id="harness_b", item="_none", call="await bot.waitForTicks(1);"),
]
SHORT_ITEMS = [
    dict(id="crafting_table", item="crafting_table", call='await obtain(bot, "crafting_table", 1);'),
    dict(id="wooden_pickaxe", item="wooden_pickaxe", call='await obtain(bot, "wooden_pickaxe", 1);'),
    dict(id="stone_pickaxe", item="stone_pickaxe", call='await obtain(bot, "stone_pickaxe", 1);'),
]
DEBUG_ITEMS = [
    dict(id="diamond_pickaxe", item="diamond_pickaxe", call='await obtain(bot, "diamond_pickaxe", 1);'),
]
VANILLA_MEDIAN = {
    "diamond_pickaxe": 17.0,
    "golden_sword": 19.5,
    "compass": 19.9,
}
CRITERION_LOCKED = {
    "written_at": "2026-09-25T before collecting this round's obtain MC results",
    "success_rate_min": 0.90,
    "limits": {k: v / 2.0 for k, v in VANILLA_MEDIAN.items()},
    "vanilla_median": dict(VANILLA_MEDIAN),
    "note": "median calls_needed <= A/A median / 2; success >= 90%",
}
MAX_CALLS = int(os.environ.get("OBTAIN_MAX_CALLS", "12"))
REQUEST_TIMEOUT = 600  # 09-25 upstream Voyager default 600 s; obtain budget 540 s
RESULT_JSONL = os.environ.get("OBTAIN_RESULT_JSONL") or os.path.join(HERE, "obtain_mc_runs_r3fix.jsonl")
RESULT_PATH = os.environ.get("OBTAIN_RESULT_PATH") or os.path.join(HERE, "obtain_mc_result_r3.json")
LOG_PATH = os.environ.get("OBTAIN_LOG_PATH") or os.path.join(HERE, "obtain_mc_r3fix.log")
WORK_ROOT = os.path.join(HERE, "obtain_mc_work")
KEEP_ROOT = os.path.join(HERE, "obtain_mc_keep")
ZS_SEED = 20260902099
FORBIDDEN_MC = {25610, 25611, 25612, 25593, 25594, 25595}
FORBIDDEN_BR = {3110, 3111, 3112, 3013, 3014, 3015}

LEAF_SOURCE = (os.environ.get("OBTAIN_LEAF_SOURCE") or "hand").strip().lower()
if LEAF_SOURCE not in ("hand", "prim", "gov"):
    raise SystemExit(f"OBTAIN_LEAF_SOURCE must be hand|prim|gov, got {LEAF_SOURCE!r}")
GOV_ONLINE = os.environ.get("GOV_ONLINE", "0").strip() in ("1", "true", "True", "yes")
GOV_RETIRE_K = int(os.environ.get("GOV_RETIRE_K", "2"))
GOV_CANDIDATES = os.environ.get("GOV_CANDIDATES") or os.path.join(REPO, "_gov", "candidates.json")
GOV_LEDGER_DIR = os.environ.get("GOV_LEDGER_DIR") or os.path.join(REPO, "_gov", "ledger")
# E7 (预注册_账本消融_2026-09-29): measurement fences and ledger conditions. With every switch at its default the harness
# injects the released globals and writes the released identity fields (E7_ACTIVE is False).
GOV_QUIESCE_MS = int(os.environ.get("GOV_QUIESCE_MS", "0") or 0)
GOV_QUIESCE_CAP_MS = int(os.environ.get("GOV_QUIESCE_CAP_MS", "4000") or 4000)
GOV_LATE_FENCE = (os.environ.get("GOV_LATE_FENCE") or "0").strip() in ("1", "true", "True", "yes")
GOV_LEDGER_MODE = (os.environ.get("GOV_LEDGER_MODE") or "full").strip()
GOV_RETIRE_MODE = (os.environ.get("GOV_RETIRE_MODE") or "permanent").strip()
GOV_FALLBACK = (os.environ.get("GOV_FALLBACK") or "on").strip()
assert GOV_LEDGER_MODE in ("full", "off", "retire_only", "reorder_only"), GOV_LEDGER_MODE
assert GOV_RETIRE_MODE in ("permanent", "quarantine"), GOV_RETIRE_MODE
assert GOV_FALLBACK in ("on", "off"), GOV_FALLBACK
E7_ACTIVE = bool(GOV_QUIESCE_MS or GOV_LATE_FENCE or GOV_LEDGER_MODE != "full" or GOV_RETIRE_MODE != "permanent"
                 or GOV_FALLBACK != "on")
EXPERIMENT_ID = (os.environ.get("OBTAIN_EXPERIMENT_ID") or "default").strip() or "default"
ALLOW_SEED_OVERLAP = os.environ.get("OBTAIN_ALLOW_SEED_OVERLAP", "").strip() == "1"
SEED_OVERLAP = False


# This continuation: only 1 test server (25590/3010). Do not open 25591.
def _seed_at(i):
    return [SEEDS[i]] if i < len(SEEDS) else []


def _worker_ports(wid):
    env_mc = os.environ.get("OBTAIN_MC_PORT")
    env_br = os.environ.get("OBTAIN_BR_PORT")
    env_wid = os.environ.get("OBTAIN_WORKER_ID")
    present = [env_mc is not None, env_br is not None, env_wid is not None]
    if any(present) and not all(present):
        raise SystemExit(
            f"port env incomplete: OBTAIN_MC_PORT={env_mc!r} OBTAIN_BR_PORT={env_br!r} "
            f"OBTAIN_WORKER_ID={env_wid!r} (set all three or none)"
        )
    if all(present) and str(int(wid)) == str(int(env_wid)):
        return int(env_mc), int(env_br)
    return 25620 + int(wid), 3120 + int(wid)


def _n_workers():
    n = int(os.environ.get("OBTAIN_WORKERS", "3"))
    return max(1, min(8, n))


WORKERS = tuple(
    {"id": i, "mc": _worker_ports(i)[0], "br": _worker_ports(i)[1], "seeds": _seed_at(i)}
    for i in range(_n_workers())
)


def assert_ports_unique_paired():
    mcs, brs = [], []
    for w in WORKERS:
        mc, br = int(w["mc"]), int(w["br"])
        if mc in FORBIDDEN_MC or br in FORBIDDEN_BR or mc in AA_MC or br in AA_BR:
            raise SystemExit(f"refusing forbidden ports mc={mc} br={br}")
        mcs.append(mc)
        brs.append(br)
    if len(set(mcs)) != len(mcs):
        raise SystemExit(f"duplicate MC ports {mcs}")
    if len(set(brs)) != len(brs):
        raise SystemExit(f"duplicate bridge ports {brs}")
    if len(WORKERS) != len(set(zip(mcs, brs))):
        raise SystemExit(f"MC/bridge port pairs not unique {list(zip(mcs, brs))}")
TECH = (
    "oak_log", "spruce_log", "birch_log", "jungle_log", "acacia_log", "dark_oak_log", "mangrove_log",
    "oak_planks", "spruce_planks", "birch_planks", "stick", "crafting_table",
    "wooden_pickaxe", "cobblestone", "stone_pickaxe", "raw_iron", "iron_ingot",
    "furnace", "iron_pickaxe", "diamond", "raw_gold", "gold_ingot", "redstone",
    "diamond_pickaxe", "golden_sword", "compass",
)

os.environ.update(
    MC_HOST="127.0.0.1",
    RUNTIME_CALL_PROBE="1",
    PYTHONDONTWRITEBYTECODE="1",
    TOKENIZERS_PARALLELISM="false",
    TZ="UTC",
)
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(k, None)
os.environ["no_proxy"] = os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1"


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as fp:
        fp.write(line + "\n")


def append_jsonl(row):
    os.makedirs(HERE, exist_ok=True)
    with open(RESULT_JSONL, "a", encoding="utf-8") as fp:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
        fp.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        fp.flush()
        fcntl.flock(fp.fileno(), fcntl.LOCK_UN)


def load_jsonl():
    rows = []
    if not os.path.isfile(RESULT_JSONL):
        return rows
    with open(RESULT_JSONL, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def inv_fingerprint(inv):
    items = []
    for k, v in (inv or {}).items():
        try:
            n = int(v or 0)
        except (TypeError, ValueError):
            continue
        if n != 0:
            items.append((str(k), n))
    return tuple(sorted(items))


def parse_pending_at_return(chats):
    pending = False
    for c in chats or []:
        s = str(c)
        if "[obtain] pending_at_return" in s:
            tail = s.split("pending_at_return", 1)[-1].strip().lower()
            pending = tail.startswith("true") or tail == "1"
    return pending


def hung_from_contract(contract, err=None):
    """Bridge timeout or leaf gov_unsettled → hang path (reset env, restore bag)."""
    hung = False
    hang_cause = None
    if err:
        err_s = str(err)
        if "Timeout" in err_s or "timeout" in err_s.lower():
            hung = True
            hang_cause = "timeout"
    reason = None
    raw = ""
    if isinstance(contract, dict):
        reason = contract.get("reason")
        raw = str(contract.get("raw") or "")
    blob = " ".join(str(x) for x in (reason, raw) if x)
    if "unsettled" in blob:
        hung = True
        hang_cause = "gov_unsettled"
    return hung, hang_cause


def parse_obtain_contract(obs):
    """Prefer the last [contract] obtain line so nested craft/gather/smelt cannot look like the call result."""
    if obs is None:
        return None
    lines = [c for c in SM.chats(obs) if "[contract]" in c]
    if not lines:
        return None
    obtain_lines = [c for c in lines if "[contract] obtain " in c]
    raw = obtain_lines[-1] if obtain_lines else lines[-1]
    m = SM.CONTRACT_RE.search(raw)
    if not m:
        return {"raw": raw, "name": None, "ok": None, "reason": None}
    return {"raw": raw, "name": m.group(1), "ok": m.group(2) == "ok", "reason": m.group(3)}


def parse_phase_seconds(chats):
    for c in reversed(chats or []):
        s = str(c)
        if "[obtain] phase_seconds " in s:
            raw = s.split("[obtain] phase_seconds ", 1)[-1].strip()
            try:
                return json.loads(raw)
            except Exception:
                return None
    return None


def parse_strategies(chats):
    for c in reversed(chats or []):
        s = str(c)
        if "[obtain] strategies " in s:
            raw = s.split("[obtain] strategies ", 1)[-1].strip()
            try:
                return json.loads(raw)
            except Exception:
                return None
    return None


def leaf_condition_name():
    if LEAF_SOURCE == "hand":
        return "hand"
    if LEAF_SOURCE == "prim":
        return "prim"
    name = f"gov_online{int(GOV_ONLINE)}_k{GOV_RETIRE_K}"
    if GOV_QUIESCE_MS:
        name += f"_q{GOV_QUIESCE_MS}" + (f"c{GOV_QUIESCE_CAP_MS}" if GOV_QUIESCE_CAP_MS != 4000 else "")
    if GOV_LATE_FENCE:
        name += "_lf"
    if GOV_LEDGER_MODE != "full":
        name += f"_lm{GOV_LEDGER_MODE}"
    if GOV_RETIRE_MODE != "permanent":
        name += f"_rm{GOV_RETIRE_MODE}"
    if GOV_FALLBACK != "on":
        name += "_strict"
    return name


def pool_fingerprint():
    path = GOV_CANDIDATES
    if not os.path.isfile(path):
        return "none"
    h = hashlib.sha256()
    with open(path, "rb") as fp:
        for chunk in iter(lambda: fp.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def result_identity():
    gov = LEAF_SOURCE == "gov"
    ident = {
        "experiment_id": EXPERIMENT_ID,
        "leaf_source": LEAF_SOURCE,
        "gov_online": bool(GOV_ONLINE) if gov else False,
        "gov_retire_k": GOV_RETIRE_K if gov else None,
        "pool_fingerprint": pool_fingerprint() if gov else None,
        "max_calls": MAX_CALLS,
    }
    if E7_ACTIVE:   # the released identity is unchanged when every E7 switch is at its default
        ident.update({
            "gov_quiesce_ms": GOV_QUIESCE_MS,
            "gov_quiesce_cap_ms": GOV_QUIESCE_CAP_MS,
            "gov_late_fence": bool(GOV_LATE_FENCE),
            "gov_ledger_mode": GOV_LEDGER_MODE if gov else None,
            "gov_retire_mode": GOV_RETIRE_MODE if gov else None,
            "gov_fallback": GOV_FALLBACK if gov else None,
        })
    return ident


def identity_matches(row, ident=None):
    ident = ident or result_identity()
    for k, v in ident.items():
        if row.get(k) != v:
            return False
    return True


def attach_identity(row, seed_overlap=False):
    row.update(result_identity())
    if LEAF_SOURCE == "gov":
        row["seed_overlap"] = bool(seed_overlap)
    return row


if not os.environ.get("OBTAIN_RESULT_JSONL"):
    RESULT_JSONL = os.path.join(HERE, f"obtain_mc_runs_{leaf_condition_name()}_{EXPERIMENT_ID}.jsonl")
if not os.environ.get("OBTAIN_RESULT_PATH"):
    RESULT_PATH = os.path.join(HERE, f"obtain_mc_result_{leaf_condition_name()}_{EXPERIMENT_ID}.json")


def require_gov_experiment_id():
    if LEAF_SOURCE == "gov" and GOV_ONLINE:
        raw = (os.environ.get("OBTAIN_EXPERIMENT_ID") or "").strip()
        if not raw or raw == "default":
            raise SystemExit(
                "gov online requires OBTAIN_EXPERIMENT_ID (not unset/default)"
            )


def load_gov_candidates():
    path = GOV_CANDIDATES
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fp:
        data = json.load(fp)
    return data if isinstance(data, dict) else {}


def gov_injected_source(cands):
    chunks = []
    leaves = {}
    for key, recs in (cands or {}).items():
        leaves[key] = []
        for rec in recs or []:
            code = rec.get("code") or ""
            fn = rec.get("fn")
            cid = rec.get("id")
            if not code or not fn or not cid:
                continue
            chunks.append(code)
            chunks.append(f"globalThis[{json.dumps(fn)}] = {fn};")
            chash = rec.get("code_hash") or hashlib.sha256(code.encode("utf-8")).hexdigest()
            try:
                target_n = int(rec.get("target_n") or 1)
            except (TypeError, ValueError):
                target_n = 1
            leaves[key].append({"id": cid, "fn": fn, "code_hash": chash, "target_n": max(1, target_n)})
    return "\n\n".join(chunks), leaves


def hand_preamble():
    return """
if (typeof globalThis.__leafSource !== "undefined") throw new Error("hand: __leafSource set");
if (typeof globalThis.__govLeaves !== "undefined") throw new Error("hand: __govLeaves set");
if (typeof globalThis.__govLedger !== "undefined") throw new Error("hand: __govLedger set");
if (typeof globalThis.__govOnline !== "undefined") throw new Error("hand: __govOnline set");
if (typeof globalThis.__govNonce !== "undefined") throw new Error("hand: __govNonce set");
if (typeof globalThis.__govRetireK !== "undefined") throw new Error("hand: __govRetireK set");
"""


def prim_preamble(nonce):
    return (
        'globalThis.__leafSource = "prim";\n'
        f"globalThis.__govNonce = {json.dumps(nonce)};\n"
        "globalThis.__govOnline = false;\n"
        "globalThis.__govLeaves = {};\n"
        "globalThis.__govRetireK = 0;\n"
        "globalThis.__govActive = null;\n"
        "globalThis.__govUnsettled = false;\n"
        "globalThis.__govLedger = undefined;\n"
        "try { delete globalThis.__govLedger; } catch (e) {}\n"
    ) + (
        f"globalThis.__govQuiesceMs = {int(GOV_QUIESCE_MS)};\n"
        f"globalThis.__govQuiesceCapMs = {int(GOV_QUIESCE_CAP_MS)};\n" if E7_ACTIVE else ""
    )


def gov_preamble(nonce, leaves, online, retire_k, ledger, ctx=None):
    lines = [
        'globalThis.__leafSource = "gov";',
        f"globalThis.__govOnline = {str(bool(online)).lower()};",
        f"globalThis.__govRetireK = {int(retire_k)};",
        f"globalThis.__govNonce = {json.dumps(nonce)};",
        "globalThis.__govLeaves = " + json.dumps(leaves, ensure_ascii=False) + ";",
    ]
    if E7_ACTIVE:
        lines += [
            f"globalThis.__govQuiesceMs = {int(GOV_QUIESCE_MS)};",
            f"globalThis.__govQuiesceCapMs = {int(GOV_QUIESCE_CAP_MS)};",
            f"globalThis.__govLateFence = {str(bool(GOV_LATE_FENCE)).lower()};",
            f"globalThis.__govLedgerMode = {json.dumps(GOV_LEDGER_MODE)};",
            f"globalThis.__govRetireMode = {json.dumps(GOV_RETIRE_MODE)};",
            f"globalThis.__govFallback = {json.dumps(GOV_FALLBACK)};",
            f"globalThis.__govContext = {json.dumps(str(ctx) if ctx is not None else '')};",
        ]
    if online:
        lines.append("globalThis.__govLedger = " + json.dumps(ledger or {}, ensure_ascii=False) + ";")
    else:
        lines.append("globalThis.__govLedger = undefined;")
        lines.append("try { delete globalThis.__govLedger; } catch (e) {}")
    return "\n".join(lines) + "\n"


def parse_leaf_metrics(chats, nonce):
    chats = chats or []
    deliveries = {}
    gov_tries = 0
    gov_ok = 0
    gov_retired = 0
    gov_fallbacks = 0
    gov_skip_na = 0
    gov_unsettled = 0
    gov_late_pickups = 0
    gov_unquiet = 0
    gov_quarantined = 0
    gov_skip_quarantined = 0
    gov_strict_nofallback = 0
    last_ledger = None
    gov_lines = []
    seen_seq = set()
    prefix = f"[gov] {nonce} " if nonce else None
    ledger_prefix = f"[govledger] {nonce} " if nonce else None
    for c in chats:
        s = str(c)
        if prefix and s.startswith(prefix):
            gov_lines.append(s)
            rest = s[len(prefix):]
            if rest.startswith("try "):
                gov_tries += 1
                if " ok=true " in (" " + rest + " ") or rest.endswith(" ok=true"):
                    gov_ok += 1
            elif rest.startswith("retire "):
                gov_retired += 1
            elif rest.startswith("fallback prim"):
                gov_fallbacks += 1
            elif rest.startswith("skip_na "):
                gov_skip_na += 1
            elif rest.startswith("unsettled"):
                gov_unsettled += 1
            elif rest.startswith("late_pickup "):
                try:
                    gov_late_pickups += int(rest.rsplit("n=", 1)[1].split()[0])
                except (IndexError, ValueError):
                    gov_late_pickups += 1
            elif rest.startswith("unquiet "):
                gov_unquiet += 1
            elif rest.startswith("quarantine "):
                gov_quarantined += 1
            elif rest.startswith("skip_quarantined "):
                gov_skip_quarantined += 1
            elif rest.startswith("strict_nofallback "):
                gov_strict_nofallback += 1
        if ledger_prefix and s.startswith(ledger_prefix):
            raw = s[len(ledger_prefix):]
            try:
                last_ledger = json.loads(raw)
            except Exception:
                pass
        parts = s.split()
        if nonce and len(parts) >= 4 and parts[0] == "[leaf]" and parts[1] == nonce:
            try:
                seq = int(parts[2])
            except (TypeError, ValueError):
                continue
            if seq in seen_seq:
                continue
            seen_seq.add(seq)
            tag = None
            for p in parts[3:]:
                if p.startswith("delivered_by="):
                    tag = p.split("=", 1)[1]
                    break
            if tag:
                deliveries[tag] = deliveries.get(tag, 0) + 1
    return {
        "deliveries": deliveries,
        "gov_tries": gov_tries,
        "gov_ok": gov_ok,
        "gov_retired": gov_retired,
        "gov_fallbacks": gov_fallbacks,
        "gov_skip_na": gov_skip_na,
        "gov_unsettled": gov_unsettled,
        "gov_late_pickups": gov_late_pickups,
        "gov_unquiet": gov_unquiet,
        "gov_quarantined": gov_quarantined,
        "gov_skip_quarantined": gov_skip_quarantined,
        "gov_strict_nofallback": gov_strict_nofallback,
        "gov_lines": gov_lines,
        "ledger": last_ledger,
    }


class LedgerLock:
    def __init__(self, path):
        self.path = path
        self.fp = None

    def acquire(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.fp = open(self.path, "a+", encoding="utf-8")
        try:
            fcntl.flock(self.fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.fp.seek(0)
            other = (self.fp.read() or "").strip()
            self.fp.close()
            self.fp = None
            raise SystemExit(f"ledger lock held by pid {other or '?'} path={self.path}")
        self.fp.seek(0)
        self.fp.truncate()
        self.fp.write(str(os.getpid()))
        self.fp.flush()

    def release(self):
        if self.fp is None:
            return
        try:
            fcntl.flock(self.fp.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            self.fp.close()
        except Exception:
            pass
        self.fp = None


def ledger_file(worker_id, seed):
    # pre-registration v7 (online lineage): one ledger shared by all games of a lineage when GOV_LEDGER_LINEAGE is set
    lineage = os.environ.get("GOV_LEDGER_LINEAGE", "").strip()
    if lineage:
        return os.path.join(GOV_LEDGER_DIR, EXPERIMENT_ID, leaf_condition_name(), f"lineage_{lineage}.json")
    return os.path.join(
        GOV_LEDGER_DIR, EXPERIMENT_ID, leaf_condition_name(), str(seed), f"w{worker_id}.json"
    )


def gov_resume_requested():
    return os.environ.get("GOV_RESUME", "").strip() in ("1", "true", "True", "yes")


def refuse_existing_experiment_ledger():
    if gov_resume_requested():
        return
    root = os.path.join(GOV_LEDGER_DIR, EXPERIMENT_ID)
    if not os.path.isdir(root):
        return
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            if fn.endswith(".json"):
                raise SystemExit(
                    f"ledger already exists for OBTAIN_EXPERIMENT_ID={EXPERIMENT_ID} "
                    f"at {os.path.join(dirpath, fn)}; use a new id or set GOV_RESUME=1"
                )


def load_ledger(path, ident=None):
    ident = ident or result_identity()
    if not os.path.isfile(path):
        return {"experiment_id": ident["experiment_id"], "pool_fingerprint": ident["pool_fingerprint"], "entries": {}}
    try:
        with open(path, encoding="utf-8") as fp:
            data = json.load(fp)
        if not isinstance(data, dict):
            raise SystemExit(f"ledger not an object: {path}")
        if data.get("experiment_id") != ident["experiment_id"] or data.get("pool_fingerprint") != ident["pool_fingerprint"]:
            raise SystemExit(
                f"ledger identity mismatch path={path} "
                f"file=({data.get('experiment_id')},{data.get('pool_fingerprint')}) "
                f"want=({ident['experiment_id']},{ident['pool_fingerprint']})"
            )
        if "entries" not in data or not isinstance(data.get("entries"), dict):
            # legacy unwrapped ledger is a mismatch, not overwrite
            raise SystemExit(f"ledger missing entries wrapper: {path}")
        return data
    except SystemExit:
        raise
    except Exception as e:
        raise SystemExit(f"ledger unreadable {path}: {e}")


def save_ledger(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
        fp.flush()
        os.fsync(fp.fileno())
    os.replace(tmp, path)


def tech_progress(before, after):
    n = 0
    for k in TECH:
        if int((after or {}).get(k, 0) or 0) > int((before or {}).get(k, 0) or 0):
            n += 1
    return n


def save_logs(work, tag):
    import shutil

    dst = os.path.join(KEEP_ROOT, tag)
    os.makedirs(dst, exist_ok=True)
    for name in ("minecraft.log",):
        src = os.path.join(work, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(dst, name))
    latest = os.path.join(work, "server", "logs", "latest.log")
    if os.path.isfile(latest):
        shutil.copy2(latest, os.path.join(dst, "server_latest.log"))
    mf = os.path.join(work, "mineflayer")
    if os.path.isdir(mf):
        shutil.copytree(mf, os.path.join(dst, "mineflayer"), dirs_exist_ok=True)
    log(f"kept logs in {dst}")


def hold_unpaused(env):
    """obtain() can run 240s. If /step starts while the world is frozen,
    vanilla kicks the bot at ~30s (disconnect.timeout) and the bridge
    restarts. Keep the test world ticking for the whole call."""
    try:
        env.unpause()
    except Exception as e:
        log(f"unpause: {e}")
    env.server_paused = False
    env.pause = lambda: False


class HarnessSpawnError(Exception):
    """Test script failed to place the bot on a standable spawn. Not an obtain call."""


JS_HARNESS_WORLD_SPAWN = """
async function __harnessWorldSpawn() {
    const start = bot.entity.position.floored();
    const isAir = (b) => !b || b.name === "air" || b.name === "cave_air" || b.name === "void_air" || b.boundingBox === "empty";
    const isSolid = (b) => !!(b && b.boundingBox === "block");
    let found = null;
    for (let dy = 0; dy <= 96; dy++) {
        const yy = start.y + dy;
        const below = bot.blockAt(new Vec3(start.x, yy - 1, start.z));
        const feet = bot.blockAt(new Vec3(start.x, yy, start.z));
        const head = bot.blockAt(new Vec3(start.x, yy + 1, start.z));
        if (isSolid(below) && isAir(feet) && isAir(head)) {
            found = { x: start.x, y: yy, z: start.z, below: below && below.name };
            break;
        }
    }
    const sp = found || { x: start.x, y: start.y, z: start.z, below: "?" };
    const msg = `[harness] world_spawn open=${start.x},${start.y},${start.z} standable=${sp.x},${sp.y},${sp.z} below=${sp.below}`;
    try { console.log(msg); } catch (err) {}
    try { await bot.chat(msg); } catch (err) {}
}
await __harnessWorldSpawn();
"""

JS_HARNESS_STANDABLE = """
async function __harnessStandable() {
    const p = bot.entity.position.floored();
    const below = bot.blockAt(p.offset(0, -1, 0));
    const feet = bot.blockAt(p);
    const head = bot.blockAt(p.offset(0, 1, 0));
    const isAir = (b) => !b || b.name === "air" || b.name === "cave_air" || b.name === "void_air" || b.boundingBox === "empty";
    const isSolid = (b) => !!(b && b.boundingBox === "block");
    const ok = isSolid(below) && isAir(feet) && isAir(head);
    const msg = `[harness] standable=${ok} pos=${p.x},${p.y},${p.z} below=${below && below.name} feet=${feet && feet.name} head=${head && head.name}`;
    try { console.log(msg); } catch (err) {}
    try { await bot.chat(msg); } catch (err) {}
}
await __harnessStandable();
"""


def js_empty_bag(x, y, z):
    return (
        f'await bot.chat("/tp @s {int(x)} {int(y)} {int(z)}"); await bot.waitForTicks(20);\n'
        "try { await bot.waitForChunksToLoad(); } catch (err) {}\n"
        "await bot.waitForTicks(20);\n"
        """
async function reallyClear() {
    for (let i = 0; i < 16; i++) {
        await bot.chat("/clear @s");
        await bot.waitForTicks(20);
        for (const it of bot.inventory.items().slice()) {
            try { await bot.tossStack(it); } catch (err) {}
        }
        if (bot.inventory.items().length === 0) return;
    }
    try {
        const slots = bot.inventory.slots || [];
        for (let i = 0; i < slots.length; i++) {
            if (slots[i]) bot.inventory.updateSlot(i, null);
        }
    } catch (err) {}
}
await reallyClear();
"""
        + SM.boot_code()
        + "\nbot._obtainGlobal = null; bot._obtainState = { atY: {}, heading: 0 };\n"
        "globalThis.__gatherMaxDistance = 32; globalThis.__gatherDirShift = 0;\n"
        "await bot.waitForTicks(10);"
    )


def js_start_check(x, y, z):
    return """
async function __harnessStartCheck() {
    const sx = %d, sy = %d, sz = %d;
    const p = bot.entity.position;
    const fl = p.floored();
    const dist = Math.hypot(p.x - sx, p.y - sy, p.z - sz);
    const invItems = bot.inventory.items();
    const inv = invItems.map((i) => i.name + ":" + i.count).join(",");
    const below = bot.blockAt(fl.offset(0, -1, 0));
    const feet = bot.blockAt(fl);
    const head = bot.blockAt(fl.offset(0, 1, 0));
    const isAir = (b) => !b || b.name === "air" || b.name === "cave_air" || b.name === "void_air" || b.boundingBox === "empty";
    const isSolid = (b) => !!(b && b.boundingBox === "block");
    const standable = isSolid(below) && isAir(feet) && isAir(head);
    const empty = invItems.length === 0;
    const near = dist <= 2.0;
    const ok = empty && near && standable;
    const msg = "[harness] start ok=" + ok + " empty=" + empty + " dist=" + dist.toFixed(2) + " standable=" + standable + " pos=" + p.x.toFixed(2) + "," + p.y.toFixed(2) + "," + p.z.toFixed(2) + " below=" + (below && below.name) + " inv=" + (inv || "-");
    try { console.log(msg); } catch (err) {}
    try { await bot.chat(msg); } catch (err) {}
}
await __harnessStartCheck();
""" % (int(x), int(y), int(z))


def harness_js_snippets():
    """Every JS fragment the harness sends to env.step, with dummy coords. For node --check."""
    dummy = js_empty_bag(-368, 73, -16)
    return [
        ("world_spawn", JS_HARNESS_WORLD_SPAWN),
        ("standable", JS_HARNESS_STANDABLE),
        ("empty_bag", dummy),
        ("start_check", js_start_check(-368, 73, -16)),
        ("wait_10", "await bot.waitForTicks(10);"),
        ("wait_20", "await bot.waitForTicks(20);"),
        ("wait_30", "await bot.waitForTicks(30);"),
        ("ping", 'await bot.chat("[obtain-harness] ping"); await bot.waitForTicks(40);'),
        ("boot", SM.boot_code()),
        ("obtain_diamond", 'await obtain(bot, "diamond_pickaxe", 1);'),
        ("obtain_sword", 'await obtain(bot, "golden_sword", 1);'),
        ("obtain_compass", 'await obtain(bot, "compass", 1);'),
        ("wait_1", "await bot.waitForTicks(1);"),
        ("hand_preamble", hand_preamble()),
        ("prim_preamble", prim_preamble("n0")),
        ("gov_preamble", gov_preamble("n0", {"log": [{"id": "gov_log_0", "fn": "gov_log_0__x"}]}, False, 2, {})),
    ]


def capture_world_spawn(env, seed):
    """ZS_SPAWN is the official 099 spawn. Other seeds: this world's own surface spawn."""
    if int(seed) == ZS_SEED:
        log(f"[harness] seed={seed} spawn=ZS_SPAWN {ZS_SPAWN}")
        return dict(ZS_SPAWN)
    obs = env.step(JS_HARNESS_WORLD_SPAWN, programs="")
    chats = SM.chats(obs)
    line = next((c for c in chats if str(c).startswith("[harness] world_spawn")), None)
    st = SM.state(obs)
    pos = (st or {}).get("pos") or {}
    spawn = {
        "x": int(pos.get("x") if pos.get("x") is not None else 0),
        "y": int(pos.get("y") if pos.get("y") is not None else 64),
        "z": int(pos.get("z") if pos.get("z") is not None else 0),
    }
    if line and "standable=" in line:
        try:
            part = line.split("standable=", 1)[1].split(" ")[0]
            xs, ys, zs = part.split(",")
            spawn = {"x": int(float(xs)), "y": int(float(ys)), "z": int(float(zs))}
        except Exception as e:
            log(f"[harness] parse world_spawn failed {e} line={line}")
    log(f"[harness] seed={seed} world_spawn={spawn} line={line} pos={pos}")
    return spawn


def empty_bag(env, spawn=None):
    sp = dict(spawn or ZS_SPAWN)
    x, y, z = int(sp["x"]), int(sp["y"]), int(sp["z"])
    env.step(js_empty_bag(x, y, z), programs="")


def assert_standable(env, seed, scene_id, spawn=None):
    """Feet on a solid block, feet+head air. Fail the harness, not obtain."""
    obs = env.step(JS_HARNESS_STANDABLE, programs="")
    chats = SM.chats(obs)
    line = next((c for c in chats if str(c).startswith("[harness] standable=")), None)
    st = SM.state(obs)
    pos = (st or {}).get("pos")
    if not line or "standable=true" not in line:
        raise HarnessSpawnError(
            f"seed={seed} scene={scene_id} spawn={spawn or ZS_SPAWN} pos={pos} chats={chats[-8:]}"
        )
    return pos


def _pos_xyz(pos):
    if not pos:
        return None
    try:
        return (float(pos.get("x")), float(pos.get("y")), float(pos.get("z")))
    except Exception:
        return None


def _dist_to_spawn(pos, spawn):
    xyz = _pos_xyz(pos)
    if xyz is None:
        return 999.0
    return (
        (xyz[0] - float(spawn["x"])) ** 2
        + (xyz[1] - float(spawn["y"])) ** 2
        + (xyz[2] - float(spawn["z"])) ** 2
    ) ** 0.5


def probe_start(env, spawn):
    """Read empty-bag / distance / standable after empty_bag. Returns (ok, info)."""
    sp = dict(spawn)
    obs = env.step(js_start_check(sp["x"], sp["y"], sp["z"]), programs="")
    chats = SM.chats(obs)
    line = next((c for c in chats if str(c).startswith("[harness] start ")), None)
    st = SM.state(obs) or {}
    pos = st.get("pos") or {}
    inv = dict(st.get("inv") or {})
    standable = bool(line and "standable=true" in line)
    empty = not inv
    dist = _dist_to_spawn(pos, sp)
    if line:
        try:
            if "empty=true" in line:
                empty = True
            elif "empty=false" in line:
                empty = False
            if "standable=true" in line:
                standable = True
            elif "standable=false" in line:
                standable = False
            if "dist=" in line:
                dist = float(line.split("dist=", 1)[1].split(" ")[0])
        except Exception as e:
            log(f"[harness] parse start line failed {e} line={line}")
    near = dist <= 2.0
    ok = bool(empty and near and standable)
    info = {
        "ok": ok,
        "empty": empty,
        "near": near,
        "standable": standable,
        "dist": round(dist, 2),
        "pos": pos,
        "inv": inv,
        "line": line,
    }
    log(f"[harness] probe_start {info}")
    return ok, info


def prepare_start(env, seed, scene_id, spawn):
    """empty_bag + start assertions. Retry once. Raises HarnessSpawnError."""
    last = None
    for attempt in (1, 2):
        empty_bag(env, spawn=spawn)
        ok, info = probe_start(env, spawn)
        last = info
        if ok:
            return info
        log(f"[harness] start attempt {attempt} failed seed={seed} scene={scene_id} {info}")
    raise HarnessSpawnError(
        f"seed={seed} scene={scene_id} spawn={spawn} last={last}"
    )


def _harness_fail_row(seed, scene, spawn, err, start_info=None):
    info = start_info or {}
    row = {
        "seed": seed,
        "scene": scene["id"],
        "item": scene["item"],
        "call": scene.get("call"),
        "decision_calls": 0,
        "calls": [],
        "calls_needed": None,
        "success": False,
        "hurt": False,
        "died": False,
        "fail_reasons": ["harness_fail"],
        "elapsed_s_total": 0,
        "subgoals_advanced": [],
        "vanilla_median": VANILLA_MEDIAN.get(scene["id"]),
        "limit": CRITERION_LOCKED["limits"].get(scene["id"]),
        "max_calls": MAX_CALLS,
        "spawn": dict(spawn or {}),
        "start_inv": dict(info.get("inv") or {}),
        "start_pos": info.get("pos"),
        "fresh_world": True,
        "harness_fail": True,
        "harness_error": str(err),
        "leaf_source": LEAF_SOURCE,
        "gov_online": bool(GOV_ONLINE) if LEAF_SOURCE == "gov" else False,
        "gov_retire_k": GOV_RETIRE_K if LEAF_SOURCE == "gov" else None,
        "deliveries": None if LEAF_SOURCE == "hand" else {},
        "gov_tries": 0,
        "gov_ok": 0,
        "gov_retired": 0,
        "gov_fallbacks": 0,
    }
    attach_identity(row, seed_overlap=SEED_OVERLAP)
    append_jsonl({"event": "harness_fail", "seed": seed, "scene": scene["id"], "error": str(err)})
    append_jsonl({"event": "job_done", "row": row})
    return row


def run_job(env, programs, seed, scene, payload_hook=None, spawn=None, keep_tag=None):
    spawn = dict(spawn or ZS_SPAWN)
    try:
        start_info = prepare_start(env, seed, scene["id"], spawn)
    except HarnessSpawnError as e:
        log(f"HARNESS FAIL {e}")
        return _harness_fail_row(seed, scene, spawn, e)
    st_inv = dict(start_info.get("inv") or {})
    st_pos = start_info.get("pos")
    row = {
        "seed": seed,
        "scene": scene["id"],
        "item": scene["item"],
        "call": scene["call"],
        "decision_calls": 0,
        "calls": [],
        "calls_needed": None,
        "success": False,
        "hurt": False,
        "died": False,
        "fail_reasons": [],
        "elapsed_s_total": 0,
        "subgoals_advanced": [],
        "vanilla_median": VANILLA_MEDIAN.get(scene["id"]),
        "limit": CRITERION_LOCKED["limits"].get(scene["id"]),
        "max_calls": MAX_CALLS,
        "spawn": dict(spawn),
        "start_inv": st_inv,
        "start_pos": st_pos,
        "fresh_world": True,
        "leaf_source": LEAF_SOURCE,
        "gov_online": bool(GOV_ONLINE) if LEAF_SOURCE == "gov" else False,
        "gov_retire_k": GOV_RETIRE_K if LEAF_SOURCE == "gov" else None,
        "deliveries": None if LEAF_SOURCE == "hand" else {},
        "gov_tries": 0,
        "gov_ok": 0,
        "gov_retired": 0,
        "gov_fallbacks": 0,
        "gov_skip_na": 0,
        "gov_unsettled": 0,
        "gov_late_pickups": 0,
        "gov_unquiet": 0,
        "gov_quarantined": 0,
        "gov_skip_quarantined": 0,
        "gov_strict_nofallback": 0,
        "leak_events": [],
        "pending_at_return": [],
        "n_hard_resets": 0,
        "env_dead": False,
        "keep_tag": keep_tag,
        "minecraft_log": os.path.join(KEEP_ROOT, keep_tag, "minecraft.log") if keep_tag else None,
    }
    attach_identity(row, seed_overlap=SEED_OVERLAP)
    append_jsonl({"event": "job_start", **{k: row[k] for k in ("seed", "scene")}})
    start_have = int((st_inv or {}).get(scene["item"], 0) or 0)
    prev_snap = None
    framework_touched_bag = False
    for i in range(1, MAX_CALLS + 1):
        if prev_snap is not None:
            try:
                b_st = SM.state(env.step("await bot.waitForTicks(0);", programs=""))
                b_fp = inv_fingerprint((b_st or {}).get("inv"))
                if b_fp != prev_snap and not framework_touched_bag:
                    evt = {
                        "after_call": i - 1,
                        "before_call": i,
                        "a": dict(prev_snap),
                        "b": dict(b_fp),
                    }
                    row["leak_events"].append(evt)
                    log(f"[harness] leak after call {i-1} before call {i}: {evt}")
            except Exception as e:
                log(f"[harness] leak snapshot B failed: {e}")
                row["env_dead"] = True
                row["fail_reasons"].append("env_dead")
                break
        framework_touched_bag = False
        try:
            before = SM.state(env.step("await bot.waitForTicks(10);", programs=""))
        except Exception as e:
            row["fail_reasons"].append("bridge_timeout")
            row["calls"].append({"i": i, "error": f"{type(e).__name__}: {str(e)[:300]}", "hung": True})
            break
        health0 = before.get("health")
        t0 = time.time()
        obs = None
        err = None
        hung = False
        hang_cause = None
        try:
            code = programs() if callable(programs) else programs
            obs = env.step(scene["call"], programs=code)
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:300]}"
            hung = "Timeout" in type(e).__name__ or "timeout" in str(e).lower()
            hang_cause = "timeout" if hung else hang_cause
            obs = None
        elapsed = round(time.time() - t0, 1)
        after = SM.state(obs) if obs is not None else {}
        have = int((after.get("inv") or {}).get(scene["item"], 0) or 0)
        health1 = after.get("health")
        died = health1 is not None and health1 <= 0
        hurt = health0 is not None and health1 is not None and health1 < health0
        c = parse_obtain_contract(obs)
        if (
            c
            and c.get("ok")
            and c.get("reason") == "done"
            and have < 1
            and scene.get("item")
        ):
            c = dict(c)
            c["ok"] = False
            c["reason"] = "inv_mismatch"
        c_hung, c_cause = hung_from_contract(c, err)
        if c_hung:
            hung = True
            hang_cause = c_cause or hang_cause
        advanced = tech_progress(before.get("inv"), after.get("inv"))
        inv_after = dict((after.get("inv") or {}))
        reason = (c or {}).get("reason") if c else (err or "no_contract")
        all_chats = SM.chats(obs) if obs is not None else []
        chats_tail = all_chats[-20:]
        phase_seconds = parse_phase_seconds(all_chats)
        strategies = parse_strategies(all_chats)
        nonce = None
        if callable(programs) and hasattr(programs, "last_nonce"):
            nonce = programs.last_nonce
        leaf_m = parse_leaf_metrics(all_chats, nonce)
        pending = parse_pending_at_return(all_chats)
        if LEAF_SOURCE != "hand":
            for k, v in (leaf_m.get("deliveries") or {}).items():
                row["deliveries"][k] = row["deliveries"].get(k, 0) + v
        row["gov_tries"] += int(leaf_m.get("gov_tries") or 0)
        row["gov_ok"] += int(leaf_m.get("gov_ok") or 0)
        row["gov_retired"] += int(leaf_m.get("gov_retired") or 0)
        row["gov_fallbacks"] += int(leaf_m.get("gov_fallbacks") or 0)
        row["gov_skip_na"] += int(leaf_m.get("gov_skip_na") or 0)
        row["gov_unsettled"] += int(leaf_m.get("gov_unsettled") or 0)
        for _k in ("gov_late_pickups", "gov_unquiet", "gov_quarantined", "gov_skip_quarantined", "gov_strict_nofallback"):
            row[_k] += int(leaf_m.get(_k) or 0)
        if (
            LEAF_SOURCE == "gov"
            and GOV_ONLINE
            and leaf_m.get("ledger") is not None
            and callable(programs)
            and hasattr(programs, "write_ledger")
        ):
            programs.write_ledger(leaf_m["ledger"])
        call_row = {
            "i": i,
            "elapsed_s": elapsed,
            "have": have,
            "health_before": health0,
            "health_after": health1,
            "pos": after.get("pos"),
            "contract": c,
            "reason": reason,
            "error": err,
            "chats_tail": chats_tail,
            "hurt": hurt,
            "died": died,
            "hung": hung,
            "hang_cause": hang_cause,
            "subgoals_advanced": advanced,
            "inv": inv_after,
            "inv_keys": sorted(inv_after.keys()),
            "phase_seconds": phase_seconds,
            "strategies": strategies,
            "deliveries": dict(leaf_m.get("deliveries") or {}),
            "gov_tries": leaf_m.get("gov_tries"),
            "gov_ok": leaf_m.get("gov_ok"),
            "gov_retired": leaf_m.get("gov_retired"),
            "gov_fallbacks": leaf_m.get("gov_fallbacks"),
            "gov_skip_na": leaf_m.get("gov_skip_na"),
            "gov_unsettled": leaf_m.get("gov_unsettled"),
            "gov_lines": leaf_m.get("gov_lines") or [],
            "pending_at_return": pending,
            "nonce": nonce,
            "t0": t0,
            "t1": t0 + elapsed,
        }
        row["calls"].append(call_row)
        row["pending_at_return"].append({"i": i, "pending": pending})
        row["decision_calls"] = i
        row["elapsed_s_total"] = round(row["elapsed_s_total"] + elapsed, 1)
        row["hurt"] = row["hurt"] or hurt
        row["died"] = row["died"] or died
        row["subgoals_advanced"].append(advanced)
        if not (c and c.get("ok")) and have <= start_have:
            row["fail_reasons"].append(reason)
        log(
            f"  {scene['id']} seed={seed} call {i}/{MAX_CALLS} have={have} "
            f"adv={advanced} contract={c} elapsed={elapsed}s health={health1} "
            f"reason={reason} died={died} hung={hung} pending={pending} "
            f"phase={phase_seconds} strategies={strategies} inv={inv_after}"
        )
        append_jsonl(
            {
                "event": "call",
                "task": scene["id"],
                "seed": seed,
                "scene": scene["id"],
                "i": i,
                "have": have,
                "elapsed_s": elapsed,
                "contract": c,
                "reason": reason,
                "subgoals_advanced": advanced,
                "advanced": advanced,
                "inv": inv_after,
                "pos": after.get("pos"),
                "died": died,
                "hung": hung,
                "pending_at_return": pending,
                "error": err,
                "chats_tail": call_row["chats_tail"],
                "phase_seconds": phase_seconds,
                "strategies": strategies,
            }
        )
        prev_snap = inv_fingerprint(inv_after)
        if hung:
            # W3: hung/unsettled first — never credit target-inventory increase as success
            row["fail_reasons"].append("hung")
            try:
                # v6b: gov_unsettled -> keep the at-return bag (legit progress), the reset kills the runaway task;
                # a bridge timeout keeps the conservative pre-call restore
                if hang_cause == "gov_unsettled" and isinstance(inv_after, dict):
                    inv0 = dict(inv_after)
                    restore_kind = "at-return"
                else:
                    inv0 = dict((before or {}).get("inv") or {})
                    restore_kind = "pre-call"
                env.reset(options={"mode": "hard", "wait_ticks": 20, "inventory": inv0})
                proc = getattr(getattr(env, "mineflayer", None), "process", None)
                SM.record_pid(getattr(proc, "pid", None), "mineflayer-node")
                hold_unpaused(env)
                env.step(SM.boot_code(), programs="")
                row["n_hard_resets"] = int(row.get("n_hard_resets") or 0) + 1
                framework_touched_bag = True
                prev_snap = inv_fingerprint(inv0)
                log(f"[harness] hung hang_cause={hang_cause} -> hard reset, restored {restore_kind} bag {inv0}")
            except Exception as e:
                log(f"reset after hung failed: {e}")
                row["env_dead"] = True
                row["fail_reasons"].append("env_dead")
            else:
                continue
        if died:
            # keepInventory=true, as in the pilot: the bot respawns with its bag; the harness does not clear it
            row["fail_reasons"].append("death")
        if row.get("env_dead"):
            break
        if have - start_have >= 1:
            row["success"] = True
            row["calls_needed"] = i
            break
    if not row["success"]:
        row["calls_needed"] = f"{MAX_CALLS}+"
    append_jsonl({"event": "job_done", "row": row})
    return row


def _sum_phases(rows):
    keys = ("surface", "descend", "search_ore", "mine", "smelt", "craft", "path_fail", "escape")
    tot = {k: 0.0 for k in keys}
    n = 0
    for r in rows:
        for c in r.get("calls") or []:
            ph = c.get("phase_seconds") or {}
            if not ph:
                continue
            n += 1
            for k in keys:
                tot[k] += float(ph.get(k) or 0)
    if not n:
        return None
    return {k: round(v, 1) for k, v in tot.items()}


def gate_summary(runs):
    harness_n = sum(1 for r in runs if r.get("harness_fail"))
    complete = [
        r
        for r in runs
        if not r.get("harness_fail")
        and (r.get("success") or (r.get("calls") and len(r["calls"]) >= 1))
    ]
    gates = {"criterion_locked": dict(CRITERION_LOCKED), "partial": False}
    all_ok = True
    n_ok = sum(1 for r in complete if r.get("success"))
    rate = (n_ok / len(complete)) if complete else 0.0
    rate_ok = bool(complete) and rate >= 0.90
    gates["success_rate"] = {"value": rate, "n": len(complete), "ok": rate_ok, "criterion": ">=0.90"}
    if not rate_ok:
        all_ok = False
    for scene in ITEMS:
        sid = scene["id"]
        rows = [r for r in complete if r.get("scene") == sid]
        needed = [r["calls_needed"] for r in rows if r.get("success") and r.get("calls_needed")]
        med = statistics.median(needed) if needed else None
        limit = CRITERION_LOCKED["limits"][sid]
        n = len(rows)
        n_success = sum(1 for r in rows if r.get("success"))
        scene_rate = (n_success / n) if n else 0.0
        calls_ok = med is not None and med <= limit
        ok = n > 0 and scene_rate >= 0.90 and calls_ok
        gates[sid] = {
            "calls_needed_median": med,
            "calls_needed_limit": limit,
            "vanilla_median": VANILLA_MEDIAN[sid],
            "n_success": n_success,
            "n": n,
            "success_rate": scene_rate,
            "elapsed_s_median": statistics.median(
                [r["elapsed_s_total"] for r in rows if r.get("elapsed_s_total") is not None]
            )
            if rows
            else None,
            "subgoals_advanced_median": statistics.median(
                [statistics.mean(r["subgoals_advanced"]) for r in rows if r.get("subgoals_advanced")]
            )
            if any(r.get("subgoals_advanced") for r in rows)
            else None,
            "n_hurt": sum(1 for r in rows if r.get("hurt")),
            "n_died": sum(1 for r in rows if r.get("died")),
            "fail_reasons": [x for r in rows for x in (r.get("fail_reasons") or [])],
            "phase_seconds_sum": _sum_phases(rows),
            "ok": ok,
        }
        if not ok:
            all_ok = False
    expected = len(SEEDS) * len(ITEMS)
    gates["n_harness_fail"] = harness_n
    if harness_n:
        gates["partial"] = True
        all_ok = False
    if len(complete) < expected:
        gates["partial"] = True
        all_ok = False
    gates["all_ok"] = all_ok
    gates["met_prewritten"] = all_ok
    n_leak = sum(len(r.get("leak_events") or []) for r in complete)
    n_pending = 0
    for r in complete:
        for p in r.get("pending_at_return") or []:
            if isinstance(p, dict) and p.get("pending"):
                n_pending += 1
            elif p is True:
                n_pending += 1
    gates["n_leak_events"] = n_leak
    gates["n_pending_at_return"] = n_pending
    gates["n_env_dead"] = sum(1 for r in complete if r.get("env_dead"))
    gates["n_hard_resets"] = sum(int(r.get("n_hard_resets") or 0) for r in complete)
    if n_leak or n_pending:
        gates["leak_guard"] = False
    else:
        gates["leak_guard"] = True
    return gates


def save_payload(payload):
    with open(RESULT_PATH, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2, default=str)


def _wait_ports_free(mc_port, br_port, tag):
    for port, name in ((mc_port, "MC"), (br_port, "bridge")):
        for _ in range(24):
            if not SM.port_in_use(port):
                break
            time.sleep(0.5)
        log(f"{tag}: port {port} ({name}) {'IN_USE' if SM.port_in_use(port) else 'free'}")


def _close_scene(env, srv, work, tag):
    try:
        if env:
            env.close()
    except Exception as e:
        log(f"env.close: {e}")
    if srv is not None:
        try:
            srv.terminate()
            srv.wait(timeout=30)
        except Exception:
            try:
                srv.kill()
            except Exception:
                pass
    SM.kill_ours()
    SM.OUR_PIDS.clear()
    SM._CLEANED = False
    save_logs(work, tag)
    import shutil

    if os.path.isdir(work):
        shutil.rmtree(work, ignore_errors=True)


def run_one_scene(wid, mc_port, br_port, seed, scene, load_programs, harness_only=False):
    """One seed×scene = one fresh world. Start MC, check spawn, optionally obtain, then tear down."""
    import shutil
    from voyager.env import VoyagerEnv

    work = os.path.join(WORK_ROOT, f"w{wid}_{seed}_{scene['id']}")
    if os.path.exists(work):
        shutil.rmtree(work)
    os.makedirs(work, exist_ok=True)
    log(f"=== worker {wid} seed {seed} scene {scene['id']} work={work} ===")
    srv = SM.start_mc(work, mc_port, seed)
    SM.record_pid(srv.pid, "minecraft-java")
    env = None
    tag = f"r3fix_4/w{wid}_{seed}_{scene['id']}_{time.strftime('%H%M%S')}"
    row_out = None
    try:
        env = VoyagerEnv(
            mc_port=mc_port,
            server_port=br_port,
            request_timeout=REQUEST_TIMEOUT,
            log_path=work,
            pause_verify=True,
        )
        env.reset(options={"mode": "hard", "wait_ticks": 20})
        proc = getattr(getattr(env, "mineflayer", None), "process", None)
        SM.record_pid(getattr(proc, "pid", None), "mineflayer-node")
        hold_unpaused(env)
        env.step(SM.boot_code(), programs="")
        time.sleep(3)
        try:
            ping = env.step(
                'await bot.chat("[obtain-harness] ping"); await bot.waitForTicks(40);',
                programs="",
            )
            log("ping chats=" + str(SM.chats(ping)[-5:]))
            log("ping pos=" + str((SM.state(ping) or {}).get("pos")))
        except Exception as e:
            log(f"ping failed: {type(e).__name__}: {e}")
        spawn = capture_world_spawn(env, seed)
        log(f"[harness] seed={seed} scene={scene['id']} using spawn={spawn}")
        if harness_only:
            try:
                info = prepare_start(env, seed, scene["id"], spawn)
                row_out = {
                    "seed": seed,
                    "scene": scene["id"],
                    "item": scene.get("item"),
                    "success": True,
                    "calls": [],
                    "calls_needed": 0,
                    "start_inv": dict(info.get("inv") or {}),
                    "start_pos": info.get("pos"),
                    "spawn": dict(spawn),
                    "fresh_world": True,
                    "harness_fail": False,
                    "harness_only": True,
                }
                attach_identity(row_out, seed_overlap=SEED_OVERLAP)
                append_jsonl({"event": "job_done", "row": row_out})
                log(f"HARNESS CHECK ok seed={seed} scene={scene['id']} pos={info.get('pos')}")
                return row_out
            except HarnessSpawnError as e:
                log(f"HARNESS FAIL {e}")
                row_out = _harness_fail_row(seed, scene, spawn, e)
                return row_out
        try:
            row_out = run_job(env, load_programs, seed, scene, spawn=spawn, keep_tag=tag)
            return row_out
        except Exception as e:
            log(f"job crashed {type(e).__name__}: {str(e)[:240]}")
            row_out = {
                "seed": seed,
                "scene": scene["id"],
                "success": False,
                "fail_reasons": [f"crash:{type(e).__name__}"],
                "calls": [],
                "fresh_world": True,
            }
            attach_identity(row_out, seed_overlap=SEED_OVERLAP)
            append_jsonl({"event": "job_done", "row": row_out})
            return row_out
    finally:
        _close_scene(env, srv, work, tag)
        _wait_ports_free(mc_port, br_port, f"after {seed}/{scene['id']}")
        if row_out is not None:
            row_out["keep_tag"] = tag
            row_out["minecraft_log"] = os.path.join(KEEP_ROOT, tag, "minecraft.log")


def worker_loop(wid, mc_port, br_port, seeds, scenes=None, max_calls=None, harness_only=False):
    global SEED_OVERLAP, MAX_CALLS
    scenes = list(scenes or ITEMS)
    if max_calls is not None:
        MAX_CALLS = int(max_calls)
    SM.MC_PORT = mc_port
    SM.BR_PORT = br_port
    SM.LOG_PATH = LOG_PATH
    SM.OUR_PIDS.clear()
    SM._CLEANED = False
    assert_ports_unique_paired()
    require_gov_experiment_id()
    if LEAF_SOURCE == "gov" and GOV_ONLINE:
        refuse_existing_experiment_ledger()
    log(f"worker {wid} mc={mc_port} br={br_port} seeds={seeds} scenes={[s['id'] for s in scenes]} harness_only={harness_only} leaf={LEAF_SOURCE} online={GOV_ONLINE}")
    if mc_port in FORBIDDEN_MC or br_port in FORBIDDEN_BR or mc_port in AA_MC or br_port in AA_BR:
        raise SystemExit(f"refusing forbidden ports mc={mc_port} br={br_port}")
    for port, name in ((mc_port, "MC"), (br_port, "bridge")):
        if SM.port_in_use(port):
            raise SystemExit(f"port {port} ({name}) already in use — do not kill others")
    from voyager.control_primitives import load_control_primitives
    from voyager.control_primitives_extra import load_extra_base_skills
    from voyager.v30_flags import V30_NAMED_ACTIONS

    extras = load_extra_base_skills(list(V30_NAMED_ACTIONS))
    base = "\n\n".join(load_control_primitives()) + "\n\n" + "\n\n".join(extras)
    gov_src = ""
    gov_leaves = {}
    SEED_OVERLAP = False
    if LEAF_SOURCE == "gov":
        cands = load_gov_candidates()
        src_seeds = set()
        for recs in (cands or {}).values():
            for c in recs or []:
                if c.get("source_seed") is not None:
                    try:
                        src_seeds.add(int(c["source_seed"]))
                    except (TypeError, ValueError):
                        pass
        overlap = src_seeds & set(int(s) for s in seeds)
        if overlap and not ALLOW_SEED_OVERLAP:
            raise SystemExit(f"gov source_seed overlap with OBTAIN_SEEDS: {sorted(overlap)}; set OBTAIN_ALLOW_SEED_OVERLAP=1 for 099 diagnostics")
        SEED_OVERLAP = bool(overlap)
        gov_src, gov_leaves = gov_injected_source(cands)
        if gov_src:
            base = gov_src + "\n\n" + base

    ident = result_identity()
    ledger_state = {
        "data": {
            "experiment_id": ident["experiment_id"],
            "pool_fingerprint": ident["pool_fingerprint"],
            "entries": {},
        },
        "path": None,
        "lock": None,
    }

    def load_programs():
        nonce = uuid.uuid4().hex
        load_programs.last_nonce = nonce
        if LEAF_SOURCE == "hand":
            pre = hand_preamble()
        elif LEAF_SOURCE == "prim":
            pre = prim_preamble(nonce)
        else:
            entries = (ledger_state["data"] or {}).get("entries") if GOV_ONLINE else None
            pre = gov_preamble(nonce, gov_leaves, GOV_ONLINE, GOV_RETIRE_K, entries, ctx=ledger_state.get("ctx"))
        return pre + "\n" + base

    def write_ledger(data):
        if not GOV_ONLINE or LEAF_SOURCE != "gov":
            return
        entries = data if isinstance(data, dict) else {}
        if "entries" in entries and "experiment_id" in entries:
            wrapped = entries
        else:
            wrapped = {
                "experiment_id": ident["experiment_id"],
                "pool_fingerprint": ident["pool_fingerprint"],
                "entries": entries,
            }
        ledger_state["data"] = wrapped
        if ledger_state["path"]:
            save_ledger(ledger_state["path"], ledger_state["data"])

    load_programs.last_nonce = None
    load_programs.write_ledger = write_ledger

    os.makedirs(WORK_ROOT, exist_ok=True)
    done = set()
    if not harness_only:
        for r in load_jsonl():
            row = r.get("row") or {}
            if r.get("event") == "job_done" and row.get("success") and not row.get("harness_fail"):
                if identity_matches(row, ident) and row.get("seed") in set(seeds):
                    done.add((row.get("seed"), row.get("scene")))
        log(f"skip already-ok {sorted(done)}")
    runs = []
    try:
        for seed in seeds:
            ledger_state["ctx"] = str(seed)   # E7: the world a quarantine applies to
            pending = [s for s in scenes if (seed, s["id"]) not in done]
            if not pending:
                log(f"seed {seed} already complete")
                continue
            lock = None
            if LEAF_SOURCE == "gov" and GOV_ONLINE and not harness_only:
                path = ledger_file(wid, seed)
                lock_path = path + ".lock"
                lock = LedgerLock(lock_path)
                lock.acquire()
                ledger_state["path"] = path
                ledger_state["data"] = load_ledger(path, ident)
                ledger_state["lock"] = lock
            try:
                for scene in pending:
                    log(f"-- {scene['id']} seed={seed} --")
                    row = run_one_scene(
                        wid, mc_port, br_port, seed, scene, load_programs, harness_only=harness_only
                    )
                    runs.append(row)
            finally:
                if lock is not None:
                    if ledger_state["path"] and ledger_state["data"] is not None:
                        save_ledger(ledger_state["path"], ledger_state["data"])
                    lock.release()
                    ledger_state["lock"] = None
                    ledger_state["path"] = None
                    ledger_state["data"] = {
                        "experiment_id": ident["experiment_id"],
                        "pool_fingerprint": ident["pool_fingerprint"],
                        "entries": {},
                    }
    finally:
        SM.kill_ours()
        _wait_ports_free(mc_port, br_port, f"worker {wid} exit")
    return runs


def merge_and_report():
    rows = []
    for rec in load_jsonl():
        if rec.get("event") == "job_done" and rec.get("row"):
            rows.append(rec["row"])
    ident = result_identity()
    by = {}
    for r in rows:
        if not identity_matches(r, ident):
            continue
        by[(r.get("seed"), r.get("scene"))] = r
    planned = {s["id"] for s in ITEMS}
    seed_set = set(int(s) for s in SEEDS)
    runs = [
        r for r in by.values()
        if r.get("scene") in planned and r.get("seed") in seed_set
    ]
    payload = {
        "seeds": SEEDS,
        "scenes": [s["id"] for s in ITEMS],
        "criterion_locked": CRITERION_LOCKED,
        "runs": runs,
        "gates": gate_summary(runs),
        "ended_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    save_payload(payload)
    log("GATES " + json.dumps(payload["gates"], ensure_ascii=False)[:2000])
    print(json.dumps(payload["gates"], ensure_ascii=False, indent=2))
    print("--- per-job wall clock ---")
    for r in sorted(runs, key=lambda x: (str(x.get("scene")), int(x.get("seed") or 0))):
        print(
            f"  {r.get('scene')} seed={r.get('seed')} success={r.get('success')} "
            f"calls_needed={r.get('calls_needed')} elapsed_s_total={r.get('elapsed_s_total')} "
            f"died={r.get('died')} fail={r.get('fail_reasons')} "
            f"leaf={r.get('leaf_source')} deliveries={r.get('deliveries')} "
            f"gov_tries={r.get('gov_tries')} gov_ok={r.get('gov_ok')} "
            f"gov_retired={r.get('gov_retired')} gov_fallbacks={r.get('gov_fallbacks')} "
            f"unsettled={r.get('gov_unsettled')} resets={r.get('n_hard_resets')} "
            f"leak_events={len(r.get('leak_events') or [])} "
            f"pending_at_return={r.get('pending_at_return')} env_dead={r.get('env_dead')}"
        )
        log(
            f"JOB {r.get('scene')} seed={r.get('seed')} success={r.get('success')} "
            f"calls_needed={r.get('calls_needed')} elapsed_s_total={r.get('elapsed_s_total')} "
            f"died={r.get('died')}"
        )
    return 0 if payload["gates"].get("all_ok") else 1


def run_harness_check(wid, mc_port, br_port):
    """099 and 010 × 2 games, 0 obtain calls. Print PASS/FAIL."""
    runs = worker_loop(
        wid, mc_port, br_port, HARNESS_CHECK_SEEDS,
        scenes=HARNESS_CHECK_SCENES, max_calls=0, harness_only=True,
    )
    fails = [r for r in runs if r.get("harness_fail") or not r.get("success")]
    expected = len(HARNESS_CHECK_SEEDS) * len(HARNESS_CHECK_SCENES)
    ok = len(runs) == expected and not fails
    for r in runs:
        log(
            f"harness_check seed={r.get('seed')} scene={r.get('scene')} "
            f"ok={not r.get('harness_fail')} start_pos={r.get('start_pos')} "
            f"start_inv={r.get('start_inv')} fresh={r.get('fresh_world')}"
        )
    print("HARNESS_CHECK", "PASS" if ok else "FAIL")
    print(json.dumps({
        "ok": ok,
        "n": len(runs),
        "expected": expected,
        "n_fail": len(fails),
        "rows": [
            {
                "seed": r.get("seed"),
                "scene": r.get("scene"),
                "harness_fail": r.get("harness_fail"),
                "start_pos": r.get("start_pos"),
                "start_inv": r.get("start_inv"),
                "fresh_world": r.get("fresh_world"),
                "error": r.get("harness_error"),
            }
            for r in runs
        ],
    }, ensure_ascii=False, indent=2, default=str))
    return 0 if ok else 1


def _parse_log_hms(line):
    m = re.match(r"\[(\d{2}):(\d{2}):(\d{2})\]", line)
    if not m:
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))


def analyze_minecraft_log(path, call_windows=None):
    """Read this scene's minecraft.log. Missing file → 未验证, not 无."""
    if not path or not os.path.isfile(path):
        return {
            "verified": False,
            "reason": "未验证",
            "minecraft_log": path,
            "issued_server_command": None,
            "teleported_total": None,
            "teleported_during_calls": None,
            "issued_lines": [],
            "teleported_during_lines": [],
        }
    issued = []
    teleported = []
    during = []
    windows = []
    for t0, t1 in call_windows or []:
        try:
            lt0 = time.localtime(t0)
            lt1 = time.localtime(t1)
            a = lt0.tm_hour * 3600 + lt0.tm_min * 60 + lt0.tm_sec
            b = lt1.tm_hour * 3600 + lt1.tm_min * 60 + lt1.tm_sec
            windows.append((a, b))
        except Exception:
            pass
    with open(path, encoding="utf-8", errors="replace") as fp:
        for line in fp:
            raw = line.rstrip()
            if "issued server command" in raw:
                issued.append(raw[:240])
            if "Teleported" in raw:
                teleported.append(raw[:240])
                hms = _parse_log_hms(raw)
                if hms is not None and windows:
                    for a, b in windows:
                        lo, hi = (a, b) if a <= b else (b, a)
                        if lo <= hms <= hi:
                            during.append(raw[:240])
                            break
    return {
        "verified": True,
        "reason": None,
        "minecraft_log": path,
        "issued_server_command": len(issued),
        "teleported_total": len(teleported),
        "teleported_during_calls": len(during) if windows else None,
        "issued_lines": issued[:20],
        "teleported_during_lines": during[:20],
    }


def grep_mc_slash(work_keep_tag):
    """Deprecated path: prefer analyze_minecraft_log on the scene's nested file."""
    dst = os.path.join(KEEP_ROOT, work_keep_tag)
    path = os.path.join(dst, "minecraft.log")
    return analyze_minecraft_log(path)


def run_gov_smoke():
    """T5: one MC, ports 25620/3120, MAX_CALLS=2, golden_sword, one subprocess per condition."""
    os.environ["OBTAIN_MC_PORT"] = os.environ.get("OBTAIN_MC_PORT") or "25620"
    os.environ["OBTAIN_BR_PORT"] = os.environ.get("OBTAIN_BR_PORT") or "3120"
    mc_port = int(os.environ["OBTAIN_MC_PORT"])
    br_port = int(os.environ["OBTAIN_BR_PORT"])
    if mc_port in FORBIDDEN_MC or br_port in FORBIDDEN_BR:
        raise SystemExit("smoke ports forbidden")
    exp_id = os.environ.get("OBTAIN_EXPERIMENT_ID") or time.strftime("smoke_v6_%Y%m%d_%H%M%S")
    cand = os.environ.get("GOV_CANDIDATES") or os.path.join(REPO, "_gov", "pool_random.json")
    if not os.path.isfile(cand):
        cand = os.path.join(REPO, "_gov", "candidates.json")
    games = [
        ("hand", "0", "20260902010"),
        ("prim", "0", "20260902010"),
        ("gov", "0", "20260902010"),
    ]
    reports = []
    py = sys.executable
    script = os.path.abspath(__file__)
    for i, (src, online, seed) in enumerate(games):
        jsonl = os.path.join(HERE, f"gov_smoke_{i}_{src}_{online}_{seed}.jsonl")
        logp = os.path.join(HERE, f"gov_smoke_{i}.log")
        if os.path.isfile(jsonl):
            os.remove(jsonl)
        env = dict(os.environ)
        env.update({
            "OBTAIN_LEAF_SOURCE": src,
            "GOV_ONLINE": online,
            "OBTAIN_SEEDS": seed,
            "OBTAIN_SCENES": "golden_sword",
            "OBTAIN_MAX_CALLS": "2",
            "OBTAIN_MC_PORT": str(mc_port),
            "OBTAIN_BR_PORT": str(br_port),
            "OBTAIN_WORKER_ID": "0",
            "OBTAIN_WORKERS": "1",
            "OBTAIN_EXPERIMENT_ID": exp_id,
            "OBTAIN_RESULT_JSONL": jsonl,
            "OBTAIN_LOG_PATH": logp,
            "OBTAIN_ALLOW_SEED_OVERLAP": "0",
            "GOV_CANDIDATES": cand,
        })
        print(
            f"SMOKE {i+1}/{len(games)} source={src} online={online} seed={seed} "
            f"mc={mc_port}/{br_port} exp={exp_id}",
            flush=True,
        )
        proc = subprocess.run(
            [py, script, "--worker", "0", "--phase", "full"],
            cwd=REPO,
            env=env,
            timeout=1400,
        )
        row = {}
        if os.path.isfile(jsonl):
            with open(jsonl, encoding="utf-8") as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    recj = json.loads(line)
                    if recj.get("event") == "job_done" and recj.get("row"):
                        row = recj["row"]
        gov_lines = []
        call_windows = []
        for c in row.get("calls") or []:
            gov_lines.extend(c.get("gov_lines") or [])
            if c.get("t0") is not None and c.get("t1") is not None:
                call_windows.append((c["t0"], c["t1"]))
        mc_log = row.get("minecraft_log")
        mc_info = analyze_minecraft_log(mc_log, call_windows)
        rec = {
            "i": i + 1,
            "leaf_source": src,
            "gov_online": online == "1",
            "seed": int(seed),
            "success": row.get("success"),
            "calls_needed": row.get("calls_needed"),
            "contract": (row.get("calls") or [{}])[-1].get("contract") if row.get("calls") else None,
            "deliveries": row.get("deliveries"),
            "gov_tries": row.get("gov_tries"),
            "gov_ok": row.get("gov_ok"),
            "gov_retired": row.get("gov_retired"),
            "gov_fallbacks": row.get("gov_fallbacks"),
            "gov_skip_na": row.get("gov_skip_na"),
            "gov_unsettled": row.get("gov_unsettled"),
            "n_hard_resets": row.get("n_hard_resets"),
            "env_dead": row.get("env_dead"),
            "leak_events": row.get("leak_events") or [],
            "n_leak_events": len(row.get("leak_events") or []),
            "pending_at_return": row.get("pending_at_return"),
            "hung_calls": [
                {"i": c.get("i"), "hang_cause": c.get("hang_cause"), "hung": c.get("hung")}
                for c in (row.get("calls") or [])
                if c.get("hung")
            ],
            "gov_lines": gov_lines,
            "child_exit": proc.returncode,
            "minecraft_log": mc_log,
            "mc_log": mc_info,
            "fail_reasons": row.get("fail_reasons"),
            "keep_tag": row.get("keep_tag"),
            "experiment_id": exp_id,
            "gov_candidates": cand if src == "gov" else None,
        }
        if src == "hand":
            rec["hand_gov_leak"] = bool(gov_lines) or any("[leaf]" in str(x) for c in row.get("calls") or [] for x in (c.get("chats_tail") or []))
            rec["hand_leaf_keys"] = [
                str(s) for c in row.get("calls") or [] for s in (c.get("chats_tail") or [])
                if str(s).startswith("[obtain] strategy ")
            ]
        reports.append(rec)
        print(json.dumps(rec, ensure_ascii=False, default=str)[:4000], flush=True)
        if proc.returncode not in (0, 1):
            print(f"SMOKE child abnormal exit {proc.returncode}", flush=True)
    return reports


def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", type=int, default=None)
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--merge", action="store_true", help="merge jsonl and print gates only")
    ap.add_argument(
        "--phase",
        choices=["debug", "short", "full", "harness_check", "gov_smoke", "tournament"],
        default="full",
        help="debug=1 call diamond_pickaxe; short=table/wood/stone; full=3 seeds × 3 items; harness_check=099/010 × 2 start assertions; gov_smoke=T5 five games; tournament=gov leaf tournament",
    )
    args = ap.parse_args()
    if args.merge:
        return merge_and_report()
    if args.phase == "gov_smoke":
        reports = run_gov_smoke()
        print("SMOKE_SUMMARY " + json.dumps(reports, ensure_ascii=False, default=str)[:4000])
        return 0
    if args.phase == "tournament":
        import gov_tournament as GT
        extra = []
        if args.worker is not None:
            extra.extend(["--worker", str(args.worker)])
        return GT.main(extra)
    if args.fresh and args.worker is None:
        for p in (RESULT_JSONL, RESULT_PATH, LOG_PATH):
            if os.path.isfile(p):
                os.remove(p)
        open(LOG_PATH, "w", encoding="utf-8").write("")
    phase_scenes = ITEMS
    phase_seeds = list(SEEDS)
    phase_max = None
    harness_only = False
    if args.phase == "debug":
        phase_scenes = DEBUG_ITEMS
        phase_seeds = [SEEDS[0]]
        phase_max = 1
    elif args.phase == "short":
        phase_scenes = SHORT_ITEMS
        phase_seeds = [SEEDS[0]]
        phase_max = 4
    elif args.phase == "harness_check":
        w = WORKERS[0 if args.worker is None else int(args.worker)]
        return run_harness_check(w["id"], w["mc"], w["br"])
    if args.worker is None:
        w = WORKERS[0]
        log(
            f"single server mc={w['mc']} br={w['br']} phase={args.phase} "
            f"seeds={phase_seeds} scenes={[s['id'] for s in phase_scenes]} jsonl={RESULT_JSONL}"
        )
        worker_loop(
            w["id"], w["mc"], w["br"], phase_seeds,
            scenes=phase_scenes, max_calls=phase_max, harness_only=harness_only,
        )
        if args.phase == "full":
            return merge_and_report()
        return 0
    w = WORKERS[int(args.worker)]
    worker_loop(
        w["id"], w["mc"], w["br"], w["seeds"] or phase_seeds,
        scenes=phase_scenes, max_calls=phase_max, harness_only=harness_only,
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log(traceback.format_exc())
        raise
