#!/usr/bin/env python3
"""2026-09-11 审计后的统一启动器（F14；fix_0911b 改为读两机共用端口表 + UTC + F16 参数）。

替代 _cc0903 里的 run_arm.sh / pro_cd_0910.py / start_*_one.py / run_matched_0909.py
（问题见 审计报告 §2.5：无单实例保护、同名二次启动 rm -rf ckpt、25575 硬编码撞车、裸 java、
不透传 --api-base/--model-name）。

用法（仓库根目录，venv 内）：
    python _cc0911/launch_0911.py --plan smoke_v41            # smoke：A/B 各 6 轮，用 smoke lane
    python _cc0911/launch_0911.py --plan smoke_full           # 全开关 6 轮：params+chain+dynamic cap0+exec
    python _cc0911/launch_0911.py --plan rq2arm_v41           # RQ 双臂 OLD/NEW × t1–t3，160 轮（--machine-jobs 按机切）
    python _cc0911/launch_0911.py --plan rq2arm_v41 --only-jobs OLD1,NEW1  # 任意机器只起指定 job（与 --machine-jobs 互斥）
    python _cc0911/launch_0911.py --plan rq2arm_smoke         # 同上两臂，6 轮 smoke lane
    python _cc0911/launch_0911.py --plan mech_cap3_smoke      # NEW 臂、容量 3、10 轮、本机 lane 9
    python _cc0911/launch_0911.py --plan rq_v2_smoke          # NEW + --planner-v2，12 轮，slac002 smoke lane 8
    python _cc0911/launch_0911.py --plan rq_v2_confirm        # OLD vs NEW-v2 × t1–t3，160 轮（--machine-jobs 按机切）
    python _cc0911/launch_0911.py --plan rq_v2_full           # OLD / C1 / C123 × t1–t3，160 轮（--machine-jobs 按机切）
    python _cc0911/launch_0911.py --plan rq_v21               # C1/C123 重跑 + --noeffect-guard（原版不在本 plan）
    python _cc0911/launch_0911.py --plan rq_v22               # C1/C123 再重跑：禁用落到执行层（原版不在本 plan）
    python _cc0911/launch_0911.py --plan rq_v23               # C1/C123 第三次重跑：空转判定自有豁免名单（原版不在本 plan）
    python _cc0911/launch_0911.py --plan rq_v24               # 环境修复后重跑：原版 + C1 × t1–t3，机器人为 op（gamerule/暂停/收回工作台生效）
    python _cc0911/launch_0911.py --plan rq_v24_cap           # C123（原定③，X=20）× t1–t3，rq_v23 CAP 同参，机器人为 op
    python _cc0911/launch_0911.py --plan rq_v24_v4_smoke      # 同上换 V4 Flash 0731（OpenRouter），世界 1 两臂 6 轮，本机 smoke lane 8/9
    python _cc0911/launch_0911.py --plan rq_v24_v4a           # 同上换 V4 Flash 0731（OpenRouter），世界 1 两臂，slac002 lane 8/9
    python _cc0911/launch_0911.py --plan rq_v24_v4b           # 同上，世界 2/3 两臂，沿用 rq_v24 的 lane（rq_v24 跑完再起）
    python _cc0911/launch_0911.py --plan rq_v26               # v24 暂停状态反转修复后重跑 V4.1 世界 1/2（5 条，lane 3 被占时先起）
    python _cc0911/launch_0911.py --plan rq_v26b              # 同上，原版世界 2（lane 3 空出后起）
    python _cc0911/launch_0911.py --plan rq_v28_smoke         # v28 小测：C1 世界 1，10 轮，本机 lane 8
    python _cc0911/launch_0911.py --plan rq_v28               # v28 V4.1 C1 × 世界 1–3，本机 lane 0/1/2
    python _cc0911/launch_0911.py --plan rq_v28_cap           # v28 V4.1 C123 × 世界 1–3，slac002 lane 0/1/2
    python _cc0911/launch_0911.py --plan rq_v28_v4            # v28 V4 C1 × 世界 1–3，slac002 lane 3/8/9
    python _cc0911/launch_0911.py --plan v41_ab               # V4.1 Flash 官方 A/B × t1–t3，160 轮
    python _cc0911/launch_0911.py --plan full_v41_ab          # 同上全开关，suffix=_full_v41_0911
    python _cc0911/launch_0911.py --plan v4_ab                # V4 Flash 0731 via OpenRouter，钉 DeepInfra,Novita
    python _cc0911/launch_0911.py --plan a41or                # 桥格：V4.1 Flash via OpenRouter（同钉）
    python _cc0911/launch_0911.py --plan v41_ab --dry-run
    python _cc0911/launch_0911.py --status

规矩：
  * 端口只从 _cc0903/ports_0911.json 取（MC = 25600+10·机器号+lane，bridge = 3100+10·机器号+lane），
    机器按 $USER / 主机名判断（admin1 → machine 1；user → slac002）。正式批用非 smoke lane，
    并发 = 表里的 max_parallel；smoke 计划用 use=smoke 的 lane。
  * 幂等：每作业一个 pidfile（runs_0911/<plan>/<tag>.pid）；调度器自身 launcher.pid；第二份直接退出。
  * java：表里 machines.<m>.java17 优先；否则找 $JAVA17 / ROOT/toolchain/java-17*/bin/java / sdkman / /usr/lib/jvm；
    不是 17 就拒绝起跑。
  * 时钟：进程级 TZ=UTC（build_env），MC 加 -Duser.timezone=UTC，launcher 日志 …Z；系统时区不动。
  * 不设代理（WSL 可直连；旧代理 172.25.144.1 已失效会挂死请求）。
  * 续跑：worker 非终态退出自动 --resume，次数落盘 <tag>.resumes，上限 MAX_RESUME；绝不 rm -rf ckpt。
  * 显式传全部开关，不依赖代码默认值。
"""
import argparse
import datetime
import getpass
import glob
import hashlib
import json
import os
import pathlib
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid

ROOT = pathlib.Path(os.environ.get("VOYAGER_ROOT", pathlib.Path(__file__).resolve().parents[1]))
MAX_RESUME = 3
SEED_BASE = 20260902000  # 世界 w 的种子 = SEED_BASE + trial；同 trial 两臂同种子
TERMINAL_REASONS = {"iteration_limit", "environment_censored", "curriculum_stall", "curriculum_error_limit"}
BOT_NAME = "bot"  # voyager/env/mineflayer/index.js 里写死的离线用户名
PORTS_FILE = next((p for p in (ROOT / "_cc0903" / "ports_0911.json", ROOT / "_cc0911" / "ports_0911.json") if p.exists()), None)

# WP-K smoke_full extra；NEW 原样接上。OLD 从 COMMON 去掉 --skill-api-prompt。
NEW_EXTRA = [
    "--skill-params", "--chain", "--governance", "dynamic",
    "--governance-capacity", "0", "--contribution-mode", "exec",
]
OLD_DROP = ["--skill-api-prompt"]

# ---- 各臂共同开关（实验方案 §2/§3 + 审计 §4）----
COMMON = [
    "--max-iterations", "160",
    "--contribution-mode", "binary",
    "--call-detect", "runtime",          # 2026-09-11 拍板起最小实验：计分用运行时判定（方案 §3 原定值）。
                                         # 理由：哨兵检查 + otherError 路径修复后探针可信；0910 批 api 开时
                                         # text_nodefs 比 runtime 高约 2 倍（写了没执行的调用会被记分）。
                                         # 三路仍全部落盘（call_probe_compare），事后可离线换口径。
    "--retire-min-exposure", "12",
    "--retire-use-threshold", "0.15",
    "--curation-interval", "15",
    "--substitute-guard", "fp",
    "--substitute-undecided", "allow_retire",
    "--skill-api-prompt",                # Exp 2/3 的呈现方式：签名式
]
OR_BASE = "https://openrouter.ai/api/v1"
OR_PROVIDERS = "DeepInfra,Novita"        # 实验方案_v2 §1.1：两家都 fp8，同时托管 V4 0731 与 V4.1

