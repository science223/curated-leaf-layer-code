"""Zero-shot noise-fix helpers (harness layer). All flags default off.

A: Place subgoals judged only from a place event: onSave `{block}_placed`
(the placeItem primitive success). Nearby-block "absent then present" is
not success (walking up to an existing table). No events → None (caller
falls back). Chat alone does not count.
C: Mine/Craft/Smelt/Collect judged by inventory increment via voyager.verify.
"""
from __future__ import annotations

import json
import os
import re
from typing import Mapping, Optional, Sequence, Tuple

from voyager.verify import (
    HOLD_VERBS,
    ORE_TO_DROP,
    SMELT_OUTPUT,
    WOOD_LOGS,
    _count_targets,
    parse_task,
)

DEFAULT_SUBGOAL_CAP = 8
# First /give + this many retries after a failed delta check.
GIVE_VERIFY_RETRIES = 2
ZS_WORLD_SEED = 20260902099
# level.dat SpawnX/Y/Z of a fresh world with this seed (zs_existing_v41, 1.19).
ZS_SPAWN = {"x": -368, "y": 73, "z": -16}
ZS_FIXED_TIME = 1000

_PLACE_RE = re.compile(r"^\s*place\s+(?:\d+\s+)?([a-z0-9_]+)\s*$", re.IGNORECASE)

_CANONICAL_REL = os.path.join("_cc0924", "zs_canonical_subgoals.json")


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def canonical_path():
    return os.path.join(_repo_root(), _CANONICAL_REL)


def noise_flags_from_args(args) -> dict:
    master = bool(getattr(args, "zs_noise_fix", False))

    def _on(name):
        return master or bool(getattr(args, name, False))

    return {
        "place_judge": _on("zs_place_judge"),
        "count_judge": _on("zs_count_judge"),
        "canonical_subgoals": _on("zs_canonical_subgoals"),
        "subgoal_cap": _on("zs_subgoal_cap_on"),
        "fixed_start": _on("zs_fixed_start"),
    }


def any_noise_flag(flags: Optional[Mapping] = None) -> bool:
    return bool(flags) and any(bool(v) for v in flags.values())


def result_extra_fields(
    enabled: bool,
    *,
    stuck_segments=None,
    subgoal_results=None,
    flags=None,
) -> dict:
    if not enabled:
        return {}
    out = {
        "zs_noise_fix": True,
        "zs_noise_flags": dict(flags or {}),
        "stuck_segments": list(stuck_segments or []),
    }
    if subgoal_results is not None:
        out["subgoal_results"] = subgoal_results
    return out


def parse_place(subgoal: str) -> Optional[str]:
    m = _PLACE_RE.match(str(subgoal or "").strip())
    if not m:
        return None
    return m.group(1).lower().replace("minecraft:", "")


def _event_pairs(events: Optional[Sequence]):
    out = []
    for item in events or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            out.append((item[0], item[1] if isinstance(item[1], dict) else {}))
    return out


def _voxel_names(ev) -> Optional[set]:
    """Nearby-block names from a real observation, or None if voxels are missing.

    Empty list is a real observation (nothing nearby). Missing/None is not empty.
    """
    if not isinstance(ev, dict) or "voxels" not in ev:
        return None
    vox = ev.get("voxels")
    if vox is None:
        return None
    if isinstance(vox, dict):
        vox = list(vox)
    return {str(v).replace("minecraft:", "") for v in vox}


def _is_place_success_event(_et, ev, block: str) -> bool:
    """placeItem success = onSave `{block}_placed` (bot.save(`${name}_placed`))."""
    if not isinstance(ev, dict):
        return False
    name = str(ev.get("onSave") or "")
    save_name = f"{block}_placed"
    if name == save_name or (
        name.endswith("_placed") and name[: -len("_placed")] == block
    ):
        return True
    return False


def judge_place(subgoal: str, events: Optional[Sequence]) -> Optional[bool]:
    """Hard-evidence place check. True / False / None (cannot judge → caller fallback).

    Success: this subgoal's events include onSave `{block}_placed` (placeItem
    primitive success). Nearby-block "absent then present" is not success.
    Chat text is ignored. No events → None (fall back to the original critic).
    """
    block = parse_place(subgoal)
    if not block:
        return None
    pairs = _event_pairs(events)
    if not pairs:
        return None
    for et, ev in pairs:
        if _is_place_success_event(et, ev, block):
            return True
    return False


def _last_inventory(events: Optional[Sequence]) -> Optional[dict]:
    inv = None
    for _et, ev in _event_pairs(events):
        if isinstance(ev, dict) and "inventory" in ev:
            inv = ev.get("inventory") or {}
    return inv


