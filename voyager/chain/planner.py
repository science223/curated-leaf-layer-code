"""Plan a linear skill chain from a full-library catalog."""
from __future__ import annotations

import json
import re

from langchain.schema import AIMessage, HumanMessage, SystemMessage

from voyager.chain.catalog import CATALOG_LINE_MAX, format_catalog
from voyager.chain.requires import delta_is_empty
from voyager.intent import call_llm_with_retry
from voyager.prompts import load_prompt
from voyager.utils.json_utils import fix_and_parse_json

_MAX_PARSE_ATTEMPTS = 3  # 1 try + 2 retries
_EXPR_MARKERS = ("(", ")", "+", "*", "/", "=>", "`")


def render_plan_prompt(*, task, context, observation_text, skill_catalog):
    """Fill `chain_plan.txt`. Safe against JSON braces in the template."""
    template = load_prompt("chain_plan")
    catalog_text = format_catalog(skill_catalog)
    return (
        template.replace("{task}", task or "")
        .replace("{context}", context or "None")
        .replace("{observation}", observation_text or "None")
        .replace("{catalog}", catalog_text)
    )


def render_plan_prompt_v2(
    *, task, context, observation_text, skill_catalog, noeffect_banned=None
):
    """Fill `chain_plan_v2.txt`. Catalog lines clipped to CATALOG_LINE_MAX."""
    from voyager.governance.noeffect import noeffect_ban_suffix

    template = load_prompt("chain_plan_v2")
    catalog_text = format_catalog(skill_catalog, max_line_chars=CATALOG_LINE_MAX)
    extra = noeffect_ban_suffix(noeffect_banned)
    return (
        template.replace("{task}", task or "")
        .replace("{context}", context or "None")
        .replace("{observation}", observation_text or "None")
        .replace("{catalog}", catalog_text)
        .replace("{noeffect_banned}", extra)
    )


def _clip_text(text, limit=2000):
    text = text if isinstance(text, str) else ("" if text is None else str(text))
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _public_nodes(nodes):
    return [
        {
            "goal": node.get("goal"),
            "verb": node.get("verb"),
            "target": node.get("target"),
            "skill": node.get("skill"),
            "args": node.get("args") or {},
            "repeat": node.get("repeat", 1),
        }
        for node in nodes or []
    ]


def render_review_prompt(*, task, observation_text, nodes, skill_catalog, skill_codes):
    template = load_prompt("chain_review_v2")
    index = _catalog_index(skill_catalog)
    blocks = []
    for node in nodes or []:
        name = node.get("skill")
        if not name or name not in index:
            continue
        code = (skill_codes or {}).get(name) or ""
        if len(code) > 2000:
            code = code[:1999] + "…"
        record = index[name].get("record") or []
        blocks.append(f"### {name}\nrecord: {record}\n```javascript\n{code}\n```")
    skills_text = "\n\n".join(blocks) if blocks else "(no catalog skills in proposal)"
    chain_text = json.dumps(_public_nodes(nodes), ensure_ascii=False)
    return (
        template.replace("{task}", task or "")
        .replace("{observation}", observation_text or "None")
        .replace("{chain}", chain_text)
        .replace("{skills}", skills_text)
    )


def observation_text_from_action_agent(
    action_agent,
    *,
    events,
    code="",
    task="",
    context="",
    critique="",
    task_intent=None,
):
    """Reuse ActionAgent.render_human_message — same source as the action model."""
    message = action_agent.render_human_message(
        events=events,
        code=code,
        task=task,
        context=context,
        critique=critique,
        task_intent=task_intent,
    )
    return getattr(message, "content", "") or ""


def _catalog_index(skill_catalog):
    index = {}
    for item in skill_catalog or []:
        name = item.get("name")
        if name:
            index[name] = item
    return index


def _param_names(signature):
    names = set()
    if not isinstance(signature, dict):
        return names
    for i, param in enumerate(signature.get("params") or []):
        if not isinstance(param, dict):
            continue
        pname = param.get("name")
        if not pname or pname == "bot" or (i == 0 and pname == "bot"):
            continue
        names.add(pname)
    return names


