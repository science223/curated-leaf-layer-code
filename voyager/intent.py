import json
import os
import random
import re
import signal
import threading
import time

TERRAIN_NOISE_ITEMS = {
    "andesite",
    "cobblestone",
    "coarse_dirt",
    "deepslate",
    "diorite",
    "dirt",
    "flint",
    "granite",
    "grass_block",
    "gravel",
    "netherrack",
    "sand",
    "stone",
    "tuff",
}

_ITEM_ALIASES = {
    "logs": "log",
    "wood_log": "log",
    "wood_logs": "log",
    "wooden_log": "log",
    "wooden_logs": "log",
    "plank": "planks",
    "wood_plank": "planks",
    "wood_planks": "planks",
    "wooden_plank": "planks",
    "wooden_planks": "planks",
    "raw_cod": "cod",
    "raw_salmon": "salmon",
    "iron_ore": "raw_iron",
    "gold_ore": "raw_gold",
    "copper_ore": "raw_copper",
    "coal_ore": "coal",
    "diamond_ore": "diamond",
    "emerald_ore": "emerald",
    "lapis_ore": "lapis_lazuli",
    "lapis_lazuli_ore": "lapis_lazuli",
    "redstone_ore": "redstone",
}

_COUNT_WORDS = {
    "a",
    "an",
    "any",
    "eight",
    "eleven",
    "five",
    "four",
    "nine",
    "of",
    "one",
    "seven",
    "six",
    "some",
    "ten",
    "the",
    "thirteen",
    "three",
    "twelve",
    "two",
}


def _identifier_to_words(text):
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = text.replace("_", " ")
    return text


def normalize_item_name(item):
    if not item:
        return ""
    item = _identifier_to_words(str(item)).lower()
    item = item.replace("minecraft:", "")
    item = re.sub(r"[^a-z0-9_ ]+", " ", item)
    words = [
        word
        for word in item.replace("_", " ").split()
        if word and not word.isdigit() and word not in _COUNT_WORDS
    ]
    if not words:
        return ""
    name = "_".join(words)
    if name.endswith("s") and name not in {"planks", "sticks", "leaves"}:
        name = name[:-1]
    return _ITEM_ALIASES.get(name, name)


def _normalize_items(items):
    if not isinstance(items, list):
        return []
    normalized = []
    for item in items:
        name = normalize_item_name(item)
        if name and name not in normalized:
            normalized.append(name)
    return normalized


ACTION_ALIASES = {
    # 拿东西
    "obtain": "obtain",
    "collect": "obtain",
    "get": "obtain",
    "retrieve": "obtain",
    "obtain_via_command": "obtain",
    "gather": "obtain",
    # 开箱子 / 容器交互
    "loot": "chest",
    "loot_chest": "chest",
    "open": "chest",
    "deposit": "chest",
    "store": "chest",
    # 采集
    "mine": "mine",
    "dig": "mine",
    # 合成
    "craft": "craft",
    "make": "craft",
    # 熔炼与烹饪
    "smelt": "smelt",
    "cook": "cook",
    # 战斗
    "kill": "kill",
    "kill_and_collect": "kill",
    "attack": "kill",
    "hunt": "kill",
    # 装备
    "equip": "equip",
    "wear": "equip",
    "hold": "equip",
    # 放置
    "place": "place",
    "build": "place",
    # 进食
    "eat": "eat",
    "consume": "eat",
    # 空间/移动（借自 Odyssey spatial；指纹看不见这一类）
    "swim": "spatial",
    "escape": "spatial",
    "move": "spatial",
    "go": "spatial",
    "travel": "spatial",
    "explore": "spatial",
    "navigate": "spatial",
    "climb": "spatial",
    "descend": "spatial",
    "return": "spatial",
    # 其他已观测但不归上述类的
    "fish": "fish",
    "catch": "fish",
    "check": "check",
    "fill_bucket": "obtain",
    "fill": "obtain",
    "disable": "other",
}


CANONICAL_ACTIONS = set(ACTION_ALIASES.values()) | {"other"}