def judge_count(
    subgoal: str,
    events: Optional[Sequence],
    start_inventory: Optional[Mapping[str, int]] = None,
) -> Optional[bool]:
    parsed = parse_task(subgoal)
    if parsed is None or parsed.verb not in HOLD_VERBS:
        return None
    inv = _last_inventory(events)
    if inv is None:
        return None
    have = _count_targets(inv, parsed.targets)
    start = _count_targets(start_inventory or {}, parsed.targets)
    delta = have - start
    # 子目标第一次开始时的背包是 start_inventory。跨尝试累计看增量；
    # 「已经够了」按当前持有 ≥ N 判（审查 8 条假失败：already have / start1-end8）。
    # 增量 ≥ N 在 start≥0 时被 own-N 包含；两者都写上以免口径再拧。
    return have >= parsed.n or delta >= parsed.n


def judge_subgoal(
    subgoal: str,
    events: Optional[Sequence],
    start_inventory: Optional[Mapping[str, int]] = None,
    flags: Optional[Mapping] = None,
) -> Optional[bool]:
    flags = flags or {}
    if flags.get("place_judge"):
        placed = judge_place(subgoal, events)
        if placed is not None:
            return placed
    if flags.get("count_judge"):
        return judge_count(subgoal, events, start_inventory)
    return None


def product_for_subgoal(subgoal: str) -> Optional[Tuple[str, int]]:
    block = parse_place(subgoal)
    if block:
        return block, 1
    parsed = parse_task(subgoal)
    if parsed is None:
        return None
    n = int(parsed.n)
    targets = set(parsed.targets or ())
    if parsed.verb in {"smelt", "cook"}:
        outs = sorted(t for t in targets if t in SMELT_OUTPUT.values())
        if outs:
            return outs[0], n
        raw = str(parsed.raw_item or "")
        if raw in SMELT_OUTPUT:
            return SMELT_OUTPUT[raw], n
        if targets:
            return sorted(targets)[0], n
        return None
    drops = sorted(t for t in targets if t in ORE_TO_DROP.values())
    if drops:
        return drops[0], n
    raw = str(parsed.raw_item or "")
    if raw in {"log", "wood", "wood_log", "wooden_log"}:
        return "oak_log", n
    if raw in {"planks", "wood_planks", "wooden_planks"}:
        return "oak_planks", n
    if raw in {"stick", "sticks"}:
        return "stick", n
    concrete = sorted(
        t
        for t in targets
        if t not in {"log", "wood", "planks"} and t not in WOOD_LOGS
    )
    if raw and raw not in {"log", "wood", "planks"}:
        return raw, n
    if concrete:
        return concrete[0], n
    if targets:
        return sorted(targets)[0], n
    return None


def load_canonical_file(path: Optional[str] = None) -> dict:
    path = path or canonical_path()
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def canonical_subgoals_for(task: str, data: Optional[dict] = None) -> list:
    data = data if data is not None else load_canonical_file()
    tasks = data.get("tasks") or {}
    entry = tasks.get(task)
    if entry is None:
        for _key, val in tasks.items():
            if isinstance(val, dict) and val.get("target") and val["target"] in task.replace(" ", "_"):
                entry = val
                break
            if _key.lower() == (task or "").lower():
                entry = val
                break
    if not isinstance(entry, dict):
        return []
    return [str(s).strip() for s in (entry.get("subgoals") or []) if str(s).strip()]


def give_js(item: str, n: int) -> str:
    item = str(item).replace("minecraft:", "")
    n = int(n)
    return (
        f'await bot.chat("/give @s minecraft:{item} {n}");\n'
        "await bot.waitForTicks(12);\n"
        "await bot.waitForTicks(40);\n"
    )


def fixed_start_js(spawn: Optional[Mapping] = None, time: int = ZS_FIXED_TIME) -> str:
    spawn = spawn or ZS_SPAWN
    lines = [
        f'await bot.chat("/time set {int(time)}"); await bot.waitForTicks(12);',
        'await bot.chat("/weather clear"); await bot.waitForTicks(12);',
        'await bot.chat("/gamerule doWeatherCycle false"); await bot.waitForTicks(12);',
        (
            f'await bot.chat("/tp @s {int(spawn["x"])} {int(spawn["y"])} '
            f'{int(spawn["z"])}"); await bot.waitForTicks(12);'
        ),
        "await bot.waitForTicks(20);",
    ]
    return "\n".join(lines)


def record_injection(injected, item, n):
    """Add n units of item to the harness /give ledger. Mutates injected."""
    if injected is None or not item:
        return injected
    key = str(item).replace("minecraft:", "")
    injected[key] = int(injected.get(key, 0) or 0) + int(n)
    return injected


