"""Drop-in stand-in for voyager.env.bridge.VoyagerEnv.

Does not start mineflayer, bind a port, or talk to Minecraft.
Behaviour is driven by `// FAKE:` comments in the generated code.
"""
from __future__ import annotations

import copy
import re

from voyager.env.bridge import StepExecutionTimeout

_GAIN_RE = re.compile(r"//\s*FAKE:\s*gain\s+item=(\S+)\s+count=(\d+)")
_ERROR_RE = re.compile(r"//\s*FAKE:\s*error\s+(.+)")
_TIMEOUT_RE = re.compile(r"//\s*FAKE:\s*timeout\b")
_CRASH_RE = re.compile(r"//\s*FAKE:\s*crash\b")
_AWAIT_RE = re.compile(r"await\s+(\w+)\s*\(\s*bot")
_ASYNC_FN_RE = re.compile(r"async\s+function\s+(\w+)\s*\(")
_ORIG_RE = re.compile(r"const\s+__orig_")
_FN_HEAD_RE = re.compile(r"async\s+function\s+(\w+)\s*\([^)]*\)\s*\{")
_FOR_REPEAT_RE = re.compile(
    r"for\s*\(\s*let\s+__i\s*=\s*0;\s*__i\s*<\s*(\d+);\s*__i\+\+\s*\)\s*\{([^{}]*)\}"
)

_CRASH_MESSAGE = (
    "Failed to step Minecraft server (HTTP 503): {'error': 'bot disconnected: kicked'}"
)
_MAX_NEST = 3


def _default_status(inventory, equipment=None, position=None):
    used = sum(1 for count in inventory.values() if count)
    pos = position if isinstance(position, dict) else {"x": 0.0, "y": 64.0, "z": 0.0}
    equip = equipment if isinstance(equipment, list) and len(equipment) == 6 else [None] * 6
    return {
        "health": 20.0,
        "food": 20.0,
        "saturation": 5.0,
        "oxygen": 20.0,
        "position": {"x": float(pos.get("x", 0)), "y": float(pos.get("y", 64)), "z": float(pos.get("z", 0))},
        "velocity": {"x": 0.0, "y": 0.0, "z": 0.0},
        "yaw": 0.0,
        "pitch": 0.0,
        "onGround": True,
        "equipment": list(equip),
        "name": "bot",
        "biome": "plains",
        "entities": {},
        "timeOfDay": "day",
        "inventoryUsed": used,
        "elapsedTime": 1,
    }


