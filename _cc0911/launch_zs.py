#!/usr/bin/env python3
"""v29 零样本启动器：一次尝试 = 一个作业 = 一个全新世界（种子 20260902099）。

复用 launch_0911.py 的端口表、java、环境、起服、停服、日志，不改动 launch_0911.py。

用法（仓库根目录）：
    python _cc0911/launch_zs.py --plan zs_existing_v41 --dry-run
    python _cc0911/launch_zs.py --plan zs_existing_v41                 # 只起分配给本机的作业
    python _cc0911/launch_zs.py --plan zs_existing_v41 --ignore-machine  # 另一台机器不在时，本机全包
    python _cc0911/launch_zs.py --plan zs_existing_v41 --ignore-machine --trials-max 1  # 只跑每题第 1 次（32 个）
    python _cc0911/launch_zs.py --plan zs_smoke_v41                     # 1 个库 × 1 个任务 × 5 步
    python _cc0911/launch_zs.py --status --plan zs_existing_v41

规矩：
  * 作业标签唯一 → start_mc 从 mc_template 复制一个新服务器目录，也就是一个全新世界。
  * 作业结束后删掉 <tag>_server（C 盘只剩约 30G，每个世界 140MB 起），日志保留。
  * 成功判定看结果文件里 attempt_id == <tag> 的那一行：stopped_reason 为 success 或 cap 记 DONE；
    否则删掉世界、换新世界重试，最多 MAX_RETRY 次，再不行记 FAIL。
  * 作业按 (任务, 次数, 库) 交错排序后按各机 lane 数轮流分配，两台机器分到的各臂比例相同，
    机器快慢不会和臂混在一起。
  * 幂等：<tag>.DONE / <tag>.FAIL / <tag>.pid / launcher.pid，与 launch_0911 相同。
"""
import argparse
import importlib.util
import json
import os
import pathlib
import shutil
import sys
import time

_HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("launch_0911", _HERE / "launch_0911.py")
L = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(L)

ROOT = L.ROOT
ZS_SEED = L.SEED_BASE + 99          # 20260902099，预注册 v2 第 143 行
MAX_RETRY = 2
OK_REASONS = {"success", "cap", "subgoal_cap_final"}
RESULTS_NAME = "zeroshot_results.jsonl"

TASKS = [
    ("dp", "Craft 1 diamond pickaxe"),
    ("gs", "Craft 1 golden sword"),
    ("lb", "Collect 1 lava bucket"),
    ("cp", "Craft 1 compass"),
]
# 09-25 压噪：去掉熔岩题（可选，见方案第 6 节）。三题共用合成链。
TASKS_0925 = [
    ("dp", "Craft 1 diamond pickaxe"),
    ("gs", "Craft 1 golden sword"),
    ("cp", "Craft 1 compass"),
]
SYS_V30_FLAGS = [
    "--v30-named-actions",
    "--v30-verify-critic",
    "--v30-shared-fixes",
]
LIB_ROOT = {
    "admin1": "/mnt/e/homework/research/voyager/_audit_0911",
    "slac002": "/home/user/zs_libs/_audit_0911",   # 周一起跑前把用到的 skill/ 目录同步过去
    "local": str(ROOT / "libs"),   # 其他机器（--machine local）：仓库自带的 libs/，含 OLD_w1
}
# library_id → (预设, 相对 LIB_ROOT 的 ckpt 目录；None = 空库)
LIBS_EXISTING_V41 = {
    "OLD_w1": ("OLD", "v24_final/slac002_v26/ckpt_A_flash_off_t1_v26OLD"),
    "OLD_w2": ("OLD", "v24_final/slac002_v26/ckpt_A_flash_off_t2_v26OLD"),
    "OLD_w3": ("OLD", "v24_final/local/ckpt_A_flash_off_t3_v24OLD"),
    "C1p_w1": ("C1p", "v28_final/local/ckpt_A_flash_off_t1_v28NEW"),
    "C1p_w2": ("C1p", "v28_final/local/ckpt_A_flash_off_t2_v28NEW"),
    "C1p_w3": ("C1p", "v28_final/local/ckpt_A_flash_off_t3_v28NEW"),
    "EMPTY_OLD": ("OLD", None),
    "EMPTY_C1p": ("C1p", None),
}

# 09-22：离线整理出的「理想库」。preset 仍是 OLD（检索 top-5 / static / 整理关），
# 只换库——和 OLD_w1/w2/w3 构成同系统内的整理开/关对照。
# 09-22 基础层「点名」臂：库与原版臂逐字节相同（未整理的三个原始库），
# 变的是把两个提炼技能塞进基础动作层**并写进提示词第 2 条那张名单**。
LIBS_BASE_0922 = {
    "BASE_w1": LIBS_EXISTING_V41["OLD_w1"],
    "BASE_w2": LIBS_EXISTING_V41["OLD_w2"],
    "BASE_w3": LIBS_EXISTING_V41["OLD_w3"],
}
LIBS_IDEAL_0922 = {
    "IDEAL_w1": ("OLD", "curated_0922/IDEAL_w1"),
    "IDEAL_w2": ("OLD", "curated_0922/IDEAL_w2"),
    "IDEAL_w3": ("OLD", "curated_0922/IDEAL_w3"),
}

# 只上两个：killMobWithWeapon 在这 4 道题里出现 0 次（强制 peaceful、全是合成/挖矿）。
BASE_SKILLS_0922 = ["craftWithTable", "mineWithTool"]

