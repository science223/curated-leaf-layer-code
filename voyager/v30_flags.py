"""v30 CLI flag group. All default off; run_meta only records flags that are on."""
from __future__ import annotations


V30_NAMED_ACTIONS = (
    "gather",
    "craft",
    "smelt",
    "equipBest",
    "prepare",
    "discardJunk",
    "mineOre",
    "collectFluid",
    "obtain",
)


def add_v30_arguments(parser):
    group = parser.add_argument_group(
        "v30",
        "v4.1 components (all default off; off = byte-identical to current wt_v30)",
    )
    group.add_argument(
        "--v30-no-lru-evict",
        action="store_true",
        default=False,
        help="只记账不 LRU 移出。不加时与改动前相同（记账后仍 evict）。",
    )
    group.add_argument(
        "--v30-verify-critic",
        action="store_true",
        default=False,
        help="用规则核对器替代 LLM critic（解析不了则回退）。不加时走原版 critic。",
    )
    group.add_argument(
        "--v30-named-actions",
        action="store_true",
        default=False,
        help="注入 gather/craft/smelt/equipBest/prepare/discardJunk 并点名。不加时不注入。",
    )
    group.add_argument(
        "--v30-ledger",
        action="store_true",
        default=False,
        help="按次契约记账 + 先修后退骨架。不加时不记账。",
    )
    group.add_argument(
        "--v30-shared-fixes",
        action="store_true",
        default=False,
        help="两臂共修：F2 调用图主函数、F5 不存在物品、F7 critic delta、F8 returnItems 快照+增量。不加时与现在逐字节相同。",
    )
    group.add_argument(
        "--v30-anti-stall",
        action="store_true",
        default=False,
        help="N1 防卡死。不加时不触发。",
    )
    group.add_argument(
        "--v30-knowledge-text",
        action="store_true",
        default=False,
        help="把技能常数表译成的陈述性知识注入 action 提示词（与点名技能同一位置）。不加时不注入。",
    )
    return parser


def merge_named_actions(existing, enabled):
    """Union existing extra_base_skills with v30 names when the flag is on."""
    if not enabled:
        return existing
    out = list(existing or [])
    for name in V30_NAMED_ACTIONS:
        if name not in out:
            out.append(name)
    return out


def v30_voyager_kwargs(args) -> dict:
    """Only keys that must change when a v30 flag is on. All-off → {}."""
    kw = {}
    if getattr(args, "v30_no_lru_evict", False):
        kw["lru_evict"] = False
    if getattr(args, "v30_verify_critic", False):
        kw["verify_critic"] = True
    if getattr(args, "v30_named_actions", False):
        kw["v30_named_actions"] = True
    if getattr(args, "v30_ledger", False):
        kw["v30_ledger"] = True
    if getattr(args, "v30_shared_fixes", False):
        kw["v30_shared_fixes"] = True
    if getattr(args, "v30_anti_stall", False):
        if not getattr(args, "v30_named_actions", False):
            raise ValueError(
                "--v30-anti-stall requires --v30-named-actions "
                "(gather/storeJunk must exist before anti-stall can inject them)"
            )
        kw["v30_anti_stall"] = True
    if getattr(args, "v30_knowledge_text", False):
        kw["v30_knowledge_text"] = True
    return kw


def v30_run_meta_fields(kwargs: dict) -> dict:
    """Fields written into run_meta.json; empty when every v30 switch is off."""
    out = {}
    if kwargs.get("lru_evict") is False:
        out["v30_no_lru_evict"] = True
    if kwargs.get("verify_critic"):
        out["v30_verify_critic"] = True
    if kwargs.get("v30_named_actions"):
        out["v30_named_actions"] = True
    if kwargs.get("v30_ledger"):
        out["v30_ledger"] = True
    if kwargs.get("v30_shared_fixes"):
        out["v30_shared_fixes"] = True
    if kwargs.get("v30_anti_stall"):
        out["v30_anti_stall"] = True
    if kwargs.get("v30_knowledge_text"):
        out["v30_knowledge_text"] = True
    return out