def canonical_action(raw):
    """把任意动作串归一化到固定分类。

    🔴 两条来源都必须走这里：
      - infer_task_intent()：正则从任务描述取的首词
      - normalize_intent()：模型自己吐的 JSON 里的 target_action
    第二条此前完全不过滤，模型能造出 loot_chest / kill_and_collect 这类新类，
    集合因此一直是开放的。未知一律落 other，不再动态造类。
    """
    key = str(raw or "").strip().lower()
    if not key:
        return ""
    # 🔴 幂等：本函数会被调用两次（infer_task_intent 归一化一次，
    # 它的返回值再经 normalize_intent 归一化一次）。chest / spatial 这类
    # 只是输出类名、不在别名表的键里，不先放行就会被第二遍打回 other。
    if key in CANONICAL_ACTIONS:
        return key
    if key in ACTION_ALIASES:
        return ACTION_ALIASES[key]
    # 模型常吐 kill_and_collect / obtain_via_command 这类复合词：取首段再查一次
    head = re.split(r"[^a-z]+", key)[0]
    if head and head in ACTION_ALIASES:
        return ACTION_ALIASES[head]
    return "other"


def normalize_intent(intent, source=None):
    if not isinstance(intent, dict):
        intent = {}
    normalized = {
        "target_action": canonical_action(intent.get("target_action", "")),
        "target_items": _normalize_items(intent.get("target_items", [])),
        "source": intent.get("source") or source or "unknown",
    }
    target_entities = _normalize_items(intent.get("target_entities", []))
    if target_entities:
        normalized["target_entities"] = target_entities
    return normalized


def infer_task_intent(task, source="rule"):
    task_text = _identifier_to_words(task or "").strip()
    task_lower = task_text.lower()
    action_match = re.match(r"([a-z]+)", task_lower)
    action = action_match.group(1) if action_match else ""
    # 🔴 固定分类（2026-09-03）。此前 target_action 是"任务描述的第一个词"，
    # 查不到别名就原样返回——是个开放集合，课程怎么措辞就产生什么类别。
    # 实测 1708 次运行里出现 20 个取值，三簇同义词各自裂开（见 实验方案.md §1.5）。
    # 现在关闭成固定 11 类 + other 兜底：未知动词不再造新类。
    #
    # spatial 一类借自 Odyssey 的能力域划分（liu2024odyssey）——移动/游泳/逃生
    # 这类技能不产生物品栏变化，执行效果指纹看不见它们，本方此前没有对应类别。
    target_action = canonical_action(action)
    target_text = re.sub(r"^[a-z]+\s+", "", task_lower)
    target_text = re.sub(r"\bat\b.*$", "", target_text).strip()
    target_item = normalize_item_name(target_text)
    target_items = []
    target_entities = []
    if target_item:
        if target_action == "kill":
            target_entities = [target_item]
        else:
            target_items = [target_item]
    return normalize_intent(
        {
            "target_action": target_action,
            "target_items": target_items,
            "target_entities": target_entities,
            "source": source,
        },
        source=source,
    )


def extract_intent_from_text(text, label, source):
    prefix = f"{label}:"
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped.lower().startswith(prefix.lower()):
            continue
        payload = stripped[len(prefix) :].strip()
        try:
            return normalize_intent(json.loads(payload), source=source)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", payload)
            if not match:
                return normalize_intent({}, source=source)
            try:
                return normalize_intent(json.loads(match.group(0)), source=source)
            except json.JSONDecodeError:
                return normalize_intent({}, source=source)
    return normalize_intent({}, source=source)


def _target_matches_item(target, item):
    if target == item:
        return True
    family_suffixes = {
        "log": "_log",
        "planks": "_planks",
        "sapling": "_sapling",
        "leaves": "_leaves",
    }
    if target in family_suffixes and item.endswith(family_suffixes[target]):
        return True
    if target == "fish" and item in {"cod", "salmon", "tropical_fish", "pufferfish"}:
        return True
    return False


def target_matches_item(target, item):
    return _target_matches_item(normalize_item_name(target), normalize_item_name(item))