PLANS = {
    "smoke_v41": dict(suffix="_smoke0911", max_iter=6, smoke=True,
                      jobs=[("A", 1), ("B", 1)], model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers=""),
    "smoke_v4or": dict(suffix="_smokeor0911", max_iter=6, smoke=True,
                       jobs=[("A", 1), ("B", 1)], model="deepseek/deepseek-v4-flash-0731", api_base=OR_BASE,
                       key_env="OPENROUTER_API_KEY", providers=OR_PROVIDERS),
    "v41_ab": dict(suffix="_v41_0911", max_iter=160, smoke=False,
                   jobs=[("A", 1), ("B", 1), ("A", 2), ("B", 2), ("A", 3), ("B", 3)],
                   model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers=""),
    # 同一批的另外三个世界（slac002 跑，本机跑 t1–t3），seed=20260902000+trial，与本机不撞
    "v41_ab_t456": dict(suffix="_v41_0911", max_iter=160, smoke=False,
                        jobs=[("A", 4), ("B", 4), ("A", 5), ("B", 5), ("A", 6), ("B", 6)],
                        model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers=""),
    "v4_ab": dict(suffix="_v4_0911", max_iter=160, smoke=False,
                  jobs=[("A", 1), ("B", 1), ("A", 2), ("B", 2), ("A", 3), ("B", 3)],
                  model="deepseek/deepseek-v4-flash-0731", api_base=OR_BASE,
                  key_env="OPENROUTER_API_KEY", providers=OR_PROVIDERS),
    "a41or": dict(suffix="_a41or_0911", max_iter=160, smoke=False,
                  jobs=[("A", 1), ("A", 2), ("A", 3)],
                  model="deepseek/deepseek-v4.1-flash", api_base=OR_BASE,
                  key_env="OPENROUTER_API_KEY", providers=OR_PROVIDERS),
    "smoke_full": dict(
        suffix="_fullsmoke0911", max_iter=6, smoke=True,
        jobs=[("A", 1), ("B", 1)], model=None, api_base=None,
        key_env="DEEPSEEK_API_KEY", providers="",
        extra_args=[
            "--skill-params", "--chain", "--governance", "dynamic",
            "--governance-capacity", "0", "--contribution-mode", "exec",
        ],
    ),
    "full_v41_ab": dict(
        suffix="_full_v41_0911", max_iter=160, smoke=False,
        jobs=[("A", 1), ("B", 1), ("A", 2), ("B", 2), ("A", 3), ("B", 3)],
        model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        extra_args=[
            "--skill-params", "--chain", "--governance", "dynamic",
            "--governance-capacity", "0", "--contribution-mode", "exec",
        ],
    ),
    # RQ 双臂：两臂一律 --arm A（Flash、curation 关）。后缀区分 OLD/NEW。
    # 分配表写死：本机 trial 1 两臂；slac002 trial 2、3 四条。
    "rq2arm_v41": dict(
        suffix="_rq2arm_v41", max_iter=160, smoke=False,
        jobs=None, model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
    ),
    "rq2arm_smoke": dict(
        suffix="_rq2arm_smoke", max_iter=6, smoke=True,
        jobs=None, model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
    ),
    # WP-N2：与 rq2arm_smoke NEW 臂相同，只把 capacity 0→3、本机 lane 9。
    # WP-O：max_iter 8→10，让 LRU 淘汰多触发几次。
    "mech_cap3_smoke": dict(
        suffix="_mechcap3sm", max_iter=10, smoke=True,
        jobs=None, model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
    ),
    # WP-Q：rq2arm_v41 NEW 臂参数 + --planner-v2，12 轮，trial 1，slac002 lane 8。
    "rq_v2_smoke": dict(
        suffix="_rqNEWv2sm", max_iter=12, smoke=True,
        jobs=None, model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
    ),
    # WP-R：rq2arm_v41 OLD/NEW 逐字同参；NEW 外加 --planner-v2。后缀 _v2cOLD/_v2cNEW。
    # 分配表写死：slac002 世界 1+2 四条；admin1 世界 3 两条。
    "rq_v2_confirm": dict(
        suffix="_v2c", max_iter=160, smoke=False,
        jobs=None, model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
    ),
    # WP-S：三臂 OLD / C1(NEW+planner-v2, cap 0) / C123(同 C1, cap 20)。
    # slac002=世界 1+2 共 6 条（正式 0–3 + smoke 8/9，并行 5，第 6 条排队）；
    # admin1=世界 3 共 3 条（lanes 0–2，并行 3）。
    "rq_v2_full": dict(
        suffix="_v2c", max_iter=160, smoke=False,
        jobs=None, model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        include_smoke_lanes_on=("slac002",),
        parallel_by_machine={"slac002": 5},
    ),
    # WP-W：C1/C123 重跑 + --noeffect-guard。原版 _v2cOLD 不在本 plan。
    # slac002=世界 1+2 共 4 条（lane 1/2/8/9，不占仍在跑的 OLD lane 0/3；并行 3，第 4 条排队）；
    # admin1=世界 3 共 2 条（lane 1/2，不占 OLD lane 0；并行 2）。
    "rq_v21": dict(
        suffix="_v21", max_iter=160, smoke=False,
        jobs=None, model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        include_smoke_lanes_on=("slac002",),
        parallel_by_machine={"slac002": 3, "admin1": 2},
    ),
    # WP-X：C1/C123 再重跑。与 rq_v21 同 lane/并行/机器，后缀 _v22NEW/_v22CAP。
    "rq_v22": dict(
        suffix="_v22", max_iter=160, smoke=False,
        jobs=None, model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        include_smoke_lanes_on=("slac002",),
        parallel_by_machine={"slac002": 3, "admin1": 2},
    ),
    # WP-Z：C1/C123 第三次重跑。与 rq_v22 同 lane/并行/机器，后缀 _v23NEW/_v23CAP。
    "rq_v23": dict(
        suffix="_v23", max_iter=160, smoke=False,
        jobs=None, model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        include_smoke_lanes_on=("slac002",),
        parallel_by_machine={"slac002": 3, "admin1": 2},
    ),
    # 09-12 环境修复：此前独立服务器 ops.json 为空，机器人不是 op → gamerule（永昼/死亡不掉落）、
    # /pause、收回工作台熔炉、硬重置全部静默失效。本 plan 起 op_bot=True。
    # 原版 + C1 × t1–t3（C1 = rq_v23 NEW 逐字同参）；C123′ 待离线回放通过后另起 plan，lane 8/9 与 admin1 lane 1 留给它。
    "rq_v24": dict(
        suffix="_v24", max_iter=160, smoke=False,
        jobs=None, model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        include_smoke_lanes_on=("slac002",),
        parallel_by_machine={"slac002": 4, "admin1": 2},
        op_bot=True,
    ),
    # 预注册更正 4 补：③′ 离线回放未过 → 不跑 ③′；C123 按预注册原定③（近期性容量移出 X=20）在修好的环境里跑。
    # lane：slac002 t1→0、t2→1（rq_v24 世界 1 两条跑完后空出），admin1 t3→1（v23 C1 世界 3 跑完后空出）。
    "rq_v24_cap": dict(
        suffix="_v24", max_iter=160, smoke=False,
        jobs=None, model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        include_smoke_lanes_on=("slac002",),
        parallel_by_machine={"slac002": 2, "admin1": 1},
        op_bot=True,
    ),
    # RQ 第三问（是否依赖模型能力）：rq_v24 逐字同参，只换模型为 V4 Flash 0731 via OpenRouter。
    "rq_v24_v4_smoke": dict(
        suffix="_v24v4sm", max_iter=6, smoke=True,
        jobs=None, model="deepseek/deepseek-v4-flash-0731", api_base=OR_BASE,
        key_env="OPENROUTER_API_KEY", providers=OR_PROVIDERS, op_bot=True,
    ),
    # 正式 V4 批拆两份以便世界 1 先起（两份同后缀 _v24v4，ckpt 按世界区分不撞；launcher 按 plan 单实例）。
    "rq_v24_v4a": dict(
        suffix="_v24v4", max_iter=160, smoke=False,
        jobs=None, model="deepseek/deepseek-v4-flash-0731", api_base=OR_BASE,
        key_env="OPENROUTER_API_KEY", providers=OR_PROVIDERS,
        include_smoke_lanes_on=("slac002",),
        parallel_by_machine={"slac002": 2},
        op_bot=True,
    ),
    "rq_v24_v4b": dict(
        suffix="_v24v4", max_iter=160, smoke=False,
        jobs=None, model="deepseek/deepseek-v4-flash-0731", api_base=OR_BASE,
        key_env="OPENROUTER_API_KEY", providers=OR_PROVIDERS,
        include_smoke_lanes_on=("slac002",),
        parallel_by_machine={"slac002": 2, "admin1": 2},
        op_bot=True,
    ),
    # 09-13 暂停状态反转：/pause 是切换式，自记状态与实际相反时执行代码期间世界冻结；V4.1 世界 1/2
    # 六条污染 9–92%（_audit_0911/v24_final/发现_暂停状态反转_2026-09-13.md）。rq_v24/rq_v24_cap 逐字同参
    # + --pause-verify（执行前核实服务器在走）+ --no-stall-abort（预注册更正 7 的规则，从头生效）。
    "rq_v26": dict(
        suffix="_v26", max_iter=160, smoke=False,
        jobs=None, model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        include_smoke_lanes_on=("slac002",),
        parallel_by_machine={"slac002": 5},
        op_bot=True,
    ),
    # 09-13 预注册补充分析 C（空检索对照）：原版 + --retrieval-top-k 0（技能照常入库但从不展示），
    # 其余与 rq_v26 原版逐字同参（--pause-verify --no-stall-abort）。V4.1 与 V4 各 × 世界 1–3，全部 slac002。
    "rq_v27_nr": dict(
        suffix="_v27NR", max_iter=160, smoke=False,
        jobs=None, model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        include_smoke_lanes_on=("slac002",),
        parallel_by_machine={"slac002": 3},
        op_bot=True,
    ),
    "rq_v27_nr_v4": dict(
        suffix="_v27v4NR", max_iter=160, smoke=False,
        jobs=None, model="deepseek/deepseek-v4-flash-0731", api_base=OR_BASE,
        key_env="OPENROUTER_API_KEY", providers=OR_PROVIDERS,
        include_smoke_lanes_on=("slac002",),
        parallel_by_machine={"slac002": 3},
        op_bot=True,
    ),
    "rq_v27_nr_smoke": dict(
        suffix="_v27NRsm", max_iter=4, smoke=True,
        jobs=None, model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        op_bot=True,
    ),
    "rq_v26b": dict(
        suffix="_v26", max_iter=160, smoke=False,
        jobs=None, model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        include_smoke_lanes_on=("slac002",),
        parallel_by_machine={"slac002": 1},
        op_bot=True,
    ),
    # 09-14 v28（预注册补充 D）：机制臂 = rq_v26 的 C1/C123 逐字同参 + --gap-reference --chain-time-budget 240。
    # 原版不重跑（沿用 _v26OLD / _v24OLD / _v24v4OLD）。见 _audit_0911/v24_final/机制臂输因_第0步诊断_2026-09-14.md。
    "rq_v28": dict(
        suffix="_v28", max_iter=160, smoke=False,
        jobs=None, model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        parallel_by_machine={"admin1": 3},
        op_bot=True,
    ),
    "rq_v28_cap": dict(
        suffix="_v28", max_iter=160, smoke=False,
        jobs=None, model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        include_smoke_lanes_on=("slac002",),
        parallel_by_machine={"slac002": 3},
        op_bot=True,
    ),
    "rq_v28_v4": dict(
        suffix="_v28v4", max_iter=160, smoke=False,
        jobs=None, model="deepseek/deepseek-v4-flash-0731", api_base=OR_BASE,
        key_env="OPENROUTER_API_KEY", providers=OR_PROVIDERS,
        include_smoke_lanes_on=("slac002",),
        parallel_by_machine={"slac002": 3},
        op_bot=True,
    ),
    "rq_v28_smoke": dict(
        suffix="_v28sm", max_iter=10, smoke=True,
        jobs=None, model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        op_bot=True,
    ),
}