def _skip_body(src, start):
    depth = 1
    i = start
    while i < len(src) and depth:
        ch = src[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    return i


def _extract_function_bodies(src):
    bodies = {}
    for match in _FN_HEAD_RE.finditer(src or ""):
        name = match.group(1)
        end = _skip_body(src, match.end())
        bodies[name] = src[match.end() : end - 1] if end > match.end() else ""
    return bodies


def _expand_repeat_loops(src):
    """Expand assembler `for (let __i=0; __i<k; __i++) { await …; }` to k copies."""

    def repl(match):
        count = int(match.group(1))
        body = match.group(2)
        return body * count

    text = src or ""
    previous = None
    while previous != text:
        previous = text
        text = _FOR_REPEAT_RE.sub(repl, text)
    return text


def _strip_function_bodies(src):
    src = src or ""
    parts = []
    i = 0
    for match in _FN_HEAD_RE.finditer(src):
        parts.append(src[i : match.end()])
        end = _skip_body(src, match.end())
        parts.append("}")
        i = end
    parts.append(src[i:])
    return "".join(parts)


def _snap(inventory):
    return dict(inventory)


def _delta(before, after):
    out = {}
    keys = set(before) | set(after)
    for key in keys:
        diff = int(after.get(key, 0)) - int(before.get(key, 0))
        if diff:
            out[key] = diff
    return out


class FakeEnv:
    """Mimics the VoyagerEnv public surface used by Voyager.learn/reset/step."""

    def __init__(
        self,
        mc_port=None,
        azure_login=None,
        server_host="http://127.0.0.1",
        server_port=3000,
        request_timeout=150,
        log_path="./logs",
        **kwargs,
    ):
        self.mc_port = mc_port
        self.azure_login = azure_login
        self.server_host = server_host
        self.server_port = server_port
        self.request_timeout = request_timeout
        self.log_path = log_path
        self.extra_kwargs = kwargs
        # 真 VoyagerEnv 自 v26 有此属性（run_meta 记录）；缺了 smoke_learn_offline 在建 run_meta 时崩
        self.pause_verify = bool(kwargs.get("pause_verify", False))
        self.inventory = {}
        self.equipment = [None] * 6
        self.position = {"x": 0.0, "y": 64.0, "z": 0.0}
        self.voxels = ["oak_log", "dirt", "grass_block"]
        self.block_records = []
        self.nearby_chests = {}
        self.has_reset = False
        self.calls = []
        self.lifecycle = []
        self.server_paused = False
        self._probe_installed = None

    def _payload(self, extra=None):
        data = {
            "voxels": list(self.voxels),
            "status": _default_status(self.inventory, self.equipment, self.position),
            "inventory": dict(self.inventory),
            "nearbyChests": dict(self.nearby_chests),
            "blockRecords": list(self.block_records),
        }
        if extra is not None:
            kind, text = extra
            data[kind] = text
        return data

    def _events(self, extras=None, skill_calls=None, skill_outcomes=None):
        events = []
        for extra in extras or []:
            events.append([extra[0], self._payload(extra)])
        observe = self._payload()
        if skill_calls is not None:
            observe["skillCalls"] = skill_calls
        if skill_outcomes is not None:
            observe["skillOutcomes"] = skill_outcomes
        events.append(["observe", observe])
        return events

    def reset(self, *, seed=None, options=None):
        options = options or {}
        mode = options.get("mode", "hard")
        inventory = options.get("inventory") or {}
        if mode == "hard" and inventory:
            self.inventory = dict(inventory)
        else:
            self.inventory = {}
        if options.get("equipment"):
            equipment = list(options["equipment"])
            if len(equipment) < 6:
                equipment = equipment + [None] * (6 - len(equipment))
            self.equipment = equipment[:6]
        if options.get("position"):
            self.position = copy.deepcopy(options["position"])
        self.has_reset = True
        self.lifecycle.append(("reset", {"seed": seed, "options": options}))
        # After a timeout, learn() replaces last_events with reset() output.
        # Timeout is not env_crash, so _check_probe still runs; without a
        # sentinel it increments probe_missing_rounds. Keep the last installed
        # sentinel on reset events once a probed step has run.
        skill_calls = None
        if self._probe_installed is not None:
            skill_calls = {"__probe_installed__": self._probe_installed}
        return self._events(skill_calls=skill_calls)

    def _apply_gains(self, text):
        for match in _GAIN_RE.finditer(text or ""):
            name = match.group(1)
            count = int(match.group(2))
            self.inventory[name] = self.inventory.get(name, 0) + count

    def _run_skill(self, name, bodies, defined, depth, stack, skill_calls, outcomes, error_names):
        before = _snap(self.inventory)
        rec = {"name": name, "ok": True, "err": None, "t_ms": 0, "delta": {}}
        outcomes.append(rec)
        skill_calls[name] = skill_calls.get(name, 0) + 1
        body = bodies.get(name, "")
        if depth < _MAX_NEST and name not in stack:
            for child in _AWAIT_RE.findall(body):
                if child in defined:
                    self._run_skill(
                        child, bodies, defined, depth + 1, stack + (name,),
                        skill_calls, outcomes, error_names,
                    )
        self._apply_gains(body)
        if name in error_names:
            rec["ok"] = False
            rec["err"] = error_names[name]
        rec["delta"] = _delta(before, self.inventory)

    def step(self, code, programs=""):
        if not self.has_reset:
            raise RuntimeError("Environment has not been reset yet")
        self.calls.append((code, programs))
        code = code or ""
        programs = programs or ""

        if _TIMEOUT_RE.search(code):
            raise StepExecutionTimeout(
                f"生成的代码在 {self.request_timeout}s 内没跑完（桥接仍在响应）。"
                "按任务失败处理，不计入 env_dead_after。"
            )
        if _CRASH_RE.search(code):
            raise RuntimeError(_CRASH_MESSAGE)

        extras = []
        error_text = None
        for match in _ERROR_RE.finditer(code):
            error_text = match.group(1).strip()
            extras.append(("onError", error_text))

        skill_calls = None
        skill_outcomes = None
        if "__probe_installed__" in programs:
            wrapped = len(_ORIG_RE.findall(programs))
            self._probe_installed = wrapped
            bodies = _extract_function_bodies(programs)
            bodies.update(_extract_function_bodies(code))
            defined = set(bodies) | set(_ASYNC_FN_RE.findall(programs)) | set(
                _ASYNC_FN_RE.findall(code)
            )
            skill_calls = {"__probe_installed__": wrapped}
            skill_outcomes = []
            top_level = _expand_repeat_loops(_strip_function_bodies(code))
            outer_names = [n for n in _AWAIT_RE.findall(top_level) if n in defined]
            error_names = {}
            if error_text:
                for name in outer_names:
                    error_names[name] = error_text
            for name in outer_names:
                self._run_skill(
                    name, bodies, defined, 0, (), skill_calls, skill_outcomes, error_names
                )
            # 顶层（函数体之外）的 FAKE:gain 仍计入物品栏，不属于某个技能。
            self._apply_gains(top_level)
        else:
            for match in _GAIN_RE.finditer(code):
                name = match.group(1)
                count = int(match.group(2))
                self.inventory[name] = self.inventory.get(name, 0) + count

        return self._events(
            extras=extras, skill_calls=skill_calls, skill_outcomes=skill_outcomes
        )

    def close(self):
        self.lifecycle.append("close")

    def pause(self):
        self.lifecycle.append("pause")
        self.server_paused = True
        return self.server_paused

    def unpause(self):
        self.lifecycle.append("unpause")
        self.server_paused = False
        return self.server_paused