PLANS = {
    # 09-22「点名」臂。跑在 V29_named 这份副本里，正式 lane 被理想库批占着，
    # 所以借 smoke lane；理想库跑完再重起拿满 lane（有 .DONE 标记，幂等续跑）。
    "zs_base_named_0922": dict(
        libs=LIBS_BASE_0922, tasks=TASKS, trials=3, max_rounds=50,
        model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        smoke=False, op_bot=True, machine_weights={"slac002": 1},
        include_smoke_lanes_on=("slac002",),
        parallel_by_machine={"slac002": 4},   # 09-23：理想库批已停、lane 0-3 空出，扩到 4（原为 1）
        extra_base_skills=BASE_SKILLS_0922,
    ),
    # 09-22：理想库零样本。3 库 × 4 题 × 3 次 = 36 次。本机 WSL 出不了网，全压 slac002。
    "zs_ideal_0922": dict(
        libs=LIBS_IDEAL_0922, tasks=TASKS, trials=3, max_rounds=50,
        model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        smoke=False, op_bot=True, machine_weights={"slac002": 4},
    ),
    # 周一：现成库的零样本（强模型，官方 V4.1）。8 个库（含两个空库）× 4 任务 × 3 次 = 96 次尝试。
    "zs_existing_v41": dict(
        libs=LIBS_EXISTING_V41, tasks=TASKS, trials=3, max_rounds=50,
        model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        smoke=False, op_bot=True, machine_weights={"admin1": 3, "slac002": 4},
    ),
    # 真机小测：原版世界 1 的库 × 做金剑 × 1 次，5 步，本机 smoke lane。
    "zs_smoke_v41": dict(
        libs={"OLD_w1": LIBS_EXISTING_V41["OLD_w1"]}, tasks=[TASKS[1]], trials=1, max_rounds=5,
        model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        smoke=True, op_bot=True, machine_weights={"admin1": 1},
    ),
    # 09-25 A/A 标定 σ：OLD 预设（与 09-21 96 次同参）+ --zs-noise-fix。
    # 1 库 × 3 题 × 8 次 = 24。只写计划，不起跑。
    # 只用本机 admin1。slac002 不用（不跑 dry-run、不排作业）。
    "zs_aa_0925": dict(
        libs={"OLD_w1": LIBS_EXISTING_V41["OLD_w1"]}, tasks=TASKS_0925, trials=8, max_rounds=50,
        model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        smoke=False, op_bot=True, lane_host="admin1", machine_weights={"admin1": 3},
        zs_noise_fix=True,
    ),
    # 09-25 试点：OLD vs SYS，两臂都 --zs-noise-fix。SYS = v5.1 named-actions
    # + verify-critic + shared-fixes，不开 anti-stall。3 库 × 3 题 × 3 次/臂 = 27/臂。
    # 只用本机 admin1。slac002 不用。
    "zs_pilot_0925": dict(
        libs={
            "OLD_w1": LIBS_EXISTING_V41["OLD_w1"],
            "OLD_w2": LIBS_EXISTING_V41["OLD_w2"],
            "OLD_w3": LIBS_EXISTING_V41["OLD_w3"],
            "SYS_w1": LIBS_EXISTING_V41["OLD_w1"],
            "SYS_w2": LIBS_EXISTING_V41["OLD_w2"],
            "SYS_w3": LIBS_EXISTING_V41["OLD_w3"],
        },
        tasks=TASKS_0925, trials=3, max_rounds=50,
        model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        smoke=False, op_bot=True, lane_host="admin1", machine_weights={"admin1": 3},
        zs_noise_fix=True,
        v30_flags_for_lib_prefix={"SYS_": SYS_V30_FLAGS},
        # slac002 不用：A/A 与试点只在 admin1。
    ),    # 09-26 规则 A 试点（09-25 晚授权：天花板单测达标 + 审查无必须修项才起）。
    # 两臂同一世界种子、同一库 OLD_w1、都开 --zs-noise-fix 与 --v30-shared-fixes（"两臂共修"）；
    # 单次执行时限两臂都是 600 s（_COMMON_KWARGS）。SYS 另加点名动作 + obtain（不开核对器）。
    # 3 题 × 8 次 × 2 臂 = 48 局，本机 3 路。
    "zs_ruleA_pilot_0926": dict(
        libs={
            "OLD_w1": LIBS_EXISTING_V41["OLD_w1"],
            "SYS_w1": LIBS_EXISTING_V41["OLD_w1"],
        },
        tasks=TASKS_0925, trials=8, max_rounds=50,
        model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        smoke=False, op_bot=True, lane_host="admin1", machine_weights={"admin1": 3},
        zs_noise_fix=True,
        v30_flags_for_lib_prefix={
            "OLD_": ["--v30-shared-fixes"],
            # 09-26 目标审计（Codex）：核对器只给 SYS 会改变反馈/终止＝混淆 → 本试点不开；
            # 处理因素只剩"点名动作 + obtain"。两臂都用原版 critic。
            # 09-26 pilot v3 (Qwen pv2 must-fix 1.1): SYS had been launched without --v30-shared-fixes
            "SYS_": ["--v30-shared-fixes", "--v30-named-actions", "--extra-base-skills", "obtain"],
        },
    ),
    # 09-27 规则 A SYSGOV：SYS + 治理叶子层（在线）。同一世界种子、同一库 OLD_w1、同上限。
    # 1 库 × 3 题 × 8 次 = 24 局，本机 3 路。只写计划；真跑须另发任务书。
    # 09-28 E4（预注册_真实循环未见种子_2026-09-28）：SYS vs SYSGOV 换到 5 个未见评估种子，同一代码 ~/wt_v30_e4、
    # 同一库 OLD_w1、规则 A、每 (种子, 题, 臂) 1 局 = 30 局，本机 3 路。未见种子从各自的地表出生点起跑。
    "zs_e4_unseen_0928": dict(
        libs={"SYS_w1": LIBS_EXISTING_V41["OLD_w1"], "SYSGOV_w1": LIBS_EXISTING_V41["OLD_w1"]},
        tasks=TASKS_0925, trials=1, max_rounds=50,
        world_seeds=[20260902007, 20260902008, 20260902009, 20260902012, 20260902013],
        model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        smoke=False, op_bot=True, lane_host="admin1", machine_weights={"admin1": 3},
        zs_noise_fix=True,
        v30_flags_for_lib_prefix={
            "SYS_": ["--v30-shared-fixes", "--v30-named-actions", "--extra-base-skills", "obtain"],
            "SYSGOV_": [
                "--v30-shared-fixes", "--v30-named-actions",
                "--extra-base-skills", "obtain",
                "--gov-pool", str(ROOT / "_gov" / "pool_system.json"),
                "--gov-online",
            ],
        },
    ),
    "zs_e4_smoke_0928": dict(
        libs={"SYSGOV_w1": LIBS_EXISTING_V41["OLD_w1"]},
        tasks=[TASKS_0925[1]], trials=1, max_rounds=3, world_seeds=[20260902007],
        model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        smoke=False, op_bot=True, lane_host="admin1", machine_weights={"admin1": 3},
        zs_noise_fix=True,
        v30_flags_for_lib_prefix={
            "SYSGOV_": [
                "--v30-shared-fixes", "--v30-named-actions",
                "--extra-base-skills", "obtain",
                "--gov-pool", str(ROOT / "_gov" / "pool_system.json"),
                "--gov-online",
            ],
        },
    ),
    # 09-29 E8（预注册_真实循环不发物品_2026-09-29）：OLD / SYS / SYSGOV 在 E4 的 5 个未见种子上，规则 A 去掉
    # "卡住就发物品"：--zs-noise-fix 的另外四项逐项打开，不开 --zs-subgoal-cap-on（没有子目标上限、不发物品），
    # 上限 50 轮；其余与 E4 相同（同库 OLD_w1、同模型、同出生点探测）。3 臂 × 5 种子 × 3 题 = 45 局，本机 3 路。
    "zs_e8_nogrant_0929": dict(
        libs={"OLD_w1": LIBS_EXISTING_V41["OLD_w1"], "SYS_w1": LIBS_EXISTING_V41["OLD_w1"],
              "SYSGOV_w1": LIBS_EXISTING_V41["OLD_w1"]},
        tasks=TASKS_0925, trials=1, max_rounds=50,
        world_seeds=[20260902007, 20260902008, 20260902009, 20260902012, 20260902013],
        model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        smoke=False, op_bot=True, lane_host="admin1", machine_weights={"admin1": 3},
        zs_noise_flags=["--zs-place-judge", "--zs-count-judge", "--zs-canonical-subgoals", "--zs-fixed-start"],
        v30_flags_for_lib_prefix={
            "OLD_": ["--v30-shared-fixes"],
            "SYS_": ["--v30-shared-fixes", "--v30-named-actions", "--extra-base-skills", "obtain"],
            "SYSGOV_": [
                "--v30-shared-fixes", "--v30-named-actions",
                "--extra-base-skills", "obtain",
                "--gov-pool", str(ROOT / "_gov" / "pool_system.json"),
                "--gov-online",
            ],
        },
    ),
    # E8 冒烟：OLD、金剑、种子 007、上限 3 轮（看不发物品的旗标、出生点、结果行字段）。
    "zs_e8_smoke_0929": dict(
        libs={"OLD_w1": LIBS_EXISTING_V41["OLD_w1"]},
        tasks=[TASKS_0925[1]], trials=1, max_rounds=3, world_seeds=[20260902007],
        model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        smoke=False, op_bot=True, lane_host="admin1", machine_weights={"admin1": 3},
        zs_noise_flags=["--zs-place-judge", "--zs-count-judge", "--zs-canonical-subgoals", "--zs-fixed-start"],
        v30_flags_for_lib_prefix={"OLD_": ["--v30-shared-fixes"]},
    ),
    # 09-29 E9（方案_E9_循环内完整生命周期）：谱系按在线组的局序一局一局跑（外层 e9 谱系脚本每局前长叶子、传账本文件）。
    "zs_e9g_life_L1": dict(
        libs={"SYSGOV_w1": LIBS_EXISTING_V41["OLD_w1"]}, tasks=TASKS_0925, trials=1, max_rounds=50,
        cells=[(20260902008, "cp"), (20260902013, "cp"), (20260902012, "dp"), (20260902009, "dp"), (20260902013, "gs"), (20260902013, "dp"), (20260902007, "cp"), (20260902009, "gs"), (20260902007, "gs"), (20260902008, "dp"), (20260902007, "dp"), (20260902012, "cp"), (20260902008, "gs"), (20260902009, "cp"), (20260902012, "gs")],
        model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        smoke=False, op_bot=True, lane_host="admin1", machine_weights={"admin1": 3}, lanes_only=[3],
        zs_noise_fix=True,
        v30_flags_for_lib_prefix={"SYSGOV_": ["--v30-shared-fixes", "--v30-named-actions", "--extra-base-skills", "obtain",
                                              "--gov-pool", str(ROOT / "runs_0911" / "e9g_life_L1" / "_gov" / "pool_e3.json"),
                                              "--gov-online"]},
    ),
    "zs_e9g_life_L2": dict(
        libs={"SYSGOV_w1": LIBS_EXISTING_V41["OLD_w1"]}, tasks=TASKS_0925, trials=1, max_rounds=50,
        cells=[(20260902012, "dp"), (20260902008, "cp"), (20260902007, "dp"), (20260902008, "dp"), (20260902013, "gs"), (20260902013, "dp"), (20260902007, "gs"), (20260902013, "cp"), (20260902007, "cp"), (20260902009, "gs"), (20260902012, "gs"), (20260902008, "gs"), (20260902012, "cp"), (20260902009, "cp"), (20260902009, "dp")],
        model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        smoke=False, op_bot=True, lane_host="admin1", machine_weights={"admin1": 3}, lanes_only=[1],
        zs_noise_fix=True,
        v30_flags_for_lib_prefix={"SYSGOV_": ["--v30-shared-fixes", "--v30-named-actions", "--extra-base-skills", "obtain",
                                              "--gov-pool", str(ROOT / "runs_0911" / "e9g_life_L2" / "_gov" / "pool_e3.json"),
                                              "--gov-online"]},
    ),
    "zs_e9g_life_L3": dict(
        libs={"SYSGOV_w1": LIBS_EXISTING_V41["OLD_w1"]}, tasks=TASKS_0925, trials=1, max_rounds=50,
        cells=[(20260902012, "dp"), (20260902013, "dp"), (20260902007, "gs"), (20260902007, "cp"), (20260902008, "cp"), (20260902007, "dp"), (20260902013, "cp"), (20260902013, "gs"), (20260902009, "cp"), (20260902008, "gs"), (20260902008, "dp"), (20260902012, "cp"), (20260902012, "gs"), (20260902009, "dp"), (20260902009, "gs")],
        model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        smoke=False, op_bot=True, lane_host="admin1", machine_weights={"admin1": 3}, lanes_only=[2],
        zs_noise_fix=True,
        v30_flags_for_lib_prefix={"SYSGOV_": ["--v30-shared-fixes", "--v30-named-actions", "--extra-base-skills", "obtain",
                                              "--gov-pool", str(ROOT / "runs_0911" / "e9g_life_L3" / "_gov" / "pool_e3.json"),
                                              "--gov-online"]},
    ),
    "zs_e9g_life_L9": dict(  # smoke lineage: two development cells outside the evaluation grid, smoke lane 8
        libs={"SYSGOV_w1": LIBS_EXISTING_V41["OLD_w1"]}, tasks=TASKS_0925, trials=1, max_rounds=50,
        cells=[(20260902010, "gs"), (20260902011, "cp")],
        model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        smoke=True, op_bot=True, lane_host="admin1", machine_weights={"admin1": 3}, lanes_only=[8],
        zs_noise_fix=True,
        v30_flags_for_lib_prefix={"SYSGOV_": ["--v30-shared-fixes", "--v30-named-actions", "--extra-base-skills", "obtain",
                                              "--gov-pool", str(ROOT / "runs_0911" / "e9g_life_L9" / "_gov" / "pool_e3.json"),
                                              "--gov-online"]},
    ),
    "zs_e9g_sys": dict(
        libs={"SYS_w1": LIBS_EXISTING_V41["OLD_w1"]}, tasks=TASKS_0925, trials=3, max_rounds=50,
        cells=[(20260902007, "dp"), (20260902007, "gs"), (20260902007, "cp"), (20260902008, "dp"), (20260902008, "gs"), (20260902008, "cp"), (20260902009, "dp"), (20260902009, "gs"), (20260902009, "cp"), (20260902012, "dp"), (20260902012, "gs"), (20260902012, "cp"), (20260902013, "dp"), (20260902013, "gs"), (20260902013, "cp")],
        model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        smoke=False, op_bot=True, lane_host="admin1", machine_weights={"admin1": 3}, lanes_only=[1, 2, 3],
        zs_noise_fix=True,
        v30_flags_for_lib_prefix={"SYS_": ["--v30-shared-fixes", "--v30-named-actions", "--extra-base-skills", "obtain"]},
    ),
    "zs_e9n_life_L1": dict(
        libs={"SYSGOV_w1": LIBS_EXISTING_V41["OLD_w1"]}, tasks=TASKS_0925, trials=1, max_rounds=50,
        cells=[(20260902008, "cp"), (20260902013, "cp"), (20260902012, "dp"), (20260902009, "dp"), (20260902013, "gs"), (20260902013, "dp"), (20260902007, "cp"), (20260902009, "gs"), (20260902007, "gs"), (20260902008, "dp"), (20260902007, "dp"), (20260902012, "cp"), (20260902008, "gs"), (20260902009, "cp"), (20260902012, "gs")],
        model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        smoke=False, op_bot=True, lane_host="admin1", machine_weights={"admin1": 3}, lanes_only=[3],
        zs_noise_flags=["--zs-place-judge", "--zs-count-judge", "--zs-canonical-subgoals", "--zs-fixed-start"],
        v30_flags_for_lib_prefix={"SYSGOV_": ["--v30-shared-fixes", "--v30-named-actions", "--extra-base-skills", "obtain",
                                              "--gov-pool", str(ROOT / "runs_0911" / "e9n_life_L1" / "_gov" / "pool_e3.json"),
                                              "--gov-online"]},
    ),
    "zs_e9n_life_L2": dict(
        libs={"SYSGOV_w1": LIBS_EXISTING_V41["OLD_w1"]}, tasks=TASKS_0925, trials=1, max_rounds=50,
        cells=[(20260902012, "dp"), (20260902008, "cp"), (20260902007, "dp"), (20260902008, "dp"), (20260902013, "gs"), (20260902013, "dp"), (20260902007, "gs"), (20260902013, "cp"), (20260902007, "cp"), (20260902009, "gs"), (20260902012, "gs"), (20260902008, "gs"), (20260902012, "cp"), (20260902009, "cp"), (20260902009, "dp")],
        model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        smoke=False, op_bot=True, lane_host="admin1", machine_weights={"admin1": 3}, lanes_only=[1],
        zs_noise_flags=["--zs-place-judge", "--zs-count-judge", "--zs-canonical-subgoals", "--zs-fixed-start"],
        v30_flags_for_lib_prefix={"SYSGOV_": ["--v30-shared-fixes", "--v30-named-actions", "--extra-base-skills", "obtain",
                                              "--gov-pool", str(ROOT / "runs_0911" / "e9n_life_L2" / "_gov" / "pool_e3.json"),
                                              "--gov-online"]},
    ),
    "zs_e9n_life_L3": dict(
        libs={"SYSGOV_w1": LIBS_EXISTING_V41["OLD_w1"]}, tasks=TASKS_0925, trials=1, max_rounds=50,
        cells=[(20260902012, "dp"), (20260902013, "dp"), (20260902007, "gs"), (20260902007, "cp"), (20260902008, "cp"), (20260902007, "dp"), (20260902013, "cp"), (20260902013, "gs"), (20260902009, "cp"), (20260902008, "gs"), (20260902008, "dp"), (20260902012, "cp"), (20260902012, "gs"), (20260902009, "dp"), (20260902009, "gs")],
        model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        smoke=False, op_bot=True, lane_host="admin1", machine_weights={"admin1": 3}, lanes_only=[2],
        zs_noise_flags=["--zs-place-judge", "--zs-count-judge", "--zs-canonical-subgoals", "--zs-fixed-start"],
        v30_flags_for_lib_prefix={"SYSGOV_": ["--v30-shared-fixes", "--v30-named-actions", "--extra-base-skills", "obtain",
                                              "--gov-pool", str(ROOT / "runs_0911" / "e9n_life_L3" / "_gov" / "pool_e3.json"),
                                              "--gov-online"]},
    ),
    "zs_e9n_life_L9": dict(  # smoke lineage: two development cells outside the evaluation grid, smoke lane 8
        libs={"SYSGOV_w1": LIBS_EXISTING_V41["OLD_w1"]}, tasks=TASKS_0925, trials=1, max_rounds=50,
        cells=[(20260902010, "gs"), (20260902011, "cp")],
        model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        smoke=True, op_bot=True, lane_host="admin1", machine_weights={"admin1": 3}, lanes_only=[8],
        zs_noise_flags=["--zs-place-judge", "--zs-count-judge", "--zs-canonical-subgoals", "--zs-fixed-start"],
        v30_flags_for_lib_prefix={"SYSGOV_": ["--v30-shared-fixes", "--v30-named-actions", "--extra-base-skills", "obtain",
                                              "--gov-pool", str(ROOT / "runs_0911" / "e9n_life_L9" / "_gov" / "pool_e3.json"),
                                              "--gov-online"]},
    ),
    "zs_e9n_sys": dict(
        libs={"SYS_w1": LIBS_EXISTING_V41["OLD_w1"]}, tasks=TASKS_0925, trials=3, max_rounds=50,
        cells=[(20260902007, "dp"), (20260902007, "gs"), (20260902007, "cp"), (20260902008, "dp"), (20260902008, "gs"), (20260902008, "cp"), (20260902009, "dp"), (20260902009, "gs"), (20260902009, "cp"), (20260902012, "dp"), (20260902012, "gs"), (20260902012, "cp"), (20260902013, "dp"), (20260902013, "gs"), (20260902013, "cp")],
        model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        smoke=False, op_bot=True, lane_host="admin1", machine_weights={"admin1": 3}, lanes_only=[1, 2, 3],
        zs_noise_flags=["--zs-place-judge", "--zs-count-judge", "--zs-canonical-subgoals", "--zs-fixed-start"],
        v30_flags_for_lib_prefix={"SYS_": ["--v30-shared-fixes", "--v30-named-actions", "--extra-base-skills", "obtain"]},
    ),
    "zs_ruleA_sysgov_0927": dict(
        libs={"SYSGOV_w1": LIBS_EXISTING_V41["OLD_w1"]},
        tasks=TASKS_0925, trials=8, max_rounds=50,
        model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        smoke=False, op_bot=True, lane_host="admin1", machine_weights={"admin1": 3},
        zs_noise_fix=True,
        v30_flags_for_lib_prefix={
            "SYSGOV_": [
                "--v30-shared-fixes", "--v30-named-actions",
                "--extra-base-skills", "obtain",
                "--gov-pool", str(ROOT / "_gov" / "pool_system.json"),
                "--gov-online",
            ],
        },
    ),
    # 10-06 E12 (预注册_E12_E9新世界复现_2026-10-06): E9 on the 15 further seeds of E6, one cell per world.
    "zs_e12n_life_L1": dict(
        libs={"SYSGOV_w1": LIBS_EXISTING_V41["OLD_w1"]}, tasks=TASKS_0925, trials=1, max_rounds=50,
        cells=[(20260902342, "cp"), (20260902781, "dp"), (20260902937, "cp"), (20260902169, "dp"), (20260902574, "gs"), (20260902593, "cp"), (20260902546, "dp"), (20260902630, "gs"), (20260902714, "cp"), (20260902619, "dp"), (20260902474, "gs"), (20260902250, "gs"), (20260902491, "cp"), (20260902853, "gs"), (20260902397, "dp")],
        model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        smoke=False, op_bot=True, lane_host="admin1", machine_weights={"admin1": 3}, lanes_only=[3],
        zs_noise_flags=["--zs-place-judge", "--zs-count-judge", "--zs-canonical-subgoals", "--zs-fixed-start"],
        v30_flags_for_lib_prefix={"SYSGOV_": ["--v30-shared-fixes", "--v30-named-actions", "--extra-base-skills", "obtain",
                                              "--gov-pool", str(ROOT / "runs_0911" / "e12n_life_L1" / "_gov" / "pool_e3.json"),
                                              "--gov-online"]},
    ),
    "zs_e12n_life_L2": dict(
        libs={"SYSGOV_w1": LIBS_EXISTING_V41["OLD_w1"]}, tasks=TASKS_0925, trials=1, max_rounds=50,
        cells=[(20260902546, "dp"), (20260902619, "dp"), (20260902853, "gs"), (20260902781, "dp"), (20260902593, "cp"), (20260902491, "cp"), (20260902630, "gs"), (20260902714, "cp"), (20260902250, "gs"), (20260902937, "cp"), (20260902342, "cp"), (20260902397, "dp"), (20260902574, "gs"), (20260902169, "dp"), (20260902474, "gs")],
        model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        smoke=False, op_bot=True, lane_host="admin1", machine_weights={"admin1": 3}, lanes_only=[1],
        zs_noise_flags=["--zs-place-judge", "--zs-count-judge", "--zs-canonical-subgoals", "--zs-fixed-start"],
        v30_flags_for_lib_prefix={"SYSGOV_": ["--v30-shared-fixes", "--v30-named-actions", "--extra-base-skills", "obtain",
                                              "--gov-pool", str(ROOT / "runs_0911" / "e12n_life_L2" / "_gov" / "pool_e3.json"),
                                              "--gov-online"]},
    ),
    "zs_e12n_life_L3": dict(
        libs={"SYSGOV_w1": LIBS_EXISTING_V41["OLD_w1"]}, tasks=TASKS_0925, trials=1, max_rounds=50,
        cells=[(20260902593, "cp"), (20260902937, "cp"), (20260902169, "dp"), (20260902714, "cp"), (20260902474, "gs"), (20260902546, "dp"), (20260902619, "dp"), (20260902853, "gs"), (20260902250, "gs"), (20260902491, "cp"), (20260902342, "cp"), (20260902574, "gs"), (20260902397, "dp"), (20260902781, "dp"), (20260902630, "gs")],
        model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        smoke=False, op_bot=True, lane_host="admin1", machine_weights={"admin1": 3}, lanes_only=[2],
        zs_noise_flags=["--zs-place-judge", "--zs-count-judge", "--zs-canonical-subgoals", "--zs-fixed-start"],
        v30_flags_for_lib_prefix={"SYSGOV_": ["--v30-shared-fixes", "--v30-named-actions", "--extra-base-skills", "obtain",
                                              "--gov-pool", str(ROOT / "runs_0911" / "e12n_life_L3" / "_gov" / "pool_e3.json"),
                                              "--gov-online"]},
    ),
    "zs_e12n_life_L9": dict(
        libs={"SYSGOV_w1": LIBS_EXISTING_V41["OLD_w1"]}, tasks=TASKS_0925, trials=1, max_rounds=50,
        cells=[(20260902010, "gs"), (20260902011, "cp")],
        model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        smoke=True, op_bot=True, lane_host="admin1", machine_weights={"admin1": 3}, lanes_only=[8],
        zs_noise_flags=["--zs-place-judge", "--zs-count-judge", "--zs-canonical-subgoals", "--zs-fixed-start"],
        v30_flags_for_lib_prefix={"SYSGOV_": ["--v30-shared-fixes", "--v30-named-actions", "--extra-base-skills", "obtain",
                                              "--gov-pool", str(ROOT / "runs_0911" / "e12n_life_L9" / "_gov" / "pool_e3.json"),
                                              "--gov-online"]},
    ),
    "zs_e12n_sys": dict(
        libs={"SYS_w1": LIBS_EXISTING_V41["OLD_w1"]}, tasks=TASKS_0925, trials=3, max_rounds=50,
        cells=[(20260902169, "dp"), (20260902250, "gs"), (20260902342, "cp"), (20260902397, "dp"), (20260902474, "gs"), (20260902491, "cp"), (20260902546, "dp"), (20260902574, "gs"), (20260902593, "cp"), (20260902619, "dp"), (20260902630, "gs"), (20260902714, "cp"), (20260902781, "dp"), (20260902853, "gs"), (20260902937, "cp")],
        model=None, api_base=None, key_env="DEEPSEEK_API_KEY", providers="",
        smoke=False, op_bot=True, lane_host="admin1", machine_weights={"admin1": 3}, lanes_only=[1, 2, 3],
        parallel_by_machine={"local": 3},   # "local" has max_parallel 1; three lanes as E9's SYS (Qwen review 10-06)
        zs_noise_flags=["--zs-place-judge", "--zs-count-judge", "--zs-canonical-subgoals", "--zs-fixed-start"],
        v30_flags_for_lib_prefix={"SYS_": ["--v30-shared-fixes", "--v30-named-actions", "--extra-base-skills", "obtain"]},
    ),
}

