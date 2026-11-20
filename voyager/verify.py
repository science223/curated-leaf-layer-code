"""A: rule-based task verifier (v3 §A / v4.1).

Parse task text → (verb, N, target item set). If parsing fails, return None
so the caller can fall back to the LLM critic.

Hold-type verbs (mine/collect/craft/smelt/obtain/cook/gather/fill): success
iff the ending inventory holds ≥ N of the target set, except "Obtain/Mine N
more X" which requires a task-start snapshot and increment ≥ N (missing
snapshot → None). Any hold verb targeting an index.js returnItems /give
item (crafting_table, furnace, chest, iron_pickaxe) always None: those may
be given before the final observation.
eat / kill / equip use state change when that state is provided; otherwise
None. Armor equip only looks at armor slots (not main/off hand); weapons
and tools only look at the main hand. eat/coordinate-deposit "before"
state is the previous round's ending observation, not this step's first
onChat. eat at full hunger → None. kill succeeds only when this-round
kill-event count ≥ N. A verifier exception is caught at the call site
and treated as None (fallback), never a crash.

Deposit N <item>: if the task names a chest at (x, y, z), success and
failure look only at that coordinate. Fail only when that chest is a
countable dict in both snapshots and the target increment is < N;
otherwise fallback. If the task names no coordinate, success is still
any same-coordinate chest increment ≥ N; a shortfall is fallback, not
fail. Newly discovered chests, Unknown/Invalid → known, event names
that do not match the target, discardJunk/storeJunk ok, bag decrease
alone → None (fallback), not success. Missing or non-contiguous
previous-round snapshot → do not reuse a stale bag or chest map.

Unknown targets fall back (None): a verdict is issued only when at least one
target is in the explicit mapping whitelist or minecraft-data item/block
names. eat requires a hunger (food) rise; missing hunger → None.
"""
from __future__ import annotations

import csv
import json
import os
import random
import re
import time
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


HOLD_VERBS = frozenset(
    {"mine", "collect", "craft", "smelt", "obtain", "cook", "gather", "fill"}
)
STATE_VERBS = frozenset({"eat", "kill", "equip"})

WOOD_SPECIES = (
    "oak",
    "spruce",
    "birch",
    "jungle",
    "acacia",
    "dark_oak",
    "mangrove",
    "crimson",
    "warped",
)
WOOD_LOGS = frozenset(
    [f"{s}_log" for s in WOOD_SPECIES if s not in ("crimson", "warped")]
    + ["crimson_stem", "warped_stem"]
    + [f"stripped_{s}_log" for s in WOOD_SPECIES if s not in ("crimson", "warped")]
    + ["stripped_crimson_stem", "stripped_warped_stem"]
)
WOOD_PLANKS = frozenset(f"{s}_planks" for s in WOOD_SPECIES)

# 矿 → 掉落物（1.19）；含 deepslate 变体。
ORE_TO_DROP = {
    "coal_ore": "coal",
    "deepslate_coal_ore": "coal",
    "iron_ore": "raw_iron",
    "deepslate_iron_ore": "raw_iron",
    "copper_ore": "raw_copper",
    "deepslate_copper_ore": "raw_copper",
    "gold_ore": "raw_gold",
    "deepslate_gold_ore": "raw_gold",
    "diamond_ore": "diamond",
    "deepslate_diamond_ore": "diamond",
    "emerald_ore": "emerald",
    "deepslate_emerald_ore": "emerald",
    "lapis_ore": "lapis_lazuli",
    "lapis_lazuli_ore": "lapis_lazuli",
    "deepslate_lapis_ore": "lapis_lazuli",
    "deepslate_lapis_lazuli_ore": "lapis_lazuli",
    "redstone_ore": "redstone",
    "deepslate_redstone_ore": "redstone",
    "nether_gold_ore": "gold_nugget",
    "nether_quartz_ore": "quartz",
    "ancient_debris": "ancient_debris",
    "stone": "cobblestone",
    "clay": "clay_ball",
}

# index.js returnItems may /give these after the program and before observe.
# Actual /give targets in returnItems(): crafting_table, furnace, chest, iron_pickaxe.
RETURN_GIVE_ITEMS = frozenset(
    {"crafting_table", "furnace", "chest", "iron_pickaxe"}
)
# Env may /give these without a real craft. Snapshot+delta is not evidence.
ENV_CRAFT_GIVE_ITEMS = frozenset({"crafting_table", "furnace", "chest"})
FOOD_MAX = 20.0
ARMOR_SUFFIXES = ("helmet", "chestplate", "leggings", "boots")
WEAPON_TOOL_SUFFIXES = (
    "sword",
    "axe",
    "pickaxe",
    "shovel",
    "hoe",
    "bow",
    "crossbow",
    "trident",
    "shield",
)

SMELT_OUTPUT = {
    "raw_iron": "iron_ingot",
    "raw_copper": "copper_ingot",
    "raw_gold": "gold_ingot",
    "cobblestone": "stone",
    "sand": "glass",
    "red_sand": "glass",
    "clay_ball": "brick",
    "cactus": "green_dye",
    "netherrack": "nether_brick",
    "ancient_debris": "netherite_scrap",
    "porkchop": "cooked_porkchop",
    "raw_porkchop": "cooked_porkchop",
    "beef": "cooked_beef",
    "raw_beef": "cooked_beef",
    "chicken": "cooked_chicken",
    "raw_chicken": "cooked_chicken",
    "mutton": "cooked_mutton",
    "raw_mutton": "cooked_mutton",
    "rabbit": "cooked_rabbit",
    "cod": "cooked_cod",
    "raw_cod": "cooked_cod",
    "salmon": "cooked_salmon",
    "raw_salmon": "cooked_salmon",
    "potato": "baked_potato",
    "kelp": "dried_kelp",
}

KILL_DROPS = {
    "pig": frozenset({"porkchop", "cooked_porkchop"}),
    "cow": frozenset({"beef", "cooked_beef", "leather"}),
    "chicken": frozenset({"chicken", "cooked_chicken", "feather"}),
    "sheep": frozenset({"mutton", "cooked_mutton", "white_wool"}),
    "zombie": frozenset({"rotten_flesh", "iron_ingot", "carrot", "potato"}),
    "skeleton": frozenset({"bone", "arrow", "bow"}),
    "creeper": frozenset({"gunpowder"}),
    "spider": frozenset({"string", "spider_eye"}),
    "cod": frozenset({"cod", "cooked_cod"}),
    "salmon": frozenset({"salmon", "cooked_salmon"}),
    "fish": frozenset({"cod", "salmon", "cooked_cod", "cooked_salmon"}),
}

_WORD_NUM = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}

_STOP_PHRASE = re.compile(
    r"\s+(?:in|at|near|on|from|into|with|using)\s+.+$",
    re.IGNORECASE,
)
_HEAD_VERB = re.compile(
    r"^\s*(mine|craft|smelt|cook|obtain|collect|gather|eat|kill|equip|"
    r"chop|harvest|deposit|fill)\s+(.+?)\s*$",
    re.IGNORECASE,
)
# "Fill the bucket with water" → water_bucket (minecraft-data pc/1.19 items.json).
_FILL_WATER_BUCKET = re.compile(
    r"\bbucket\b.+\bwater\b|\bwater\b.+\bbucket\b",
    re.IGNORECASE,
)
_LEADING_COUNT = re.compile(
    r"^(?:(\d+)|(" + "|".join(_WORD_NUM.keys()) + r"))\s+(more\s+)?(.+)$",
    re.IGNORECASE,
)
_TO_OBTAIN = re.compile(
    r"(?:break|chop|harvest|destroy).+?\bto\s+obtain\s+(\d+)\s+(.+)$",
    re.IGNORECASE,
)
_BLOCK_OF = re.compile(r"^block\s+of\s+(.+)$", re.IGNORECASE)
_PAREN_OR = re.compile(r"\(([^)]+)\)")
# "into the chest at (57, 51, -206)"; spaces and negatives allowed.
_CHEST_AT_XYZ = re.compile(
    r"chest\s+at\s*\(\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*\)",
    re.IGNORECASE,
)
_XYZ_IN_PARENS = re.compile(
    r"\(\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*\)"
)