def rq2arm_jobs(smoke=False):
    """OLD/NEW × trials. smoke：本机只 OLD t1；slac002 OLD+NEW t1。正式批按世界分机器。"""
    old_suf = "_rqOLD0912sm" if smoke else "_rqOLD0912"
    new_suf = "_rqNEW0912sm" if smoke else "_rqNEW0912"
    jobs = []
    if smoke:
        jobs.append(dict(
            rq="OLD", trial=1, voyager_arm="A", suffix=old_suf,
            machine=["admin1", "slac002"], extra_args=[], drop_args=list(OLD_DROP),
        ))
        jobs.append(dict(
            rq="NEW", trial=1, voyager_arm="A", suffix=new_suf,
            machine=["slac002"], extra_args=list(NEW_EXTRA), drop_args=[],
        ))
        return jobs
    machine_of = {1: "admin1", 2: "slac002", 3: "slac002"}
    for trial in (1, 2, 3):
        m = machine_of[trial]
        jobs.append(dict(
            rq="OLD", trial=trial, voyager_arm="A", suffix=old_suf,
            machine=m, extra_args=[], drop_args=list(OLD_DROP),
        ))
        jobs.append(dict(
            rq="NEW", trial=trial, voyager_arm="A", suffix=new_suf,
            machine=m, extra_args=list(NEW_EXTRA), drop_args=[],
        ))
    return jobs


def mech_cap3_jobs():
    extra = list(NEW_EXTRA)
    flag = "--governance-capacity"
    if flag in extra:
        extra[extra.index(flag) + 1] = "3"
    else:
        extra.extend([flag, "3"])
    return [dict(
        rq="NEW", trial=1, voyager_arm="A", suffix="_mechcap3sm",
        machine="admin1", extra_args=extra, drop_args=[], lane=9,
    )]


PLANS["rq2arm_v41"]["jobs"] = rq2arm_jobs(smoke=False)
PLANS["rq2arm_smoke"]["jobs"] = rq2arm_jobs(smoke=True)
PLANS["mech_cap3_smoke"]["jobs"] = mech_cap3_jobs()


def rq_v2_smoke_jobs():
    extra = list(NEW_EXTRA) + ["--planner-v2"]
    return [dict(
        rq="NEW", trial=1, voyager_arm="A", suffix="_rqNEWv2sm",
        machine="slac002", extra_args=extra, drop_args=[], lane=8,
    )]


PLANS["rq_v2_smoke"]["jobs"] = rq_v2_smoke_jobs()