# 10-09 E13 (预注册_E13_换小模型复现_2026-10-09): the five E12 plans with the local Qwen3.8-27B served by Ollama on this
# machine. Only the plan name, the run-directory prefix (e12n_ -> e13n_), the model, the API base (E13_API_BASE, set by
# go_e13.sh from the WSL gateway) and the key variable change.
def _e13_rename(v):
    if isinstance(v, str):
        return v.replace("e12n_", "e13n_")
    if isinstance(v, list):
        return [_e13_rename(x) for x in v]
    if isinstance(v, tuple):
        return tuple(_e13_rename(x) for x in v)
    if isinstance(v, dict):
        return {k: _e13_rename(x) for k, x in v.items()}
    return v


for _k in ("zs_e12n_life_L1", "zs_e12n_life_L2", "zs_e12n_life_L3", "zs_e12n_life_L9", "zs_e12n_sys"):
    _p = _e13_rename(PLANS[_k])
    _p.update(model="qwen3.8:27b", api_base=os.environ.get("E13_API_BASE", "http://e13-api-base-not-set/v1"),
              key_env="OLLAMA_API_KEY", providers="")
    PLANS[_k.replace("zs_e12n_", "zs_e13n_")] = _p

# 10-23 E16 (预注册_E16_小模型调用冻结叶子池_2026-10-23): the Qwen3.8-27B loop (local Ollama, as the E13 plans) with the
# frozen final pool of an E12 (QD) or E13 (QQ) lineage, ledger restarted every game, Rule A without grants; SYS reference =
# the E13 SYS games. The pools are the six E15 pool files.
PLANS["zs_e16_smoke"] = dict(
    libs={"QD1_w1": LIBS_EXISTING_V41["OLD_w1"], "QQ1_w1": LIBS_EXISTING_V41["OLD_w1"]}, tasks=TASKS_0925, trials=1, max_rounds=50,
    cells=[(20260902010, "gs")],
    model="qwen3.8:27b", api_base=os.environ.get("E13_API_BASE", "http://e13-api-base-not-set/v1"), key_env="OLLAMA_API_KEY", providers="",
    smoke=True, op_bot=True, lane_host="admin1", machine_weights={"admin1": 3}, lanes_only=[8, 9],
    zs_noise_flags=["--zs-place-judge", "--zs-count-judge", "--zs-canonical-subgoals", "--zs-fixed-start"],
    v30_flags_for_lib_prefix={"QD1_": ["--v30-shared-fixes", "--v30-named-actions", "--extra-base-skills", "obtain", "--gov-pool", str(ROOT / "_gov" / "pool_e16_e12_L1.json"), "--gov-online"], "QQ1_": ["--v30-shared-fixes", "--v30-named-actions", "--extra-base-skills", "obtain", "--gov-pool", str(ROOT / "_gov" / "pool_e16_e13_L1.json"), "--gov-online"]},
)
PLANS["zs_e16_main"] = dict(
    libs={"QD1_w1": LIBS_EXISTING_V41["OLD_w1"], "QQ1_w1": LIBS_EXISTING_V41["OLD_w1"], "QD2_w1": LIBS_EXISTING_V41["OLD_w1"], "QQ2_w1": LIBS_EXISTING_V41["OLD_w1"], "QD3_w1": LIBS_EXISTING_V41["OLD_w1"], "QQ3_w1": LIBS_EXISTING_V41["OLD_w1"]}, tasks=TASKS_0925, trials=1, max_rounds=50,
    cells=[(20260902169, "dp"), (20260902250, "gs"), (20260902342, "cp"), (20260902397, "dp"), (20260902474, "gs"), (20260902491, "cp"), (20260902546, "dp"), (20260902574, "gs"), (20260902593, "cp"), (20260902619, "dp"), (20260902630, "gs"), (20260902714, "cp"), (20260902781, "dp"), (20260902853, "gs"), (20260902937, "cp")],
    model="qwen3.8:27b", api_base=os.environ.get("E13_API_BASE", "http://e13-api-base-not-set/v1"), key_env="OLLAMA_API_KEY", providers="",
    smoke=False, op_bot=True, lane_host="admin1", machine_weights={"admin1": 3}, lanes_only=[1, 2, 3],
    parallel_by_machine={"slac002": 3},
    zs_noise_flags=["--zs-place-judge", "--zs-count-judge", "--zs-canonical-subgoals", "--zs-fixed-start"],
    v30_flags_for_lib_prefix={"QD1_": ["--v30-shared-fixes", "--v30-named-actions", "--extra-base-skills", "obtain", "--gov-pool", str(ROOT / "_gov" / "pool_e16_e12_L1.json"), "--gov-online"], "QQ1_": ["--v30-shared-fixes", "--v30-named-actions", "--extra-base-skills", "obtain", "--gov-pool", str(ROOT / "_gov" / "pool_e16_e13_L1.json"), "--gov-online"], "QD2_": ["--v30-shared-fixes", "--v30-named-actions", "--extra-base-skills", "obtain", "--gov-pool", str(ROOT / "_gov" / "pool_e16_e12_L2.json"), "--gov-online"], "QQ2_": ["--v30-shared-fixes", "--v30-named-actions", "--extra-base-skills", "obtain", "--gov-pool", str(ROOT / "_gov" / "pool_e16_e13_L2.json"), "--gov-online"], "QD3_": ["--v30-shared-fixes", "--v30-named-actions", "--extra-base-skills", "obtain", "--gov-pool", str(ROOT / "_gov" / "pool_e16_e12_L3.json"), "--gov-online"], "QQ3_": ["--v30-shared-fixes", "--v30-named-actions", "--extra-base-skills", "obtain", "--gov-pool", str(ROOT / "_gov" / "pool_e16_e13_L3.json"), "--gov-online"]},
)


