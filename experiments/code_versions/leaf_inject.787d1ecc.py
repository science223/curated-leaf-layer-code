"""Governance leaf injection shared by the obtain harness and Rule A zeroshot.

Semantics match `_cc0924/test_obtain_mc.py`: candidate source + `globalThis[fn]=fn`,
per-step preamble (`__leafSource` / `__govOnline` / `__govLeaves` / `__govLedger` /
`__govNonce`), and nonce-filtered `[govledger]` / `[gov]` / `[leaf]` parsing.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid

STEP_START = "globalThis.__stepStart = Date.now();\n"

# Framework-owned JS (teleport / give / setup start / empty): stamp only, no gov inject.
_FRAMEWORK_MARKERS = (
    "/give @s",
    "/tp @s",
    "/kill @e[type=item]",
    "/weather clear",
    "/gamerule doWeatherCycle",
    "/difficulty",
)


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
            leaves[key].append(
                {"id": cid, "fn": fn, "code_hash": chash, "target_n": max(1, target_n)}
            )
    return "\n\n".join(chunks), leaves


def gov_preamble(nonce, leaves, online, retire_k, ledger, identity=None):
    lines = [
        'globalThis.__leafSource = "gov";',
        f"globalThis.__govOnline = {str(bool(online)).lower()};",
        f"globalThis.__govRetireK = {int(retire_k)};",
        f"globalThis.__govNonce = {json.dumps(nonce)};",
        "globalThis.__govLeaves = " + json.dumps(leaves, ensure_ascii=False) + ";",
    ]
    if online:
        lines.append(
            "globalThis.__govLedger = " + json.dumps(ledger or {}, ensure_ascii=False) + ";"
        )
    else:
        lines.append("globalThis.__govLedger = undefined;")
        lines.append("try { delete globalThis.__govLedger; } catch (e) {}")
    ident = identity or {}
    if ident:
        lines.append(
            "globalThis.__govExperimentId = "
            + json.dumps(ident.get("experiment_id") or "", ensure_ascii=False)
            + ";"
        )
        lines.append(
            "globalThis.__govPoolFingerprint = "
            + json.dumps(ident.get("pool_fingerprint") or "", ensure_ascii=False)
            + ";"
        )
        lines.append(
            "globalThis.__govAttemptId = "
            + json.dumps(ident.get("attempt_id") or "", ensure_ascii=False)
            + ";"
        )
        lines.append(
            "globalThis.__govLibraryId = "
            + json.dumps(ident.get("library_id") or "", ensure_ascii=False)
            + ";"
        )
        lines.append("globalThis.__govTrial = " + json.dumps(ident.get("trial")) + ";")
        lines.append(
            "globalThis.__govTask = "
            + json.dumps(ident.get("task") or "", ensure_ascii=False)
            + ";"
        )
    return "\n".join(lines) + "\n"


def parse_leaf_metrics(chats, nonce):
    chats = chats or []
    deliveries = {}
    gov_tries = 0
    gov_ok = 0
    gov_retired = 0
    gov_fallbacks = 0
    gov_skip_na = 0
    gov_missing = 0
    gov_unsettled = 0
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
            elif rest.startswith("missing "):
                gov_missing += 1
            elif rest.startswith("unsettled"):
                gov_unsettled += 1
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
        "gov_missing": gov_missing,
        "gov_unsettled": gov_unsettled,
        "gov_lines": gov_lines,
        "ledger": last_ledger,
    }


def bucket_delivered_by(tag):
    t = str(tag or "")
    if t.startswith("candidate:") or t == "candidate":
        return "candidate"
    if t.startswith("innate"):
        return "innate"
    if t == "prim_fallback":
        return "prim_fallback"
    return None


def leaf_source_counts(deliveries):
    out = {"innate": 0, "candidate": 0, "prim_fallback": 0}
    for tag, n in (deliveries or {}).items():
        bucket = bucket_delivered_by(tag)
        if bucket:
            out[bucket] += int(n or 0)
    return out


def empty_gov_metrics():
    return {
        "gov_tries": 0,
        "gov_ok": 0,
        "gov_retired": 0,
        "gov_fallbacks": 0,
        "gov_skip_na": 0,
        "gov_missing": 0,
        "gov_unsettled": 0,
        "gov_leaf_deliveries": {"innate": 0, "candidate": 0, "prim_fallback": 0},
    }


def extract_chats(events):
    out = []
    for item in events or []:
        if not (isinstance(item, (list, tuple)) and len(item) >= 2):
            continue
        etype, payload = item[0], item[1]
        if etype == "onChat" and isinstance(payload, dict):
            msg = str(payload.get("onChat") or "").strip()
            if msg:
                out.append(msg)
        elif etype == "onChat" and isinstance(payload, str):
            msg = payload.strip()
            if msg:
                out.append(msg)
    return out


def is_framework_js(code):
    """Teleport / give items / setup start / empty: no gov inject."""
    s = str(code or "")
    if not s.strip():
        return True
    if "async function " in s or "function " in s or "obtain(" in s:
        return False
    return any(m in s for m in _FRAMEWORK_MARKERS)


def load_gov_pool(path):
    with open(path, encoding="utf-8") as fp:
        data = json.load(fp)
    if not isinstance(data, dict):
        raise ValueError(f"gov pool must be a JSON object: {path}")
    return data


def pool_file_fingerprint(path):
    if not path or not os.path.isfile(path):
        return "none"
    digest = hashlib.sha256()
    with open(path, "rb") as fp:
        for chunk in iter(lambda: fp.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()[:12]


def _ledger_entries(parsed):
    if not isinstance(parsed, dict):
        return None
    if "entries" in parsed and isinstance(parsed.get("entries"), dict):
        return parsed["entries"]
    return parsed


class GovEpisode:
    """In-attempt ledger + counters. Fresh object (or reset) when the attempt changes."""

    def __init__(self, pool, *, online=False, identity=None, retire_k=2, nonce_fn=None):
        self.pool_src, self.leaves = gov_injected_source(pool)
        self.online = bool(online)
        self.identity = dict(identity or {})
        self.retire_k = int(retire_k)
        self.nonce_fn = nonce_fn or (lambda: uuid.uuid4().hex)
        self.ledger = {}
        self.last_nonce = None
        self.metrics = empty_gov_metrics()

    def reset_episode(self):
        self.ledger = {}
        self.last_nonce = None
        self.metrics = empty_gov_metrics()

    def _accumulate(self, parsed):
        m = self.metrics
        for key in (
            "gov_tries",
            "gov_ok",
            "gov_retired",
            "gov_fallbacks",
            "gov_skip_na",
            "gov_missing",
            "gov_unsettled",
        ):
            m[key] = int(m.get(key) or 0) + int(parsed.get(key) or 0)
        buckets = m["gov_leaf_deliveries"]
        for src, n in leaf_source_counts(parsed.get("deliveries")).items():
            buckets[src] = int(buckets.get(src) or 0) + int(n)

    def result_fields(self):
        m = self.metrics
        return {
            "gov_tries": int(m.get("gov_tries") or 0),
            "gov_ok": int(m.get("gov_ok") or 0),
            "gov_retired": int(m.get("gov_retired") or 0),
            "gov_fallbacks": int(m.get("gov_fallbacks") or 0),
            "gov_skip_na": int(m.get("gov_skip_na") or 0),
            "gov_missing": int(m.get("gov_missing") or 0),
            "gov_unsettled": int(m.get("gov_unsettled") or 0),
            "gov_leaf_deliveries": dict(m.get("gov_leaf_deliveries") or leaf_source_counts({})),
        }

    def wrap_step(self, orig_step):
        def step(code, programs=""):
            code = str(code or "")
            if is_framework_js(code):
                return orig_step(STEP_START + code, programs=programs)
            nonce = str(self.nonce_fn())
            self.last_nonce = nonce
            pre = gov_preamble(
                nonce,
                self.leaves,
                self.online,
                self.retire_k,
                self.ledger,
                identity=self.identity,
            )
            new_code = STEP_START + pre + code
            extra = self.pool_src
            new_programs = str(programs or "")
            if extra:
                new_programs = new_programs + ("\n\n" if new_programs else "") + extra
            events = orig_step(new_code, programs=new_programs)
            parsed = parse_leaf_metrics(extract_chats(events), nonce)
            self._accumulate(parsed)
            entries = _ledger_entries(parsed.get("ledger"))
            if entries is not None:
                self.ledger = entries
            return events

        return step


def make_stamped_env_step(orig_step, gov_episode=None):
    """Wrap env.step. No episode → same stamp as the Rule A pilot (byte-identical)."""
    if gov_episode is None:
        def _stamped_env_step(code, programs=""):
            return orig_step(STEP_START + str(code or ""), programs=programs)

        return _stamped_env_step
    return gov_episode.wrap_step(orig_step)