def rq_v2_confirm_jobs():
    """OLD/NEW × t1–t3. NEW = rq2arm NEW + --planner-v2. slac002=t1/t2；admin1=t3."""
    old_suf = "_v2cOLD"
    new_suf = "_v2cNEW"
    machine_of = {1: "slac002", 2: "slac002", 3: "admin1"}
    jobs = []
    for trial in (1, 2, 3):
        m = machine_of[trial]
        jobs.append(dict(
            rq="OLD", trial=trial, voyager_arm="A", suffix=old_suf,
            machine=m, extra_args=[], drop_args=list(OLD_DROP),
        ))
        jobs.append(dict(
            rq="NEW", trial=trial, voyager_arm="A", suffix=new_suf,
            machine=m, extra_args=list(NEW_EXTRA) + ["--planner-v2"], drop_args=[],
        ))
    return jobs


PLANS["rq_v2_confirm"]["jobs"] = rq_v2_confirm_jobs()


def rq_v2_full_jobs():
    """OLD/C1/C123 × t1–t3. C1 = rq2arm NEW + --planner-v2, cap 0; C123 = C1 with cap 20.

    slac002=t1/t2；admin1=t3.
    """
    old_suf = "_v2cOLD"
    new_suf = "_v2cNEW"
    cap_suf = "_v2cCAP"
    machine_of = {1: "slac002", 2: "slac002", 3: "admin1"}
    cap_extra = list(NEW_EXTRA) + ["--planner-v2"]
    flag = "--governance-capacity"
    cap_extra[cap_extra.index(flag) + 1] = "20"
    jobs = []
    for trial in (1, 2, 3):
        m = machine_of[trial]
        jobs.append(dict(
            rq="OLD", trial=trial, voyager_arm="A", suffix=old_suf,
            machine=m, extra_args=[], drop_args=list(OLD_DROP),
        ))
        jobs.append(dict(
            rq="NEW", trial=trial, voyager_arm="A", suffix=new_suf,
            machine=m, extra_args=list(NEW_EXTRA) + ["--planner-v2"], drop_args=[],
        ))
        jobs.append(dict(
            rq="CAP", trial=trial, voyager_arm="A", suffix=cap_suf,
            machine=m, extra_args=list(cap_extra), drop_args=[],
        ))
    return jobs


PLANS["rq_v2_full"]["jobs"] = rq_v2_full_jobs()


def rq_v21_jobs():
    """C1/C123 × t1–t3 = rq_v2_full NEW/CAP 逐字同参 + --noeffect-guard.

    后缀 _v21NEW/_v21CAP。lane 钉在已停的 NEW/CAP 占用位（slac002 CAP2 原排队，用 lane 9）。
    不占用仍在跑的 _v2cOLD lane。
    """
    new_suf = "_v21NEW"
    cap_suf = "_v21CAP"
    machine_of = {1: "slac002", 2: "slac002", 3: "admin1"}
    lane_of = {
        (1, "NEW"): 1, (1, "CAP"): 2,
        (2, "NEW"): 8, (2, "CAP"): 9,
        (3, "NEW"): 1, (3, "CAP"): 2,
    }
    new_extra = list(NEW_EXTRA) + ["--planner-v2", "--noeffect-guard"]
    cap_extra = list(NEW_EXTRA) + ["--planner-v2", "--noeffect-guard"]
    flag = "--governance-capacity"
    cap_extra[cap_extra.index(flag) + 1] = "20"
    jobs = []
    for trial in (1, 2, 3):
        m = machine_of[trial]
        jobs.append(dict(
            rq="NEW", trial=trial, voyager_arm="A", suffix=new_suf,
            machine=m, extra_args=list(new_extra), drop_args=[],
            lane=lane_of[(trial, "NEW")],
        ))
        jobs.append(dict(
            rq="CAP", trial=trial, voyager_arm="A", suffix=cap_suf,
            machine=m, extra_args=list(cap_extra), drop_args=[],
            lane=lane_of[(trial, "CAP")],
        ))
    return jobs


PLANS["rq_v21"]["jobs"] = rq_v21_jobs()


def rq_v22_jobs():
    """C1/C123 × t1–t3 = rq_v21 逐字同参；后缀 _v22NEW/_v22CAP。"""
    jobs = rq_v21_jobs()
    for job in jobs:
        job["suffix"] = job["suffix"].replace("_v21NEW", "_v22NEW").replace(
            "_v21CAP", "_v22CAP"
        )
    return jobs


PLANS["rq_v22"]["jobs"] = rq_v22_jobs()


def rq_v23_jobs():
    """C1/C123 × t1–t3 = rq_v22 逐字同参；后缀 _v23NEW/_v23CAP。

    Returns new dicts (and new extra/drop lists). Does not mutate rq_v22 jobs.
    """
    jobs = []
    for job in rq_v22_jobs():
        copied = dict(job)
        copied["suffix"] = job["suffix"].replace("_v22NEW", "_v23NEW").replace(
            "_v22CAP", "_v23CAP"
        )
        extra = job.get("extra_args")
        copied["extra_args"] = list(extra) if extra is not None else extra
        drop = job.get("drop_args")
        copied["drop_args"] = list(drop) if drop is not None else drop
        jobs.append(copied)
    return jobs


PLANS["rq_v23"]["jobs"] = rq_v23_jobs()


def rq_v24_jobs():
    """原版 + C1 × t1–t3，世界→机器与 rq_v2_full 相同（slac002=t1/t2，admin1=t3）。

    OLD 与 rq_v2_full OLD 同参；C1 与 rq_v23 NEW 同参。后缀 _v24OLD/_v24NEW。
    """
    lane_of = {(1, "OLD"): 0, (1, "NEW"): 1, (2, "OLD"): 3, (2, "NEW"): 2,
               (3, "OLD"): 0, (3, "NEW"): 2}
    olds = {j["trial"]: j for j in rq_v2_full_jobs() if j["rq"] == "OLD"}
    news = {j["trial"]: j for j in rq_v23_jobs() if j["rq"] == "NEW"}
    jobs = []
    for trial in (1, 2, 3):
        for rq, src in (("OLD", olds[trial]), ("NEW", news[trial])):
            job = dict(src, suffix=f"_v24{rq}", lane=lane_of[(trial, rq)],
                       extra_args=list(src["extra_args"]), drop_args=list(src["drop_args"]))
            jobs.append(job)
    return jobs


PLANS["rq_v24"]["jobs"] = rq_v24_jobs()


def rq_v24_cap_jobs():
    """C123 × t1–t3 = rq_v23 CAP 逐字同参，后缀 _v24CAP。"""
    lane_of = {1: 0, 2: 1, 3: 1}
    return [dict(j, suffix="_v24CAP", lane=lane_of[j["trial"]],
                 extra_args=list(j["extra_args"]), drop_args=list(j["drop_args"]))
            for j in rq_v23_jobs() if j["rq"] == "CAP"]


PLANS["rq_v24_cap"]["jobs"] = rq_v24_cap_jobs()


def rq_v24_v4_jobs(part):
    """rq_v24 的原版 + C1，只改后缀。part：smoke = 世界 1、本机 lane 8/9、6 轮；
    a = 世界 1、slac002 lane 8/9；b = 世界 2/3、沿用 rq_v24 的 lane。"""
    tag = "_v24v4sm" if part == "smoke" else "_v24v4"
    jobs = []
    for job in rq_v24_jobs():
        if (job["trial"] == 1) != (part in ("smoke", "a")):
            continue
        j = dict(job, suffix=f"{tag}{job['rq']}", extra_args=list(job["extra_args"]), drop_args=list(job["drop_args"]))
        if part == "smoke":
            j.update(machine="admin1", lane=8 if job["rq"] == "OLD" else 9)
        elif part == "a":
            j.update(lane=8 if job["rq"] == "OLD" else 9)
        elif job["trial"] == 3 and job["rq"] == "NEW":
            j.update(lane=1)  # 09-13 07:2x 北京：v24 C1 世界 3 仍占 lane 2，C123 世界 3 已空出 lane 1 → V4 世界 3 提前起
        jobs.append(j)
    return jobs


