import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

# 2026-08-24：默认**不启用**保护名单。
# 理由不是"忘了配"，是研究立场：需要一张人肉白名单来阻止自动判据删掉最基础的技能，
# 本身就说明判据不成立。名单是补丁，掩盖的正是本课题要研究的现象
# （被检索≠被调用，导致通用技能的有用度被系统性低估）。
# 保留机制与这五个候选名，供需要时显式开启：
#   --protected-skills craftCraftingTable,craftWoodenPickaxe,craftStonePickaxe,mineIronOre,craftFurnace
CANDIDATE_PROTECTED_SKILLS = (
    "craftCraftingTable",
    "craftWoodenPickaxe",
    "craftStonePickaxe",
    "mineIronOre",
    "craftFurnace",
)


# MiniLM 已在本机缓存。导入 voyager 时会立刻加载它；
# 不设离线开关，新版 huggingface_hub 仍会去网上列仓库文件，墙了就 ConnectTimeout。
# 与 run_batch.py / run_overnight_smoke.sh 同一组变量。
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_DATASETS_OFFLINE", "1")
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")


class Tee:
    def __init__(self, stream, log_file):
        self.stream = stream
        self.log_file = log_file

    def write(self, data):
        self.stream.write(data)
        self.log_file.write(data)

    def flush(self):
        self.stream.flush()
        self.log_file.flush()

    def isatty(self):
        return self.stream.isatty()

    def fileno(self):
        return self.stream.fileno()


log_dir = Path.home() / "Voyager" / "logs"
log_dir.mkdir(parents=True, exist_ok=True)
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
log_file = open(log_dir / f"voyager_{timestamp}.log", "a", encoding="utf-8")

sys.stdout = Tee(sys.stdout, log_file)
sys.stderr = Tee(sys.stderr, log_file)


def parse_protected_skills(value):
    names = [name.strip() for name in value.split(",") if name.strip()]
    if not names:
        return []
    return names


