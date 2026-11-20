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


def e7_from_env(world_seed=None):
    """E7 switches from the environment (预注册_账本消融); None when every switch is at its default."""
    q = int(os.environ.get("GOV_QUIESCE_MS", "0") or 0)
    cap = int(os.environ.get("GOV_QUIESCE_CAP_MS", "4000") or 4000)
    fence = (os.environ.get("GOV_LATE_FENCE") or "0").strip() in ("1", "true", "True", "yes")
    lmode = (os.environ.get("GOV_LEDGER_MODE") or "full").strip()
    rmode = (os.environ.get("GOV_RETIRE_MODE") or "permanent").strip()
    fb = (os.environ.get("GOV_FALLBACK") or "on").strip()
    assert lmode in ("full", "off", "retire_only", "reorder_only"), lmode
    assert rmode in ("permanent", "quarantine"), rmode
    assert fb in ("on", "off"), fb
    if not (q or fence or lmode != "full" or rmode != "permanent" or fb != "on"):
        return None
    return {"quiesce_ms": q, "quiesce_cap_ms": cap, "late_fence": fence, "ledger_mode": lmode, "retire_mode": rmode,
            "fallback": fb, "context": "" if world_seed is None else str(world_seed)}


def gov_preamble(nonce, leaves, online, retire_k, ledger, identity=None, e7=None):
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
    if e7:
        lines += [
            f"globalThis.__govQuiesceMs = {int(e7['quiesce_ms'])};",
            f"globalThis.__govQuiesceCapMs = {int(e7['quiesce_cap_ms'])};",
            f"globalThis.__govLateFence = {str(bool(e7['late_fence'])).lower()};",
            f"globalThis.__govLedgerMode = {json.dumps(e7['ledger_mode'])};",
            f"globalThis.__govRetireMode = {json.dumps(e7['retire_mode'])};",
            f"globalThis.__govFallback = {json.dumps(e7['fallback'])};",
            f"globalThis.__govContext = {json.dumps(e7['context'])};",
        ]
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
    extra = {"gov_late_pickups": 0, "gov_unquiet": 0, "gov_quarantined": 0, "gov_skip_quarantined": 0,
             "gov_strict_nofallback": 0}
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
            elif rest.startswith("late_pickup "):
                try:
                    extra["gov_late_pickups"] += int(rest.rsplit("n=", 1)[1].split()[0])
                except (IndexError, ValueError):
                    extra["gov_late_pickups"] += 1
            elif rest.startswith("unquiet "):
                extra["gov_unquiet"] += 1
            elif rest.startswith("quarantine "):
                extra["gov_quarantined"] += 1
            elif rest.startswith("skip_quarantined "):
                extra["gov_skip_quarantined"] += 1
            elif rest.startswith("strict_nofallback "):
                extra["gov_strict_nofallback"] += 1
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
        **extra,
        "gov_lines": gov_lines,
        "ledger": last_ledger,
    }


def bucket_delivered_by(tag):
    t = str(tag or "")
    if t.startswith("candidate:innate_"):   # E9: innate candidates are not harvested or written ones
        return "innate"
    if t.startswith("unquiet:"):
        return "unquiet"
    if t.startswith("candidate:") or t == "candidate":
        return "candidate"
    if t.startswith("innate"):
        return "innate"
    if t == "prim_fallback":
        return "prim_fallback"
    return None


def leaf_source_counts(deliveries):
    out = {"innate": 0, "candidate": 0, "prim_fallback": 0, "unquiet": 0}
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
        "gov_late_pickups": 0,
        "gov_unquiet": 0,
        "gov_quarantined": 0,
        "gov_skip_quarantined": 0,
        "gov_strict_nofallback": 0,
        "gov_leaf_deliveries": {"innate": 0, "candidate": 0, "prim_fallback": 0, "unquiet": 0},
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


def ledger_calls(entries):
    """E9: calls booked in a ledger (sum over candidates)."""
    return sum(int((e or {}).get("calls") or 0) for e in (entries or {}).values())


