"""F2: pick the call-graph root as the main function. F7: fill critic deltas."""
from __future__ import annotations

from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from voyager.governance.ledger import CALLER_FAULT_REASONS
from voyager.utils.js_parse import parse_call_graph, parse_program, JsParseError

CALLER_MISUSE_REASONS = frozenset(CALLER_FAULT_REASONS)


def _is_bot_sig(fn: dict) -> bool:
    params = fn.get("params") or []
    if len(params) != 1:
        return False
    return getattr(params[0], "name", None) == "bot"


def _last_async_name(functions: Sequence[dict]) -> Optional[str]:
    for fn in reversed(list(functions)):
        if fn.get("type") == "AsyncFunctionDeclaration" or (
            fn.get("async") and fn.get("type") in {
                "AsyncFunctionDeclaration",
                "ArrowFunctionExpression",
                "FunctionExpression",
            }
        ):
            if fn.get("name"):
                return fn["name"]
        if fn.get("type") == "AsyncFunctionDeclaration" and fn.get("name"):
            return fn["name"]
    for fn in reversed(list(functions)):
        if fn.get("async") and fn.get("name"):
            return fn["name"]
    return None


def _legacy_last_async(code: str) -> Optional[str]:
    parsed = parse_program(code)
    for fn in reversed(parsed.get("functions") or []):
        if fn.get("type") == "AsyncFunctionDeclaration" and fn.get("name"):
            return fn["name"]
    return None


_NO_ASYNC_ERR = "No async function found. Your main function must be async."
_BOT_SIG_ERR = "Main function {name} must take a single argument named 'bot'"
_DECL_TYPES = frozenset({"AsyncFunctionDeclaration", "FunctionDeclaration"})


def select_main(code: str, *, call_graph: bool = False) -> Tuple[Optional[str], Optional[str]]:
    """Return (main_name, error). error is set when call-graph cannot pick a unique root."""
    if not call_graph:
        name = _legacy_last_async(code)
        if name is None:
            return None, _NO_ASYNC_ERR
        return name, None

    last = _legacy_last_async(code) or "?"
    try:
        data = parse_call_graph(code)
    except JsParseError as exc:
        return None, str(exc)
    functions = data.get("functions") or []
    # Root among ALL async top-level names; signature checked after.
    async_named = [
        fn for fn in functions if fn.get("async") and fn.get("name")
    ]
    if not async_named:
        return None, _NO_ASYNC_ERR
    all_names = {fn.get("name") for fn in functions if fn.get("name")}
    referenced = set()
    for fn in functions:
        self_name = fn.get("name")
        for ref in fn.get("refs") or []:
            if ref != self_name and ref in all_names:
                referenced.add(ref)
    roots = [fn for fn in async_named if fn["name"] not in referenced]
    if len(roots) != 1:
        return None, f"请只保留一个主函数，上次执行的是 {last}"
    root = roots[0]
    if root.get("type") not in _DECL_TYPES:
        # Arrow / function-expression root is not in parse_program bodies.
        # Same wording as the original last-async path so OLD-arm text matches.
        return None, _NO_ASYNC_ERR
    if not _is_bot_sig(root):
        return None, _BOT_SIG_ERR.format(name=root["name"])
    return root["name"], None


def inventory_delta(before: Optional[Mapping], after: Optional[Mapping]) -> Dict[str, int]:
    before = before or {}
    after = after or {}
    keys = set(before) | set(after)
    out: Dict[str, int] = {}
    for key in keys:
        try:
            b = int(before.get(key) or 0)
        except (TypeError, ValueError):
            b = 0
        try:
            a = int(after.get(key) or 0)
        except (TypeError, ValueError):
            a = 0
        d = a - b
        if d:
            out[key] = d
    return out


def _event_inventories(events: Sequence) -> List[dict]:
    invs = []
    for item in events or []:
        payload = None
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            payload = item[1]
        elif isinstance(item, dict):
            payload = item
        if isinstance(payload, dict) and "inventory" in payload:
            inv = payload.get("inventory")
            if isinstance(inv, dict):
                invs.append(inv)
    return invs


def enrich_executed_skills(
    skills: Optional[Iterable],
    events: Sequence = (),
    *,
    before_inventory: Optional[Mapping] = None,
) -> list:
    """Fill empty per-skill delta from this round's inventory change (F7)."""
    recs = [dict(r) for r in (skills or []) if isinstance(r, dict)]
    invs = _event_inventories(events)
    after = invs[-1] if invs else {}
    before = before_inventory
    if before is None:
        before = invs[0] if len(invs) >= 2 else {}
    delta = inventory_delta(before, after)
    out = []
    for rec in recs:
        current = rec.get("delta")
        if not current:
            rec["delta"] = dict(delta)
        out.append(rec)
    return out
