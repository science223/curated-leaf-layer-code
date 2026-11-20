"""Skill-layer v1.1: mineOre / collectFluid routing, knowledge arm, no task names."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from types import SimpleNamespace

os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("OPENAI_API_BASE", "https://api.deepseek.com/v1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from voyager.agents.action import ActionAgent
from voyager.control_primitives_extra import load_extra_base_skills
from voyager.control_primitives_extra_context import load_extra_base_skill_rules
from voyager.knowledge_arm import (
    DEEP_ORE_KEYS,
    KNOWLEDGE_ARM_PATH,
    load_skill_constants,
    render_knowledge_arm_text,
    write_knowledge_arm_text,
)
from voyager.named_action_docs import render_named_action_docs, render_named_action_rule
from voyager.v30_flags import (
    V30_NAMED_ACTIONS,
    add_v30_arguments,
    v30_run_meta_fields,
    v30_voyager_kwargs,
)

REPO = os.path.dirname(os.path.abspath(__file__))
EXTRA = os.path.join(REPO, "voyager", "control_primitives_extra")
CTX = os.path.join(REPO, "voyager", "control_primitives_extra_context")

NEW_SKILLS = ("mineOre", "collectFluid", "obtain")

TASK_NAME_RE = re.compile(
    r"diamond_pickaxe|golden_sword|golden_apple|"
    r"\bbookshelf\b|\bcompass\b|\bclock\b|iron_chestplate|"
    r"钻石镐|金剑|岩浆桶|指南针|金苹果|书架|青金石块|红石块|"
    r"Craft 1 diamond pickaxe|Mine 3 diamond",
    re.IGNORECASE,
)


def test_new_skills_in_named_actions():
    for name in NEW_SKILLS:
        assert name in V30_NAMED_ACTIONS
        assert os.path.isfile(os.path.join(EXTRA, name + ".js"))
        assert os.path.isfile(os.path.join(CTX, name + ".js"))


def test_mineore_collectfluid_no_exploreuntil():
    for name in ("mineOre", "collectFluid"):
        text = open(os.path.join(EXTRA, name + ".js"), encoding="utf-8").read()
        assert "exploreUntil" not in text, name


def test_skill_js_has_no_task_names():
    for name in NEW_SKILLS:
        for folder in (EXTRA, CTX):
            text = open(os.path.join(folder, name + ".js"), encoding="utf-8").read()
            hit = TASK_NAME_RE.search(text)
            assert hit is None, (name, folder, hit.group(0) if hit else None)


def test_routing_docs_generated_from_code():
    impl = {
        name: open(os.path.join(EXTRA, name + ".js"), encoding="utf-8").read()
        for name in ("gather", "mineOre", "collectFluid")
    }
    mine_docs = render_named_action_docs("mineOre", impl["mineOre"])
    gather_rule = render_named_action_rule("gather")
    mine_rule = render_named_action_rule("mineOre")
    fluid_rule = render_named_action_rule("collectFluid")
    for key in DEEP_ORE_KEYS:
        assert key in mine_rule, key
        assert key in gather_rule, key
    assert "mineOre" in gather_rule
    assert "gather" in mine_rule
    assert "240" in mine_docs
    assert "increment" in mine_docs.lower() or "MORE" in mine_docs
    assert "source" in fluid_rule.lower()
    assert "missing_bucket" in fluid_rule
    rules = load_extra_base_skill_rules(["gather", "mineOre", "collectFluid"])
    assert "mineOre" in rules
    assert "collectFluid" in rules
    for key in DEEP_ORE_KEYS:
        assert key in rules


def test_knowledge_text_matches_js_constants():
    write_knowledge_arm_text()
    const = load_skill_constants()
    rendered = render_knowledge_arm_text(const)
    disk = open(KNOWLEDGE_ARM_PATH, encoding="utf-8").read()
    assert disk == rendered
    ore = const["mineOre"]
    assert ore["heights"]["diamond"]["peakY"] == -59
    assert ore["stripSpacing"] == 3
    assert "peakY" in str(ore)
    for key, h in ore["heights"].items():
        assert f"Y={h['peakY']}" in rendered, key
        assert f"Y={h['minY']}" in rendered, key
    assert str(ore["stripSpacing"]) in rendered
    assert ", ".join(ore["discard"]) in rendered
    fluid = const["collectFluid"]
    assert str(fluid["sourceLevel"]) in rendered
    assert str(fluid["lavaTargetY"]) in rendered
    low = rendered.lower()
    assert "for (" not in low
    assert "await " not in low
    assert "exploreuntil" not in low
    assert "function " not in low
    assert "diamond pickaxe" not in low
    assert "golden sword" not in low


def test_knowledge_flag_default_off_leaves_prompt(tmp_path):
    off = ActionAgent(
        model_name="deepseek-v4-flash", ckpt_dir=str(tmp_path / "off")
    ).render_system_message(skills=[]).content
    on = ActionAgent(
        model_name="deepseek-v4-flash",
        ckpt_dir=str(tmp_path / "on"),
        v30_knowledge_text=True,
    ).render_system_message(skills=[]).content
    assert "most common near Y=-59" not in off
    assert "most common near Y=-59" in on
    assert off != on
    assert "async function mineOre" not in off


def test_knowledge_flag_kwargs_and_run_meta():
    import argparse

    parser = argparse.ArgumentParser()
    add_v30_arguments(parser)
    empty = parser.parse_args([])
    assert v30_voyager_kwargs(empty) == {}
    args = SimpleNamespace(
        v30_no_lru_evict=False,
        v30_verify_critic=False,
        v30_named_actions=False,
        v30_ledger=False,
        v30_shared_fixes=False,
        v30_anti_stall=False,
        v30_knowledge_text=True,
    )
    kw = v30_voyager_kwargs(args)
    assert kw == {"v30_knowledge_text": True}
    assert v30_run_meta_fields(kw) == {"v30_knowledge_text": True}


def test_mineore_increment_not_total():
    js = r"""
    const fs = require("fs");
    const src = fs.readFileSync("voyager/control_primitives_extra/mineOre.js", "utf8");
    const Vec3 = class { constructor(x,y,z){ this.x=x; this.y=y; this.z=z; this.floored=()=>this; this.offset=(a,b,c)=>new Vec3(this.x+a,this.y+b,this.z+c); this.minus=(o)=>new Vec3(this.x-o.x,this.y-o.y,this.z-o.z); this.distanceTo=()=>0; this.clone=()=>new Vec3(this.x,this.y,this.z); } };
    const mcData = {
      itemsByName: { diamond:{id:1,name:"diamond"}, iron_pickaxe:{id:2,name:"iron_pickaxe"} },
      blocksByName: {},
    };
    const bot = {
      chat() {},
      inventory: { items() { return [
        {type:1,name:"diamond",count:6},
        {type:2,name:"iron_pickaxe",count:1,maxDurability:250,durabilityUsed:0},
      ]; } },
      heldItem: {name:"iron_pickaxe", maxDurability:250, durabilityUsed:0},
      entity: { position: { x:0, y:64, z:0, floored() { return new Vec3(0,64,0); } } },
      equip: async () => {},
      findBlock() { return null; },
      blockAt() { return {name:"stone", boundingBox:"block"}; },
      dig: async () => {},
      toss: async () => {},
      waitForTicks: async () => {},
      lookAt: async () => {},
      setControlState() {},
    };
    eval(src);
    let calls = 0;
    const realNow = Date.now;
    Date.now = () => { calls += 1; return calls < 25 ? realNow() : realNow() + 300000; };
    (async () => {
      const r = await mineOre(bot, "diamond", 2);
      if (r.reason === "already_satisfied") throw new Error("TOTAL semantics");
      console.log("reason=" + r.reason + " ok=" + r.ok);
    })().catch((e) => { console.error(e.message); process.exit(1); });
    """
    proc = subprocess.run(["node", "-e", js], cwd=REPO, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "already_satisfied" not in proc.stdout


def test_mineore_missing_tool():
    js = r"""
    const fs = require("fs");
    const src = fs.readFileSync("voyager/control_primitives_extra/mineOre.js", "utf8");
    const Vec3 = class { constructor(x,y,z){ this.x=x; this.y=y; this.z=z; this.floored=()=>this; this.offset=()=>this; } };
    const mcData = { itemsByName: { diamond:{id:1} }, blocksByName: {} };
    const bot = {
      chat() {},
      inventory: { items() { return [{type:1,name:"diamond",count:0}, {type:9,name:"wooden_pickaxe",count:1}]; } },
      entity: { position: { x:0, y:64, z:0, floored() { return new Vec3(0,64,0); } } },
      equip: async () => {},
    };
    eval(src);
    (async () => {
      const r = await mineOre(bot, "diamond", 1);
      if (r.reason !== "missing_tool" || r.ok) throw new Error("got " + JSON.stringify(r));
      console.log("ok");
    })().catch((e) => { console.error(e.message); process.exit(1); });
    """
    proc = subprocess.run(["node", "-e", js], cwd=REPO, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr + proc.stdout


def test_collectfluid_activate_without_fill_is_not_done():
    """mineflayer may chat 'goto near' and resolve activateBlock without filling."""
    js = r"""
    const fs = require("fs");
    const Vec3 = class {
      constructor(x,y,z){ this.x=x; this.y=y; this.z=z; }
      floored(){ return this; }
      offset(a,b,c){ return new Vec3(this.x+a, this.y+b, this.z+c); }
      minus(o){ return new Vec3(this.x-o.x, this.y-o.y, this.z-o.z); }
      distanceTo(){ return 0; }
      clone(){ return new Vec3(this.x,this.y,this.z); }
    };
    global.Vec3 = Vec3;
    global.GoalBlock = class { constructor(x,y,z){ this.x=x;this.y=y;this.z=z; } };
    const mcData = { itemsByName: { bucket:{id:1}, lava_bucket:{id:2} } };
    const lavaPos = new Vec3(2, 10, 0);
    const bot = {
      chat() {},
      inventory: { items() { return [{type:1,name:"bucket",count:1}]; } },
      heldItem: {name:"bucket"},
      entity: { position: Object.assign(new Vec3(0, 12, 0), { floored(){ return new Vec3(0,12,0); } }) },
      equip: async () => {},
      findBlock() { return { name:"lava", position: lavaPos, getProperties(){ return {level:0}; } }; },
      blockAt(p) { return {name:"stone", boundingBox:"block"}; },
      dig: async () => {},
      toss: async () => {},
      waitForTicks: async () => {},
      lookAt: async () => {},
      activateBlock: async () => { bot.chat("Please goto a place near the block first!"); },
      activateItem: async () => {},
      setControlState() {},
      pathfinder: { setGoal() {}, goto: async () => {} },
      placeBlock: async () => {},
    };
    eval(fs.readFileSync("voyager/control_primitives_extra/mineOre.js", "utf8"));
    eval(fs.readFileSync("voyager/control_primitives_extra/collectFluid.js", "utf8"));
    let calls = 0;
    const realNow = Date.now;
    Date.now = () => { calls += 1; return calls < 8 ? realNow() : realNow() + 300000; };
    (async () => {
      const r = await collectFluid(bot, "lava");
      if (r.ok && r.reason === "done") throw new Error("false done without lava_bucket");
      if (r.reason !== "timeout" && r.reason !== "fail") throw new Error("got " + JSON.stringify(r));
      console.log("ok " + r.reason);
    })().catch((e) => { console.error(e.message); process.exit(1); });
    """
    proc = subprocess.run(["node", "-e", js], cwd=REPO, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr + proc.stdout


def test_collectfluid_missing_bucket():
    js = r"""
    const fs = require("fs");
    const src = fs.readFileSync("voyager/control_primitives_extra/collectFluid.js", "utf8");
    const Vec3 = class { constructor(x,y,z){ this.x=x; this.y=y; this.z=z; } };
    const mcData = { itemsByName: { bucket:{id:1}, lava_bucket:{id:2} } };
    const bot = {
      chat() {},
      inventory: { items() { return []; } },
      entity: { position: { x:0, y:64, z:0, floored() { return new Vec3(0,64,0); } } },
    };
    eval(src);
    (async () => {
      const r = await collectFluid(bot, "lava");
      if (r.reason !== "missing_bucket" || r.ok) throw new Error("got " + JSON.stringify(r));
      console.log("ok");
    })().catch((e) => { console.error(e.message); process.exit(1); });
    """
    proc = subprocess.run(["node", "-e", js], cwd=REPO, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr + proc.stdout


def test_skill_mc_gate_criterion_locked_before_results():
    """Round-2 gate: hard segments use half vanilla; easy segments use vanilla (not worse)."""
    import importlib.util

    path = os.path.join(REPO, "_cc0924", "test_skills_mc.py")
    spec = importlib.util.spec_from_file_location("skills_mc_gates", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.GATE_SUCCESS_RATE_MIN == 0.90
    assert mod.GATE_HARD_VANILLA_MEDIAN == 3.0
    lim_d, txt_d = mod.calls_needed_limit(7.0)
    lim_l, txt_l = mod.calls_needed_limit(31)
    lim_g, txt_g = mod.calls_needed_limit(1)
    lim_i, txt_i = mod.calls_needed_limit(1.0)
    lim_r, txt_r = mod.calls_needed_limit(1)
    assert lim_d == 3.5 and "/2" in txt_d
    assert lim_l == 15.5 and "/2" in txt_l
    assert lim_g == 1 and "vanilla_median" in txt_g and "/2" not in txt_g.split("(")[0]
    assert lim_i == 1.0
    assert lim_r == 1
    # gold must NOT use half(0.5) — that was the old rule
    payload = {
        "seeds": [1, 2, 3, 4, 5],
        "scenes": ["diamond", "gold", "redstone", "iron", "lava"],
        "vanilla": {
            "diamond": {"median": 7.0, "half": 3.5},
            "gold": {"median": 1, "half": 0.5},
            "redstone": {"median": 1, "half": 0.5},
            "iron": {"median": 1.0, "half": 0.5},
            "lava": {"median": 31, "half": 15.5},
        },
        "runs": [],
    }
    for sid, need in (("diamond", 3), ("gold", 1), ("redstone", 1), ("iron", 1), ("lava", 12)):
        for seed in range(5):
            payload["runs"].append(
                {
                    "seed": seed,
                    "scene": sid,
                    "success": True,
                    "calls_needed": need,
                    "calls": [{"i": i} for i in range(1, need + 1)],
                    "max_calls": 8,
                    "elapsed_s_total": 10,
                    "hurt": False,
                    "died": False,
                    "fail_reasons": [],
                    "prep_ok": True,
                }
            )
    gates = mod.gate_summary(payload)
    assert gates["gold"]["calls_needed_limit"] == 1
    assert gates["gold"]["ok"] is True
    assert gates["diamond"]["calls_needed_limit"] == 3.5
    assert gates["diamond"]["ok"] is True
    assert gates["lava"]["calls_needed_limit"] == 15.5
    assert gates["lava"]["ok"] is True
    assert gates["success_rate"]["ok"] is True
    assert gates["all_ok"] is True
    assert gates["gold"]["first_call_success_rate"] == 1.0
    assert gates["diamond"]["first_call_success_rate"] == 0.0
    payload["runs"] = [r for r in payload["runs"] if r["scene"] != "gold"] + [
        {
            "seed": i,
            "scene": "gold",
            "success": True,
            "calls_needed": 2,
            "calls": [{"i": 1}, {"i": 2}],
            "max_calls": 8,
            "elapsed_s_total": 10,
            "hurt": False,
            "died": False,
            "fail_reasons": [],
            "prep_ok": True,
        }
        for i in range(5)
    ]
    gates2 = mod.gate_summary(payload)
    assert gates2["gold"]["calls_needed_median"] == 2
    assert gates2["gold"]["ok"] is False  # 2 > vanilla 1


def test_height_table_cites_1_19_source():
    text = open(os.path.join(EXTRA, "mineOre.js"), encoding="utf-8").read()
    assert "1.19" in text or "1.18" in text
    assert "minecraft.wiki" in text
    const = load_skill_constants()["mineOre"]
    assert "wiki" in const["source"].lower()
    assert const["heights"]["gold"]["peakY"] == -16
    assert const["heights"]["lapis"]["peakY"] == 0
    assert const["heights"]["iron"]["minTool"] == "stone"
    assert const["heights"]["diamond"]["minTool"] == "iron"


def test_obtain_prefers_findblocks_and_has_docs():
    text = open(os.path.join(EXTRA, "obtain.js"), encoding="utf-8").read()
    assert "findBlocks" in text
    assert "stripSpacing" not in text
    docs = render_named_action_docs("obtain", text)
    assert "obtain" in docs
    rule = render_named_action_rule("obtain")
    assert "obtain(bot" in rule
    assert "final" in rule.lower() or "Prefer" in rule or "prefer" in rule

