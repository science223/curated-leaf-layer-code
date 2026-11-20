import copy
import json
import os
import re
import time
from typing import Dict

import voyager.utils as U
from .env import VoyagerEnv
from .env.bridge import StepExecutionTimeout

from .agents import ActionAgent
from .agents import CriticAgent
from .agents import CurriculumAgent
from .agents import SkillManager
from .agents.skill import _MODE_UNSET, RUNTIME_CALL_PROBE_ENABLED
from .agents.action import PARSER_INFRA_PREFIX
from .agents.curriculum import CurriculumStalled
from .iteration_logger import append_iteration_metrics
from .intent import (
    call_llm_with_retry,
    infer_task_intent,
    purify_fingerprint,
    set_llm_hard_timeout,
)
from .llm_meta import RunLLMAudit, build_extra_body, is_openrouter_base, create_chat_openai


def _unpack_critic_result(result):
    """Accept 2-tuple (legacy mocks) or 3-tuple (success, critique, skill_credit)."""
    if not isinstance(result, tuple):
        return bool(result), "", {}
    success = result[0]
    critique = result[1] if len(result) > 1 else ""
    credit = result[2] if len(result) > 2 else {}
    if not isinstance(credit, dict):
        credit = {}
    return success, critique, credit


def _code_version():
    """(git_head, dirty) —— 2026-09-11 审计 01 C-6：run_meta 不记代码版本，事后
    分不清一条运行跑的是哪一版。取不到时返回 (None, None)，绝不抛。"""
    import subprocess

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo, capture_output=True, text=True, timeout=10,
        ).stdout.strip() or None
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=repo, capture_output=True, text=True, timeout=10,
        ).stdout
        dirty = bool(status.strip()) if head else None
        return head, dirty
    except Exception:
        return None, None


# 🔴 计分口径开关。默认 "program_code+exec_code"，即修好之后的正确行为。
#
# 2026-09-07 定案：模型在 program_code 里**重写**检索到的技能（只有定义、
# 没有调用），真正的调用写在 exec_code 里（形如 `await mineThreeIronOre(bot);`，
# 二十来个字符）。而 update_skill_usage 一直只收 program_code，于是：
#   text        匹配到定义头 → 命中（对的答案、错的理由，即所谓自匹配缺陷）
#   text_nodefs 剔掉定义头后 program_code 里再无痕迹 → 169 个检索轮命中 0 次，
#               而运行时真值是 32 次
# 后果：B/D 臂全部用 text_nodefs 计分，success_count 恒 0，
# 退役规则 u(s)=(success+1)/(retrieved+2) 分子恒空，退化成「按检索次数退役」——
# 与 09-04 发现的那个退化配置是同一种病，换信号并没有治好它。
# 拼上 exec_code 后，文本判定与运行时真值**逐轮 100% 一致**（169/169）。
#
# 设成环境变量是为了能把旧行为复现出来做对照，不是为了留退路：
#   SCORING_SCOPE=program_code   复现 09-07 之前的错误口径
SCORING_SCOPE = os.environ.get("SCORING_SCOPE", "program_code+exec_code")


def _scoring_text(info):
    """交给 update_skill_usage 做调用判定的文本。"""
    prog = info.get("program_code", "") or ""
    if SCORING_SCOPE == "program_code":
        return prog
    return prog + "\n" + (info.get("exec_code", "") or "")


def _next_env_crash_streak(current_streak, rollout_crashed):
    return current_streak + 1 if rollout_crashed else 0


def _should_stop_environment_censored(crash_streak, env_dead_after):
    return env_dead_after > 0 and crash_streak >= env_dead_after


# 2026-09-04 实测：上限 3 次、退避 10s+20s，累计只有约 30 秒容忍。
# 当天 06:13–08:25 的上游抖动就是这样把 B:t2 与 B:t3 分别杀死在第 77、83 轮
# （两次都是 curriculum.qa_questions 连吃 3 次 "Connection error."）。
# 放宽到与 rollout 崩溃同一口径：5 次、15/30/60/120，累计约 3.8 分钟。
CURRICULUM_FAILURE_LIMIT = 5
CURRICULUM_RETRY_BASE_SECONDS = 15
CURRICULUM_RETRY_MAX_SECONDS = 300


def _curriculum_retry_delay(failure_streak):
    return min(
        CURRICULUM_RETRY_BASE_SECONDS * (2 ** max(0, failure_streak - 1)),
        CURRICULUM_RETRY_MAX_SECONDS,
    )


# rollout 崩溃的退避。原先只固定 sleep(3)，于是连续 5 次崩溃能在
# 50 秒内跑完——2026-09-04 08:19 一次约一分钟的上游抖动就这样把
# 六个 C/D 运行全部杀死在第 0 轮（每个都是 5 次"Connection error."）。
# 课程失败那条路径本来就有指数退避，这里补齐，口径一致。
# 只影响崩溃后的等待时长，不改变智能体行为，因此不影响可比性。
ENV_CRASH_RETRY_BASE_SECONDS = 15
ENV_CRASH_RETRY_MAX_SECONDS = 300


def _env_crash_retry_delay(crash_streak):
    return min(
        ENV_CRASH_RETRY_BASE_SECONDS * (2 ** max(0, crash_streak - 1)),
        ENV_CRASH_RETRY_MAX_SECONDS,
    )