_MC_DATA_PC_119 = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "env",
    "mineflayer",
    "node_modules",
    "minecraft-data",
    "minecraft-data",
    "data",
    "pc",
    "1.19",
)

SHEET_SEED = 924  # 任务书「固定种子 0924」
SHEET_N = 60

# Original vanilla long runs (read-only). events/ files are per-round;
# metrics/iterations.jsonl is the task-level LLM critic row.
VANILLA_EVENT_RUNS = (
    (
        "1",
        "/mnt/e/homework/research/voyager/_audit_0911/v24_final/"
        "slac002_v26/ckpt_A_flash_off_t1_v26OLD",
    ),
    (
        "2",
        "/mnt/e/homework/research/voyager/_audit_0911/v24_final/"
        "slac002_v26/ckpt_A_flash_off_t2_v26OLD",
    ),
    (
        "3",
        "/mnt/e/homework/research/voyager/_audit_0911/v24_final/"
        "local/ckpt_A_flash_off_t3_v24OLD",
    ),
)

# EventRecorder.record: spaces and \\/:"*?<>| become underscores.
_EVENT_FILENAME_SANITIZE = re.compile(r'[\\/:"*?<>| ]')

LABEL_COLUMNS = ("world", "round", "task", "chat_tail", "inv_end")
PRED_COLUMNS = (
    "world",
    "round",
    "task",
    "chat_tail",
    "inv_end",
    "verify_status",
    "verify_success",
    "verify_critique",
)


@dataclass(frozen=True)
class ParsedTask:
    verb: str
    n: int
    targets: frozenset
    raw_item: str
    relative: bool = False


@dataclass(frozen=True)
class VerifyResult:
    success: bool
    critique: str
    verb: str
    n: int
    targets: frozenset
    source: str = "verifier"


_TOOL_ARMOR_SUFFIXES = (
    "_sword",
    "_pickaxe",
    "_axe",
    "_shovel",
    "_hoe",
    "_helmet",
    "_chestplate",
    "_leggings",
    "_boots",
)


def is_impossible_119_item(
    raw_item: str, targets: Iterable[str] = (), task: str = ""
) -> bool:
    """Fail-fast only for a SINGLE tool/armor id that is not in pc/1.19.

    Compound tasks ("A and B", commas) always return False so the caller falls back.
    """
    raw = str(raw_item or "")
    blob = f"{raw} {task or ''}".replace("_", " ")
    if re.search(r"\band\b|,", blob, re.I):
        return False
    if not raw:
        return False
    if known_targets_of(set(targets) | {raw}):
        return False
    if raw in mc_data_names():
        return False
    return any(raw.endswith(sfx) for sfx in _TOOL_ARMOR_SUFFIXES)


def format_verify_fail(
    *,
    task: str = "",
    verb: str,
    n: int,
    targets,
    have: int,
    executed_function: Optional[str] = None,
) -> str:
    names = sorted(targets or [])
    short = max(0, int(n) - int(have))
    lines = [
        f"verifier: {verb} hold>={n} of {names}; have={have} → fail",
        f"缺 {', '.join(names) or '?'}: 需要 {n}，现有 {have}，差 {short}",
    ]
    if executed_function:
        lines.append(f"本轮实际执行了 {executed_function}")
    if task:
        lines.append(f"task: {task}")
    return "\n".join(lines)


class VerifyFailStreak:
    """Same task + same fail key twice → third round falls back to the LLM critic."""

    def __init__(self):
        self.task = None
        self.key = None
        self.count = 0

    def should_fallback_llm(self, task: str, key: str) -> bool:
        return self.task == task and self.key == key and self.count >= 2

    def record_fail(self, task: str, key: str) -> None:
        if self.task == task and self.key == key:
            self.count += 1
        else:
            self.task = task
            self.key = key
            self.count = 1

    def reset(self) -> None:
        self.task = None
        self.key = None
        self.count = 0


class TaskAlignmentError(ValueError):
    """Event-file task name does not match the iterations.jsonl task window."""


def parse_inv_end(text: str) -> Dict[str, int]:
    """Parse TSV `dirt:1;jungle_log:1` into a dict. Empty → {}."""
    out: Dict[str, int] = {}
    if not text or not str(text).strip():
        return out
    for part in str(text).split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        name, _, count = part.partition(":")
        name = name.strip()
        try:
            n = int(str(count).strip())
        except ValueError:
            continue
        if name:
            out[name] = out.get(name, 0) + n
    return out


def _singularize(token: str) -> str:
    if token in {
        "planks",
        "stairs",
        "leggings",
        "boots",
        "shears",
        "beans",
        "leaves",
        "glasses",
        # Minecraft 1.19 pc blocks.json/items.json: block name is lapis_ore
        # (drop lapis_lazuli). "lapis" is a mineral name, not an English plural;
        # stripping the trailing s produced lapi_ore, which is not a game id.
        "lapis",
    }:
        return token
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("ses") or token.endswith("shes") or token.endswith("ches"):
        return token[:-2]
    if token.endswith("s") and not token.endswith("ss") and len(token) > 3:
        return token[:-1]
    return token


def _normalize_item_phrase(phrase: str) -> str:
    phrase = (phrase or "").strip().lower()
    phrase = phrase.replace("minecraft:", "")
    phrase = phrase.replace("-", " ")
    phrase = re.sub(r"[^a-z0-9_ ]+", " ", phrase)
    phrase = re.sub(r"\s+", " ", phrase).strip()
    if not phrase:
        return ""
    m = _BLOCK_OF.match(phrase)
    if m:
        inner = m.group(1).strip().replace(" ", "_")
        inner = _singularize(inner)
        return f"{inner}_block"
    words = [w for w in phrase.replace("_", " ").split() if w]
    # "diamond block" / "iron block" → diamond_block
    if len(words) >= 2 and words[-1] in {"block", "blocks"}:
        head = "_".join(_singularize(w) for w in words[:-1])
        return f"{head}_block"
    if words[-1] in {"ore", "log", "logs", "planks", "ingot", "ingots"}:
        words = [_singularize(w) if w not in {"planks"} else w for w in words]
        if words[-1] == "logs":
            words[-1] = "log"
        if words[-1] == "ingots":
            words[-1] = "ingot"
        return "_".join(words)
    words = [_singularize(w) for w in words]
    aliases = {
        "wood_pickaxe": "wooden_pickaxe",
        "wood_axe": "wooden_axe",
        "wood_sword": "wooden_sword",
        "wood_shovel": "wooden_shovel",
        "wood_hoe": "wooden_hoe",
        "wood_helmet": "wooden_helmet",
        "gold_pickaxe": "golden_pickaxe",
        "gold_axe": "golden_axe",
        "gold_sword": "golden_sword",
        "gold_shovel": "golden_shovel",
        "gold_hoe": "golden_hoe",
        "gold_helmet": "golden_helmet",
        "gold_chestplate": "golden_chestplate",
        "gold_leggings": "golden_leggings",
        "gold_boots": "golden_boots",
        "wood_log": "log",
        "wooden_log": "log",
        "wood": "log",
        "cocoa_bean": "cocoa_beans",
        "cocoa_beans": "cocoa_beans",
        "lapis": "lapis_lazuli",
        "stick": "stick",
        "sticks": "stick",
        "torch": "torch",
        "torches": "torch",
        "crafting_table": "crafting_table",
        "workbench": "crafting_table",
    }
    joined = "_".join(words)
    joined = aliases.get(joined, joined)
    if joined == "sticks":
        joined = "stick"
    if joined == "torches":
        joined = "torch"
    return joined


