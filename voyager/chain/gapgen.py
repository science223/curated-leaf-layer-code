"""Generate a parameterized skill for one catalog-miss chain node."""
from __future__ import annotations

from voyager.intent import call_llm_with_retry


def generate_gap_skill(
    action_agent, *, node, events, context, existing_names,
    skills=None, code="", critique="",
):
    """Render + parse via the action agent (`skill_params=True` expected).

    Task text is the node's `goal`. Returns `parsed_result` or None.

    v28 `--gap-reference`：`skills` = 与原版同一检索得到的技能源码（首轮用 reset 时的
    top-5，之后按原版口径用 context + 聊天摘要重检索），`code`/`critique` = 上一轮执行的
    代码与评语，与原版 action agent 每轮看到的相同。三者缺省时与 v26 逐字节相同。
    """
    if action_agent is None or not node:
        return None
    task = (node.get("goal") or "").strip()
    if not task:
        return None
    events = events or [
        (
            "observe",
            {
                "inventory": {},
                "nearbyChests": {},
                "voxels": [],
                "status": {
                    "biome": "plains",
                    "timeOfDay": "day",
                    "entities": {},
                    "health": 20.0,
                    "food": 20.0,
                    "position": {"x": 0.0, "y": 64.0, "z": 0.0},
                    "equipment": [None] * 6,
                    "inventoryUsed": 0,
                },
            },
        )
    ]
    try:
        system_message = action_agent.render_system_message(skills=list(skills or []))
        human_message = action_agent.render_human_message(
            events=events,
            code=code or "",
            task=task,
            context=context or "",
            critique=critique or "",
        )
        ai_message = call_llm_with_retry(
            action_agent.llm,
            [system_message, human_message],
            label="action.gapgen",
        )
        parsed = action_agent.process_ai_message(message=ai_message, task=task)
    except Exception as err:
        print(f"\033[33m[chain.gapgen] {err}\033[0m")
        return None
    if not isinstance(parsed, dict):
        # v28：以前这里静默返回，回退原因在日志里看不到。只加打印，行为不变。
        print(f"\033[33m[chain.gapgen] parse failed: {str(parsed)[:400]}\033[0m")
        return None
    name = parsed.get("program_name")
    if not name or not parsed.get("program_code"):
        print(
            f"\033[33m[chain.gapgen] parsed without program_name/program_code "
            f"(name={name!r})\033[0m"
        )
        return None
    existing = set(existing_names or [])
    if name in existing:
        print(
            f"\033[33m[chain.gapgen] generated name {name} already in library; "
            "keeping it (will rewrite on add)\033[0m"
        )
    return parsed