def purify_fingerprint(raw_fingerprint, task_intent=None, skill_intent=None):
    raw = _normalize_items(raw_fingerprint or [])
    task = normalize_intent(task_intent, source="curriculum")
    skill = normalize_intent(skill_intent, source="action")
    chosen = skill if skill["target_items"] else task
    chosen_source = "skill_intent" if skill["target_items"] else "task_intent"

    matched_targets = []
    for target in chosen["target_items"]:
        if any(_target_matches_item(target, item) for item in raw):
            matched_targets.append(target)

    if matched_targets:
        fingerprint = sorted(set(matched_targets))
        confidence = "high"
        reason = f"{chosen_source}_target_matched"
    else:
        filtered = [item for item in raw if item not in TERRAIN_NOISE_ITEMS]
        if filtered:
            fingerprint = sorted(set(filtered))
            confidence = "low" if chosen["target_items"] else "medium"
            reason = (
                f"{chosen_source}_target_not_observed_noise_filtered"
                if chosen["target_items"]
                else "no_target_noise_filtered"
            )
        else:
            fingerprint = raw
            confidence = "low" if raw else "none"
            reason = "raw_preserved_after_empty_filter" if raw else "no_inventory_gain"

    task_targets = set(task["target_items"])
    skill_targets = set(skill["target_items"])
    if task_targets and skill_targets:
        if task_targets == skill_targets:
            consistency = "match"
        elif matched_targets:
            consistency = "override"
        else:
            consistency = "conflict"
    elif skill_targets:
        consistency = "skill_only"
    elif task_targets:
        consistency = "task_only"
    else:
        consistency = "missing"

    return {
        "fingerprint": fingerprint,
        "raw_fingerprint": raw,
        "task_intent": task,
        "skill_intent": skill,
        "fingerprint_confidence": confidence,
        "fingerprint_filter_reason": reason,
        "intent_consistency": consistency,
    }


class LLMTimeoutError(TimeoutError):
    pass


def is_retryable_llm_error(error):
    message = str(error).lower()
    retry_markers = [
        "429",
        "500",
        "502",
        "503",
        "504",
        "rate limit",
        "service unavailable",
        "temporarily unavailable",
        "timeout",
        "timed out",
        "try again",
        "llm call timed out",
    ]
    return any(marker in message for marker in retry_markers)


def _invoke_llm(llm, messages):
    if hasattr(llm, "invoke"):
        return llm.invoke(messages)
    return llm(messages)


def _call_with_hard_timeout(fn, timeout_seconds):
    if threading.current_thread() is not threading.main_thread():
        return fn()

    def handler(signum, frame):
        raise LLMTimeoutError(f"LLM call timed out after {timeout_seconds}s")

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, handler)
    signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
    try:
        return fn()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


# LLM 单次调用的硬超时（SIGALRM，主线程）。
# 2026-09-11 审计 01 C-8 / 03 C-04：以前写死 90s，voyager.py 传给 ChatOpenAI 的
# openai_api_request_timeout=240 在主线程从不生效。现在由 Voyager.__init__ 通过
# set_llm_hard_timeout() 统一设置，并写进 run_meta.llm_hard_timeout_s。
LLM_HARD_TIMEOUT_S = int(os.environ.get("LLM_HARD_TIMEOUT_S", "90"))


def set_llm_hard_timeout(seconds):
    global LLM_HARD_TIMEOUT_S
    LLM_HARD_TIMEOUT_S = int(seconds)
    return LLM_HARD_TIMEOUT_S


def call_llm_with_retry(
    llm,
    messages,
    max_retries=5,
    base_delay=2.0,
    label="llm",
    timeout_seconds=None,
):
    if timeout_seconds is None:
        timeout_seconds = LLM_HARD_TIMEOUT_S
    last_error = None
    for attempt in range(max_retries):
        try:
            print(
                f"\033[33m[llm] {label} request "
                f"attempt {attempt + 1}/{max_retries}...\033[0m",
                flush=True,
            )
            result = _call_with_hard_timeout(
                lambda: _invoke_llm(llm, messages), timeout_seconds
            )
            print(f"\033[33m[llm] {label} response received.\033[0m", flush=True)
            return result
        except Exception as error:
            last_error = error
            if attempt == max_retries - 1 or not is_retryable_llm_error(error):
                raise
            delay = base_delay * (2**attempt) + random.uniform(0, 0.5)
            print(
                f"\033[33mLLM call failed with retryable error: {error}. "
                f"Retrying in {delay:.1f}s ({attempt + 1}/{max_retries}).\033[0m",
                flush=True,
            )
            time.sleep(delay)
    raise last_error