def injected_count(injected, item):
    if not item:
        return 0
    key = str(item).replace("minecraft:", "")
    try:
        return int((injected or {}).get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def earned_count(have, injected, target):
    """Current holdings minus harness-injected copies of the final target."""
    try:
        have_n = int(have or 0)
    except (TypeError, ValueError):
        have_n = 0
    return max(0, have_n - injected_count(injected, target))


def final_target_ok(have, injected, target):
    """Whole-task success: deduct injected copies; any injected target item fails.

    最终目标物品只要有一件是注入的就不算成功（非末项产物恰为最终目标同样处理）。
    """
    if injected_count(injected, target) > 0:
        return False
    try:
        return int(have or 0) > 0
    except (TypeError, ValueError):
        return False


def placed_blocks_from_events(events: Optional[Sequence]) -> set:
    """Block names placed this attempt: onSave `{name}_placed`."""
    out = set()
    for _et, ev in _event_pairs(events):
        if not isinstance(ev, dict):
            continue
        name = str(ev.get("onSave") or "")
        if name.endswith("_placed"):
            out.add(name[: -len("_placed")])
    return out


def skip_reason(
    subgoal: str,
    events: Optional[Sequence],
    injected: Optional[Mapping] = None,
    *,
    flags: Optional[Mapping] = None,
    placed_this_attempt: Optional[set] = None,
):
    """Rule A: skip a subgoal with 0 rounds, or None to occupy a round.

    earned: current bag minus /give already meets N.
    give_cascade: have ≥ N only because of harness inject.
    place_already: have the block, it is nearby, and this attempt placed it.
    """
    flags = flags or {}
    if not (flags.get("count_judge") or flags.get("place_judge")):
        return None
    block = parse_place(subgoal)
    if block and flags.get("place_judge"):
        inv = _last_inventory(events) or {}
        have = int(inv.get(block, 0) or 0)
        nearby = False
        for _et, ev in _event_pairs(events):
            names = _voxel_names(ev)
            if names is not None and block in names:
                nearby = True
        placed = block in (placed_this_attempt or set())
        if have >= 1 and nearby and placed:
            return "place_already"
        return None
    if not flags.get("count_judge"):
        return None
    parsed = parse_task(subgoal)
    if parsed is None or parsed.verb not in HOLD_VERBS:
        return None
    inv = _last_inventory(events) or {}
    have = _count_targets(inv, parsed.targets)
    inj = 0
    for t in parsed.targets or ():
        inj = max(inj, injected_count(injected, t))
    earned = max(0, have - inj)
    if earned >= int(parsed.n):
        return "earned"
    if have >= int(parsed.n):
        return "give_cascade"
    return None


def plan_prompt(task, subgoals, idx, *, named=False, target=None) -> str:
    """Both arms: final goal + current + remaining + one program may finish many steps."""
    subgoals = [str(s) for s in (subgoals or [])]
    if 0 <= int(idx) < len(subgoals):
        current = subgoals[int(idx)]
        remaining = subgoals[int(idx) + 1 :]
    else:
        current = str(task or "")
        remaining = []
    rem = ", ".join(remaining) if remaining else "(none)"
    lines = [
        f"Final goal: {task}",
        f"Current step: {current}",
        f"Remaining steps: {rem}",
        "You may complete multiple remaining steps in one program.",
    ]
    if named:
        item = str(target or "item").replace("minecraft:", "")
        lines.append(
            f'Prefer obtain(bot, "{item}", 1) for the final goal. '
            "If unfinished, call obtain again or handle the reported shortage."
        )
    return "\n".join(lines)


def clear_field_js() -> str:
    """Ground drops + nearby chest contents. Used after a hard reset (Rule A)."""
    return """
await bot.chat("/kill @e[type=item]");
await bot.waitForTicks(12);
try {
    const ids = [];
    if (mcData.blocksByName.chest) ids.push(mcData.blocksByName.chest.id);
    if (mcData.blocksByName.trapped_chest) ids.push(mcData.blocksByName.trapped_chest.id);
    const spots = ids.length ? bot.findBlocks({ matching: ids, maxDistance: 32, count: 16 }) : [];
    for (const pos of spots) {
        const blk = bot.blockAt(pos);
        if (!blk) continue;
        try {
            const chest = await bot.openChest(blk);
            for (const it of chest.containerItems()) {
                try { await chest.toss(it.type, null, it.count); } catch (err) {}
            }
            chest.close();
        } catch (err) {}
    }
} catch (err) {}
await bot.waitForTicks(10);
""".strip()


def make_stuck_segment(
    *,
    subgoal_index: int,
    subgoal: str,
    start_round: int,
    end_round: int,
    give_item: Optional[str] = None,
    give_count: Optional[int] = None,
    give_ok: bool = False,
) -> dict:
    return {
        "subgoal_index": int(subgoal_index),
        "subgoal": subgoal,
        "start_round": int(start_round),
        "end_round": int(end_round),
        "rounds": int(end_round) - int(start_round),
        "give_item": give_item,
        "give_count": give_count,
        "give_ok": bool(give_ok),
    }
