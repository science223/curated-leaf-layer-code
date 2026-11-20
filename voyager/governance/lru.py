"""LRU-style skill-cache governor (WP-G).

Pure Python state machine: no LLM, no vector DB, no mineflayer.

Score
    ``task_idx - last_exec_task``. Never executed → ``task_idx - created_task``.
    Lower score = more recently executed.

shown_list
    Active (not evicted) skills, ascending score (most recently executed first).
    Ties: higher ``exec_count`` first, then earlier ``created_task``, then name.

evict_if_over
    Only when ``len(active) > capacity``. Evict the surplus, highest score first
    (longest since last execution).
    Ties: smaller ``exec_count`` first, then earlier ``created_task``, then name.
    ``capacity`` 0 or None → never evict.

Execution refresh
    ``record_outcomes`` refreshes ``last_exec_task`` only when ``ok`` is True.
    Multiple outcomes for the same skill in one task count as one refresh and
    increment ``exec_count`` once.
    When ``noeffect_guard`` is on, ok-but-idle records (``no_effect_idle``)
    increment ``no_effect_count`` instead and do not refresh recency.
    Missing ``no_effect_count`` in old state files is treated as 0.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Union


class LRUGovernor:
    def __init__(self, capacity: Optional[int], state: Optional[dict] = None):
        self.capacity = capacity
        self._skills: Dict[str, dict] = {}
        if state:
            raw = state.get("skills") if isinstance(state, dict) else None
            if isinstance(raw, dict):
                for name, entry in raw.items():
                    if not isinstance(name, str) or not isinstance(entry, dict):
                        continue
                    self._skills[name] = self._normalize_entry(entry)

    @staticmethod
    def _normalize_entry(entry: dict) -> dict:
        last = entry.get("last_exec_task")
        evicted = entry.get("evicted_task")
        return {
            "created_task": int(entry.get("created_task") or 0),
            "last_exec_task": None if last is None else int(last),
            "exec_count": int(entry.get("exec_count") or 0),
            "exec_fail_count": int(entry.get("exec_fail_count") or 0),
            "no_effect_count": int(entry.get("no_effect_count") or 0),
            "evicted_task": None if evicted is None else int(evicted),
        }

    def _active_names(self) -> List[str]:
        return [
            name
            for name, entry in self._skills.items()
            if entry.get("evicted_task") is None
        ]

    def add(self, name: str, task_idx: int) -> None:
        existing = self._skills.get(name)
        if existing is not None and existing.get("evicted_task") is None:
            return
        self._skills[name] = {
            "created_task": int(task_idx),
            "last_exec_task": None,
            "exec_count": 0,
            "exec_fail_count": 0,
            "no_effect_count": 0,
            "evicted_task": None,
        }

    def remove(self, name: str) -> None:
        self._skills.pop(name, None)

    def record_outcomes(
        self,
        task_idx: int,
        outcomes: List[dict],
        *,
        noeffect_guard: bool = False,
    ) -> None:
        seen_ok = set()
        seen_fail = set()
        seen_idle = set()
        for rec in outcomes or []:
            if not isinstance(rec, dict):
                continue
            name = rec.get("name")
            if not isinstance(name, str) or name.startswith("__"):
                continue
            entry = self._skills.get(name)
            if entry is None or entry.get("evicted_task") is not None:
                continue
            if rec.get("ok"):
                if noeffect_guard and rec.get("no_effect_idle"):
                    if name in seen_idle:
                        continue
                    seen_idle.add(name)
                    entry["no_effect_count"] = int(entry.get("no_effect_count") or 0) + 1
                    continue
                if name in seen_ok:
                    continue
                seen_ok.add(name)
                if entry.get("last_exec_task") == int(task_idx):
                    continue
                entry["last_exec_task"] = int(task_idx)
                entry["exec_count"] = int(entry.get("exec_count") or 0) + 1
            else:
                if name in seen_fail:
                    continue
                seen_fail.add(name)
                entry["exec_fail_count"] = int(entry.get("exec_fail_count") or 0) + 1

    def shown_list(self, task_idx: int) -> List[str]:
        names = self._active_names()
        names.sort(
            key=lambda n: (
                self.score(n, task_idx),
                -int(self._skills[n].get("exec_count") or 0),
                int(self._skills[n].get("created_task") or 0),
                n,
            )
        )
        return names

    def evict_if_over(self, task_idx: int) -> List[dict]:
        if self.capacity is None or int(self.capacity) <= 0:
            return []
        active = self._active_names()
        n_over = len(active) - int(self.capacity)
        if n_over <= 0:
            return []
        ordered = sorted(
            active,
            key=lambda n: (
                -self.score(n, task_idx),
                int(self._skills[n].get("exec_count") or 0),
                int(self._skills[n].get("created_task") or 0),
                n,
            ),
        )
        evicted = []
        t = int(task_idx)
        for name in ordered[:n_over]:
            entry = self._skills[name]
            score = self.score(name, task_idx)
            exec_count = int(entry.get("exec_count") or 0)
            last_exec = entry.get("last_exec_task")
            entry["evicted_task"] = t
            evicted.append(
                {
                    "name": name,
                    "score": score,
                    "exec_count": exec_count,
                    "last_exec_task": None if last_exec is None else int(last_exec),
                    "task_idx": t,
                    "reason": "lru_capacity",
                }
            )
        return evicted

    def score(self, name: str, task_idx: int) -> int:
        entry = self._skills[name]
        last = entry.get("last_exec_task")
        if last is None:
            last = entry.get("created_task", task_idx)
        return int(task_idx) - int(last)

    def last_exec_label(self, name: str, task_idx: int) -> str:
        """Catalog suffix: ``k tasks ago`` or ``never`` (not the eviction score)."""
        entry = self._skills.get(name)
        if entry is None or entry.get("last_exec_task") is None:
            return "never"
        ago = int(task_idx) - int(entry["last_exec_task"])
        return f"{ago} tasks ago"

    def working_set(self, task_idx: int, window: int) -> int:
        """Active skills executed in the last ``window`` tasks (ago < window)."""
        if window is None or int(window) <= 0:
            return 0
        win = int(window)
        t = int(task_idx)
        n = 0
        for name in self._active_names():
            last = self._skills[name].get("last_exec_task")
            if last is None:
                continue
            if t - int(last) < win:
                n += 1
        return n

    def recency_histogram(self, task_idx: int) -> dict:
        """``{tasks_since_last_exec: count}`` plus ``never`` for never-executed."""
        counts: Dict[Union[int, str], int] = {}
        never = 0
        t = int(task_idx)
        for name in self._active_names():
            last = self._skills[name].get("last_exec_task")
            if last is None:
                never += 1
                continue
            ago = t - int(last)
            counts[ago] = int(counts.get(ago) or 0) + 1
        counts["never"] = never
        return counts

    def snapshot(self) -> dict:
        skills: Dict[str, Any] = {}
        for name, entry in self._skills.items():
            skills[name] = copy.deepcopy(entry)
        return {"capacity": self.capacity, "skills": skills}