def is_literal(value):
    """JSON scalar that can be emitted as a JS literal. Objects/arrays/expressions are not."""
    if isinstance(value, bool) or value is None:
        return True
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (
            value != value or value in (float("inf"), float("-inf"))
        ):
            return False
        return True
    if isinstance(value, str):
        if any(mark in value for mark in _EXPR_MARKERS):
            return False
        return True
    return False


def _extract_json_text(text):
    raw = (text or "").strip()
    md = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if md:
        raw = md.group(1).strip()
    start = raw.find("[")
    end = raw.rfind("]")
    if start != -1 and end > start:
        return raw[start : end + 1]
    return raw


def _as_node_list(parsed):
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for key in ("nodes", "chain", "plan"):
            value = parsed.get(key)
            if isinstance(value, list):
                return value
        if "goal" in parsed or "skill" in parsed:
            return [parsed]
    raise ValueError(f"chain plan is not a JSON array: {type(parsed).__name__}")


def normalize_node(raw, catalog_index):
    if not isinstance(raw, dict):
        raise ValueError("node is not an object")
    goal = str(raw.get("goal") or "")
    verb = str(raw.get("verb") or "")
    target = str(raw.get("target") or "")
    skill = raw.get("skill")
    if skill is not None:
        skill = str(skill)
        if skill in ("", "null", "None"):
            skill = None
    if skill is not None and skill not in catalog_index:
        skill = None
    args = raw.get("args") if isinstance(raw.get("args"), dict) else {}
    cleaned = {}
    non_literal = False
    signature = None
    if skill is not None:
        signature = catalog_index[skill].get("signature")
        allowed = _param_names(signature)
        for key, value in args.items():
            if key == "bot" or key not in allowed:
                continue
            if not is_literal(value):
                non_literal = True
                break
            cleaned[key] = value
    else:
        cleaned = {}
    if non_literal:
        skill = None
        signature = None
        cleaned = {}
    repeat = raw.get("repeat", 1)
    try:
        repeat = int(repeat)
    except (TypeError, ValueError):
        repeat = 1
    if repeat < 1:
        repeat = 1
    node = {
        "goal": goal,
        "verb": verb,
        "target": target,
        "skill": skill,
        "args": cleaned,
        "repeat": repeat,
    }
    if signature is not None:
        node["signature"] = signature
    return node


def parse_chain_response(text, skill_catalog):
    payload = _extract_json_text(text)
    parsed = fix_and_parse_json(payload)
    if isinstance(parsed, str):
        parsed = json.loads(parsed)
    nodes_raw = _as_node_list(parsed)
    index = _catalog_index(skill_catalog)
    return [normalize_node(item, index) for item in nodes_raw]


def _strip_fence(text):
    raw = (text or "").strip()
    md = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if md:
        raw = md.group(1).strip()
    return raw


def parse_chain_payload(text, skill_catalog):
    """Parse v2 object `{reasoning, chain}` or a bare JSON array. Always returns both."""
    raw = _strip_fence(text)
    parsed = None
    try:
        parsed = fix_and_parse_json(raw)
        if isinstance(parsed, str):
            parsed = json.loads(parsed)
    except Exception:
        parsed = None
    if parsed is None:
        payload = _extract_json_text(text)
        parsed = fix_and_parse_json(payload)
        if isinstance(parsed, str):
            parsed = json.loads(parsed)
    reasoning = ""
    if isinstance(parsed, dict) and parsed.get("reasoning") is not None:
        reasoning = parsed.get("reasoning")
        if not isinstance(reasoning, str):
            reasoning = json.dumps(reasoning, ensure_ascii=False)
    nodes_raw = _as_node_list(parsed)
    index = _catalog_index(skill_catalog)
    nodes = [normalize_node(item, index) for item in nodes_raw]
    return {"nodes": nodes, "reasoning": reasoning}