def parse_args():
    parser = argparse.ArgumentParser(
        description="Launch a Voyager formal experiment arm/trial."
    )
    parser.add_argument("--arm", required=True, choices=["A", "B", "C", "D"])
    # 2026-09-11：第三方托管的 DeepSeek 旧版本（V4 Flash 0731 官方已下线，OpenRouter/硅基流动仍有）。
    # 三个参数默认值 = 改动前行为（官方地址、按臂推模型名、DEEPSEEK_API_KEY）。
    parser.add_argument("--api-base", default="https://api.deepseek.com/v1",
                        help="OpenAI 兼容接口地址，如 https://api.siliconflow.cn/v1 或 https://openrouter.ai/api/v1")
    parser.add_argument("--model-name", default=None,
                        help="覆盖按臂推出的模型名，如 deepseek-ai/DeepSeek-V4-Flash；不传则 deepseek-v4-{flash,pro}")
    parser.add_argument("--api-key-env", default="DEEPSEEK_API_KEY",
                        help="读哪个环境变量当 API key")
    parser.add_argument("--provider-order", default="",
                        help="F16：OpenRouter 钉供应商，逗号分隔（如 DeepInfra,Novita）；"
                             "非空时 allow_fallbacks=false。官方 API 忽略。")
    # trial 只进 ckpt 目录名和 run_meta，不参与世界/种子选择（种子由 prep_one.sh 给）。
    # 原来卡死在 5，09-06 补第 6 个世界时 A:t6 起跑即死。放宽到 1..24。
    parser.add_argument("--trial", required=True, type=int, choices=list(range(1, 25)))
    parser.add_argument("--mc-port", required=True, type=int)
    parser.add_argument("--server-port", type=int, default=3000)
    parser.add_argument("--max-iterations", type=int, default=300)
    parser.add_argument("--env-request-timeout", type=int, default=300)
    parser.add_argument("--env-dead-after", type=int, default=5)
    parser.add_argument("--resume", action="store_true", default=False)
    parser.add_argument("--curation-mode", choices=["baseline", "context"], default=None)
    parser.add_argument(
        "--dedup-mode",
        choices=["effect", "semantic", "syntactic", "none"],
        default=None,
    )
    parser.add_argument("--retrieval-gate", action="store_true", default=False)
    parser.add_argument("--protect-critical", action="store_true", default=False)
    # P0-1 退役判据（2026-08-19 冻结值，见 reports/P0-1_退役判据规格草案_2026-08-19.md §7.3）
    parser.add_argument("--retire-min-exposure", type=int, default=12)
    parser.add_argument("--retire-use-threshold", type=float, default=0.15)
    parser.add_argument("--curation-interval", type=int, default=15)
    parser.add_argument(
        "--contribution-mode",
        choices=["binary", "effect_delta", "exec"],
        default="binary",
    )
    parser.add_argument(
        "--structured-retrieval",
        action="store_true",
        default=False,
        help=(
            "G 臂（甲）：检索时先按动作类别硬过滤候选，再走原有重排。"
            "同类不足 k 个时用原候选补齐。关闭时与改动前逐字节等价。"
        ),
    )
    parser.add_argument(
        "--call-detect",
        choices=["text", "text_nodefs", "runtime"],
        default="runtime",
        help=(
            "有用度分子里\"被调用\"怎么判：text=现行整程序文本探针（含自匹配缺陷，"
            "重写轮会把定义行算成调用）；text_nodefs=同上但剔除函数定义处；"
            "runtime=mineflayer 侧运行时真调用计数。三种始终并行记录到 "
            "metrics/call_probe_compare.jsonl，本开关只决定哪一种进入退役规则。"
        ),
    )
    parser.add_argument(
        "--substitute-guard",
        choices=["off", "fp"],
        default="off",
    )
    parser.add_argument(
        "--substitute-undecided",
        choices=["allow_retire", "protect"],
        default="allow_retire",
        help="B2 指纹判不了时的默认：allow_retire=放行移出（默认），protect=保护",
    )
    parser.add_argument(
        "--protected-skills",
        type=parse_protected_skills,
        default=[],
        help="退役保护名单（逗号分隔）。默认空——见文件顶部说明。候选见 CANDIDATE_PROTECTED_SKILLS",
    )
    parser.add_argument(
        "--ckpt-suffix",
        default="",
        help="附加到 ckpt 目录名末尾，用于隔离 smoke/验证跑，避免覆盖正式数据",
    )
    parser.add_argument(
        "--thinking",
        choices=["disabled", "enabled"],
        default="disabled",
    )
    parser.add_argument(
        "--skill-api-prompt",
        action="store_true",
        default=False,
        help=(
            "把检索到的技能在 action prompt 中单列为显式可调用的命名 API"
            "（呈现方式受控实验开关）。关闭时 prompt 与改动前逐字节相同。"
        ),
    )
    parser.add_argument(
        "--skill-params",
        action="store_true",
        default=False,
        help=(
            "技能主函数除 bot 外可带默认值参数，回复须含 Invocation 调用行。"
            "关闭时提示词、解析规则与改动前逐字节相同。"
        ),
    )
    parser.add_argument(
        "--governance",
        choices=["static", "dynamic"],
        default="static",
        help="static=现有检索+阈值退役；dynamic=全库 LRU 容量淘汰。",
    )
    parser.add_argument(
        "--governance-capacity",
        type=int,
        default=None,
        help="dynamic 时的技能库容量；0 或省略则不淘汰。",
    )
    parser.add_argument(
        "--chain",
        action="store_true",
        default=False,
        help=(
            "链式执行：建链 → 全库填空 → 缺口生成 → 一次 env.step。"
            "关闭时主循环与改动前一致。"
        ),
    )
    parser.add_argument(
        "--planner-v2",
        action="store_true",
        default=False,
        help=(
            "链式规划器 v2：原版观察、技能说明书、先想后做+看代码复核。"
            "关闭时规划器与改动前逐字节相同。"
        ),
    )
    parser.add_argument(
        "--no-stall-abort",
        action="store_true",
        default=False,
        help="关掉课程'背包连续满 20 轮就结束整轮'的护栏（保留逃逸）；不加时与改动前逐字节相同。",
    )
    parser.add_argument(
        "--retrieval-top-k",
        type=int,
        default=5,
        help="每轮检索展示给模型的技能条数（原版默认 5）。0 = 空检索对照：技能照常入库，但从不展示给模型。",
    )
    parser.add_argument(
        "--gap-reference",
        action="store_true",
        default=False,
        help="v28：链式臂写缺口代码时附上与原版同口径检索的技能源码及上一轮代码/评语；不加时与 v26 逐字节相同。",
    )
    parser.add_argument(
        "--chain-time-budget",
        type=int,
        default=0,
        help="v28：链式执行在节点之间检查已用秒数，超过即抛错结束本步（不等请求超时、不触发硬重置）；0 = 关。",
    )
    parser.add_argument(
        "--chain-node-check",
        action="store_true",
        default=False,
        help="v29：链式执行给每个节点套 try/catch，只有抛错才抛带节点序号的错误并停链（写进 chain_log 的 node_fail）；"
             "库技能正常返回却没改背包（无效果）只记录进 chain_log 的 node_noeffect、不停链。不加时与 v28 逐字节相同。",
    )
    parser.add_argument(
        "--chain-node-repair",
        action="store_true",
        default=False,
        help="v29：本任务内当场返修。返修目标：节点报错 → 报错的节点；本步无报错却判失败 → 第一个非放置类的无效果"
             "库技能节点（疑似原因）；都没有 → 不动（不禁用、评语与关开关时相同）；判成功的步不动。目标是库技能则本任务禁用"
             "（目录去掉、执行环境挂替身），是新写的缺口代码则把失败代码带给下一次写缺口；指名节点的评语给规划器与缺口生成。"
             "须同时开 --chain-node-check。不加时与 v28 逐字节相同。",
    )
    parser.add_argument(
        "--pause-verify",
        action="store_true",
        default=False,
        help="执行前核实服务器真的在走（/pause 是切换式，自记状态可能与实际相反）；不加时与改动前逐字节相同。",
    )
    parser.add_argument(
        "--noeffect-guard",
        action="store_true",
        default=False,
        help=(
            "空转防护：组件②只记有效执行；v2 规划器同任务空转 2 次即禁用。"
            "关闭时与改动前逐字节相同。"
        ),
    )
    parser.add_argument(
        "--no-intent-fallback-dedup",
        dest="intent_fallback_dedup",
        action="store_false",
        default=True,
    )
    parser.add_argument(
        "--extra-base-skills",
        nargs="+",
        default=None,
        help="09-23：额外注入基础层并点名的技能（voyager/control_primitives_extra/ 下的文件名）。"
        "不传＝默认行为，与改动前逐字节相同。",
    )
    from voyager.v30_flags import add_v30_arguments

    add_v30_arguments(parser)
    args = parser.parse_args()
    if args.chain_node_repair and not args.chain_node_check:
        parser.error("--chain-node-repair requires --chain-node-check")
    return args