def resolve_machine_weights(plan, lane_host=None):
    """A/A / 试点：只排本机 admin1。slac002 不用。

    `--lane-host slac002` 仍能解析（旧开关），但本轮 A/A 与试点不在 slac002
    跑 dry-run、不排作业。真跑若改机，须由规划者另发任务书。
    """
    host = (
        (lane_host if lane_host is not None else "")
        or os.environ.get("ZS_LANE_HOST")
        or plan.get("lane_host")
        or ""
    ).strip()
    if not host:
        return dict(plan["machine_weights"])
    if host in ("admin1", "local"):
        return {"admin1": 3}
    if host == "slac002":
        return {"slac002": 4}
    raise ValueError(f"unknown lane_host {host!r} (admin1|local|slac002)")


def build_jobs(plan):
    """(任务, 次数, 库) 交错，再按机器权重轮流分配。"""
    order = []
    seeds = plan.get("world_seeds")  # E4: per-job world seeds; absent -> ZS_SEED exactly as before
    if plan.get("cells"):  # E9: an explicit (world_seed, task_code) order; plans without "cells" are unchanged
        codes = dict(plan["tasks"])
        for trial in range(1, plan["trials"] + 1):
            for n, (seed, code) in enumerate(plan["cells"], 1):
                for lib_id, (preset, rel) in plan["libs"].items():
                    order.append(dict(tag=f"ZS_{lib_id}_g{n:02d}_{code}_s{str(seed)[-3:]}_t{trial}", lib_id=lib_id,
                                      preset=preset, rel=rel, task=codes[code], task_code=code, trial=trial,
                                      world_seed=seed))
        cycle = [m for m, w in plan["machine_weights"].items() for _ in range(int(w))]
        for i, job in enumerate(order):
            job["machine"] = cycle[i % len(cycle)]
        return order
    for trial in range(1, plan["trials"] + 1):
        for seed in (seeds or [None]):
            for code, task in plan["tasks"]:
                for lib_id, (preset, rel) in plan["libs"].items():
                    sfx = f"_s{str(seed)[-3:]}" if seed else ""
                    order.append(dict(tag=f"ZS_{lib_id}_{code}{sfx}_t{trial}", lib_id=lib_id, preset=preset, rel=rel,
                                      task=task, task_code=code, trial=trial, world_seed=seed or ZS_SEED))
    cycle = [m for m, w in plan["machine_weights"].items() for _ in range(int(w))]
    for i, job in enumerate(order):
        job["machine"] = cycle[i % len(cycle)]
    return order