# TODO: remove event memory
class Voyager:
    def __init__(
        self,
        mc_port: int = None,
        azure_login: Dict[str, str] = None,
        server_port: int = 3000,
        openai_api_key: str = None,
        # --- DeepSeek 适配新增参数 ---
        openai_api_base: str = None,          # DeepSeek: "https://api.deepseek.com/v1"
        # ----------------------------
        env_wait_ticks: int = 20,
        env_request_timeout: int = 300,
        env_dead_after: int = 5,
        max_iterations: int = 160,
        reset_placed_if_failed: bool = False,
        action_agent_model_name: str = "deepseek-v4-flash",
        action_agent_temperature: float = 0,
        action_agent_task_max_retries: int = 4,
        action_agent_show_chat_log: bool = True,
        action_agent_show_execution_error: bool = True,
        skill_api_prompt: bool = False,
        skill_params: bool = False,
        chain: bool = False,
        planner_v2: bool = False,
        noeffect_guard: bool = False,
        stall_abort: bool = True,
        pause_verify: bool = False,
        gap_reference: bool = False,
        chain_time_budget_s: int = 0,
        chain_node_check: bool = False,
        chain_node_repair: bool = False,
        curriculum_agent_model_name: str = "deepseek-v4-flash",
        curriculum_agent_temperature: float = 0,
        curriculum_agent_qa_model_name: str = "deepseek-v4-flash",
        curriculum_agent_qa_temperature: float = 0,
        curriculum_agent_warm_up: Dict[str, int] = None,
        curriculum_agent_core_inventory_items: str = r".*_log|.*_planks|stick|crafting_table|furnace"
        r"|cobblestone|dirt|coal|.*_pickaxe|.*_sword|.*_axe",
        curriculum_agent_mode: str = "auto",
        critic_agent_model_name: str = "deepseek-v4-flash",
        critic_agent_temperature: float = 0,
        critic_agent_mode: str = "auto",
        skill_manager_model_name: str = "deepseek-v4-flash",
        skill_manager_temperature: float = 0,
        skill_manager_retrieval_top_k: int = 5,
        skill_manager_curation_enabled: bool = False,
        skill_manager_retrieval_pool_size: int = 20,
        skill_manager_mmr_lambda: float = 0.7,
        skill_manager_retire_min_exposure: int = 12,
        skill_manager_retire_use_threshold: float = 0.15,
        skill_manager_curation_interval: int = 15,
        skill_manager_contribution_mode: str = "binary",
        skill_manager_call_detect: str = "runtime",
        skill_manager_structured_retrieval: bool = False,
        skill_manager_substitute_guard: str = "off",
        skill_manager_substitute_undecided: str = "allow_retire",
        skill_manager_curation_mode=_MODE_UNSET,
        skill_manager_dedup_mode=_MODE_UNSET,
        skill_manager_intent_fallback_dedup: bool = True,
        skill_manager_retrieval_gate_enabled: bool = False,
        skill_manager_protect_critical_skills: bool = False,
        skill_manager_protected_skills=None,
        skill_manager_governance: str = "static",
        skill_manager_governance_capacity=None,
        openai_api_request_timeout: int = 240,
        openrouter_provider_order=None,
        ckpt_dir: str = "ckpt",
        skill_library_dir: str = None,
        resume: bool = False,
        llm_thinking: str = "disabled",
        experiment_arm: str = None,
        experiment_trial: int = None,
        freeze_library: bool = False,
        force_difficulty: str = None,
        extra_base_skills=None,
        lru_evict=True,
        verify_critic=False,
        v30_ledger=False,
        v30_named_actions=False,
        v30_shared_fixes=False,
        v30_anti_stall=False,
        v30_knowledge_text=False,
        zs_fixed_time=None,
        zs_fixed_weather: bool = False,
    ):
        """
        The main class for Voyager.
        Action agent is the iterative prompting mechanism in paper.
        Curriculum agent is the automatic curriculum in paper.
        Critic agent is the self-verification in paper.
        Skill manager is the skill library in paper.
        :param mc_port: minecraft in-game port
        :param azure_login: minecraft login config
        :param server_port: mineflayer port
        :param openai_api_key: openai/deepseek api key
        :param openai_api_base: api base url, set to "https://api.deepseek.com/v1" for DeepSeek
        :param env_wait_ticks: how many ticks at the end each step will wait, if you found some chat log missing,
        you should increase this value
        :param env_request_timeout: how many seconds the Python side waits for one /step HTTP request to
        finish executing generated code in mineflayer. The default 300 seconds matches the longest built-in
        primitive timeout such as killMob, so it should not truncate a single primitive call.
        :param env_dead_after: stop learn() after this many consecutive rollout crashes. Set to 0 or a
        negative value to disable dead-environment early stopping.
        :param reset_placed_if_failed: whether to reset placed blocks if failed, useful for building task
        :param action_agent_model_name: action agent model name
        :param action_agent_temperature: action agent temperature
        :param action_agent_task_max_retries: how many times to retry if failed
        :param skill_api_prompt: whether retrieved skills are presented to the action agent as
        explicitly callable APIs in a dedicated prompt section (presentation experiment switch);
        when False the rendered action prompt is byte-identical to the pre-switch template
        :param skill_params: whether action skills may take extra parameters with defaults
        and must include an Invocation block; when False, prompts and parser are
        byte-identical to the pre-switch behavior
        :param curriculum_agent_model_name: curriculum agent model name
        :param curriculum_agent_temperature: curriculum agent temperature
        :param curriculum_agent_qa_model_name: curriculum agent qa model name
        :param curriculum_agent_qa_temperature: curriculum agent qa temperature
        :param curriculum_agent_warm_up: info will show in curriculum human message
        if completed task larger than the value in dict, available keys are:
        {
            "context": int,
            "biome": int,
            "time": int,
            "other_blocks": int,
            "nearby_entities": int,
            "health": int,
            "hunger": int,
            "position": int,
            "equipment": int,
            "chests": int,
            "optional_inventory_items": int,
        }
        :param curriculum_agent_core_inventory_items: only show these items in inventory before optional_inventory_items
        reached in warm up
        :param curriculum_agent_mode: "auto" for automatic curriculum, "manual" for human curriculum
        :param critic_agent_model_name: critic agent model name
        :param critic_agent_temperature: critic agent temperature
        :param critic_agent_mode: "auto" for automatic critic ,"manual" for human critic
        :param skill_manager_model_name: skill manager model name
        :param skill_manager_temperature: skill manager temperature
        :param skill_manager_retrieval_top_k: how many skills to retrieve for each task
        :param skill_manager_curation_enabled: whether to use curated diversity retrieval
        :param skill_manager_retrieval_pool_size: candidate pool size for curated retrieval
        :param skill_manager_mmr_lambda: relevance/usefulness vs diversity tradeoff
        :param skill_manager_retire_min_exposure: minimum retrieval exposure before retirement
        :param skill_manager_retire_use_threshold: retirement threshold for skill usefulness
        :param skill_manager_curation_interval: task interval for running skill retirement
        :param skill_manager_contribution_mode: retirement usefulness numerator source,
        "binary" (success_count, default) or "effect_delta" (inventory-gain contribution_sum)
        :param skill_manager_substitute_guard: retirement substitute protection,
        "off" (default) or "fp" (protect low-use skills without an effect-fingerprint substitute)
        :param skill_manager_substitute_undecided: when substitute_guard="fp" and effect fingerprint
        cannot decide, "allow_retire" (default, fail-open) or "protect"
        :param skill_manager_curation_mode: curation mode, or unset/None to use SkillManager env fallback
        :param skill_manager_dedup_mode: dedup mode, or unset/None to use SkillManager env fallback
        :param skill_manager_intent_fallback_dedup: whether effect dedup can fall back to intent grouping
        :param skill_manager_retrieval_gate_enabled: whether to filter low-use retrieval candidates
        :param skill_manager_protect_critical_skills: whether retirement should skip protected skills
        :param skill_manager_protected_skills: skill names protected from retirement
        :param openai_api_request_timeout: how many seconds to wait for openai api
        :param ckpt_dir: checkpoint dir
        :param skill_library_dir: skill library dir
        :param resume: whether to resume from checkpoint
        :param freeze_library: v29 zero-shot only (default False). The skill library in
        skill_library_dir (a copy) becomes read-only: add/archive/dedup/retire/LRU eviction are
        skipped and only logged. When False nothing changes.
        :param force_difficulty: v29 zero-shot only (default None = unchanged). Pin the world
        difficulty to this value on every reset instead of letting it follow the number of completed
        sub-goals (peaceful -> easy above 15), which would otherwise let a faster arm change its own
        environment mid-run
        :param chain_node_check: v29 chain only: wrap every chain node call in try/catch; only a thrown
        error stops the chain, re-thrown with the node index (chain_log node_fail). A library skill that
        returns without the expected inventory change is only recorded (chain_log node_noeffect) and
        does not stop the chain
        :param chain_node_repair: v29 chain only, requires chain_node_check: pick one repair target per
        failed attempt -- the node that threw; else, if nothing threw but the attempt failed, the first
        non-place library node without the expected inventory change (suspected cause); else nothing.
        A library target is disabled for the rest of the task, failed new gap code is shown to the next
        gap writer, and a node-indexed critique goes to the next attempt
        """
        # v29：返修要靠逐节点检查给出的失败信号，单开返修直接报错（在起环境之前）。
        if chain_node_repair and not chain_node_check:
            raise ValueError("--chain-node-repair requires --chain-node-check")
        # init env
        self.env = VoyagerEnv(
            mc_port=mc_port,
            azure_login=azure_login,
            server_port=server_port,
            request_timeout=env_request_timeout,
            pause_verify=pause_verify,
        )
        self.env_wait_ticks = env_wait_ticks
        self.env_request_timeout = env_request_timeout
        self.env_dead_after = env_dead_after
        self.reset_placed_if_failed = reset_placed_if_failed
        self.max_iterations = max_iterations
        self.ckpt_dir = ckpt_dir
        self.skill_manager_curation_enabled = skill_manager_curation_enabled
        self.skill_manager_retrieval_pool_size = skill_manager_retrieval_pool_size
        self.skill_manager_mmr_lambda = skill_manager_mmr_lambda
        self.skill_manager_retire_min_exposure = skill_manager_retire_min_exposure
        self.skill_manager_retire_use_threshold = skill_manager_retire_use_threshold
        self.skill_manager_curation_interval = skill_manager_curation_interval
        self.skill_manager_contribution_mode = skill_manager_contribution_mode
        self.skill_manager_substitute_guard = skill_manager_substitute_guard
        self.skill_manager_substitute_undecided = skill_manager_substitute_undecided
        self._llm_audit_iter = None
        self.llm_audit = RunLLMAudit(
            ckpt_dir,
            arm=experiment_arm,
            trial=experiment_trial,
            model_requested=action_agent_model_name,
            curation_enabled=skill_manager_curation_enabled,
            thinking=llm_thinking,
            resume=resume,
        )
        # 2026-09-11 审计 01 C-8：LLM 硬超时统一由这里设置（以前 intent.py 写死 90s，
        # openai_api_request_timeout=240 从不生效）。
        llm_hard_timeout_s = set_llm_hard_timeout(openai_api_request_timeout)
        git_head, git_dirty = _code_version()
        self.llm_audit.run_meta.update(
            {
                "env_request_timeout": self.env_request_timeout,
                "env_dead_after": self.env_dead_after,
                "skill_api_prompt": skill_api_prompt,
                "skill_params_enabled": bool(skill_params),
                "chain_enabled": bool(chain),
                # ---- 2026-09-11 审计 01 C-6 补的字段 ----
                "max_iterations": max_iterations,
                "openai_api_base": openai_api_base,
                "openai_api_request_timeout": openai_api_request_timeout,
                "llm_hard_timeout_s": llm_hard_timeout_s,
                "runtime_call_probe_enabled": RUNTIME_CALL_PROBE_ENABLED,
                "action_agent_task_max_retries": action_agent_task_max_retries,
                "retrieval_top_k": skill_manager_retrieval_top_k,
                "retrieval_pool_size": skill_manager_retrieval_pool_size,
                "mmr_lambda": skill_manager_mmr_lambda,
                "git_head": git_head,
                "git_dirty": git_dirty,
                "code_batch": "fix_0911",
                "skill_api_signature_rule": "last_async_function_bot",
                "iteration_limit_rule": "stop_when_iteration_ge_max",
                "probe_missing_rounds": 0,
            }
        )
        self.llm_audit._write_run_meta()

        # --- DeepSeek 适配：设置 API Key 和 Base URL ---
        os.environ["OPENAI_API_KEY"] = openai_api_key
        if openai_api_base:
            os.environ["OPENAI_API_BASE"] = openai_api_base
        # F16：OpenRouter 钉供应商列表走环境变量给 create_chat_openai；记进 run_meta。
        provider_order = list(openrouter_provider_order or [])
        if provider_order:
            os.environ["OPENROUTER_PROVIDER_ORDER"] = ",".join(provider_order)
        self.llm_audit.run_meta.update(
            {
                "openrouter_mode": is_openrouter_base(openai_api_base),
                "provider_order": provider_order,
                "allow_fallbacks": (False if provider_order else None),
                "request_extra_body": build_extra_body(
                    llm_thinking, api_base=openai_api_base, provider_order=provider_order
                ),
            }
        )
        self.llm_audit._write_run_meta()
        # -----------------------------------------------

        # init agents
        self.action_agent = ActionAgent(
            model_name=action_agent_model_name,
            temperature=action_agent_temperature,
            request_timout=openai_api_request_timeout,
            ckpt_dir=ckpt_dir,
            resume=resume,
            chat_log=action_agent_show_chat_log,
            execution_error=action_agent_show_execution_error,
            thinking=llm_thinking,
            llm_audit=self.llm_audit,
            skill_api_prompt=skill_api_prompt,
            skill_params=skill_params,
            extra_base_skills=extra_base_skills,
            v30_shared_fixes=v30_shared_fixes,
            v30_knowledge_text=v30_knowledge_text,
        )
        self.action_agent_task_max_retries = action_agent_task_max_retries
        self.chain = bool(chain)
        self.planner_v2 = bool(chain) and bool(planner_v2)
        self.noeffect_guard = bool(noeffect_guard)
        self.stall_abort = bool(stall_abort)
        self._noeffect_idle_counts = {}
        self._chain_fallback = False
        self._chain_critique = ""
        # v28：写缺口代码时补齐原版 action agent 每轮都有的两样东西——检索到的技能源码
        # （参考）与上一轮的代码/评语（反馈）。开关关时这三个字段只被赋值、从不被读。
        self.gap_reference = bool(gap_reference)
        self.chain_time_budget_s = int(chain_time_budget_s or 0)
        self._gap_ref_skills = []
        self._gap_ref_names = []
        self._gap_last_code = ""
        self._gap_critique = ""
        # v29：--chain-node-check（逐节点出错信号）/ --chain-node-repair（本任务内当场返修），
        # 只在链式执行里生效。开关关时下面两个状态字段只被赋值、从不被读。
        self.chain_node_check = self.chain and bool(chain_node_check)
        self.chain_node_repair = self.chain_node_check and bool(chain_node_repair)
        self._node_disabled = {}
        self._node_gap_fix = ""
        self.chain_llm = None
        self.chain_review_llm = None
        if self.chain:
            self.chain_llm = create_chat_openai(
                model_name=action_agent_model_name,
                temperature=action_agent_temperature,
                request_timeout=openai_api_request_timeout,
                thinking=llm_thinking,
                audit=self.llm_audit,
                agent="chain.plan",
            )
            if self.planner_v2:
                self.chain_review_llm = create_chat_openai(
                    model_name=action_agent_model_name,
                    temperature=action_agent_temperature,
                    request_timeout=openai_api_request_timeout,
                    thinking=llm_thinking,
                    audit=self.llm_audit,
                    agent="chain.review",
                )
                self.llm_audit.run_meta["planner_v2"] = True
                self.llm_audit._write_run_meta()
        self.curriculum_agent = CurriculumAgent(
            model_name=curriculum_agent_model_name,
            temperature=curriculum_agent_temperature,
            qa_model_name=curriculum_agent_qa_model_name,
            qa_temperature=curriculum_agent_qa_temperature,
            request_timout=openai_api_request_timeout,
            ckpt_dir=ckpt_dir,
            resume=resume,
            mode=curriculum_agent_mode,
            warm_up=curriculum_agent_warm_up,
            core_inventory_items=curriculum_agent_core_inventory_items,
            thinking=llm_thinking,
            llm_audit=self.llm_audit,
        )
        if not self.stall_abort:
            # 2026-09-13：背包满护栏会掐掉仍在进步的运行（修 op 后才触发，只伤链式臂）；保留逃逸，不再结束整轮。
            self.curriculum_agent.stall_abort_after = float("inf")
        self.critic_agent = CriticAgent(
            model_name=critic_agent_model_name,
            temperature=critic_agent_temperature,
            request_timout=openai_api_request_timeout,
            mode=critic_agent_mode,
            thinking=llm_thinking,
            llm_audit=self.llm_audit,
        )
        # v29 零样本：冻结库（默认关；关时不传这个参数，SkillManager 调用与改动前相同）。
        freeze_kwargs = {}
        if freeze_library:
            if not skill_library_dir or os.path.abspath(skill_library_dir) == os.path.abspath(ckpt_dir):
                raise ValueError("freeze_library=True 需要与 ckpt_dir 不同的 skill_library_dir（库副本）")
            freeze_kwargs = {"freeze_library": True}
        self.skill_manager = SkillManager(
            model_name=skill_manager_model_name,
            temperature=skill_manager_temperature,
            retrieval_top_k=skill_manager_retrieval_top_k,
            request_timout=openai_api_request_timeout,
            ckpt_dir=skill_library_dir if skill_library_dir else ckpt_dir,
            resume=True if resume or skill_library_dir else False,
            curation_enabled=skill_manager_curation_enabled,
            retrieval_pool_size=skill_manager_retrieval_pool_size,
            mmr_lambda=skill_manager_mmr_lambda,
            retire_min_exposure=skill_manager_retire_min_exposure,
            retire_use_threshold=skill_manager_retire_use_threshold,
            curation_interval=skill_manager_curation_interval,
            contribution_mode=skill_manager_contribution_mode,
            substitute_guard=skill_manager_substitute_guard,
            substitute_undecided=skill_manager_substitute_undecided,
            curation_mode=skill_manager_curation_mode,
            dedup_mode=skill_manager_dedup_mode,
            intent_fallback_dedup=skill_manager_intent_fallback_dedup,
            retrieval_gate_enabled=skill_manager_retrieval_gate_enabled,
            protect_critical_skills=skill_manager_protect_critical_skills,
            protected_skills=skill_manager_protected_skills,
            call_detect=skill_manager_call_detect,
            structured_retrieval=skill_manager_structured_retrieval,
            skill_params=skill_params,
            extra_base_skills=extra_base_skills,
            thinking=llm_thinking,
            llm_audit=self.llm_audit,
            governance=skill_manager_governance,
            governance_capacity=skill_manager_governance_capacity,
            noeffect_guard=noeffect_guard,
            lru_evict=lru_evict,
            **freeze_kwargs,
        )
        # P0-1 冻结清单（规格 §7.3）：每次运行实际生效的退役判据取值，事后可审计。
        # 只追加字段，不删已有键；取 SkillManager 归一化后的实际值。
        # failed_round_credit 本轮按裁定写死，不提供 CLI。
        self.llm_audit.run_meta.update(
            {
                "retire_min_exposure": self.skill_manager.retire_min_exposure,
                "retire_use_threshold": self.skill_manager.retire_use_threshold,
                "curation_interval": self.skill_manager.curation_interval,
                "call_detect": self.skill_manager.call_detect,
                "structured_retrieval": self.skill_manager.structured_retrieval,
                "contribution_mode": self.skill_manager.contribution_mode,
                "scoring_rule": self.skill_manager.contribution_mode,
                "probe_records_outcomes": True,
                "substitute_guard": self.skill_manager.substitute_guard,
                "substitute_undecided": self.skill_manager.substitute_undecided,
                "failed_round_credit": "off",
                "protected_skills": sorted(self.skill_manager.protected_skills),
                "retrieval_gate_enabled": self.skill_manager.retrieval_gate_enabled,
                "retrieval_gate_threshold": self.skill_manager.retrieval_gate_threshold,
                # 🔴 计分口径。2026-09-07 之前恒为 "program_code"，那是个 bug：
                # 模型在 program_code 里重写技能（只有定义），在 exec_code 里调用它，
                # 只扫 program_code 的 text_nodefs 因此在 169 个检索轮里命中 0 次，
                # 而运行时真值是 32 次 → success_count 恒 0 → 退役规则退化成
                # 「按检索次数退役」。拼上 exec_code 后与运行时真值逐轮 100% 一致。
                # 这个字段让新旧运行在事后分析里可区分，别混成同一格。
                "scoring_scope": SCORING_SCOPE,
                "governance": self.skill_manager.governance,
                "governance_capacity": self.skill_manager.governance_capacity,
                "noeffect_guard": bool(self.noeffect_guard),
                "stall_abort": bool(self.stall_abort),
                "pause_verify": bool(self.env.pause_verify),
                "gap_reference": bool(self.gap_reference),
                "chain_time_budget_s": int(self.chain_time_budget_s),
            }
        )
        if self.chain_node_check:
            # v29：只在开关打开时写，关时 run_meta 与 d10d6ff 逐字节相同（与 freeze_library 同一做法）
            self.llm_audit.run_meta["chain_node_check"] = True
            self.llm_audit.run_meta["chain_node_repair"] = bool(self.chain_node_repair)
        if force_difficulty:
            self.llm_audit.run_meta["force_difficulty"] = force_difficulty
        if freeze_library:
            self.llm_audit.run_meta["freeze_library"] = True
            self.llm_audit.run_meta["skill_library_dir"] = os.path.abspath(skill_library_dir)
        if extra_base_skills:
            # 同 chain_node_check 的做法：不开时 run_meta 与改动前逐字节相同
            self.llm_audit.run_meta["extra_base_skills"] = list(extra_base_skills)
        # v30 开关：仅在打开时写字段，全关时 run_meta 键集与改动前相同。
        self.lru_evict = bool(lru_evict)
        self.verify_critic = bool(verify_critic)
        self.v30_ledger = bool(v30_ledger)
        self.v30_named_actions = bool(v30_named_actions)
        self.v30_shared_fixes = bool(v30_shared_fixes)
        self.v30_anti_stall = bool(v30_anti_stall)
        self.v30_knowledge_text = bool(v30_knowledge_text)
        self.action_ledger = None
        self._verify_prev_obs = None
        self._verify_prev_round = None
        self._verify_fail_streak = None
        self._anti_stall = None
        if not self.lru_evict:
            self.llm_audit.run_meta["v30_no_lru_evict"] = True
        if self.verify_critic:
            self.llm_audit.run_meta["v30_verify_critic"] = True
        if self.v30_named_actions:
            self.llm_audit.run_meta["v30_named_actions"] = True
        if self.v30_shared_fixes:
            self.llm_audit.run_meta["v30_shared_fixes"] = True
        if self.v30_anti_stall:
            if not self.v30_named_actions:
                raise ValueError(
                    "--v30-anti-stall requires --v30-named-actions"
                )
            from voyager.governance.anti_stall import AntiStall

            self._anti_stall = AntiStall()
            self.llm_audit.run_meta["v30_anti_stall"] = True
        if self.v30_knowledge_text:
            self.llm_audit.run_meta["v30_knowledge_text"] = True
        if self.v30_ledger:
            from voyager.governance.ledger import ActionLedger

            persist_path = os.path.join(ckpt_dir, "skill", "v30_ledger.jsonl")
            self.action_ledger = ActionLedger(
                named_actions=list(extra_base_skills or []),
                persist_path=persist_path,
            )
            for name in list(self.action_ledger.retired):
                self.skill_manager.retire_named_action(name)
                extras = getattr(self.action_agent, "extra_base_skills", None)
                if extras and name in extras:
                    extras.remove(name)
            self.llm_audit.run_meta["v30_ledger"] = True
        # Zero-shot noise-fix only (default None/False = getNextTime, same as before).
        self.zs_fixed_time = zs_fixed_time
        self.zs_fixed_weather = bool(zs_fixed_weather)
        if self.zs_fixed_time is not None:
            self.llm_audit.run_meta["zs_fixed_time"] = int(self.zs_fixed_time)
        if self.zs_fixed_weather:
            self.llm_audit.run_meta["zs_fixed_weather"] = True
        self.llm_audit._write_run_meta()
        self.recorder = U.EventRecorder(ckpt_dir=ckpt_dir, resume=resume)
        self._llm_audit_iter = self.recorder.iteration
        self.llm_audit.set_iteration_provider(lambda: self._llm_audit_iter)
        self.resume = resume
        self.force_difficulty = force_difficulty
        # 2026-09-11 审计 02 C-8：整理节奏计数器原来只在内存，--resume 后从 0 重计。
        # 落盘到 ckpt/curation/curation_state.json，resume 时恢复。
        self._curation_state_path = U.f_join(ckpt_dir, "curation", "curation_state.json")
        self._curation_step = 0
        if resume and os.path.exists(self._curation_state_path):
            try:
                self._curation_step = int(
                    U.load_json(self._curation_state_path).get("curation_step", 0)
                )
            except Exception as err:
                print(f"\033[33m[curation] 读取 curation_state.json 失败，从 0 起：{err}\033[0m")
        self._probe_missing_rounds = 0
        self._rollout_parse_fail_model = 0
        self._rollout_parse_fail_infra = 0

        # init variables for rollout
        self.action_agent_rollout_num_iter = -1
        self.task = None
        self.context = ""
        self.messages = None
        self.conversations = []
        self.last_events = None
        self.rollout_retrieved_skills = []
        self.rollout_action_latency_ms = None
        self.rollout_start_inventory = {}
        self.rollout_task_intent = {}

    def _record_run_stop(self, reason, iteration):
        self.llm_audit.run_meta["stopped_reason"] = reason
        self.llm_audit.run_meta["stopped_at_iteration"] = iteration
        self.llm_audit._write_run_meta()

    def _save_curation_state(self):
        try:
            U.f_mkdir(self.ckpt_dir, "curation")
            U.dump_json(
                {"curation_step": self._curation_step}, self._curation_state_path
            )
        except Exception as err:
            print(f"\033[33m[curation] 写 curation_state.json 失败：{err}\033[0m")

    def _check_probe(self, runtime_calls, rollout_crashed):
        """2026-09-11 审计 P0-1：探针哨兵检查。

        返回哨兵值（=包装的技能数）或 None。None 且库非空且探针开启且本轮没崩，
        说明注入链路断了——以前这种情况 runtime 判定静默返回空集，与"真没调用"
        分不开。这里只告警 + 计数进 run_meta，不中断运行（过夜优先）。
        """
        probe_installed = None
        if isinstance(runtime_calls, dict):
            probe_installed = runtime_calls.get("__probe_installed__")
        lib_size_at_exec = getattr(self, "_rollout_lib_size", len(self.skill_manager.skills))
        if (
            RUNTIME_CALL_PROBE_ENABLED
            and lib_size_at_exec > 0
            and not rollout_crashed
            and probe_installed is None
        ):
            self._probe_missing_rounds += 1
            self.llm_audit.run_meta["probe_missing_rounds"] = self._probe_missing_rounds
            self.llm_audit._write_run_meta()
            print(
                "\033[41m[probe-missing] 运行时探针哨兵没有回传（库有 "
                f"{len(self.skill_manager.skills)} 个技能）。runtime 判定这一轮不可信。"
                f"累计 {self._probe_missing_rounds} 轮。\033[0m",
                flush=True,
            )
        return probe_installed

    def _remember_retrieved_skills(self):
        for skill_name in self.skill_manager.last_retrieved_skill_names:
            if skill_name not in self.rollout_retrieved_skills:
                self.rollout_retrieved_skills.append(skill_name)

    def reset(self, task, context="", reset_env=True, task_intent=None):
        self.action_agent_rollout_num_iter = 0
        self.task = task
        self.context = context
        self.rollout_retrieved_skills = []
        self.rollout_action_latency_ms = 0
        self._rollout_parse_fail_model = 0
        self._rollout_parse_fail_infra = 0
        # 探针检查要用**执行时**的库大小：本任务成功后 add_new_skill 会先于检查发生，
        # 第一个任务库为空、探针本就不注入，用结束后的库大小会误报 probe-missing
        # （smoke0911 两条各误报 1 轮）。
        self._rollout_lib_size = len(self.skill_manager.skills)
        self.rollout_start_inventory = {}
        self.rollout_skill_credit = {}
        self.rollout_skill_outcomes = []
        self.rollout_task_intent = task_intent or infer_task_intent(
            task, source="task_fallback"
        )
        self._noeffect_idle_counts = {}
        if reset_env:
            self.env.reset(
                options={
                    "mode": "soft",
                    "wait_ticks": self.env_wait_ticks,
                }
            )
        difficulty = (
            "easy" if len(self.curriculum_agent.completed_tasks) > 15 else "peaceful"
        )
        # v29 零样本：force_difficulty 不为空时固定难度（默认 None，行为与 d10d6ff 相同）。
        # 原因：上面这条规则用"完成的子目标数"决定世界难度，零样本里跑得快的一臂会先跨过 15、
        # 被换成会刷敌对生物的 easy，等于用成绩去改环境，两臂不可比（09-20 Grok 复核指出）。
        if getattr(self, "force_difficulty", None):
            difficulty = self.force_difficulty
        # step to peek an observation
        if getattr(self, "zs_fixed_time", None) is not None:
            time_weather = f"await bot.chat('/time set {int(self.zs_fixed_time)}');\n"
            if getattr(self, "zs_fixed_weather", False):
                time_weather += (
                    "await bot.chat('/weather clear');\n"
                    "await bot.chat('/gamerule doWeatherCycle false');\n"
                )
            events = self.env.step(
                time_weather + f"bot.chat('/difficulty {difficulty}');"
            )
        else:
            events = self.env.step(
                "bot.chat(`/time set ${getNextTime()}`);\n"
                + f"bot.chat('/difficulty {difficulty}');"
            )
        self.skill_manager.cur_iter = self.recorder.iteration
        skills = self.skill_manager.retrieve_skills(query=self.context)
        self._remember_retrieved_skills()
        if self.gap_reference:
            # 原版口径的相似度 top-5（机制臂上面那次检索返回的是整库列表，不能直接用）
            self._gap_ref_names, self._gap_ref_skills = (
                self.skill_manager.similar_skills_for_reference(self.context)
            )
        self._gap_last_code = ""
        self._gap_critique = ""
        # v29 --chain-node-repair：本任务禁用集合与待带的失败缺口代码不跨任务。
        self._node_disabled = {}
        self._node_gap_fix = ""
        print(
            f"\033[33mRender Action Agent system message with {len(skills)} skills\033[0m"
        )
        system_message = self.action_agent.render_system_message(skills=skills)
        human_message = self.action_agent.render_human_message(
            events=events,
            code="",
            task=self.task,
            context=context,
            critique="",
            task_intent=self.rollout_task_intent,
        )
        self.messages = [system_message, human_message]
        print(
            f"\033[32m****Action Agent human message****\n{human_message.content}\033[0m"
        )
        assert len(self.messages) == 2
        self.conversations = []
        self.rollout_start_inventory = dict(events[-1][1].get("inventory", {}))
        return self.messages

    def close(self):
        self.env.close()

    def _env_programs(self, code=None):
        """JS injected at every env.step. Flag off → skill_manager.programs 原值。

        v29 --chain-node-repair：本任务禁用的库技能挂替身；`code` 里以函数声明同名重写的不挂
        （重写的新版本应当能跑）。返修关或禁用集合为空时与改动前逐字节相同。
        """
        guard_on = bool(getattr(self, "noeffect_guard", False))
        planner_v2_on = bool(getattr(self, "planner_v2", False))
        node_stubs = self._node_disabled_stubs(code)
        if guard_on and planner_v2_on:
            from voyager.governance.noeffect import banned_skills

            banned = banned_skills(getattr(self, "_noeffect_idle_counts", {}))
            if banned:
                if node_stubs:
                    return self.skill_manager.build_programs(
                        disabled=banned, extra_stubs=node_stubs
                    )
                # 返修关或没有要挂的替身：与 d10d6ff 同一调用（参数也不多带）
                return self.skill_manager.build_programs(disabled=banned)
        if node_stubs:
            return self.skill_manager.build_programs(extra_stubs=node_stubs)
        return self.skill_manager.programs

    def _check_task_success(
        self,
        *,
        events,
        task,
        context,
        chest_observation,
        executed_skills=None,
        executed_function=None,
    ):
        """v30 A：verify_critic 开且能核对时用核对结果；关时走原版 LLM critic。"""
        if getattr(self, "v30_shared_fixes", False) and executed_skills:
            from voyager.js_main import enrich_executed_skills

            before = None
            last = getattr(self, "last_events", None)
            if last and isinstance(last[-1][1], dict):
                before = last[-1][1].get("inventory")
            if before is None:
                before = getattr(self, "rollout_start_inventory", None) or {}
            executed_skills = enrich_executed_skills(
                executed_skills, events, before_inventory=before
            )
        if getattr(self, "verify_critic", False):
            try:
                from voyager.verify import (
                    VerifyFailStreak,
                    _count_targets,
                    format_verify_fail,
                    verify_from_events,
                )

                if getattr(self, "_verify_fail_streak", None) is None:
                    self._verify_fail_streak = VerifyFailStreak()
                verdict = verify_from_events(
                    task,
                    events,
                    prev_round_obs=getattr(self, "_verify_prev_obs", None),
                    prev_round=getattr(self, "_verify_prev_round", None),
                    current_round=getattr(
                        getattr(self, "recorder", None), "iteration", None
                    ),
                    task_start_inventory=getattr(
                        self, "rollout_start_inventory", None
                    ),
                    shared_fixes=bool(getattr(self, "v30_shared_fixes", False)),
                    executed_function=executed_function,
                )
                if verdict is not None:
                    if not verdict.success:
                        if "该物品在本版本不存在" in (verdict.critique or ""):
                            print(
                                f"\033[32m[verify] {verdict.critique}\033[0m",
                                flush=True,
                            )
                            self._remember_verify_snapshot(events)
                            if getattr(self, "_verify_fail_streak", None) is not None:
                                self._verify_fail_streak.reset()
                            return False, verdict.critique, {}
                        inv = {}
                        if events and isinstance(events[-1][1], dict):
                            inv = events[-1][1].get("inventory") or {}
                        have = _count_targets(inv, verdict.targets)
                        fail_key = (
                            f"{verdict.verb}|{verdict.n}|"
                            f"{tuple(sorted(verdict.targets))}|{have}"
                        )
                        streak = self._verify_fail_streak
                        if streak.should_fallback_llm(task, fail_key):
                            streak.reset()
                        streak.record_fail(task, fail_key)
                        critique = format_verify_fail(
                            task=task,
                            verb=verdict.verb,
                            n=verdict.n,
                            targets=verdict.targets,
                            have=have,
                            executed_function=executed_function,
                        )
                        print(f"\033[32m[verify] {critique}\033[0m", flush=True)
                        self._remember_verify_snapshot(events)
                        return False, critique, {}
                    if getattr(self, "_verify_fail_streak", None) is not None:
                        self._verify_fail_streak.reset()
                    print(
                        f"\033[32m[verify] {verdict.critique}\033[0m",
                        flush=True,
                    )
                    self._remember_verify_snapshot(events)
                    return verdict.success, verdict.critique, {}
            except Exception as exc:
                print(f"[verify] error {type(exc).__name__}: {exc}", flush=True)
        self._remember_verify_snapshot(events)
        return self.critic_agent.check_task_success(
            events=events,
            task=task,
            context=context,
            chest_observation=chest_observation,
            max_retries=5,
            executed_skills=executed_skills,
        )

    def _remember_verify_snapshot(self, events):
        """Keep this step's last observation for the next round's eat/deposit/more."""
        try:
            from voyager.verify import _snapshot_events

            snaps = _snapshot_events(events)
            if snaps:
                self._verify_prev_obs = snaps[-1][1]
                self._verify_prev_round = getattr(
                    getattr(self, "recorder", None), "iteration", None
                )
        except Exception:
            pass

    def _anti_stall_observe_round(self, events, task, success):
        """N1: one AntiStall.observe per recorder step (same function as replay)."""
        stall = getattr(self, "_anti_stall", None)
        if stall is None:
            return None
        inv = {}
        biome = ""
        if events and isinstance(events[-1], (list, tuple)) and len(events[-1]) >= 2:
            payload = events[-1][1]
            if isinstance(payload, dict):
                inv = payload.get("inventory") or {}
                status = payload.get("status") or {}
                biome = status.get("biome") or ""
        if not hasattr(self, "_anti_stall_seen"):
            self._anti_stall_seen = set()
        cur = {k for k, v in inv.items() if v}
        new_items = len(cur - self._anti_stall_seen)
        self._anti_stall_seen |= cur
        task_s = task or ""
        mining = bool(
            re.search(r"mine|collect|gather", task_s, re.I)
            and re.search(r"ore|diamond|emerald|log|wood", task_s, re.I)
        )
        rec = {
            "iter": self.recorder.iteration,
            "success": bool(success),
            "new_items": new_items,
            "task": task_s,
            "biome": biome,
            "biome_has_unexplored_target": mining,
        }
        decision = stall.observe(rec)
        if not decision:
            return None
        metrics_dir = os.path.join(self.ckpt_dir, "metrics")
        os.makedirs(metrics_dir, exist_ok=True)
        log_path = os.path.join(metrics_dir, "anti_stall.jsonl")
        row = {
            "trigger": decision.get("trigger"),
            "iter": decision.get("iter"),
            "action": decision.get("action"),
            "kind": decision.get("kind"),
            "task": decision.get("task"),
        }
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        if decision.get("task"):
            self._anti_stall_pending = decision
        if (
            decision.get("kind") == "missing_wood"
            or decision.get("action") == "gather_wood_far"
        ):
            shift = int(getattr(self, "_gather_dir_shift", 0) or 0) + 3
            dist = max(64, int(getattr(self, "_gather_max_distance", 32) or 32) + 32)
            self._gather_dir_shift = shift
            self._gather_max_distance = dist
            self._anti_stall_gather_opts = {"shift": shift, "maxDistance": dist}
        return decision

    def _apply_anti_stall_gather_prefix(self, code):
        """Prefix gather globals once, then clear so JS rotation can proceed."""
        opts = getattr(self, "_anti_stall_gather_opts", None)
        if not opts:
            return code
        self._anti_stall_gather_opts = None
        return (
            f"globalThis.__gatherDirShift={int(opts.get('shift') or 0)};"
            f"globalThis.__gatherMaxDistance={int(opts.get('maxDistance') or 64)};\n"
            + (code or "")
        )

    def _ingest_contract_events(self, events):
        """v30 账本：从 skillOutcomes 运行时通道收契约结果。chat [contract] 只当日志。"""
        if not getattr(self, "v30_ledger", False) or self.action_ledger is None:
            return
        iter_n = getattr(getattr(self, "recorder", None), "iteration", None)
        for name, decision in self.action_ledger.ingest_skill_outcomes(
            events, iteration=iter_n
        ):
            if decision == "retire":
                self.skill_manager.retire_named_action(name)
                extras = getattr(self.action_agent, "extra_base_skills", None)
                if extras and name in extras:
                    extras.remove(name)

    def _node_disabled_stubs(self, code=None):
        """v29：本任务禁用集合 → 替身语句列表（返修关或集合为空时为 []）。"""
        disabled = getattr(self, "_node_disabled", None) or {}
        if not getattr(self, "chain_node_repair", False) or not disabled:
            return []
        from .chain.assembler import defined_function_names, node_disabled_stub_js

        redefined = defined_function_names(code)
        return [
            node_disabled_stub_js(name, reason)
            for name, reason in disabled.items()
            if isinstance(name, str)
            and name.isidentifier()
            and name in self.skill_manager.skills
            and name not in redefined
        ]

    def step(self):
        if getattr(self, "chain", False) and not getattr(self, "_chain_fallback", False):
            return self.chain_step()
        if self.action_agent_rollout_num_iter < 0:
            raise ValueError("Agent must be reset before stepping")
        self._llm_audit_iter = self.recorder.iteration + 1
        action_llm_start = time.perf_counter()
        try:
            ai_message = call_llm_with_retry(
                self.action_agent.llm, self.messages, label="action.step"
            )
        finally:
            elapsed_ms = (time.perf_counter() - action_llm_start) * 1000
            if self.rollout_action_latency_ms is None:
                self.rollout_action_latency_ms = 0
            self.rollout_action_latency_ms += elapsed_ms
        print(f"\033[34m****Action Agent ai message****\n{ai_message.content}\033[0m")
        self.conversations.append(
            (self.messages[0].content, self.messages[1].content, ai_message.content)
        )
        parsed_result = self.action_agent.process_ai_message(
            message=ai_message, task=self.task
        )
        success = False
        if isinstance(parsed_result, dict):
            code = parsed_result["program_code"] + "\n" + parsed_result["exec_code"]
            code = self._apply_anti_stall_gather_prefix(code)
            events = self.env.step(
                code,
                programs=self._env_programs(code),
            )
            self.recorder.record(events, self.task)
            self.action_agent.update_chest_memory(events[-1][1]["nearbyChests"])
            runtime_outcomes = (
                events[-1][1].get("skillOutcomes")
                if events and isinstance(events[-1][1], dict)
                else None
            )
            critic_result = self._check_task_success(
                events=events,
                task=self.task,
                context=self.context,
                chest_observation=self.action_agent.render_chest_observation(),
                executed_skills=runtime_outcomes,
                executed_function=parsed_result.get("program_name"),
            )
            success, critique, skill_credit = _unpack_critic_result(critic_result)
            self._anti_stall_observe_round(events, self.task, success)
            self._ingest_contract_events(events)
            self.rollout_skill_credit = skill_credit
            if isinstance(runtime_outcomes, list):
                self.rollout_skill_outcomes.extend(runtime_outcomes)

            if self.reset_placed_if_failed and not success:
                # revert all the placing event in the last step
                blocks = []
                positions = []
                for event_type, event in events:
                    if event_type == "onSave" and event["onSave"].endswith("_placed"):
                        block = event["onSave"].split("_placed")[0]
                        position = event["status"]["position"]
                        blocks.append(block)
                        positions.append(position)
                new_events = self.env.step(
                    f"await givePlacedItemBack(bot, {U.json_dumps(blocks)}, {U.json_dumps(positions)})",
                    programs=self._env_programs(),
                )
                events[-1][1]["inventory"] = new_events[-1][1]["inventory"]
                events[-1][1]["voxels"] = new_events[-1][1]["voxels"]
            self.last_events = copy.deepcopy(events)
        else:
            assert isinstance(parsed_result, str)
            # 口径不变：打回也算一个 action step（recorder.iteration +1）。
            self.recorder.record([], self.task)
            self._anti_stall_observe_round([], self.task, False)
            if parsed_result.startswith(PARSER_INFRA_PREFIX):
                self._rollout_parse_fail_infra += 1
                print(
                    f"\033[41m[parser-infra] 解析器基础设施故障（不是模型的错）："
                    f"{parsed_result}\033[0m",
                    flush=True,
                )
            else:
                self._rollout_parse_fail_model += 1
            print(f"\033[34m{parsed_result} Trying again!\033[0m")
        assert len(self.messages) == 2
        self.action_agent_rollout_num_iter += 1
        done = (
            self.action_agent_rollout_num_iter >= self.action_agent_task_max_retries
            or success
        )
        # 2026-09-11 审计 02 C-2（幽灵曝光）：以前这里无条件再检索一次并计入
        # rollout_retrieved_skills，但 done 之后这批技能从未进过任何被执行的
        # prompt——每个 rollout 最后一次检索都是幽灵曝光，分母系统性偏大。
        # 现在只在还要走下一步时检索。
        if not done:
            if isinstance(parsed_result, dict):
                new_skills = self.skill_manager.retrieve_skills(
                    query=self.context
                    + "\n\n"
                    + self.action_agent.summarize_chatlog(events)
                )
                self._remember_retrieved_skills()
                system_message = self.action_agent.render_system_message(
                    skills=new_skills
                )
                human_message = self.action_agent.render_human_message(
                    events=events,
                    code=parsed_result["program_code"],
                    task=self.task,
                    context=self.context,
                    critique=critique,
                    task_intent=self.rollout_task_intent,
                )
                self.messages = [system_message, human_message]
            else:
                # 2026-09-11 审计 03 C-02（已知问题 7）：解析被打回时以前 messages
                # 原样重发，temperature=0 下大概率得到同一回复，4 步烧光。
                # 现在把解析错误作为 critique 回灌，系统消息（技能清单）不变。
                human_message = self.action_agent.render_human_message(
                    events=self.last_events,
                    code="",
                    task=self.task,
                    context=self.context,
                    critique=(
                        "Your previous response could not be parsed and was NOT "
                        f"executed: {parsed_result}. Reply again with exactly one "
                        "```javascript code block; the main function must be the "
                        "last async function and must take a single argument named "
                        "'bot'."
                    ),
                    task_intent=self.rollout_task_intent,
                )
                self.messages = [self.messages[0], human_message]
        info = {
            "task": self.task,
            "success": success,
            "conversations": self.conversations,
            "task_intent": self.rollout_task_intent,
        }
        if isinstance(parsed_result, dict):
            # Keep attempted code for observability even when the task fails.
            # Scoring remains success-gated in SkillManager.update_skill_usage().
            info["program_code"] = parsed_result.get("program_code", "")
            # 仅供埋点：真正执行的是 program_code + exec_code，
            # 只搜前者会漏掉写在 exec_code 里的技能调用。
            # 计分口径不动（update_skill_usage 仍只收 program_code）。
            info["exec_code"] = parsed_result.get("exec_code", "")
            info["exec_args"] = parsed_result.get("exec_args")
            info["signature"] = parsed_result.get("signature")
        if success:
            assert (
                "program_code" in parsed_result and "program_name" in parsed_result
            ), "program and program_name must be returned when success"
            info["program_name"] = parsed_result["program_name"]
            info["skill_intent"] = parsed_result.get("skill_intent", {})
        else:
            print(
                f"\033[32m****Action Agent human message****\n{self.messages[-1].content}\033[0m"
            )
        return self.messages, 0, done, info

    def _gap_reretrieve(self, events):
        """v28：与原版 step() 第 2 轮起的检索同口径（context + 聊天摘要，相似度 top-5）。只读，不计曝光。"""
        self._gap_ref_names, skills = self.skill_manager.similar_skills_for_reference(
            self.context + "\n\n" + self.action_agent.summarize_chatlog(events)
        )
        return list(skills)

    def _library_entry(self, node):
        return (self.skill_manager.skills or {}).get(node.get("skill")) or {}

    def _node_needs_effect(self, node):
        """v29：库技能节点"正常返回但背包没变"是否记为无效果。技能库里记录的意图 + 规划器写的 verb，
        交给 assembler.node_needs_effect（复用 noeffect 的现成定义）。"""
        from .chain.assembler import node_needs_effect

        return node_needs_effect(self._library_entry(node), node.get("verb"))

    def _node_repair(self, fail, generated):
        """v29 --chain-node-repair：本任务内当场返修，返回指名返修目标节点的评语。

        库里的现成技能（报错，或本步没报错却判失败时第一个非放置类的无效果节点）→ 进本任务禁用集合
        （目录里去掉、执行环境挂替身）；新写的缺口代码报错 → 记下这段代码，下一次写缺口时带上
        （错误信息在评语与上一轮事件里）。
        """
        from .chain.planner import render_node_failure_critique

        name = fail.get("skill")
        if fail.get("source") == "library":
            why = (
                "returned without the expected inventory change; suspected cause"
                if fail.get("kind") == "no_effect"
                else "error: " + (fail.get("message") or "")[:160]
            )
            self._node_disabled[name] = (
                f"chain node {fail.get('node')}/{fail.get('total')} ({why})"
            )
        else:
            self._node_gap_fix = (generated or {}).get(name) or ""
        return render_node_failure_critique(fail)

    def chain_step(self):
        from .chain.assembler import (
            assemble,
            find_noeffect_nodes,
            is_place_node,
            parse_node_failure,
            strip_budget_guards,
            strip_node_checks,
        )
        from .chain.catalog import build_catalog
        from .chain.gapgen import generate_gap_skill
        from .chain.planner import (
            observation_text_from_events,
            plan_chain,
            render_outcomes_critique,
        )

        if self.action_agent_rollout_num_iter < 0:
            raise ValueError("Agent must be reset before stepping")
        self._llm_audit_iter = self.recorder.iteration + 1
        node_check_on = bool(getattr(self, "chain_node_check", False))
        node_repair_on = bool(getattr(self, "chain_node_repair", False))

        def fallback(reason=""):
            print(f"\033[33m[chain] fallback to legacy step ({reason})\033[0m")
            self._chain_fallback = True
            try:
                messages, reward, done, info = self.step()
            finally:
                self._chain_fallback = False
            info = dict(info or {})
            if self.gap_reference and not done:
                self._gap_last_code = info.get("program_code") or ""
                self._gap_critique = ""
                self._gap_ref_skills = self._gap_reretrieve(self.last_events)
            if node_repair_on:
                # 上一轮不是链式执行，没有失败的缺口代码可带
                self._node_gap_fix = ""
            info["chain_nodes"] = 0
            info["chain_reused"] = 0
            info["chain_gaps"] = 0
            info["chain_gap_failed"] = 0
            info["chain_node_ok"] = 0
            info["chain_route"] = "fallback"
            if node_check_on:
                info["chain_node_fail"] = None
            return messages, reward, done, info

        # v29 --chain-node-repair：本任务禁用的库技能不进目录（规划器因此会把那一步标成缺口）。
        node_disabled = list(getattr(self, "_node_disabled", None) or {}) if node_repair_on else []

        def drop_node_disabled(items):
            if not node_disabled:
                return items
            gone = set(node_disabled)
            return [item for item in items if item.get("name") not in gone]

        catalog = build_catalog(self.skill_manager)
        banned = []
        guard_on = bool(getattr(self, "noeffect_guard", False))
        planner_v2_on = bool(getattr(self, "planner_v2", False))
        if guard_on and planner_v2_on:
            from voyager.governance.noeffect import banned_skills

            banned = banned_skills(getattr(self, "_noeffect_idle_counts", {}))
        events = self.last_events
        obs_text = observation_text_from_events(events)
        plan_context = self.context or ""
        extra_critique = getattr(self, "_chain_critique", "") or ""
        if extra_critique:
            plan_context = (plan_context + "\n\n" + extra_critique).strip()
        plan_meta = None

        action_llm_start = time.perf_counter()
        try:
            if planner_v2_on:
                from .chain.planner import (
                    observation_text_from_action_agent,
                    plan_chain_v2,
                )
                from .chain.requires import records_from_chain_log

                chain_log_path = os.path.join(self.ckpt_dir, "metrics", "chain_log.jsonl")
                records = records_from_chain_log(chain_log_path)
                catalog = build_catalog(
                    self.skill_manager,
                    include_requires=True,
                    records=records,
                )
                if banned:
                    banned_set = set(banned)
                    catalog = [
                        item for item in catalog if item.get("name") not in banned_set
                    ]
                catalog = drop_node_disabled(catalog)
                if self.messages and len(self.messages) > 1:
                    obs_text = self.messages[1].content
                else:
                    obs_text = observation_text_from_action_agent(
                        self.action_agent,
                        events=events,
                        code="",
                        task=self.task,
                        context=self.context or "",
                        critique=extra_critique,
                        task_intent=self.rollout_task_intent,
                    )
                skill_codes = {}
                for item in catalog:
                    name = item.get("name")
                    entry = (self.skill_manager.skills or {}).get(name) or {}
                    skill_codes[name] = entry.get("code") or ""
                planned = plan_chain_v2(
                    self.chain_llm,
                    getattr(self, "chain_review_llm", None),
                    task=self.task,
                    context=plan_context,
                    observation_text=obs_text,
                    skill_catalog=catalog,
                    skill_codes=skill_codes,
                    noeffect_banned=banned if guard_on else None,
                )
                nodes = planned.get("nodes") or []
                plan_meta = planned.get("meta") or {}
            else:
                nodes = plan_chain(
                    self.chain_llm,
                    task=self.task,
                    context=plan_context,
                    observation_text=obs_text,
                    skill_catalog=drop_node_disabled(catalog),
                )
        finally:
            elapsed_ms = (time.perf_counter() - action_llm_start) * 1000
            if self.rollout_action_latency_ms is None:
                self.rollout_action_latency_ms = 0
            self.rollout_action_latency_ms += elapsed_ms

        if not nodes:
            return fallback("empty or unparseable chain")

        reused = sum(1 for node in nodes if node.get("skill"))
        gaps = sum(1 for node in nodes if not node.get("skill"))
        generated = {}
        parsed_by_name = {}
        gap_failed = 0
        existing = set(self.skill_manager.skills.keys())
        executable = []
        for node in nodes:
            if node.get("skill"):
                executable.append(node)
                continue
            gap_kwargs = {}
            gap_context = plan_context
            if self.gap_reference:
                # 原版口径：Context 只放任务 context，上一轮评语只走 critique 栏（plan_context 里已拼了评语，
                # 再传 critique 会重复一次）
                gap_context = self.context or ""
                gap_kwargs = dict(
                    skills=self._gap_ref_skills,
                    code=self._gap_last_code,
                    critique=self._gap_critique,
                )
                print(
                    f"\033[33m[chain.gapgen] reference: {len(self._gap_ref_skills)} skills "
                    f"{self._gap_ref_names}, "
                    f"last code {len(self._gap_last_code)} chars, "
                    f"critique {len(self._gap_critique)} chars\033[0m"
                )
            elif node_repair_on and self._node_gap_fix:
                # v29 返修（--gap-reference 关）：上一轮失败的缺口代码带给这次写缺口。错误信息已在
                # Context（plan_context 里的指名评语）与 Execution error（上一轮事件）里，不再重复传评语。
                gap_kwargs = dict(code=self._node_gap_fix)
                print(
                    f"\033[33m[chain.gapgen] repair: failed gap code "
                    f"{len(self._node_gap_fix)} chars\033[0m"
                )
            parsed = generate_gap_skill(
                self.action_agent,
                node=node,
                events=events,
                context=gap_context,
                existing_names=existing | set(generated),
                **gap_kwargs,
            )
            if parsed is None:
                gap_failed += 1
                break
            name = parsed["program_name"]
            filled = dict(node)
            filled["skill"] = name
            if parsed.get("signature"):
                filled["signature"] = parsed["signature"]
            generated[name] = parsed["program_code"]
            parsed_by_name[name] = parsed
            existing.add(name)
            executable.append(filled)

        if not executable:
            return fallback("gap generation failed before any executable node")

        assembled = assemble(
            executable,
            generated,
            time_budget_s=self.chain_time_budget_s,
            node_check=node_check_on,
        )
        program_code = assembled.get("program_code") or ""
        exec_code = assembled.get("exec_code") or ""
        code = self._apply_anti_stall_gather_prefix(program_code + "\n" + exec_code)
        print(f"\033[34m****Chain exec_code****\n{exec_code}\033[0m")
        events = self.env.step(
            code,
            programs=self._env_programs(code),
        )
        self.recorder.record(events, self.task)
        node_fail = None
        if node_check_on:
            node_fail = parse_node_failure(events, executable, generated)
            if node_fail:
                print(f"\033[33m[chain-node] {json.dumps(node_fail, ensure_ascii=False)}\033[0m")
        self.action_agent.update_chest_memory(events[-1][1]["nearbyChests"])
        runtime_outcomes = (
            events[-1][1].get("skillOutcomes")
            if events and isinstance(events[-1][1], dict)
            else None
        )
        critic_result = self._check_task_success(
            events=events,
            task=self.task,
            context=self.context,
            chest_observation=self.action_agent.render_chest_observation(),
            executed_skills=runtime_outcomes,
        )
        success, critique, skill_credit = _unpack_critic_result(critic_result)
        self._anti_stall_observe_round(events, self.task, success)
        self._ingest_contract_events(events)
        self.rollout_skill_credit = skill_credit
        if isinstance(runtime_outcomes, list):
            self.rollout_skill_outcomes.extend(runtime_outcomes)

        if self.reset_placed_if_failed and not success:
            blocks = []
            positions = []
            for event_type, event in events:
                if event_type == "onSave" and event["onSave"].endswith("_placed"):
                    block = event["onSave"].split("_placed")[0]
                    position = event["status"]["position"]
                    blocks.append(block)
                    positions.append(position)
            new_events = self.env.step(
                f"await givePlacedItemBack(bot, {U.json_dumps(blocks)}, {U.json_dumps(positions)})",
                programs=self._env_programs(),
            )
            events[-1][1]["inventory"] = new_events[-1][1]["inventory"]
            events[-1][1]["voxels"] = new_events[-1][1]["voxels"]
        self.last_events = copy.deepcopy(events)

        outcomes = runtime_outcomes if isinstance(runtime_outcomes, list) else []
        has_error = any(event_type == "onError" for event_type, _ in events)
        node_noeffect = []
        if node_check_on:
            # 无效果只记录、不停链：库技能从探针 outcomes 判；新写的缺口代码没有探针，不判
            node_noeffect = find_noeffect_nodes(
                executable,
                outcomes,
                needs_effect=lambda node: node.get("skill") not in generated
                and self._node_needs_effect(node),
                stop_before=(node_fail or {}).get("node"),
            )
        node_ok = 0
        for node in executable:
            name = node.get("skill")
            recs = [
                rec
                for rec in outcomes
                if isinstance(rec, dict) and rec.get("name") == name
            ]
            if recs:
                ok = any(rec.get("ok") for rec in recs)
                rec = recs[-1]
            else:
                ok = not has_error
                rec = {"name": name, "ok": ok, "err": None, "delta": {}}
            if ok:
                node_ok += 1
            parsed = parsed_by_name.get(name)
            if parsed is None or not ok:
                continue
            delta = rec.get("delta") or {}
            gained = sorted(
                key
                for key, value in delta.items()
                if isinstance(value, (int, float)) and value > 0
            )
            fingerprint_meta = purify_fingerprint(
                gained,
                task_intent=self.rollout_task_intent,
                skill_intent=parsed.get("skill_intent") or {},
            )
            skill_info = {
                "task": node.get("goal") or self.task,
                "program_name": parsed["program_name"],
                "program_code": parsed["program_code"],
                "signature": parsed.get("signature"),
                "exec_args": parsed.get("exec_args"),
                "skill_intent": parsed.get("skill_intent") or {},
            }
            skill_info.update(fingerprint_meta)
            self.skill_manager.add_new_skill(skill_info)

        if reused and gaps:
            route = "mixed"
        elif reused:
            route = "reuse_only"
        else:
            route = "gap_only"

        metrics_dir = os.path.join(self.ckpt_dir, "metrics")
        os.makedirs(metrics_dir, exist_ok=True)
        chain_log_path = os.path.join(metrics_dir, "chain_log.jsonl")
        public_nodes = []
        for node in executable:
            public_nodes.append(
                {
                    "goal": node.get("goal"),
                    "verb": node.get("verb"),
                    "target": node.get("target"),
                    "skill": node.get("skill"),
                    "args": node.get("args") or {},
                    "repeat": node.get("repeat", 1),
                }
            )
        outcome_summary = []
        for rec in outcomes:
            if not isinstance(rec, dict):
                continue
            item = {
                "name": rec.get("name"),
                "ok": rec.get("ok"),
                "err": rec.get("err"),
                "delta": rec.get("delta"),
            }
            if getattr(self, "planner_v2", False):
                from .chain.requires import delta_is_empty

                item["no_effect"] = bool(rec.get("ok")) and delta_is_empty(
                    rec.get("delta")
                )
            outcome_summary.append(item)
        log_row = {
            "iter": self.recorder.iteration,
            "task": self.task,
            "nodes": public_nodes,
            "exec_code": exec_code,
            "outcomes": outcome_summary,
            "route": route,
        }
        if getattr(self, "planner_v2", False) and plan_meta:
            log_row["planner_version"] = 2
            reasoning = plan_meta.get("reasoning") or {}
            log_row["reasoning"] = {
                "plan": (reasoning.get("plan") or "")[:2000],
                "review": (reasoning.get("review") or "")[:2000],
            }
            log_row["reviewed"] = bool(plan_meta.get("reviewed"))
            log_row["review_changed"] = bool(plan_meta.get("review_changed"))
        if guard_on:
            log_row["noeffect_banned"] = list(banned)
        if node_check_on:
            log_row["node_fail"] = node_fail
            log_row["node_noeffect"] = node_noeffect
        if node_repair_on:
            # 本步规划与执行时生效的禁用集合（本步新失败的在下一步才生效）
            log_row["node_disabled"] = list(node_disabled)
        if guard_on and planner_v2_on:
            from voyager.governance.noeffect import update_task_idle_counts

            if not isinstance(getattr(self, "_noeffect_idle_counts", None), dict):
                self._noeffect_idle_counts = {}
            update_task_idle_counts(
                self._noeffect_idle_counts,
                outcomes,
                self.skill_manager.skills,
            )
        with open(chain_log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(log_row, ensure_ascii=False) + "\n")

        self.conversations.append(
            (
                (self.messages[0].content if self.messages else ""),
                (
                    self.messages[1].content
                    if self.messages and len(self.messages) > 1
                    else ""
                ),
                exec_code,
            )
        )
        assert len(self.messages) == 2
        self.action_agent_rollout_num_iter += 1
        done = (
            self.action_agent_rollout_num_iter >= self.action_agent_task_max_retries
            or success
        )
        self._chain_critique = ""
        node_critique = ""
        if node_repair_on:
            # 失败缺口代码只带给紧接着的下一轮（与"上一轮代码"同口径）
            self._node_gap_fix = ""
            # 返修目标：节点报错 → 报错的节点；本步没有任何报错但判失败 → 第一个非放置类的无效果库技能
            # 节点（疑似原因；放置类 = 技能意图或节点动词为 place，东西已在附近时幂等返回是无辜的，
            # 照常记进 node_noeffect 但不当候选）；都没有 → 不禁用、评语与关开关时相同。
            # 判成功的步、本任务最后一次尝试（done）一律不动。
            target = node_fail
            suspects = [
                index
                for index in node_noeffect
                if not is_place_node(
                    self._library_entry(executable[index - 1]),
                    executable[index - 1].get("verb"),
                )
            ]
            if target is None and not success and not has_error and suspects:
                first = suspects[0]
                target = {
                    "node": first,
                    "total": len(executable),
                    "skill": executable[first - 1].get("skill"),
                    "kind": "no_effect",
                    "message": "",
                    "source": "library",
                }
            if target and not done:
                node_critique = self._node_repair(target, generated)
        if self.gap_reference and not done:
            # 与原版一致：下一轮看到本轮执行的代码与评语，参考技能按 context + 聊天摘要重检索
            self._gap_last_code = strip_budget_guards(code)
            if node_check_on:
                self._gap_last_code = strip_node_checks(self._gap_last_code)
            self._gap_critique = "\n".join(
                part
                for part in (
                    critique,
                    render_outcomes_critique(
                        outcomes, planner_v2=getattr(self, "planner_v2", False)
                    ),
                    node_critique,
                )
                if part
            )
            self._gap_ref_skills = self._gap_reretrieve(events)
        if not done:
            self._chain_critique = "\n".join(
                part
                for part in (
                    critique,
                    render_outcomes_critique(
                        outcomes, planner_v2=getattr(self, "planner_v2", False)
                    ),
                    node_critique,
                )
                if part
            )
            human_message = self.action_agent.render_human_message(
                events=events,
                code=program_code,
                task=self.task,
                context=self.context,
                critique=self._chain_critique,
                task_intent=self.rollout_task_intent,
            )
            self.messages = [self.messages[0], human_message]
        info = {
            "task": self.task,
            "success": success,
            "conversations": self.conversations,
            "task_intent": self.rollout_task_intent,
            "program_code": program_code,
            "exec_code": exec_code,
            "chain_nodes": len(nodes),
            "chain_reused": reused,
            "chain_gaps": gaps,
            "chain_gap_failed": gap_failed,
            "chain_node_ok": node_ok,
            "chain_route": route,
        }
        if node_check_on:
            info["chain_node_fail"] = node_fail
        names = assembled.get("program_names") or []
        if names:
            last_name = names[-1]
            last_parsed = parsed_by_name.get(last_name) or {}
            info["program_name"] = last_name
            info["skill_intent"] = last_parsed.get("skill_intent", {})
            info["signature"] = last_parsed.get("signature")
            info["exec_args"] = last_parsed.get("exec_args")
        if not success:
            print(
                f"\033[32m****Action Agent human message****\n{self.messages[-1].content}\033[0m"
            )
        return self.messages, 0, done, info

    def rollout(self, *, task, context, reset_env=True, task_intent=None):
        self.reset(
            task=task, context=context, reset_env=reset_env, task_intent=task_intent
        )
        while True:
            messages, reward, done, info = self.step()
            if done:
                break
        return messages, reward, done, info

    def learn(self, reset_env=True):
        if self.resume:
            # keep the inventory
            self.env.reset(
                options={
                    "mode": "soft",
                    "wait_ticks": self.env_wait_ticks,
                }
            )
        else:
            # clear the inventory
            self.env.reset(
                options={
                    "mode": "hard",
                    "wait_ticks": self.env_wait_ticks,
                }
            )
            self.resume = True
        self.last_events = self.env.step("")

        env_crash_streak = 0
        curriculum_failure_streak = 0
        while True:
            # 2026-09-11 审计 01 C-2：原来是 `>`，max_iterations=160 的运行实际停在
            # 161–164 步。改为 `>=`，run_meta.iteration_limit_rule 记录了这一规则。
            if self.recorder.iteration >= self.max_iterations:
                # WP-O：不是 off-by-one。判断在循环开头；最后任务若 n_steps>1，
                # recorder.iteration 可以越过 max_iterations（例如 max=8、末任务
                # 两步 → jsonl 末行 iter=9，stopped_at_iteration=9）。只记真实
                # recorder.iteration，不改循环语义。
                print("Iteration limit reached")
                self._record_run_stop("iteration_limit", self.recorder.iteration)
                break
            self._llm_audit_iter = self.recorder.iteration + 1
            if getattr(self, "_anti_stall_pending", None):
                pending = self._anti_stall_pending
                self._anti_stall_pending = None
                task = pending.get("task")
                context = pending.get("context") or ""
                print(pending.get("log") or f"[anti-stall] injected {task}", flush=True)
                curriculum_failure_streak = 0
            else:
                try:
                    task, context = self.curriculum_agent.propose_next_task(
                        events=self.last_events,
                        chest_observation=self.action_agent.render_chest_observation(),
                        max_retries=5,
                    )
                except CurriculumStalled as e:
                    print(f"\033[41m[curriculum-stall] {e}. Ending this run early.\033[0m")
                    self._record_run_stop("curriculum_stall", self.recorder.iteration)
                    break
                except Exception as e:
                    curriculum_failure_streak += 1
                    print(
                        "\033[41m"
                        f"[curriculum-error] attempt {curriculum_failure_streak}/"
                        f"{CURRICULUM_FAILURE_LIMIT}: {e}"
                        "\033[0m"
                    )
                    if curriculum_failure_streak >= CURRICULUM_FAILURE_LIMIT:
                        print(
                            "\033[41m"
                            f"[curriculum-error] {curriculum_failure_streak} consecutive "
                            "curriculum failures. Ending this run early."
                            "\033[0m"
                        )
                        self._record_run_stop(
                            "curriculum_error_limit", self.recorder.iteration
                        )
                        break
                    retry_delay = _curriculum_retry_delay(curriculum_failure_streak)
                    print(
                        f"\033[41m[curriculum-error] retrying this iteration in "
                        f"{retry_delay} seconds.\033[0m"
                    )
                    time.sleep(retry_delay)
                    continue
                curriculum_failure_streak = 0
            if getattr(self, "v30_shared_fixes", False):
                from voyager.verify import is_impossible_119_item, parse_task

                parsed_task = parse_task(task)
                if parsed_task and is_impossible_119_item(
                    parsed_task.raw_item, parsed_task.targets, task=task
                ):
                    print(
                        f"\033[33m[verify] {task}: 该物品在本版本不存在\033[0m",
                        flush=True,
                    )
                    self.recorder.record([], task)
                    self._anti_stall_observe_round([], task, False)
                    self.curriculum_agent.update_exploration_progress(
                        {
                            "task": task,
                            "success": False,
                            "critique": "该物品在本版本不存在",
                        }
                    )
                    continue
            task_intent = getattr(self.curriculum_agent, "last_task_intent", {}) or (
                infer_task_intent(task, source="task_fallback")
            )
            print(
                f"\033[35mStarting task {task} for at most {self.action_agent_task_max_retries} times\033[0m"
            )
            rollout_crashed = False
            try:
                messages, reward, done, info = self.rollout(
                    task=task,
                    context=context,
                    reset_env=reset_env,
                    task_intent=task_intent,
                )
            except Exception as e:
                time.sleep(3)  # wait for mineflayer to exit
                info = {
                    "task": task,
                    "success": False,
                }
                # 代码跑太久 ≠ 环境死了。前者是模型自己写了过长的循环
                # （典型：5 次 × 120 秒的 exploreUntil），桥接全程活着；
                # 把它计进 env_dead_after 会让一次运气不好的出题废掉整个运行。
                rollout_crashed = not isinstance(e, StepExecutionTimeout)
                if not rollout_crashed:
                    print(
                        "\033[41m[step-timeout] 生成的代码超时，按任务失败处理，"
                        "不计入连续崩溃。\033[0m",
                        flush=True,
                    )
                # 🔴 恢复路径本身必须能失败。
                # 原先这里没有保护：桥接不健康时 env.reset 会再抛一次
                # ReadTimeout，异常从 except 块里逃出去、穿过 learn()，
                # 整个运行当场死亡。2026-09-05 的 A:t1 就是这么没的
                # （voyager.py:620 → bridge.check_process → 300s 读超时）。
                # 恢复失败就按真崩溃计数、继续下一轮，交给 env_dead_after 判死。
                try:
                    self.last_events = self.env.reset(
                        options={
                            "mode": "hard",
                            "wait_ticks": self.env_wait_ticks,
                            "inventory": self.last_events[-1][1]["inventory"],
                            "equipment": self.last_events[-1][1]["status"]["equipment"],
                            "position": self.last_events[-1][1]["status"]["position"],
                        }
                    )
                except Exception as reset_err:
                    rollout_crashed = True   # 连恢复都失败，这就是真环境故障
                    print(
                        f"\033[41m[recovery] 崩溃后重置环境失败：{reset_err}\033[0m",
                        flush=True,
                    )
                # use red color background to print the error
                print("Your last round rollout terminated due to error:")
                print(f"\033[41m{e}\033[0m")
                # 2026-09-11 审计 01 C-4：崩溃行以前与真失败行不可区分。
                info["crashed"] = True
                info["crash_kind"] = "env_crash" if rollout_crashed else "step_timeout"
                info["error"] = f"{type(e).__name__}: {str(e)[:300]}"

            env_crash_streak = _next_env_crash_streak(
                env_crash_streak, rollout_crashed
            )
            if rollout_crashed and not _should_stop_environment_censored(
                env_crash_streak, self.env_dead_after
            ):
                crash_delay = _env_crash_retry_delay(env_crash_streak)
                print(
                    f"\033[41m[env-crash] 第 {env_crash_streak}/{self.env_dead_after} 次连续崩溃，"
                    f"退避 {crash_delay} 秒再继续。\033[0m",
                    flush=True,
                )
                time.sleep(crash_delay)
            if _should_stop_environment_censored(
                env_crash_streak, self.env_dead_after
            ):
                # 用 recorder.iteration（而非 _llm_audit_iter）：崩溃路径下 recorder 不自增，
                # 取 _llm_audit_iter 会比 iterations.jsonl 的最后一行大 1，事后对不上账。
                stopped_iteration = self.recorder.iteration
                self._record_run_stop(
                    "environment_censored",
                    stopped_iteration,
                )
                print(
                    "\033[41m"
                    f"[environment_censored] {env_crash_streak} consecutive rollout "
                    f"crashes at iteration {stopped_iteration}. Ending this run early."
                    "\033[0m"
                )
                break

            if info["success"] and info.get("program_name") and info.get("program_code"):
                end_inv = (
                    self.last_events[-1][1].get("inventory", {})
                    if self.last_events
                    else {}
                )
                start_inv = getattr(self, "rollout_start_inventory", {})
                gained = sorted(
                    [it for it, n in end_inv.items() if n > start_inv.get(it, 0)]
                )
                fingerprint_meta = purify_fingerprint(
                    gained,
                    task_intent=info.get("task_intent") or task_intent,
                    skill_intent=info.get("skill_intent", {}),
                )
                info.update(fingerprint_meta)
                self.skill_manager.add_new_skill(info)

            # 运行时调用计数：mineflayer 侧把 globalThis.__skillCalls 并进最后一次
            # 观测。整理开/关两条路都要用，先统一取出来并做哨兵检查（P0-1）。
            runtime_calls_all = (
                self.last_events[-1][1].get("skillCalls", {})
                if self.last_events
                else {}
            )
            # index.js 每步清空 __skillOutcomes；同一任务多轮要在 Python 侧攒起来
            # 再交给 record_outcomes，否则只有最后一轮会被记账。
            if self.rollout_skill_outcomes:
                runtime_outcomes_all = self.rollout_skill_outcomes
            else:
                runtime_outcomes_all = (
                    self.last_events[-1][1].get("skillOutcomes")
                    if self.last_events and isinstance(self.last_events[-1][1], dict)
                    else None
                )
            skill_credit = getattr(self, "rollout_skill_credit", {}) or {}
            probe_installed = self._check_probe(runtime_calls_all, rollout_crashed)

            if self.skill_manager.curation_enabled and not rollout_crashed:
                # P0-1 B1：本轮物品栏正变化量 = Σ max(0, end-start)（整数）。
                # 缺物品栏数据时记 0，不抛异常。update_skill_usage 内部只在
                # 成功轮且技能被调用时才把它累加进 contribution_sum。
                end_inv = (
                    self.last_events[-1][1].get("inventory", {})
                    if self.last_events
                    else {}
                )
                start_inv = getattr(self, "rollout_start_inventory", {}) or {}
                try:
                    contribution_delta = sum(
                        max(0, int(count) - int(start_inv.get(item, 0)))
                        for item, count in end_inv.items()
                    )
                except (TypeError, ValueError):
                    contribution_delta = 0
                # 运行时调用计数与 inventory 走同一条路：mineflayer 侧把
                # globalThis.__skillCalls 并进观测，这里取最后一次观测的那份。
                runtime_calls = (
                    self.last_events[-1][1].get("skillCalls", {})
                    if self.last_events
                    else {}
                )
                self.skill_manager.update_skill_usage(
                    info.get("success", False),
                    self.rollout_retrieved_skills,
                    _scoring_text(info),
                    contribution_delta=contribution_delta,
                    runtime_calls=runtime_calls,
                    exec_code=info.get("exec_code", ""),
                    runtime_outcomes=runtime_outcomes_all,
                    critic_credit=skill_credit,
                )
                self._curation_step = getattr(self, "_curation_step", 0) + 1
                self._save_curation_state()
                if (
                    self._curation_step
                    % self.skill_manager.curation_interval
                    == 0
                ):
                    retired = self.skill_manager.retire(self.recorder.iteration)
                    if retired:
                        print(
                            f"\033[35m[curation] retired {len(retired)}: {retired}\033[0m"
                        )
                    merged = self.skill_manager.deduplicate()
                    if merged:
                        print(
                            f"\033[35m[curation] dedup archived {len(merged)}: {merged}\033[0m"
                        )

            elif not rollout_crashed:
                # 2026-09-10：整理关的臂也把三路调用判定逐回合落盘（只记录）。
                # 之前这段只在 curation_enabled 下跑，A 臂开 RUNTIME_CALL_PROBE=1
                # 拿不到 call_probe_compare.jsonl。
                self.skill_manager.record_call_probes_only(
                    info.get("success", False),
                    self.rollout_retrieved_skills,
                    _scoring_text(info),
                    runtime_calls=(
                        self.last_events[-1][1].get("skillCalls", {})
                        if self.last_events
                        else {}
                    ),
                    exec_code=info.get("exec_code", ""),
                    runtime_outcomes=runtime_outcomes_all,
                    critic_credit=skill_credit,
                )

            self.curriculum_agent.update_exploration_progress(info)
            used_skills = sorted(
                self.skill_manager._called_skills(
                    info.get("program_code", ""), self.rollout_retrieved_skills
                )
            )
            # 加宽口径：program_code + exec_code。与 used_skills 并存，
            # 用于量化「只搜 program_code」造成的低估。不参与任何计分。
            used_skills_exec = sorted(
                self.skill_manager._called_skills(
                    (info.get("program_code", "") or "")
                    + chr(10)
                    + (info.get("exec_code", "") or ""),
                    self.rollout_retrieved_skills,
                )
            )
            # 2026-09-11 审计 P0-7：used_skills / used_skills_exec 都是带自匹配的 text
            # 判定，分析脚本却把它当"真实调用"。补两路口径并存，事后可对照。
            scoring_text = _scoring_text(info)
            used_skills_nodefs = sorted(
                self.skill_manager._called_skills_no_defs(
                    scoring_text, self.rollout_retrieved_skills
                )
            )
            used_skills_runtime = sorted(
                self.skill_manager._called_skills_runtime(
                    runtime_calls_all, self.rollout_retrieved_skills
                )
            )
            exec_ok, exec_fail, exec_nested, _exec_source = (
                self.skill_manager._exec_sets(
                    runtime_outcomes_all, self.rollout_retrieved_skills
                )
            )
            append_iteration_metrics(
                self.ckpt_dir,
                iter=self.recorder.iteration,
                task=info.get("task"),
                success=info.get("success"),
                skill_count=len(self.skill_manager.skills),
                retrieved_skills=self.rollout_retrieved_skills,
                used_skills=used_skills,
                used_skills_exec=used_skills_exec,
                used_skills_nodefs=used_skills_nodefs,
                used_skills_runtime=used_skills_runtime,
                probe_installed=probe_installed,
                n_steps=self.action_agent_rollout_num_iter,
                parse_fail_model=self._rollout_parse_fail_model,
                parse_fail_infra=self._rollout_parse_fail_infra,
                crashed=bool(info.get("crashed", False)),
                crash_kind=info.get("crash_kind"),
                error=info.get("error"),
                n_completed=len(self.curriculum_agent.completed_tasks),
                n_failed=len(self.curriculum_agent.failed_tasks),
                action_latency_ms=(
                    round(self.rollout_action_latency_ms)
                    if self.rollout_action_latency_ms is not None
                    else None
                ),
                curation_enabled=self.skill_manager.curation_enabled,
                executed_skills=sorted(exec_ok),
                executed_failed=sorted(exec_fail),
                executed_nested=sorted(exec_nested),
                skill_credit=skill_credit,
                exec_args=info.get("exec_args"),
                chain_nodes=info.get("chain_nodes"),
                chain_reused=info.get("chain_reused"),
                chain_gaps=info.get("chain_gaps"),
                chain_gap_failed=info.get("chain_gap_failed"),
                chain_node_ok=info.get("chain_node_ok"),
                chain_route=info.get("chain_route"),
            )
            print(
                f"\033[35mCompleted tasks: {', '.join(self.curriculum_agent.completed_tasks)}\033[0m"
            )
            print(
                f"\033[35mFailed tasks: {', '.join(self.curriculum_agent.failed_tasks)}\033[0m"
            )

        # 🔴 出口一律记停因。原先只有 environment_censored 那一条分支会记，
        #    结果 18 个运行里 11 个的 run_meta.json 没有 stopped_reason，
        #    「这次是跑满了还是被截断了」只能靠 grep 日志反推。
        #    截断与跑满的产出率不可比，这个字段是筛数据的第一道闸。
        #    没有任何 stopped_reason = 进程被外部杀掉（那时轮不到这里执行）。
        if "stopped_reason" not in self.llm_audit.run_meta:
            self._record_run_stop("completed", self.recorder.iteration)
        return {
            "completed_tasks": self.curriculum_agent.completed_tasks,
            "failed_tasks": self.curriculum_agent.failed_tasks,
            "skills": self.skill_manager.skills,
        }

    def decompose_task(self, task):
        if not self.last_events:
            self.last_events = self.env.reset(
                options={
                    "mode": "hard",
                    "wait_ticks": self.env_wait_ticks,
                }
            )
        self._llm_audit_iter = self.recorder.iteration + 1
        return self.curriculum_agent.decompose_task(task, self.last_events)

    def inference(self, task=None, sub_goals=[], reset_mode="hard", reset_env=True):
        if not task and not sub_goals:
            raise ValueError("Either task or sub_goals must be provided")
        if not sub_goals:
            sub_goals = self.decompose_task(task)
        self.env.reset(
            options={
                "mode": reset_mode,
                "wait_ticks": self.env_wait_ticks,
            }
        )
        self.curriculum_agent.completed_tasks = []
        self.curriculum_agent.failed_tasks = []
        self.last_events = self.env.step("")
        while self.curriculum_agent.progress < len(sub_goals):
            next_task = sub_goals[self.curriculum_agent.progress]
            task_intent = infer_task_intent(next_task, source="decomposition_rule")
            context = self.curriculum_agent.get_task_context(next_task)
            print(
                f"\033[35mStarting task {next_task} for at most {self.action_agent_task_max_retries} times\033[0m"
            )
            messages, reward, done, info = self.rollout(
                task=next_task,
                context=context,
                reset_env=reset_env,
                task_intent=task_intent,
            )
            self.curriculum_agent.update_exploration_progress(info)
            print(
                f"\033[35mCompleted tasks: {', '.join(self.curriculum_agent.completed_tasks)}\033[0m"
            )
            print(
                f"\033[35mFailed tasks: {', '.join(self.curriculum_agent.failed_tasks)}\033[0m"
            )
