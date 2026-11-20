"""v30 per-call action ledger + repair/retire skeleton (v4 §4).

Repair that would call an LLM to rewrite an action is an interface only.
"""
from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence


# Caller passed a bad argument (unknown item name, etc.). Charge the caller,
# not the named action's failure rate.
CALLER_FAULT_REASONS = frozenset(
    {
        "unknown_item",
        "caller_fault",
    }
)

PRECONDITION_REASONS = frozenset(
    {
        "missing_tool",
        "missing_materials",
        "missing_fuel",
        "missing_furnace",
        "missing_chest",
        "unknown_recipe",
    }
) | CALLER_FAULT_REASONS

# Environment / progress: logged and counted separately, not charged as
# action failures. Only thrown errors or "had the resources but still
# missed the contract" increment the failure rate.
PROGRESS_REASONS = frozenset(
    {
        "not_found",
        "partial",
        "timeout",
    }
)

WILSON_Z = 1.959963984540054  # 95%
REPAIR_TRIGGER_N = 8
REPAIR_TRIGGER_LOWER = 0.30
MAX_REPAIRS_PER_LINEAGE = 2
INITIAL_VERSION = "v0"


def wilson_lower_bound(failures: int, n: int, z: float = WILSON_Z) -> float:
    """Wilson score 95% lower bound of a binomial failure rate."""
    if n <= 0:
        return 0.0
    failures = max(0, int(failures))
    n = int(n)
    p = failures / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = p + z2 / (2.0 * n)
    margin = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)
    return max(0.0, (center - margin) / denom)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CallRecord:
    action: str
    version: str
    ok: bool
    reason: str
    counts_against: bool
    iteration: Optional[int] = None


class ActionRewriter:
    """Interface: LLM rewrite of a named action. Not implemented (no API call)."""

    def rewrite(self, action: str, version: str, records: Sequence[CallRecord]) -> str:
        raise NotImplementedError(
            "v30 repair rewrite is an interface only; do not call an LLM here"
        )