def zs_args(job, mc_port, br_port, plan, machine, out_dir):
    args = [sys.executable, "-B", "run_zeroshot.py",
            "--arm", job["preset"], "--tasks", job["task"], "--trials", "1",
            "--trial-index", str(job["trial"]), "--max-rounds", str(plan["max_rounds"]),
            "--world-seed", str(job.get("world_seed") or ZS_SEED), "--mc-port", str(mc_port), "--server-port", str(br_port),
            "--out", str(out_dir / RESULTS_NAME), "--work-dir", str(out_dir / "work" / job["tag"]),
            "--attempt-id", job["tag"], "--library-id", job["lib_id"],
            "--api-key-env", plan["key_env"]]
    if job["rel"]:
        args += ["--library-src", f"{LIB_ROOT[machine]}/{job['rel']}"]
    else:
        args += ["--empty-library"]
    if plan["model"]:
        args += ["--model-name", plan["model"]]
    if plan["api_base"]:
        args += ["--api-base", plan["api_base"]]
    if plan.get("providers"):
        args += ["--provider-order", plan["providers"]]
    if plan.get("extra_base_skills"):
        # 不设这个键的计划一个字符都不多传，旧批次续跑的命令行逐字节不变。
        args += ["--extra-base-skills"] + list(plan["extra_base_skills"])
    if plan.get("zs_noise_fix") or job.get("zs_noise_fix"):
        args += ["--zs-noise-fix"]
    if plan.get("zs_noise_flags"):
        # E8 (no grants): the --zs-noise-fix switches one by one, without --zs-subgoal-cap-on. Plans without this key
        # build byte-identical command lines.
        args += list(plan["zs_noise_flags"])
    for prefix, flags in (plan.get("v30_flags_for_lib_prefix") or {}).items():
        if job["lib_id"].startswith(prefix):
            args += list(flags)
    return args


