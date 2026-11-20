"""基础层扩展开关（09-22）的离线关卡。

背景：从技能库里提炼出 3 个组合技能（craftWithTable / mineWithTool /
killMobWithWeapon），提升到「基础层」——和那 11 个人写的控制原语一样，
每一步都注入执行环境、并把简化版写进动作提示词。

这个开关**必须默认关**，关着时与改动前逐字节相同，否则所有已跑完的臂都不可比。
下面每条断言对应一个能让实验作废的失误。
"""
import os
import sys
from unittest.mock import patch

import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.environ.setdefault("OPENAI_API_KEY", "placeholder")
os.environ.setdefault("DEEPSEEK_API_KEY", "placeholder")

from voyager.agents.action import ActionAgent  # noqa: E402
from voyager.agents.skill import SkillManager  # noqa: E402
from voyager.control_primitives import load_control_primitives  # noqa: E402
from voyager.control_primitives_extra import load_extra_base_skills  # noqa: E402
from voyager.control_primitives_extra_context import (  # noqa: E402
    load_extra_base_skills_context,
)

NAMES = ["craftWithTable", "mineWithTool", "killMobWithWeapon"]


# ---------------------------------------------------------------- 加载器
@pytest.mark.parametrize("loader", [load_extra_base_skills, load_extra_base_skills_context])
@pytest.mark.parametrize("empty", [None, [], ()])
def test_loader_defaults_to_nothing(loader, empty):
    """不传 / 传空 → 一条都不加载。这是"关着时不改变任何臂"的根。"""
    assert loader(empty) == []


@pytest.mark.parametrize("loader", [load_extra_base_skills, load_extra_base_skills_context])
def test_loader_returns_all_names(loader):
    out = loader(NAMES)
    assert len(out) == len(NAMES)
    assert all(isinstance(x, str) and x.strip() for x in out)


@pytest.mark.parametrize("loader", [load_extra_base_skills, load_extra_base_skills_context])
def test_loader_rejects_typo(loader):
    """名字打错必须当场报错。静默少注入 = 两臂差异说不清，比崩了更糟。"""
    with pytest.raises(ValueError):
        loader(["craftWithTabel"])


def test_impl_and_context_cover_the_same_names():
    """实现层和上下文层必须一一对应，否则模型看到的和实际能调的对不上。"""
    base = os.path.join(ROOT, "voyager")
    impl = {f[:-3] for f in os.listdir(os.path.join(base, "control_primitives_extra"))
            if f.endswith(".js")}
    ctx = {f[:-3] for f in os.listdir(os.path.join(base, "control_primitives_extra_context"))
           if f.endswith(".js")}
    assert impl == ctx, (sorted(impl - ctx), sorted(ctx - impl))
    assert set(NAMES) <= impl


def test_extra_names_do_not_shadow_control_primitives():
    """提炼出来的名字不能和 11 个原语撞名，撞了就是后注入的覆盖前面的。"""
    primitive_names = {f[:-3] for f in
                       os.listdir(os.path.join(ROOT, "voyager", "control_primitives"))
                       if f.endswith(".js")}
    assert primitive_names.isdisjoint(set(NAMES))


# ---------------------------------------------------------------- 执行环境
def test_skill_manager_primitives_unchanged_when_off(tmp_path):
    off = SkillManager(ckpt_dir=str(tmp_path / "off")).control_primitives
    assert off == load_control_primitives()


def test_skill_manager_primitives_grow_when_on(tmp_path):
    off = SkillManager(ckpt_dir=str(tmp_path / "off2")).control_primitives
    on = SkillManager(ckpt_dir=str(tmp_path / "on2"),
                      extra_base_skills=NAMES).control_primitives
    assert len(on) == len(off) + len(NAMES)
    assert on[: len(off)] == off  # 原语顺序不能被打乱


# ---------------------------------------------------------------- 提示词
def _prompt(tmp_path, extra):
    agent = ActionAgent(model_name="deepseek-v4-flash",
                        ckpt_dir=str(tmp_path), extra_base_skills=extra)
    return agent.render_system_message(skills=[]).content


def test_prompt_byte_identical_when_off(tmp_path):
    """关着时的提示词必须和"这个开关不存在"时一模一样。"""
    baseline = ActionAgent(model_name="deepseek-v4-flash", ckpt_dir=str(tmp_path / "b"))
    with_flag = ActionAgent(model_name="deepseek-v4-flash", ckpt_dir=str(tmp_path / "f"),
                            extra_base_skills=None)
    a = baseline.render_system_message(skills=[]).content
    b = with_flag.render_system_message(skills=[]).content
    assert a == b
    for n in NAMES:
        assert n not in a


def test_prompt_contains_extras_when_on(tmp_path):
    off = _prompt(tmp_path / "off3", None)
    on = _prompt(tmp_path / "on3", NAMES)
    assert len(on) > len(off)
    for n in NAMES:
        assert f"async function {n}" in on


# ---------------------------------------------------------------- 零样本运行器
def _kwargs(extra):
    import voyager.zeroshot as Z

    argv = ["--arm", "OLD", "--empty-library", "--mc-port", "25565",
            "--out", "/tmp/_gate.jsonl", "--work-dir", "/tmp/_gate_wd"]
    if extra:
        argv += ["--extra-base-skills"] + list(extra)
    # resolve_config 要求 RUNTIME_CALL_PROBE=1 且在 import voyager 之前设置。
    # 本文件其余用例会先 import SkillManager，探针默认关；同一测试在
    # ~/v30_baseline_ref 里用默认环境也会因缺探针失败（与 v30 语义无关）。
    # 这里只给 zeroshot 这条路径补上探针开关，不改 zeroshot.py。
    with patch("voyager.zeroshot.RUNTIME_CALL_PROBE_ENABLED", True):
        cfg = Z.resolve_config(Z.build_parser().parse_args(argv))
        return Z.build_voyager_kwargs(cfg, ckpt_dir="/tmp/_gate_ck",
                                      library_root=None, trial=1)


def test_zeroshot_kwargs_identical_when_off():
    """不传开关时 kwargs 连键都不能多一个——否则结果行的 voyager_kwargs 变了，
    和已跑完的 96 次不可比。"""
    off, on = _kwargs(None), _kwargs(NAMES)
    assert "extra_base_skills" not in off
    assert on["extra_base_skills"] == NAMES
    assert {k: v for k, v in on.items() if k != "extra_base_skills"} == off


def test_zeroshot_rejects_typo_before_launch():
    """打错名字要在世界重置之前就退出，别白跑一次。"""
    import voyager.zeroshot as Z

    with pytest.raises(Z.ConfigError):
        _kwargs(["craftWithTabel"])


def test_presets_untouched():
    """PRESETS 一个键都不能动：smoke_v29_zeroshot 拿它和 run_voyager 的命令行逐键比。"""
    import voyager.zeroshot as Z

    for preset in Z.PRESETS.values():
        assert "extra_base_skills" not in preset
