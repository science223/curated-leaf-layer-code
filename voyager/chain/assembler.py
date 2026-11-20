"""Assemble a chain of nodes into one program_code + exec_code blob."""
from __future__ import annotations

import json
import re


def _js_literal(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (int, float)):
        return json.dumps(value)
    raise TypeError(f"non-literal arg value: {value!r}")


def _param_list(signature):
    params = []
    if isinstance(signature, dict):
        raw = signature.get("params") or []
    else:
        raw = []
    for i, param in enumerate(raw):
        if not isinstance(param, dict):
            continue
        name = param.get("name")
        if not name or name == "bot" or (i == 0 and name == "bot"):
            continue
        params.append(param)
    return params


def call_expr(name, args=None, signature=None):
    """`await NAME(bot, …);` with args in signature order (positional)."""
    args = args or {}
    params = _param_list(signature)
    names = [p.get("name") for p in params]
    last = -1
    for i, pname in enumerate(names):
        if pname in args:
            last = i
    parts = ["bot"]
    for i, param in enumerate(params):
        if i > last:
            break
        pname = param.get("name")
        if pname in args:
            parts.append(_js_literal(args[pname]))
        else:
            default = param.get("default")
            if default is None:
                parts.append("undefined")
            else:
                parts.append(str(default))
    return f"await {name}({', '.join(parts)});"


def budget_guard(time_budget_s, index, name):
    """v28 `--chain-time-budget`：节点开跑前检查已用时间，超预算就抛错结束本步。

    抛错走 Mineflayer 的 onError（与普通执行报错相同），不触发 300s 请求超时，
    因而不会走超时后的硬重置（回滚背包、传送回步前位置）。
    """
    if not time_budget_s:
        return ""
    ms = int(time_budget_s) * 1000
    msg = json.dumps(
        f"chain time budget {int(time_budget_s)}s exceeded before node {index}: {name}; "
        "remaining nodes skipped",
        ensure_ascii=False,
    )
    return f"if (Date.now() - __chainT0 > {ms}) {{ throw new Error({msg}); }} "


_BUDGET_START = "const __chainT0 = Date.now();"
_BUDGET_GUARD_RE = re.compile(
    r'if \(Date\.now\(\) - __chainT0 > \d+\) \{ throw new Error\("(?:[^"\\]|\\.)*"\); \} '
)


def strip_budget_guards(code):
    """去掉 budget_guard 插入的计时行，得到与不加预算时相同的代码（给"上一轮代码"用）。"""
    if not code or "__chainT0" not in code:
        return code
    lines = [ln for ln in code.split("\n") if ln.strip() != _BUDGET_START]
    return _BUDGET_GUARD_RE.sub("", "\n".join(lines))


# ---------------------------------------------------------------------------
# v29 `--chain-node-check`：逐节点出错信号（只认抛错）。
#
# 每个节点（repeat 循环里的每一次调用）外面套 try/catch，原调用语句逐字保留在中间：
#   try { await NAME(bot, …); } catch (__e) { __chainErr("[chain-node] node i/n NAME failed: ", __e); }
# 调用抛错 → 抛 Error("[chain-node] node i/n NAME failed: error: <原错误信息>")，沿用原错误的 stack
# （index.js 的 handleError 仍指到原来的出错行）；抛错即终止本步，后面的节点不再执行。
# 不做背包快照、不因"没改背包"抛错：库技能的无效果由 Python 从探针 outcomes 判（find_noeffect_nodes），只记录。
# 前缀字符串用 json.dumps 转义；计时守卫（budget_guard）在 try 外面，预算超时的报错不算节点失败。
# ---------------------------------------------------------------------------
NODE_CHECK_TAG = "[chain-node]"
_NODE_HELPERS = (
    "const __chainErr = (p, e) => { const ne = new Error(p + 'error: ' + String((e && e.message) || e));"
    " if (e && typeof e.stack === 'string') { try { ne.stack = e.stack; } catch (_) {} } throw ne; };",
)
_NODE_WRAP_RE = re.compile(r'try \{ (.*?) \} catch \(__e\) \{ __chainErr\("(?:[^"\\]|\\.)*", __e\); \}')
_NODE_FAIL_RE = re.compile(re.escape(NODE_CHECK_TAG) + r" node (\d+)/(\d+) (.+?) failed: error: ")
_FUNC_DECL_RE = re.compile(r"\bfunction\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(")