PLANS["rq_v24_v4_smoke"]["jobs"] = rq_v24_v4_jobs("smoke")
PLANS["rq_v24_v4a"]["jobs"] = rq_v24_v4_jobs("a")
PLANS["rq_v24_v4b"]["jobs"] = rq_v24_v4_jobs("b")


def rq_v26_jobs(part):
    """V4.1 原版/C1/C123 × 世界 1/2（slac002）= rq_v24 / rq_v24_cap 逐字同参 + --pause-verify --no-stall-abort，
    后缀 _v26OLD/_v26NEW/_v26CAP。part a：5 条（lane 3 被 V4 C1 世界 2 续跑占着）；b：原版世界 2，lane 3 空出后起。"""
    lane_of = {(1, "OLD"): 0, (1, "NEW"): 1, (1, "CAP"): 2, (2, "NEW"): 8, (2, "CAP"): 9, (2, "OLD"): 3}
    jobs = []
    for job in rq_v24_jobs() + rq_v24_cap_jobs():
        key = (job["trial"], job["rq"])
        if key not in lane_of or (key == (2, "OLD")) != (part == "b"):
            continue
        jobs.append(dict(job, suffix=f"_v26{job['rq']}", lane=lane_of[key],
                         extra_args=list(job["extra_args"]) + ["--pause-verify", "--no-stall-abort"],
                         drop_args=list(job["drop_args"])))
    return jobs


def rq_v27_nr_jobs(part):
    """空检索对照：rq_v24 原版 × 世界 1–3，加 --pause-verify --no-stall-abort --retrieval-top-k 0，全部 slac002。
    part：v41 = lane 0/1/2（DeepSeek 官方）；v4 = lane 3/8/9（OpenRouter V4）；smoke = 世界 1、lane 8、4 轮。"""
    tag = {"v41": "_v27NR", "v4": "_v27v4NR", "smoke": "_v27NRsm"}[part]
    lanes = {"v41": {1: 0, 2: 1, 3: 2}, "v4": {1: 3, 2: 8, 3: 9}, "smoke": {1: 8}}[part]
    jobs = []
    for job in rq_v24_jobs():
        if job["rq"] != "OLD" or job["trial"] not in lanes:
            continue
        jobs.append(dict(job, suffix=f"{tag}OLD", lane=lanes[job["trial"]], machine="slac002",
                         extra_args=list(job["extra_args"]) + ["--pause-verify", "--no-stall-abort", "--retrieval-top-k", "0"],
                         drop_args=list(job["drop_args"])))
    return jobs


PLANS["rq_v27_nr"]["jobs"] = rq_v27_nr_jobs("v41")
PLANS["rq_v27_nr_v4"]["jobs"] = rq_v27_nr_jobs("v4")
PLANS["rq_v27_nr_smoke"]["jobs"] = rq_v27_nr_jobs("smoke")
PLANS["rq_v26"]["jobs"] = rq_v26_jobs("a")
PLANS["rq_v26b"]["jobs"] = rq_v26_jobs("b")


# 09-23 背包技能 discardJunk：原版 vs 原版+技能（进基础层+点名），= rq_v26 原版逐字同参，
# 处理臂只多 --extra-base-skills discardJunk。全部 slac002（本机下午要用）。
# 预注册：_协作工作区/预注册_背包技能_试点与正式批_2026-09-23.md
PLANS["inv_0923_pilot"] = dict(
    suffix="_i23", max_iter=160, smoke=False,
    jobs=None, model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
    include_smoke_lanes_on=("slac002",),
    parallel_by_machine={"slac002": 4},
    op_bot=True,
)


def inv_0923_jobs(trials, lanes):
    tmpl = [j for j in rq_v24_jobs() if j["rq"] == "OLD"][0]
    lane_iter = iter(lanes)
    jobs = []
    for t in trials:
        for arm, extra in (("OLD", []), ("INV", ["--extra-base-skills", "discardJunk"])):
            jobs.append(dict(tmpl, trial=t, rq=arm, suffix=f"_i23{arm}", machine="slac002", lane=next(lane_iter),
                             extra_args=list(tmpl["extra_args"]) + ["--pause-verify", "--no-stall-abort"] + extra,
                             drop_args=list(tmpl["drop_args"])))
    return jobs


PLANS["inv_0923_pilot"]["jobs"] = inv_0923_jobs((4, 5), (0, 1, 2, 3))


# 09-25 v30 放行关（开发世界 1–3，原版 vs 系统，两臂只差 4 个 v30 开关；同一世界两臂同机）。
V30_SYS = ["--v30-verify-critic", "--v30-named-actions", "--v30-ledger", "--v30-no-lru-evict"]


def v30_jobs(placement, max_iter=None):
    """placement: {trial: (machine, lane_OLD, lane_SYS)}；OLD 模板与 inv_0923 相同（rq_v24 OLD + pause-verify + no-stall-abort）。"""
    tmpl = [j for j in rq_v24_jobs() if j["rq"] == "OLD"][0]
    jobs = []
    for t, (machine, lo, ls) in placement.items():
        for arm, lane, extra in (("OLD", lo, []), ("SYS", ls, list(V30_SYS))):
            jobs.append(dict(tmpl, trial=t, rq=arm, suffix=f"_g25{arm}", machine=machine, lane=lane,
                             extra_args=list(tmpl["extra_args"]) + ["--pause-verify", "--no-stall-abort"] + extra,
                             drop_args=list(tmpl["drop_args"])))
    return jobs


PLANS["v30_gate_0925"] = dict(
    suffix="_g25", max_iter=160, smoke=False,
    jobs=v30_jobs({1: ("admin1", 0, 1), 2: ("admin1", 2, 8), 3: ("slac002", 0, 1)}),
    model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
    include_smoke_lanes_on=("admin1",),
    parallel_by_machine={"admin1": 4, "slac002": 2},
    op_bot=True,
)
# 冒烟：只跑系统臂、8 轮；本机 lane 8（世界 1），slac002 lane 8（世界 3）；冒烟只能用冒烟 lane 8/9。
PLANS["v30_smoke_0925"] = dict(
    suffix="_s25", max_iter=8, smoke=True,
    jobs=[dict(j, suffix="_s25SYS") for j in v30_jobs({1: ("admin1", 8, 8)}) if j["rq"] == "SYS"]
         + [dict(j, suffix="_s25SYS") for j in v30_jobs({3: ("slac002", 8, 8)}) if j["rq"] == "SYS"],
    model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
    include_smoke_lanes_on=("admin1", "slac002"),
    parallel_by_machine={"admin1": 1, "slac002": 1},
    op_bot=True,
)

V28_EXTRA = ["--pause-verify", "--no-stall-abort", "--gap-reference", "--chain-time-budget", "240"]


def rq_v28_jobs(part):
    """v28 机制臂 = rq_v26 的 C1/C123 逐字同参（rq_v24 NEW / rq_v24_cap + --pause-verify --no-stall-abort）
    + --gap-reference --chain-time-budget 240。
    part：local = V4.1 C1 × 世界 1–3（admin1 lane 0/1/2）；cap = V4.1 C123 × 世界 1–3（slac002 lane 0/1/2）；
    v4 = V4 C1 × 世界 1–3（slac002 lane 3/8/9）；smoke = V4.1 C1 世界 1（admin1 lane 8，10 轮）。"""
    rq = "CAP" if part == "cap" else "NEW"
    tag = {"local": "_v28", "cap": "_v28", "v4": "_v28v4", "smoke": "_v28sm"}[part]
    machine = "admin1" if part in ("local", "smoke") else "slac002"
    lanes = {"local": {1: 0, 2: 1, 3: 2}, "cap": {1: 0, 2: 1, 3: 2},
             "v4": {1: 3, 2: 8, 3: 9}, "smoke": {1: 8}}[part]
    src = rq_v24_cap_jobs() if rq == "CAP" else [j for j in rq_v24_jobs() if j["rq"] == "NEW"]
    jobs = []
    for job in src:
        if job["trial"] not in lanes:
            continue
        jobs.append(dict(job, suffix=f"{tag}{rq}", lane=lanes[job["trial"]], machine=machine,
                         extra_args=list(job["extra_args"]) + list(V28_EXTRA),
                         drop_args=list(job["drop_args"])))
    return jobs


