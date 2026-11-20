"""F3: named-action prompt text generated from the real JS (no LLM)."""
from __future__ import annotations

import re
from typing import List

from voyager.v30_flags import V30_NAMED_ACTIONS
from voyager.knowledge_arm import DEEP_ORE_KEYS, SURFACE_NAMES

_SIG = re.compile(
    r"async\s+function\s+(\w+)\s*\(([^)]*)\)",
    re.MULTILINE,
)
_REASON = re.compile(r'report\(\s*(?:true|false)\s*,\s*"([^"]+)"')


_BEHAVIOR = {
    "gather": (
        "n is how many MORE to obtain (increment ≥ n), same meaning as mineBlock count. "
        "Picks a tool tier then explores rotating compass directions (not a fixed 3); "
        "mines nearby, then explores remaining time × 60s per direction; "
        "same block position failing twice is blacklisted; 240s budget. "
        "Repeated not_found calls rotate the start heading and expand search radius. "
        "Do not hand-write exploreUntil + mineBlock loops (they hit the 300s env timeout)."
    ),
    "smelt": (
        "n is how many MORE to smelt (increment), same meaning as smeltItem count. "
        "Crafts/places a furnace if missing and picks fuel (coal, charcoal, then planks/logs). "
        "If the bag has fewer inputs than n, smelts what it can and returns partial."
    ),
    "craft": (
        "n is the TOTAL to own (unchanged). Uses a nearby crafting table if one exists; "
        "does not place a second table. Already holding ≥ n → already_satisfied."
    ),
    "equipBest": (
        "Equip the best armor currently in the bag for head/torso/legs/feet. "
        "leather < gold < chainmail < iron < diamond < netherite. "
        "Does not equip inventory.items()[0] blindly."
    ),
    "prepare": (
        "Ensure logs ≥ 4 or planks ≥ 12, and a crafting table. "
        "From an empty bag, mine extra logs first (table costs 1 log)."
    ),
    "discardJunk": (
        "Free inventory space until used slots ≤ 28. Alias storeJunk. "
        "Already ≤ 28 → already_satisfied."
    ),
    "mineOre": (
        "n is how many MORE drops to obtain (increment ≥ n), same meaning as mineBlock count. "
        "Uses the 1.19 ore height table, stairs down without falling, then strip-mines "
        "with a fixed tunnel spacing (dig-pad-walk; no exploreUntil). "
        "Probes neighboring blocks for liquid before digging, never digs the block underfoot, "
        "seals liquid and retreats, tosses cobble/deepslate so the bag does not fill, "
        "and reports missing_tool if the pickaxe tier or remaining durability is too low. "
        "Strip position is kept on the bot so a later call continues the same tunnel. "
        "240s budget. For diamond, gold, redstone, lapis, and deep iron only."
    ),
    "collectFluid": (
        "Fills one bucket from a water or lava SOURCE block (level 0), not flowing liquid. "
        "No empty bucket → missing_bucket. Lava is sought at the deep cave lava height; "
        "water near sea level. Returns as soon as the bucket is filled. 240s budget."
    ),
    "obtain": (
        "n is the TOTAL to own. Recursively expands the 1.19 recipe and drop tables. "
        "Leaves: surface → gather; deep ore → large-radius findBlocks then peak Y scan "
        "(no strip mine); smelt → smelt; craft → craft. Tool-tier gates, spare pick if "
        "durability < 16, fuel by smelt count, 240s budget with persisted progress, "
        "discard whitelist on a full bag. Prefer obtain(bot, final_item, n) for the final "
        "goal; if unfinished, call obtain again or handle the reported shortage."
    ),
}

_RULES = {
    "gather": (
        "Use `gather(bot, name, n)` for surface mine/collect/gather/chop/wood tasks "
        f"({', '.join(SURFACE_NAMES)} and similar). "
        "n is how many MORE to obtain (increment ≥ n). "
        "Do not use gather for "
        + ", ".join(DEEP_ORE_KEYS)
        + " — those go to mineOre. "
        "Do not hand-write exploreUntil+mineBlock loops (they hit the 300s timeout); "
        "gather already explores rotating directions × 60s with a 240s budget "
        "and expands search radius on repeated not_found."
    ),
    "smelt": (
        "Use `smelt(bot, item, n)` for smelt/cook tasks. n is how many MORE to smelt. "
        "Do not hand-write furnace setup or fuel picking; smelt places a furnace and picks fuel."
    ),
    "craft": (
        "Use `craft(bot, item, n)` for craft tasks. n is the count to own, not recipe repeats. "
        "If a crafting table is already nearby, use it; do not place a second one."
    ),
    "equipBest": (
        "Use `equipBest(bot)` when the inventory has better armor than what is worn. "
        "Do not hand-write armor-slot loops."
    ),
    "prepare": (
        "Use `prepare(bot)` before a trip that needs wood and a crafting table."
    ),
    "discardJunk": (
        "Start every program with `if (bot.inventoryUsed() >= 32) await discardJunk(bot);` "
        "so the inventory never fills up. Never hand-write chest-depositing code; "
        "use `discardJunk(bot)` whenever a task asks you to deposit or drop useless items."
    ),
    "mineOre": (
        "Use `mineOre(bot, name, n)` for "
        + ", ".join(DEEP_ORE_KEYS)
        + " (deep ores). n is how many MORE drops to obtain. "
        "Do not use gather for these ores. Do not hand-write strip mines or exploreUntil loops."
    ),
    "collectFluid": (
        "Use `collectFluid(bot, fluid)` for water or lava. Only source blocks fill a bucket. "
        "Missing empty bucket → missing_bucket. Do not hand-write bucket filling."
    ),
    "obtain": (
        "Prefer `obtain(bot, item, n)` for the final goal (n = total to own). "
        "If unfinished, call obtain again or handle the reported shortage. "
        "Do not hand-write the recipe tree."
    ),
}


def _reasons(impl_src: str) -> List[str]:
    seen = []
    for match in _REASON.finditer(impl_src or ""):
        reason = match.group(1)
        if reason not in seen:
            seen.append(reason)
    return seen


def render_named_action_docs(name: str, impl_src: str) -> str:
    """Signature + contract + real behavior, generated from implementation."""
    sig = f"async function {name}(bot)"
    match = _SIG.search(impl_src or "")
    if match and match.group(1) == name:
        sig = f"async function {name}({match.group(2).strip()})"
    reasons = _reasons(impl_src)
    reason_s = " | ".join(reasons) if reasons else "done | fail"
    behavior = _BEHAVIOR.get(name, "")
    body = [
        f"  // Contract: {behavior}",
        f"  // Reasons: {reason_s}. Returns {{ok, reason}} and chats `[contract] {name} ok|fail reason`.",
        "  // Implementation is already loaded; call it, do not rewrite it.",
    ]
    return f"{sig} {{\n" + "\n".join(body) + "\n}"


def render_named_action_rule(name: str) -> str:
    return _RULES.get(name, f"Use `{name}(bot, ...)` when it matches the task.")


def render_named_action_context(name: str, impl_src: str) -> str:
    rule = render_named_action_rule(name)
    docs = render_named_action_docs(name, impl_src)
    return f"//! USE-RULE: - {rule}\n{docs}\n"


def is_v30_named(name: str) -> bool:
    return name in V30_NAMED_ACTIONS