def node_needs_effect(skill_entry=None, verb=None):
    """库技能节点"正常返回但背包没变"是否记为无效果。

    复用 noeffect.counts_as_effective（IDLE_EXEMPT_ACTIONS）：拿一条 ok + 空 delta 的记录去问它，
    对技能记录的意图（skill_intent → task_intent）或规划器给节点写的 verb 任一判为有效，就算
    不需要改背包（移动、装备等）；否则应改背包。不新增任何类别。
    """
    from voyager.governance.noeffect import counts_as_effective

    idle_ok = {"ok": True, "delta": {}}
    if counts_as_effective(idle_ok, skill_entry or {}):
        return False
    if verb and counts_as_effective(idle_ok, {"skill_intent": {"target_action": verb}}):
        return False
    return True


def is_place_node(skill_entry=None, verb=None):
    """放置类节点：技能记录的意图为 place，或规划器给节点写的 verb 为 place，任一成立即是。

    v29 ② 不把它们当"疑似原因"候选：东西已经在附近时幂等返回是无辜的（规划者 09-19 拍板）。
    它们仍照常记进 node_noeffect。
    """
    from voyager.dedup_keys import target_action_for_dedup

    return target_action_for_dedup(skill_entry or {}) == "place" or str(verb or "").strip().lower() == "place"


def find_noeffect_nodes(nodes, outcomes, needs_effect=None, stop_before=None):
    """本步"正常返回却没改背包"的库技能节点序号（1 起，升序）。只记录，不停链。

    探针记录（skillOutcomes）按调用开始的顺序排、含嵌套调用。把顶层节点调用（repeat 展开）依次对到
    指针之后第一条同名记录，对上就推进指针；needs_effect(node) 为真的节点，记录 ok 且 delta 为空即记下
    （repeat 里任一次即算）。新写的缺口代码由调用方给 needs_effect=False：没有探针的对不上记录、不判；
    与库技能同名重写的会对上记录，只推进指针、不判。stop_before = 本步报错的节点序号，它和它之后的节点
    没有跑完，不判。已知局限：前面节点内部嵌套调用了与后面节点同名的库技能时，可能对到嵌套那条记录。
    """
    from voyager.chain.requires import delta_is_empty

    records = [rec for rec in (outcomes or []) if isinstance(rec, dict)]
    ptr = 0
    found = []
    index = 0
    for node in nodes or []:
        name = node.get("skill")
        if not name:
            continue
        index += 1
        if stop_before is not None and index >= stop_before:
            break
        try:
            repeat = max(1, int(node.get("repeat") or 1))
        except (TypeError, ValueError):
            repeat = 1
        judge = bool(needs_effect(node)) if needs_effect is not None else True
        for _ in range(repeat):
            j = next((k for k in range(ptr, len(records)) if records[k].get("name") == name), None)
            if j is None:
                break
            ptr = j + 1
            rec = records[j]
            if judge and rec.get("ok") and delta_is_empty(rec.get("delta")) and index not in found:
                found.append(index)
    return found


def node_check_prefix(index, total, name):
    return json.dumps(f"{NODE_CHECK_TAG} node {index}/{total} {name} failed: ", ensure_ascii=False)


def node_check_wrap(call, index, total, name):
    return f"try {{ {call} }} catch (__e) {{ __chainErr({node_check_prefix(index, total, name)}, __e); }}"


def strip_node_checks(code):
    """去掉 node_check 插入的 try/catch 与辅助函数，得到不加检查时的代码（给"上一轮代码"用）。"""
    if not code or "__chainErr(" not in code:
        return code
    kept = [ln for ln in code.split("\n") if ln not in _NODE_HELPERS]
    return _NODE_WRAP_RE.sub(lambda m: m.group(1), "\n".join(kept))


def _strip_handle_error_tail(rest):
    """index.js handleError 在 err.message 后面接 "\\n" + ("at … in your code" 或空)，去掉这一截。"""
    lines = rest.split("\n")
    if len(lines) > 1 and (
        lines[-1] == ""
        or (lines[-1].startswith("at ") and lines[-1].endswith(" in your code"))
    ):
        lines = lines[:-1]
    return "\n".join(lines)


