"""③′ reversible curation governor (WP-AC).

Pure Python state machine: no LLM, no vector DB, no mineflayer, no IO.
Deterministic: the same input sequence always produces the same output.

Spec (numbers frozen by the WP-AC brief — do not tune):

capacity X = 20
    Catalog display cap. When more than X skills are *visible*, hide the
    lowest-heat candidate until ``<= X`` or no candidate remains (then the
    task is recorded once as an overflow; nothing is force-hidden).

min_age = 12 tasks
    A skill younger than 12 tasks (``task_idx - created_task < 12``) is never
    a hide candidate.

half_life = 15 tasks
    Contribution heat ``H(s, T) = Σ_t score_t × 0.5 ** ((T - t) / 15)``.

Per-task scores (max of the satisfied branches, never summed):
    critic credit   task succeeded and ``skill_credit[s]`` is True  → 2
    effective       ``counts_as_effective`` (reused, not re-implemented) → 1
    satisfied       ok + empty delta, but every fingerprint item of s was
                    already in the pre-call inventory                 → 1
    idle            ok + empty delta, fingerprint present, decidable,
                    outputs not all pre-owned                          → 0
    neutral         ok + empty delta but undecidable (no fingerprint, or
                    pre-inventory unknown)                             → 0,
                    and NOT counted as idle.

Dynamic foundation (B): a skill s with at least one *visible* dependent
``u != s`` in the current active library (visible + hidden) is protected
from hiding this round. Dependence = call (``\\bs\\s*(`` in u's code, unless
u defines ``function s(`` / ``async function s(`` itself) or supply-demand
(s's fingerprint ∩ ``extract_requires(u.code)`` blocks/inventory ≠ ∅).
Protection lapses automatically once no dependent is visible anymore.

Hide is reversible (C): hidden skills stay in the library and remain
executable (e.g. via nested calls); they are only removed from the catalog.
``recall(name, effect_key)`` restores a hidden skill matched by same name or
same effect key (``voyager.dedup_keys.effect_key``) before a rewrite happens,
then re-hides one skill if the cap is exceeded again.

Reuse (never re-implemented here):
    voyager.governance.noeffect.counts_as_effective
    voyager.chain.requires.extract_requires / delta_is_empty
    voyager.dedup_keys.effect_key
"""
from __future__ import annotations

import copy
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from voyager.chain.requires import delta_is_empty, extract_requires
from voyager.dedup_keys import effect_key
from voyager.governance.noeffect import counts_as_effective

DEFAULT_CAPACITY = 20
DEFAULT_MIN_AGE = 12
DEFAULT_HALF_LIFE = 15.0

WEIGHT_CREDIT = 2.0
WEIGHT_EFFECTIVE = 1.0
WEIGHT_SATISFIED = 1.0


@lru_cache(maxsize=None)
def _call_re(name: str) -> "re.Pattern":
    return re.compile(r"\b" + re.escape(name) + r"\s*\(")


@lru_cache(maxsize=None)
def _def_re(name: str) -> "re.Pattern":
    return re.compile(r"\b(?:async\s+)?function\s+" + re.escape(name) + r"\s*\(")


@lru_cache(maxsize=None)
def _requires_items(code: str) -> Tuple[str, ...]:
    req = extract_requires(code) or {}
    items = set(req.get("blocks") or []) | set(req.get("inventory") or [])
    return tuple(sorted(str(x) for x in items))


def calls_skill(u_code: str, s_name: str) -> bool:
    """True when u's code calls s — unless u defines its own ``s``."""
    if not u_code or not s_name:
        return False
    if _def_re(s_name).search(u_code):
        return False
    return bool(_call_re(s_name).search(u_code))


def supply_depends(s_fingerprint: Any, u_code: str) -> bool:
    """True when u requires items/blocks that s produces (fingerprint)."""
    fp = {str(x) for x in (s_fingerprint or []) if x}
    if not fp or not u_code:
        return False
    return bool(fp & set(_requires_items(u_code)))