def _split_alternatives(item_text: str) -> Tuple[str, Set[str]]:
    extras: Set[str] = set()
    # Strip "into the chest at (x, y, z)" BEFORE reading parentheses, so
    # coordinates are not treated as item alternatives.
    text = _STOP_PHRASE.sub("", item_text or "").strip()
    m = _PAREN_OR.search(text)
    core = text
    if m:
        inner = m.group(1)
        core = (text[: m.start()] + " " + text[m.end() :]).strip()
        for piece in re.split(r"\s*(?:or|/|,)\s*", inner):
            piece = piece.strip()
            if piece:
                extras.add(_normalize_item_phrase(piece))
    core = _STOP_PHRASE.sub("", core).strip()
    return core, extras


def expand_targets(verb: str, item_id: str, extras: Iterable[str] = ()) -> frozenset:
    verb = (verb or "").lower()
    items: Set[str] = set()
    if item_id:
        items.add(item_id)
    for extra in extras:
        if extra:
            items.add(extra)

    # log / planks：任意木种
    if item_id in {"log", "wood_log", "wooden_log", "wood"}:
        items.update(WOOD_LOGS)
        items.discard("log")
        items.discard("wood_log")
        items.discard("wooden_log")
        items.discard("wood")
    if item_id in {"planks", "wood_planks", "wooden_planks"}:
        items.update(WOOD_PLANKS)

    # 具体木种：接受 stripped 变体
    if item_id.endswith("_log") and not item_id.startswith("stripped_"):
        items.add(f"stripped_{item_id}")
    if item_id.endswith("_stem") and not item_id.startswith("stripped_"):
        items.add(f"stripped_{item_id}")

    # deepslate 变体：deepslate_X_ore ↔ X_ore
    if item_id.startswith("deepslate_") and item_id.endswith("_ore"):
        base = item_id[len("deepslate_") :]
        items.add(base)
        # lapis lazuli naming
        if base == "lapis_lazuli_ore":
            items.add("lapis_ore")
        if base == "lapis_ore":
            items.add("lapis_lazuli_ore")
            items.add("deepslate_lapis_lazuli_ore")
    elif item_id.endswith("_ore") and not item_id.startswith("deepslate_"):
        items.add(f"deepslate_{item_id}")
        if item_id == "lapis_ore":
            items.add("lapis_lazuli_ore")
            items.add("deepslate_lapis_ore")
            items.add("deepslate_lapis_lazuli_ore")
        if item_id == "lapis_lazuli_ore":
            items.add("lapis_ore")
            items.add("deepslate_lapis_ore")

    # 矿 → 掉落物
    for ore, drop in ORE_TO_DROP.items():
        if ore in items or item_id == ore:
            items.add(drop)
            items.add(ore)

    # smelt/cook：看产物
    if verb in {"smelt", "cook"}:
        outputs = set()
        for name in list(items) + [item_id]:
            if name in SMELT_OUTPUT:
                outputs.add(SMELT_OUTPUT[name])
        if outputs:
            return frozenset(outputs)

    # block_of already handled in normalize; keep both X_block forms
    if item_id.endswith("_block"):
        items.add(item_id)

    items.discard("")
    return frozenset(items)


def _explicit_whitelist() -> Set[str]:
    names: Set[str] = set()
    names.update(ORE_TO_DROP.keys())
    names.update(ORE_TO_DROP.values())
    names.update(SMELT_OUTPUT.keys())
    names.update(SMELT_OUTPUT.values())
    names.update(WOOD_LOGS)
    names.update(WOOD_PLANKS)
    names.update(WOOD_SPECIES)
    names.update(KILL_DROPS.keys())
    for drops in KILL_DROPS.values():
        names.update(drops)
    names.update(
        {
            "log",
            "wood",
            "wood_log",
            "wooden_log",
            "planks",
            "wood_planks",
            "wooden_planks",
            "crafting_table",
            "workbench",
            "stick",
            "torch",
            "cocoa_beans",
            "lapis_lazuli",
        }
    )
    return names


@lru_cache(maxsize=1)
def mc_data_names() -> frozenset:
    """minecraft-data pc/1.19 items.json ∪ blocks.json names. No invented ids."""
    names: Set[str] = set()
    for filename in ("items.json", "blocks.json"):
        path = os.path.join(_MC_DATA_PC_119, filename)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            continue
        if isinstance(payload, list):
            for entry in payload:
                if isinstance(entry, dict) and entry.get("name"):
                    names.add(str(entry["name"]))
        elif isinstance(payload, dict):
            names.update(str(k) for k in payload.keys())
    names.discard("")
    names.discard("air")
    return frozenset(names)


@lru_cache(maxsize=1)
def known_item_names() -> frozenset:
    """Explicit mapping aliases ∪ minecraft-data 1.19 item/block names."""
    names = _explicit_whitelist()
    names.update(mc_data_names())
    names.discard("")
    names.discard("air")
    names.discard("wool")
    names.discard("fish")
    return frozenset(names)


def known_targets_of(targets: Iterable[str]) -> frozenset:
    """A hold/equip target is known only if it exists in minecraft-data 1.19."""
    known = mc_data_names()
    return frozenset(t for t in targets if t and t in known)


def parse_task(task: str) -> Optional[ParsedTask]:
    if not task or not str(task).strip():
        return None
    text = str(task).strip()

    obtain_m = _TO_OBTAIN.search(text)
    if obtain_m:
        n = int(obtain_m.group(1))
        raw = obtain_m.group(2).strip()
        core, extras = _split_alternatives(raw)
        item_id = _normalize_item_phrase(core)
        if not item_id:
            return None
        targets = expand_targets("obtain", item_id, extras)
        if not targets:
            return None
        return ParsedTask(
            verb="obtain", n=n, targets=targets, raw_item=item_id, relative=False
        )

    m = _HEAD_VERB.match(text)
    if not m:
        return None
    verb = m.group(1).lower()
    rest = m.group(2).strip()
    if verb in {"chop", "harvest"}:
        verb = "mine"
    if verb == "fill":
        # Hold ≥1 water_bucket; same end-inventory check as obtain (v3 §A).
        if _FILL_WATER_BUCKET.search(rest):
            return ParsedTask(
                verb="fill",
                n=1,
                targets=frozenset({"water_bucket"}),
                raw_item="water_bucket",
                relative=False,
            )
        return None

    n = 1
    item_text = rest
    relative = False
    cm = _LEADING_COUNT.match(rest)
    if cm:
        n = int(cm.group(1)) if cm.group(1) else _WORD_NUM[cm.group(2).lower()]
        relative = bool(cm.group(3))
        item_text = cm.group(4)
    elif verb not in {"equip"}:
        # mine/craft/... without a count: cannot parse
        return None

    core, extras = _split_alternatives(item_text)
    item_id = _normalize_item_phrase(core)
    if not item_id:
        return None
    if verb == "kill":
        # "2 fish (cod or salmon)" → extras already has cod/salmon; core may be fish
        targets = set(extras) if extras else set()
        mob = item_id
        # drop trailing location already stripped; mob names
        mob = mob.replace("_in_the_river", "").replace("_in_river", "")
        if mob:
            targets.add(mob)
            targets.update(KILL_DROPS.get(mob, ()))
        for extra in extras:
            targets.update(KILL_DROPS.get(extra, ()))
        targets.discard("")
        if not targets:
            return None
        return ParsedTask(
            verb="kill",
            n=n,
            targets=frozenset(targets),
            raw_item=item_id,
            relative=False,
        )

    targets = expand_targets(verb, item_id, extras)
    if not targets:
        return None
    return ParsedTask(
        verb=verb, n=n, targets=targets, raw_item=item_id, relative=relative
    )


def _count_targets(inventory: Mapping[str, int], targets: Iterable[str]) -> int:
    total = 0
    inv = inventory or {}
    for name in targets:
        try:
            total += int(inv.get(name, 0) or 0)
        except (TypeError, ValueError):
            continue
    return total


def _equipment_names(equipment) -> Set[str]:
    names: Set[str] = set()
    if not equipment:
        return names
    if isinstance(equipment, dict):
        values = equipment.values()
    elif isinstance(equipment, (list, tuple)):
        values = equipment
    else:
        return names
    for item in values:
        if not item:
            continue
        if isinstance(item, dict):
            name = item.get("name") or item.get("id") or ""
        else:
            name = str(item)
        name = str(name).replace("minecraft:", "").strip().lower()
        if name and name not in {"air", "none", "null", "empty"}:
            names.add(name)
    return names