def parse_node_failure(events, nodes, generated=None, max_chars=1000):
    """从本步 onError 事件里解析节点检查抛出的结构化失败；没有则返回 None。

    只认序号、总数、技能名都与本步执行的节点对得上的那条（取最后一条），防止把别的
    步骤残留的报错算到本步头上。返回
    `{node, total, skill, kind: "error", message, source: "library"|"gap"}`。
    """
    names = [node.get("skill") for node in nodes or [] if node.get("skill")]
    generated = generated or {}
    found = None
    for item in events or []:
        if not isinstance(item, (list, tuple)) or len(item) < 2 or item[0] != "onError":
            continue
        data = item[1]
        text = data.get("onError") if isinstance(data, dict) else None
        if not isinstance(text, str):
            continue
        for match in _NODE_FAIL_RE.finditer(text):
            index, total, name = int(match.group(1)), int(match.group(2)), match.group(3)
            if total != len(names) or not 1 <= index <= total or names[index - 1] != name:
                continue
            found = {
                "node": index,
                "total": total,
                "skill": name,
                "kind": "error",
                "message": _strip_handle_error_tail(text[match.end():])[:max_chars],
                "source": "gap" if name in generated else "library",
            }
            break
    return found


def defined_function_names(code):
    """代码里以函数声明形式定义的名字（同名重写时不给它挂禁用桩）。"""
    return set(_FUNC_DECL_RE.findall(code or ""))


def node_disabled_stub_js(name, reason=""):
    """v29 `--chain-node-repair`：本任务内禁用被选为返修目标的库技能（报错的，或无报错判败时的非放置类疑似原因；
    与 noeffect_disabled_stub_js 同类的桩）。"""
    why = reason or "a chain node"
    msg = (
        f"{name} is disabled for the rest of this task: it failed as {why} earlier in this task. "
        "Write new code instead of calling it."
    )
    return f"{name} = function () {{ throw new Error({json.dumps(msg, ensure_ascii=False)}); }};"


def exec_line(node, guard="", check=None):
    """`check=(index, total)` 时套节点检查（v29）；None 时与 v28 逐字节相同。"""
    name = node.get("skill")
    if not name:
        raise ValueError("node has no skill to assemble")
    repeat = node.get("repeat") if node.get("repeat") is not None else 1
    try:
        repeat = int(repeat)
    except (TypeError, ValueError):
        repeat = 1
    if repeat < 1:
        repeat = 1
    call = call_expr(name, node.get("args") or {}, node.get("signature"))
    if check is not None:
        index, total = check
        call = node_check_wrap(call, index, total, name)
    if repeat == 1:
        return f"{guard}{call}"
    return f"for (let __i=0; __i<{repeat}; __i++) {{ {guard}{call} }}"


def assemble(nodes, generated=None, time_budget_s=0, node_check=False):
    """Return `{program_code, exec_code, program_names}`.

    `generated` maps newly written function names to their source. Reused
    library skills are only invoked, not redefined.

    v29 `node_check=True`：每个节点套 try/catch，抛错时带上节点序号重新抛出。
    False 时输出与 v28 逐字节相同。
    """
    generated = generated or {}
    program_names = []
    seen = set()
    chunks = []
    exec_lines = []
    total = sum(1 for node in nodes or [] if node.get("skill")) if node_check else 0
    for node in nodes or []:
        name = node.get("skill")
        if not name:
            continue
        if name in generated and name not in seen:
            chunks.append(generated[name].rstrip())
            program_names.append(name)
            seen.add(name)
        guard = budget_guard(time_budget_s, len(exec_lines) + 1, name)
        check = (len(exec_lines) + 1, total) if node_check else None
        exec_lines.append(exec_line(node, guard, check))
    if node_check and exec_lines:
        exec_lines[0:0] = list(_NODE_HELPERS)
    if time_budget_s and exec_lines:
        exec_lines.insert(0, "const __chainT0 = Date.now();")
    program_code = "\n\n".join(chunks)
    exec_code = "\n".join(exec_lines)
    if exec_code:
        exec_code += "\n"
    return {
        "program_code": program_code,
        "exec_code": exec_code,
        "program_names": program_names,
    }
