"""N1 anti-stall: window trigger + wood-fail trigger, typed recovery."""
from __future__ import annotations

import json
import os
import re
from typing import Dict, Iterable, List, Optional

WOOD_RE = re.compile(
    r"(wood|log|plank|stick|crafting\s*table)",
    re.IGNORECASE,
)
CHEST_RE = re.compile(
    r"(chest|deposit|discard|junk|inventory|storejunk)",
    re.IGNORECASE,
)
MINE_WOOD_RE = re.compile(
    r"^\s*(mine|collect|gather|chop|obtain|harvest).*(wood|log)",
    re.IGNORECASE,
)

WINDOW = 15
WINDOW_FAILS = 15
WOOD_FAILS = 8
COOLDOWN = 20


def is_wood_task(task: str) -> bool:
    return bool(MINE_WOOD_RE.search(task or ""))


def is_chest_task(task: str) -> bool:
    return bool(CHEST_RE.search(task or ""))


class AntiStall:
    def __init__(self, cooldown: int = COOLDOWN, *, quiet: bool = False):
        self.history: List[dict] = []
        self.cooldown = int(cooldown)
        self._cool_until = 0
        self.triggers: List[dict] = []
        self.wood_fail_streak = 0
        self.quiet = bool(quiet)

    def observe(self, rec: dict) -> Optional[dict]:
        """Record one round. Return a decision dict if we fire this round."""
        rec = dict(rec)
        rec.setdefault("success", False)
        rec.setdefault("new_items", 0)
        rec.setdefault("task", "")
        rec.setdefault("biome", "")
        rec.setdefault("iter", len(self.history) + 1)
        self.history.append(rec)
        if rec.get("success"):
            self.wood_fail_streak = 0
        elif is_wood_task(rec.get("task") or ""):
            self.wood_fail_streak += 1
        else:
            self.wood_fail_streak = 0

        # Trigger is per-round window only (same unit as recorder steps).
        # Wood streak is a recovery *action*, not an independent early trigger —
        # opening-game "Mine 1 wood log" retries must not count as a stall.
        kind = None
        window = self.history[-WINDOW:]
        if len(window) >= WINDOW:
            fails = sum(1 for r in window if not r.get("success"))
            new_items = sum(int(r.get("new_items") or 0) for r in window)
            if fails >= WINDOW_FAILS and new_items == 0:
                kind = "window"
                if self.wood_fail_streak >= WOOD_FAILS:
                    kind = "missing_wood"

        if kind is None:
            return None
        iter_n = rec["iter"]
        if iter_n < self._cool_until:
            return None
        act = classify_and_act(
            window=self.history[-WINDOW:],
            biome_has_unexplored_target=bool(rec.get("biome_has_unexplored_target")),
        )
        if kind == "missing_wood":
            act = {
                "kind": "missing_wood",
                "action": "gather_wood_far",
                "task": "Mine 1 wood log",
                "context": (
                    "Anti-stall: wood gathering failed repeatedly. "
                    "Call gather(bot, \"log\", 1); it rotates heading and expands radius. "
                    "Do not hand-write exploreUntil loops."
                ),
            }
        decision = {
            "trigger": kind,
            "iter": iter_n,
            "action": act.get("action") or act.get("kind"),
            "kind": act.get("kind"),
            "task": act.get("task"),
            "context": act.get("context"),
            "log": (
                f"[anti-stall] trigger={kind} iter={iter_n} "
                f"action={act.get('kind')} task={act.get('task')}"
            ),
        }
        self.triggers.append(decision)
        self._cool_until = iter_n + self.cooldown
        if not self.quiet:
            print(decision["log"], flush=True)
        return decision


