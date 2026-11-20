"""Leaf-item alias table (Python copy). JS copy lives in obtain.js as OBTAIN_LEAF_ALIAS.

Must stay byte-identical in key/value pairs; tests compare both.
"""
from __future__ import annotations

OBTAIN_LEAF_ALIAS = {
    "log": "log",
    "wood": "log",
    "logs": "log",
    "wood_log": "log",
    "wooden_log": "log",
    "oak_log": "log",
    "spruce_log": "log",
    "birch_log": "log",
    "jungle_log": "log",
    "acacia_log": "log",
    "dark_oak_log": "log",
    "mangrove_log": "log",
    "cherry_log": "log",
    "stripped_oak_log": "log",
    "stripped_spruce_log": "log",
    "stripped_birch_log": "log",
    "stripped_jungle_log": "log",
    "stripped_acacia_log": "log",
    "stripped_dark_oak_log": "log",
    "stripped_mangrove_log": "log",
    "crimson_stem": "log",
    "warped_stem": "log",
    "cobblestone": "cobblestone",
    "stone": "cobblestone",
    "cobbled_deepslate": "cobblestone",
    "blackstone": "cobblestone",
    "coal": "coal",
    "coal_ore": "coal",
    "deepslate_coal_ore": "coal",
    "sand": "sand",
    "dirt": "dirt",
    "grass_block": "dirt",
    "gravel": "gravel",
    "clay": "clay",
    "diamond": "diamond",
    "diamond_ore": "diamond",
    "deepslate_diamond_ore": "diamond",
    "raw_iron": "raw_iron",
    "iron_ore": "raw_iron",
    "deepslate_iron_ore": "raw_iron",
    "iron": "raw_iron",
    "raw_gold": "raw_gold",
    "gold_ore": "raw_gold",
    "deepslate_gold_ore": "raw_gold",
    "gold": "raw_gold",
    "redstone": "redstone",
    "redstone_ore": "redstone",
    "deepslate_redstone_ore": "redstone",
    "lapis_lazuli": "lapis_lazuli",
    "lapis": "lapis_lazuli",
    "lapis_ore": "lapis_lazuli",
    "deepslate_lapis_ore": "lapis_lazuli",
    "lapis_lazuli_ore": "lapis_lazuli",
}

SCENE_LEAF_KEYS = (
    "log",
    "cobblestone",
    "coal",
    "raw_iron",
    "diamond",
    "raw_gold",
    "redstone",
    "sand",
)

EVAL_SEEDS = frozenset(
    {
        20260902010,
        20260902011,
        20260902003,
        20260902004,
        20260902005,
    }
)

OBTAIN_CLASS_VERBS = frozenset({"mine", "gather", "collect", "obtain", "fill"})


def leaf_alias(name: str) -> str:
    n = str(name or "").lower().replace("minecraft:", "")
    if n in OBTAIN_LEAF_ALIAS:
        return OBTAIN_LEAF_ALIAS[n]
    if n.endswith("_log") or n.endswith("_stem"):
        return "log"
    return n


def equiv_from_inv(inv, key: str) -> int:
    """Same contract as _obtainEquivCount: item counts, not block counts."""
    inv = inv or {}
    k = leaf_alias(key)

    def n(name):
        return int(inv.get(name, 0) or 0)

    def sum_suffix(suffix):
        return sum(int(v or 0) for name, v in inv.items() if str(name).endswith(suffix))

    if k == "log":
        logs = sum(
            int(v or 0)
            for name, v in inv.items()
            if str(name).endswith("_log") or str(name).endswith("_stem")
        )
        planks = sum_suffix("_planks")
        return logs + (planks // 4)
    if k == "raw_iron":
        return n("raw_iron") + n("iron_ingot")
    if k == "raw_gold":
        return n("raw_gold") + n("gold_ingot")
    if k == "cobblestone":
        return n("cobblestone")
    return n(k)