def depends_on(u_entry: Optional[dict], s_name: str, s_entry: Optional[dict]) -> bool:
    """B-definition: u depends on s via call or supply-demand."""
    code = (u_entry or {}).get("code") or ""
    if calls_skill(code, s_name):
        return True
    return supply_depends((s_entry or {}).get("fingerprint"), code)


def visible_dependents_of(
    s_name: str,
    s_entry: Optional[dict],
    visible_entries: Dict[str, dict],
) -> List[str]:
    """Sorted visible skills u (u != s) that depend on s. Single implementation
    shared by the governor's protection filter and replay metric code."""
    out = []
    for u_name, u_entry in (visible_entries or {}).items():
        if u_name == s_name:
            continue
        if depends_on(u_entry, s_name, s_entry):
            out.append(u_name)
    return sorted(out)


def normalize_effect_key(ek: Any) -> Optional[Tuple[str, Tuple[str, ...]]]:
    """Canonical form for comparison / JSON round-trips: (action, items)."""
    if ek is None:
        return None
    try:
        action, items = ek
    except (TypeError, ValueError):
        return None
    if action is None or items is None:
        return None
    if isinstance(items, str):
        items = [items]
    return (str(action), tuple(sorted({str(x) for x in items})))


class R3PrimeGovernor:
    """③′: contribution heat + dynamic foundation protection + hide/recall.

    Interface mirrors :class:`voyager.governance.lru.LRUGovernor` where the
    concepts coincide (``add`` / ``record_outcomes`` / ``snapshot``);
    ``evict_if_over`` becomes the reversible ``hide_if_over``.
    """

    def __init__(
        self,
        capacity: Optional[int] = DEFAULT_CAPACITY,
        *,
        min_age: int = DEFAULT_MIN_AGE,
        half_life: float = DEFAULT_HALF_LIFE,
        dependency_protection: bool = True,
        satisfied_scoring: bool = True,
        recall_enabled: bool = True,
        ranking: str = "heat",
        state: Optional[dict] = None,
    ):
        if ranking not in ("heat", "recency"):
            raise ValueError(f"ranking must be 'heat' or 'recency', got {ranking!r}")
        self.capacity = capacity
        # ③′ = "heat"；③″ = "recency"（现行③的近期性分数，但"使用"按归功/有效/已满足记）
        self.ranking = ranking
        self.min_age = int(min_age)
        self.half_life = float(half_life)
        # Ablation switches (WP-AC replay). Full ③′ = all three True.
        self.dependency_protection = bool(dependency_protection)
        self.satisfied_scoring = bool(satisfied_scoring)
        self.recall_enabled = bool(recall_enabled)
        self._skills: Dict[str, dict] = {}
        self._entries: Dict[str, dict] = {}
        self._overflow_tasks: List[int] = []
        self._task_idx: Optional[int] = None
        if isinstance(state, dict):
            raw = state.get("skills")
            if isinstance(raw, dict):
                for name, st in raw.items():
                    if not isinstance(name, str) or not isinstance(st, dict):
                        continue
                    self._skills[name] = self._normalize_state(st)

    # ------------------------------------------------------------------ state

    @staticmethod
    def _normalize_state(st: dict) -> dict:
        scores = []
        for pair in st.get("scores") or []:
            try:
                scores.append([int(pair[0]), float(pair[1])])
            except (TypeError, ValueError, IndexError):
                continue
        hidden = st.get("hidden_task")
        last = st.get("last_scored_task")
        return {
            "created_task": int(st.get("created_task") or 0),
            "hidden_task": None if hidden is None else int(hidden),
            "last_scored_task": None if last is None else int(last),
            "scores": scores,
            "idle_count": int(st.get("idle_count") or 0),
            "neutral_count": int(st.get("neutral_count") or 0),
            "fail_count": int(st.get("fail_count") or 0),
            "rewrite_count": int(st.get("rewrite_count") or 0),
            **({"recalled_task": int(st["recalled_task"])} if st.get("recalled_task") is not None else {}),
            **({"recall_count": int(st["recall_count"])} if st.get("recall_count") else {}),
        }

    def _new_state(self, task_idx: int) -> dict:
        return {
            "created_task": int(task_idx),
            "hidden_task": None,
            "last_scored_task": None,
            "scores": [],
            "idle_count": 0,
            "neutral_count": 0,
            "fail_count": 0,
            "rewrite_count": 0,
        }

    # ------------------------------------------------------------- library

    def add(self, name: str, task_idx: int, entry: Optional[dict] = None) -> str:
        """Add (or rewrite) a skill.

        - unknown name          → new visible skill, ``created_task=task_idx``
        - visible same name     → in-place rewrite: entry updated, state kept
        - hidden same name      → the hidden version is discarded and replaced
          by the fresh one (this is the rewrite path when ``recall`` was not
          used / disabled; with recall enabled the caller recalls first).
        """
        self._task_idx = int(task_idx)
        if entry is not None:
            self._entries[name] = entry
        cur = self._skills.get(name)
        if cur is not None:
            if cur.get("hidden_task") is None:
                cur["rewrite_count"] = int(cur.get("rewrite_count") or 0) + 1
                return name
            del self._skills[name]
        self._skills[name] = self._new_state(task_idx)
        return name

    def entry_of(self, name: str) -> dict:
        return self._entries.get(name) or {}

    def effect_key_of(self, name: str) -> Optional[Tuple[str, Tuple[str, ...]]]:
        return normalize_effect_key(effect_key(self.entry_of(name)))

    def _active_names(self) -> List[str]:
        return list(self._skills.keys())

    def _visible_names(self) -> List[str]:
        return [
            n for n, st in self._skills.items() if st.get("hidden_task") is None
        ]

    def _hidden_names(self) -> List[str]:
        return [
            n for n, st in self._skills.items() if st.get("hidden_task") is not None
        ]

    def visible(self) -> List[str]:
        """Visible (catalog) skills, name-sorted for determinism."""
        return sorted(self._visible_names())

    def hidden(self) -> List[str]:
        """Hidden-but-alive skills, name-sorted for determinism."""
        return sorted(self._hidden_names())

    def shown_list(self, task_idx: int) -> List[str]:
        """Catalog order. heat: hottest first, ties by name.
        recency: same order as LRUGovernor.shown_list（最近使用在前；并列：使用次数多、创建早、名字）。"""
        names = self._visible_names()
        if self.ranking == "recency":
            names.sort(key=lambda n: (self.recency_score(n, task_idx), -len(self._skills[n]["scores"]),
                                      int(self._skills[n].get("created_task") or 0), n))
        else:
            names.sort(key=lambda n: (-self.heat(n, task_idx), n))
        return names

    def recency_score(self, name: str, task_idx: int) -> int:
        """现行③的分数：task_idx − 最后一次"使用"（得分>0）的任务号；从未使用 → task_idx − created_task。"""
        st = self._skills[name]
        last = st.get("last_scored_task")
        return int(task_idx) - int(st.get("created_task") or 0 if last is None else last)

    # ------------------------------------------------------------- scoring

    def heat(self, name: str, T: Optional[int] = None) -> float:
        """H(s, T) = Σ score_t × 0.5 ** ((T - t) / half_life)."""
        if T is None:
            T = self._task_idx if self._task_idx is not None else 0
        st = self._skills.get(name)
        if st is None:
            return 0.0
        total = 0.0
        tf = float(T)
        for t, s in st["scores"]:
            total += float(s) * 0.5 ** ((tf - float(t)) / self.half_life)
        return total

    def _score_task(
        self,
        recs: List[dict],
        entry: dict,
        *,
        success: bool,
        credited: bool,
        pre_inventory: Optional[dict],
    ) -> Tuple[float, str]:
        if success and credited:
            return WEIGHT_CREDIT, "credit"
        ok_recs = [r for r in recs if isinstance(r, dict) and r.get("ok")]
        if any(counts_as_effective(r, entry) for r in ok_recs):
            return WEIGHT_EFFECTIVE, "effective"
        if ok_recs:
            fp = [str(x) for x in (entry.get("fingerprint") or []) if x]
            if not fp or not isinstance(pre_inventory, dict):
                return 0.0, "neutral"
            if self.satisfied_scoring and all(
                float(pre_inventory.get(item) or 0) > 0 for item in fp
            ):
                return WEIGHT_SATISFIED, "satisfied"
            return 0.0, "idle"
        if recs:
            return 0.0, "fail"
        return 0.0, "none"

    def record_outcomes(
        self,
        task_idx: int,
        outcomes: List[dict],
        *,
        success: bool = False,
        credit: Optional[Dict[str, bool]] = None,
        pre_inventory: Optional[dict] = None,
    ) -> Dict[str, dict]:
        """Score one task for every active skill touched by it.

        Active = visible or hidden (hidden skills still execute via nested
        calls and keep accruing heat). Returns ``{name: {score, kind}}``.
        """
        self._task_idx = int(task_idx)
        credit = credit or {}
        by_name: Dict[str, List[dict]] = {}
        for rec in outcomes or []:
            if not isinstance(rec, dict):
                continue
            name = rec.get("name")
            if not isinstance(name, str) or name.startswith("__"):
                continue
            if name not in self._skills:
                continue
            by_name.setdefault(name, []).append(rec)
        if success:
            for name, flag in credit.items():
                if flag is True and isinstance(name, str) and name in self._skills:
                    by_name.setdefault(name, [])
        scored: Dict[str, dict] = {}
        for name in sorted(by_name):
            st = self._skills[name]
            entry = self.entry_of(name)
            score, kind = self._score_task(
                by_name[name],
                entry,
                success=bool(success),
                credited=credit.get(name) is True,
                pre_inventory=pre_inventory,
            )
            if score > 0:
                st["scores"].append([int(task_idx), float(score)])
                st["last_scored_task"] = int(task_idx)
            if kind == "idle":
                st["idle_count"] += 1
            elif kind == "neutral":
                st["neutral_count"] += 1
            elif kind == "fail":
                st["fail_count"] += 1
            scored[name] = {"score": score, "kind": kind}
        return scored

    # -------------------------------------------------------------- hiding

    def _hide_key(self, name: str, task_idx: int) -> tuple:
        """Smallest key is hidden first. heat: lowest heat; ties: earlier
        last-scored task first (never scored = earliest); then name.
        recency: highest recency score first; ties as LRUGovernor.evict_if_over
        (fewer uses, earlier created, name). Fully deterministic."""
        st = self._skills[name]
        if self.ranking == "recency":
            return (-self.recency_score(name, task_idx), len(st["scores"]),
                    int(st.get("created_task") or 0), name)
        last = st.get("last_scored_task")
        return (self.heat(name, task_idx), last is not None, int(last or 0), name)

    def visible_dependents(self, name: str, task_idx: Optional[int] = None) -> List[str]:
        """B: visible actives u != name that depend on name, right now."""
        vis = {u: self.entry_of(u) for u in self._visible_names() if u != name}
        return visible_dependents_of(name, self.entry_of(name), vis)

    def _is_candidate(self, name: str, task_idx: int) -> bool:
        st = self._skills[name]
        if st.get("hidden_task") is not None:
            return False
        if task_idx - int(st.get("created_task") or 0) < self.min_age:
            return False
        if self.dependency_protection and self.visible_dependents(name, task_idx):
            return False
        # ③″：本任务刚取回的技能就是现在要用的，同一任务里不再被藏（否则取回白做）。③′(heat) 行为不变。
        if self.ranking == "recency" and st.get("recalled_task") == task_idx:
            return False
        return True

    def hide_if_over(self, task_idx: int) -> List[dict]:
        """Hide lowest-heat candidates while visible > capacity.

        Returns the hide records for this round. If every candidate is
        protected/too young, nothing is hidden and the task is recorded once
        as an overflow.
        """
        self._task_idx = int(task_idx)
        t = int(task_idx)
        cap = self.capacity
        if cap is None or int(cap) <= 0:
            return []
        hidden_now: List[dict] = []
        overflow = False
        while len(self._visible_names()) > int(cap):
            cands = [n for n in self._visible_names() if self._is_candidate(n, t)]
            if not cands:
                overflow = True
                break
            victim = min(cands, key=lambda n: self._hide_key(n, t))
            st = self._skills[victim]
            # audit: the visible set at this exact hide decision (sequential
            # loop — earlier victims of the same round are already hidden)
            vis_at_hide = sorted(self._visible_names())
            st["hidden_task"] = t
            hidden_now.append(
                {
                    "name": victim,
                    "task_idx": t,
                    "heat": self.heat(victim, t),
                    "recency_score": self.recency_score(victim, t),
                    "last_scored_task": st.get("last_scored_task"),
                    "created_task": st.get("created_task"),
                    "visible_at_hide": vis_at_hide,
                    "reason": "r3pp_capacity_hide" if self.ranking == "recency" else "r3p_capacity_hide",
                }
            )
        if overflow and t not in self._overflow_tasks:
            self._overflow_tasks.append(t)
        return hidden_now

    @property
    def overflow_tasks(self) -> List[int]:
        return list(self._overflow_tasks)

    # -------------------------------------------------------------- recall

    def recall(
        self,
        name: str,
        effect_key_value: Any = None,
        task_idx: Optional[int] = None,
    ) -> Optional[str]:
        """先取回、再重写: restore a hidden skill by same name or same effect
        key, then defend the cap again. Returns the restored skill's name, or
        None on a miss. Never deletes anything."""
        if not self.recall_enabled:
            return None
        t = self._task_idx if task_idx is None else int(task_idx)
        self._task_idx = t
        hit: Optional[str] = None
        st = self._skills.get(name)
        if st is not None and st.get("hidden_task") is not None:
            hit = name
        else:
            ek = normalize_effect_key(effect_key_value)
            if ek is not None:
                matches = sorted(
                    h
                    for h in self._hidden_names()
                    if self.effect_key_of(h) == ek
                )
                if matches:
                    hit = matches[0]
        if hit is None:
            return None
        self._skills[hit]["hidden_task"] = None
        if self.ranking == "recency":
            self._skills[hit]["recalled_task"] = t
            self._skills[hit]["recall_count"] = int(self._skills[hit].get("recall_count") or 0) + 1
        self.hide_if_over(t if t is not None else 0)
        return hit

    # ------------------------------------------------------------ snapshot

    def snapshot(self) -> dict:
        skills: Dict[str, Any] = {}
        for name in sorted(self._skills):
            st = self._skills[name]
            ek = self.effect_key_of(name)
            skills[name] = {
                **copy.deepcopy(st),
                "effect_key": None if ek is None else [ek[0], list(ek[1])],
                "fingerprint": list(self.entry_of(name).get("fingerprint") or []),
            }
        return {
            "capacity": self.capacity,
            "min_age": self.min_age,
            "half_life": self.half_life,
            "dependency_protection": self.dependency_protection,
            "satisfied_scoring": self.satisfied_scoring,
            "recall_enabled": self.recall_enabled,
            "overflow_tasks": list(self._overflow_tasks),
            "skills": skills,
        }


# ③″ 预设（设计文件第 7 节）：近期性排序 + 依赖保护 + 已满足计为使用 + 隐藏可取回，无最少暴露。
R3PP_KWARGS = dict(ranking="recency", min_age=0, dependency_protection=True,
                   satisfied_scoring=True, recall_enabled=True)


def make_r3pp(capacity: Optional[int] = DEFAULT_CAPACITY, state: Optional[dict] = None) -> "R3PrimeGovernor":
    return R3PrimeGovernor(capacity, state=state, **R3PP_KWARGS)