def derive_experiment_config(arm, trial, ckpt_suffix=""):
    model_short = "flash" if arm in {"A", "B"} else "pro"
    model_name = f"deepseek-v4-{model_short}"
    curation_enabled = arm in {"B", "D"}
    gov = "on" if curation_enabled else "off"
    ckpt_dir = f"ckpt_{arm}_{model_short}_{gov}_t{trial}{ckpt_suffix}"
    return model_name, curation_enabled, ckpt_dir


args = parse_args()
MODEL_NAME, curation_enabled, ckpt_dir = derive_experiment_config(
    args.arm, args.trial, args.ckpt_suffix
)
if args.model_name:
    MODEL_NAME = args.model_name
max_iterations = args.max_iterations

api_key = os.getenv(args.api_key_env)
if not api_key:
    raise RuntimeError("请先 export DEEPSEEK_API_KEY=sk-...")

print(
    "启动配置: "
    f"arm={args.arm}, "
    f"model={MODEL_NAME}, "
    f"curation_enabled={curation_enabled}, "
    f"ckpt_dir={ckpt_dir}, "
    f"mc_port={args.mc_port}, "
    f"server_port={args.server_port}, "
    f"max_iterations={max_iterations}, "
    f"env_request_timeout={args.env_request_timeout}, "
    f"env_dead_after={args.env_dead_after}, "
    f"resume={args.resume}",
    flush=True,
)

from voyager import Voyager
from voyager.v30_flags import merge_named_actions, v30_voyager_kwargs

_v30_kw = v30_voyager_kwargs(args)
_extra_base_skills = merge_named_actions(args.extra_base_skills, args.v30_named_actions)

