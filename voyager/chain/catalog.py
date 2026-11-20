"""Build the full-library skill catalog shown to the chain planner.

Default order is insertion-by-created_iter. Dynamic governance uses
`governor.shown_list` unless `order=` injects an explicit ranking.
"""
from __future__ import annotations

from voyager.chain.requires import compact_record, compact_requires, extract_requires

CATALOG_LINE_MAX = 300


def _signature_params(signature):
    if not isinstance(signature, dict):
        return [{"name": "bot", "default": None}]
    params = signature.get("params")
    if not isinstance(params, list) or not params:
        return [{"name": "bot", "default": None}]
    return params


def format_signature(name, signature=None):
    """Render `name(bot, p=default…)` for catalog lines."""
    parts = []
    for i, param in enumerate(_signature_params(signature)):
        if not isinstance(param, dict):
            continue
        pname = param.get("name") or ("bot" if i == 0 else f"p{i}")
        default = param.get("default")
        if i == 0 or default is None:
            parts.append(str(pname))
        else:
            parts.append(f"{pname}={default}")
    if not parts:
        parts = ["bot"]
    return f"{name}({', '.join(parts)})"


def first_description_line(description):
    """First useful line of a skills.json description field."""
    text = description or ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ""
    first = lines[0]
    if first.startswith("async function"):
        for line in lines[1:]:
            if line.startswith("//"):
                return line[2:].strip()
        return first
    return first


def catalog_entry(name, entry, last_exec=None, *, include_requires=False, record=None):
    entry = entry or {}
    signature = entry.get("signature")
    if not isinstance(signature, dict) or not signature.get("params"):
        signature = {"params": [{"name": "bot", "default": None}]}
    item = {
        "name": name,
        "signature": signature,
        "description": first_description_line(entry.get("description")),
        "created_iter": entry.get("created_iter"),
    }
    if last_exec is not None:
        item["last_exec"] = last_exec
    if include_requires:
        item["requires"] = extract_requires(entry.get("code") or "")
    if record is not None:
        item["record"] = list(record)
    return item


def _created_iter_sort_key(skills, name):
    created = skills[name].get("created_iter")
    return (
        created is None,
        created if isinstance(created, int) else 0,
        name,
    )


def build_catalog(skill_manager, order=None, *, include_requires=False, records=None):
    """Return `[{name, signature, description}, …]`.

    Default order: `created_iter` ascending (then name). When
    `skill_manager.governance == "dynamic"` and `order` is omitted, use
    `governor.shown_list(cur_iter)` (most recently executed first), library
    members only. `order=` still injects an explicit ranking; names not in
    the library are skipped.
    """
    skills = getattr(skill_manager, "skills", None) or {}
    governance = str(getattr(skill_manager, "governance", "static") or "static")
    governor = getattr(skill_manager, "governor", None)
    task_idx = getattr(skill_manager, "cur_iter", None)
    if task_idx is None:
        task_idx = 0
    if (
        order is None
        and governance == "dynamic"
        and governor is not None
        and hasattr(governor, "shown_list")
    ):
        order = governor.shown_list(task_idx)
    if order is not None:
        names = [n for n in order if n in skills]
        extra = [n for n in skills if n not in set(names)]
        extra.sort(key=lambda n: _created_iter_sort_key(skills, n))
        names.extend(extra)
    else:
        names = sorted(skills.keys(), key=lambda n: _created_iter_sort_key(skills, n))
    catalog = []
    attach_last = (
        governance == "dynamic"
        and governor is not None
        and hasattr(governor, "last_exec_label")
    )
    for name in names:
        last_exec = None
        if attach_last:
            last_exec = governor.last_exec_label(name, task_idx)
        rec = None
        if records is not None:
            rec = records.get(name) or []
        catalog.append(
            catalog_entry(
                name,
                skills[name],
                last_exec=last_exec,
                include_requires=include_requires,
                record=rec,
            )
        )
    return catalog


def format_catalog(catalog, *, max_line_chars=None):
    """One catalog line per skill: `name(bot, p=default…) — description`.

    Dynamic catalogs append ` [last_exec: k tasks ago | never]`.
    Planner v2 may append `requires=` / `record=` and clip to `max_line_chars`.
    """
    lines = []
    for item in catalog or []:
        name = item.get("name") or ""
        sig = format_signature(name, item.get("signature"))
        desc = item.get("description") or ""
        prefix = f"{sig} — {desc}".rstrip()
        extras = []
        last_exec = item.get("last_exec")
        if last_exec is not None:
            extras.append(f"[last_exec: {last_exec}]")
        if "requires" in item:
            extras.append(f"requires={compact_requires(item.get('requires'))}")
        if "record" in item:
            extras.append(f"record={compact_record(item.get('record'))}")
        extra = (" " + " ".join(extras)) if extras else ""
        line = prefix + extra
        if max_line_chars and len(line) > max_line_chars:
            budget = max_line_chars - 1 - len(extra)
            if budget >= 8:
                line = prefix[:budget] + "…" + extra
            else:
                line = (prefix + extra)[: max_line_chars - 1] + "…"
        lines.append(line)
    return "\n".join(lines) if lines else "(empty library)"