class ActionLedger:
    def __init__(
        self,
        named_actions: Optional[Sequence[str]] = None,
        rewriter: Optional[ActionRewriter] = None,
        max_repairs: int = MAX_REPAIRS_PER_LINEAGE,
        persist_path: Optional[str] = None,
    ):
        self.named_actions = list(named_actions or [])
        self.rewriter = rewriter
        self.max_repairs = int(max_repairs)
        self.persist_path = persist_path
        self._restoring = False
        self.retrieval_blacklist = set()
        self.version: Dict[str, str] = defaultdict(lambda: INITIAL_VERSION)
        self.repair_count: Dict[str, int] = defaultdict(int)
        self.pending_repair = set()
        self.retired = set()
        self.records: List[CallRecord] = []
        self.replaced_source: Dict[str, str] = {}
        # per (action, version): [n, failures] counting only contract-attributed calls
        self._stats: Dict[tuple, List[int]] = defaultdict(lambda: [0, 0])
        # per (action, version): reason → count for progress/env outcomes
        self._progress: Dict[tuple, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        if persist_path and os.path.isfile(persist_path):
            self._restore_from_jsonl(persist_path)

    def current_version(self, action: str) -> str:
        return self.version[action]

    def _action_state(self, action: str) -> str:
        if action in self.retired:
            return "retired"
        if action in self.pending_repair:
            return "pending_repair"
        return "active"

    def _last_record(self, action: str) -> Optional[CallRecord]:
        for rec in reversed(self.records):
            if rec.action == action:
                return rec
        return None

    def _append_event(self, payload: dict) -> None:
        if self._restoring or not self.persist_path:
            return
        parent = os.path.dirname(self.persist_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with open(self.persist_path, "a", encoding="utf-8") as fp:
            fp.write(line + "\n")

    def _event_payload(
        self,
        *,
        action: str,
        version: str,
        reason: str,
        counts_against: bool,
        state: str,
        kind: str,
        iteration=None,
        ok: bool = False,
    ) -> dict:
        return {
            "time": _now_iso(),
            "iteration": iteration,
            "action": action,
            "version": version,
            "reason": reason,
            "counts_against": bool(counts_against),
            "ok": bool(ok),
            "state": state,
            "kind": kind,
        }

    def _restore_from_jsonl(self, path: str) -> None:
        self._restoring = True
        try:
            with open(path, encoding="utf-8") as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(ev, dict):
                        continue
                    self._apply_persisted_event(ev)
            print(
                f"[ledger] restored {path} records={len(self.records)} "
                f"retired={sorted(self.retired)}",
                flush=True,
            )
        finally:
            self._restoring = False

    def _apply_persisted_event(self, ev: dict) -> None:
        kind = ev.get("kind") or "record"
        action = ev.get("action")
        if not action:
            return
        if kind == "record":
            self.record(
                action,
                ok=bool(ev.get("ok")),
                reason=str(ev.get("reason") or ""),
                version=ev.get("version"),
                iteration=ev.get("iteration"),
            )
        elif kind in ("pending_repair", "pending"):
            self.mark_pending_repair(action)
        elif kind == "retire":
            self.retire(action)

    def record(
        self,
        action: str,
        ok: bool,
        reason: str,
        version: Optional[str] = None,
        iteration: Optional[int] = None,
    ) -> CallRecord:
        version = version or self.current_version(action)
        reason = str(reason or "")
        if iteration is not None:
            try:
                iteration = int(iteration)
            except (TypeError, ValueError):
                iteration = None
        precond = reason in PRECONDITION_REASONS
        progress = reason in PROGRESS_REASONS
        counts = (not precond) and (not progress)
        rec = CallRecord(
            action=action,
            version=version,
            ok=bool(ok),
            reason=reason,
            counts_against=counts,
            iteration=iteration,
        )
        self.records.append(rec)
        key = (action, version)
        if progress and action not in self.retired:
            self._progress[key][reason] += 1
            n_prog = sum(self._progress[key].values())
            print(
                f"[ledger] progress {action}@{version} reason={reason} "
                f"(not counted; progress_n={n_prog})",
                flush=True,
            )
        if counts and action not in self.retired:
            self._stats[key][0] += 1
            if not rec.ok:
                self._stats[key][1] += 1
            n, failures = self._stats[key]
            print(
                f"[ledger] count {action}@{version} ok={rec.ok} reason={reason} "
                f"n={n} failures={failures}",
                flush=True,
            )
        self._append_event(
            self._event_payload(
                action=action,
                version=version,
                reason=reason,
                counts_against=counts,
                state=self._action_state(action),
                kind="record",
                iteration=iteration,
                ok=rec.ok,
            )
        )
        return rec

    def stats(self, action: str, version: Optional[str] = None):
        version = version or self.current_version(action)
        n, failures = self._stats[(action, version)]
        return {
            "action": action,
            "version": version,
            "n": n,
            "failures": failures,
            "failure_rate": (failures / n) if n else 0.0,
            "wilson_lower": wilson_lower_bound(failures, n),
        }

    def progress_stats(self, action: str, version: Optional[str] = None):
        version = version or self.current_version(action)
        counts = dict(self._progress[(action, version)])
        return {
            "action": action,
            "version": version,
            "counts": counts,
            "n": sum(counts.values()),
        }

    def should_repair(self, action: str, version: Optional[str] = None) -> bool:
        if action in self.retired:
            return False
        st = self.stats(action, version)
        return (
            st["n"] >= REPAIR_TRIGGER_N
            and st["wilson_lower"] > REPAIR_TRIGGER_LOWER
        )

    def mark_pending_repair(self, action: str) -> None:
        """待修标记本身不消耗修复额度、不递增版本。"""
        if action in self.retired:
            return
        already = action in self.pending_repair
        self.pending_repair.add(action)
        if already:
            return
        last = self._last_record(action)
        ver = self.current_version(action)
        print(f"[ledger] pending_repair {action}@{ver}", flush=True)
        self._append_event(
            self._event_payload(
                action=action,
                version=ver,
                reason="" if last is None else last.reason,
                counts_against=False,
                state="pending_repair",
                kind="pending_repair",
                iteration=None if last is None else last.iteration,
                ok=False,
            )
        )

    def shadow_validate(
        self,
        action: str,
        checker=None,
    ) -> bool:
        """影子验证：用账本失败现场做回归。checker(records) -> bool。

        没有 checker 则无法证明通过，返回 False（不替换）。
        """
        if checker is None:
            return False
        scenes = [
            r
            for r in self.records
            if r.action == action and (not r.ok) and r.counts_against
        ]
        return bool(checker(scenes))

    def commit_repair(
        self,
        action: str,
        *,
        shadow_passed: bool,
        candidate: Optional[str] = None,
    ) -> Optional[str]:
        """Only bump version/repair_count after shadow verification passes
        and the candidate replaces the live action.
        """
        if action in self.retired:
            return self.current_version(action)
        if not shadow_passed:
            self.mark_pending_repair(action)
            return None
        if self.repair_count[action] >= self.max_repairs:
            return None
        source = candidate
        if source is None and self.rewriter is not None:
            source = self.rewriter.rewrite(
                action,
                self.current_version(action),
                [r for r in self.records if r.action == action],
            )
        # 没有可替换的新版本（空候选 / rewriter 返回空）→ 不升版本、不扣额度。
        if not source:
            self.mark_pending_repair(action)
            return None
        self.replaced_source[action] = source
        self.repair_count[action] += 1
        n = self.repair_count[action]
        self.version[action] = f"v{n}"
        self.pending_repair.discard(action)
        return self.version[action]

    def apply_repair(self, action: str, *, shadow_passed: bool = False) -> Optional[str]:
        """Replace only after shadow verification. Default does not bump."""
        return self.commit_repair(action, shadow_passed=shadow_passed)

    def request_repair(self, action: str) -> str:
        """Mark 待修 only. Does not rewrite, replace, or consume the quota."""
        self.mark_pending_repair(action)
        return self.current_version(action)

    def retire(self, action: str) -> None:
        """Cancel named-action listing and isolate the lineage from retrieval."""
        already = action in self.retired
        last = self._last_record(action)
        st = self.stats(action)
        self.retired.add(action)
        self.pending_repair.discard(action)
        self.named_actions = [n for n in self.named_actions if n != action]
        self.retrieval_blacklist.add(action)
        if already:
            return
        ver = self.current_version(action)
        print(
            f"[ledger] retire {action}@{ver} n={st['n']} failures={st['failures']} "
            f"wilson_lower={st['wilson_lower']:.3f}",
            flush=True,
        )
        self._append_event(
            self._event_payload(
                action=action,
                version=ver,
                reason="" if last is None else last.reason,
                counts_against=False,
                state="retired",
                kind="retire",
                iteration=None if last is None else last.iteration,
                ok=False,
            )
        )

    def repair_is_implemented(self) -> bool:
        """True only when a rewriter subclass actually implements rewrite."""
        if self.rewriter is None:
            return False
        return type(self.rewriter).rewrite is not ActionRewriter.rewrite

    def ingest_skill_outcomes(self, events, iteration=None) -> List[tuple]:
        """Record named-action contracts from skillOutcomes. Chat is ignored.

        Library-skill probe records (no `reason`) are skipped. Returns
        [(action, after_record decision), ...].
        """
        raw = []
        for item in events or []:
            payload = None
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                payload = item[1]
            elif isinstance(item, dict):
                payload = item
            if isinstance(payload, dict) and payload.get("skillOutcomes"):
                raw = payload.get("skillOutcomes") or []
        named = set(self.named_actions)
        decisions = []
        for rec in raw or []:
            if not isinstance(rec, dict):
                continue
            name = rec.get("name")
            if name not in named:
                continue
            if "reason" not in rec:
                continue
            reason = str(rec.get("reason") or "")
            ok = bool(rec.get("ok"))
            self.record(name, ok=ok, reason=reason, iteration=iteration)
            decisions.append((name, self.after_record(name)))
        return decisions

    def after_record(self, action: str) -> Optional[str]:
        """Trigger rule after a call. Returns 'pending_repair', 'retire', or None.

        LLM rewrite is an interface only. When it is not implemented, reaching
        the 待修 threshold retires immediately (cancel named listing + retrieval
        blacklist). There is no waiting path that can never complete.
        When rewrite is implemented, 待修 does not consume the quota; a caller
        that wants to replace must call commit_repair() with shadow_passed=True
        after shadow verification. After max_repairs, the next trigger retires.
        """
        if action in self.retired:
            return None
        if not self.should_repair(action):
            return None
        if (not self.repair_is_implemented()) or (
            self.repair_count[action] >= self.max_repairs
        ):
            self.retire(action)
            return "retire"
        self.mark_pending_repair(action)
        return "pending_repair"
