from voyager.utils import parse_llm_json
import os
import re
import time
import types

import voyager.utils as U
from langchain.prompts import SystemMessagePromptTemplate
from langchain.schema import AIMessage, HumanMessage, SystemMessage

from voyager.prompts import load_prompt
from voyager.control_primitives_context import load_control_primitives_context
from voyager.control_primitives_extra_context import load_extra_base_skills_context
from voyager.control_primitives_extra_context import load_extra_base_skill_rules
from voyager.intent import (
    extract_intent_from_text,
    infer_task_intent,
)
from voyager.llm_meta import create_chat_openai
from voyager.utils.js_parse import parse_program, parse_invocation, JsParseError, JsSyntaxError

# skill_api_prompt 开关用的技能签名解析：从源码里取 `async function NAME(bot) {`
# 形式的函数名。解析不出来的技能跳过签名行，但完整源码仍然附进小节。
_SKILL_SIGNATURE_PATTERN = re.compile(r"async\s+function\s+(\w+)\s*\(\s*bot\s*\)")

# 解析器基础设施故障的错误串前缀。voyager.step() 用它区分
# "仪器坏了"（parse_fail_infra）与"模型写错代码"（parse_fail_model）。
PARSER_INFRA_PREFIX = "[parser-infra] "

# skill_params 关闭时填回模板的原文。必须与改占位符之前的规则 1 / 规则 3 逐字符相同。
_PARAM_RULE_OFF = "Write an async function taking the bot as the only argument."
_GENERIC_RULE_OFF = (
    "Your function will be reused for building more complex functions. Therefore, you should make it generic and reusable. "
    "You should not make strong assumption about the inventory (as it may be changed at a later time), and therefore you should always check whether you have the required items before using them. "
    "If not, you should first collect the required items and reuse the above useful programs."
)
_PARAM_RULE_ON = (
    "Write an async function whose first parameter must be bot. Any further parameters must have default values. "
    "Make quantity and the target block, entity, or item into parameters. "
    "Name the function by capability (mineCopperOre, not mineFiveCopperOre). "
    "Do not bake a specific quantity into the function body."
)
_GENERIC_RULE_ON = (
    _GENERIC_RULE_OFF
    + " Keep the skill parameterized: callers pass the current task's quantity and target; do not specialize the function to one count."
)
_INVOCATION_BLOCK_ON = (
    "\nInvocation:\n"
    "```javascript\n"
    "await yourMainFunctionName(bot, <args for the current task>);\n"
    "```\n"
)
_CODE_BLOCK_RE = re.compile(r"```(?:javascript|js)(.*?)```", re.DOTALL)


def _ensure_semicolon(src):
    text = (src or "").strip()
    if text and not text.endswith(";"):
        text += ";"
    return text


def _skill_api_name(skill_code):
    # 2026-09-11 审计 03 C-07：技能源码 = program_code = helper 在前、主函数在后
    # （action_response_format 要求），原来 .search() 取第一个匹配，helper 恰好
    # 也是 (bot) 单参时签名行会写成 helper 名。改取**最后一个**，与解析器
    # "最后一个 async 函数是主函数"（process_ai_message）同一规则。
    matches = _SKILL_SIGNATURE_PATTERN.findall(skill_code)
    return matches[-1] if matches else None


def _skill_signature_line(skill_code, skill_params=False):
    if not skill_params:
        name = _skill_api_name(skill_code)
        return f"- await {name}(bot);" if name else None
    try:
        parsed = parse_program(skill_code)
    except Exception:
        return None
    main = None
    for function in reversed(parsed["functions"]):
        if function["type"] == "AsyncFunctionDeclaration":
            main = function
            break
    if main is None:
        return None
    parts = []
    for i, param in enumerate(main["params"]):
        pname = param.name or ("bot" if i == 0 else "")
        default = getattr(param, "default", None)
        if i == 0 or default is None:
            parts.append(pname)
        else:
            parts.append(f"{pname} = {default}")
    return f"- await {main['name']}({', '.join(parts)});"


