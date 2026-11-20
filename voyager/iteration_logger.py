import json
import os
from datetime import datetime


def append_iteration_metrics(
    ckpt_dir,
    *,
    iter=None,
    task=None,
    success=None,
    skill_count=None,
    retrieved_skills=None,
    n_completed=None,
    n_failed=None,
    action_latency_ms=None,
    curation_enabled=None,
    used_skills=None,
    used_skills_exec=None,
    # ---- 2026-09-11 审计新增字段（只追加，不改旧字段语义）----
    used_skills_nodefs=None,   # text_nodefs 判定（program_code+exec_code）
    used_skills_runtime=None,  # 运行时探针判定
    probe_installed=None,      # 哨兵回传值；None = 探针链路没跑到
    n_steps=None,              # 本任务消耗的 action step 数（含打回）
    parse_fail_model=None,     # 解析失败：模型代码问题
    parse_fail_infra=None,     # 解析失败：解析器基础设施故障（P0-2）
    crashed=None,              # 本任务 rollout 是否抛异常
    crash_kind=None,           # step_timeout / env_crash / None
    error=None,                # 异常类型与前 300 字
    executed_skills=None,      # 本任务 ok 的技能名
    executed_failed=None,      # 本任务执行过且全部失败的技能名
    executed_nested=None,      # 执行了但未检索到
    skill_credit=None,         # critic 给的 {name: bool}
    exec_args=None,            # 本任务最终执行的实参列表；skill_params 关时为 null
    chain_nodes=None,
    chain_reused=None,
    chain_gaps=None,
    chain_gap_failed=None,
    chain_node_ok=None,
    chain_route=None,          # reuse_only / gap_only / mixed / fallback
):
    """Append one crash-safe JSONL row with per-iteration metrics.

    口径提醒（审计 01 C-4 / 03 C-03）：一行一个任务；iter 是任务结束后的
    recorder.iteration（action step 计数，含打回）。崩溃行 recorder 不自增，
    iter 会与上一行相同，靠 crashed/crash_kind 区分。
    """
    metrics_dir = os.path.join(ckpt_dir, "metrics")
    os.makedirs(metrics_dir, exist_ok=True)
    metrics_path = os.path.join(metrics_dir, "iterations.jsonl")
    row = {
        "iter": iter,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "task": task,
        "success": success,
        "skill_count": skill_count,
        "retrieved_skills": retrieved_skills,
        "used_skills": used_skills,
        "used_skills_exec": used_skills_exec,
        "used_skills_nodefs": used_skills_nodefs,
        "used_skills_runtime": used_skills_runtime,
        "probe_installed": probe_installed,
        "n_steps": n_steps,
        "parse_fail_model": parse_fail_model,
        "parse_fail_infra": parse_fail_infra,
        "crashed": crashed,
        "crash_kind": crash_kind,
        "error": error,
        "n_completed": n_completed,
        "n_failed": n_failed,
        "action_latency_ms": action_latency_ms,
        "curation_enabled": curation_enabled,
        "executed_skills": executed_skills,
        "executed_failed": executed_failed,
        "executed_nested": executed_nested,
        "skill_credit": skill_credit,
        "exec_args": exec_args,
        "chain_nodes": chain_nodes,
        "chain_reused": chain_reused,
        "chain_gaps": chain_gaps,
        "chain_gap_failed": chain_gap_failed,
        "chain_node_ok": chain_node_ok,
        "chain_route": chain_route,
    }
    with open(metrics_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