def _one_equip_name(item) -> str:
    if not item:
        return ""
    if isinstance(item, dict):
        name = item.get("name") or item.get("id") or ""
    else:
        name = str(item)
    name = str(name).replace("minecraft:", "").strip().lower()
    if name in {"air", "none", "null", "empty"}:
        return ""
    return name


def _is_armor_name(name: str) -> bool:
    return any(str(name).endswith(s) for s in ARMOR_SUFFIXES)


def _is_weapon_tool_name(name: str) -> bool:
    return any(str(name).endswith(s) for s in WEAPON_TOOL_SUFFIXES)


def _equipment_by_slot(equipment) -> Optional[Tuple[Set[str], Set[str], Set[str]]]:
    """Split equipment into (armor, mainhand, offhand). None if slots unknown.

    status.js getEquipment order: slots 5-8 (helmet..boots), mainHand, offhand.
    """
    armor: Set[str] = set()
    main: Set[str] = set()
    off: Set[str] = set()
    if isinstance(equipment, dict):
        armor_keys = {
            "head",
            "helmet",
            "chest",
            "chestplate",
            "torso",
            "legs",
            "leggings",
            "feet",
            "boots",
            "armor",
        }
        main_keys = {"mainhand", "main_hand", "hand", "held"}
        off_keys = {"offhand", "off_hand", "shield"}
        for key, val in equipment.items():
            k = str(key).replace("minecraft:", "").strip().lower()
            name = _one_equip_name(val)
            if not name:
                continue
            if k in armor_keys or (isinstance(key, int) and 0 <= int(key) <= 3):
                armor.add(name)
            elif k in main_keys or key == 4:
                main.add(name)
            elif k in off_keys or key == 5:
                off.add(name)
            else:
                return None
        return armor, main, off
    if isinstance(equipment, (list, tuple)) and len(equipment) >= 6:
        for item in equipment[0:4]:
            name = _one_equip_name(item)
            if name:
                armor.add(name)
        name = _one_equip_name(equipment[4])
        if name:
            main.add(name)
        name = _one_equip_name(equipment[5])
        if name:
            off.add(name)
        return armor, main, off
    return None


def _kill_event_payload(event_type, event) -> str:
    payload = ""
    if event_type == "onSave" and isinstance(event, dict):
        payload = str(event.get("onSave") or "")
    elif event_type == "onChat" and isinstance(event, dict):
        payload = str(event.get("onChat") or "")
    elif isinstance(event, dict):
        payload = str(event.get("onSave") or event.get("onChat") or "")
    return payload


def _kill_event_count(events: Sequence, mobs: Iterable[str]) -> int:
    """Count this-round `bot.save('<mob>_killed')` events for the wanted mobs."""
    want = {str(m).lower() for m in mobs if m}
    n = 0
    for event_type, event in events or []:
        payload_l = _kill_event_payload(event_type, event).lower()
        if "_killed" not in payload_l:
            continue
        mob = payload_l.replace("_killed", "").replace("minecraft:", "").strip()
        if mob in want:
            n += 1
    return n


def _kill_mobs_wanted(parsed: ParsedTask) -> list:
    mobs = []
    if parsed.raw_item:
        mobs.append(parsed.raw_item)
    for t in parsed.targets:
        if t in KILL_DROPS:
            mobs.append(t)
    return mobs


def _parse_round_number(value) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def rounds_are_contiguous(prev_round, current_round) -> bool:
    """True iff current_round is exactly prev_round + 1 (both parse as int)."""
    prev_i = _parse_round_number(prev_round)
    cur_i = _parse_round_number(current_round)
    if prev_i is None or cur_i is None:
        return False
    return cur_i == prev_i + 1


def _event_pairs(events: Optional[Sequence]) -> List[Tuple]:
    pairs = []
    for item in events or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            pairs.append((item[0], item[1]))
        elif isinstance(item, dict):
            pairs.append(
                (item.get("type") or item.get("event_type") or "", item)
            )
    return pairs


_DID_RECIPE_RE = re.compile(
    r"i did the recipe for (\S+)(?:\s+\d+\s+times)?", re.IGNORECASE
)
_GIVE_ITEM_RE = re.compile(
    r"/give\s+@s\s+(?:minecraft:)?([a-z0-9_]+)", re.IGNORECASE
)


def _canon_item_name(name: str) -> str:
    return str(name or "").strip().lower().replace(" ", "_")


def _target_name_set(targets: Iterable[str]) -> set:
    names = set()
    for t in targets or []:
        if not t:
            continue
        names.add(_canon_item_name(t))
    names.discard("")
    return names


def _event_text_blob(event: dict) -> str:
    return " ".join(
        str(event.get(k) or "") for k in ("onChat", "onSave", "chat")
    )


def _give_of_targets(events: Optional[Sequence], names: Iterable[str]) -> bool:
    want = set(names)
    if not want:
        return False
    for _etype, event in _event_pairs(events):
        if not isinstance(event, dict):
            continue
        blob = _event_text_blob(event)
        for match in _GIVE_ITEM_RE.finditer(blob):
            if _canon_item_name(match.group(1)) in want:
                return True
    return False


def _craft_event_observed(events: Optional[Sequence], targets: Iterable[str]) -> bool:
    """True iff this round shows a real craft of the *same* target item.

    Accepts mineflayer 'I did the recipe for <item>' (after bot.craft) or a
    named-action craft contract bound to that item. Does not accept model/action
    chat such as 'I can make chest' (printed *before* craft). Env /give of the
    target is not a craft.
    """
    names = _target_name_set(targets)
    if not names:
        return False
    if _give_of_targets(events, names):
        return False
    for _etype, event in _event_pairs(events):
        if not isinstance(event, dict):
            continue
        blob = _event_text_blob(event)
        for match in _DID_RECIPE_RE.finditer(blob):
            if _canon_item_name(match.group(1)) in names:
                return True
        for rec in event.get("skillOutcomes") or []:
            if not isinstance(rec, dict):
                continue
            if rec.get("name") != "craft" or not rec.get("ok"):
                continue
            reason = str(rec.get("reason") or "").lower()
            if reason != "done":
                continue
            item = rec.get("item") or rec.get("target") or rec.get("arg") or ""
            if _canon_item_name(item) in names:
                return True
    return False


def _is_state_snapshot(event) -> bool:
    return isinstance(event, dict) and (
        "inventory" in event or "status" in event or "nearbyChests" in event
    )


def _snapshot_events(events: Optional[Sequence]) -> list:
    """Events that carry inventory / status / chests — not only type=observe.

    Vanilla event files typically have one trailing observe plus onChat
    snapshots that already include the same observation fields.
    """
    snaps = []
    for event_type, event in _event_pairs(events):
        if _is_state_snapshot(event):
            snaps.append((event_type, event))
    return snaps


def _task_key_from_text(task: str) -> str:
    """Same sanitizing EventRecorder uses for the event filename stem."""
    return _EVENT_FILENAME_SANITIZE.sub("_", task or "")


def _task_key_from_event_filename(path: str) -> str:
    base = os.path.basename(path)
    if base.endswith(".json"):
        base = base[:-5]
    return "_".join(base.split("_")[:-2])


def _xyz_tuple(match) -> Tuple[int, int, int]:
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _parse_task_chest_xyz(task: str) -> Optional[Tuple[int, int, int]]:
    m = _CHEST_AT_XYZ.search(task or "")
    if not m:
        return None
    return _xyz_tuple(m)


def _xyz_from_chest_key(key) -> Optional[Tuple[int, int, int]]:
    m = _XYZ_IN_PARENS.search(str(key))
    if not m:
        return None
    return _xyz_tuple(m)


