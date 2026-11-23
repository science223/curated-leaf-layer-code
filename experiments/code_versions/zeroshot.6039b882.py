"""v29 零样本泛化运行器（Voyager 原文 zero-shot generalization 的口径）。

把一条长跑结束时的技能库带进一个没去过的新世界，清空背包，依次做固定任务，
每个任务最多 max_rounds 步，数做成用了几步；另可跑"空库"对照。

每次尝试（attempt = 一个库 × 一个任务 × 一个 trial）：
  0. 可选 --reset-world-cmd：先执行它把服务器世界换回干净副本。结果里的 world_reset 三选一：
     "cmd"（跑了这个命令）> "fresh_world"（调用方用 --world-fresh 声明本作业本来就是新世界）>
     "hard_only"（都没有，只有 hard reset）；
  1. 把源库的 skill/ 复制到本次尝试专属目录（绝不写源目录）；C1p 默认还把源运行的
     metrics/chain_log.jsonl 拷进本次 ckpt（--seed-records），每行加 "seeded": true，
     让规划器目录里的 record= 带上长跑的执行记录；
  2. 算副本的库哈希（library_sha256：只算核心内容 skills.json、code/、description/）；
  3. 用副本构造 Voyager(freeze_library=True)：add_new_skill / 归档 / 去重 / 退役 / LRU 移出 /
     治理状态落盘都不改库，只打日志（副本里的 skill/frozen_ops.jsonl）；检索日志等照常写进
     副本（不进哈希）。本次尝试的逐步事件、run_meta、llm_calls 在它自己的 ckpt 目录。
     构造后把本次 run_meta 与源运行 metrics/run_meta.json 逐项核对（META_CHECK_KEYS），
     不一致就报错退出（退出码 1，不写结果行）；
  4. hard reset（清背包）；
  5. decompose_task(task) 得到子目标；
  6. 按子目标逐个 rollout；每走一步就查背包，有目标物品立即停（success）；
     步数 = recorder.iteration，到 max_rounds 即停（cap）。子目标失败就重试同一个
     （原版 inference 语义），由上限截住。每步分三类记数：executed_rounds（真执行了代码）、
     parse_fail_rounds / parse_fail_infra_rounds（被解析打回：模型写错 / 解析器坏了）、
     step_timeouts（超时）——每步都被打回时 executed_rounds=0，不会被当成正常的 cap；
  7. 结束后再算一次库哈希，并核对向量库条目数；任一变了 library_changed=True。
     另对源目录 skill/ 做前后逐文件快照（内容哈希 + 大小 + mtime），变了 library_src_changed=True。
每次尝试追加一行 JSON 到 --out（整行一次 write + 追加模式 + flock，可多作业并发追加）。

退出码：0 = 全部尝试以 success/cap 结束且库没变；1 = 配置错误（一次尝试都不跑/不写行）；
2 = 命令行参数错误（argparse）；3 = 有尝试以其它原因结束（环境/崩溃类，行照写）；
4 = 有尝试的库副本或源库被改动（最严重，优先于 3）。
"""
from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Optional

try:
    import fcntl
except ImportError:  # pragma: no cover - 只在 Windows 上缺
    fcntl = None

from voyager.agents.skill import RUNTIME_CALL_PROBE_ENABLED
from voyager.env.bridge import StepExecutionTimeout
from voyager.governance.leaf_inject import (
    GovEpisode,
    empty_gov_metrics,
    gov_injected_source,
    load_gov_pool,
    make_stamped_env_step,
    pool_file_fingerprint,
)
from voyager.intent import infer_task_intent
from voyager.v30_flags import add_v30_arguments, merge_named_actions, v30_voyager_kwargs
from voyager.voyager import (
    Voyager,
    _env_crash_retry_delay,
    _should_stop_environment_censored,
)
from voyager import zs_noise as ZN

DEFAULT_TASKS = (
    ("Craft 1 diamond pickaxe", "diamond_pickaxe"),
    ("Craft 1 golden sword", "golden_sword"),
    ("Collect 1 lava bucket", "lava_bucket"),
    ("Craft 1 compass", "compass"),
)
DEFAULT_WORLD_SEED = 20260902099
DEFAULT_MAX_ROUNDS = 50
DEFAULT_TRIALS = 3
# 与 run_voyager.py 同名同默认（--arm A 推出的模型名；接口地址；读哪个环境变量当 key）
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_API_BASE = "https://api.deepseek.com/v1"
DEFAULT_API_KEY_ENV = "DEEPSEEK_API_KEY"

NORMAL_STOPS = ("success", "cap", "subgoal_cap_final")
EXIT_OK = 0
EXIT_SETUP = 1
EXIT_ABNORMAL = 3
EXIT_LIBRARY_CHANGED = 4
WORLD_RESET_TIMEOUT_S = 1800

# ---------------------------------------------------------------------------
# 预设 = 源运行实际用的 run_voyager.py 命令行映射成的 Voyager(...) 关键字参数。
# 命令行出处 _cc0911/launch_0911.py：COMMON（去掉 OLD_DROP）+ --arm A（Flash、整理关）；
# v26 原版 / v28 C1′ 另加 --pause-verify --no-stall-abort；C1′ = NEW_EXTRA + --planner-v2
# --noeffect-guard + --gap-reference --chain-time-budget 240。映射关系见 run_voyager.py 末尾的
# Voyager(...) 调用。smoke/smoke_v29_zeroshot.py 的 Z7 用 launch_0911.build_args + run_voyager
# 自己的 parse_args 逐键复核这张表。
# pause_verify / stall_abort 只管环境与课程护栏（零样本不走课程出题），世界 3 原版（_v24OLD）
# 当时没加这两个开关；零样本一律按 v26/v28 的取值，不参与 run_meta 核对。
_COMMON_KWARGS = dict(
    env_request_timeout=600,  # 09-25 回到上游 Voyager 默认 600 s（A/A 原版 451 轮 0 次撞 300 s）
    env_dead_after=5,
    skill_manager_curation_enabled=False,
    skill_manager_retire_min_exposure=12,
    skill_manager_retire_use_threshold=0.15,
    skill_manager_curation_interval=15,
    skill_manager_call_detect="runtime",
    skill_manager_structured_retrieval=False,
    skill_manager_substitute_guard="fp",
    skill_manager_substitute_undecided="allow_retire",
    skill_manager_curation_mode=None,
    skill_manager_dedup_mode=None,
    skill_manager_intent_fallback_dedup=True,
    skill_manager_retrieval_gate_enabled=False,
    skill_manager_retrieval_top_k=5,
    skill_manager_protect_critical_skills=False,
    skill_manager_protected_skills=[],
    llm_thinking="disabled",
    stall_abort=False,
    pause_verify=True,
)
PRESETS = {
    # 原版默认：检索 top-5、static 治理、整理关
    "OLD": dict(
        _COMMON_KWARGS,
        skill_api_prompt=False,
        skill_params=False,
        chain=False,
        planner_v2=False,
        noeffect_guard=False,
        gap_reference=False,
        chain_time_budget_s=0,
        skill_manager_contribution_mode="binary",
        skill_manager_governance="static",
        skill_manager_governance_capacity=None,
    ),
    # v28 C1′（ckpt_*_v28NEW 的 run_meta）
    "C1p": dict(
        _COMMON_KWARGS,
        skill_api_prompt=True,
        skill_params=True,
        chain=True,
        planner_v2=True,
        noeffect_guard=True,
        gap_reference=True,
        chain_time_budget_s=240,
        skill_manager_contribution_mode="exec",
        skill_manager_governance="dynamic",
        skill_manager_governance_capacity=0,
    ),
}
# 允许与源运行不同的"环境/运行控制"类参数（其余预设键必须与源命令行逐一相等）
ENV_CONTROL_KWARGS = ("pause_verify", "stall_abort")

# 与源运行 metrics/run_meta.json 逐项核对的键。两边都由 Voyager.__init__ 写入、同名同义。
# 第二项是旧版本 run_meta 缺这个键时的含义（v26 原版没有 planner_v2 / gap_reference /
# chain_time_budget_s）；_REQUIRED = 两边都必须有。
_REQUIRED = object()
META_CHECK_KEYS = (
    ("skill_api_prompt", _REQUIRED),
    ("skill_params_enabled", _REQUIRED),
    ("chain_enabled", _REQUIRED),
    ("planner_v2", False),
    ("noeffect_guard", _REQUIRED),
    ("gap_reference", False),
    ("chain_time_budget_s", 0),
    ("retrieval_top_k", _REQUIRED),
    ("retrieval_pool_size", _REQUIRED),
    ("mmr_lambda", _REQUIRED),
    ("structured_retrieval", _REQUIRED),
    ("governance", _REQUIRED),
    ("governance_capacity", _REQUIRED),
    ("curation_enabled", _REQUIRED),
    ("contribution_mode", _REQUIRED),
    ("call_detect", _REQUIRED),
    ("substitute_guard", _REQUIRED),
    ("scoring_scope", _REQUIRED),
    ("runtime_call_probe_enabled", _REQUIRED),
    ("action_agent_task_max_retries", _REQUIRED),
    # 模型与接口：与 run_voyager 同一构造路径（Voyager.__init__ / llm_meta）的产物
    ("model_requested", _REQUIRED),
    ("thinking", _REQUIRED),
    ("openai_api_base", _REQUIRED),
    ("openrouter_mode", _REQUIRED),
    ("provider_order", _REQUIRED),
    ("allow_fallbacks", _REQUIRED),
    ("request_extra_body", _REQUIRED),
)


