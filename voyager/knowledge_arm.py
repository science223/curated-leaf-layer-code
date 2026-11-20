"""Skill-layer constants: parse from JS, render knowledge-arm text, routing table."""
from __future__ import annotations

import json
import os
import re
from typing import Dict

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXTRA = os.path.join(REPO, "voyager", "control_primitives_extra")
KNOWLEDGE_ARM_PATH = os.path.join(REPO, "_cc0924", "knowledge_arm_text.md")

# Hard routing (v1.1): deep ores → mineOre, surface → gather.
DEEP_ORE_KEYS = ("diamond", "gold", "redstone", "lapis", "iron")
DEEP_ORE_NAMES = (
    "diamond",
    "diamond_ore",
    "deepslate_diamond_ore",
    "gold",
    "gold_ore",
    "raw_gold",
    "deepslate_gold_ore",
    "redstone",
    "redstone_ore",
    "deepslate_redstone_ore",
    "lapis",
    "lapis_lazuli",
    "lapis_ore",
    "deepslate_lapis_ore",
    "iron",
    "iron_ore",
    "raw_iron",
    "deepslate_iron_ore",
)
SURFACE_NAMES = (
    "log",
    "wood",
    "oak_log",
    "spruce_log",
    "sand",
    "dirt",
    "leaves",
    "oak_leaves",
    "sugar_cane",
    "kelp",
)

_JSON_LIT = re.compile(
    r"(?:MINEORE_KNOWLEDGE|COLLECTFLUID_KNOWLEDGE)\s*=\s*JSON\.parse\(`(\{.*?\})`\)",
    re.DOTALL,
)


def extract_js_knowledge(path: str) -> dict:
    text = open(path, encoding="utf-8").read()
    match = _JSON_LIT.search(text)
    if not match:
        raise ValueError(f"no knowledge JSON in {path}")
    return json.loads(match.group(1))


def load_skill_constants() -> Dict[str, dict]:
    return {
        "mineOre": extract_js_knowledge(os.path.join(EXTRA, "mineOre.js")),
        "collectFluid": extract_js_knowledge(os.path.join(EXTRA, "collectFluid.js")),
    }


def render_knowledge_arm_text(const: Dict[str, dict] | None = None) -> str:
    """Declarative facts only: no pseudocode, no per-task tips, no automation."""
    c = const or load_skill_constants()
    ore = c["mineOre"]
    fluid = c["collectFluid"]
    heights = ore["heights"]
    lines = [
        "# Vanilla + equivalent knowledge (pre-registered)",
        "",
        "This text restates constants from the skill implementations as game facts.",
        "It does not include procedures, control flow, or per-task instructions.",
        "",
        "## Ore generation heights (Java Edition 1.19)",
        "",
        f"Source: {ore['source']}",
        "",
    ]
    for key in DEEP_ORE_KEYS:
        h = heights[key]
        lines.append(
            f"- {key}: generates from Y={h['minY']} to Y={h['maxY']}, "
            f"most common near Y={h['peakY']}. Drop item: {h['drops']}. "
            f"Minimum pickaxe tier that yields a drop: {h['minTool']}."
        )
    lines += [
        "",
        "## Strip mining geometry",
        "",
        f"- Parallel tunnels {ore['tunnelHeight']} blocks high, spaced {ore['stripSpacing']} "
        "blocks apart, at the height where that ore is most common, expose nearly all veins "
        "at that layer (veins are a few blocks across).",
        "",
        "## Tools and durability",
        "",
        f"- Diamond, gold, and redstone ores drop nothing unless mined with an "
        f"{heights['diamond']['minTool']} pickaxe or better.",
        f"- Iron and lapis ores drop nothing unless mined with a "
        f"{heights['iron']['minTool']} pickaxe or better.",
        f"- An iron pickaxe has 250 uses; deepslate takes one use per block. "
        f"Below {ore['minDurability']} remaining uses the pickaxe is treated as exhausted.",
        "",
        "## Inventory while mining",
        "",
        "- Strip mining produces large amounts of "
        + ", ".join(ore["discard"])
        + ".",
        f"- Keeping about {ore['keepPlaceBlocks']} cobblestone or cobbled deepslate is enough "
        "to fill holes; the rest can be dropped so the 36-slot bag does not fill.",
        "",
        "## Fluids and buckets",
        "",
        f"Source: {fluid['source']}",
        "",
        f"- A bucket fills only from a source block (still liquid with level "
        f"{fluid['sourceLevel']}). Flowing liquid does not fill a bucket.",
        f"- Cave lava in this version is common near Y={fluid['lavaTargetY']} "
        "(lava seas replace air below Y=-54).",
        f"- Surface water is common near sea level Y={fluid['waterTargetY']}.",
        "- An empty bucket is required; without one the collection cannot start "
        f"({fluid['missingReason']}).",
        "",
        "## Time",
        "",
        f"- A single mining or fluid-collection attempt in this environment is budgeted at "
        f"{ore['budgetSeconds']} seconds.",
        "",
    ]
    return "\n".join(lines)


def write_knowledge_arm_text(path: str | None = None) -> str:
    text = render_knowledge_arm_text()
    dest = path or KNOWLEDGE_ARM_PATH
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as handle:
        handle.write(text)
    return dest


def load_knowledge_arm_text(path: str | None = None) -> str:
    dest = path or KNOWLEDGE_ARM_PATH
    return open(dest, encoding="utf-8").read()