def _chest_at_xyz(chests, xyz: Tuple[int, int, int]):
    """Value at (x, y, z), or None if that coordinate is absent."""
    if not isinstance(chests, Mapping) or xyz is None:
        return None
    canon = f"({xyz[0]}, {xyz[1]}, {xyz[2]})"
    if canon in chests:
        return chests[canon]
    for key, val in chests.items():
        if _xyz_from_chest_key(key) == xyz:
            return val
    return None


def _deposit_same_chest_delta(
    prev_chests, chests, targets, coord=None
) -> Optional[int]:
    """Target-item delta on chests countable as dict in both snapshots.

    If coord is (x, y, z), only that chest is considered. Returns None when
    it is missing or not a dict in either snapshot (Unknown/Invalid/absent).
    If coord is None, returns the best jointly-observed delta, or None.
    """
    if not isinstance(prev_chests, Mapping) or not isinstance(chests, Mapping):
        return None
    if coord is not None:
        after_items = _chest_at_xyz(chests, coord)
        before_items = _chest_at_xyz(prev_chests, coord)
        if not isinstance(after_items, Mapping) or not isinstance(
            before_items, Mapping
        ):
            return None
        return _count_targets(after_items, targets) - _count_targets(
            before_items, targets
        )
    best: Optional[int] = None
    for key, after_items in chests.items():
        if not isinstance(after_items, Mapping):
            continue
        before_items = prev_chests.get(key)
        if not isinstance(before_items, Mapping):
            continue
        delta = _count_targets(after_items, targets) - _count_targets(
            before_items, targets
        )
        if best is None or delta > best:
            best = delta
    return best


def verify_task_outcome(
    task: str,
    *,
    inventory: Optional[Mapping[str, int]] = None,
    prev_inventory: Optional[Mapping[str, int]] = None,
    hunger_before: Optional[float] = None,
    hunger_after: Optional[float] = None,
    equipment=None,
    events: Optional[Sequence] = None,
    inventory_missing: bool = False,
    chests=None,
    prev_chests=None,
    prev_round=None,
    current_round=None,
    task_start_inventory: Optional[Mapping[str, int]] = None,
    shared_fixes: bool = False,
    executed_function: Optional[str] = None,
) -> Tuple[Optional[VerifyResult], Optional[str]]:
    """Like verify_task, plus a fallback reason code when the result is None."""
    parsed = parse_task(task)
    if parsed is None:
        return None, "unparsed"
    verb, n, targets = parsed.verb, parsed.n, parsed.targets
    known_targets = known_targets_of(targets)
    if verb == "kill":
        if parsed.raw_item in KILL_DROPS:
            known_targets = frozenset(known_targets | {parsed.raw_item})
        known_targets = frozenset(
            known_targets | (set(targets) & set(KILL_DROPS.keys()))
        )
    if not known_targets:
        if shared_fixes and is_impossible_119_item(
            parsed.raw_item, parsed.targets, task=task
        ):
            critique = (
                f"verifier: {parsed.raw_item} 该物品在本版本不存在"
            )
            return (
                VerifyResult(
                    success=False,
                    critique=critique,
                    verb=verb,
                    n=n,
                    targets=parsed.targets,
                ),
                None,
            )
        return None, "unknown_target"
    targets = known_targets

    if (
        prev_inventory is not None
        and prev_round is not None
        and current_round is not None
        and not rounds_are_contiguous(prev_round, current_round)
    ):
        prev_inventory = None
        prev_chests = None

    if verb in HOLD_VERBS:
        return_item = (
            parsed.raw_item in RETURN_GIVE_ITEMS
            or parsed.raw_item == "workbench"
            or bool(set(targets) & RETURN_GIVE_ITEMS)
        )
        if return_item and not shared_fixes:
            return None, "env_return_items"
        if inventory_missing:
            return None, "hold_missing_inventory"
        inv = inventory or {}
        have = _count_targets(inv, targets)
        if return_item and shared_fixes:
            need_craft = (
                parsed.raw_item in ENV_CRAFT_GIVE_ITEMS
                or parsed.raw_item == "workbench"
                or bool(set(targets) & ENV_CRAFT_GIVE_ITEMS)
            )
            if need_craft:
                craft_names = set(targets)
                if parsed.raw_item:
                    raw = str(parsed.raw_item).lower().replace(" ", "_")
                    if raw == "workbench":
                        craft_names.add("crafting_table")
                    elif raw in {str(t).lower().replace(" ", "_") for t in targets}:
                        craft_names.add(raw)
                if not _craft_event_observed(events, craft_names):
                    return None, "env_return_items"
                ok = have >= n
                critique = (
                    f"verifier: {verb} craft-event hold>={n} of {sorted(targets)}; "
                    f"have={have} → {'success' if ok else 'fail'}"
                )
                if not ok:
                    critique = format_verify_fail(
                        task=task,
                        verb=verb,
                        n=n,
                        targets=targets,
                        have=have,
                        executed_function=executed_function,
                    )
                return (
                    VerifyResult(
                        success=ok, critique=critique, verb=verb, n=n, targets=targets
                    ),
                    None,
                )
            start_inv = (
                task_start_inventory
                if task_start_inventory is not None
                else prev_inventory
            )
            if start_inv is None:
                start_inv = {}
            start_have = _count_targets(start_inv, targets)
            delta = have - start_have
            if start_have >= n:
                ok = have >= n
            else:
                ok = delta >= n
            critique = (
                f"verifier: {verb} returnItems snapshot+delta "
                f">={n} of {sorted(targets)}; start={start_have} have={have} "
                f"delta={delta} → {'success' if ok else 'fail'}"
            )
            if not ok:
                critique = format_verify_fail(
                    task=task,
                    verb=verb,
                    n=n,
                    targets=targets,
                    have=have,
                    executed_function=executed_function,
                )
            return (
                VerifyResult(
                    success=ok, critique=critique, verb=verb, n=n, targets=targets
                ),
                None,
            )
        if parsed.relative:
            start_inv = (
                task_start_inventory
                if task_start_inventory is not None
                else prev_inventory
            )
            if start_inv is None:
                return None, "missing_task_start"
            start_have = _count_targets(start_inv, targets)
            delta = have - start_have
            ok = delta >= n
            critique = (
                f"verifier: {verb} +>={n} of {sorted(targets)} since task start; "
                f"start={start_have} have={have} delta={delta} → "
                f"{'success' if ok else 'fail'}"
            )
            return (
                VerifyResult(
                    success=ok, critique=critique, verb=verb, n=n, targets=targets
                ),
                None,
            )
        ok = have >= n
        critique = (
            f"verifier: {verb} hold>={n} of {sorted(targets)}; have={have} → "
            f"{'success' if ok else 'fail'}"
        )
        return (
            VerifyResult(
                success=ok, critique=critique, verb=verb, n=n, targets=targets
            ),
            None,
        )

    if verb == "deposit":
        # Named chest: success and fail both bind to that coordinate.
        # Fail only if it is a countable dict in both snapshots.
        # No coordinates: success still same-chest +≥ N; never fail.
        coord = _parse_task_chest_xyz(task)
        best_delta = _deposit_same_chest_delta(
            prev_chests, chests, targets, coord=coord
        )
        if best_delta is not None and best_delta >= n:
            critique = (
                f"verifier: deposit {sorted(targets)} same-chest "
                f"+{best_delta}>={n} → success"
            )
            return (
                VerifyResult(
                    success=True, critique=critique, verb=verb, n=n, targets=targets
                ),
                None,
            )
        if coord is not None and best_delta is not None:
            critique = (
                f"verifier: deposit {sorted(targets)} same-chest "
                f"+{best_delta}>={n} → fail"
            )
            return (
                VerifyResult(
                    success=False, critique=critique, verb=verb, n=n, targets=targets
                ),
                None,
            )
        return None, "deposit_no_evidence"

    if verb == "eat":
        if hunger_before is None or hunger_after is None:
            return None, "eat_missing_hunger"
        try:
            hb = float(hunger_before)
            ha = float(hunger_after)
        except (TypeError, ValueError):
            return None, "eat_missing_hunger"
        if hb >= FOOD_MAX:
            return None, "eat_hunger_full"
        ok = ha > hb
        return (
            VerifyResult(
                success=ok,
                critique=f"verifier: eat hunger {hunger_before}→{hunger_after} → "
                f"{'success' if ok else 'fail'}",
                verb=verb,
                n=n,
                targets=targets,
            ),
            None,
        )

    if verb == "kill":
        hits = _kill_event_count(events or [], _kill_mobs_wanted(parsed))
        if hits <= 0:
            return None, "kill_no_events"
        ok = hits >= n
        return (
            VerifyResult(
                success=ok,
                critique=f"verifier: kill events {hits}>={n} for {parsed.raw_item} → "
                f"{'success' if ok else 'fail'}",
                verb=verb,
                n=n,
                targets=targets,
            ),
            None,
        )

    if verb == "equip":
        slots = _equipment_by_slot(equipment)
        if slots is None:
            names = _equipment_names(equipment)
            if not names:
                return None, "equip_missing_equipment"
            return None, "equip_unslotted"
        armor, mainhand, offhand = slots
        if not (armor or mainhand or offhand):
            return None, "equip_missing_equipment"
        if any(_is_armor_name(t) for t in targets):
            pool = armor
            kind = "armor"
        elif any(_is_weapon_tool_name(t) for t in targets):
            pool = mainhand
            kind = "mainhand"
        else:
            pool = armor
            kind = "armor"
        ok = bool(pool & set(targets))
        return (
            VerifyResult(
                success=ok,
                critique=(
                    f"verifier: equip {sorted(targets)} in {kind} "
                    f"{sorted(pool)} → {'success' if ok else 'fail'}"
                ),
                verb=verb,
                n=n,
                targets=targets,
            ),
            None,
        )

    return None, "unparsed"