class ConfigError(Exception):
    """命令行/源库配置问题：一次尝试都不跑，退出码 1。"""


class ConfigMismatch(ConfigError):
    """源运行 run_meta 与本次预设构造出的 run_meta 不一致。"""


@dataclasses.dataclass
class ZeroShotConfig:
    preset: str
    arm: str
    library_src: Optional[str]
    library_id: str
    library_size: int
    source_meta: Optional[dict]
    tasks: list
    trial_numbers: list
    max_rounds: int
    world_seed: int
    mc_port: int
    server_port: int
    out: str
    work_dir: str
    reset_world_cmd: Optional[str]
    world_fresh: bool
    seed_records: str
    attempt_id: Optional[str]
    model: str
    api_base: str
    provider_order: list
    # 额外基础技能（control_primitives_extra/ 下的文件名）。空 = 默认行为，
    # 不改变任何现有臂：既不进提示词，也不进结果行。
    extra_base_skills: list = dataclasses.field(default_factory=list)
    zs_noise_fix: bool = False
    zs_noise_flags: dict = dataclasses.field(default_factory=dict)
    zs_subgoal_cap: int = 8
    v30_kwargs: dict = dataclasses.field(default_factory=dict)
    api_key: str = dataclasses.field(default="", repr=False)
    # 治理叶子层（SYSGOV）：不传 --gov-pool 时保持 None，结果行也不多字段。
    gov_pool_path: Optional[str] = None
    gov_online: bool = False
    gov_pool: dict = dataclasses.field(default_factory=dict)
    gov_pool_fingerprint: Optional[str] = None


# ---------------------------------------------------------------- 小工具
def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _err(err):
    return f"{type(err).__name__}: {str(err)[:300]}"


def _slug(text, limit=80):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text))[:limit].strip("_") or "x"


def _jsonish(value):
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _read_json(path):
    with open(path, encoding="utf-8") as fp:
        return json.load(fp)


def _read_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


LIBRARY_CORE = ("skills.json", "code", "description")


def _file_sha256(path):
    file_hash = hashlib.sha256()
    with open(path, "rb") as fp:
        for chunk in iter(lambda: fp.read(1 << 20), b""):
            file_hash.update(chunk)
    return file_hash.hexdigest()


def library_sha256(skill_dir):
    """库哈希（只算核心内容：skills.json、code/、description/；检索日志、治理状态等不进）。
    算法（与任务 C 盘点同口径）：核心文件的相对路径（'/' 分隔）按字节序排序，逐文件算
    sha256，再对 "路径\\t哈希\\n" 串起来的整串算 sha256。返回 (十六进制摘要, 核心文件数)。"""
    entries = []
    for top in LIBRARY_CORE:
        full_top = os.path.join(skill_dir, top)
        if os.path.isfile(full_top):
            entries.append((top, full_top))
        elif os.path.isdir(full_top):
            for root, _dirs, files in os.walk(full_top):
                for name in files:
                    full = os.path.join(root, name)
                    entries.append((os.path.relpath(full, skill_dir).replace(os.sep, "/"), full))
    entries.sort(key=lambda item: item[0].encode("utf-8"))
    text = "".join(f"{rel}\t{_file_sha256(full)}\n" for rel, full in entries)
    return hashlib.sha256(text.encode("utf-8")).hexdigest(), len(entries)


def dir_snapshot(path):
    """目录逐文件快照 {相对路径: (大小, mtime_ns, sha256)}：连"原样重写同内容"也看得出来。"""
    snap = {}
    for root, _dirs, files in os.walk(path):
        for name in files:
            full = os.path.join(root, name)
            st = os.stat(full)
            snap[os.path.relpath(full, path).replace(os.sep, "/")] = (st.st_size, st.st_mtime_ns, _file_sha256(full))
    return snap


def _count_lines(path):
    if not os.path.exists(path):
        return 0
    with open(path, "rb") as fp:
        return sum(1 for _ in fp)


def _vectordb_count(voyager):
    try:
        return int(voyager.skill_manager.vectordb._collection.count()), None
    except Exception as err:
        return None, _err(err)


def reset_chroma_index_cache():
    """chromadb 0.3.x 把 hnswlib 索引对象缓存在**类属性** Clickhouse.index_cache 里（整个进程共享、
    按集合 UUID）。库副本与源库的集合 UUID 相同，同一进程里先加载过的目录会"占住"这个 UUID，
    后来的副本查询走的是它、写入（add/delete 时 _save）也会落到它的目录。冻结时不会有写入；
    这里在每次尝试开头清掉，让本次的索引对象绑定本次的副本，写入也只可能落在本次副本里。"""
    try:
        from chromadb.db.clickhouse import Clickhouse

        Clickhouse.index_cache.clear()
        return True
    except Exception:
        return False


def seed_chain_log(library_src, ckpt_dir):
    """把源运行的 metrics/chain_log.jsonl 拷进本次尝试的 ckpt（每行加 "seeded": true）。

    C1p 的规划器目录里每条技能后面的 record= 来自 chain_log.jsonl（voyager.chain.requires
    .records_from_chain_log 每个技能取最近 5 条）。零样本是新 ckpt，不拷就开局全空，等于把长跑
    攒下的执行记录丢了。只读源文件，写的是本次尝试自己的 ckpt；chain_log 在 metrics/ 下，
    不在 skill/ 里，所以与库哈希、冻结无关。返回拷进去的行数。"""
    src = os.path.join(library_src or "", "metrics", "chain_log.jsonl")
    if not library_src or not os.path.isfile(src):
        return 0
    dst = os.path.join(ckpt_dir, "metrics", "chain_log.jsonl")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    rows = 0
    with open(src, encoding="utf-8") as fin, open(dst, "w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            row["seeded"] = True  # 事后可区分"带过来的"与"本次尝试产生的"
            fout.write(json.dumps(row, ensure_ascii=False) + "\n")
            rows += 1
    return rows


def make_empty_library(library_root):
    """空库对照：与真库同样的目录结构，skills.json = {}，向量库为空目录。"""
    skill_dir = os.path.join(library_root, "skill")
    for sub in ("code", "description", "vectordb"):
        os.makedirs(os.path.join(skill_dir, sub), exist_ok=True)
    with open(os.path.join(skill_dir, "skills.json"), "w", encoding="utf-8") as fp:
        json.dump({}, fp)
    return skill_dir


def append_jsonl_locked(path, row):
    """整行一次 os.write（O_APPEND）+ flock：多个作业同时往同一个结果文件追加也不会串行。"""
    data = (json.dumps(row, ensure_ascii=False, default=str) + "\n").encode("utf-8")
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        written = 0
        while written < len(data):
            written += os.write(fd, data[written:])
        os.fsync(fd)
    finally:
        if fcntl is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)


# 观测里 status.equipment 是 [头, 胸, 腿, 脚, 主手, 副手] 六个名字。mineflayer 的
# inventory.items() 只覆盖 9–44 号格：盔甲(5–8)和副手(45)不在里面，主手是快捷栏的一格、
# 已经算在 inventory 里。所以只补这 5 个物品栏点不到的槽位，跳过主手免得重复计数。
_EQUIPMENT_EXTRA_SLOTS = (0, 1, 2, 3, 5)


def target_count(events, target):
    """目标物品的件数：物品栏里的 + 穿在身上或拿在副手里的（09-20 Grok 复核指出的漏判）。"""
    try:
        observe = events[-1][1]
        inventory = observe.get("inventory") or {}
        count = int(inventory.get(target, 0) or 0)
    except Exception:
        return 0
    try:
        equipment = (observe.get("status") or {}).get("equipment") or []
        if isinstance(equipment, list) and len(equipment) == 6:
            count += sum(1 for i in _EQUIPMENT_EXTRA_SLOTS if equipment[i] == target)
    except Exception:
        pass
    return count


def _skill_calls(events):
    try:
        raw = events[-1][1].get("skillCalls") or {}
    except Exception:
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and not k.startswith("__")}


