"""Static `requires` extraction from skill JavaScript (no LLM)."""
from __future__ import annotations

import re

_ERROR_MAX = 60

_BLOCK_DOT = re.compile(r"\bblocksByName\.([A-Za-z0-9_]+)")
_BLOCK_BR = re.compile(r"\bblocksByName\[\s*['\"]([^'\"]+)['\"]\s*\]")
_ITEM_DOT = re.compile(r"\bitemsByName\.([A-Za-z0-9_]+)")
_ITEM_BR = re.compile(r"\bitemsByName\[\s*['\"]([^'\"]+)['\"]\s*\]")
_ENT_DOT = re.compile(r"\bentitiesByName\.([A-Za-z0-9_]+)")
_ENT_EQ = re.compile(
    r"""(?:entity|entities|e|mob)\.name\s*===?\s*['\"]([^'\"]+)['\"]"""
)
_COUNT_ITEM = re.compile(
    r"""(?:countItem|findInventoryItem)\(\s*['\"]([^'\"]+)['\"]"""
)
_THROW = re.compile(
    r"throw\s+new\s+Error\(\s*(?:`([^`]*)`|\"([^\"]*)\"|'([^']*)')",
    re.DOTALL,
)


def _uniq(seq):
    out = []
    seen = set()
    for item in seq:
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _clip_error(text):
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= _ERROR_MAX:
        return text
    return text[: _ERROR_MAX - 1] + "…"


def extract_requires(code):
    """Return `{blocks, entities, inventory, errors}` from skill source.

    Looks for `blocksByName` / `findBlock` matching names, entity name filters,
    inventory item lookups, and `throw new Error("…")` message literals.
    """
    src = code or ""
    blocks = _BLOCK_DOT.findall(src) + _BLOCK_BR.findall(src)
    entities = _ENT_DOT.findall(src) + _ENT_EQ.findall(src)
    inventory = _ITEM_DOT.findall(src) + _ITEM_BR.findall(src) + _COUNT_ITEM.findall(src)
    errors = []
    for match in _THROW.finditer(src):
        raw = next((g for g in match.groups() if g is not None), "")
        clipped = _clip_error(raw)
        if clipped:
            errors.append(clipped)
    return {
        "blocks": _uniq(blocks),
        "entities": _uniq(entities),
        "inventory": _uniq(inventory),
        "errors": _uniq(errors),
    }


def compact_requires(requires):
    if not requires:
        return "{}"
    parts = []
    for key in ("blocks", "entities", "inventory", "errors"):
        vals = requires.get(key) or []
        if not vals:
            continue
        parts.append(f"{key}:{','.join(str(v) for v in vals)}")
    return "{" + ";".join(parts) + "}"


def compact_record(record):
    if not record:
        return "[]"
    return "[" + ";".join(str(item) for item in record) + "]"


def delta_is_empty(delta):
    if not isinstance(delta, dict) or not delta:
        return True
    for value in delta.values():
        if value not in (None, 0, "", [], {}):
            return False
    return True


def outcome_record_label(rec):
    """ok+Δ / ok∅ / err:<message> from a chain outcome dict."""
    if not isinstance(rec, dict):
        return "err:unknown"
    if rec.get("ok"):
        no_effect = rec.get("no_effect")
        if no_effect is None:
            no_effect = delta_is_empty(rec.get("delta"))
        return "ok∅" if no_effect else "ok+Δ"
    err = rec.get("err")
    if err is None:
        return "err:error"
    return "err:" + _clip_error(str(err))


def records_from_chain_log(path, limit=5):
    """Last `limit` chain outcomes per skill name from existing chain_log.jsonl."""
    import json
    import os

    by_name = {}
    if not path or not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            for rec in row.get("outcomes") or []:
                if not isinstance(rec, dict):
                    continue
                name = rec.get("name")
                if not name:
                    continue
                by_name.setdefault(name, []).append(outcome_record_label(rec))
    return {name: labels[-limit:] for name, labels in by_name.items()}
