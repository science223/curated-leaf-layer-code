"""Idle-execution guard: one predicate, shared by governor and v2 planner."""
from __future__ import annotations

from voyager.chain.requires import delta_is_empty
from voyager.dedup_keys import NON_INVENTORY_DEDUP_ACTIONS, target_action_for_dedup

NOEFFECT_BAN_PREFIX = (
    "The following skills succeeded twice in this task without inventory change "
    "and must not be reused for the rest of this task: "
)

# Idle-guard exemption is NOT the dedup list. Dedup uses
# NON_INVENTORY_DEDUP_ACTIONS (approach/equip/explore/find/go/locate/move/
# navigate/open/place/search/travel) because those skills are not compared by
# inventory fingerprints. The guard asks a different question: does ok with an
# empty delta mean the skill actually did its job?
#
# Removed from the inherited list (WP-Z offline replay, wpZ_predicate_audit.md):
# - place: a successful place consumes the block, so empty delta cannot be a
#   real success. Required logs: 11 library execs, 6 ok, 4 empty-delta
#   (incl. v22 t3 placeCraftingTable ×3), 2 nonempty; first-following
#   prerequisite errors = 0 in this t3-only set (world-2 C1″ incident is on
#   slac002, not in the required paths). After the predicate change, replay
#   of v22 NEW shows placeCraftingTable stored exec_count=3 / no_effect=0
#   vs replay exec=0 / no_effect=3.
# Kept (ok<5 → 数据不足, or ok+empty had 0 prerequisite-error chain):
# approach/equip/explore/find/go/locate/move/navigate/open/search/travel.
IDLE_EXEMPT_ACTIONS = frozenset(NON_INVENTORY_DEDUP_ACTIONS - {"place"})


def counts_as_effective(rec, skill_entry) -> bool:
    if not isinstance(rec, dict) or not rec.get("ok"):
        return False
    if not delta_is_empty(rec.get("delta")):
        return True
    action = target_action_for_dedup(skill_entry or {})
    return action in IDLE_EXEMPT_ACTIONS


def is_idle(rec, skill_entry) -> bool:
    """ok but not effective (= 空转)."""
    return bool(isinstance(rec, dict) and rec.get("ok")) and not counts_as_effective(rec, skill_entry)


def mark_noeffect_idle(outcomes, skills):
    """Copy outcomes; mark ok-but-idle records with ``no_effect_idle``."""
    skills = skills or {}
    marked = []
    for rec in outcomes or []:
        if not isinstance(rec, dict):
            marked.append(rec)
            continue
        copy = dict(rec)
        name = copy.get("name")
        entry = skills.get(name) if isinstance(name, str) else None
        if is_idle(copy, entry or {}):
            copy["no_effect_idle"] = True
        marked.append(copy)
    return marked


def update_task_idle_counts(counts, outcomes, skills):
    """+1 per library skill that idled this round (once per name)."""
    if counts is None:
        counts = {}
    skills = skills or {}
    seen = set()
    for rec in outcomes or []:
        if not isinstance(rec, dict):
            continue
        name = rec.get("name")
        if not isinstance(name, str) or name.startswith("__") or name in seen:
            continue
        entry = skills.get(name)
        if not isinstance(entry, dict):
            continue
        if is_idle(rec, entry):
            seen.add(name)
            counts[name] = int(counts.get(name) or 0) + 1
    return counts


def banned_skills(counts, min_idle=2):
    return sorted(
        name
        for name, n in (counts or {}).items()
        if int(n or 0) >= int(min_idle)
    )


def noeffect_ban_suffix(names):
    names = [n for n in (names or []) if n]
    if not names:
        return ""
    return "\n\n" + NOEFFECT_BAN_PREFIX + ", ".join(names)


def noeffect_disabled_stub_js(name):
    """JS statement: replace ``name`` with a stub that throws immediately.

    Message matches the planner ban line (English): the skill ran twice with
    no inventory change and must not be reused for the rest of this task.
    """
    msg = (
        f"{name} is disabled for the rest of this task: "
        "it ran twice without any inventory change. Write new code instead of calling it."
    )
    return f'{name} = function () {{ throw new Error("{msg}"); }};'
