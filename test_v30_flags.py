"""v30 item 5: flags default off → prompts and run_meta keys unchanged vs pre-task baseline."""
import argparse
import json
import os
import subprocess
import sys
from types import SimpleNamespace

os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("OPENAI_API_BASE", "https://api.deepseek.com/v1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from voyager.agents.action import ActionAgent
from voyager.agents.critic import CriticAgent
from voyager.v30_flags import (
    V30_NAMED_ACTIONS,
    add_v30_arguments,
    merge_named_actions,
    v30_run_meta_fields,
    v30_voyager_kwargs,
)

REPO = os.path.dirname(os.path.abspath(__file__))
BASELINE = os.path.expanduser("~/v30_baseline_ref")
# Constructor-written run_meta values that differ by tree/clock, not by v30 flags.
_RUN_META_VOLATILE = ("started_at", "git_head", "git_dirty")

_DUMP_SCRIPT = r"""
import json, os, sys
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("OPENAI_API_BASE", "https://api.deepseek.com/v1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
ckpt, out = sys.argv[1], sys.argv[2]

class DummyEnv:
    # Skip MC bind. Voyager.__init__ still builds run_meta and agents.
    def __init__(self, **kwargs):
        self.pause_verify = bool(kwargs.get("pause_verify", False))
        self.server_port = kwargs.get("server_port", 3000)

import voyager.voyager as voyager_mod
voyager_mod.VoyagerEnv = DummyEnv
from voyager.voyager import Voyager
from voyager.prompts import load_prompt

voyager = Voyager(
    mc_port=25565,
    ckpt_dir=ckpt,
    openai_api_key=os.environ.get("OPENAI_API_KEY", "sk-test"),
    openai_api_base=os.environ.get("OPENAI_API_BASE") or "https://api.deepseek.com/v1",
)
action_text = voyager.action_agent.render_system_message(skills=[]).content
critic_text = voyager.critic_agent.render_system_message().content
curriculum_text = load_prompt("curriculum")
run_meta = dict(voyager.llm_audit.run_meta)
try:
    from voyager.v30_flags import v30_run_meta_fields
    v30_fields = v30_run_meta_fields({})
except ImportError:
    v30_fields = {}
payload = {
    "voyager_file": os.path.abspath(voyager_mod.__file__),
    "action": action_text,
    "critic": critic_text,
    "curriculum": curriculum_text,
    "v30_fields": v30_fields,
    "run_meta": run_meta,
}
open(out, "w", encoding="utf-8").write(json.dumps(payload, ensure_ascii=False, default=str))
print("DUMP_OK", os.path.abspath(voyager_mod.__file__))
"""


def _empty_args():
    parser = argparse.ArgumentParser()
    add_v30_arguments(parser)
    parser.add_argument("--extra-base-skills", nargs="+", default=None)
    return parser.parse_args([])


def _dump_tree(tree, tmp_path, tag):
    assert os.path.isdir(tree), f"missing tree: {tree}"
    ckpt = str(tmp_path / f"{tag}_ckpt")
    out = str(tmp_path / f"{tag}.json")
    os.makedirs(ckpt, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = tree
    env["OPENAI_API_KEY"] = env.get("OPENAI_API_KEY") or "sk-test"
    env["OPENAI_API_BASE"] = env.get("OPENAI_API_BASE") or "https://api.deepseek.com/v1"
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    env["TOKENIZERS_PARALLELISM"] = "false"
    proc = subprocess.run(
        [sys.executable, "-c", _DUMP_SCRIPT, ckpt, out],
        cwd=tree,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"dump {tree} failed rc={proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    with open(out, encoding="utf-8") as handle:
        return json.load(handle)


def test_v30_kwargs_empty_when_all_off():
    assert v30_voyager_kwargs(_empty_args()) == {}
    assert v30_run_meta_fields({}) == {}
    assert merge_named_actions(None, False) is None


def test_v30_kwargs_only_when_on():
    args = SimpleNamespace(
        v30_no_lru_evict=True,
        v30_verify_critic=True,
        v30_named_actions=True,
        v30_ledger=True,
        extra_base_skills=None,
    )
    kw = v30_voyager_kwargs(args)
    assert kw["lru_evict"] is False
    assert kw["verify_critic"] is True
    assert kw["v30_named_actions"] is True
    assert kw["v30_ledger"] is True
    meta = v30_run_meta_fields(kw)
    assert set(meta) == {
        "v30_no_lru_evict",
        "v30_verify_critic",
        "v30_named_actions",
        "v30_ledger",
    }
    extra = merge_named_actions(None, True)
    assert extra == list(V30_NAMED_ACTIONS)


def test_prompts_and_run_meta_match_pre_task_baseline(tmp_path):
    """v30 全关时，三类提示词与构造出的 run_meta 对任务前基线相同（键与值）。"""
    baseline = _dump_tree(BASELINE, tmp_path, "baseline")
    current = _dump_tree(REPO, tmp_path, "current")
    assert baseline["voyager_file"].startswith(BASELINE), baseline["voyager_file"]
    assert current["voyager_file"].startswith(REPO), current["voyager_file"]
    assert current["action"] == baseline["action"]
    assert current["critic"] == baseline["critic"]
    assert current["curriculum"] == baseline["curriculum"]
    assert current["v30_fields"] == {}
    assert baseline["v30_fields"] == {}
    current_meta = dict(current["run_meta"])
    baseline_meta = dict(baseline["run_meta"])
    for key in _RUN_META_VOLATILE:
        current_meta.pop(key, None)
        baseline_meta.pop(key, None)
    assert current_meta == baseline_meta
    for name in V30_NAMED_ACTIONS:
        assert f"async function {name}(" not in current["action"]


def test_action_prompt_contains_named_actions_when_on(tmp_path):
    off = ActionAgent(
        model_name="deepseek-v4-flash", ckpt_dir=str(tmp_path / "off")
    ).render_system_message(skills=[]).content
    on = ActionAgent(
        model_name="deepseek-v4-flash",
        ckpt_dir=str(tmp_path / "on"),
        extra_base_skills=list(V30_NAMED_ACTIONS),
    ).render_system_message(skills=[]).content
    assert on != off
    for name in V30_NAMED_ACTIONS:
        assert f"async function {name}(" in on
        assert f"async function {name}(" not in off


def test_critic_prompt_unchanged():
    from voyager.prompts import load_prompt

    agent = CriticAgent(model_name="deepseek-v4-flash")
    text = agent.render_system_message().content
    assert text == load_prompt("critic")
    for name in V30_NAMED_ACTIONS:
        assert f"async function {name}(" not in text


def test_curriculum_prompt_unchanged():
    from voyager.prompts import load_prompt

    # CurriculumAgent.render_system_message is this template with no v30 slots.
    text = load_prompt("curriculum")
    for name in V30_NAMED_ACTIONS:
        assert f"async function {name}(" not in text
        assert f"`{name}(bot" not in text


def test_verify_critic_hook_uses_verifier_when_on():
    from voyager.voyager import Voyager

    v = Voyager.__new__(Voyager)
    v.verify_critic = True
    events = [
        (
            "observe",
            {
                "inventory": {"raw_iron": 3},
                "status": {
                    "food": 10,
                    "equipment": [],
                    "inventoryUsed": 1,
                    "biome": "plains",
                    "timeOfDay": "day",
                    "health": 20,
                    "position": {"x": 0, "y": 64, "z": 0},
                    "entities": [],
                },
                "voxels": [],
            },
        )
    ]
    result = v._check_task_success(
        events=events,
        task="Mine 3 iron ore",
        context="",
        chest_observation="",
    )
    assert result[0] is True
    assert "verifier" in result[1]


def test_verify_critic_hook_falls_back_when_unparsed():
    from voyager.voyager import Voyager

    v = Voyager.__new__(Voyager)
    v.verify_critic = True

    class FakeCritic:
        def check_task_success(self, **kwargs):
            return True, "llm-critic", {"skill": 1}

    v.critic_agent = FakeCritic()
    events = [
        (
            "observe",
            {
                "inventory": {},
                "status": {
                    "food": 10,
                    "equipment": [],
                    "inventoryUsed": 0,
                    "biome": "plains",
                    "timeOfDay": "day",
                    "health": 20,
                    "position": {"x": 0, "y": 64, "z": 0},
                    "entities": [],
                },
                "voxels": [],
            },
        )
    ]
    result = v._check_task_success(
        events=events,
        task="Open the unknown chest nearby and check its contents",
        context="",
        chest_observation="",
    )
    assert result == (True, "llm-critic", {"skill": 1})


def test_verify_critic_off_never_calls_verifier():
    from voyager.voyager import Voyager

    v = Voyager.__new__(Voyager)
    v.verify_critic = False

    class FakeCritic:
        def check_task_success(self, **kwargs):
            return False, "llm-only", {}

    v.critic_agent = FakeCritic()
    events = [
        (
            "observe",
            {
                "inventory": {"raw_iron": 3},
                "status": {
                    "food": 10,
                    "equipment": [],
                    "inventoryUsed": 1,
                    "biome": "plains",
                    "timeOfDay": "day",
                    "health": 20,
                    "position": {"x": 0, "y": 64, "z": 0},
                    "entities": [],
                },
                "voxels": [],
            },
        )
    ]
    result = v._check_task_success(
        events=events,
        task="Mine 3 iron ore",
        context="",
        chest_observation="",
    )
    assert result == (False, "llm-only", {})


def test_verify_critic_never_calls_llm_when_verdict_set():
    """v1.4 ②：能核对的步骤不调用 critic。"""
    from types import SimpleNamespace

    from voyager.voyager import Voyager

    v = Voyager.__new__(Voyager)
    v.verify_critic = True
    v.v30_shared_fixes = False
    v._verify_prev_obs = None
    v._verify_prev_round = None
    v.rollout_start_inventory = {}
    v.recorder = SimpleNamespace(iteration=3)
    calls = {"n": 0}

    class FakeCritic:
        def check_task_success(self, **kwargs):
            calls["n"] += 1
            return True, "llm-should-not-run", {}

    v.critic_agent = FakeCritic()
    events = [
        (
            "observe",
            {
                "inventory": {"raw_iron": 3},
                "status": {
                    "food": 10,
                    "equipment": [],
                    "inventoryUsed": 1,
                    "biome": "plains",
                    "timeOfDay": "day",
                    "health": 20,
                    "position": {"x": 0, "y": 64, "z": 0},
                    "entities": [],
                },
                "voxels": [],
            },
        )
    ]
    ok, critique, _ = v._check_task_success(
        events=events,
        task="Mine 3 iron ore",
        context="",
        chest_observation="",
    )
    assert ok is True
    assert "verifier" in (critique or "")
    assert calls["n"] == 0
    fail_events = [
        (
            "observe",
            {
                "inventory": {"raw_iron": 0},
                "status": {
                    "food": 10,
                    "equipment": [],
                    "inventoryUsed": 0,
                    "biome": "plains",
                    "timeOfDay": "day",
                    "health": 20,
                    "position": {"x": 0, "y": 64, "z": 0},
                    "entities": [],
                },
                "voxels": [],
            },
        )
    ]
    ok2, _, _ = v._check_task_success(
        events=fail_events,
        task="Mine 3 iron ore",
        context="",
        chest_observation="",
    )
    assert ok2 is False
    assert calls["n"] == 0