def observation_text_from_events(events):
    if not events:
        return "None"
    last = events[-1]
    payload = last[1] if isinstance(last, (list, tuple)) and len(last) > 1 else {}
    if not isinstance(payload, dict):
        return "None"
    status = payload.get("status") or {}
    inventory = payload.get("inventory") or {}
    voxels = payload.get("voxels") or []
    pos = status.get("position") or {}
    return (
        f"Inventory: {inventory}\n"
        f"Nearby blocks: {voxels}\n"
        f"Biome: {status.get('biome')}\n"
        f"Health: {status.get('health')}\n"
        f"Hunger: {status.get('food')}\n"
        f"Position: {pos}\n"
    )


def render_outcomes_critique(outcomes, *, planner_v2=False):
    if not outcomes:
        return ""
    lines = ["Previous chain node outcomes (runtime probe):"]
    silent = []
    for rec in outcomes:
        if not isinstance(rec, dict):
            continue
        lines.append(
            f"- {rec.get('name')}: ok={rec.get('ok')} err={rec.get('err')} "
            f"delta={rec.get('delta')}"
        )
        if planner_v2 and rec.get("ok") and delta_is_empty(rec.get("delta")):
            name = rec.get("name") or "?"
            silent.append(
                f"{name} returned without any inventory change (possible silent failure)"
            )
    lines.extend(silent)
    return "\n".join(lines)


def render_node_failure_critique(fail):
    """v29 `--chain-node-repair`：指名返修目标节点的评语（英文），进 `_chain_critique` 与 `_gap_critique`。

    `fail` 二选一：
    - kind="error"：assembler.parse_node_failure 的返回值（节点抛错、链停在这里）；
    - kind="no_effect"：本步没报错却判失败时，第一个非放置类的无效果库技能节点（疑似原因，链是跑完的）。
    说清：第几个节点、哪个技能、什么问题；前面的节点已完成；从当前状态重新规划；
    本任务内别再用这个技能；仍需要就写新代码（skill=null）。
    """
    if not isinstance(fail, dict):
        return ""
    index = int(fail.get("node") or 0)
    total = int(fail.get("total") or 0)
    name = fail.get("skill") or "?"
    if fail.get("kind") == "no_effect":
        lines = [
            f"Chain node {index}/{total} ({name}) returned without the expected inventory change "
            "(suspected cause).",
            "All nodes ran without errors, but the task was not completed.",
        ]
    else:
        what = "error: " + _clip_text(fail.get("message") or "unknown error", 300)
        head = f"Chain node {index}/{total} ({name}) failed: {what}"
        lines = [head if head.endswith((".", "…")) else head + "."]
        if index <= 1:
            done = "It was the first node, so no earlier node ran."
        elif index == 2:
            done = "Node 1 before it completed."
        else:
            done = f"Nodes 1-{index - 1} before it completed."
        if index < total:
            done += " The nodes after it did not run."
        lines.append(done)
    lines.append("Re-plan from the current state shown in the observation.")
    if fail.get("source") == "library":
        lines.append(
            f"Do not use {name} again in this task; it has been removed from the skill catalog."
        )
    else:
        lines.append(f"Do not use {name} again in this task; it was new code that failed.")
    lines.append("If this step is still needed, write new code for it (skill=null).")
    return "\n".join(lines)


def plan_chain(llm, *, task, context, observation_text, skill_catalog):
    """Call the planner LLM and return a list of node dicts. `[]` on parse failure."""
    prompt = render_plan_prompt(
        task=task,
        context=context,
        observation_text=observation_text,
        skill_catalog=skill_catalog,
    )
    messages = [
        SystemMessage(content=prompt),
        HumanMessage(
            content="Return the JSON array of chain nodes for the task above."
        ),
    ]
    last_error = None
    for attempt in range(_MAX_PARSE_ATTEMPTS):
        ai_message = call_llm_with_retry(llm, messages, label="chain.plan")
        content = getattr(ai_message, "content", ai_message)
        if not isinstance(content, str):
            content = str(content)
        try:
            return parse_chain_response(content, skill_catalog)
        except Exception as err:
            last_error = err
            messages = list(messages) + [
                ai_message
                if isinstance(ai_message, AIMessage)
                else AIMessage(content=content),
                HumanMessage(
                    content=(
                        "Your previous reply could not be parsed as a JSON array of "
                        f"chain nodes ({err}). Reply with ONLY the JSON array."
                    )
                ),
            ]
    print(f"\033[33m[chain.plan] parse failed after retries: {last_error}\033[0m")
    return []