def llm_usage(ckpt_dir):
    """本次尝试 ckpt 的 metrics/llm_calls.jsonl → (调用次数, tokens)。"""
    calls = 0
    tokens = {"prompt": 0, "completion": 0, "total": 0}
    for row in _read_jsonl(os.path.join(ckpt_dir, "metrics", "llm_calls.jsonl")):
        calls += 1
        prompt = row.get("prompt_tokens") if isinstance(row.get("prompt_tokens"), int) else 0
        completion = (
            row.get("completion_tokens") if isinstance(row.get("completion_tokens"), int) else 0
        )
        total = row.get("total_tokens")
        tokens["prompt"] += prompt
        tokens["completion"] += completion
        tokens["total"] += total if isinstance(total, int) else prompt + completion
    return calls, tokens


_LLM_KIND = {"action": "decision", "critic": "critic", "skill": "skill_gen"}


def llm_usage_by_kind(ckpt_dir):
    """Split llm_calls.jsonl by agent: decision / critic / skill_gen (+ empty repair)."""
    kinds = ("decision", "critic", "skill_gen", "repair")
    by = {
        k: {"calls": 0, "tokens": {"prompt": 0, "completion": 0, "total": 0}} for k in kinds
    }
    path = os.path.join(ckpt_dir, "metrics", "llm_calls.jsonl")
    for row in _read_jsonl(path):
        kind = _LLM_KIND.get(row.get("agent") or "")
        if not kind:
            continue
        prompt = row.get("prompt_tokens") if isinstance(row.get("prompt_tokens"), int) else 0
        completion = (
            row.get("completion_tokens") if isinstance(row.get("completion_tokens"), int) else 0
        )
        total = row.get("total_tokens")
        total = total if isinstance(total, int) else prompt + completion
        slot = by[kind]
        slot["calls"] += 1
        slot["tokens"]["prompt"] += prompt
        slot["tokens"]["completion"] += completion
        slot["tokens"]["total"] += total
    return by


def compare_run_meta(source_meta, zeroshot_meta):
    """逐项比较 META_CHECK_KEYS；返回不一致的 [{key, source, zeroshot}]。"""
    diffs = []
    for key, default in META_CHECK_KEYS:
        if default is _REQUIRED:
            left = source_meta.get(key, "<missing>")
            right = zeroshot_meta.get(key, "<missing>")
        else:
            left = source_meta.get(key, default)
            right = zeroshot_meta.get(key, default)
        left, right = _jsonish(left), _jsonish(right)
        if left != right:
            diffs.append({"key": key, "source": left, "zeroshot": right})
    return diffs


def continue_governor_timeline(skill_manager, t_end):
    """C1p（dynamic 治理）：治理器里的时间戳是长跑的步号，零样本的步号从 0 重新数。
    目录行 "[last_exec: k tasks ago]" 的 k = 当前步号 − last_exec_task，不平移会出现负数。
    这里只在内存里把全部时间戳减去 t_end（长跑结束时的步号），零样本第 s 步看到的
    k = s + (t_end − last_exec_task)，即长跑时间线的延续。排序只看差值，不受影响；
    库冻结时治理状态不落盘。返回实际平移量。"""
    governor = getattr(skill_manager, "governor", None)
    skills = getattr(governor, "_skills", None)
    if governor is None or not isinstance(skills, dict) or not t_end:
        return 0
    for entry in skills.values():
        entry["created_task"] = int(entry.get("created_task") or 0) - int(t_end)
        for key in ("last_exec_task", "evicted_task"):
            if entry.get(key) is not None:
                entry[key] = int(entry[key]) - int(t_end)
    return int(t_end)


def _governor_t_end(source_meta, skill_manager):
    stopped = (source_meta or {}).get("stopped_at_iteration")
    if isinstance(stopped, int) and stopped > 0:
        return stopped
    governor = getattr(skill_manager, "governor", None)
    stamps = []
    for entry in (getattr(governor, "_skills", None) or {}).values():
        for key in ("created_task", "last_exec_task", "evicted_task"):
            if isinstance(entry.get(key), int):
                stamps.append(entry[key])
    return max(stamps) + 1 if stamps else 0


# ---------------------------------------------------------------- 配置
def parse_task_spec(spec):
    """'Craft 1 compass' / 'compass'（默认表里的任务或目标物品）/ '任务文本=目标物品'。"""
    spec = (spec or "").strip()
    if "=" in spec:
        task, target = (part.strip() for part in spec.split("=", 1))
        if not task or not re.fullmatch(r"[a-z0-9_]+", target or ""):
            raise ConfigError(f"任务写法不对：{spec!r}（应为 '任务文本=目标物品'，物品名如 diamond_pickaxe）")
        return task, target
    for task, target in DEFAULT_TASKS:
        if spec.lower() in (task.lower(), target):
            return task, target
    raise ConfigError(
        f"未知任务 {spec!r}：可写默认表里的任务/物品名（{', '.join(t for _, t in DEFAULT_TASKS)}），"
        "或 '任务文本=目标物品'"
    )


def derive_library_id(preset, library_src):
    if library_src is None:
        return f"EMPTY_{preset}"
    base = os.path.basename(os.path.normpath(library_src))
    match = re.search(r"_t(\d+)", base)
    return f"{preset}_w{match.group(1)}" if match else base