PLANS["rq_v28"]["jobs"] = rq_v28_jobs("local")
PLANS["rq_v28_cap"]["jobs"] = rq_v28_jobs("cap")
PLANS["rq_v28_v4"]["jobs"] = rq_v28_jobs("v4")
PLANS["rq_v28_smoke"]["jobs"] = rq_v28_jobs("smoke")


def plan_lanes_and_parallel(plan, mcfg, machine):
    """Lane list + concurrency for this plan on this machine.

    Default matches the historical rule: smoke plans use smoke lanes (all of them);
    formal plans use non-smoke lanes and cap at machines.<m>.max_parallel.
    A plan may opt in to smoke lanes and/or override parallelism; unset → no change.
    """
    lanes = {k: v for k, v in mcfg["lanes"].items() if (v.get("use") == "smoke") == bool(plan["smoke"])}
    extra = plan.get("include_smoke_lanes_on") or ()
    if machine in extra:
        for k, v in mcfg["lanes"].items():
            if v.get("use") == "smoke":
                lanes[k] = v
    lane_list = [(int(k), v["mc"], v["bridge"]) for k, v in sorted(lanes.items(), key=lambda kv: int(kv[0]))]
    if plan["smoke"]:
        parallel = len(lane_list)
    else:
        parallel = min(int(mcfg.get("max_parallel", 1)), len(lane_list))
    override = (plan.get("parallel_by_machine") or {}).get(machine)
    if override is not None:
        parallel = min(int(override), len(lane_list))
    return lane_list, parallel


def normalize_job(job, plan):
    if isinstance(job, dict):
        j = dict(job)
        j.setdefault("voyager_arm", j.get("rq", "A"))
        j.setdefault("suffix", plan["suffix"])
        j.setdefault("rq", j.get("voyager_arm"))
        j.setdefault("trial", j.get("trial"))
        return j
    arm, trial = job
    return dict(
        rq=arm, trial=trial, voyager_arm=arm, suffix=plan["suffix"],
        machine=None, extra_args=plan.get("extra_args"), drop_args=plan.get("drop_args"),
    )


def job_tag(job):
    return f"{job['rq']}{job['trial']}"


def job_seed(job):
    return SEED_BASE + int(job["trial"])


def job_on_machine(job, machine):
    assigned = job.get("machine")
    if not assigned:
        return True
    if isinstance(assigned, (list, tuple, set)):
        return machine in assigned
    return assigned == machine


def parse_job_tags(text):
    return [t.strip() for t in (text or "").split(",") if t.strip()]


def select_jobs(all_jobs, *, only_jobs=None, machine_jobs=None, machine=None, only=None):
    """Filter jobs. --only-jobs ignores the machine allocation table.

    --only-jobs and --machine-jobs are mutually exclusive.
    """
    if only_jobs and machine_jobs is not None:
        raise ValueError("--only-jobs 与 --machine-jobs 互斥")
    jobs = list(all_jobs)
    if only_jobs:
        keep = set(parse_job_tags(only_jobs))
        jobs = [j for j in jobs if job_tag(j) in keep]
    elif machine_jobs is not None:
        want = machine if machine_jobs == "auto" else machine_jobs
        jobs = [j for j in jobs if job_on_machine(j, want)]
    if only:
        keep = set(parse_job_tags(only))
        jobs = [j for j in jobs if job_tag(j) in keep]
    return jobs


def format_alloc(jobs):
    buckets = {}
    for job in jobs:
        assigned = job.get("machine") or "(any)"
        if isinstance(assigned, (list, tuple, set)):
            keys = list(assigned)
        else:
            keys = [assigned]
        for key in keys:
            buckets.setdefault(key, []).append(job_tag(job))
    return buckets


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(out_dir, msg):
    line = f"{utc_now()} {msg}"
    print(line, flush=True)
    with (out_dir / "launcher.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_ports():
    if not PORTS_FILE:
        raise SystemExit("🔴 找不到 _cc0903/ports_0911.json（两机应已下发）；拒绝手写端口")
    return json.loads(PORTS_FILE.read_text(encoding="utf-8"))


def detect_machine(ports, override=None):
    if override:
        return override
    user = getpass.getuser()
    host = socket.gethostname().lower()
    for name in ports["machines"]:
        if name == user or name in host:
            return name
    raise SystemExit(f"🔴 无法从 user={user} host={host} 判断机器；用 --machine 指定（{list(ports['machines'])}）")


def port_free(p):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", p)) != 0


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, TypeError, ValueError):
        return False


def read_pidfile(path):
    try:
        return int(path.read_text().strip())
    except Exception:
        return None


def _is_java17(path):
    try:
        out = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=15)
        banner = out.stderr or out.stdout or ""
        return '"17.' in banner or " 17." in banner
    except Exception:
        return False


def find_java17(machine_cfg):
    candidates = []
    if machine_cfg.get("java17"):
        candidates.append(machine_cfg["java17"])
    if os.environ.get("JAVA17"):
        candidates.append(os.environ["JAVA17"])
    candidates += glob.glob(str(ROOT / "toolchain" / "java-17*" / "bin" / "java"))
    candidates += glob.glob(os.path.expanduser("~/.sdkman/candidates/java/17*/bin/java"))
    candidates += glob.glob("/usr/lib/jvm/*17*/bin/java")
    candidates.append(shutil.which("java") or "")
    for c in candidates:
        if c and os.path.exists(c) and _is_java17(c):
            return c
    return None


def build_env(key_env):
    env = os.environ.copy()
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_DATASETS_OFFLINE="1",
               HF_ENDPOINT="https://hf-mirror.com", MC_HOST="127.0.0.1",
               RUNTIME_CALL_PROBE="1", PYTHONDONTWRITEBYTECODE="1",
               TOKENIZERS_PARALLELISM="false",
               TZ="UTC")   # 两机时钟统一：voyager 写的 naive ts 从此一律 UTC；系统时区不动
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(k, None)
    env["no_proxy"] = env["NO_PROXY"] = "localhost,127.0.0.1,::1"
    # 09-25：本机 WSL 直连不通（Clash fake-ip），只能走网关代理出网。显式开关，不设时 env 与改动前逐键相同。
    _proxy = os.environ.get("V30_EGRESS_PROXY", "").strip()
    if _proxy:
        for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            env[_k] = _proxy
    if not env.get(key_env):
        raise SystemExit(f"🔴 环境变量 {key_env} 为空（.env 里没有？）")
    return env


def ckpt_name(arm, trial, suffix):
    model_short = "flash" if arm in ("A", "B") else "pro"
    gov = "on" if arm in ("B", "D") else "off"
    return f"ckpt_{arm}_{model_short}_{gov}_t{trial}{suffix}"