def classify_and_act(
    window: List[dict],
    *,
    biome_has_unexplored_target: bool = False,
) -> dict:
    tasks = [r.get("task") or "" for r in window or []]
    n = max(1, len(tasks))
    wood_n = sum(1 for t in tasks if WOOD_RE.search(t) or is_wood_task(t))
    chest_n = sum(1 for t in tasks if is_chest_task(t))
    if wood_n >= max(3, (n + 1) // 3):
        return {
            "kind": "missing_wood",
            "action": "gather_wood_far",
            "task": "Mine 1 wood log",
            "context": (
                "Anti-stall: missing wood. Use gather with a far search / new direction."
            ),
        }
    if chest_n >= max(3, (n + 1) // 3):
        return {
            "kind": "chest_loop",
            "action": "storeJunk",
            "task": "Place and deposit useless items into a chest",
            "context": "Anti-stall: chest/inventory loop. Call discardJunk/storeJunk once, then resume.",
        }
    if biome_has_unexplored_target:
        return {
            "kind": "hold",
            "action": "stay",
            "task": None,
            "context": "Anti-stall: current biome still has unexplored target resources; do not leave.",
        }
    return {
        "kind": "leave_biome",
        "action": "leave_biome",
        "task": "Explore a different biome",
        "context": (
            "Anti-stall last resort: leave the current biome. "
            "Only used when the biome has no remaining unexplored target resource."
        ),
    }


def _event_ts(name: str) -> str:
    parts = name.split("_")
    if len(parts) >= 2:
        return parts[-2] + "_" + parts[-1]
    return name


def _load_event_inv(path: str) -> dict:
    try:
        payload = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, list):
        return {}
    for item in reversed(payload):
        if isinstance(item, (list, tuple)) and len(item) >= 2 and isinstance(item[1], dict):
            inv = item[1].get("inventory")
            if isinstance(inv, dict):
                return inv
        if isinstance(item, dict) and isinstance(item.get("inventory"), dict):
            return item["inventory"]
    return {}


def _expand_jsonl(path: str) -> List[dict]:
    rows = []
    if not os.path.isfile(path):
        return rows
    parsed = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            parsed.append(json.loads(line))
        except ValueError:
            continue
    for row in parsed:
        n_steps = int(row.get("n_steps") or 0)
        crashed = bool(row.get("crashed"))
        if n_steps <= 0:
            if crashed:
                emit = 1
            else:
                continue
        else:
            emit = n_steps
        for i in range(emit):
            last = i == emit - 1
            rows.append(
                {
                    "iter": row.get("iter"),
                    "task": row.get("task") or "",
                    "success": bool(row.get("success")) if last else False,
                }
            )
    return rows


_EVENT_SANITIZE = re.compile(r'[\\/:"*?<>| ]')


def _task_key_from_text(task: str) -> str:
    return _EVENT_SANITIZE.sub("_", task or "")


def _task_key_from_event_filename(path: str) -> str:
    base = os.path.basename(path)
    if base.endswith(".json"):
        base = base[:-5]
    parts = base.split("_")
    if len(parts) >= 2:
        return "_".join(parts[:-2])
    return base


def load_ckpt_rounds(ckpt: str) -> List[dict]:
    """One rec per events file. Align jsonl by recorder iter + task key.

    Event-file task name must match the iterations.jsonl window for that
    1-based round. Any mismatch raises TaskAlignmentError and stops;
    do not skip the jsonl pointer and keep scoring.
    """
    from voyager.verify import (
        TaskAlignmentError,
        _task_key_from_event_filename,
        _task_key_from_text,
        _task_windows_from_iterations,
        list_event_files,
        load_iteration_rows,
    )

    files = list_event_files(ckpt)
    iter_rows = load_iteration_rows(ckpt)
    assigned = _task_windows_from_iterations(iter_rows)
    if not files:
        rounds = []
        jsonl_steps = _expand_jsonl(os.path.join(ckpt, "metrics", "iterations.jsonl"))
        for i, meta in enumerate(jsonl_steps):
            rounds.append(
                {
                    "iter": i + 1,
                    "jsonl_iter": meta.get("iter"),
                    "task": meta.get("task") or "",
                    "success": bool(meta.get("success")),
                    "new_items": 0,
                    "biome": "",
                }
            )
        return rounds

    seen = set()
    rounds = []
    for i, path in enumerate(files, start=1):
        inv = _load_event_inv(path)
        cur = {k for k, v in inv.items() if v}
        new_items = len(cur - seen)
        seen |= cur
        meta = assigned.get(i) or {}
        task = meta.get("task") or ""
        file_key = _task_key_from_event_filename(path)
        assigned_key = _task_key_from_text(task)
        if assigned_key and file_key and assigned_key != file_key:
            raise TaskAlignmentError(
                "event/task mismatch round={} event_file={!r} "
                "iter_task={!r} file_task={!r}".format(
                    i, os.path.basename(path), task, file_key
                )
            )
        if not task:
            task = (
                file_key.replace("_", " ")
                if file_key
                else os.path.basename(path).rsplit("_", 2)[0].replace("_", " ")
            )
        rounds.append(
            {
                "iter": i,
                "jsonl_iter": i,
                "task": task,
                "success": bool(meta.get("is_final") and meta.get("llm_success")),
                "new_items": new_items,
                "biome": "",
            }
        )
    return rounds


def detect_stall_start(
    rounds: List[dict], *, min_len: int = 80, max_succ: int = 2
) -> Optional[int]:
    """1-based first round of a stall suffix (last min_len rounds complete ≤ max_succ).

    None if the run is not a stall. Walks the suffix earlier while it still has
    ≤ max_succ successes.
    """
    n = len(rounds or [])
    if n < min_len:
        return None
    if sum(1 for r in rounds[-min_len:] if r.get("success")) > max_succ:
        return None
    start = n - min_len
    for i in range(n - min_len, -1, -1):
        succ = sum(1 for r in rounds[i:] if r.get("success"))
        if succ <= max_succ:
            start = i
        else:
            break
    return start + 1


def replay_ckpt(ckpt: str) -> dict:
    rounds = load_ckpt_rounds(ckpt)
    stall = AntiStall(quiet=True)
    window_iter = None
    wood_iter = None
    for rec in rounds:
        before = len(stall.triggers)
        decision = stall.observe(rec)
        if decision and len(stall.triggers) > before:
            if decision["trigger"] == "window" and window_iter is None:
                window_iter = decision["iter"]
            if decision["trigger"] == "missing_wood" and wood_iter is None:
                wood_iter = decision["iter"]
    n = max(1, len(rounds))
    first = stall.triggers[0]["iter"] if stall.triggers else None
    return {
        "ckpt": ckpt,
        "n_rounds": n,
        "n_triggers": len(stall.triggers),
        "triggers_per_100": 100.0 * len(stall.triggers) / n,
        "first_trigger_iter": first,
        "window_trigger_iter": window_iter,
        "wood_trigger_iter": wood_iter,
        "stall_start": detect_stall_start(rounds),
        "rounds": rounds,
        "triggers": [
            {"iter": t["iter"], "trigger": t["trigger"], "kind": t["kind"]}
            for t in stall.triggers
        ],
    }