def _render_skill_api_section(skills, skill_params=False):
    signatures = [
        line
        for line in (
            _skill_signature_line(code, skill_params=skill_params) for code in skills
        )
        if line
    ]
    parts = [
        "Here are the skills you have already written and saved. They are ALREADY LOADED in your",
        "execution environment and can be called directly by name, exactly like the programs above.",
        "",
    ]
    if signatures:
        parts.append("Callable skills (signatures):")
        parts.extend(signatures)
        parts.append("")
    parts.append("Full source of your saved skills:")
    parts.append("")
    parts.append("\n\n".join(skills))
    return "\n".join(parts)


class ActionAgent:
    def __init__(
        self,
        model_name="gpt-3.5-turbo",
        temperature=0,
        request_timout=120,
        ckpt_dir="ckpt",
        resume=False,
        chat_log=True,
        execution_error=True,
        thinking="disabled",
        llm_audit=None,
        skill_api_prompt=False,
        skill_params=False,
        extra_base_skills=None,
        v30_shared_fixes=False,
        v30_knowledge_text=False,
    ):
        # 09-22：从技能库提炼、提升到基础层的组合技能名单。None/空 = 关，
        # 关时本文件行为与改动前逐字节相同。
        self.extra_base_skills = list(extra_base_skills or [])
        self.v30_shared_fixes = bool(v30_shared_fixes)
        self.v30_knowledge_text = bool(v30_knowledge_text)
        self.ckpt_dir = ckpt_dir
        self.chat_log = chat_log
        self.execution_error = execution_error
        self.skill_api_prompt = skill_api_prompt
        self.skill_params = bool(skill_params)
        U.f_mkdir(f"{ckpt_dir}/action")
        if resume:
            print(f"\033[32mLoading Action Agent from {ckpt_dir}/action\033[0m")
            self.chest_memory = U.load_json(f"{ckpt_dir}/action/chest_memory.json")
        else:
            self.chest_memory = {}
        self.llm = create_chat_openai(
            model_name=model_name,
            temperature=temperature,
            request_timeout=request_timout,
            thinking=thinking,
            audit=llm_audit,
            agent="action",
        )

    def update_chest_memory(self, chests):
        for position, chest in chests.items():
            if position in self.chest_memory:
                if isinstance(chest, dict):
                    self.chest_memory[position] = chest
                if chest == "Invalid":
                    print(
                        f"\033[32mAction Agent removing chest {position}: {chest}\033[0m"
                    )
                    self.chest_memory.pop(position)
            else:
                if chest != "Invalid":
                    print(f"\033[32mAction Agent saving chest {position}: {chest}\033[0m")
                    self.chest_memory[position] = chest
        U.dump_json(self.chest_memory, f"{self.ckpt_dir}/action/chest_memory.json")

    def render_chest_observation(self):
        chests = []
        for chest_position, chest in self.chest_memory.items():
            if isinstance(chest, dict) and len(chest) > 0:
                chests.append(f"{chest_position}: {chest}")
        for chest_position, chest in self.chest_memory.items():
            if isinstance(chest, dict) and len(chest) == 0:
                chests.append(f"{chest_position}: Empty")
        for chest_position, chest in self.chest_memory.items():
            if isinstance(chest, str):
                assert chest == "Unknown"
                chests.append(f"{chest_position}: Unknown items inside")
        assert len(chests) == len(self.chest_memory)
        if chests:
            chests = "\n".join(chests)
            return f"Chests:\n{chests}\n\n"
        else:
            return f"Chests: None\n\n"

    def render_system_message(self, skills=[]):
        system_template = load_prompt("action_template")
        # FIXME: Hardcoded control_primitives
        base_skills = [
            "exploreUntil",
            "mineBlock",
            "craftItem",
            "placeItem",
            "smeltItem",
            "killMob",
        ]
        if not self.llm.model_name == "gpt-3.5-turbo":
            base_skills += [
                "useChest",
                "mineflayer",
            ]
        if self.skill_api_prompt and skills:
            # 技能 API 呈现（实验开关）：{programs} 只留控制原语，检索到的技能移入
            # 独立的 {skill_api_section} 小节——先列签名、注明已加载可按名调用，
            # 再原样附完整源码。给模型的技能信息量不变：内容、顺序、全文均与
            # 开关关闭时一致，只改变呈现方式。
            programs = "\n\n".join(
                load_control_primitives_context(base_skills)
                + load_extra_base_skills_context(self.extra_base_skills)
            )
            skill_api_section = _render_skill_api_section(
                skills, skill_params=getattr(self, "skill_params", False)
            )
            skill_api_rule = (
                "        - If one of your saved skills above accomplishes a sub-step of the task, "
                "call it by name instead of rewriting it.\n"
            )
        else:
            programs = "\n\n".join(
                load_control_primitives_context(base_skills)
                + load_extra_base_skills_context(self.extra_base_skills)
                + skills
            )
            skill_api_section = ""
            skill_api_rule = ""
        if getattr(self, "v30_knowledge_text", False):
            from voyager.knowledge_arm import (
                KNOWLEDGE_ARM_PATH,
                load_knowledge_arm_text,
                write_knowledge_arm_text,
            )

            if not os.path.isfile(KNOWLEDGE_ARM_PATH):
                write_knowledge_arm_text()
            knowledge = load_knowledge_arm_text().strip()
            if knowledge:
                programs = programs + "\n\n" + knowledge
        if getattr(self, "skill_params", False):
            param_rule = _PARAM_RULE_ON
            generic_rule = _GENERIC_RULE_ON
            invocation_block = _INVOCATION_BLOCK_ON
        else:
            param_rule = _PARAM_RULE_OFF
            generic_rule = _GENERIC_RULE_OFF
            invocation_block = ""
        response_format = load_prompt("action_response_format").replace(
            "{invocation_block}", invocation_block
        )
        system_message_prompt = SystemMessagePromptTemplate.from_template(
            system_template
        )
        # 额外基础技能的「点名」行。不开开关时是空串，提示词逐字节不变。
        skill_api_rule = (load_extra_base_skill_rules(self.extra_base_skills)
                          + skill_api_rule)
        system_message = system_message_prompt.format(
            programs=programs,
            response_format=response_format,
            skill_api_section=skill_api_section,
            skill_api_rule=skill_api_rule,
            param_rule=param_rule,
            generic_rule=generic_rule,
        )
        assert isinstance(system_message, SystemMessage)
        return system_message

    def render_human_message(
        self, *, events, code="", task="", context="", critique="", task_intent=None
    ):
        chat_messages = []
        error_messages = []
        # FIXME: damage_messages is not used
        damage_messages = []
        assert events[-1][0] == "observe", "Last event must be observe"
        for i, (event_type, event) in enumerate(events):
            if event_type == "onChat":
                chat_messages.append(event["onChat"])
            elif event_type == "onError":
                error_messages.append(event["onError"])
            elif event_type == "onDamage":
                damage_messages.append(event["onDamage"])
            elif event_type == "observe":
                biome = event["status"]["biome"]
                time_of_day = event["status"]["timeOfDay"]
                voxels = event["voxels"]
                entities = event["status"]["entities"]
                health = event["status"]["health"]
                hunger = event["status"]["food"]
                position = event["status"]["position"]
                equipment = event["status"]["equipment"]
                inventory_used = event["status"]["inventoryUsed"]
                inventory = event["inventory"]
                assert i == len(events) - 1, "observe must be the last event"

        observation = ""

        if code:
            observation += f"Code from the last round:\n{code}\n\n"
        else:
            observation += f"Code from the last round: No code in the first round\n\n"

        if self.execution_error:
            if error_messages:
                error = "\n".join(error_messages)
                observation += f"Execution error:\n{error}\n\n"
            else:
                observation += f"Execution error: No error\n\n"

        if self.chat_log:
            if chat_messages:
                chat_log = "\n".join(chat_messages)
                observation += f"Chat log: {chat_log}\n\n"
            else:
                observation += f"Chat log: None\n\n"

        observation += f"Biome: {biome}\n\n"

        observation += f"Time: {time_of_day}\n\n"

        if voxels:
            observation += f"Nearby blocks: {', '.join(voxels)}\n\n"
        else:
            observation += f"Nearby blocks: None\n\n"

        if entities:
            nearby_entities = [
                k for k, v in sorted(entities.items(), key=lambda x: x[1])
            ]
            observation += f"Nearby entities (nearest to farthest): {', '.join(nearby_entities)}\n\n"
        else:
            observation += f"Nearby entities (nearest to farthest): None\n\n"

        observation += f"Health: {health:.1f}/20\n\n"

        observation += f"Hunger: {hunger:.1f}/20\n\n"

        observation += f"Position: x={position['x']:.1f}, y={position['y']:.1f}, z={position['z']:.1f}\n\n"

        observation += f"Equipment: {equipment}\n\n"

        if inventory:
            observation += f"Inventory ({inventory_used}/36): {inventory}\n\n"
        else:
            observation += f"Inventory ({inventory_used}/36): Empty\n\n"

        if not (
            task == "Place and deposit useless items into a chest"
            or task.startswith("Deposit useless items into the chest at")
        ):
            observation += self.render_chest_observation()

        observation += f"Task: {task}\n\n"

        if task_intent:
            observation += f"Task Intent: {U.json_dumps(task_intent)}\n\n"

        if context:
            observation += f"Context: {context}\n\n"
        else:
            observation += f"Context: None\n\n"

        if critique:
            observation += f"Critique: {critique}\n\n"
        else:
            observation += f"Critique: None\n\n"

        return HumanMessage(content=observation)

    def process_ai_message(self, message, task=""):
        assert isinstance(message, AIMessage)

        retry = 3
        error = None
        infra_failure = False
        skill_params = getattr(self, "skill_params", False)
        while retry > 0:
            try:
                code_pattern = _CODE_BLOCK_RE
                invocation_region = None
                if skill_params:
                    inv_idx = message.content.find("Invocation:")
                    if inv_idx < 0:
                        code_region = message.content
                    else:
                        code_region = message.content[:inv_idx]
                        invocation_region = message.content[
                            inv_idx + len("Invocation:") :
                        ]
                    code = "\n".join(code_pattern.findall(code_region))
                else:
                    code = "\n".join(code_pattern.findall(message.content))
                # 解析走一次性 node 子进程，不走 JSPyBridge 常驻桥——后者的属性
                # 访问有 10 秒硬超时，2026-09-02 起 86% 的回复卡在那里。
                # 详见 voyager/utils/js_parse.py。
                parsed = parse_program(code)
                assert parsed["n_body"] > 0, "No functions found"
                functions = parsed["functions"]
                if getattr(self, "v30_shared_fixes", False):
                    from voyager.js_main import select_main

                    root_name, cg_err = select_main(code, call_graph=True)
                    if cg_err:
                        raise AssertionError(cg_err)
                    main_function = None
                    for function in functions:
                        if function.get("name") == root_name:
                            main_function = function
                            break
                    if main_function is None:
                        raise AssertionError(
                            "No async function found. Your main function must be async."
                        )
                else:
                    # find the last async function
                    main_function = None
                    for function in reversed(functions):
                        if function["type"] == "AsyncFunctionDeclaration":
                            main_function = function
                            break
                assert (
                    main_function is not None
                ), "No async function found. Your main function must be async."
                if not skill_params:
                    assert (
                        len(main_function["params"]) == 1
                        and main_function["params"][0].name == "bot"
                    ), f"Main function {main_function['name']} must take a single argument named 'bot'"
                else:
                    params = main_function["params"]
                    if not params or params[0].name != "bot":
                        raise AssertionError(
                            f"Main function {main_function['name']}: first parameter must be named 'bot'"
                        )
                    for param in params[1:]:
                        if getattr(param, "kind", None) != "assignment":
                            raise AssertionError(
                                f"Main function {main_function['name']} parameter "
                                f"'{param.name}' must have a default value"
                            )
                skill_intent = extract_intent_from_text(
                    message.content, "Skill Intent", source="action"
                )
                if not skill_intent.get("target_items"):
                    skill_intent = infer_task_intent(main_function["name"], source="function_name")
                if not skill_intent.get("target_items"):
                    skill_intent = infer_task_intent(task, source="task_fallback")
                program_code = "\n\n".join(function["body"] for function in functions)
                if not skill_params:
                    exec_code = f"await {main_function['name']}(bot);"
                    return {
                        "program_code": program_code,
                        "program_name": main_function["name"],
                        "exec_code": exec_code,
                        "skill_intent": skill_intent,
                    }
                if invocation_region is None:
                    raise AssertionError("Missing Invocation block")
                inv_match = code_pattern.search(invocation_region)
                if inv_match is None:
                    raise AssertionError("Missing Invocation block")
                inv_src = inv_match.group(1)
                parsed_inv = parse_invocation(inv_src)
                inv_name = parsed_inv.get("name")
                if inv_name != main_function["name"]:
                    raise AssertionError(
                        f"Invocation must call main function {main_function['name']}, got {inv_name}"
                    )
                inv_args = parsed_inv.get("args") or []
                if (
                    not inv_args
                    or inv_args[0].get("kind") != "identifier"
                    or inv_args[0].get("name") != "bot"
                ):
                    raise AssertionError(
                        "Invocation first argument must be identifier 'bot'"
                    )
                for arg in inv_args[1:]:
                    if arg.get("kind") != "literal":
                        raise AssertionError("Invocation args must be literals")
                exec_code = _ensure_semicolon(inv_src)
                exec_args = [arg.get("value") for arg in inv_args[1:]]
                signature = {
                    "params": [
                        {
                            "name": param.name,
                            "default": getattr(param, "default", None),
                        }
                        for param in main_function["params"]
                    ]
                }
                return {
                    "program_code": program_code,
                    "program_name": main_function["name"],
                    "exec_code": exec_code,
                    "skill_intent": skill_intent,
                    "signature": signature,
                    "exec_args": exec_args,
                }
            except JsSyntaxError as e:
                # 模型代码语法错：归模型
                retry -= 1
                error = e
                infra_failure = False
                time.sleep(1)
            except JsParseError as e:
                # 2026-09-11 审计 P0-2：node 缺 / babel 缺 / 子进程超时 / 崩溃。
                # 以前和上面走同一个 except，同一串错误文本，09-02 吞掉 86% 回复
                # 时没人看得出来。现在带前缀返回，voyager.step() 分开计数落盘。
                retry -= 1
                error = e
                infra_failure = True
                time.sleep(1)
            except Exception as e:
                retry -= 1
                error = e
                infra_failure = False
                time.sleep(1)
        prefix = PARSER_INFRA_PREFIX if infra_failure else ""
        return f"{prefix}Error parsing action response (before program execution): {error}"

    def summarize_chatlog(self, events):
        def filter_item(message: str):
            craft_pattern = r"I cannot make \w+ because I need: (.*)"
            craft_pattern2 = (
                r"I cannot make \w+ because there is no crafting table nearby"
            )
            mine_pattern = r"I need at least a (.*) to mine \w+!"
            if re.match(craft_pattern, message):
                return re.match(craft_pattern, message).groups()[0]
            elif re.match(craft_pattern2, message):
                return "a nearby crafting table"
            elif re.match(mine_pattern, message):
                return re.match(mine_pattern, message).groups()[0]
            else:
                return ""

        chatlog = set()
        for event_type, event in events:
            if event_type == "onChat":
                item = filter_item(event["onChat"])
                if item:
                    chatlog.add(item)
        return "I also need " + ", ".join(chatlog) + "." if chatlog else ""
