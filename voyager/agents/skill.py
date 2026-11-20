from voyager.utils import parse_llm_json
import glob
import json
import math
import os
import re
import time

import voyager.utils as U
from voyager.dedup_keys import (
    are_equivalent,
    dedup_skip_reason as dedup_key_skip_reason,
    equivalence_key,
    target_action_for_dedup,
)
from voyager.intent import (
    call_llm_with_retry,
    infer_task_intent,
    normalize_intent,
    target_matches_item,
)
from langchain.schema import HumanMessage, SystemMessage
from langchain.vectorstores import Chroma
from voyager.llm_meta import create_chat_openai

# DeepSeek 没有 Embeddings API，改用本地 HuggingFace 模型
# 首次运行会自动下载模型（~90MB），之后离线可用
try:
    from langchain.embeddings import HuggingFaceEmbeddings
    _EMBEDDINGS = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
except Exception as _emb_err:
    raise ImportError(
        f"无法加载 HuggingFace Embeddings: {_emb_err}\n"
        "请先安装: pip install sentence-transformers"
    )

from voyager.prompts import load_prompt
from voyager.control_primitives import load_control_primitives
from voyager.control_primitives_extra import load_extra_base_skills


def _cosine_similarity(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _distance_to_relevance(distance):
    return 1 / (1 + distance)


AUTO_DEDUP_FINGERPRINT_CONFIDENCE = {"high", "medium"}
INTENT_RETRIEVAL_WEIGHT = 0.25
NON_INVENTORY_DEDUP_ACTIONS = {
    "approach",
    "equip",
    "explore",
    "find",
    "go",
    "locate",
    "move",
    "navigate",
    "open",
    "place",
    "search",
    "travel",
}
VALID_CURATION_MODES = {"baseline", "context"}
VALID_DEDUP_MODES = {"effect", "semantic", "syntactic", "none"}
# P0-1（2026-08-19 冻结，见 reports/P0-1_退役判据规格草案_2026-08-19.md §7.3）
# B1 贡献口径开关：binary=成功次数（默认，旧行为）；effect_delta=物品栏正变化量累计
VALID_CONTRIBUTION_MODES = {"binary", "effect_delta", "exec"}
# B2 替代品保护开关：off=不保护（默认，旧行为）；fp=按效果指纹判替代，无替代则保护
VALID_SUBSTITUTE_GUARDS = {"off", "fp"}
# B2 指纹判不了时的默认：allow_retire=放行移出（fail-open，默认）；protect=保护
VALID_SUBSTITUTE_UNDECIDED = {"allow_retire", "protect"}
_MODE_UNSET = object()


# 🔴 运行时调用计数注入的总开关。**默认关闭。**
#
# 2026-09-03 夜跑实测：开启时三个 trial 全部在第 5 轮死亡——mineflayer 反复
# 超时退出，连续 5 次 rollout 崩溃后触发 env_dead_after 自我终止。关闭后同一
# 世界、同一配置跑过第 10 轮无异常，对照明确。
#
# 注入生成的 JS 语法是合法的（对 155 个真实技能 + 全部控制原语做过
# node --check，开关两种状态都通过），所以是运行时行为问题，成因尚未定位。
# 在定位并修好之前保持关闭，避免它静默毁掉正式运行。
#
# 关闭期间 --call-detect runtime 拿不到计数（退化为空集，等于没有技能被记账，
# 会让所有技能有用度触底而被全部退役）。因此关闭期间 **B 臂必须改用
# --call-detect text_nodefs**：它剔除函数定义处后再做文本匹配，同样修掉
# 自匹配缺陷，只是无法区分"文本里有调用但实际没执行到"。
# 改成环境变量控制，默认仍然关闭。
# 这样验证「今天修完容错之后探针是否还会杀死运行」不需要改代码：
#     RUNTIME_CALL_PROBE=1 python run_voyager.py ...
# 09-03 关掉它的理由是「mineflayer 反复超时 → 连续 5 次崩溃 → 自我终止」，
# 而那条路径正是今天 StepExecutionTimeout 修掉的——当时杀死探针的机制已不存在，
# 值得重测。测通之前默认保持关闭。
#
# ---- 2026-09-06 复盘：至今没有一次**有效**的复测 ----
# 当天 03:20 那次「探针测试」是无效的，别再拿它当证据：
#   · 死亡时 skill_count=0，而 _runtime_call_probe() 开头就是 if not names: return ""
#     —— 注入的是空字符串，探针那一行代码根本没执行过
#   · 最后写盘 03:29:03，WSL 开机时间 03:30:05；日志停在
#     「[llm] action.step response received.」，无报错无 Traceback
#   · 结论：它是被 Windows 自动更新导致的 WSL 重启砍断的，与探针无关
# 但这**不推翻** 09-03 那次三 trial 的对照结论。那次是真对照，只是它的
# 致死路径（超时→算环境死亡→自我终止）已被 StepExecutionTimeout 消除；
# 现在同样的超时只会表现为任务失败，不会杀死运行。
#
# 注入逻辑本身经 _cc0903/test_probe_inject.py 孤立验证过 5 条判据（含
# 「只定义不调用则不计数」「重写后再调用仍计到重写版」），但那是在 node 里
# 跑假技能，**没有覆盖真实 mineflayer**（155 个技能、真异步 bot 操作）。
# 所以现在的状态是：不能说它坏，也不能说它好，欠一次真跑。
# 重测时用 RUNTIME_CALL_PROBE=1 CALL_DETECT=text_nodefs——让退役仍由安全的
# 文本判定驱动，runtime 只被记录不参与决策，这样即使探针有问题也毁不掉数据。
RUNTIME_CALL_PROBE_ENABLED = os.environ.get("RUNTIME_CALL_PROBE", "0") == "1"

class SkillManager:
    def __init__(
        self,
        model_name="gpt-3.5-turbo",
        temperature=0,
        retrieval_top_k=5,
        request_timout=120,
        ckpt_dir="ckpt",
        resume=False,
        curation_enabled=False,
        retrieval_pool_size=20,
        mmr_lambda=0.7,
        retire_min_exposure=12,
        retire_use_threshold=0.15,
        curation_interval=15,
        contribution_mode="binary",
        substitute_guard="off",
        substitute_undecided="allow_retire",
        curation_mode=_MODE_UNSET,
        dedup_mode=_MODE_UNSET,
        intent_fallback_dedup=True,
        retrieval_gate_enabled=False,
        retrieval_gate_threshold=0.35,
        protect_critical_skills=False,
        protected_skills=None,
        thinking="disabled",
        llm_audit=None,
        call_detect="runtime",
        reference_k=None,
        structured_retrieval=False,
        skill_params=False,
        governance="static",
        governance_capacity=None,
        noeffect_guard=False,
        freeze_library=False,
        extra_base_skills=None,
        lru_evict=True,
    ):
        # 哪一种"被调用"判定进入退役规则：text（现行，含自匹配缺陷）/
        # text_nodefs（剔除定义行）/ runtime（运行时真调用）。
        # 默认保持 text——不静默改变既有行为；正式对照显式传 runtime。
        # 🔴 放在 __init__ 最前面：后面会构造 LLM 客户端，那步失败会先抛，
        #    校验排在其后就永远轮不到。
        if call_detect not in ("text", "text_nodefs", "runtime",
                               "reference", "runtime+reference"):
            raise ValueError(f"unknown call_detect: {call_detect}")

        # 🔴 探针关着时选 runtime = 分子恒为 0 = 检索得越多越必然被退役。
        # 这不是"稍差一点的规则"，是所有可能规则里最坏的一个，
        # 而且日志上看不出异常——2026-09-04/05 的全部 B/D 运行都这样跑掉了
        # （38 个归档技能，success_count 无一例外是 0）。
        # 上面那段注释 09-03 就写了警告，光靠注释拦不住，改成硬失败。
        if call_detect in ("runtime", "runtime+reference") \
                and not RUNTIME_CALL_PROBE_ENABLED and curation_enabled:
            raise ValueError(
                "call_detect='runtime' 但 RUNTIME_CALL_PROBE_ENABLED=False：\n"
                "  运行时计数拿不到，分子恒为空集，u(s)=1/(检索数+2)，\n"
                "  结果是被检索越多的技能越必然被退役，与有用与否无关。\n"
                "  整理开启的臂请改用 --call-detect text_nodefs，\n"
                "  或先修好运行时探针再把 RUNTIME_CALL_PROBE_ENABLED 打开。"
            )

        if curation_mode is _MODE_UNSET or curation_mode is None:
            curation_mode = os.environ.get("CURATION_MODE", "baseline")
        curation_mode = str(curation_mode).strip().lower()
        if curation_mode not in VALID_CURATION_MODES:
            raise ValueError(f"Invalid curation_mode: {curation_mode}")

        if dedup_mode is _MODE_UNSET or dedup_mode is None:
            dedup_mode = os.environ.get("DEDUP_MODE", "effect")
        dedup_mode = str(dedup_mode).strip().lower()
        if dedup_mode not in VALID_DEDUP_MODES:
            raise ValueError(f"Invalid dedup_mode: {dedup_mode}")

        contribution_mode = str(contribution_mode).strip().lower()
        if contribution_mode not in VALID_CONTRIBUTION_MODES:
            raise ValueError(f"Invalid contribution_mode: {contribution_mode}")
        substitute_guard = str(substitute_guard).strip().lower()
        if substitute_guard not in VALID_SUBSTITUTE_GUARDS:
            raise ValueError(f"Invalid substitute_guard: {substitute_guard}")
        substitute_undecided = str(substitute_undecided).strip().lower()
        if substitute_undecided not in VALID_SUBSTITUTE_UNDECIDED:
            raise ValueError(f"Invalid substitute_undecided: {substitute_undecided}")

        if not isinstance(intent_fallback_dedup, bool):
            raise ValueError("intent_fallback_dedup must be a bool")
        if not isinstance(retrieval_gate_enabled, bool):
            raise ValueError("retrieval_gate_enabled must be a bool")
        if not isinstance(protect_critical_skills, bool):
            raise ValueError("protect_critical_skills must be a bool")
        if protected_skills is None:
            protected_skills = set()
        elif isinstance(protected_skills, str):
            raise ValueError("protected_skills must be an iterable of skill names")
        else:
            protected_skills = set(protected_skills)

        self.llm = create_chat_openai(
            model_name=model_name,
            temperature=temperature,
            request_timeout=request_timout,
            thinking=thinking,
            audit=llm_audit,
            agent="skill",
        )
        U.f_mkdir(f"{ckpt_dir}/skill/code")
        U.f_mkdir(f"{ckpt_dir}/skill/description")
        U.f_mkdir(f"{ckpt_dir}/skill/vectordb")
        # programs for env execution
        # 点名名单：退役时从此列表移除。None/空与改动前相同（不注入 extra）。
        self.extra_base_skills = list(extra_base_skills or [])
        self.control_primitives = load_control_primitives() + load_extra_base_skills(
            extra_base_skills
        )
        # 检索隔离：空集合时不走过滤分支，retrieve 与改动前相同。
        self.retrieval_blacklist = set()
        # v30：记账后是否做 LRU 移出。默认 True＝现状（记账后无条件 evict）。
        self.lru_evict = bool(lru_evict)
        if resume:
            print(f"\033[33mLoading Skill Manager from {ckpt_dir}/skill\033[0m")
            self.skills = U.load_json(f"{ckpt_dir}/skill/skills.json")
        else:
            self.skills = {}
        for entry in self.skills.values():
            entry["retrieved_count"] = entry.get("retrieved_count", 0)
            entry["success_count"] = entry.get("success_count", 0)
            # P0-1 B1：resume 旧库时给缺失的贡献量字段补 0
            entry["contribution_sum"] = entry.get("contribution_sum", 0)
            entry["exec_ok_count"] = entry.get("exec_ok_count", 0)
            entry["exec_fail_count"] = entry.get("exec_fail_count", 0)
            entry["critic_credit_count"] = entry.get("critic_credit_count", 0)
            entry["fingerprint"] = entry.get("fingerprint", [])
            entry["raw_fingerprint"] = entry.get(
                "raw_fingerprint", entry.get("fingerprint", [])
            )
            entry["task_intent"] = entry.get("task_intent", {})
            entry["skill_intent"] = entry.get("skill_intent", {})
            entry["fingerprint_confidence"] = entry.get(
                "fingerprint_confidence", "unknown"
            )
            entry["fingerprint_filter_reason"] = entry.get(
                "fingerprint_filter_reason", "legacy"
            )
            entry["intent_consistency"] = entry.get("intent_consistency", "legacy")
        self.retrieval_top_k = retrieval_top_k
        self.curation_enabled = curation_enabled
        self.retrieval_pool_size = retrieval_pool_size
        self.mmr_lambda = mmr_lambda
        self.retire_min_exposure = retire_min_exposure
        self.retire_use_threshold = retire_use_threshold
        self.curation_interval = curation_interval
        self.contribution_mode = contribution_mode
        self.call_detect = call_detect
        if reference_k is not None:
            self.K_REFERENCE = int(reference_k)
        # G 臂（甲）：按动作类别硬过滤候选，再走原有重排。
        # 关闭时与改动前逐字节等价。
        self.structured_retrieval = structured_retrieval
        self.skill_params = bool(skill_params)
        governance = str(governance or "static").strip().lower()
        if governance not in ("static", "dynamic"):
            raise ValueError(f"unknown governance: {governance}")
        self.governance = governance
        self.governance_capacity = governance_capacity
        self.noeffect_guard = bool(noeffect_guard)
        self.governor = None
        self.substitute_guard = substitute_guard
        self.substitute_undecided = substitute_undecided
        self.retrieval_gate_threshold = retrieval_gate_threshold
        self.curation_mode = curation_mode
        self.dedup_mode = dedup_mode
        self.intent_fallback_dedup = intent_fallback_dedup
        self.retrieval_gate_enabled = retrieval_gate_enabled
        self.protect_critical_skills = protect_critical_skills
        self.protected_skills = protected_skills
        self.ckpt_dir = ckpt_dir
        # v29 零样本：库冻结开关，默认关（关时下面所有路径与改动前相同）。
        # 开时 add_new_skill / 归档 / 去重 / 退役 / LRU 移出 / 计数与治理状态落盘都不改库，
        # 只打日志（frozen_ops.jsonl）；检索等日志照常写在 ckpt_dir（零样本里就是库副本）。
        self.freeze_library = bool(freeze_library)
        self.vectordb = Chroma(
            collection_name="skill_vectordb",
            embedding_function=_EMBEDDINGS,
            persist_directory=f"{ckpt_dir}/skill/vectordb",
        )
        assert self.vectordb._collection.count() == len(self.skills), (
            f"Skill Manager's vectordb is not synced with skills.json.\n"
            f"There are {self.vectordb._collection.count()} skills in vectordb but {len(self.skills)} skills in skills.json.\n"
            f"Did you set resume=False when initializing the manager?\n"
            f"You may need to manually delete the vectordb directory for running from scratch."
        )
        self.last_retrieved_skill_names = []
        self.cur_iter = None
        self._last_rerank_scores = {}
        self._last_query_intent = None
        if self.governance == "dynamic":
            from voyager.governance.lru import LRUGovernor

            state = None
            state_path = self._governance_state_path()
            if resume and os.path.exists(state_path):
                try:
                    state = U.load_json(state_path)
                except Exception as err:
                    print(
                        f"\033[33m[governance] 读取 governance_state.json 失败，从空状态起：{err}\033[0m"
                    )
                    state = None
            self.governor = LRUGovernor(self.governance_capacity, state=state)
            if resume and (not state or not state.get("skills")):
                for name, entry in self.skills.items():
                    created = entry.get("created_iter")
                    self.governor.add(
                        name, created if created is not None else 0
                    )
            self._save_governor_state()

    def build_programs(self, disabled=None, extra_stubs=None):
        """拼装注入 evaluateCode 的 JS。``programs`` 属性是它在 disabled 为空时的别名。

        disabled 里的名字（必须 isidentifier 且在 self.skills 里，否则忽略）
        在「技能代码 + control primitives」之后、运行时探针包装之前，被替换成
        立即抛错的替身。函数声明会被提升，替身是语句，所以库内其它技能对它的
        调用同样走到替身。探针包装的是最终生效的那一版，因此调用被禁技能也会
        被记成一条 ok=false、err=替身信息 的 outcome。

        extra_stubs（v29 --chain-node-repair）：调用方已拼好的替身语句，放在同一位置
        （disabled 替身之后、探针之前）。None/空时输出与改动前逐字节相同。
        """
        from voyager.governance.noeffect import noeffect_disabled_stub_js

        programs = ""
        for skill_name, entry in self.skills.items():
            programs += f"{entry['code']}\n\n"
        for primitives in self.control_primitives:
            programs += f"{primitives}\n\n"
        for name in disabled or []:
            if not isinstance(name, str) or not name.isidentifier():
                continue
            if name not in self.skills:
                continue
            programs += noeffect_disabled_stub_js(name) + "\n"
        for stub in extra_stubs or []:
            programs += stub + "\n"
        # 🔴 运行时计数注入默认关闭，见 RUNTIME_CALL_PROBE_ENABLED 的说明。
        if RUNTIME_CALL_PROBE_ENABLED:
            programs += self._runtime_call_probe()
        return programs

    @property
    def programs(self):
        return self.build_programs()

    def _runtime_call_probe(self):
        """注入运行时调用计数包装。

        技能函数与模型写的程序跑在同一个 async IIFE 里（mineflayer/index.js
        evaluateCode）。这里在 programs 末尾追加一段包装：把每个技能函数换成
        "先记一次调用、再调原函数"的版本，计数写进 globalThis.__skillCalls。
        每次调用另把 {name, ok, err, t_ms, delta} 推进 globalThis.__skillOutcomes
        （开始时 push，finally 填结果——数组顺序 = 调用开始顺序）。

        为什么这样能修掉自匹配：函数声明会被提升到 IIFE 顶部，所以模型重写某个
        技能时提升的是新定义；而本段包装是语句，在模型代码语句之前执行，包到的
        就是最终生效的那一版。**定义本身不会计数，只有真调用才会。**

        技能之间的互相调用也会走包装，因此组合率可以直接从运行时读出，
        不必事后扫文件再排除自身名字匹配。包装必须是 async + await，否则
        ok/err/delta 在技能返回前就算完，嵌套调用测不到。
        """
        names = [n for n in self.skills.keys() if n.isidentifier()]
        if not names:
            return ""
        lines = [
            "// —— 运行时调用计数（由 SkillManager._runtime_call_probe 注入）——",
            "globalThis.__skillCalls = globalThis.__skillCalls || {};",
            "globalThis.__skillOutcomes = globalThis.__skillOutcomes || [];",
            "globalThis.__invSnap = function (__bot) {",
            "  const m = {};",
            "  try {",
            "    const items = (__bot && __bot.inventory && typeof __bot.inventory.items === 'function')",
            "      ? __bot.inventory.items() : [];",
            "    for (const it of (items || [])) {",
            "      if (!it || !it.name) continue;",
            "      m[it.name] = (m[it.name] || 0) + (typeof it.count === 'number' ? it.count : 1);",
            "    }",
            "  } catch (e) {}",
            "  return m;",
            "};",
            "globalThis.__invDelta = function (before, after) {",
            "  const d = {};",
            "  const keys = {};",
            "  for (const k in (before || {})) keys[k] = 1;",
            "  for (const k in (after || {})) keys[k] = 1;",
            "  for (const k in keys) {",
            "    const n = ((after && after[k]) || 0) - ((before && before[k]) || 0);",
            "    if (n !== 0) d[k] = n;",
            "  }",
            "  return d;",
            "};",
            # 🔴 安装哨兵。没有它，runtime_raw={} 分不清两种截然不同的情况：
            #   (a) 这段注入压根没执行（链路断了，runtime 那列是假象）
            #   (b) 注入执行了，但这一轮确实没有任何技能被调用（真值就是 0）
            # 09-06 的探针复测就卡在这里：三个运行 runtime_raw 全空，
            # 而同期 nodefs 也全是 0，两种解释都说得通，数据分不开。
            # 值取包装了多少个技能，顺带能看出库大小对不对得上。
            # 名字带 __ 前后缀，不可能出现在候选技能名里，
            # 因此 _called_skills_runtime 的 name in candidates 过滤会把它排除，
            # 不会污染 runtime 判定结果。
            f"globalThis.__skillCalls['__probe_installed__'] = {len(names)};",
        ]
        for name in names:
            lines.append(
                f"try {{ if (typeof {name} === 'function') {{ "
                f"const __orig_{name} = {name}; "
                f"{name} = async function (...args) {{ "
                f"const __bot = args[0]; "
                f"const __t0 = Date.now(); "
                f"const __before = globalThis.__invSnap(__bot); "
                f"const rec = {{name: '{name}', ok: false, err: null, t_ms: 0, delta: {{}}}}; "
                f"globalThis.__skillOutcomes = globalThis.__skillOutcomes || []; "
                f"globalThis.__skillOutcomes.push(rec); "
                f"globalThis.__skillCalls['{name}'] = "
                f"(globalThis.__skillCalls['{name}'] || 0) + 1; "
                f"try {{ "
                f"const r = await __orig_{name}.apply(this, args); "
                f"rec.ok = true; rec.err = null; "
                f"return r; "
                f"}} catch (e) {{ "
                f"rec.ok = false; "
                f"rec.err = String((e && e.message) || e).slice(0, 200); "
                f"throw e; "
                f"}} finally {{ "
                f"rec.t_ms = Date.now() - __t0; "
                f"rec.delta = globalThis.__invDelta(__before, globalThis.__invSnap(__bot)); "
                f"}} "
                f"}}; "
                f"}} }} catch (e) {{}}"
            )
        return "\n".join(lines) + "\n\n"

    def add_new_skill(self, info):
        if getattr(self, "freeze_library", False):
            self._frozen_skip(
                "add_new_skill", skill=info.get("program_name"), task=info.get("task")
            )
            return
        if info["task"].startswith("Deposit useless items into the chest at"):
            # No need to reuse the deposit skill
            return
        program_name = info["program_name"]
        program_code = info["program_code"]
        prev_pending = getattr(self, "_pending_skill_signature", None)
        self._pending_skill_signature = info.get("signature")
        try:
            skill_description = self.generate_skill_description(
                program_name, program_code
            )
        finally:
            self._pending_skill_signature = prev_pending
        print(
            f"\033[33mSkill Manager generated description for {program_name}:\n{skill_description}\033[0m"
        )
        if program_name in self.skills:
            print(f"\033[33mSkill {program_name} already exists. Rewriting!\033[0m")
            self.vectordb._collection.delete(ids=[program_name])
            i = 2
            while f"{program_name}V{i}.js" in os.listdir(f"{self.ckpt_dir}/skill/code"):
                i += 1
            dumped_program_name = f"{program_name}V{i}"
        else:
            dumped_program_name = program_name
        self.vectordb.add_texts(
            texts=[skill_description],
            ids=[program_name],
            metadatas=[{"name": program_name}],
        )
        # P0-1 H4 修复（2026-08-24）：同名重学过去是整条 dict 替换，三个计数器与
        # created_iter 一并归零。实测 b1b2test0824 一轮内发生 ≥10 次重学，12 点真实
        # 贡献被抹掉 7 点，B1 的分子在原理上永远攒不起来；且 fingerprint 一并被新版
        # 的空值覆盖，导致 B2 判不出替代品而 fail-open 放行退役。
        # 口径（用户 2026-08-24 拍板）：计数全继承累加；created_iter 保留最初值；
        # fingerprint 仅在新版为空时沿用旧版。合并写法与 deduplicate() 保持一致。
        prev_entry = self.skills.get(program_name)
        new_fingerprint = info.get("fingerprint", [])
        new_raw_fingerprint = info.get("raw_fingerprint", new_fingerprint)
        entry = {
            "code": program_code,
            "description": skill_description,
            "retrieved_count": (
                prev_entry.get("retrieved_count", 0) if prev_entry else 0
            ),
            "success_count": (
                prev_entry.get("success_count", 0) if prev_entry else 0
            ),
            "contribution_sum": (
                prev_entry.get("contribution_sum", 0) if prev_entry else 0
            ),
            "exec_ok_count": (
                prev_entry.get("exec_ok_count", 0) if prev_entry else 0
            ),
            "exec_fail_count": (
                prev_entry.get("exec_fail_count", 0) if prev_entry else 0
            ),
            "critic_credit_count": (
                prev_entry.get("critic_credit_count", 0) if prev_entry else 0
            ),
            "fingerprint": new_fingerprint,
            "raw_fingerprint": new_raw_fingerprint,
            "task_intent": info.get("task_intent", {}),
            "skill_intent": info.get("skill_intent", {}),
            "fingerprint_confidence": info.get("fingerprint_confidence", "unknown"),
            "fingerprint_filter_reason": info.get(
                "fingerprint_filter_reason", "unknown"
            ),
            "intent_consistency": info.get("intent_consistency", "unknown"),
            "created_iter": (
                prev_entry.get("created_iter", self.cur_iter)
                if prev_entry
                else self.cur_iter
            ),
            "signature": info.get("signature"),
            "exec_args_example": (
                prev_entry.get("exec_args_example")
                if prev_entry is not None
                and prev_entry.get("exec_args_example") is not None
                else info.get("exec_args")
            ),
        }
        if prev_entry is not None:
            entry["rewrite_count"] = prev_entry.get("rewrite_count", 0) + 1
            entry["last_rewrite_iter"] = self.cur_iter
            # 新版指纹为空（该轮库存无净增）时沿用旧版，避免 B2 因 effect_key=None
            # 走 substitute_undecided=allow_retire 而误放行。
            if not new_fingerprint and prev_entry.get("fingerprint"):
                entry["fingerprint"] = prev_entry["fingerprint"]
                entry["raw_fingerprint"] = prev_entry.get(
                    "raw_fingerprint", prev_entry["fingerprint"]
                )
                entry["fingerprint_confidence"] = prev_entry.get(
                    "fingerprint_confidence", "unknown"
                )
                entry["fingerprint_filter_reason"] = "inherited_from_previous_version"
        self.skills[program_name] = entry
        assert self.vectordb._collection.count() == len(
            self.skills
        ), "vectordb is not synced with skills.json"
        U.dump_text(
            program_code, f"{self.ckpt_dir}/skill/code/{dumped_program_name}.js"
        )
        U.dump_text(
            skill_description,
            f"{self.ckpt_dir}/skill/description/{dumped_program_name}.txt",
        )
        U.dump_json(self.skills, f"{self.ckpt_dir}/skill/skills.json")
        self.vectordb.persist()
        if getattr(self, "governance", "static") == "dynamic" and self.governor is not None:
            task_idx = self.cur_iter if self.cur_iter is not None else 0
            self.governor.add(program_name, task_idx)
            self._save_governor_state()
            # WP-O: do not evict here. Mid-task adds must stay available for
            # later action rounds of the same task; eviction runs after
            # record_outcomes in _dynamic_after_outcomes.

    def generate_skill_description(self, program_name, program_code, signature=None):
        if signature is None:
            signature = getattr(self, "_pending_skill_signature", None)
        param_desc = (
            "\n6) Explain the meaning and default value of each parameter."
            if getattr(self, "skill_params", False)
            else ""
        )
        system_content = load_prompt("skill").replace("{param_desc_rule}", param_desc)
        human_content = (
            program_code + "\n\n" + f"The main function is `{program_name}`."
        )
        if getattr(self, "skill_params", False):
            sig = signature or {}
            parts = []
            for param in sig.get("params") or []:
                name = param.get("name") or ""
                default = param.get("default")
                if default is not None:
                    parts.append(f"{name} = {default}")
                else:
                    parts.append(name)
            inner = ", ".join(parts) if parts else "bot"
            human_content += f"\nSignature: async function {program_name}({inner})."
        messages = [
            SystemMessage(content=system_content),
            HumanMessage(content=human_content),
        ]
        skill_description = (
            "    // "
            + call_llm_with_retry(
                self.llm, messages, label="skill.generate_description"
            ).content
        )
        return f"async function {program_name}(bot) {{\n{skill_description}\n}}"

    def retrieve_skills(self, query):
        # 每次检索前清掉上一轮的结构化统计，否则走不到过滤的分支时
        # （k==0，或整理关）会把上一轮的旧值记进日志。
        self._last_structured_stat = None
        if getattr(self, "governance", "static") == "dynamic":
            return self._retrieve_skills_dynamic(query)
        k = min(self.vectordb._collection.count(), self.retrieval_top_k)
        if k == 0:
            self.last_retrieved_skill_names = []
            self._record_retrieval(query, [])
            return []
        print(f"\033[33mSkill Manager retrieving for {k} skills\033[0m")
        if not self.curation_enabled:
            self._last_rerank_scores = {}
            self._last_query_intent = None
            docs_and_scores = self.vectordb.similarity_search_with_score(query, k=k)
        else:
            pool_k = min(self.vectordb._collection.count(), self.retrieval_pool_size)
            docs_and_scores = self.vectordb.similarity_search_with_score(query, k=pool_k)
            if self.structured_retrieval:
                docs_and_scores = self._filter_by_action_class(
                    query, docs_and_scores, k
                )
            docs_and_scores = self._select_diverse_skills(query, docs_and_scores, k)
        bl = getattr(self, "retrieval_blacklist", None) or set()
        if bl:
            docs_and_scores = [
                (doc, score)
                for doc, score in docs_and_scores
                if doc.metadata.get("name") not in bl
            ]
        self.last_retrieved_skill_names = [
            doc.metadata["name"] for doc, _ in docs_and_scores
        ]
        print(
            f"\033[33mSkill Manager retrieved skills: "
            f"{', '.join(self.last_retrieved_skill_names)}\033[0m"
        )
        skills = []
        for doc, _ in docs_and_scores:
            skills.append(self.skills[doc.metadata["name"]]["code"])
        self._record_retrieval(query, docs_and_scores)
        return skills

    def similar_skills_for_reference(self, query, k=None):
        """v28 `--gap-reference`：与原版（static 治理、整理关）同口径的相似度 top-k。

        机制臂是 dynamic 治理，`retrieve_skills` 返回按近期性排序的整库列表、不看相似度；
        写缺口代码时要补齐的是原版 action agent 看到的那 5 条，所以这里单独做一次原版口径的检索。
        只读：不写 retrieval_log、不改 last_retrieved_skill_names，不影响任何记账。
        向量库与当前库一一对应（add/rewrite/_archive_skill 同步增删），名字仍按当前库过滤以防万一。
        返回 (names, codes)。
        """
        k = self.retrieval_top_k if k is None else int(k)
        n = self.vectordb._collection.count()
        if k <= 0 or n == 0:
            return [], []
        docs_and_scores = self.vectordb.similarity_search_with_score(query, k=min(n, k))
        names = []
        for doc, _ in docs_and_scores:
            name = doc.metadata.get("name")
            if name in self.skills and name not in names:
                names.append(name)
        return names, [self.skills[name]["code"] for name in names]

    def _retrieve_skills_dynamic(self, query):
        """Full-library list ordered by LRUGovernor; query similarity is ignored."""
        self._last_rerank_scores = {}
        self._last_query_intent = None
        task_idx = self.cur_iter if self.cur_iter is not None else 0
        names = []
        if self.governor is not None:
            bl = getattr(self, "retrieval_blacklist", None) or set()
            names = [
                name
                for name in self.governor.shown_list(task_idx)
                if name in self.skills and name not in bl
            ]
        self.last_retrieved_skill_names = names
        print(
            f"\033[33mSkill Manager dynamic list ({len(names)}): "
            f"{', '.join(names)}\033[0m"
        )
        skills = [self.skills[name]["code"] for name in names]
        self._record_retrieval(query, [])
        return skills

    def _filter_by_action_class(self, query, docs_and_scores, k):
        """G 臂（甲）：按动作类别硬过滤候选池。

        任务归到哪一类，就只保留同类的技能。这一步把"描述向量谁更像"换成
        "做的是不是同一类事"——离散匹配，不受描述互相挤压的影响。

        同类不足 k 个时用原候选补齐（**回落**），保证进提示词的技能数不减少；
        否则分类稀疏会直接削弱 G 臂，测出来的就不是分类本身的效果了。

        类别为空或 other 时不过滤——那说明分类没判出来，硬过滤没有依据。
        """
        query_intent = infer_task_intent(query, source="retrieval_query")
        query_action = query_intent.get("target_action", "")
        if not query_action or query_action == "other":
            self._last_structured_stat = {"applied": False, "reason": "no_class"}
            return docs_and_scores

        same, rest = [], []
        for doc, score in docs_and_scores:
            entry = self.skills.get(doc.metadata["name"], {})
            entry_action = self._entry_intent_for_retrieval(
                entry, doc.metadata["name"]
            ).get("target_action", "")
            (same if entry_action == query_action else rest).append((doc, score))

        self._last_structured_stat = {
            "applied": True,
            "query_action": query_action,
            "same_class": len(same),
            "pool": len(docs_and_scores),
            "fell_back": len(same) < k,
        }
        if len(same) >= k:
            return same
        # 同类不够，按原顺序补齐
        return same + rest[: max(0, k - len(same))]

    def _select_diverse_skills(self, query, docs_and_scores, k):
        self._last_rerank_scores = {}
        query_intent = infer_task_intent(query, source="retrieval_query")
        self._last_query_intent = query_intent
        # Adapted from collaborator curation.js retrievalGate (L401).
        if getattr(self, "retrieval_gate_enabled", False):
            ungated_docs_and_scores = docs_and_scores
            docs_and_scores = [
                (doc, score)
                for doc, score in docs_and_scores
                if self._retrieval_gate(self.skills[doc.metadata["name"]])
            ]
            if not docs_and_scores:
                docs_and_scores = ungated_docs_and_scores
                try:
                    self._record_curation_event(
                        {
                            "action": "retrieval_gate_fallback",
                            "count": len(ungated_docs_and_scores),
                            "reason": "all_candidates_filtered",
                        }
                    )
                except Exception as exc:
                    print(
                        f"\033[33m[retrieval-gate] fallback log skipped "
                        f"(non-fatal): {exc}\033[0m"
                    )
        if len(docs_and_scores) <= k:
            return docs_and_scores
        embeddings = _EMBEDDINGS.embed_documents(
            [doc.page_content for doc, _ in docs_and_scores]
        )
        selected = []
        remaining = list(range(len(docs_and_scores)))
        while remaining and len(selected) < k:
            best_idx = None
            best_score = None
            best_metrics = None
            for idx in remaining:
                doc, distance = docs_and_scores[idx]
                name = doc.metadata["name"]
                entry = self.skills[name]
                rel = _distance_to_relevance(distance)
                intent_score = self._retrieval_intent_score(query_intent, entry, name)
                rel = max(0, rel + INTENT_RETRIEVAL_WEIGHT * intent_score)
                use = (entry.get("success_count", 0) + 1) / (
                    entry.get("retrieved_count", 0) + 2
                )
                red = 0
                if selected:
                    red = max(
                        _cosine_similarity(embeddings[idx], embeddings[chosen_idx])
                        for chosen_idx in selected
                    )
                quality = rel * use
                diversity_factor = max(0, 1 - (1 - self.mmr_lambda) * red)
                score = quality * diversity_factor
                if best_score is None or score > best_score:
                    best_idx = idx
                    best_score = score
                    best_metrics = {
                        "relevance": rel,
                        "intent_score": intent_score,
                        "use": use,
                    }
            if best_metrics is not None:
                name = docs_and_scores[best_idx][0].metadata["name"]
                self._last_rerank_scores[name] = best_metrics
            selected.append(best_idx)
            remaining.remove(best_idx)
        return [docs_and_scores[idx] for idx in selected]

    def _retrieval_gate(self, entry, n_min=None, tau=None):
        # 阈值来自构造参数，不再硬编码；曝光门与退役共用（论文 Table 5）
        if n_min is None:
            n_min = self.retire_min_exposure
        if tau is None:
            tau = self.retrieval_gate_threshold
        """True=保留该候选。曝光不足时一律放行（探索宽限）。"""
        retrieved_count = entry.get("retrieved_count", 0)
        if retrieved_count < n_min:
            return True
        usefulness = (entry.get("success_count", 0) + 1) / (retrieved_count + 2)
        return usefulness >= tau

    def _entry_intent_for_retrieval(self, entry, skill_name=None):
        skill_intent = normalize_intent(entry.get("skill_intent", {}), source="skill")
        task_intent = normalize_intent(entry.get("task_intent", {}), source="task")
        if (
            skill_intent.get("target_action")
            or skill_intent.get("target_items")
            or skill_intent.get("target_entities")
        ):
            return skill_intent
        if (
            task_intent.get("target_action")
            or task_intent.get("target_items")
            or task_intent.get("target_entities")
        ):
            return task_intent
        return infer_task_intent(skill_name or "", source="skill_name")

    def _targets_overlap(self, query_targets, skill_targets):
        for query_target in query_targets:
            for skill_target in skill_targets:
                if target_matches_item(query_target, skill_target) or target_matches_item(
                    skill_target, query_target
                ):
                    return True
        return False

    def _retrieval_intent_score(self, query_intent, entry, skill_name=None):
        query_intent = normalize_intent(query_intent, source="retrieval_query")
        skill_intent = self._entry_intent_for_retrieval(entry, skill_name)
        query_action = query_intent.get("target_action", "")
        skill_action = skill_intent.get("target_action", "")
        query_targets = query_intent.get("target_items", []) + query_intent.get(
            "target_entities", []
        )
        skill_targets = skill_intent.get("target_items", []) + skill_intent.get(
            "target_entities", []
        )

        action_match = query_action and skill_action and query_action == skill_action
        target_match = self._targets_overlap(query_targets, skill_targets)
        action_conflict = query_action and skill_action and query_action != skill_action

        score = 0.0
        if action_match:
            score += 0.2
        if target_match:
            score += 0.3
        if action_match and target_match:
            score += 0.15
        elif action_conflict and target_match:
            score -= 0.2
        elif action_conflict:
            score -= 0.05
        return score

    def update_skill_usage(
        self,
        success: bool,
        retrieved_skills: list,
        solution_code: str = "",
        contribution_delta=None,
        runtime_calls=None,
        exec_code: str = "",
        runtime_outcomes=None,
        critic_credit=None,
    ):
        # 曝光计数：对本轮检索到的每个技能 +1（口径不变）
        # 成功计数：只记给"在成功代码里真被调用"的技能，避免陪跑技能被摊派成功
        # P0-1 B1：contribution_sum 记本轮物品栏正变化量（整数），规则冻结：
        # - 仅成功轮且技能名出现在最终程序调用里（used）才累加；
        # - 被检索未被调用：曝光 +1，contribution_sum 不加（本轮贡献为 0）；
        # - 同一程序里多个技能被调用：每个被调用技能都加整轮正变化量（无法更细归因）；
        # - failed_round_credit=off：失败轮 used 为空集，既不记 success 也不记贡献；
        # - 缺物品栏数据时 contribution_delta 传 None（或非法值），记 0，不抛异常。
        # 第零道门：同一批技能，三种独立判定并行记录。
        #   text        现行探针——整程序文本搜 `名字(`，会把重写轮的定义行算成调用
        #   text_nodefs 同上，但先剔掉函数定义处，用来量化自匹配贡献了多少
        #   runtime     运行时真调用计数（mineflayer 侧包装回传），"被调用"的真值
        # 只有 self.call_detect 选中的那一种进入退役规则；另外两种只记录，供比对。
        probes = {
            "text": self._called_skills(solution_code, retrieved_skills),
            "text_nodefs": self._called_skills_no_defs(
                solution_code, retrieved_skills
            ),
            "runtime": self._called_skills_runtime(runtime_calls, retrieved_skills),
        }
        # 参考、以及"调用并上参考"，两者恒记录；只有 call_detect 选中的进入退役规则。
        probes["reference"] = self._referenced_skills(solution_code, retrieved_skills)
        probes["runtime+reference"] = probes["runtime"] | probes["reference"]
        exec_ok, exec_fail, exec_nested, exec_source = self._exec_sets(
            runtime_outcomes, retrieved_skills
        )
        self._log_call_probes(
            probes, retrieved_skills, success, runtime_calls,
            solution_code=solution_code, exec_code=exec_code,
            exec_ok=exec_ok, exec_fail=exec_fail, exec_nested=exec_nested,
            exec_source=exec_source, critic_credit=critic_credit,
        )
        if getattr(self, "freeze_library", False):
            self._frozen_skip(
                "update_skill_usage", n_retrieved=len(set(retrieved_skills or []))
            )
            return

        selected = probes.get(self.call_detect, probes["text"])
        used = selected if success else set()
        credit = self._contribution_credit(contribution_delta)
        for name in set(retrieved_skills):
            if name not in self.skills:
                continue
            self.skills[name]["retrieved_count"] = (
                self.skills[name].get("retrieved_count", 0) + 1
            )
            if name in used:
                self.skills[name]["success_count"] = (
                    self.skills[name].get("success_count", 0) + 1
                )
                self.skills[name]["contribution_sum"] = (
                    self.skills[name].get("contribution_sum", 0) + credit
                )
        # exec_*：只在有 runtime_outcomes 时更新；不看是否检索、不看任务成败。
        # 每任务每技能最多 +1。runtime_outcomes is None（老 bridge / 探针关）则不动。
        if runtime_outcomes is not None:
            for name in exec_ok:
                if name not in self.skills:
                    continue
                self.skills[name]["exec_ok_count"] = (
                    self.skills[name].get("exec_ok_count", 0) + 1
                )
            for name in exec_fail:
                if name not in self.skills:
                    continue
                self.skills[name]["exec_fail_count"] = (
                    self.skills[name].get("exec_fail_count", 0) + 1
                )
        if isinstance(critic_credit, dict):
            credited = set()
            for name, flag in critic_credit.items():
                if flag is True and name in self.skills and name not in credited:
                    self.skills[name]["critic_credit_count"] = (
                        self.skills[name].get("critic_credit_count", 0) + 1
                    )
                    credited.add(name)
        U.dump_json(self.skills, f"{self.ckpt_dir}/skill/skills.json")
        self._dynamic_after_outcomes(runtime_outcomes)

    @staticmethod
    def _contribution_credit(contribution_delta):
        """把本轮物品栏正变化量折算为非负整数；缺失/非法数据记 0，不抛异常。"""
        if contribution_delta is None:
            return 0
        try:
            return max(0, int(contribution_delta))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _called_skills(code, candidate_names):
        """返回 candidate_names 中、以函数调用形式出现在 code 里的技能名集合。

        🔴 已知缺陷（保留原样，作为第零道门的对照基准）：技能被重写时，新程序里
        `async function NAME(bot) {` 这一行同样命中本正则，于是重写轮被记为
        "调用了自己"。真值判定见 _called_skills_runtime。
        """
        if not code:
            return set()
        called = set()
        for name in set(candidate_names):
            if re.search(r"\b" + re.escape(name) + r"\s*\(", code):
                called.add(name)
        return called

    # ---- 参考（照抄）判定。口径照搬预注册分析 A（_ref_use_A.py）。
    # 09-21 拍板：技能被照抄进模型写的动作代码，本质上也是"被用了"，
    # 退役记账必须认它——只认直接调用会让退役分子几乎恒为 0，删技能等于随机删。
    # 记账默认 k=10：冻结数据上误报(对照组)从 0.24-0.40 降到 0.16-0.23，净效应不降。
    # 要对齐预注册分析 A 的主口径就传 skill_manager_reference_k=5。
    K_REFERENCE = 10
    _PRIM_LINES_CACHE = None

    @staticmethod
    def _norm_lines(code):
        """规范化成可比对的行：去注释、数字换 N、压空白、丢掉太短的行。"""
        code = re.sub(r"/\*.*?\*/", " ", code or "", flags=re.S)
        out = []
        for line in code.splitlines():
            line = re.sub(r"//.*$", "", line)
            line = re.sub(r"\b\d+(?:\.\d+)?\b", "N", line)
            line = re.sub(r"\s+", " ", line).strip()
            if len(line) >= 12:
                out.append(line)
        return out

    @classmethod
    def _prim_lines(cls):
        """控制原语里的行（样板代码），不能算作参考。只算一次。"""
        if cls._PRIM_LINES_CACHE is not None:
            return cls._PRIM_LINES_CACHE
        lines = set()
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for sub in ("control_primitives", "control_primitives_context"):
            for path in glob.glob(os.path.join(base, sub, "*.js")):
                try:
                    with open(path, encoding="utf-8", errors="replace") as fh:
                        lines.update(cls._norm_lines(fh.read()))
                except OSError:
                    continue
        cls._PRIM_LINES_CACHE = lines
        return lines

    def _referenced_skills(self, code, candidate_names):
        """这一轮模型"照着写"了哪些检索出来的技能。"""
        if not code:
            return set()
        prim = self._prim_lines()
        written = set(ln for ln in self._norm_lines(code) if ln not in prim)
        if not written:
            return set()
        out = set()
        for name in set(candidate_names or []):
            entry = self.skills.get(name)
            if not entry:
                continue
            src = set(ln for ln in self._norm_lines(entry.get("code") or "")
                      if ln not in prim)
            if len(src & written) >= self.K_REFERENCE:
                out.add(name)
        return out

    @staticmethod
    def _called_skills_no_defs(code, candidate_names):
        """同 _called_skills，但先把函数定义处剔掉再匹配。

        剔的是 `function NAME(`、`async function NAME(`、以及
        `const/let/var NAME = ... (` 这几种定义形式。两者之差即自匹配的贡献量。
        """
        if not code:
            return set()
        stripped = re.sub(
            r"\b(?:async\s+)?function\s+\w+\s*\(", "function (", code
        )
        stripped = re.sub(
            r"\b(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?(?:function\s*)?\(",
            "__def__ (",
            stripped,
        )
        called = set()
        for name in set(candidate_names):
            if re.search(r"\b" + re.escape(name) + r"\s*\(", stripped):
                called.add(name)
        return called

    @staticmethod
    def _called_skills_runtime(runtime_calls, candidate_names):
        """从 mineflayer 回传的运行时计数里取本轮真被调用的技能。

        runtime_calls 形如 {"mineIronOre": 2, ...}，计数 >0 即真调用过。
        缺失（老版本 bridge、或本轮崩在 eval 之前）时返回空集。
        """
        if not runtime_calls or not isinstance(runtime_calls, dict):
            return set()
        candidates = set(candidate_names)
        return {
            name
            for name, count in runtime_calls.items()
            if name in candidates and isinstance(count, int) and count > 0
        }

    @staticmethod
    def _exec_sets(runtime_outcomes, retrieved_skills):
        """从本任务 skillOutcomes 抽出 exec_ok / exec_fail / exec_nested。

        runtime_outcomes is None → 探针没回传，exec_source=None，集合为空。
        有列表时：某技能至少一次 ok=true → exec_ok（每任务至多一次）；
        有执行且全部失败 → exec_fail。exec_nested = 执行了但不在 retrieved 里。
        """
        if runtime_outcomes is None:
            return set(), set(), set(), None
        by_name = {}
        for rec in runtime_outcomes:
            if not isinstance(rec, dict):
                continue
            name = rec.get("name")
            if not name or not isinstance(name, str) or name.startswith("__"):
                continue
            by_name.setdefault(name, []).append(bool(rec.get("ok")))
        exec_ok = set()
        exec_fail = set()
        for name, oks in by_name.items():
            if any(oks):
                exec_ok.add(name)
            else:
                exec_fail.add(name)
        retrieved = set(retrieved_skills or [])
        exec_nested = (exec_ok | exec_fail) - retrieved
        return exec_ok, exec_fail, exec_nested, "probe"

    def record_call_probes_only(
        self,
        success: bool,
        retrieved_skills: list,
        solution_code: str = "",
        runtime_calls=None,
        exec_code: str = "",
        runtime_outcomes=None,
        critic_credit=None,
    ):
        """整理关(A/C 臂)时的三路判定落盘。只写 call_probe_compare.jsonl，
        不碰 retrieved_count / success_count / contribution_sum，不参与退役。

        2026-09-10 加：update_skill_usage 只在 curation_enabled 时被调用，
        于是 A 臂上 RUNTIME_CALL_PROBE=1 只把 skillCalls 留在 events 里，
        逐回合的 runtime vs text_nodefs 比对文件一条都不会写。
        """
        if not retrieved_skills and runtime_outcomes is None:
            return
        probes = {
            "text": self._called_skills(solution_code, retrieved_skills),
            "text_nodefs": self._called_skills_no_defs(
                solution_code, retrieved_skills
            ),
            "runtime": self._called_skills_runtime(runtime_calls, retrieved_skills),
        }
        # 参考、以及"调用并上参考"，两者恒记录；只有 call_detect 选中的进入退役规则。
        probes["reference"] = self._referenced_skills(solution_code, retrieved_skills)
        probes["runtime+reference"] = probes["runtime"] | probes["reference"]
        exec_ok, exec_fail, exec_nested, exec_source = self._exec_sets(
            runtime_outcomes, retrieved_skills
        )
        self._log_call_probes(
            probes, retrieved_skills, success, runtime_calls,
            solution_code=solution_code, exec_code=exec_code,
            exec_ok=exec_ok, exec_fail=exec_fail, exec_nested=exec_nested,
            exec_source=exec_source, critic_credit=critic_credit,
        )
        self._dynamic_after_outcomes(runtime_outcomes)

    def _log_call_probes(self, probes, retrieved_skills, success, runtime_calls,
                         solution_code="", exec_code="",
                         exec_ok=None, exec_fail=None, exec_nested=None,
                         exec_source=None, critic_credit=None):
        """把三种判定的结果逐轮落盘，供第零道门比对。只记录，不参与判定。

        2026-09-07 加了两样东西，为定「text_nodefs 为何漏记真实执行」的案：

        1. runtime_not_nodefs —— runtime 命中而 nodefs 没命中的技能。
           实测 B 臂 157 个检索轮里 text 12 / nodefs 3 / runtime 9，
           说明 nodefs 系统性漏记，但成因先后有四条猜测被自己的数据推翻
           （剥函数体、调用在 exec_code、技能互调、条目名错配），全部作废。
           不再猜，直接把当轮文本留下来看。

        2. program_code / exec_code 原文（各截断到 4000 字，避免日志爆掉）。
           判定器实际只吃 solution_code(=program_code)；exec_code 一并存下来
           是为了验证「调用是不是写在 exec_code 里」这个已被否定的假设，
           并给后来者留下自证材料。
        """
        try:
            path = f"{self.ckpt_dir}/metrics/call_probe_compare.jsonl"
            U.f_mkdir(f"{self.ckpt_dir}/metrics")
            runtime_raw = runtime_calls if isinstance(runtime_calls, dict) else {}
            record = {
                # 2026-09-11 审计 02 C-7：原来没有轮次标识，无法与 iterations.jsonl 对齐。
                # cur_iter 是本任务开始前的 recorder.iteration（rollout 起点设置），
                # 与 retrieval_log.jsonl 的 cur_iter 同口径；iterations.jsonl.iter 是
                # 任务结束后的值，两者差 = 本任务的 step 数。
                "iter": getattr(self, "cur_iter", None),
                # 2026-09-11 审计 P0-1：哨兵回传值（=包装的技能数）。None 表示探针
                # 这一轮压根没跑到（链路断），0/正整数表示注入执行了。
                "probe_installed": runtime_raw.get("__probe_installed__"),
                "success": bool(success),
                "retrieved": sorted(set(retrieved_skills)),
                "text": sorted(probes["text"]),
                "text_nodefs": sorted(probes["text_nodefs"]),
                "runtime": sorted(probes["runtime"]),
                "runtime_raw": runtime_calls if isinstance(runtime_calls, dict) else {},
                "selected": self.call_detect,
                # 只在 text 命中、runtime 没命中的：自匹配或"文本有调用但没执行到"
                "text_only": sorted(probes["text"] - probes["runtime"]),
                # 只在 runtime 命中、text 没命中的：文本探针漏记（如间接调用）
                "runtime_only": sorted(probes["runtime"] - probes["text"]),
                # 🔴 定案用：runtime 命中而 nodefs 没命中 —— nodefs 漏记的那部分
                "runtime_not_nodefs": sorted(
                    probes["runtime"] - probes["text_nodefs"]
                ),
                # 反向：nodefs 说调用了但运行时没执行到
                "nodefs_not_runtime": sorted(
                    probes["text_nodefs"] - probes["runtime"]
                ),
                # 当轮原文（截断）。判定器实际只吃 program_code。
                "program_code": (solution_code or "")[:4000],
                "exec_code": (exec_code or "")[:4000],
                "program_code_len": len(solution_code or ""),
                "exec_code_len": len(exec_code or ""),
                "exec_ok": sorted(exec_ok or []),
                "exec_fail": sorted(exec_fail or []),
                "exec_nested": sorted(exec_nested or []),
                "exec_source": exec_source,
                "critic_credit": (
                    critic_credit if isinstance(critic_credit, dict) else {}
                ),
            }
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            # 记录失败绝不影响主链路
            pass

    def _archive_skill(self, name, reason):
        if name not in self.skills:
            return
        if getattr(self, "freeze_library", False):
            self._frozen_skip("archive", skill=name, reason=reason)
            return
        entry = self.skills[name]
        self.vectordb._collection.delete(ids=[name])

        archive_code_dir = f"{self.ckpt_dir}/skill/archive/code"
        archive_description_dir = f"{self.ckpt_dir}/skill/archive/description"
        U.f_mkdir(archive_code_dir)
        U.f_mkdir(archive_description_dir)
        src_code = f"{self.ckpt_dir}/skill/code/{name}.js"
        dst_code = f"{archive_code_dir}/{name}.js"
        src_description = f"{self.ckpt_dir}/skill/description/{name}.txt"
        dst_description = f"{archive_description_dir}/{name}.txt"
        if os.path.exists(src_code):
            os.replace(src_code, dst_code)
        if os.path.exists(src_description):
            os.replace(src_description, dst_description)

        archived_path = f"{self.ckpt_dir}/skill/skills_archived.json"
        archived = U.load_json(archived_path) if os.path.exists(archived_path) else {}
        archived[name] = {
            **entry,
            "reason": reason,
            "cur_iter": getattr(self, "_archive_cur_iter", None),
        }
        U.dump_json(archived, archived_path)

        del self.skills[name]
        U.dump_json(self.skills, f"{self.ckpt_dir}/skill/skills.json")
        self.vectordb.persist()
        assert self.vectordb._collection.count() == len(
            self.skills
        ), "vectordb is not synced with skills.json"

    def _frozen_skip(self, op, **detail):
        """库冻结时被拦下的改库操作：只打日志（stdout + skill/frozen_ops.jsonl），不改库。"""
        print(f"\033[35m[freeze] {op} skipped (library frozen): {detail}\033[0m", flush=True)
        try:
            row = {"time": int(time.time()), "cur_iter": getattr(self, "cur_iter", None),
                   "op": op, **detail}
            with open(f"{self.ckpt_dir}/skill/frozen_ops.jsonl", "a", encoding="utf-8") as fp:
                fp.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
        except Exception as exc:
            print(f"\033[33m[freeze] log skipped (non-fatal): {exc}\033[0m")

    def _curation_report_path(self):
        return f"{self.ckpt_dir}/skill/curation_report.jsonl"

    def _retrieval_log_path(self):
        return f"{self.ckpt_dir}/skill/retrieval_log.jsonl"

    def _record_retrieval(self, query, docs_and_scores):
        try:
            candidates = []
            for doc, distance in docs_and_scores:
                name = doc.metadata["name"]
                row = {
                    "name": name,
                    "distance": float(distance),
                }
                rerank_scores = self._last_rerank_scores.get(name)
                if rerank_scores:
                    row.update(rerank_scores)
                candidates.append(row)
            event = {
                "time": int(time.time()),
                "cur_iter": self.cur_iter,
                "curation_enabled": self.curation_enabled,
                "library_size_N": self.vectordb._collection.count(),
                "query": str(query)[:500],
                "returned": self.last_retrieved_skill_names,
                "candidates": candidates,
            }
            # G 臂（结构化检索）的机制量。原先 _filter_by_action_class 把它算出来
            # 存进 self._last_structured_stat 就丢了——全仓库没有一处读它，
            # 于是事后无法从数据里证明按类别过滤生效过，更看不到回落率。
            # 回落率恰恰是这条臂最要紧的混杂项：同类不足 k 个时会用原候选补齐，
            # 补得太多就等于没过滤，测出来的不是分类的效果。
            event["structured_retrieval"] = bool(self.structured_retrieval)
            if self.structured_retrieval:
                event["structured_stat"] = getattr(
                    self, "_last_structured_stat", None
                )
            if getattr(self, "governance", "static") == "dynamic":
                event["governance"] = "dynamic"
                event["capacity"] = self.governance_capacity
            U.f_mkdir(f"{self.ckpt_dir}/skill")
            with open(self._retrieval_log_path(), "a", encoding="utf-8") as fp:
                fp.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        except Exception as exc:
            # Logging must never crash a (possibly multi-hour) rollout.
            print(f"\033[33m[retrieval-log] skipped (non-fatal): {exc}\033[0m")

    def _governance_state_path(self):
        return f"{self.ckpt_dir}/skill/governance_state.json"

    def _save_governor_state(self):
        if getattr(self, "governor", None) is None:
            return
        if getattr(self, "freeze_library", False):
            return  # 冻结：治理状态属于库，不落盘
        U.f_mkdir(f"{self.ckpt_dir}/skill")
        U.dump_json(self.governor.snapshot(), self._governance_state_path())

    def _dynamic_after_outcomes(self, runtime_outcomes):
        if getattr(self, "governance", "static") != "dynamic" or self.governor is None:
            return
        task_idx = self.cur_iter if self.cur_iter is not None else 0
        outcomes = runtime_outcomes if isinstance(runtime_outcomes, list) else []
        guard = bool(getattr(self, "noeffect_guard", False))
        if guard:
            from voyager.governance.noeffect import mark_noeffect_idle

            outcomes = mark_noeffect_idle(outcomes, self.skills)
        self.governor.record_outcomes(task_idx, outcomes, noeffect_guard=guard)
        self._save_governor_state()
        # v30：lru_evict=False 时只记账不移出；默认 True 保持现状。
        evicted = []
        if self.lru_evict:
            evicted = self._apply_lru_evictions(task_idx)
        self._write_governance_trace(task_idx, evicted)

    def _governance_trace_path(self):
        return f"{self.ckpt_dir}/skill/governance_trace.jsonl"

    def _write_governance_trace(self, task_idx, evicted):
        if getattr(self, "governance", "static") != "dynamic" or self.governor is None:
            return
        try:
            hist = self.governor.recency_histogram(task_idx)
            hist_out = {str(k): v for k, v in hist.items()}
            evicted_out = []
            for rec in evicted or []:
                if isinstance(rec, dict) and rec.get("name"):
                    evicted_out.append(
                        {
                            "name": rec.get("name"),
                            "last_exec_task": rec.get("last_exec_task"),
                            "exec_count": rec.get("exec_count"),
                            "task_idx": rec.get("task_idx", task_idx),
                        }
                    )
                elif isinstance(rec, str):
                    evicted_out.append(
                        {
                            "name": rec,
                            "last_exec_task": None,
                            "exec_count": None,
                            "task_idx": task_idx,
                        }
                    )
            row = {
                "task_idx": task_idx,
                "library_size": len(self.skills),
                "working_set_w5": self.governor.working_set(task_idx, 5),
                "working_set_w15": self.governor.working_set(task_idx, 15),
                "working_set_w30": self.governor.working_set(task_idx, 30),
                "never_executed": int(hist.get("never") or 0),
                "recency_histogram": hist_out,
                "evicted": evicted_out,
            }
            U.f_mkdir(f"{self.ckpt_dir}/skill")
            with open(self._governance_trace_path(), "a", encoding="utf-8") as fp:
                fp.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        except Exception as exc:
            print(f"\033[33m[governance] trace skipped (non-fatal): {exc}\033[0m")

    def _apply_lru_evictions(self, task_idx):
        if getattr(self, "governor", None) is None:
            return []
        if not getattr(self, "lru_evict", True):
            return []
        if getattr(self, "freeze_library", False):
            self._frozen_skip("lru_evict", task_idx=task_idx)
            return []
        evicted = self.governor.evict_if_over(task_idx)
        if not evicted:
            return []
        self._archive_cur_iter = task_idx
        for rec in evicted:
            name = rec.get("name")
            if not name:
                continue
            self._archive_skill(name, "lru_capacity")
            try:
                self._record_curation_event(
                    {
                        "action": "evicted",
                        "skill": name,
                        "reason": rec.get("reason", "lru_capacity"),
                        "score": rec.get("score"),
                        "exec_count": rec.get("exec_count"),
                        "capacity": self.governance_capacity,
                    }
                )
            except Exception as exc:
                print(f"\033[33m[governance] evict log skipped (non-fatal): {exc}\033[0m")
        self._save_governor_state()
        print(
            f"\033[35m[governance] evicted {len(evicted)}: "
            f"{[rec.get('name') for rec in evicted]}\033[0m"
        )
        return evicted

    def retire_named_action(self, name):
        """v30 退役：从点名名单移除，并加入检索黑名单。开关关时不会被调用。

        函数定义留在 control_primitives 里，已经学到的技能仍可调用，
        避免 ReferenceError。只取消点名（提示词名单）和检索隔离。
        """
        if not name:
            return
        self.retrieval_blacklist.add(name)
        kept = [n for n in (self.extra_base_skills or []) if n != name]
        if kept != list(self.extra_base_skills or []):
            self.extra_base_skills = kept

    def _record_curation_event(self, event):
        event = {
            "time": int(time.time()),
            "cur_iter": getattr(self, "_archive_cur_iter", None),
            **event,
        }
        U.f_mkdir(f"{self.ckpt_dir}/skill")
        with open(self._curation_report_path(), "a", encoding="utf-8") as fp:
            fp.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")

    def _intent_group_key(self, entry):
        # 无库存产物/弱指纹技能的兜底分组键:用意图 (动作, 目标物集合)。
        # 动作或目标物缺失、或意图冲突 → 不参与意图分组(返回 None)。
        if str(entry.get("intent_consistency", "unknown")).lower() == "conflict":
            return None
        action = target_action_for_dedup(entry)
        if not action:
            return None
        targets = []
        for intent_key in ("skill_intent", "task_intent"):
            intent = entry.get(intent_key) or {}
            targets += list(intent.get("target_items", []))
            targets += list(intent.get("target_entities", []))
        targets = sorted({t for t in (str(x).strip().lower() for x in targets) if t})
        if not targets:
            return None
        return ("intent", action, tuple(targets))

    def _dedup_skip_reason(self, name, entry):
        return dedup_key_skip_reason(entry, "effect")

    def _dedup_report_entry(self, name, entry, reason=None):
        return {
            "skill": name,
            "reason": reason,
            "target_action": target_action_for_dedup(entry),
            "fingerprint": entry.get("fingerprint", []),
            "fingerprint_confidence": entry.get("fingerprint_confidence", "unknown"),
            "intent_consistency": entry.get("intent_consistency", "unknown"),
            "skill_intent": entry.get("skill_intent", {}),
            "task_intent": entry.get("task_intent", {}),
        }

    def retire(self, cur_iter):
        if getattr(self, "freeze_library", False):
            self._frozen_skip("retire", cur_iter=cur_iter)
            return []
        if getattr(self, "governance", "static") == "dynamic":
            return []
        retired = []
        self._archive_cur_iter = cur_iter
        n_library = len(self.skills)
        n_evaluated = 0
        n_protected_skipped = 0
        n_substitute_protected = 0
        n_undecidable = 0
        # getattr 默认值保证旧的 __new__ 风格测试管理器（未设新属性）仍按默认开关跑
        contribution_mode = getattr(self, "contribution_mode", "binary")
        substitute_guard = getattr(self, "substitute_guard", "off")
        substitute_undecided = getattr(self, "substitute_undecided", "allow_retire")

        def record_retire_event(event):
            try:
                self._record_curation_event(event)
            except Exception as exc:
                # Logging must never crash a (possibly multi-hour) rollout.
                print(f"\033[33m[retire-log] skipped (non-fatal): {exc}\033[0m")

        # P0-1 B2：替代品判断必须基于 retire() 开始时的库快照（effect_key）。
        # 若边归档边用活动库判断，循环里先归档 A 会让与 A 同指纹的 B 突然变"无替代"。
        substitute_keys = {
            name: equivalence_key(entry, "effect")
            for name, entry in self.skills.items()
        }

        for name, entry in list(self.skills.items()):
            # Adapted from collaborator curation.js LIFE_SAFETY_VERBS guard
            # (L87/L375); protected names are caller supplied in this repo.
            if (
                getattr(self, "protect_critical_skills", False)
                and name in getattr(self, "protected_skills", set())
            ):
                n_protected_skipped += 1
                continue
            n_evaluated += 1
            retrieved_count = entry.get("retrieved_count", 0)
            success_count = entry.get("success_count", 0)
            contribution_sum = entry.get("contribution_sum", 0)
            exec_ok_count = entry.get("exec_ok_count", 0)
            # P0-1 B1：只改分子，不改分母。binary → success_count（旧行为）；
            # effect_delta → contribution_sum；exec → exec_ok_count。
            # 分母仍是 retrieved_count，平滑形式仍是 (x+1)/(retrieved_count+2)。
            # 🔴 分母绝不能换成调用回合数：从未被调用的技能分母为 0，
            # 平滑后有用度反而最高，退役永不触发。
            if contribution_mode == "effect_delta":
                numerator = contribution_sum
            elif contribution_mode == "exec":
                numerator = exec_ok_count
            else:
                numerator = success_count
            usefulness = (numerator + 1) / (retrieved_count + 2)
            # A 组门槛：曝光足够 且 有用度低于阈值（与旧逻辑同形，仅默认 n_min 变化）
            if not (
                retrieved_count >= self.retire_min_exposure
                and usefulness < self.retire_use_threshold
            ):
                continue
            # P0-1 B2：独立门槛，不与贡献相乘（贡献恰为 0 的前置技能也能因无替代而保住）。
            # 方向：库里没有功能相近替代 → 保护，不移出；有替代 → 允许按有用度移出；
            # 指纹判不了（effect_key 为 None）→ 默认 fail-open（allow_retire：视为有替代，放行移出）；
            # 若显式设为 protect，则判不了时按「无替代」保护。
            if substitute_guard == "fp":
                my_key = substitute_keys.get(name)
                if my_key is None:
                    n_undecidable += 1
                    has_substitute = substitute_undecided == "allow_retire"
                else:
                    has_substitute = any(
                        other_key == my_key
                        for other_name, other_key in substitute_keys.items()
                        if other_name != name and other_key is not None
                    )
                if not has_substitute:
                    n_substitute_protected += 1
                    continue
            reason = "low_use"
            self._archive_skill(name, reason)
            event = {
                "action": "retired",
                "skill": name,
                "reason": reason,
                "retrieved_count": retrieved_count,
                "success_count": success_count,
                "usefulness": round(usefulness, 4),
                "retire_min_exposure": self.retire_min_exposure,
                "retire_use_threshold": self.retire_use_threshold,
                "contribution_mode": contribution_mode,
                "substitute_guard": substitute_guard,
                "substitute_undecided": substitute_undecided,
            }
            if contribution_mode == "effect_delta":
                event["contribution_sum"] = contribution_sum
            if contribution_mode == "exec":
                event["exec_ok_count"] = exec_ok_count
            record_retire_event(event)
            retired.append((name, reason))
        record_retire_event(
            {
                "action": "retire_check",
                "n_library": n_library,
                "n_evaluated": n_evaluated,
                "n_retired": len(retired),
                "n_protected_skipped": n_protected_skipped,
                "contribution_mode": contribution_mode,
                "substitute_guard": substitute_guard,
                "substitute_undecided": substitute_undecided,
                "n_substitute_protected": n_substitute_protected,
                "n_undecidable": n_undecidable,
            }
        )
        return retired

    def deduplicate(self):
        if getattr(self, "freeze_library", False):
            self._frozen_skip("deduplicate")
            return []
        dedup_mode = getattr(self, "dedup_mode", "effect")
        if dedup_mode == "none":
            return []

        groups = {}
        intent_groups = {}
        skipped = []
        if dedup_mode == "semantic":
            representatives = []
            semantic_groups = []
            for name, entry in list(self.skills.items()):
                keyed_entry = {**entry, "name": name}
                matched = False
                for idx, representative in enumerate(representatives):
                    if are_equivalent(
                        keyed_entry, representative, "semantic", threshold=0.5
                    ):
                        semantic_groups[idx].append((name, entry))
                        matched = True
                        break
                if not matched:
                    representatives.append(keyed_entry)
                    semantic_groups.append([(name, entry)])
            groups = {idx: group for idx, group in enumerate(semantic_groups)}
        else:
            for name, entry in list(self.skills.items()):
                if dedup_mode == "effect":
                    skip_reason = self._dedup_skip_reason(name, entry)
                    if skip_reason:
                        intent_key = None
                        if getattr(self, "intent_fallback_dedup", True):
                            intent_key = self._intent_group_key(entry)
                        if intent_key is not None:
                            intent_groups.setdefault(intent_key, []).append(
                                (name, entry)
                            )
                        else:
                            skipped.append(
                                self._dedup_report_entry(name, entry, skip_reason)
                            )
                        continue
                key = equivalence_key({**entry, "name": name}, dedup_mode)
                if key is None:
                    continue
                groups.setdefault(key, []).append((name, entry))

        if skipped:
            self._record_curation_event(
                {
                    "action": "dedup_skipped",
                    "count": len(skipped),
                    "skills": skipped,
                }
            )

        merged = []
        # 路径一:行为指纹分组(原逻辑,保持不变)
        if dedup_mode == "effect":
            for (target_action, fingerprint_key), group in groups.items():
                if len(group) <= 1:
                    continue
                keep_name, _ = max(
                    group,
                    key=lambda item: (
                        (item[1].get("success_count", 0) + 1)
                        / (item[1].get("retrieved_count", 0) + 2),
                        -len(item[1].get("code", "")),
                    ),
                )
                for dup_name, dup_entry in group:
                    if dup_name == keep_name or dup_name not in self.skills:
                        continue
                    keep_entry = self.skills[keep_name]
                    self.skills[keep_name]["retrieved_count"] = (
                        self.skills[keep_name].get("retrieved_count", 0)
                        + dup_entry.get("retrieved_count", 0)
                    )
                    self.skills[keep_name]["success_count"] = (
                        self.skills[keep_name].get("success_count", 0)
                        + dup_entry.get("success_count", 0)
                    )
                    # P0-1 B1：合并重复技能时 contribution_sum 与 success_count 同样相加
                    self.skills[keep_name]["contribution_sum"] = (
                        self.skills[keep_name].get("contribution_sum", 0)
                        + dup_entry.get("contribution_sum", 0)
                    )
                    self._archive_skill(dup_name, reason=f"dup_of:{keep_name}")
                    merged.append((dup_name, keep_name))
                    self._record_curation_event(
                        {
                            "action": "dedup_archived",
                            "skill": self._dedup_report_entry(dup_name, dup_entry),
                            "kept": self._dedup_report_entry(keep_name, keep_entry),
                            "target_action": target_action,
                            "fingerprint": list(fingerprint_key),
                            "reason": f"dup_of:{keep_name}",
                        }
                    )
        else:
            for group_key, group in groups.items():
                if len(group) <= 1:
                    continue
                keep_name, _ = max(
                    group,
                    key=lambda item: (
                        (item[1].get("success_count", 0) + 1)
                        / (item[1].get("retrieved_count", 0) + 2),
                        -len(item[1].get("code", "")),
                    ),
                )
                for dup_name, dup_entry in group:
                    if dup_name == keep_name or dup_name not in self.skills:
                        continue
                    keep_entry = self.skills[keep_name]
                    self.skills[keep_name]["retrieved_count"] = (
                        self.skills[keep_name].get("retrieved_count", 0)
                        + dup_entry.get("retrieved_count", 0)
                    )
                    self.skills[keep_name]["success_count"] = (
                        self.skills[keep_name].get("success_count", 0)
                        + dup_entry.get("success_count", 0)
                    )
                    # P0-1 B1：合并重复技能时 contribution_sum 与 success_count 同样相加
                    self.skills[keep_name]["contribution_sum"] = (
                        self.skills[keep_name].get("contribution_sum", 0)
                        + dup_entry.get("contribution_sum", 0)
                    )
                    self._archive_skill(dup_name, reason=f"dup_of:{keep_name}")
                    merged.append((dup_name, keep_name))
                    event = {
                        "action": "dedup_archived",
                        "dedup_kind": dedup_mode,
                        "skill": self._dedup_report_entry(dup_name, dup_entry),
                        "kept": self._dedup_report_entry(keep_name, keep_entry),
                        "reason": f"dup_of:{keep_name}",
                    }
                    if dedup_mode == "syntactic":
                        event["code_signature"] = str(group_key)[:500]
                    self._record_curation_event(event)

        # 路径二:意图分组(新增,覆盖无库存产物/弱指纹技能如放箱子/导航)
        if dedup_mode == "effect" and getattr(self, "intent_fallback_dedup", True):
            for (_, action, targets), group in intent_groups.items():
                if len(group) <= 1:
                    continue
                keep_name, _ = max(
                    group,
                    key=lambda item: (
                        (item[1].get("success_count", 0) + 1)
                        / (item[1].get("retrieved_count", 0) + 2),
                        -len(item[1].get("code", "")),
                    ),
                )
                for dup_name, dup_entry in group:
                    if dup_name == keep_name or dup_name not in self.skills:
                        continue
                    keep_entry = self.skills[keep_name]
                    self.skills[keep_name]["retrieved_count"] = (
                        self.skills[keep_name].get("retrieved_count", 0)
                        + dup_entry.get("retrieved_count", 0)
                    )
                    self.skills[keep_name]["success_count"] = (
                        self.skills[keep_name].get("success_count", 0)
                        + dup_entry.get("success_count", 0)
                    )
                    # P0-1 B1：合并重复技能时 contribution_sum 与 success_count 同样相加
                    self.skills[keep_name]["contribution_sum"] = (
                        self.skills[keep_name].get("contribution_sum", 0)
                        + dup_entry.get("contribution_sum", 0)
                    )
                    self._archive_skill(dup_name, reason=f"dup_of:{keep_name}")
                    merged.append((dup_name, keep_name))
                    self._record_curation_event(
                        {
                            "action": "dedup_archived",
                            "dedup_kind": "intent",
                            "skill": self._dedup_report_entry(dup_name, dup_entry),
                            "kept": self._dedup_report_entry(keep_name, keep_entry),
                            "target_action": action,
                            "intent_targets": list(targets),
                            "reason": f"dup_of:{keep_name}",
                        }
                    )
        if (
            getattr(self, "governance", "static") == "dynamic"
            and self.governor is not None
        ):
            for dup_name, _keep_name in merged:
                self.governor.remove(dup_name)
            if merged:
                self._save_governor_state()
        return merged