def verify_task(
    task: str,
    *,
    inventory: Optional[Mapping[str, int]] = None,
    prev_inventory: Optional[Mapping[str, int]] = None,
    hunger_before: Optional[float] = None,
    hunger_after: Optional[float] = None,
    equipment=None,
    events: Optional[Sequence] = None,
    inventory_missing: bool = False,
    chests=None,
    prev_chests=None,
    prev_round=None,
    current_round=None,
    task_start_inventory: Optional[Mapping[str, int]] = None,
    shared_fixes: bool = False,
    executed_function: Optional[str] = None,
) -> Optional[VerifyResult]:
    """Return a verdict or None (caller should fall back to the LLM critic).

    inventory_missing: inv snapshot absent (not empty bag) → uncertain → None
    for hold-type checks.
    """
    result, _reason = verify_task_outcome(
        task,
        inventory=inventory,
        prev_inventory=prev_inventory,
        hunger_before=hunger_before,
        hunger_after=hunger_after,
        equipment=equipment,
        events=events,
        inventory_missing=inventory_missing,
        chests=chests,
        prev_chests=prev_chests,
        prev_round=prev_round,
        current_round=current_round,
        task_start_inventory=task_start_inventory,
        shared_fixes=shared_fixes,
        executed_function=executed_function,
    )
    return result


def verify_from_events_outcome(
    task: str,
    events: Sequence,
    *,
    prev_round_obs=None,
    prev_round=None,
    current_round=None,
    task_start_inventory: Optional[Mapping[str, int]] = None,
    shared_fixes: bool = False,
    executed_function: Optional[str] = None,
) -> Tuple[Optional[VerifyResult], Optional[str]]:
    if not events:
        return None, "missing_events"
    snaps = _snapshot_events(events)
    n_snap = len(snaps)
    parsed = parse_task(task)
    if n_snap == 0:
        if parsed is not None and parsed.verb == "kill":
            return verify_task_outcome(task, events=events)
        return None, "missing_events"
    last_obs = snaps[-1][1]
    if n_snap < 2:
        if parsed is not None and parsed.verb == "eat":
            if prev_round_obs is None:
                return None, "eat_missing_hunger"
        if parsed is not None and parsed.verb == "kill":
            return verify_task_outcome(task, events=events)
    inventory = last_obs.get("inventory") if isinstance(last_obs, dict) else None
    hunger_before = hunger_after = None
    equipment = None
    chests = None
    prev_chests = None
    prev_inv = None
    prev_ok = isinstance(prev_round_obs, dict)
    if (
        prev_ok
        and prev_round is not None
        and current_round is not None
        and not rounds_are_contiguous(prev_round, current_round)
    ):
        prev_ok = False
    if prev_ok:
        status0 = prev_round_obs.get("status") or {}
        hunger_before = status0.get("food")
        prev_inv = prev_round_obs.get("inventory")
        prev_chests = prev_round_obs.get("nearbyChests")
    if isinstance(last_obs, dict):
        status = last_obs.get("status") or {}
        hunger_after = status.get("food")
        equipment = status.get("equipment")
        chests = last_obs.get("nearbyChests")
        if inventory is None:
            inventory = last_obs.get("inventory")
    if parsed is not None and parsed.verb == "eat" and not prev_ok:
        return None, "eat_missing_hunger"
    if (
        parsed is not None
        and parsed.verb == "deposit"
        and _parse_task_chest_xyz(task) is not None
        and not prev_ok
    ):
        return None, "deposit_no_evidence"
    return verify_task_outcome(
        task,
        inventory=inventory,
        prev_inventory=prev_inv,
        hunger_before=hunger_before,
        hunger_after=hunger_after,
        equipment=equipment,
        events=events,
        inventory_missing=inventory is None,
        chests=chests,
        prev_chests=prev_chests,
        prev_round=prev_round,
        current_round=current_round,
        task_start_inventory=task_start_inventory,
        shared_fixes=shared_fixes,
        executed_function=executed_function,
    )


def verify_from_events(
    task: str,
    events: Sequence,
    *,
    prev_round_obs=None,
    prev_round=None,
    current_round=None,
    task_start_inventory: Optional[Mapping[str, int]] = None,
    shared_fixes: bool = False,
    executed_function: Optional[str] = None,
) -> Optional[VerifyResult]:
    """Voyager rollout hook. Any exception → None (LLM fallback), with a log line."""
    try:
        result, _reason = verify_from_events_outcome(
            task,
            events,
            prev_round_obs=prev_round_obs,
            prev_round=prev_round,
            current_round=current_round,
            task_start_inventory=task_start_inventory,
            shared_fixes=shared_fixes,
            executed_function=executed_function,
        )
        return result
    except Exception as exc:
        print(f"[verify] error {type(exc).__name__}: {exc}", flush=True)
        return None


def _task_success_bool(value) -> Optional[bool]:
    text = str(value).strip().lower()
    if text in {"1", "true", "yes"}:
        return True
    if text in {"0", "false", "no"}:
        return False
    return None


def _stratified_indices(rows: Sequence[Mapping], n: int, seed: int) -> list:
    """World-stratified sample without replacement. Deterministic given seed."""
    by_world: Dict[str, list] = {}
    for i, row in enumerate(rows):
        world = str(row.get("world") or "")
        by_world.setdefault(world, []).append(i)
    worlds = sorted(by_world)
    total = len(rows)
    if total == 0 or n <= 0:
        return []
    n = min(n, total)
    rng = random.Random(seed)
    floor_n = {}
    remainders = []
    assigned = 0
    for world in worlds:
        exact = n * len(by_world[world]) / total
        base = int(exact)
        floor_n[world] = base
        assigned += base
        remainders.append((exact - base, world))
    leftover = n - assigned
    remainders.sort(key=lambda item: (-item[0], item[1]))
    for _, world in remainders:
        if leftover <= 0:
            break
        floor_n[world] += 1
        leftover -= 1
    chosen = []
    for world in worlds:
        pool = list(by_world[world])
        rng.shuffle(pool)
        take = min(floor_n[world], len(pool))
        chosen.extend(pool[:take])
    chosen.sort()
    return chosen