def resolve_config(args):
    if not RUNTIME_CALL_PROBE_ENABLED:
        raise ConfigError(
            "RUNTIME_CALL_PROBE 必须为 1：两个源运行都开着运行时探针（run_meta.runtime_call_probe_enabled=True），"
            "而且必须在导入 voyager 之前设置"
        )
    if args.trials < 1 or args.max_rounds < 1:
        raise ConfigError("--trials 与 --max-rounds 都必须 ≥ 1")
    preset = args.arm
    library_src = None
    source_meta = None
    library_size = 0
    if args.library_src:
        library_src = os.path.abspath(args.library_src)
        skills_path = os.path.join(library_src, "skill", "skills.json")
        meta_path = os.path.join(library_src, "metrics", "run_meta.json")
        if not os.path.isfile(skills_path):
            raise ConfigError(f"源库缺 skill/skills.json：{library_src}（--library-src 要给 ckpt 目录）")
        if not os.path.isfile(meta_path):
            raise ConfigError(f"源库缺 metrics/run_meta.json，无法核对配置：{library_src}")
        library_size = len(_read_json(skills_path))
        source_meta = _read_json(meta_path)
    tasks = [parse_task_spec(spec) for spec in (args.tasks or [t for t, _ in DEFAULT_TASKS])]
    # 额外基础技能：名字在这里就解析一遍，打错立刻 ConfigError（退出码 1），
    # 不要等世界重置完、模型都构造好了才炸。
    extra_base = list(args.extra_base_skills or [])
    try:
        v30_kw = v30_voyager_kwargs(args)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    extra_base = list(
        merge_named_actions(extra_base or None, getattr(args, "v30_named_actions", False))
        or extra_base
        or []
    )
    if extra_base:
        from voyager.control_primitives_extra import load_extra_base_skills
        from voyager.control_primitives_extra_context import (
            load_extra_base_skills_context,
        )

        try:
            load_extra_base_skills(extra_base)
            load_extra_base_skills_context(extra_base)
        except (ValueError, FileNotFoundError) as exc:
            raise ConfigError(f"--extra-base-skills 有问题：{exc}") from exc

    noise_flags = ZN.noise_flags_from_args(args)
    zs_noise_fix = bool(getattr(args, "zs_noise_fix", False) or ZN.any_noise_flag(noise_flags))
    cap = int(getattr(args, "zs_subgoal_cap", ZN.DEFAULT_SUBGOAL_CAP) or ZN.DEFAULT_SUBGOAL_CAP)
    if cap < 1:
        raise ConfigError("--zs-subgoal-cap 必须 ≥ 1")

    gov_pool_path = getattr(args, "gov_pool", None) or None
    gov_online = bool(getattr(args, "gov_online", False))
    gov_pool = {}
    gov_fp = None
    if gov_online and not gov_pool_path:
        raise ConfigError("--gov-online 必须同时给 --gov-pool")
    if gov_pool_path:
        gov_pool_path = os.path.abspath(gov_pool_path)
        if not os.path.isfile(gov_pool_path):
            raise ConfigError(f"--gov-pool 文件不存在：{gov_pool_path}")
        try:
            gov_pool = load_gov_pool(gov_pool_path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise ConfigError(f"--gov-pool 读失败：{exc}") from exc
        gov_fp = pool_file_fingerprint(gov_pool_path)
        # v7b: a pool that yields no usable candidate (missing code/fn/id, or fn not ^gov_) would silently
        # degrade SYSGOV into SYS -> refuse it here
        _src, _leaves = gov_injected_source(gov_pool.get("candidates") if isinstance(gov_pool.get("candidates"), dict) else gov_pool)
        _usable = [c for recs in (_leaves or {}).values() for c in (recs or [])]
        if not _usable:
            raise ConfigError(f"--gov-pool 没有可用候选（缺 code/fn/id）：{gov_pool_path}")
        _bad = [c.get("fn") for c in _usable if not str(c.get("fn") or "").startswith("gov_")]
        if _bad:
            raise ConfigError(f"--gov-pool 候选入口名必须以 gov_ 开头：{_bad[:5]}")

    api_key = os.getenv(args.api_key_env)
    if not api_key:
        raise ConfigError(f"请先 export {args.api_key_env}=...（与 run_voyager.py 同一规则）")
    first_trial = 1 if args.trial_index is None else int(args.trial_index)
    return ZeroShotConfig(
        preset=preset,
        arm="EMPTY" if library_src is None else preset,
        library_src=library_src,
        library_id=args.library_id or derive_library_id(preset, library_src),
        library_size=library_size,
        source_meta=source_meta,
        tasks=tasks,
        trial_numbers=[first_trial + i for i in range(args.trials)],
        max_rounds=int(args.max_rounds),
        world_seed=int(args.world_seed),
        mc_port=int(args.mc_port),
        server_port=int(args.server_port),
        out=os.path.abspath(args.out),
        work_dir=os.path.abspath(args.work_dir),
        reset_world_cmd=args.reset_world_cmd,
        world_fresh=bool(args.world_fresh),
        # auto：C1p 开（链式规划器要看 record=），OLD 关（不走链式，拷了也没人读）
        seed_records=(("on" if preset == "C1p" else "off") if args.seed_records == "auto" else args.seed_records),
        attempt_id=args.attempt_id,
        model=args.model_name or DEFAULT_MODEL,
        api_base=args.api_base,
        # run_voyager.py 的原式：[p.strip() for p in args.provider_order.split(",") if p.strip()]
        provider_order=[p.strip() for p in args.provider_order.split(",") if p.strip()],
        extra_base_skills=extra_base,
        zs_noise_fix=zs_noise_fix,
        zs_noise_flags=noise_flags,
        zs_subgoal_cap=cap,
        v30_kwargs=v30_kw,
        api_key=api_key,
        gov_pool_path=gov_pool_path,
        gov_online=gov_online,
        gov_pool=gov_pool,
        gov_pool_fingerprint=gov_fp,
    )


def build_voyager_kwargs(cfg, *, ckpt_dir, library_root, trial):
    """预设 + 本次尝试的路径/端口 + 与 run_voyager.py 相同的 API/模型参数。"""
    kwargs = copy.deepcopy(PRESETS[cfg.preset])
    kwargs.update(
        mc_port=cfg.mc_port,
        server_port=cfg.server_port,
        ckpt_dir=ckpt_dir,
        skill_library_dir=library_root,
        resume=False,
        freeze_library=True,
        # 零样本固定和平难度：Voyager 本来用"完成的子目标超过 15 就切成 easy"，
        # 跑得快的一臂会先把自己的世界换成会刷敌对生物的，两臂就不可比了。
        force_difficulty="peaceful",
        max_iterations=cfg.max_rounds,
        openai_api_key=cfg.api_key,
        openai_api_base=cfg.api_base,
        openrouter_provider_order=list(cfg.provider_order),
        action_agent_model_name=cfg.model,
        curriculum_agent_model_name=cfg.model,
        curriculum_agent_qa_model_name=cfg.model,
        critic_agent_model_name=cfg.model,
        skill_manager_model_name=cfg.model,
        experiment_arm=f"zeroshot-{cfg.arm}",
        experiment_trial=trial,
    )
    # 只在真开了额外基础技能时才加这个键：不开时 kwargs 与改动前逐键相同，
    # 结果行的 voyager_kwargs 也就不会多出字段。
    if cfg.extra_base_skills:
        kwargs["extra_base_skills"] = list(cfg.extra_base_skills)
    if cfg.v30_kwargs:
        kwargs.update(cfg.v30_kwargs)
    if cfg.zs_noise_flags.get("fixed_start"):
        kwargs["zs_fixed_time"] = ZN.ZS_FIXED_TIME
        kwargs["zs_fixed_weather"] = True
    return kwargs


def public_kwargs(kwargs):
    """写进结果行的 voyager_kwargs：去掉 API key。"""
    return _jsonish({k: v for k, v in kwargs.items() if k != "openai_api_key"})


# ---------------------------------------------------------------- 一次尝试
def _make_attempt_dir(cfg, target, trial):
    stamp = cfg.attempt_id or (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"_{os.getpid()}"
    )
    base = f"{_slug(cfg.library_id)}__{_slug(target)}__t{trial}__{_slug(stamp)}"
    os.makedirs(cfg.work_dir, exist_ok=True)
    path = os.path.join(cfg.work_dir, base)
    k = 1
    while True:
        try:
            os.makedirs(path)
            return path
        except FileExistsError:
            k += 1
            path = os.path.join(cfg.work_dir, f"{base}_{k}")


def _run_world_reset(cfg, attempt_dir, task, target, trial):
    env = dict(
        os.environ,
        ZS_ATTEMPT_DIR=attempt_dir,
        ZS_ATTEMPT_ID=cfg.attempt_id or "",
        ZS_TASK=task,
        ZS_TARGET=target,
        ZS_TRIAL=str(trial),
        ZS_LIBRARY_ID=cfg.library_id,
        ZS_ARM=cfg.arm,
        ZS_MC_PORT=str(cfg.mc_port),
        ZS_WORLD_SEED=str(cfg.world_seed),
    )
    with open(os.path.join(attempt_dir, "world_reset.log"), "ab") as log:
        try:
            done = subprocess.run(
                cfg.reset_world_cmd, shell=True, env=env, stdout=log, stderr=log,
                timeout=WORLD_RESET_TIMEOUT_S,
            )
            return done.returncode
        except subprocess.TimeoutExpired:
            return -1


def _recover(voyager):
    """与 learn() 崩溃后的恢复相同：硬重置并还原背包/装备/位置。失败返回 False。"""
    time.sleep(3)  # wait for mineflayer to exit（learn() 同款）
    try:
        last = voyager.last_events[-1][1]
        voyager.last_events = voyager.env.reset(
            options={
                "mode": "hard",
                "wait_ticks": voyager.env_wait_ticks,
                "inventory": last["inventory"],
                "equipment": last["status"]["equipment"],
                "position": last["status"]["position"],
            }
        )
        return True
    except Exception as err:
        print(f"\033[41m[zeroshot] 恢复失败：{_err(err)}\033[0m", flush=True)
        return False


def _clean_subgoals(raw):
    if not isinstance(raw, list):
        return [], f"decomposition is not a list: {type(raw).__name__}"
    return [s.strip() for s in raw if isinstance(s, str) and s.strip()], None


def _give_product(voyager, item, n, retries=None):
    """Throttle /give; verify by pre/post delta. Does not increment the recorder.

    retries = extra attempts after a failed check (default GIVE_VERIFY_RETRIES=2),
    so the env is stepped at most 1 + retries times. Exceptions are recorded,
    never swallowed. Returns (ok, errors).
    """
    retries = ZN.GIVE_VERIFY_RETRIES if retries is None else int(retries)
    n = int(n)
    errors = []
    attempts = 1 + max(0, retries)
    js = ZN.give_js(item, n)
    for i in range(attempts):
        before = target_count(voyager.last_events, item)
        try:
            voyager.last_events = voyager.env.step(js, programs="")
        except Exception as err:
            errors.append(_err(err))
            continue
        after = target_count(voyager.last_events, item)
        if after - before >= n:
            return True, errors
        errors.append(
            f"give_delta_short: item={item} want={n} before={before} after={after} "
            f"try={i + 1}/{attempts}"
        )
    return False, errors


def _apply_noise_judge(voyager, subgoal, info, done, flags, start_inv=None):
    if not (flags.get("place_judge") or flags.get("count_judge")):
        return info, done
    if start_inv is None:
        start_inv = getattr(voyager, "zs_subgoal_start_inventory", None)
    if start_inv is None:
        start_inv = getattr(voyager, "rollout_start_inventory", None) or {}
    verdict = ZN.judge_subgoal(
        subgoal, voyager.last_events, start_inv, flags
    )
    if verdict is None:
        return info, done
    info = dict(info or {})
    info["success"] = bool(verdict)
    if verdict:
        done = True
    return info, done


def _cap_subgoal(
    voyager, rec, subgoal, idx, flags, cap_k, stuck, *, first_start, is_final,
    injected=None,
):
    if not flags.get("subgoal_cap") or cap_k is None:
        return False, False
    used = voyager.recorder.iteration - int(first_start)
    if used < cap_k:
        return False, False
    rec["capped"] = True
    if is_final:
        rec["ended_by"] = "subgoal_cap_final"
        rec["success"] = False
        rec["target_given"] = False
        stuck.append(
            ZN.make_stuck_segment(
                subgoal_index=idx,
                subgoal=subgoal,
                start_round=int(first_start),
                end_round=voyager.recorder.iteration,
                give_item=None,
                give_count=None,
                give_ok=False,
            )
        )
        return True, False
    rec["ended_by"] = "subgoal_cap"
    prod = ZN.product_for_subgoal(subgoal)
    give_ok = False
    item = count = None
    errors = []
    if prod:
        item, count = prod
        give_ok, errors = _give_product(voyager, item, count)
        if errors:
            rec["give_errors"] = list(errors)
        if give_ok:
            ZN.record_injection(injected if injected is not None else {}, item, count)
        else:
            rec["ended_by"] = "harness_give_failed"
            rec["success"] = False
            rec["target_given"] = False
            stuck.append(
                ZN.make_stuck_segment(
                    subgoal_index=idx,
                    subgoal=subgoal,
                    start_round=int(first_start),
                    end_round=voyager.recorder.iteration,
                    give_item=item,
                    give_count=count,
                    give_ok=False,
                )
            )
            return True, False
    rec["target_given"] = bool(give_ok)
    stuck.append(
        ZN.make_stuck_segment(
            subgoal_index=idx,
            subgoal=subgoal,
            start_round=int(first_start),
            end_round=voyager.recorder.iteration,
            give_item=item,
            give_count=count,
            give_ok=give_ok,
        )
    )
    rec["success"] = True
    return True, give_ok


def _subgoal_loop(voyager, cfg, *, task, target, subgoals, steps_path, code_path=None):
    """第 6 步。只在两种情况下结束：背包里有目标物品（success）或步数到上限（cap）；
    另有环境连续崩溃到 env_dead_after（env_crash，与 learn() 同一判据）。
    - 子目标被评审判成功才前进到下一个；失败就重试同一个，由上限截住；
    - 子目标都做完仍没拿到目标物品：接着直接做原任务，直到拿到或到上限；
    - 生成代码超时（StepExecutionTimeout）：与 learn() 一样硬重置还原背包、本次 rollout 作失败，
      但**这一步记进 recorder**（算一步）——否则温度 0 下同一段超时代码可以无限重来而步数不涨；
    - 其它异常（桥接/接口故障）：与 learn() 一样恢复、按连续崩溃计数与退避，不算步数。"""
    max_rounds = cfg.max_rounds
    code_path = code_path or os.path.join(os.path.dirname(steps_path), "zeroshot_code.jsonl")
    library_names = set(voyager.skill_manager.skills)
    results, completed = [], []
    tries = {}
    idx = 0
    crash_streak = n_timeouts = n_crashes = 0
    n_executed = n_parse_fail_model = n_parse_fail_infra = 0
    library_called = set()
    stopped = None
    last_error = None
    flags = getattr(cfg, "zs_noise_flags", None) or {}
    cap_k = int(getattr(cfg, "zs_subgoal_cap", ZN.DEFAULT_SUBGOAL_CAP) or ZN.DEFAULT_SUBGOAL_CAP)
    stuck = []
    first_start = {}
    first_inv = {}
    injected = {}
    noise_on = bool(getattr(cfg, "zs_noise_fix", False) or ZN.any_noise_flag(flags))
    placed_this_attempt = set()
    n_earned_skips = 0
    n_give_cascade = 0
    while stopped is None:
        if voyager.recorder.iteration >= max_rounds:
            stopped = "cap"
            break
        fallback = idx >= len(subgoals)
        subgoal = task if fallback else subgoals[idx]
        is_final = fallback or idx >= max(0, len(subgoals) - 1)
        if idx not in first_start:
            first_start[idx] = voyager.recorder.iteration
            first_inv[idx] = dict(ZN._last_inventory(voyager.last_events) or {})
        voyager.zs_subgoal_start_inventory = dict(first_inv[idx])
        if noise_on and not fallback:
            skip = ZN.skip_reason(
                subgoal,
                voyager.last_events,
                injected,
                flags=flags,
                placed_this_attempt=placed_this_attempt,
            )
            if skip:
                rec = {
                    "index": idx,
                    "subgoal": subgoal,
                    "attempt": tries.get(idx, 0),
                    "final_task_fallback": False,
                    "start_round": voyager.recorder.iteration,
                    "ended_by": "already_satisfied" if skip == "earned" else skip,
                    "success": True,
                    "end_round": voyager.recorder.iteration,
                    "steps": 0,
                }
                if skip == "give_cascade":
                    rec["give_cascade"] = True
                    rec["ended_by"] = "give_cascade"
                    n_give_cascade += 1
                elif skip == "place_already":
                    rec["ended_by"] = "place_already"
                    n_earned_skips += 1
                else:
                    rec["ended_by"] = "already_satisfied"
                    n_earned_skips += 1
                results.append(rec)
                completed.append(idx)
                idx += 1
                if idx >= len(subgoals) and ZN.final_target_ok(
                    target_count(voyager.last_events, target), injected, target
                ):
                    stopped = "success"
                continue
        if noise_on and not fallback:
            # pilot v2 (rule A, both arms): a later canonical subgoal already met with EARNED items implies the
            # earlier steps (their items may have been consumed, e.g. planks -> wooden pickaxe)
            hi = -1
            for j in range(idx + 1, len(subgoals)):
                r_later = ZN.skip_reason(
                    subgoals[j], voyager.last_events, injected,
                    flags=flags, placed_this_attempt=placed_this_attempt,
                )
                if r_later in ("earned", "place_already"):
                    hi = j
            if hi > idx:
                for k in range(idx, hi):
                    results.append({
                        "index": k,
                        "subgoal": subgoals[k],
                        "attempt": tries.get(k, 0),
                        "final_task_fallback": False,
                        "start_round": voyager.recorder.iteration,
                        "ended_by": "implied_by_later",
                        "success": True,
                        "end_round": voyager.recorder.iteration,
                        "steps": 0,
                    })
                    completed.append(k)
                    n_earned_skips += 1
                idx = hi
                continue
        tries[idx] = tries.get(idx, 0) + 1
        rec = {
            "index": idx,
            "subgoal": subgoal,
            "attempt": tries[idx],
            "final_task_fallback": fallback,
            "start_round": voyager.recorder.iteration,
            "ended_by": None,
        }
        if flags.get("subgoal_cap"):
            rec["capped"] = False
        info = {"task": subgoal, "success": False}
        crashed = False
        try:
            context = voyager.curriculum_agent.get_task_context(subgoal)
            if noise_on:
                extras = list(getattr(cfg, "extra_base_skills", None) or [])
                named = bool(
                    getattr(cfg, "v30_named_actions", False)
                    or getattr(voyager, "v30_named_actions", False)
                    or "obtain" in extras
                )
                plan = ZN.plan_prompt(
                    task, subgoals, idx, named=named, target=target
                )
                context = (str(context or "").rstrip() + "\n" + plan).strip()
            voyager.reset(
                task=subgoal,
                context=context,
                reset_env=True,
                task_intent=infer_task_intent(subgoal, source="decomposition_rule"),
            )
            while True:
                before = voyager.last_events
                pf_model = getattr(voyager, "_rollout_parse_fail_model", 0)
                pf_infra = getattr(voyager, "_rollout_parse_fail_infra", 0)
                _messages, _reward, done, info = voyager.step()
                info, done = _apply_noise_judge(
                    voyager, subgoal, info, done, flags, start_inv=first_inv[idx]
                )
                crash_streak = 0
                executed = voyager.last_events is not before
                # 这一步是执行了代码，还是被解析打回（模型写错 / 解析器本身坏了，例如找不到 babel）
                pf_model = getattr(voyager, "_rollout_parse_fail_model", 0) - pf_model
                pf_infra = getattr(voyager, "_rollout_parse_fail_infra", 0) - pf_infra
                n_executed += int(bool(executed))
                n_parse_fail_model += pf_model
                n_parse_fail_infra += pf_infra
                placed_this_attempt |= ZN.placed_blocks_from_events(voyager.last_events)
                count = target_count(voyager.last_events, target)
                calls = _skill_calls(voyager.last_events) if executed else {}
                library_called.update(name for name in calls if name in library_names)
                # v29 09-21：运行时探针只看得见"调用"，看不见原版主要的用库方式——"照着
                # 展示出来的源码写"（_ref_use_A.py 的口径：归一化后 >=5 行重合）。算它需要
                # 模型当轮写的代码和当轮展示了哪些技能，这两样原来都没落盘，补在这里。
                append_jsonl_locked(
                    code_path,
                    {
                        "round": voyager.recorder.iteration,
                        "subgoal_index": idx,
                        "subgoal_attempt": tries[idx],
                        "executed": executed,
                        "retrieved": list(
                            getattr(voyager.skill_manager, "last_retrieved_skill_names", []) or []
                        ),
                        "program_name": info.get("program_name"),
                        "program_code": info.get("program_code") or "",
                        "exec_code": info.get("exec_code") or "",
                        "time": _utc_now(),
                    },
                )
                append_jsonl_locked(
                    steps_path,
                    {
                        "round": voyager.recorder.iteration,
                        "subgoal_index": idx,
                        "subgoal": subgoal,
                        "subgoal_attempt": tries[idx],
                        "rollout_step": voyager.action_agent_rollout_num_iter,
                        "executed": executed,
                        "parse_fail_model": pf_model,
                        "parse_fail_infra": pf_infra,
                        "critic_success": bool(info.get("success")),
                        "done": bool(done),
                        "target_count": count,
                        "chain_route": info.get("chain_route"),
                        "program_name": info.get("program_name"),
                        "skill_calls": calls,
                        "time": _utc_now(),
                    },
                )
                if ZN.final_target_ok(count, injected, target):
                    stopped, rec["ended_by"] = "success", "target"
                    break
                if voyager.recorder.iteration >= max_rounds:
                    stopped, rec["ended_by"] = "cap", "cap"
                    break
                # 第 8 轮恰好成功必须先算成功，不能先截断。仅压噪开关打开时启用。
                if noise_on and info.get("success"):
                    rec["ended_by"] = "done"
                    break
                capped, _give_ok = _cap_subgoal(
                    voyager,
                    rec,
                    subgoal,
                    idx,
                    flags,
                    cap_k,
                    stuck,
                    first_start=first_start[idx],
                    is_final=is_final,
                    injected=injected,
                )
                if capped:
                    if rec.get("ended_by") == "harness_give_failed":
                        stopped = "harness_give_failed"
                        last_error = "; ".join(rec.get("give_errors") or []) or "harness_give_failed"
                    elif rec.get("ended_by") == "subgoal_cap_final":
                        stopped = "subgoal_cap_final"
                    elif ZN.final_target_ok(
                        target_count(voyager.last_events, target), injected, target
                    ):
                        stopped, rec["ended_by"] = "success", "target"
                    break
                if done:
                    rec["ended_by"] = "done"
                    break
        except StepExecutionTimeout as err:
            n_timeouts += 1
            rec["ended_by"], rec["error"] = "timeout", _err(err)
            last_error = rec["error"]
            if _recover(voyager):
                crash_streak = 0
                voyager.recorder.record(voyager.last_events, subgoal)  # 超时这一步也算一步
                append_jsonl_locked(
                    steps_path,
                    {
                        "round": voyager.recorder.iteration,
                        "subgoal_index": idx,
                        "subgoal": subgoal,
                        "subgoal_attempt": tries[idx],
                        "timeout": True,
                        "error": rec["error"],
                        "target_count": target_count(voyager.last_events, target),
                        "time": _utc_now(),
                    },
                )
                capped, _give_ok = _cap_subgoal(
                    voyager,
                    rec,
                    subgoal,
                    idx,
                    flags,
                    cap_k,
                    stuck,
                    first_start=first_start[idx],
                    is_final=is_final,
                    injected=injected,
                )
                if capped:
                    if rec.get("ended_by") == "harness_give_failed":
                        stopped = "harness_give_failed"
                        last_error = "; ".join(rec.get("give_errors") or []) or "harness_give_failed"
                    elif rec.get("ended_by") == "subgoal_cap_final":
                        stopped = "subgoal_cap_final"
                    elif ZN.final_target_ok(
                        target_count(voyager.last_events, target), injected, target
                    ):
                        stopped = "success"
            else:
                crashed = True
        except Exception as err:
            rec["ended_by"], rec["error"] = "crash", _err(err)
            last_error = rec["error"]
            _recover(voyager)
            crashed = True
        if crashed:
            n_crashes += 1
            crash_streak += 1
            print(
                f"\033[41m[zeroshot] 第 {crash_streak}/{voyager.env_dead_after} 次连续崩溃：{last_error}\033[0m",
                flush=True,
            )
            if _should_stop_environment_censored(crash_streak, voyager.env_dead_after) or (
                n_crashes >= max_rounds
            ):
                stopped = "env_crash"
            else:
                time.sleep(_env_crash_retry_delay(crash_streak))
        if rec.get("ended_by") in ("subgoal_cap_final", "harness_give_failed"):
            rec["success"] = False
        else:
            rec["success"] = bool(info.get("success")) or (
                bool(rec.get("capped")) and rec.get("ended_by") == "subgoal_cap"
            )
        rec["end_round"] = voyager.recorder.iteration
        rec["steps"] = rec["end_round"] - rec["start_round"]
        results.append(rec)
        voyager.curriculum_agent.update_exploration_progress(info)
        if rec.get("ended_by") == "subgoal_cap_final":
            stopped = stopped or "subgoal_cap_final"
        elif rec.get("ended_by") == "harness_give_failed":
            stopped = "harness_give_failed"
        elif rec["success"] and not fallback:
            completed.append(idx)
            idx += 1
            if idx >= len(subgoals) and ZN.final_target_ok(
                target_count(voyager.last_events, target), injected, target
            ):
                stopped = stopped or "success"
                rec["ended_by"] = rec.get("ended_by") or "target"
    out = {
        "stopped_reason": stopped,
        "subgoal_results": results,
        "subgoals_completed": len(set(completed)),
        # 记进 recorder 的每一步只可能是三者之一：执行了代码 / 被解析打回 / 超时的那一步，即
        # rounds_used = executed_rounds + parse_fail_rounds + parse_fail_infra_rounds + step_timeouts。
        # 每步都被打回（例如找不到 babel）时 executed_rounds=0，事后一眼可见，不会被当成正常的 cap。
        "executed_rounds": n_executed,
        "parse_fail_rounds": n_parse_fail_model,
        "parse_fail_infra_rounds": n_parse_fail_infra,
        "step_timeouts": n_timeouts,
        "step_crashes": n_crashes,
        "library_skills_called": sorted(library_called),
        "error": last_error if stopped in ("env_crash", "harness_give_failed") else None,
    }
    if getattr(cfg, "zs_noise_fix", False) or flags.get("subgoal_cap"):
        out["stuck_segments"] = stuck
        out["injected_counts"] = dict(injected)
        if stopped == "subgoal_cap_final" or any(
            r.get("ended_by") == "subgoal_cap_final" for r in results
        ):
            out["final_subgoal_capped"] = True
    if noise_on:
        out["earned_skips"] = n_earned_skips
        out["give_cascade_skips"] = n_give_cascade
    return out


def run_attempt(cfg, *, task, target, trial):
    """跑一次尝试，返回结果行（不写 --out，由调用方写）。ConfigMismatch 向上抛。"""
    t0 = time.time()
    started = _utc_now()
    attempt_dir = _make_attempt_dir(cfg, target, trial)
    ckpt_dir = os.path.join(attempt_dir, "ckpt")
    library_root = os.path.join(attempt_dir, "library")
    library_skill = os.path.join(library_root, "skill")
    src_skill = os.path.join(cfg.library_src, "skill") if cfg.library_src else None
    row = {
        "arm": cfg.arm,
        "preset": cfg.preset,
        "model": cfg.model,
        "model_returned": None,
        "api_base": cfg.api_base,
        "provider_order": list(cfg.provider_order),
        "providers_seen": [],
        "library_id": cfg.library_id,
        "library_src": cfg.library_src,
        "library_size": cfg.library_size,
        "empty_library": cfg.library_src is None,
        "task": task,
        "target_item": target,
        "trial": trial,
        "attempt_id": cfg.attempt_id,
        "world_seed": cfg.world_seed,
        # cmd = 本次执行了 --reset-world-cmd；fresh_world = 调用方（启动器）用 --world-fresh
        # 声明这次作业本来就给了全新世界；hard_only = 两者都没有，只做了 hard reset
        "world_reset": "cmd" if cfg.reset_world_cmd else ("fresh_world" if cfg.world_fresh else "hard_only"),
        "world_fresh": cfg.world_fresh,
        "max_rounds": cfg.max_rounds,
        "success": False,
        "rounds_used": 0,
        "rounds_to_success": None,
        "stopped_reason": None,
        "subgoals": [],
        "subgoals_completed": 0,
        "subgoal_results": [],
        "decompose_error": None,
        "library_hash_before": None,
        "library_hash_after": None,
        "library_changed": False,
        "library_src_changed": False,
        "library_files": None,
        "vectordb_count_before": None,
        "vectordb_count_after": None,
        "vectordb_check": "unchecked: not reached",
        "retrieval_log_offset": None,
        "seed_records": cfg.seed_records,
        "records_seeded": False,
        "chain_log_seeded_rows": 0,
        "meta_check": "no_source" if cfg.source_meta is None else None,
        "started_at": started,
        "ended_at": None,
        "llm_calls": 0,
        "tokens": {"prompt": 0, "completion": 0, "total": 0},
        "voyager_kwargs": {},
        "executed_rounds": 0,
        "parse_fail_rounds": 0,
        "parse_fail_infra_rounds": 0,
        "step_timeouts": 0,
        "step_crashes": 0,
        "error": None,
        "target_count_final": 0,
        "start_inventory": None,
        "library_skills_called": [],
        "governor_task_offset": None,
        "git_head": None,
        "git_dirty": None,
        "ckpt_dir": ckpt_dir,
        "library_copy": library_root,
    }
    if cfg.reset_world_cmd:
        row["world_reset_cmd"] = cfg.reset_world_cmd
    phase_reason = {
        "world_reset": "world_reset_failed",
        "copy": "setup_failed",
        "construct": "setup_failed",
        "hard_reset": "setup_failed",
        "decompose": "decompose_failed",
        "rollout": "exception",
    }
    phase = "world_reset"
    voyager = None
    src_hash = None
    src_snapshot = None
    gov_ep = None
    print(
        f"\033[35m[zeroshot] attempt: arm={cfg.arm} library={cfg.library_id} task={task!r} "
        f"trial={trial} dir={attempt_dir}\033[0m",
        flush=True,
    )
    try:
        if cfg.reset_world_cmd:
            rc = _run_world_reset(cfg, attempt_dir, task, target, trial)
            row["world_reset_rc"] = rc
            if rc != 0:
                raise RuntimeError(f"--reset-world-cmd 退出码 {rc}（见 {attempt_dir}/world_reset.log）")
        phase = "copy"
        if src_skill:
            src_snapshot = dir_snapshot(src_skill)
            src_hash, _ = library_sha256(src_skill)
            shutil.copytree(src_skill, library_skill)
        else:
            make_empty_library(library_root)
        row["library_hash_before"], row["library_files"] = library_sha256(library_skill)
        if src_hash is not None and src_hash != row["library_hash_before"]:
            raise RuntimeError("库副本与源库哈希不一致（复制过程出错）")
        # 副本里的检索日志是源运行留下的；本次尝试的检索行从这一行之后开始
        row["retrieval_log_offset"] = _count_lines(os.path.join(library_skill, "retrieval_log.jsonl"))
        if cfg.seed_records == "on":
            row["chain_log_seeded_rows"] = seed_chain_log(cfg.library_src, ckpt_dir)
            row["records_seeded"] = row["chain_log_seeded_rows"] > 0
            print(f"\033[35m[zeroshot] seeded chain_log rows={row['chain_log_seeded_rows']}\033[0m", flush=True)

        phase = "construct"
        kwargs = build_voyager_kwargs(cfg, ckpt_dir=ckpt_dir, library_root=library_root, trial=trial)
        row["voyager_kwargs"] = public_kwargs(kwargs)
        reset_chroma_index_cache()
        voyager = Voyager(**kwargs)
        # pilot v2 (both arms): stamp the start of every executed program so step-aware skills can keep the
        # whole step under the 600 s limit (a timed-out round is rolled back); original code never reads it
        # SYSGOV: --gov-pool 时同一包装里追加候选 programs + 前导；不给参数时 stamp 逐字节不变。
        if getattr(cfg, "gov_pool_path", None):
            gov_ep = GovEpisode(
                cfg.gov_pool,
                online=bool(cfg.gov_online),
                identity={
                    "experiment_id": cfg.attempt_id
                    or f"{cfg.library_id}:{task}:t{trial}",
                    "pool_fingerprint": cfg.gov_pool_fingerprint,
                    "attempt_id": cfg.attempt_id,
                    "library_id": cfg.library_id,
                    "trial": trial,
                    "task": task,
                },
            )
        voyager.env.step = make_stamped_env_step(voyager.env.step, gov_ep)
        row["vectordb_count_before"], vectordb_err = _vectordb_count(voyager)
        row["vectordb_check"] = f"unchecked: {vectordb_err}" if vectordb_err else "pending"
        meta = voyager.llm_audit.run_meta
        row["git_head"], row["git_dirty"] = meta.get("git_head"), meta.get("git_dirty")
        if cfg.source_meta is not None:
            diffs = compare_run_meta(cfg.source_meta, meta)
            if diffs:
                raise ConfigMismatch(
                    f"源运行 {cfg.library_src} 的 run_meta 与预设 {cfg.preset} 不一致：{json.dumps(diffs, ensure_ascii=False)}"
                )
            row["meta_check"] = "ok"
        if voyager.skill_manager.governor is not None and cfg.library_src:
            row["governor_task_offset"] = continue_governor_timeline(
                voyager.skill_manager, _governor_t_end(cfg.source_meta, voyager.skill_manager)
            )
        meta["zeroshot"] = {
            k: row[k]
            for k in ("arm", "preset", "library_id", "library_src", "task", "target_item", "trial",
                      "attempt_id", "world_seed", "world_reset", "max_rounds", "library_hash_before",
                      "governor_task_offset", "seed_records", "chain_log_seeded_rows")
        }
        if getattr(cfg, "zs_noise_fix", False) or ZN.any_noise_flag(getattr(cfg, "zs_noise_flags", None)):
            meta["zeroshot"]["zs_noise_fix"] = True
            meta["zeroshot"]["zs_noise_flags"] = dict(cfg.zs_noise_flags)
            meta["zeroshot"]["zs_subgoal_cap"] = int(cfg.zs_subgoal_cap)
        voyager.llm_audit._write_run_meta()

        phase = "hard_reset"
        reset_opts = {"mode": "hard", "wait_ticks": voyager.env_wait_ticks}
        flags = getattr(cfg, "zs_noise_flags", None) or {}
        # E4: a seed other than 099 starts at its own standable surface spawn (same probe as the isolated harness);
        # for 099 this block runs exactly the old code path.
        own_spawn = bool(flags.get("fixed_start")) and int(cfg.world_seed) != int(ZN.ZS_WORLD_SEED)
        spawn = dict(ZN.ZS_SPAWN)
        if flags.get("fixed_start") and not own_spawn:
            reset_opts["position"] = dict(ZN.ZS_SPAWN)
        voyager.env.reset(options=reset_opts)
        voyager.last_events = voyager.env.step("")
        if own_spawn:
            probe = voyager.env.step(ZN.WORLD_SPAWN_JS, programs="")
            found = ZN.parse_world_spawn(probe)
            if found is None:
                raise RuntimeError("E4 world spawn probe failed (no [harness] world_spawn line)")
            spawn = found
            row["own_spawn"] = dict(spawn)
        if flags.get("fixed_start"):
            voyager.last_events = voyager.env.step(
                ZN.fixed_start_js(spawn), programs=""
            )
        noise_on = bool(getattr(cfg, "zs_noise_fix", False) or ZN.any_noise_flag(flags))
        if noise_on:
            voyager.last_events = voyager.env.step(ZN.clear_field_js(), programs="")
        row["start_inventory"] = dict(voyager.last_events[-1][1].get("inventory") or {})

        phase = "decompose"
        subgoals, decompose_error = [], None
        if flags.get("canonical_subgoals"):
            canon = ZN.canonical_subgoals_for(task)
            if canon:
                subgoals, decompose_error = canon, "canonical"
        if not subgoals:
            try:
                subgoals, decompose_error = _clean_subgoals(voyager.decompose_task(task))
            except ValueError as err:  # 模型回复解析失败（含 JSONDecodeError）：照常做，退回原任务
                subgoals, decompose_error = [], _err(err)
        if not subgoals:
            subgoals = [task]
            decompose_error = decompose_error or "empty decomposition"
        row["subgoals"], row["decompose_error"] = subgoals, decompose_error

        phase = "rollout"
        steps_path = os.path.join(ckpt_dir, "metrics", "zeroshot_steps.jsonl")
        os.makedirs(os.path.dirname(steps_path), exist_ok=True)
        row.update(
            _subgoal_loop(voyager, cfg, task=task, target=target, subgoals=subgoals, steps_path=steps_path)
        )
        if getattr(cfg, "zs_noise_fix", False):
            row["zs_noise_fix"] = True
            row["zs_noise_flags"] = dict(getattr(cfg, "zs_noise_flags", None) or {})
            row.setdefault("stuck_segments", [])
    except ConfigMismatch:
        raise
    except Exception as err:
        row["stopped_reason"] = phase_reason[phase]
        row["error"] = _err(err)
        print(f"\033[41m[zeroshot] {row['stopped_reason']}: {row['error']}\033[0m", flush=True)
    finally:
        if row["library_hash_before"] is not None:
            row["library_hash_after"], _ = library_sha256(library_skill)
            row["library_changed"] = row["library_hash_after"] != row["library_hash_before"]
        if voyager is not None and row["vectordb_check"] == "pending":
            row["vectordb_count_after"], vectordb_err = _vectordb_count(voyager)
            if vectordb_err:
                row["vectordb_check"] = f"unchecked: {vectordb_err}"
            elif row["vectordb_count_after"] == row["vectordb_count_before"]:
                row["vectordb_check"] = "ok"
            else:
                row["vectordb_check"] = "changed"
                row["library_changed"] = True
        if src_snapshot is not None:
            row["library_src_changed"] = dir_snapshot(src_skill) != src_snapshot
        if voyager is not None:
            row["rounds_used"] = int(voyager.recorder.iteration)
            row["target_count_final"] = target_count(voyager.last_events, target)
            meta = voyager.llm_audit.run_meta
            row["model_returned"] = meta.get("first_model_returned")
            row["providers_seen"] = list(meta.get("providers_seen") or [])
        row["success"] = row["stopped_reason"] == "success"
        row["rounds_to_success"] = row["rounds_used"] if row["success"] else None
        row["llm_calls"], row["tokens"] = llm_usage(ckpt_dir)
        if getattr(cfg, "zs_noise_fix", False) or ZN.any_noise_flag(
            getattr(cfg, "zs_noise_flags", None)
        ):
            row["llm_calls_by_kind"] = llm_usage_by_kind(ckpt_dir)
            row["elapsed_s"] = round(time.time() - t0, 3)
        row["ended_at"] = _utc_now()
        if getattr(cfg, "gov_pool_path", None):
            row["gov_pool_fingerprint"] = cfg.gov_pool_fingerprint
            row["gov_online"] = bool(cfg.gov_online)
            if gov_ep is not None:
                row.update(gov_ep.result_fields())
            else:
                row.update(empty_gov_metrics())
        if voyager is not None:
            try:
                voyager._record_run_stop(row["stopped_reason"] or "config_mismatch", row["rounds_used"])
            except Exception as err:
                print(f"[zeroshot] 写 run_meta 停因失败：{_err(err)}", flush=True)
            try:
                voyager.close()
            except Exception as err:
                print(f"[zeroshot] close() failed (non-fatal): {_err(err)}", flush=True)
        try:
            os.makedirs(os.path.join(ckpt_dir, "metrics"), exist_ok=True)
            with open(os.path.join(ckpt_dir, "metrics", "zeroshot_result.json"), "w", encoding="utf-8") as fp:
                json.dump(row, fp, ensure_ascii=False, indent=2, default=str)
        except Exception as err:
            print(f"[zeroshot] 写 zeroshot_result.json 失败：{_err(err)}", flush=True)
    return row


def exit_code_for(rows):
    if any(r.get("library_changed") or r.get("library_src_changed") for r in rows):
        return EXIT_LIBRARY_CHANGED
    if any(r.get("stopped_reason") not in NORMAL_STOPS for r in rows):
        return EXIT_ABNORMAL
    return EXIT_OK


# ---------------------------------------------------------------- 命令行
def build_parser():
    parser = argparse.ArgumentParser(
        prog="run_zeroshot.py",
        description="v29 零样本泛化测试：冻结的技能库 + 新世界 + 固定任务，每任务最多 --max-rounds 步。",
    )
    lib = parser.add_mutually_exclusive_group(required=True)
    lib.add_argument("--library-src", help="源运行的 ckpt 目录（含 skill/ 与 metrics/run_meta.json）；只读")
    lib.add_argument("--empty-library", action="store_true", help="空库对照（结果行 arm=EMPTY）")
    parser.add_argument("--arm", required=True, choices=sorted(PRESETS),
                        help="预设：OLD=原版默认；C1p=v28 C1′（链式等开关全开）")
    parser.add_argument("--tasks", nargs="+", default=None,
                        help="一个或多个任务：默认表里的任务文本或目标物品名，或 '任务文本=目标物品'；"
                             "默认 4 个：钻石镐、金剑、岩浆桶、指南针")
    parser.add_argument("--trials", type=int, default=DEFAULT_TRIALS)
    parser.add_argument("--trial-index", type=int, default=None,
                        help="trial 编号从几开始（只跑一次时就是这次的编号）；不给按 1..trials")
    parser.add_argument("--max-rounds", type=int, default=DEFAULT_MAX_ROUNDS)
    parser.add_argument("--world-seed", type=int, default=DEFAULT_WORLD_SEED,
                        help="只记录；服务器由外部启动")
    parser.add_argument("--mc-port", type=int, required=True)
    parser.add_argument("--server-port", type=int, default=3000)
    parser.add_argument("--out", required=True, help="结果 JSONL（每次尝试追加一行）")
    parser.add_argument("--work-dir", required=True, help="每次尝试的库副本与 ckpt 放在这下面")
    parser.add_argument("--reset-world-cmd", default=None,
                        help="每次尝试前执行的 shell 命令（换回干净世界）；环境变量 ZS_* 给出本次尝试信息")
    parser.add_argument("--seed-records", choices=["auto", "on", "off"], default="auto",
                        help="把源运行的 metrics/chain_log.jsonl 拷进本次 ckpt，让 C1p 规划器目录里的 "
                             "record= 带上长跑的执行记录；auto=C1p 开、OLD 关")
    parser.add_argument("--world-fresh", action="store_true", default=False,
                        help="声明本次作业跑在一个全新世界上（启动器每个作业新建服务器目录时传）；"
                             "只影响结果行的 world_reset（cmd > fresh_world > hard_only）")
    parser.add_argument("--attempt-id", default=None, help="原样写进每条结果行的 attempt_id")
    parser.add_argument("--library-id", default=None,
                        help="原样写进 library_id；不给则 源库=<arm>_w<trial号>，空库=EMPTY_<arm>")
    # 以下四个与 run_voyager.py 同名、同义、同默认
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--model-name", default=None, help=f"不传则 {DEFAULT_MODEL}（run_voyager --arm A 推出的名字）")
    parser.add_argument("--api-key-env", default=DEFAULT_API_KEY_ENV)
    parser.add_argument("--extra-base-skills", nargs="+", default=None,
                        help="额外注入的基础技能文件名（voyager/control_primitives_extra/ 下，"
                             "不带 .js）。不传＝默认行为，与改动前逐字节相同")
    parser.add_argument(
        "--gov-pool",
        default=None,
        help="治理叶子候选池 JSON（键 → 候选列表）。不传＝不注入，与改动前逐字节相同",
    )
    parser.add_argument(
        "--gov-online",
        action="store_true",
        default=False,
        help="在线账本：每步把上一步 [govledger] 写回 __govLedger；须同时给 --gov-pool",
    )
    parser.add_argument("--provider-order", default="",
                        help="OpenRouter 钉供应商，逗号分隔；非空时 allow_fallbacks=false")
    add_v30_arguments(parser)
    noise = parser.add_argument_group(
        "zs-noise-fix",
        "零样本压噪（默认全关；--zs-noise-fix = 下列 1–5 全开）",
    )
    noise.add_argument(
        "--zs-noise-fix",
        action="store_true",
        default=False,
        help="总开关：放置判定 + 计数判定 + 固定子目标 + 每子目标上限 8 + 固定起始",
    )
    noise.add_argument("--zs-place-judge", action="store_true", default=False)
    noise.add_argument("--zs-count-judge", action="store_true", default=False)
    noise.add_argument("--zs-canonical-subgoals", action="store_true", default=False)
    noise.add_argument("--zs-subgoal-cap-on", action="store_true", default=False)
    noise.add_argument("--zs-fixed-start", action="store_true", default=False)
    noise.add_argument(
        "--zs-subgoal-cap",
        type=int,
        default=8,
        help="每子目标轮数上限（仅 --zs-noise-fix / --zs-subgoal-cap-on 时生效）",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        cfg = resolve_config(args)
    except ConfigError as err:
        print(f"[zeroshot] 配置错误：{err}", file=sys.stderr, flush=True)
        return EXIT_SETUP
    print(
        f"[zeroshot] arm={cfg.arm} preset={cfg.preset} library_id={cfg.library_id} "
        f"library_src={cfg.library_src} size={cfg.library_size} model={cfg.model} "
        f"api_base={cfg.api_base} provider_order={cfg.provider_order} tasks={cfg.tasks} "
        f"trials={cfg.trial_numbers} max_rounds={cfg.max_rounds} out={cfg.out} "
        f"code={os.path.dirname(os.path.abspath(__file__))}",
        flush=True,
    )
    rows = []
    for trial in cfg.trial_numbers:
        for task, target in cfg.tasks:
            try:
                row = run_attempt(cfg, task=task, target=target, trial=trial)
            except ConfigMismatch as err:
                print(f"[zeroshot] 配置核对失败，停止：{err}", file=sys.stderr, flush=True)
                return EXIT_SETUP
            append_jsonl_locked(cfg.out, row)
            rows.append(row)
            print(
                f"\033[35m[zeroshot] result: library={row['library_id']} task={task!r} trial={trial} "
                f"stopped_reason={row['stopped_reason']} rounds_used={row['rounds_used']} "
                f"library_changed={row['library_changed']}\033[0m",
                flush=True,
            )
    code = exit_code_for(rows)
    print(f"[zeroshot] done: {len(rows)} attempt(s), exit code {code}", flush=True)
    return code