def run_meta(ckpt):
    p = ROOT / ckpt / "metrics" / "run_meta.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def offline_uuid(name):
    """离线服务器分配给玩家的 UUID，同 Java UUID.nameUUIDFromBytes(("OfflinePlayer:" + name).getBytes())。"""
    h = bytearray(hashlib.md5(f"OfflinePlayer:{name}".encode()).digest())
    h[6] = (h[6] & 0x0F) | 0x30
    h[8] = (h[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(h)))


def start_mc(out_dir, tag, mc_port, seed, java, env, op_bot=False):
    lane = out_dir / f"{tag}_server"
    if not lane.exists():
        shutil.copytree(ROOT / "mc_template", lane)
        props = lane / "server.properties"
        patch = {"server-port": str(mc_port), "server-ip": "127.0.0.1", "level-seed": str(seed),
                 "enable-rcon": "false", "query.port": str(mc_port)}
        lines = [l for l in props.read_text().splitlines() if l.split("=", 1)[0] not in patch]
        props.write_text("\n".join(lines + [f"{k}={v}" for k, v in patch.items()]) + "\n")
    if op_bot:  # 每次起服都写（续跑也保证是 op）；服务器只在启动时读 ops.json
        (lane / "ops.json").write_text(json.dumps(
            [{"uuid": offline_uuid(BOT_NAME), "name": BOT_NAME, "level": 4, "bypassesPlayerLimit": False}]))
    mc_log_path = out_dir / f"{tag}_minecraft.log"
    mc_log = open(mc_log_path, "ab", buffering=0)
    srv = subprocess.Popen([java, "-Duser.timezone=UTC", "-Xms1G", "-Xmx" + (os.environ.get("V30_MC_XMX", "").strip() or "3G"), "-jar",
                            "fabric-server-launch.jar", "nogui"],
                           cwd=lane, stdout=mc_log, stderr=mc_log, start_new_session=True,
                           env={**os.environ, "TZ": "UTC"})
    (out_dir / f"{tag}_mc.pid").write_text(str(srv.pid))
    for _ in range(150):
        time.sleep(2)
        if srv.poll() is not None:
            return None, "Minecraft 启动即退出"
        if "Done (" in mc_log_path.read_text(errors="replace")[-20000:]:
            return srv, None
    srv.terminate()
    return None, "Minecraft 300 秒未就绪"


def _apply_extra_args(args, extra):
    """Append plan extra_args; drop matching flags already in args (and their values).

    So COMMON `--contribution-mode binary` is replaced when extra has `--contribution-mode exec`.
    """
    extra = list(extra or [])
    flags = {tok for tok in extra if isinstance(tok, str) and tok.startswith("--")}
    if not extra:
        return list(args)
    out = []
    i = 0
    base = list(args)
    while i < len(base):
        tok = base[i]
        if tok in flags:
            i += 1
            if i < len(base) and not str(base[i]).startswith("-"):
                i += 1
            continue
        out.append(tok)
        i += 1
    out.extend(extra)
    return out


def _drop_args(args, drop_flags):
    """Remove flags (and their values) listed in drop_flags. Bare flags OK."""
    drop = {tok for tok in (drop_flags or []) if isinstance(tok, str) and tok.startswith("--")}
    if not drop:
        return list(args)
    out = []
    i = 0
    base = list(args)
    while i < len(base):
        tok = base[i]
        if tok in drop:
            i += 1
            if i < len(base) and not str(base[i]).startswith("-"):
                i += 1
            continue
        out.append(tok)
        i += 1
    return out


def new_stack_tokens(args):
    """NEW-only switches actually present in a CMD (for dry-run proof)."""
    found = []
    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "--skill-api-prompt":
            found.append(tok)
        elif tok == "--skill-params":
            found.append(tok)
        elif tok == "--chain":
            found.append(tok)
        elif tok == "--governance":
            val = args[i + 1] if i + 1 < len(args) else ""
            found.append(f"{tok} {val}")
        elif tok == "--governance-capacity":
            val = args[i + 1] if i + 1 < len(args) else ""
            found.append(f"{tok} {val}")
        elif tok == "--contribution-mode" and i + 1 < len(args) and args[i + 1] == "exec":
            found.append(f"{tok} exec")
        elif tok == "--planner-v2":
            found.append(tok)
        i += 1
    return found


def build_args(job, mc_port, br_port, plan, resume):
    arm = job["voyager_arm"]
    trial = job["trial"]
    suffix = job["suffix"]
    args = [sys.executable, "-B", "run_voyager.py", "--arm", arm, "--trial", str(trial),
            "--mc-port", str(mc_port), "--server-port", str(br_port),
            "--ckpt-suffix", suffix, "--api-key-env", plan["key_env"]]
    args += COMMON
    args[args.index("--max-iterations") + 1] = str(plan["max_iter"])
    if plan["model"]:
        args += ["--model-name", plan["model"]]
    if plan["api_base"]:
        args += ["--api-base", plan["api_base"]]
    if plan.get("providers"):
        args += ["--provider-order", plan["providers"]]
    extra = job.get("extra_args")
    if extra is None:
        extra = plan.get("extra_args")
    args = _apply_extra_args(args, extra)
    drop = job.get("drop_args")
    if drop is None:
        drop = plan.get("drop_args")
    args = _drop_args(args, drop)
    if resume:
        args += ["--resume"]
    return args


def start_worker(out_dir, tag, args, env):
    wlog = open(out_dir / f"{tag}.log", "ab", buffering=0)
    w = subprocess.Popen(args, env=env, cwd=str(ROOT), stdout=wlog, stderr=wlog, start_new_session=True)
    (out_dir / f"{tag}.pid").write_text(str(w.pid))
    return w


