"""Offline LRU replay matching the online task order (WP-O).

Per task (task_idx = iterations.iter - n_steps):
  1. add skills whose created_task/created_iter equals this task_idx
  2. record_outcomes from all chain_log rows of this task
  3. evict_if_over

This matches SkillManager after WP-O: add_new_skill no longer evicts;
record_outcomes runs on every action-round outcome, then eviction.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from voyager.governance.lru import LRUGovernor


def task_windows(iterations: Iterable[dict]) -> List[dict]:
    windows = []
    for row in iterations or []:
        end = row.get("iter")
        steps = row.get("n_steps")
        try:
            end_i = int(end)
            step_i = int(steps) if steps is not None else 1
            start = end_i - step_i
        except (TypeError, ValueError):
            start = end
        windows.append(
            {
                "task": row.get("task"),
                "end": end,
                "start": start,
                "task_idx": start,
                "n_steps": steps,
            }
        )
    return windows


def assign_chain_to_task(chain_row: dict, windows: List[dict]) -> Optional[dict]:
    name = chain_row.get("task")
    rit = chain_row.get("iter")
    for win in windows:
        if win["task"] != name:
            continue
        try:
            if rit is None or win["end"] is None or win["start"] is None:
                continue
            if int(win["start"]) < int(rit) <= int(win["end"]):
                return win
        except (TypeError, ValueError):
            continue
    return None


def created_map(*blobs: dict) -> Dict[str, int]:
    """Earliest created_task/created_iter per skill."""
    times = created_add_times(*blobs)
    return {name: ts[0] for name, ts in times.items() if ts}


def created_add_times(*blobs: dict) -> Dict[str, List[int]]:
    """All add timestamps, including revive after LRU eviction."""
    times: Dict[str, set] = {}
    for blob in blobs:
        if not isinstance(blob, dict):
            continue
        skills = blob.get("skills") if "skills" in blob and isinstance(blob.get("skills"), dict) else blob
        if not isinstance(skills, dict):
            continue
        for name, entry in skills.items():
            if not isinstance(entry, dict):
                continue
            val = entry.get("created_task")
            if val is None:
                val = entry.get("created_iter")
            if val is None:
                continue
            try:
                iv = int(val)
            except (TypeError, ValueError):
                continue
            times.setdefault(name, set()).add(iv)
    return {name: sorted(vals) for name, vals in times.items()}


def outcomes_by_task(chain: Iterable[dict], windows: List[dict]) -> Dict[int, List[dict]]:
    by: Dict[int, List[dict]] = defaultdict(list)
    for row in chain or []:
        win = assign_chain_to_task(row, windows)
        task_idx = win["task_idx"] if win is not None else row.get("iter")
        if task_idx is None:
            continue
        try:
            t = int(task_idx)
        except (TypeError, ValueError):
            continue
        for rec in row.get("outcomes") or []:
            if isinstance(rec, dict):
                by[t].append(rec)
    return by


def evicted_names(evicted: Any) -> List[str]:
    names = []
    for item in evicted or []:
        if isinstance(item, dict):
            name = item.get("name")
            if name:
                names.append(name)
        elif isinstance(item, str):
            names.append(item)
    return names


def eviction_event(rec: dict, task_idx: int) -> dict:
    return {
        "name": rec.get("name"),
        "task_idx": rec.get("task_idx", task_idx),
        "last_exec_task": rec.get("last_exec_task"),
        "exec_count": rec.get("exec_count"),
        "score": rec.get("score"),
        "reason": rec.get("reason", "lru_capacity"),
    }


def replay_governor(
    chain: Iterable[dict],
    iterations: Iterable[dict],
    created: Dict[str, int],
    capacity: Optional[int],
    *,
    noeffect_guard: bool = False,
    skills: Optional[Dict[str, dict]] = None,
) -> Tuple[LRUGovernor, List[dict], List[dict]]:
    """Return (governor, eviction events, per-task rows)."""
    windows = task_windows(iterations)
    g = LRUGovernor(capacity)
    by_task = outcomes_by_task(chain, windows)
    added_at: Dict[str, set] = {}
    created_stamps = set()
    for name, val in (created or {}).items():
        stamps = val if isinstance(val, (list, tuple, set)) else [val]
        for stamp in stamps:
            if stamp is None:
                continue
            iv = int(stamp)
            added_at.setdefault(name, set()).add(iv)
            created_stamps.add(iv)
    task_idxs = sorted(
        {
            int(w["task_idx"])
            for w in windows
            if w.get("task_idx") is not None
        }
        | set(by_task)
        | created_stamps
    )

    events: List[dict] = []
    rows: List[dict] = []
    for t in task_idxs:
        for name, stamps in added_at.items():
            if int(t) in stamps:
                g.add(name, t)
        outcomes = by_task.get(t) or []
        if noeffect_guard:
            from voyager.governance.noeffect import mark_noeffect_idle

            outcomes = mark_noeffect_idle(outcomes, skills or {})
        g.record_outcomes(t, outcomes, noeffect_guard=bool(noeffect_guard))
        evicted = g.evict_if_over(t)
        for rec in evicted:
            events.append(eviction_event(rec, t))
        active = [
            n
            for n, e in g.snapshot()["skills"].items()
            if e.get("evicted_task") is None
        ]
        rows.append(
            {
                "task_idx": t,
                "evicted": [eviction_event(rec, t) for rec in evicted],
                "shown": list(g.shown_list(t)),
                "library_size": len(active),
            }
        )
    return g, events, rows