voyager = Voyager(
    mc_port=args.mc_port,
    server_port=args.server_port,
    ckpt_dir=ckpt_dir,
    openai_api_key=api_key,
    openai_api_base=args.api_base,
    openrouter_provider_order=[p.strip() for p in args.provider_order.split(",") if p.strip()],
    action_agent_model_name=MODEL_NAME,
    curriculum_agent_model_name=MODEL_NAME,
    curriculum_agent_qa_model_name=MODEL_NAME,
    critic_agent_model_name=MODEL_NAME,
    skill_manager_model_name=MODEL_NAME,
    max_iterations=max_iterations,
    env_request_timeout=args.env_request_timeout,
    env_dead_after=args.env_dead_after,
    skill_manager_curation_enabled=curation_enabled,
    skill_manager_retire_min_exposure=args.retire_min_exposure,
    skill_manager_retire_use_threshold=args.retire_use_threshold,
    skill_manager_curation_interval=args.curation_interval,
    skill_manager_contribution_mode=args.contribution_mode,
    skill_manager_call_detect=args.call_detect,
    skill_manager_structured_retrieval=args.structured_retrieval,
    skill_manager_substitute_guard=args.substitute_guard,
    skill_manager_substitute_undecided=args.substitute_undecided,
    skill_manager_curation_mode=args.curation_mode,
    skill_manager_dedup_mode=args.dedup_mode,
    skill_manager_intent_fallback_dedup=args.intent_fallback_dedup,
    skill_manager_retrieval_gate_enabled=args.retrieval_gate,
    skill_manager_retrieval_top_k=args.retrieval_top_k,
    skill_manager_protect_critical_skills=args.protect_critical,
    skill_manager_protected_skills=args.protected_skills,
    resume=args.resume,
    llm_thinking=args.thinking,
    skill_api_prompt=args.skill_api_prompt,
    skill_params=args.skill_params,
    skill_manager_governance=args.governance,
    skill_manager_governance_capacity=args.governance_capacity,
    chain=args.chain,
    planner_v2=args.planner_v2,
    noeffect_guard=args.noeffect_guard,
    stall_abort=not args.no_stall_abort,
    pause_verify=args.pause_verify,
    gap_reference=args.gap_reference,
    chain_time_budget_s=args.chain_time_budget,
    chain_node_check=args.chain_node_check,
    chain_node_repair=args.chain_node_repair,
    extra_base_skills=_extra_base_skills,
    experiment_arm=args.arm,
    experiment_trial=args.trial,
    **_v30_kw,
)

import os as _os
import sys as _sys
import traceback as _tb

# 🔴 learn() 必须包在 try 里。
# 原先它是裸调用：一旦抛异常，下面那段 os._exit(0) 的收尾就永远执行不到，
# 而 JSPyBridge 留着非守护线程，进程于是**永远不退出**——
# 对 pgrep 来说它还活着，编排器会一直等下去。
# 2026-09-05：A:t1 09:41 抛异常死掉，进程挂到 14:37 仍在，
# 编排器白等 5 小时后超时退出，full2 那一批根本没起跑。
_exit_code = 0
try:
    voyager.learn()
except BaseException as _err:          # noqa: BLE001 —— 这里就是要兜住一切
    _exit_code = 1
    _tb.print_exc()
    print(f"\n[fatal] learn() 未捕获异常而终止：{type(_err).__name__}: {_err}", flush=True)
    try:
        # 让停因进数据，否则事后只能靠翻日志判断这次是怎么没的
        voyager._record_run_stop("uncaught_exception", voyager.recorder.iteration)
    except Exception as _e2:
        print(f"[fatal] 记录停因也失败了：{_e2}", flush=True)

# 收尾：JSPyBridge 会留下非守护线程和 node 子进程，导致进程跑完不退出。
# 审计日志为即写即关 + flush，因此强制退出不会丢数据。
try:
    voyager.close()
except Exception as _e:
    print(f"[shutdown] close() failed (non-fatal): {_e}", flush=True)
finally:
    _sys.stdout.flush()
    _sys.stderr.flush()
    print(f"[shutdown] forcing exit (code {_exit_code})", flush=True)
    _os._exit(_exit_code)