def stop_proc(p, grace=30):
    if p is None or p.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        p.wait(timeout=grace)
    except Exception:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", choices=sorted(PLANS))
    ap.add_argument("--machine", default=None, help="覆盖机器判断（admin1 / slac002）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--only", help="只跑这些 tag，逗号分隔，如 A1,B1 或 OLD1,NEW1")
    exclusive = ap.add_mutually_exclusive_group()
    exclusive.add_argument(
        "--only-jobs",
        help="只起这些 tag（任意机器，忽略分配表），逗号分隔，如 OLD1,NEW1；与 --machine-jobs 互斥",
    )
    exclusive.add_argument(
        "--machine-jobs", nargs="?", const="auto", default=None, metavar="MACHINE",
        help="只起分配给该机器的 job；不传值则用 --machine / 自动检测；与 --only-jobs 互斥",
    )
    a = ap.parse_args()
    if a.status:
        for d in sorted((ROOT / "runs_0911").glob("*")):
            for pf in sorted(d.glob("*.pid")):
                pid = read_pidfile(pf)
                print(f"{d.name}/{pf.stem}: pid={pid} alive={pid_alive(pid)}")
            for mk in sorted(d.glob("*.DONE")) + sorted(d.glob("*.FAIL")):
                print(f"{d.name}/{mk.name}: {mk.read_text().strip()[:200]}")
        return
    if not a.plan:
        ap.error("--plan 或 --status")
    plan = PLANS[a.plan]
    out_dir = ROOT / "runs_0911" / a.plan
    out_dir.mkdir(parents=True, exist_ok=True)

    lp = out_dir / "launcher.pid"
    old = read_pidfile(lp)
    if old and pid_alive(old) and old != os.getpid():
        print(f"🔴 已有调度器在跑（pid {old}），退出。")
        return
    lp.write_text(str(os.getpid()))

    ports = load_ports()
    machine = detect_machine(ports, a.machine)
    mcfg = ports["machines"][machine]
    lane_list, parallel = plan_lanes_and_parallel(plan, mcfg, machine)
    java = find_java17(mcfg)
    if not java:
        log(out_dir, "🔴 找不到 java 17，拒绝起跑（slac002 默认 21 会崩 MC）")
        return
    if a.dry_run:
        env = os.environ.copy()
    else:
        env = build_env(plan["key_env"])
    all_jobs = [normalize_job(j, plan) for j in plan["jobs"]]
    alloc = format_alloc(all_jobs)
    log(out_dir, "ALLOC " + json.dumps(alloc, ensure_ascii=False))
    jobs = select_jobs(
        all_jobs,
        only_jobs=a.only_jobs,
        machine_jobs=a.machine_jobs,
        machine=machine,
        only=a.only,
    )
    if a.only_jobs:
        log(out_dir, f"ONLY_JOBS keep={parse_job_tags(a.only_jobs)} tags={[job_tag(j) for j in jobs]}")
    elif a.machine_jobs is not None:
        want = machine if a.machine_jobs == "auto" else a.machine_jobs
        log(out_dir, f"MACHINE_JOBS want={want} tags={[job_tag(j) for j in jobs]}")
    log(out_dir, f"PLAN {a.plan} machine={machine} ports={PORTS_FILE.name} lanes={[l[0] for l in lane_list]} "
                 f"parallel={parallel} jobs={[job_tag(j) for j in jobs]} model={plan['model'] or 'default(deepseek-v4-flash→官方路由)'} "
                 f"api_base={plan['api_base'] or 'default'} providers={plan.get('providers') or '-'} java={java} TZ=UTC")

    queue = list(jobs)
    running = []
    busy_lanes = set()

    def free_lane(want=None):
        for lane, mc, br in lane_list:
            if lane in busy_lanes:
                continue
            if want is not None and int(lane) != int(want):
                continue
            return lane, mc, br
        return None

    def launch(job, lane, mc_port, br_port, resume=False):
        arm = job["voyager_arm"]
        trial = job["trial"]
        tag = job_tag(job)
        ckpt = ckpt_name(arm, trial, job["suffix"])
        seed = job_seed(job)
        pf = out_dir / f"{tag}.pid"
        pid = read_pidfile(pf)
        if pid and pid_alive(pid):
            log(out_dir, f"SKIP {tag} 已在跑 pid={pid}")
            return None
        if (out_dir / f"{tag}.DONE").exists():
            log(out_dir, f"SKIP {tag} 已 DONE")
            return None
        meta = run_meta(ckpt)
        if meta.get("stopped_reason") in TERMINAL_REASONS and not resume:
            log(out_dir, f"SKIP {tag} ckpt 已终态 stopped_reason={meta.get('stopped_reason')}")
            (out_dir / f"{tag}.DONE").write_text(json.dumps(meta.get("stopped_reason")))
            return None
        if (ROOT / ckpt).exists() and not resume:
            resume = True
            log(out_dir, f"RESUME {tag} 发现未终态 ckpt，改为 --resume（绝不 rm -rf）")
        for p in (mc_port, br_port):
            if not port_free(p):
                log(out_dir, f"FAIL {tag} lane{lane} 端口 {p} 被占（不是本调度器的作业）")
                (out_dir / f"{tag}.FAIL").write_text(f"port {p} busy {utc_now()}")
                return None
        args = build_args(job, mc_port, br_port, plan, resume)
        if a.dry_run:
            log(out_dir, f"DRY {tag} lane{lane} mc={mc_port} bridge={br_port} resume={resume} seed={seed} ckpt={ckpt} rq={job['rq']}")
            log(out_dir, f"CMD {tag} {' '.join(args)}")
            log(out_dir, f"NEW_STACK {tag} {new_stack_tokens(args) or 'none'}")
            return None
        srv, err = start_mc(out_dir, tag, mc_port, seed, java, env, op_bot=bool(plan.get("op_bot")))
        if err:
            log(out_dir, f"FAIL {tag} {err}")
            (out_dir / f"{tag}.FAIL").write_text(f"{err} {utc_now()}")
            return None
        w = start_worker(out_dir, tag, args, env)
        busy_lanes.add(lane)
        log(out_dir, f"START {tag} lane{lane} pid={w.pid} mc={mc_port} bridge={br_port} resume={resume} seed={seed} ckpt={ckpt}")
        log(out_dir, f"CMD {tag} {' '.join(args)}")
        return {"tag": tag, "job": job, "arm": arm, "trial": trial, "lane": lane, "mc": mc_port, "br": br_port,
                "proc": w, "srv": srv, "ckpt": ckpt}

    def finish(running_job):
        code = running_job["proc"].returncode
        stop_proc(running_job["srv"])
        busy_lanes.discard(running_job["lane"])
        meta = run_meta(running_job["ckpt"])
        reason, it = meta.get("stopped_reason"), meta.get("stopped_at_iteration")
        tag = running_job["tag"]
        rp = out_dir / f"{tag}.resumes"
        n_res = int(rp.read_text() or 0) if rp.exists() else 0
        extra = (f"probe_missing={meta.get('probe_missing_rounds')} model_returned={meta.get('first_model_returned')} "
                 f"providers={meta.get('providers_seen')} thinking_actual={meta.get('thinking_actual')} "
                 f"reasoning_leak={meta.get('reasoning_leak_calls')}")
        if reason in TERMINAL_REASONS:
            (out_dir / f"{tag}.DONE").write_text(json.dumps({"reason": reason, "at": it, "exit": code, "ts": utc_now()}))
            log(out_dir, f"DONE {tag} {'OK' if reason == 'iteration_limit' else 'BAD'} exit={code} stopped_reason={reason} at={it} {extra}")
            return None
        if n_res >= MAX_RESUME:
            (out_dir / f"{tag}.FAIL").write_text(f"exit={code} reason={reason} resumes={n_res} {utc_now()}")
            log(out_dir, f"FAIL {tag} 非终态退出且续跑次数已达 {MAX_RESUME}：exit={code} reason={reason} {extra}")
            return None
        rp.write_text(str(n_res + 1))
        log(out_dir, f"RESUME {tag} 非终态退出 exit={code} reason={reason}，第 {n_res + 1}/{MAX_RESUME} 次续跑（30s 后）{extra}")
        time.sleep(30)
        return launch(running_job["job"], running_job["lane"], running_job["mc"], running_job["br"], resume=True)

    try:
        while queue or running:
            while queue and len(running) < parallel:
                want = queue[0].get("lane")
                if want is not None:
                    allowed = {int(row[0]) for row in lane_list}
                    if int(want) not in allowed:
                        job = queue.pop(0)
                        log(out_dir, f"FAIL {job_tag(job)} 指定 lane{want} 不在本 plan 的 lane 表 {sorted(allowed)}")
                        (out_dir / f"{job_tag(job)}.FAIL").write_text(
                            f"lane {want} not in {sorted(allowed)} {utc_now()}"
                        )
                        continue
                slot = free_lane(want)
                if slot is None:
                    break
                job = queue.pop(0)
                j = launch(job, *slot)
                if j:
                    running.append(j)
                if not a.dry_run:
                    time.sleep(5)
            if a.dry_run and not running:
                if not queue:
                    break
                continue
            time.sleep(20)
            for j in list(running):
                if j["proc"].poll() is not None:
                    running.remove(j)
                    nj = finish(j)
                    if nj:
                        running.append(nj)
        rows = []
        for job in jobs:
            m = run_meta(ckpt_name(job["voyager_arm"], job["trial"], job["suffix"]))
            rows.append((job_tag(job), m.get("stopped_reason"), m.get("stopped_at_iteration"),
                         m.get("first_model_returned"), m.get("providers_seen"), m.get("thinking_actual"),
                         m.get("probe_missing_rounds")))
        log(out_dir, "SUMMARY " + json.dumps(rows, ensure_ascii=False))
    finally:
        try:
            lp.unlink()
        except Exception:
            pass


if __name__ == "__main__":
    main()
