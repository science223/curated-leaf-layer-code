"""用一次性 node 子进程解析生成的 JavaScript，取代 JSPyBridge 常驻桥。

背景（2026-09-03 查实）：
    原来 `process_ai_message` 走 `from javascript import require`，每解析一次
    要跨进程取一次 `require` 属性，而 JSPyBridge 在 `proxy.py:39` 把这类属性
    访问的超时写死为 10 秒。八月 1126 次模型回复零超时；2026-09-02 起同样的
    代码开始大面积超时——九月 614 次回复里 525 次卡在
    "Timed out accessing 'require'"（86%）。超时发生在**执行生成代码之前**，
    所以那一轮必然失败，与模型写得对不对无关。

    换成一次性子进程：没有常驻桥、没有属性级往返、没有 10 秒硬超时，
    进程退出即释放全部状态。代价是每次多 ~0.2 秒的 node 启动。

输出与原实现逐字段对齐，调用方不需要改动。
"""

import json
import os
import subprocess
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPT = os.path.join(_HERE, "parse_functions.js")


def _node_path():
    """定位 @babel/core。

    首选 `voyager/utils/node_modules`（本目录的 package.json 声明，`npm install` 可复现）。
    **不要依赖 JSPyBridge 的那一份**：它的 `js/package.json` 虽然写着 `@babel/core`，
    但那是第一次 `require` 时**联网现装**的产物，不随 pip 包分发——
    换一台机器 `pip install javascript` 之后那里是空的。
    既然本模块已经不再 import javascript，也就不会再触发它那次现装。

    返回 None 表示"本目录就能解析出来"，不必设 NODE_PATH。
    """
    local = os.path.join(_HERE, "node_modules")
    if os.path.isdir(os.path.join(local, "@babel", "core")):
        return None  # parse_functions.js 与它同级，node 自己就找得到

    candidates = [os.path.join(os.path.dirname(_HERE), "env", "mineflayer", "node_modules")]
    try:
        import javascript  # noqa: F401  仅用于定位安装目录，不启动桥

        candidates.append(
            os.path.join(os.path.dirname(os.path.abspath(javascript.__file__)), "js", "node_modules")
        )
    except Exception:
        pass

    found = [c for c in candidates if os.path.isdir(os.path.join(c, "@babel", "core"))]
    if not found:
        raise RuntimeError(
            "找不到 @babel/core。请在 voyager/utils/ 下跑一次 `npm install`。"
            "找过：" + os.pathsep.join([local] + candidates)
        )
    return os.pathsep.join(found)


class JsParseError(Exception):
    """解析器基础设施故障（找不到 node、babel 缺失、子进程超时/崩溃、输出不是 JSON）。

    2026-09-11 审计 P0-2：这类故障与"模型自己写错代码"必须分开——
    前者是仪器坏了，后者是被测对象的行为。语法错误用子类 JsSyntaxError。
    """


class JsSyntaxError(JsParseError):
    """模型代码本身解析不过（babel 报的语法错误）。归模型，不归仪器。"""


def parse_program(code, timeout=60):
    """解析一段 JS，返回 {"n_body": int, "functions": [...]}。

    functions 里每一项：name / type / body / params，
    params 元素带 `.name` 属性，沿用原来 `params[0].name` 的写法。
    WP-E 起 params 另有 default（默认值源码文本或 None）与 kind
    （identifier / assignment / rest / other）；关闭 skill_params 时调用方不读它们。
    """
    data = _run_node_parse(code, timeout=timeout, parse_mode="program")
    if "error" in data:
        raise JsSyntaxError(data["error"])
    for fn in data["functions"]:
        fn["params"] = [types.SimpleNamespace(**p) for p in fn["params"]]
    return data


def parse_call_graph(code, timeout=60):
    """PARSE_MODE=callgraph: FunctionDeclaration + 顶层箭头/函数表达式，带 refs。"""
    data = _run_node_parse(code, timeout=timeout, parse_mode="callgraph")
    if "error" in data:
        raise JsSyntaxError(data["error"])
    for fn in data.get("functions") or []:
        fn["params"] = [types.SimpleNamespace(**p) for p in (fn.get("params") or [])]
    return data


def parse_invocation(code, timeout=60):
    """解析恰好一条 `await NAME(args);` 表达式语句。

    成功时返回 {"name": str, "args": [{"kind": ..., ...}, ...]}。
    kind 为 identifier（带 name）、literal（带 JSON 值）或 other。
    """
    data = _run_node_parse(code, timeout=timeout, parse_mode="invocation")
    if "error" in data:
        raise JsSyntaxError(data["error"])
    return data


def _run_node_parse(code, timeout=60, parse_mode="program"):
    env = dict(os.environ)
    np = _node_path()
    if np:
        env["NODE_PATH"] = np
    else:
        env.pop("NODE_PATH", None)
    # 解析是纯本地计算，不要让代理变量把 node 的任何请求引出去
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        env.pop(k, None)
    env["PARSE_MODE"] = parse_mode

    try:
        proc = subprocess.run(
            ["node", _SCRIPT],
            input=code,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        raise JsParseError(f"node 解析超时（{timeout}s）")
    except FileNotFoundError:
        raise JsParseError("找不到 node 可执行文件")

    if proc.returncode != 0:
        raise JsParseError(
            f"node 退出码 {proc.returncode}: {(proc.stderr or '').strip()[:500]}"
        )

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise JsParseError(
            f"解析器输出不是 JSON: {(proc.stdout or '')[:200]!r} "
            f"stderr={(proc.stderr or '').strip()[:200]!r}"
        )