def results_offset(out_dir):
    """起一个作业前结果文件的长度。行都是加锁整行追加的，所以这个偏移量总在行边界上。"""
    path = out_dir / RESULTS_NAME
    try:
        return path.stat().st_size
    except OSError:
        return 0


def result_row(out_dir, tag, offset=0):
    """结果文件里 attempt_id == tag 的最后一行；没有就 None。

    只看 offset 之后追加的行：同一个 tag 重试时，上一次留下的旧行会把本次崩掉/
    被强停的作业误判成 DONE（09-20 Grok 复核指出）。文件比 offset 还短（被截短/轮换）时没法判断行属于谁，
    当成没结果（会重试），绝不凭它判 DONE。
    """
    path = out_dir / RESULTS_NAME
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if offset and len(text.encode("utf-8")) > offset:
        text = text.encode("utf-8")[offset:].decode("utf-8", "replace")
    elif offset:
        text = ""
    found = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("attempt_id") == tag:
            found = row
    return found


def drop_world(out_dir, tag):
    """删掉这个 tag 的世界目录，返回是否真的不在了。

    原来是 rmtree(ignore_errors=True)：删不掉（文件被占、权限）时默不作声，下一次重试会
    接着用上一次的脏世界跑（机器人进度、地形都还在），零样本就不是零样本了
    （09-20 Grok 复核指出）。现在：先真删，删不掉就改名挪开，两样都不成才返回 False。
    """
    lane = out_dir / f"{tag}_server"
    if not lane.exists():
        return True
    try:
        shutil.rmtree(lane)
    except OSError as exc:
        aside = out_dir / f"{tag}_server.stale.{int(time.time())}"
        try:
            lane.rename(aside)
        except OSError:
            L.log(out_dir, f"🔴 {tag} 世界目录既删不掉也挪不开：{lane}（{exc}）")
            return False
        L.log(out_dir, f"⚠️ {tag} 世界目录删不掉（{exc}），已改名挪开：{aside.name}（记得清磁盘）")
    return not lane.exists()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", choices=sorted(PLANS), required=True)
    ap.add_argument("--machine", default=None, help="覆盖机器判断（admin1 / slac002）")
    ap.add_argument(
        "--lane-host",
        default=None,
        choices=["admin1", "local", "slac002"],
        help="有 lane_host 的计划（A/A、试点）排哪台：只用 admin1。"
        " slac002 不用（保留开关但不跑）",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--ignore-machine", action="store_true", help="不看分配表，本机跑全部作业")
    ap.add_argument("--only-jobs", default=None, help="只起这些 tag，逗号分隔")
    ap.add_argument("--trials-max", type=int, default=None,
                    help="只起第 1..N 次的作业（作业本来就按次数排序，第 1 次的先跑完）")
    ap.add_argument("--job-timeout-h", type=float, default=4.0,
                    help="单个作业的墙钟上限（小时），超了强停并按失败重试；0 = 不限")
    a = ap.parse_args(argv)

    plan = dict(PLANS[a.plan])
    if a.lane_host or plan.get("lane_host"):
        plan["machine_weights"] = resolve_machine_weights(plan, a.lane_host)
    out_dir = ROOT / "runs_0911" / a.plan
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs = build_jobs(plan)

    if a.status:
        for job in jobs:
            tag = job["tag"]
            state = ("DONE" if (out_dir / f"{tag}.DONE").exists() else
                     "FAIL" if (out_dir / f"{tag}.FAIL").exists() else
                     "RUN" if L.pid_alive(L.read_pidfile(out_dir / f"{tag}.pid")) else "-")
            print(f"{tag:28s} {job['machine']:8s} {state}")
        return 0

    lp = out_dir / "launcher.pid"
    old = L.read_pidfile(lp)
    if old and L.pid_alive(old):
        print(f"🔴 已有调度器在跑（pid {old}），退出。")
        return 1
    lp.write_text(str(os.getpid()))

    ports = L.load_ports()
    machine = L.detect_machine(ports, a.machine)
    mcfg = ports["machines"][machine]
    lane_list, parallel = L.plan_lanes_and_parallel(plan, mcfg, machine)
    if plan.get("lanes_only") is not None:  # E9: a lineage keeps to its own lane; plans without the key are unchanged
        lane_list = [ln for ln in lane_list if ln[0] in set(plan["lanes_only"])]
        parallel = min(parallel, len(lane_list))
    java = L.find_java17(mcfg)
    if not java:
        L.log(out_dir, "🔴 找不到 java 17，拒绝起跑")
        lp.unlink()
        return 1
    env = os.environ.copy() if a.dry_run else L.build_env(plan["key_env"])
    # build_env() 一律剥掉 *_PROXY（09-11：当时直连可用、旧代理会挂死）。
    # 本机现在直连不通、只有代理能出网，所以留一个显式开关把它加回去。
    # 不设 ZS_EGRESS_PROXY 时这一段什么都不做，env 与改动前逐键相同。
    _proxy = os.environ.get("ZS_EGRESS_PROXY", "").strip()
    if _proxy:
        for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            env[_k] = _proxy
        L.log(out_dir, f"出网代理 {_proxy}（ZS_EGRESS_PROXY），no_proxy={env.get('no_proxy')}")


    if a.only_jobs:
        keep = {t.strip() for t in a.only_jobs.split(",") if t.strip()}
        jobs = [j for j in jobs if j["tag"] in keep]
    elif not a.ignore_machine:
        jobs = [j for j in jobs if j["machine"] == machine]
    if a.trials_max is not None:
        jobs = [j for j in jobs if j["trial"] <= a.trials_max]
    L.log(out_dir, f"PLAN {a.plan} machine={machine} lanes={[l[0] for l in lane_list]} parallel={parallel} "
                   f"jobs={len(jobs)} seed={ZS_SEED} model={plan['model'] or 'default'} java={java}")

    # 起跑前的两道检查（2026-09-20 复核发现：端口被占会让整个队列几分钟内全记 FAIL）
    if not a.dry_run:
        alive = [j["tag"] for j in jobs if L.pid_alive(L.read_pidfile(out_dir / f"{j['tag']}.pid"))]
        if alive:
            L.log(out_dir, f"🔴 还有 {len(alive)} 个作业在跑（{alive[:5]}），拒绝起跑：先等它们结束，或确认后清理 pid 文件")
            lp.unlink()
            return 1
        ok_lanes = [(ln, mc, br) for ln, mc, br in lane_list if L.port_free(mc) and L.port_free(br)]
        skipped = [ln for ln, mc, br in lane_list if (ln, mc, br) not in ok_lanes]
        if skipped:
            L.log(out_dir, f"⚠️ lane {skipped} 的端口被别的进程占着，本次不用这些 lane")
        lane_list = ok_lanes
        if not lane_list:
            L.log(out_dir, "🔴 所有 lane 的端口都被占，拒绝起跑（先查是谁占着）")
            lp.unlink()
            return 1
        parallel = min(parallel, len(lane_list))

    queue = [j for j in jobs if not (out_dir / f"{j['tag']}.DONE").exists()]
    running, busy, bad_lanes = [], set(), set()

    def launch(job, lane, mc, br):
        tag = job["tag"]
        if (out_dir / f"{tag}.DONE").exists() or (out_dir / f"{tag}.FAIL").exists():
            return None
        pid = L.read_pidfile(out_dir / f"{tag}.pid")
        if pid and L.pid_alive(pid):
            L.log(out_dir, f"SKIP {tag} 已在跑 pid={pid}")
            return None
        for p in (mc, br):
            if not L.port_free(p):
                # 不再把作业记 FAIL：停用这条 lane，作业放回队列换别的 lane
                L.log(out_dir, f"⚠️ lane{lane} 端口 {p} 被占：本次停用这条 lane，{tag} 放回队列")
                bad_lanes.add(lane)
                return {"port_busy": True}
        args = zs_args(job, mc, br, plan, machine, out_dir)
        if a.dry_run:
            L.log(out_dir, f"DRY {tag} lane{lane} mc={mc} bridge={br} seed={job.get('world_seed') or ZS_SEED}")
            L.log(out_dir, f"CMD {tag} {' '.join(args)}")
            return None
        if not drop_world(out_dir, tag):              # 保证是全新世界
            err = f"world_dir_not_removed: {out_dir / (tag + '_server')}"
            L.log(out_dir, f"FAIL {tag} {err}")
            return dict(job=job, lane=lane, mc=mc, br=br, proc=None, srv=None,
                        start_err=err, res_off=results_offset(out_dir))
        res_off = results_offset(out_dir)             # 只认这一刻之后写的结果行
        mc_log = out_dir / f"{tag}_minecraft.log"     # 旧日志里的 "Done (" 会骗过就绪检测
        if mc_log.exists():
            mc_log.rename(out_dir / f"{tag}_minecraft.log.{int(time.time())}")
        srv, err = L.start_mc(out_dir, tag, mc, job.get("world_seed") or ZS_SEED, java, env, op_bot=bool(plan.get("op_bot")))
        if err:
            L.log(out_dir, f"FAIL {tag} {err}")
            drop_world(out_dir, tag)
            return dict(job=job, lane=lane, mc=mc, br=br, proc=None, srv=None,
                        start_err=err, res_off=res_off)
        w = L.start_worker(out_dir, tag, args, env)
        busy.add(lane)
        L.log(out_dir, f"START {tag} lane{lane} pid={w.pid} mc={mc} bridge={br} seed={job.get('world_seed') or ZS_SEED}")
        L.log(out_dir, f"CMD {tag} {' '.join(args)}")
        return dict(job=job, lane=lane, mc=mc, br=br, proc=w, srv=srv, start_err=None,
                    res_off=res_off, t0=time.time())

    def finish(rj):
        job, tag = rj["job"], rj["job"]["tag"]
        code = rj["proc"].returncode if rj["proc"] is not None else None
        L.stop_proc(rj["srv"])
        busy.discard(rj["lane"])
        drop_world(out_dir, tag)
        row = result_row(out_dir, tag, rj.get("res_off", 0))
        reason = (row or {}).get("stopped_reason") or rj.get("start_err") or "no_result_row"
        if rj.get("timed_out"):
            reason = "job_timeout"
        elif row is not None and (row.get("library_changed") or code == 4):
            # 库副本或源库被改动过：这次的结果不能用（退出码 4）
            reason = "library_changed"
        if row is not None and reason in OK_REASONS:
            (out_dir / f"{tag}.DONE").write_text(json.dumps({"reason": reason, "exit": code, "ts": L.utc_now()}))
            L.log(out_dir, f"DONE {tag} reason={reason} rounds={row.get('rounds_used')} exit={code}")
            return None
        rp = out_dir / f"{tag}.retries"
        n = int(rp.read_text() or 0) if rp.exists() else 0
        if n >= MAX_RETRY:
            (out_dir / f"{tag}.FAIL").write_text(f"exit={code} reason={reason} retries={n} {L.utc_now()}")
            L.log(out_dir, f"FAIL {tag} reason={reason} 重试已达 {MAX_RETRY} 次")
            return None
        rp.write_text(str(n + 1))
        L.log(out_dir, f"RETRY {tag} reason={reason} exit={code}，换新世界第 {n + 1}/{MAX_RETRY} 次")
        return start(job, rj["lane"], rj["mc"], rj["br"])

    def start(job, lane, mc, br):
        """起一个作业；起服失败就当一次失败走 finish（它会换新世界重试或记 FAIL）。
        返回正在跑的作业，或 None（已 DONE / FAIL / 跳过 / dry-run）。"""
        rj = launch(job, lane, mc, br)
        if isinstance(rj, dict) and rj.get("port_busy"):
            return rj                                  # 调用方把作业放回队列，换一条 lane
        if rj is not None and rj["proc"] is None:
            return finish(rj)
        return rj

    job_timeout_s = float(a.job_timeout_h) * 3600
    try:
        while queue or running:
            while queue and len(running) < parallel:
                slot = next(((ln, mc, br) for ln, mc, br in lane_list
                             if ln not in busy and ln not in bad_lanes), None)
                if slot is None:
                    break
                job = queue.pop(0)
                rj = start(job, *slot)
                if isinstance(rj, dict) and rj.get("port_busy"):
                    queue.insert(0, job)          # 换一条 lane 再试，不记 FAIL
                    continue
                if rj:
                    running.append(rj)
                if not a.dry_run:
                    time.sleep(5)
            if queue and not running and not any(
                ln not in busy and ln not in bad_lanes for ln, _, _ in lane_list
            ):
                L.log(out_dir, f"🔴 没有可用 lane 了（被占：{sorted(bad_lanes)}），剩下 {len(queue)} 个作业没跑，先停")
                break
            if a.dry_run and not running:
                if not queue:
                    break
                continue
            time.sleep(20)
            for rj in list(running):
                if rj["proc"] is None:
                    continue
                over = job_timeout_s and (time.time() - rj.get("t0", time.time())) > job_timeout_s
                if over and rj["proc"].poll() is None:
                    L.log(out_dir, f"⚠️ {rj['job']['tag']} 超过单作业上限 {a.job_timeout_h} 小时，强停后按失败处理")
                    rj["timed_out"] = True
                    L.stop_proc(rj["proc"])
                if rj["proc"].poll() is not None:
                    running.remove(rj)
                    nj = finish(rj)
                    if nj:
                        running.append(nj)
        done = sum(1 for j in jobs if (out_dir / f"{j['tag']}.DONE").exists())
        failed = sum(1 for j in jobs if (out_dir / f"{j['tag']}.FAIL").exists())
        L.log(out_dir, f"SUMMARY jobs={len(jobs)} done={done} fail={failed}")
    finally:
        try:
            lp.unlink()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