def replay_vanilla_tsv(path: str) -> dict:
    """Offline replay on the vanilla round table. No LLM.

    Does **not** treat the verifier's own hold-count as ground truth.
    Reports verifiable/fallback rates and agreement with the original LLM
    critic column `task_success` (descriptive only — not accuracy).

    Cross-round prev inventory is used only when the previous stored snapshot
    belongs to the immediately previous round of the same world. A missing
    snapshot breaks the chain (stale bags are not reused).
    """
    rows = []
    with open(path, encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    total = len(rows)
    verified = 0
    fallback = 0
    llm_agree = 0
    llm_disagree = 0
    llm_compared = 0
    fallback_by_reason: Counter = Counter()
    details = []
    # world -> {"round": int, "inv": dict, "task": str}
    prev_state_by_world: Dict[str, dict] = {}
    task_start_by_world: Dict[str, dict] = {}
    for row in rows:
        task = row.get("task") or ""
        inv_text = row.get("inv_end") or ""
        inv_present = bool(str(inv_text).strip())
        # TSV inv_used=0 with empty inv_end is an observed empty bag, not a
        # missing snapshot (status.inventoryUsed is a logged state field).
        inv_used_raw = str(row.get("inv_used") or "").strip()
        empty_observed = (not inv_present) and inv_used_raw == "0"
        inventory = parse_inv_end(inv_text) if inv_present else {}
        world = str(row.get("world") or "")
        current_round = row.get("round")
        prev = None
        prev_round = None
        state = prev_state_by_world.get(world)
        if state is not None:
            prev_round = state.get("round")
            if rounds_are_contiguous(prev_round, current_round):
                prev = state.get("inv")
            else:
                prev = None
                prev_round = None
        start_inv = None
        ts = task_start_by_world.get(world)
        if ts and ts.get("task") == task:
            start_inv = ts.get("inv")
        elif prev is not None:
            start_inv = prev
            task_start_by_world[world] = {"task": task, "inv": prev}
        else:
            task_start_by_world.pop(world, None)
        result, reason = verify_task_outcome(
            task,
            inventory=inventory,
            prev_inventory=prev,
            inventory_missing=not inv_present and not empty_observed,
            prev_round=prev_round,
            current_round=current_round,
            task_start_inventory=start_inv,
        )
        if result is None:
            fallback += 1
            status = "fallback"
            code = reason or "unparsed"
            parsed_fb = parse_task(task)
            if (
                prev is None
                and state is not None
                and not rounds_are_contiguous(state.get("round"), current_round)
                and parsed_fb is not None
                and parsed_fb.verb == "deposit"
            ):
                code = "discontinuous_prev"
            fallback_by_reason[code] += 1
            fallback_reason = code
        else:
            verified += 1
            status = "verified"
            fallback_reason = None
            llm = _task_success_bool(row.get("task_success"))
            if llm is not None:
                llm_compared += 1
                if bool(result.success) == llm:
                    llm_agree += 1
                else:
                    llm_disagree += 1
        details.append(
            {
                "world": world,
                "round": row.get("round"),
                "task": task,
                "status": status,
                "success": None if result is None else result.success,
                "critique": None if result is None else result.critique,
                "chat_tail": row.get("chat_tail") or "",
                "inv_end": inv_text,
                "task_success": row.get("task_success"),
                "fallback_reason": fallback_reason,
            }
        )
        if inv_present or empty_observed:
            prev_state_by_world[world] = {
                "round": _parse_round_number(current_round),
                "inv": inventory,
            }
        else:
            # Missing snapshot: do not keep a stale bag for later rounds.
            prev_state_by_world.pop(world, None)
            task_start_by_world.pop(world, None)
    llm_agree_rate = (llm_agree / llm_compared) if llm_compared else 0.0
    return {
        "total": total,
        "verified": verified,
        "fallback": fallback,
        "verifiable_rate": (verified / total) if total else 0.0,
        "fallback_rate": (fallback / total) if total else 0.0,
        "fallback_by_reason": dict(fallback_by_reason),
        "llm_agree": llm_agree,
        "llm_disagree": llm_disagree,
        "llm_compared": llm_compared,
        "llm_agree_rate": llm_agree_rate,
        "details": details,
        "rows": rows,
    }


def _event_file_timestamp(name: str) -> float:
    """Sort key matching EventRecorder.resume (trailing _YYYYMMDD_HHMMSS)."""
    base = name[:-5] if name.endswith(".json") else name
    stamp = "_".join(base.split("_")[-2:])
    try:
        return time.mktime(time.strptime(stamp, "%Y%m%d_%H%M%S"))
    except (ValueError, OverflowError):
        return 0.0


def list_event_files(ckpt_dir: str) -> List[str]:
    ev_dir = os.path.join(ckpt_dir, "events")
    if not os.path.isdir(ev_dir):
        return []
    names = [
        name
        for name in os.listdir(ev_dir)
        if os.path.isfile(os.path.join(ev_dir, name))
    ]
    names.sort(key=_event_file_timestamp)
    return [os.path.join(ev_dir, name) for name in names]


def load_iteration_rows(ckpt_dir: str) -> list:
    path = os.path.join(ckpt_dir, "metrics", "iterations.jsonl")
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def _task_windows_from_iterations(iter_rows: Sequence[Mapping]) -> dict:
    """Map 1-based event index → {task, llm_success or None}.

    iterations.jsonl is one row per finished task; `iter` is the recorder
    iteration of the last step. Intermediate retries share the task text;
    LLM success is only compared on the task-ending index.

    n_steps=0 (or missing/non-positive) does not occupy a round: those rows
    are skipped so they cannot overwrite a real window.
    """
    assigned: Dict[int, dict] = {}
    for row in iter_rows:
        try:
            it = int(row.get("iter"))
        except (TypeError, ValueError):
            continue
        n_steps = int(row.get("n_steps") or 0)
        if n_steps <= 0:
            continue
        start = it - n_steps + 1
        if start < 1:
            start = 1
        task = row.get("task") or ""
        llm = row.get("success")
        for i in range(start, it + 1):
            assigned[i] = {
                "task": task,
                "llm_success": llm if i == it else None,
                "is_final": i == it,
            }
    return assigned


def replay_event_dir(
    ckpt_dir: str,
    world: str,
    *,
    max_rounds: Optional[int] = None,
) -> dict:
    """Replay one vanilla ckpt's events/*. No LLM.

    max_rounds: if set, only the first N event files (by timestamp).
    """
    files = list_event_files(ckpt_dir)
    if max_rounds is not None:
        files = files[: max(0, int(max_rounds))]
    iter_rows = load_iteration_rows(ckpt_dir)
    assigned = _task_windows_from_iterations(iter_rows)
    verified = 0
    fallback = 0
    llm_agree = 0
    llm_disagree = 0
    llm_compared = 0
    fallback_by_reason: Counter = Counter()
    details = []
    prev_round_index = None
    prev_obs = None
    task_start_name = None
    task_start_inv = None
    for idx, path in enumerate(files, start=1):
        try:
            with open(path, encoding="utf-8") as handle:
                events = json.load(handle)
        except (OSError, ValueError):
            events = []
        if not isinstance(events, list):
            events = []
        meta = assigned.get(idx) or {}
        task = meta.get("task") or ""
        file_key = _task_key_from_event_filename(path)
        assigned_key = _task_key_from_text(task)
        if assigned_key and file_key and assigned_key != file_key:
            raise TaskAlignmentError(
                "event/task mismatch world={} round={} event_file={!r} "
                "iter_task={!r} file_task={!r}".format(
                    world, idx, os.path.basename(path), task, file_key
                )
            )
        if not task:
            if not file_key:
                task = ""
            else:
                # Filename stem only when iterations.jsonl has no row.
                task = file_key.replace("_", " ")
        if task != task_start_name:
            task_start_name = task
            task_start_inv = None
            if isinstance(prev_obs, dict):
                task_start_inv = prev_obs.get("inventory")
        result, reason = verify_from_events_outcome(
            task,
            events,
            prev_round_obs=prev_obs,
            prev_round=prev_round_index,
            current_round=idx,
            task_start_inventory=task_start_inv,
        )
        if result is None:
            fallback += 1
            status = "fallback"
            fallback_by_reason[reason or "unparsed"] += 1
        else:
            verified += 1
            status = "verified"
            llm = meta.get("llm_success")
            if isinstance(llm, bool):
                llm_compared += 1
                if bool(result.success) == llm:
                    llm_agree += 1
                else:
                    llm_disagree += 1
        details.append(
            {
                "world": world,
                "round": idx,
                "task": task,
                "status": status,
                "success": None if result is None else result.success,
                "critique": None if result is None else result.critique,
                "fallback_reason": None if result is not None else (
                    reason or "unparsed"
                ),
                "is_final": meta.get("is_final"),
                "event_file": os.path.basename(path),
            }
        )
        snaps = _snapshot_events(events)
        if snaps:
            prev_obs = snaps[-1][1]
            prev_round_index = idx
        else:
            prev_obs = None
            prev_round_index = None
    total = len(files)
    llm_agree_rate = (llm_agree / llm_compared) if llm_compared else 0.0
    return {
        "world": world,
        "ckpt_dir": ckpt_dir,
        "total": total,
        "verified": verified,
        "fallback": fallback,
        "verifiable_rate": (verified / total) if total else 0.0,
        "fallback_rate": (fallback / total) if total else 0.0,
        "fallback_by_reason": dict(fallback_by_reason),
        "llm_agree": llm_agree,
        "llm_disagree": llm_disagree,
        "llm_compared": llm_compared,
        "llm_agree_rate": llm_agree_rate,
        "details": details,
        "_prev_round_index": prev_round_index,
    }


def replay_event_runs(
    runs: Sequence[Tuple[str, str]] = VANILLA_EVENT_RUNS,
    *,
    max_rounds_per_world: Optional[int] = None,
) -> dict:
    """Replay several vanilla event directories. Read-only on ckpt paths."""
    combined_reason: Counter = Counter()
    details = []
    verified = fallback = 0
    llm_agree = llm_disagree = llm_compared = 0
    per_world = []
    for world, ckpt_dir in runs:
        one = replay_event_dir(
            ckpt_dir, world, max_rounds=max_rounds_per_world
        )
        per_world.append(
            {
                "world": world,
                "ckpt_dir": ckpt_dir,
                "total": one["total"],
                "verified": one["verified"],
                "fallback": one["fallback"],
                "llm_agree": one["llm_agree"],
                "llm_compared": one["llm_compared"],
            }
        )
        verified += one["verified"]
        fallback += one["fallback"]
        llm_agree += one["llm_agree"]
        llm_disagree += one["llm_disagree"]
        llm_compared += one["llm_compared"]
        combined_reason.update(one.get("fallback_by_reason") or {})
        details.extend(one.get("details") or [])
    total = verified + fallback
    llm_agree_rate = (llm_agree / llm_compared) if llm_compared else 0.0
    return {
        "total": total,
        "verified": verified,
        "fallback": fallback,
        "verifiable_rate": (verified / total) if total else 0.0,
        "fallback_rate": (fallback / total) if total else 0.0,
        "fallback_by_reason": dict(combined_reason),
        "llm_agree": llm_agree,
        "llm_disagree": llm_disagree,
        "llm_compared": llm_compared,
        "llm_agree_rate": llm_agree_rate,
        "details": details,
        "per_world": per_world,
        "max_rounds_per_world": max_rounds_per_world,
        "source": "original_events",
    }


def format_replay_stats(stats: Mapping) -> str:
    """Human-readable replay stats. llm_agree_rate is not accuracy."""
    total = int(stats.get("total") or 0)
    verified = int(stats.get("verified") or 0)
    fallback = int(stats.get("fallback") or 0)
    llm_agree = int(stats.get("llm_agree") or 0)
    llm_disagree = int(stats.get("llm_disagree") or 0)
    llm_compared = int(stats.get("llm_compared") or 0)
    source = str(stats.get("source") or "tsv")
    title = (
        "vanilla original-events replay (no LLM)"
        if source == "original_events"
        else "vanilla TSV replay (no LLM)"
    )
    lines = [
        title,
        f"total={total}",
        f"verified={verified}",
        f"fallback={fallback}",
        "verifiable_rate={:.4f}  ({}/{})".format(
            (verified / total) if total else 0.0, verified, total
        ),
        "fallback_rate={:.4f}  ({}/{})".format(
            (fallback / total) if total else 0.0, fallback, total
        ),
    ]
    reasons = stats.get("fallback_by_reason") or {}
    if reasons:
        lines.append("fallback_by_reason:")
        for key in sorted(reasons):
            count = int(reasons[key])
            rate = (count / total) if total else 0.0
            lines.append(f"  {key}={count}  ({rate:.4f})")
    lines.extend(
        [
            f"llm_agree={llm_agree}",
            f"llm_disagree={llm_disagree}",
            f"llm_compared={llm_compared}",
            "llm_agree_rate={:.4f}  ({}/{})".format(
                (llm_agree / llm_compared) if llm_compared else 0.0,
                llm_agree,
                llm_compared,
            ),
        ]
    )
    if source == "original_events":
        lines.append(
            "note: llm_agree_rate is agreement with iterations.jsonl "
            "success on task-ending rounds; it is NOT verifier accuracy."
        )
    else:
        lines.append(
            "note: llm_agree_rate is agreement with the original LLM critic "
            "column task_success; it is NOT verifier accuracy."
        )
    lines.append(
        "note: fallback_rate is reported as measured; judgments were not "
        "loosened to push it under 20%."
    )
    if stats.get("max_rounds_per_world") is not None:
        lines.append(
            f"note: truncated to first {stats['max_rounds_per_world']} "
            "rounds per world."
        )
    return "\n".join(lines) + "\n"


def load_codex_labels(path: str) -> list:
    """Read the TSV fence in impl/label/codex_labels.md.

    Each row: world, round, label (1/0/U), reason.
    """
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    match = re.search(r"```tsv\s*\n(.*?)```", text, re.DOTALL)
    if not match:
        raise ValueError(f"no ```tsv fence in {path}")
    return list(
        csv.DictReader(match.group(1).strip().splitlines(), delimiter="\t")
    )


def write_verify_sheets(
    tsv_path: str,
    label_path: str,
    pred_path: str,
    n: int = SHEET_N,
    seed: int = SHEET_SEED,
    overwrite_label: bool = False,
) -> dict:
    """Draw a world-stratified 60-row sheet. Label file has no verdicts.

    overwrite_label=False (default): do not rewrite an existing label sheet
    so row order stays frozen; still regenerate predictions with the same seed.
    """
    stats = replay_vanilla_tsv(tsv_path)
    rows = stats["rows"]
    indices = _stratified_indices(rows, n, seed)
    label_rows = []
    pred_rows = []
    details = stats["details"]
    for i in indices:
        row = rows[i]
        detail = details[i]
        label_rows.append({k: row.get(k) or "" for k in LABEL_COLUMNS})
        success = detail["success"]
        pred_rows.append(
            {
                "world": row.get("world") or "",
                "round": row.get("round") or "",
                "task": row.get("task") or "",
                "chat_tail": row.get("chat_tail") or "",
                "inv_end": row.get("inv_end") or "",
                "verify_status": detail["status"],
                "verify_success": "" if success is None else str(bool(success)),
                "verify_critique": detail["critique"] or "",
            }
        )
    os.makedirs(os.path.dirname(os.path.abspath(label_path)) or ".", exist_ok=True)
    write_label = overwrite_label or not os.path.isfile(label_path)
    if write_label:
        with open(label_path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=list(LABEL_COLUMNS),
                delimiter="\t",
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(label_rows)
    with open(pred_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(PRED_COLUMNS), delimiter="\t", extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(pred_rows)
    stats["sheet_n"] = len(indices)
    stats["sheet_seed"] = seed
    stats["label_path"] = label_path
    stats["pred_path"] = pred_path
    stats["sheet_worlds"] = sorted({(rows[i].get("world") or "") for i in indices})
    stats["label_written"] = write_label
    return stats