def ledger_sha(entries):
    """E9: content hash of ledger entries (canonical JSON); chains the games of a lineage."""
    return hashlib.sha256(json.dumps(entries or {}, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _ledger_entries(parsed):
    if not isinstance(parsed, dict):
        return None
    if "entries" in parsed and isinstance(parsed.get("entries"), dict):
        return parsed["entries"]
    return parsed


class GovEpisode:
    """In-attempt ledger + counters. Fresh object (or reset) when the attempt changes."""

    def __init__(self, pool, *, online=False, identity=None, retire_k=2, nonce_fn=None, ledger_file=None, e7=None):
        self.pool_src, self.leaves = gov_injected_source(pool)
        self.online = bool(online)
        self.identity = dict(identity or {})
        self.retire_k = int(retire_k)
        self.nonce_fn = nonce_fn or (lambda: uuid.uuid4().hex)
        self.ledger = {}
        self.last_nonce = None
        self.metrics = empty_gov_metrics()
        # E9: a lineage ledger carried across games. The committed file is only read here; this attempt's bookings go
        # to its own working copy (<ledger_file>.work.<id>, written after every booked step), which the lineage runner
        # commits once the game ends DONE, so a replayed game starts again from the ledger the lineage held before it.
        self.ledger_file = ledger_file or None
        self.e7 = e7
        self.lineage_id = os.environ.get("GOV_LEDGER_LINEAGE_ID") or None
        self.ledger_loaded_calls = 0
        self.ledger_loaded_sha = None
        self.ledger_work_file = None
        if self.ledger_file and self.online:
            data = {}
            if os.path.isfile(self.ledger_file):
                with open(self.ledger_file, encoding="utf-8") as fp:
                    data = json.load(fp)
                if self.lineage_id and data.get("lineage_id") != self.lineage_id:
                    raise ValueError(f"ledger {self.ledger_file} belongs to lineage {data.get('lineage_id')!r}, not {self.lineage_id!r}")
            self.ledger = _ledger_entries(data) or {}
            self.ledger_loaded_calls = ledger_calls(self.ledger)
            self.ledger_loaded_sha = ledger_sha(self.ledger)

    def save_ledger(self):
        if not (self.ledger_file and self.online):
            return
        if self.ledger_work_file is None:
            self.ledger_work_file = f"{self.ledger_file}.work.{uuid.uuid4().hex[:12]}"
        data = {"lineage_id": self.lineage_id, "experiment_id": self.identity.get("experiment_id"),
                "pool_fingerprint": self.identity.get("pool_fingerprint"), "base_sha": self.ledger_loaded_sha,
                "entries": self.ledger}
        tmp = self.ledger_work_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(data, fp, ensure_ascii=False)
        os.replace(tmp, self.ledger_work_file)

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
            "gov_late_pickups",
            "gov_unquiet",
            "gov_quarantined",
            "gov_skip_quarantined",
            "gov_strict_nofallback",
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
            "gov_late_pickups": int(m.get("gov_late_pickups") or 0),
            "gov_unquiet": int(m.get("gov_unquiet") or 0),
            "gov_quarantined": int(m.get("gov_quarantined") or 0),
            "gov_skip_quarantined": int(m.get("gov_skip_quarantined") or 0),
            "gov_strict_nofallback": int(m.get("gov_strict_nofallback") or 0),
            "gov_leaf_deliveries": dict(m.get("gov_leaf_deliveries") or leaf_source_counts({})),
            "gov_ledger_file": self.ledger_file,
            "gov_ledger_work_file": self.ledger_work_file,
            "gov_ledger_lineage_id": self.lineage_id,
            "gov_ledger_loaded_calls": self.ledger_loaded_calls,
            "gov_ledger_final_calls": ledger_calls(self.ledger),
            "gov_ledger_loaded_sha": self.ledger_loaded_sha,
            "gov_ledger_final_sha": ledger_sha(self.ledger) if (self.ledger_file and self.online) else None,
            "gov_retire_k": self.retire_k,
            "gov_e7": dict(self.e7) if self.e7 else None,
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
                e7=self.e7,
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
                self.save_ledger()
            return events

        return step


def make_stamped_env_step(orig_step, gov_episode=None):
    """Wrap env.step. No episode → same stamp as the Rule A pilot (byte-identical)."""
    if gov_episode is None:
        def _stamped_env_step(code, programs=""):
            return orig_step(STEP_START + str(code or ""), programs=programs)

        return _stamped_env_step
    return gov_episode.wrap_step(orig_step)