def _call_plan_payload(llm, messages, skill_catalog, label="chain.plan"):
    last_error = None
    content = ""
    for _attempt in range(_MAX_PARSE_ATTEMPTS):
        ai_message = call_llm_with_retry(llm, messages, label=label)
        content = getattr(ai_message, "content", ai_message)
        if not isinstance(content, str):
            content = str(content)
        try:
            return parse_chain_payload(content, skill_catalog), content
        except Exception as err:
            last_error = err
            messages = list(messages) + [
                ai_message
                if isinstance(ai_message, AIMessage)
                else AIMessage(content=content),
                HumanMessage(
                    content=(
                        "Your previous reply could not be parsed as "
                        '{"reasoning": string, "chain": [nodes]} '
                        f"({err}). Reply with ONLY that JSON object. "
                        "A plain JSON array of nodes is also accepted."
                    )
                ),
            ]
    print(f"\033[33m[{label}] parse failed after retries: {last_error}\033[0m")
    return None, content


def plan_chain_v2(
    plan_llm,
    review_llm,
    *,
    task,
    context,
    observation_text,
    skill_catalog,
    skill_codes=None,
    noeffect_banned=None,
):
    """Planner v2: think-then-act, optional code review. `nodes=[]` on plan failure."""
    prompt = render_plan_prompt_v2(
        task=task,
        context=context,
        observation_text=observation_text,
        skill_catalog=skill_catalog,
        noeffect_banned=noeffect_banned,
    )
    messages = [
        SystemMessage(content=prompt),
        HumanMessage(
            content=(
                "Return the JSON object {\"reasoning\": string, \"chain\": [nodes]} "
                "for the task above."
            )
        ),
    ]
    parsed, _raw = _call_plan_payload(
        plan_llm, messages, skill_catalog, label="chain.plan"
    )
    if not parsed or not parsed.get("nodes"):
        return {
            "nodes": [],
            "meta": {
                "planner_version": 2,
                "reasoning": {"plan": "", "review": ""},
                "reviewed": False,
                "review_changed": False,
            },
        }
    nodes = parsed["nodes"]
    plan_reasoning = _clip_text(parsed.get("reasoning") or "")
    reviewed = False
    review_changed = False
    review_reasoning = ""
    library_nodes = [n for n in nodes if n.get("skill")]
    if library_nodes and review_llm is not None:
        review_prompt = render_review_prompt(
            task=task,
            observation_text=observation_text,
            nodes=nodes,
            skill_catalog=skill_catalog,
            skill_codes=skill_codes,
        )
        review_messages = [
            SystemMessage(content=review_prompt),
            HumanMessage(
                content=(
                    "Return the JSON object {\"reasoning\": string, \"chain\": [nodes]} "
                    "after checking each catalog skill against the observation."
                )
            ),
        ]
        try:
            ai_message = call_llm_with_retry(
                review_llm, review_messages, label="chain.review"
            )
            content = getattr(ai_message, "content", ai_message)
            if not isinstance(content, str):
                content = str(content)
            reviewed_payload = parse_chain_payload(content, skill_catalog)
            review_nodes = reviewed_payload.get("nodes") or []
            if review_nodes:
                reviewed = True
                review_reasoning = _clip_text(reviewed_payload.get("reasoning") or "")
                if _public_nodes(review_nodes) != _public_nodes(nodes):
                    review_changed = True
                nodes = review_nodes
            else:
                print("\033[33m[chain.review] empty chain; keeping plan\033[0m")
        except Exception as err:
            print(f"\033[33m[chain.review] parse failed; keeping plan: {err}\033[0m")
    return {
        "nodes": nodes,
        "meta": {
            "planner_version": 2,
            "reasoning": {"plan": plan_reasoning, "review": review_reasoning},
            "reviewed": reviewed,
            "review_changed": review_changed,
        },
    }
