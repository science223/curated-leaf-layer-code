"""v30 item 3: named-action JS files pass node --check and load as extras."""
import os
import re
import subprocess

import pytest

from voyager.control_primitives import load_control_primitives
from voyager.control_primitives_extra import load_extra_base_skills
from voyager.control_primitives_extra_context import (
    load_extra_base_skill_rules,
    load_extra_base_skills_context,
)
from voyager.v30_flags import V30_NAMED_ACTIONS

ROOT = os.path.dirname(os.path.abspath(__file__))
EXTRA = os.path.join(ROOT, "voyager", "control_primitives_extra")
CTX = os.path.join(ROOT, "voyager", "control_primitives_extra_context")


def _js_files(folder):
    return sorted(f for f in os.listdir(folder) if f.endswith(".js"))


def test_impl_and_context_still_paired():
    impl = {f[:-3] for f in _js_files(EXTRA)}
    ctx = {f[:-3] for f in _js_files(CTX)}
    assert impl == ctx, (sorted(impl - ctx), sorted(ctx - impl))
    assert set(V30_NAMED_ACTIONS) <= impl


def test_v30_names_do_not_shadow_primitives():
    primitive_names = {
        f[:-3]
        for f in os.listdir(os.path.join(ROOT, "voyager", "control_primitives"))
        if f.endswith(".js")
    }
    assert primitive_names.isdisjoint(set(V30_NAMED_ACTIONS))


@pytest.mark.parametrize("name", V30_NAMED_ACTIONS)
def test_node_check_impl(name):
    path = os.path.join(EXTRA, name + ".js")
    proc = subprocess.run(["node", "--check", path], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("name", V30_NAMED_ACTIONS)
def test_node_check_context(name):
    path = os.path.join(CTX, name + ".js")
    proc = subprocess.run(["node", "--check", path], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_loader_accepts_v30_names():
    impl = load_extra_base_skills(list(V30_NAMED_ACTIONS))
    ctx = load_extra_base_skills_context(list(V30_NAMED_ACTIONS))
    assert len(impl) == len(V30_NAMED_ACTIONS)
    assert len(ctx) == len(V30_NAMED_ACTIONS)
    rules = load_extra_base_skill_rules(list(V30_NAMED_ACTIONS))
    for name in V30_NAMED_ACTIONS:
        assert name in rules


def test_each_impl_prints_contract_line():
    for name in V30_NAMED_ACTIONS:
        text = open(os.path.join(EXTRA, name + ".js"), encoding="utf-8").read()
        assert "[contract]" in text, name
        assert f"async function {name}" in text, name


def test_primitives_unchanged_when_extras_off():
    assert load_extra_base_skills(None) == []
    assert load_extra_base_skills([]) == []
    # 11 built-in primitives still load independently
    assert len(load_control_primitives()) == 11


def test_gather_mines_selected_block_not_name_only():
    text = open(os.path.join(EXTRA, "gather.js"), encoding="utf-8").read()
    assert "mineBlock(bot, block.name" not in text
    assert "collectBlock.collect(block" in text
    assert "bot.dig(block)" in text
    ctx = open(os.path.join(CTX, "gather.js"), encoding="utf-8").read()
    assert "mineBlock(bot, name" not in ctx
    assert "collectBlock.collect(block" in ctx


def test_named_actions_exclude_mine_kill_helpers():
    assert "mineWithTool" not in V30_NAMED_ACTIONS
    assert "killMobWithWeapon" not in V30_NAMED_ACTIONS
    loaded = load_extra_base_skills(list(V30_NAMED_ACTIONS))
    joined = "\n".join(loaded)
    assert "async function mineWithTool" not in joined
    assert "async function killMobWithWeapon" not in joined


def test_named_actions_push_skill_outcomes():
    for name in V30_NAMED_ACTIONS:
        text = open(os.path.join(EXTRA, name + ".js"), encoding="utf-8").read()
        assert "__skillOutcomes.push" in text, name
        assert f'name: "{name}"' in text or f"name: '{name}'" in text, name


def test_gather_hand_harvest_no_min_tool():
    js = r"""
    const fs = require("fs");
    const src = fs.readFileSync("voyager/control_primitives_extra/gather.js", "utf8");
    const Vec3 = class { constructor(x,y,z){ this.x=x; this.y=y; this.z=z; } };
    const mcData = { blocksByName: { oak_leaves: {id:1} }, itemsByName: {} };
    eval(src);
    const log = _gatherSpec("oak_log");
    const dirt = _gatherSpec("dirt");
    const sand = _gatherSpec("sand");
    const leaves = _gatherSpec("oak_leaves");
    const iron = _gatherSpec("iron_ore");
    const stripped = _gatherSpec("stripped_oak_log");
    if (log.minTool) throw new Error("oak_log minTool=" + log.minTool);
    if (dirt.minTool) throw new Error("dirt minTool=" + dirt.minTool);
    if (sand.minTool) throw new Error("sand minTool=" + sand.minTool);
    if (leaves.minTool !== "shears") throw new Error("leaves minTool=" + leaves.minTool);
    if (iron.minTool !== "stone") throw new Error("iron minTool=" + iron.minTool);
    if (!stripped.drops.includes("stripped_oak_log")) throw new Error("drops=" + stripped.drops);
    if (!stripped.drops.includes("oak_log")) throw new Error("drops missing oak_log");
    console.log("ok");
    """
    proc = subprocess.run(["node", "-e", js], cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "ok" in proc.stdout


def test_gather_has_540s_deadline():
    # 09-25 per-call limit back to upstream Voyager 600 s; gather/obtain budget 540 s.
    text = open(os.path.join(EXTRA, "gather.js"), encoding="utf-8").read()
    assert "540000" in text
    smelt = open(os.path.join(EXTRA, "smelt.js"), encoding="utf-8").read()
    assert "240000" in smelt
    assert "partial" in smelt


def test_craft_uses_ceil_deficit_over_output():
    text = open(os.path.join(EXTRA, "craft.js"), encoding="utf-8").read()
    assert "Math.ceil" in text
    assert "perCraft" in text
    assert "recipe.result" in text


def test_prepare_mines_six_from_empty():
    text = open(os.path.join(EXTRA, "prepare.js"), encoding="utf-8").read()
    assert "6 - logs()" in text


def test_smelt_short_materials_smelts_what_it_can():
    smelt = open(os.path.join(EXTRA, "smelt.js"), encoding="utf-8").read()
    assert "Math.min(want, haveMat)" in smelt
    assert "partial" in smelt
    junk = open(os.path.join(EXTRA, "discardJunk.js"), encoding="utf-8").read()
    assert "missing_chest" in junk


def test_smelt_unmapped_input_not_already_satisfied():
    """oak_log → charcoal via minecraft-data; dirt → unknown_recipe, never itself."""
    js = r"""
    const fs = require("fs");
    const src = fs.readFileSync("voyager/control_primitives_extra/smelt.js", "utf8");
    const mcData = {
      itemsByName: {
        oak_log: {id:1,name:"oak_log"}, charcoal: {id:2,name:"charcoal"},
        dirt: {id:3,name:"dirt"}, raw_iron: {id:4,name:"raw_iron"},
        iron_ingot: {id:5,name:"iron_ingot"},
      },
      items: {},
      recipes: {},
    };
    eval(src);
    if (typeof _smeltOutput !== "function") throw new Error("missing _smeltOutput");
    const logOut = _smeltOutput("oak_log");
    if (logOut !== "charcoal") throw new Error("oak_log output=" + logOut);
    if (_smeltOutput("oak_log") === "oak_log") throw new Error("oak_log mapped to itself");
    const dirtOut = _smeltOutput("dirt");
    if (dirtOut != null) throw new Error("dirt should be unknown, got " + dirtOut);
    if (_smeltOutput("raw_iron") !== "iron_ingot") throw new Error("raw_iron");
    console.log("ok");
    """
    proc = subprocess.run(["node", "-e", js], cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "ok" in proc.stdout


def test_gather_harvest_tools_require_pickaxe_or_shears():
    """andesite harvestTools → pickaxe; leaves empty drops → shears."""
    js = r"""
    const fs = require("fs");
    const src = fs.readFileSync("voyager/control_primitives_extra/gather.js", "utf8");
    const Vec3 = class { constructor(x,y,z){ this.x=x; this.y=y; this.z=z; } };
    const mcData = {
      blocksByName: {
        oak_leaves: {id:1, drops:[], material:"leaves;mineable/hoe"},
        andesite: {id:2, harvestTools:{737:true,742:true}, drops:[2], material:"mineable/pickaxe"},
        granite: {id:3, harvestTools:{737:true}, drops:[3], material:"mineable/pickaxe"},
        oak_log: {id:4, drops:[4]},
        dirt: {id:5, drops:[5]},
      },
      itemsByName: {},
      items: {737:{name:"wooden_pickaxe"},742:{name:"stone_pickaxe"},887:{name:"shears"}},
    };
    eval(src);
    const andesite = _gatherSpec("andesite");
    const granite = _gatherSpec("granite");
    const leaves = _gatherSpec("oak_leaves");
    const log = _gatherSpec("oak_log");
    if (!andesite || andesite.minTool == null) throw new Error("andesite minTool=" + (andesite && andesite.minTool));
    if (andesite.toolKind !== "pickaxe") throw new Error("andesite kind=" + andesite.toolKind);
    if (!granite || granite.minTool == null) throw new Error("granite minTool=" + (granite && granite.minTool));
    if (!leaves || leaves.minTool !== "shears") throw new Error("leaves minTool=" + (leaves && leaves.minTool));
    if (log.minTool) throw new Error("oak_log should stay hand-harvest, minTool=" + log.minTool);
    console.log("ok");
    """
    proc = subprocess.run(["node", "-e", js], cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "ok" in proc.stdout


def test_prepare_four_logs_mines_before_table():
    """Exactly 4 logs and no table must mine extra (table costs 1 log)."""
    js = r"""
    const fs = require("fs");
    const src = fs.readFileSync("voyager/control_primitives_extra/prepare.js", "utf8");
    eval(src);
    if (typeof _prepareMineWant !== "function") throw new Error("missing _prepareMineWant");
    const at4 = _prepareMineWant(4, 0, false);
    if (!(at4 >= 1)) throw new Error("4 logs no table mineWant=" + at4);
    const hasTable = _prepareMineWant(4, 0, true);
    if (hasTable !== 0) throw new Error("4 logs with table should not mine, got " + hasTable);
    const plenty = _prepareMineWant(8, 0, false);
    if (plenty !== 0) throw new Error("8 logs should skip mine, got " + plenty);
    console.log("ok");
    """
    proc = subprocess.run(["node", "-e", js], cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "ok" in proc.stdout


def test_obtain_is_named_and_checked():
    assert "obtain" in V30_NAMED_ACTIONS
    assert V30_NAMED_ACTIONS[-1] == "obtain"
    impl = os.path.join(EXTRA, "obtain.js")
    ctx = os.path.join(CTX, "obtain.js")
    assert os.path.isfile(impl)
    assert os.path.isfile(ctx)
    for path in (impl, ctx):
        proc = subprocess.run(["node", "--check", path], capture_output=True, text=True)
        assert proc.returncode == 0, path + "\n" + proc.stderr


def test_obtain_uses_findblocks_not_strip():
    text = open(os.path.join(EXTRA, "obtain.js"), encoding="utf-8").read()
    assert "findBlocks" in text
    assert "stripSpacing" not in text
    assert "mineOre(" not in text
    assert "__obtainDeadline" in text
    assert "inv_full" in text
    assert "global_cap" in text or "globalCap" in text


def test_obtain_recipe_pick_prefers_have_then_oak():
    js = r"""
    const fs = require("fs");
    const src = fs.readFileSync("voyager/control_primitives_extra/obtain.js", "utf8");
    eval(src);
    const oak = {ings: [{name:"oak_planks", id:5, have:0}], unique:1};
    const spruce = {ings: [{name:"spruce_planks", id:8, have:0}], unique:1};
    const haveSpruce = {ings: [{name:"spruce_planks", id:8, have:4}], unique:1};
    const few = {ings: [{name:"stick", id:50, have:0}], unique:1};
    const many = {ings: [{name:"stick", id:50, have:0},{name:"iron_ingot", id:51, have:0}], unique:2};
    if (!(_obtainRecipeBetter(haveSpruce, oak))) throw new Error("already-have should win");
    if (!(_obtainRecipeBetter(oak, spruce))) throw new Error("oak before spruce");
    if (!(_obtainRecipeBetter(few, many))) throw new Error("fewer unique ings");
    const cobble = {ings: [{name:"cobblestone", id:22, have:0},{name:"stick", id:765, have:0}], unique:2};
    const deepslate = {ings: [{name:"cobbled_deepslate", id:9, have:0},{name:"stick", id:765, have:0}], unique:2};
    if (!(_obtainRecipeBetter(cobble, deepslate))) throw new Error("cobblestone must beat cobbled_deepslate (id 9 < 22 used to win)");
    if (_obtainRecipeBetter(deepslate, cobble)) throw new Error("cobbled_deepslate must not beat cobblestone when neither is in bag");
    const haveDeep = {ings: [{name:"cobbled_deepslate", id:9, have:8},{name:"stick", id:765, have:0}], unique:2};
    if (!(_obtainRecipeBetter(haveDeep, cobble))) throw new Error("already-have cobbled_deepslate should still win");
    console.log("ok");
    """
    proc = subprocess.run(["node", "-e", js], cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "ok" in proc.stdout


def test_obtain_empty_bag_first_leaf_is_log_not_deepslate():
    """Empty inventory → punch wood, not gather cobbled_deepslate (missing_tool spin)."""
    js = r"""
    const fs = require("fs");
    const mcData = require("minecraft-data")("1.19");
    const bot = {
      inventory: { items: () => [] },
      entity: { position: { x: 0, y: 64, z: 0 }, health: 20 },
      findBlock: () => null,
      findBlocks: () => [],
      chat() {},
    };
    eval(fs.readFileSync("voyager/control_primitives_extra/obtain.js", "utf8"));
    for (const item of ["crafting_table", "wooden_pickaxe", "stone_pickaxe", "furnace", "diamond_pickaxe"]) {
      const leaf = _obtainFirstLeaf(bot, item, 1, 0);
      const blob = JSON.stringify(leaf);
      if (!leaf || leaf.kind !== "gather" || leaf.name !== "log") {
        throw new Error(item + " first leaf=" + blob + " (want gather log)");
      }
    }
    console.log("ok");
    """
    env = dict(os.environ)
    env["NODE_PATH"] = os.path.join(ROOT, "voyager", "env", "mineflayer", "node_modules")
    proc = subprocess.run(["node", "-e", js], cwd=ROOT, capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "ok" in proc.stdout


def test_obtain_minedeep_descends_when_far_from_peak():
    """Surface findBlocks hits must not skip stair-down to peakY."""
    src = open(os.path.join(ROOT, "voyager", "control_primitives_extra", "obtain.js"), encoding="utf-8").read()
    fn = src.split("async function _obtainMineDeep", 1)[1].split("async function ", 1)[0]
    assert "if (!atPeak)" in fn
    assert "nearby" in fn
    assert "_obtainGoY" in fn
    # 09-26 r3fix_4h: tryFound may also stair to an ore height; the stair-down to peakY must still follow !atPeak
    assert fn.find("if (!atPeak)") < fn.find("_obtainGoY(bot, slice")


_SLASH_CHAT = re.compile(r"""bot\.chat\s*\(\s*(?:`|['"])/""")
_SLASH_CMDS = re.compile(
    r"""(?:`|['"])/(?:tp|give|setblock|fill|gamerule|effect|kill|clear|summon|item|difficulty|time|weather|xp|fillbiome)\b"""
)
_MAXD_NUM = re.compile(r"maxDistance\s*:\s*(\d+)")
_MAXD_IDENT = re.compile(r"maxDistance\s*:\s*([A-Za-z_]\w*)")
_SCAN_NUM = re.compile(r"\bscan\s*\(\s*(\d+)\s*\)")
_SCAN_IDENT = re.compile(r"\bscan\s*\(\s*([A-Za-z_]\w*)\s*\)")
_NUM = re.compile(r"\b(\d+)\b")


def _js_assign_blobs(text, ident):
    """RHS of const/let/var ident = ... and ident = ... (paren-balanced)."""
    blobs = []
    starts = []
    for m in re.finditer(
        r"(?:(?:const|let|var)\s+)?" + re.escape(ident) + r"\s*=\s*",
        text,
    ):
        starts.append(m.end())
    for start in starts:
        depth = 0
        i = start
        while i < len(text):
            ch = text[i]
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth = max(0, depth - 1)
            elif ch == ";" and depth == 0:
                blobs.append(text[start:i])
                break
            i += 1
        else:
            blobs.append(text[start:i])
    return blobs


def _capped_at_32(blob):
    return bool(re.search(r"Math\.min\s*\(\s*32\s*,", blob))


def _distance_hits_in_file(name, text):
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        for m in _MAXD_NUM.finditer(line):
            n = int(m.group(1))
            if n > 32:
                hits.append(f"{name}:{i}:maxDistance {n}")
        for m in _SCAN_NUM.finditer(line):
            n = int(m.group(1))
            if n > 32:
                hits.append(f"{name}:{i}:scan({n})")
    idents = set(_MAXD_IDENT.findall(text)) | set(_SCAN_IDENT.findall(text))
    for ident in sorted(idents):
        if ident.isdigit():
            continue
        blobs = _js_assign_blobs(text, ident)
        if not blobs:
            # Parameter (e.g. scan = (dist) =>); callers with literals are
            # already covered by scan(N). Still flag uncapped external inject.
            continue
        for blob in blobs:
            over = [int(x) for x in _NUM.findall(blob) if int(x) > 32]
            if over and not _capped_at_32(blob):
                snippet = re.sub(r"\s+", " ", blob).strip()[:160]
                hits.append(
                    f"{name}:maxDistance var {ident} literals {over} in `{snippet}`"
                )
            if "__gatherMaxDistance" in blob and not _capped_at_32(blob):
                hits.append(f"{name}:uncapped __gatherMaxDistance into {ident}")
    return hits


def test_extra_js_no_slash_commands():
    """Skill extras must not send slash commands; vanilla arm cannot cheat.

    Also: findBlock/findBlocks maxDistance must stay <= 32 (vanilla action
    template). Variable distances (scan(dist), gather maxDist) count.
    """
    slash_hits = []
    dist_hits = []
    for name in _js_files(EXTRA):
        path = os.path.join(EXTRA, name)
        text = open(path, encoding="utf-8").read()
        for i, line in enumerate(text.splitlines(), 1):
            if _SLASH_CHAT.search(line) or _SLASH_CMDS.search(line):
                slash_hits.append(f"{name}:{i}:{line.strip()}")
        dist_hits.extend(_distance_hits_in_file(name, text))
    parts = []
    if slash_hits:
        parts.append("slash commands in extras:\n" + "\n".join(slash_hits))
    if dist_hits:
        parts.append("maxDistance > 32 in extras:\n" + "\n".join(dist_hits))
    assert not parts, "\n".join(parts)


def test_obtain_no_slash_and_no_progress_names_leaf():
    text = open(os.path.join(EXTRA, "obtain.js"), encoding="utf-8").read()
    assert "/tp" not in text
    assert "no_progress:" in text
    assert "_obtainStockSurface" in text
    assert "_obtainGoY" in text
    assert "_obtainEscape" in text
    assert "[obtain] escape" in text
    assert "scan(48)" not in text
    g = open(os.path.join(EXTRA, "gather.js"), encoding="utf-8").read()
    assert "no_progress" in g
    assert "? 64" not in g
    assert "Math.min(96" not in g
    assert "Math.min(32" in g


def test_obtain_mc_harness_uses_zs_spawn_not_y70():
    """Harness start must tp to ZS_SPAWN (x,y,z), never hardcode y=70."""
    path = os.path.join(ROOT, "_cc0924", "test_obtain_mc.py")
    text = open(path, encoding="utf-8").read()
    assert "ZS_SPAWN" in text
    assert "70 {z}" not in text
    assert "{x} 70 {z}" not in text
    assert "HarnessSpawnError" in text
    assert "standable" in text


def test_obtain_escape_only_when_enclosed():
    js = r"""
    const fs = require("fs");
    const { Vec3 } = require("vec3");
    eval(fs.readFileSync("voyager/control_primitives_extra/obtain.js", "utf8"));
    const air = { name: "air", boundingBox: "empty" };
    const grass = { name: "grass_block", boundingBox: "block" };
    const stone = { name: "stone", boundingBox: "block" };
    const bot = {
      entity: { position: new Vec3(51, 70, -240), health: 20, clone() { return this.position.clone(); } },
      blockAt(pos) {
        if (pos.y === 69) return grass;
        return air;
      },
      _obtainState: { escapeN: 0, pathFails: 5, lastPos: new Vec3(51, 70, -240) },
    };
    bot.entity.position.floored = function () { return new Vec3(Math.floor(this.x), Math.floor(this.y), Math.floor(this.z)); };
    bot.entity.position.clone = function () { return new Vec3(this.x, this.y, this.z); };
    bot.entity.position.distanceTo = function (o) { return Math.hypot(this.x-o.x, this.y-o.y, this.z-o.z); };
    if (_obtainIsEnclosed(bot)) throw new Error("open grass must not be enclosed");
    if (_obtainShouldEscape(bot)) throw new Error("open grass must not escape");
    const pit = {
      entity: { position: new Vec3(0, 10, 0), health: 20 },
      blockAt(pos) { return stone; },
      _obtainState: { escapeN: 0, pathFails: 2, lastPos: new Vec3(0, 10, 0) },
    };
    pit.entity.position.floored = function () { return new Vec3(0, 10, 0); };
    pit.entity.position.clone = function () { return new Vec3(this.x, this.y, this.z); };
    pit.entity.position.distanceTo = function (o) { return Math.hypot(this.x-o.x, this.y-o.y, this.z-o.z); };
    if (!_obtainIsEnclosed(pit)) throw new Error("solid pit should be enclosed");
    if (!_obtainShouldEscape(pit)) throw new Error("enclosed + pathFails should escape");
    pit._obtainState.escapeN = 3;
    if (_obtainShouldEscape(pit)) throw new Error("escape cap");
    console.log("ok");
    """
    env = dict(os.environ)
    env["NODE_PATH"] = os.path.join(ROOT, "voyager", "env", "mineflayer", "node_modules")
    proc = subprocess.run(["node", "-e", js], cwd=ROOT, capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "ok" in proc.stdout


def test_obtain_bom_gathers_logs_in_bulk():
    js = r"""
    const fs = require("fs");
    const mcData = require("minecraft-data")("1.19");
    const bot = {
      inventory: { items: () => [] },
      entity: { position: { x: 0, y: 64, z: 0 }, health: 20 },
      findBlock: () => null,
      findBlocks: () => [],
      chat() {},
      _obtainState: { rootItem: "diamond_pickaxe", rootNeed: 1 },
    };
    eval(fs.readFileSync("voyager/control_primitives_extra/obtain.js", "utf8"));
    const leaf = _obtainFirstLeaf(bot, "diamond_pickaxe", 1, 0);
    if (!leaf || leaf.kind !== "gather" || leaf.name !== "log") throw new Error("leaf=" + JSON.stringify(leaf));
    if (!(leaf.n >= 4)) throw new Error("expected bulk logs, n=" + leaf.n);
    const bom = _obtainBomNeed(bot, "diamond_pickaxe", 1);
    if (!(bom.log >= 4)) throw new Error("bom.log=" + bom.log);
    if (!(bom.cobblestone >= 8)) throw new Error("bom.cobble=" + bom.cobblestone);
    console.log("ok n=" + leaf.n + " log=" + bom.log + " cobble=" + bom.cobblestone);
    """
    env = dict(os.environ)
    env["NODE_PATH"] = os.path.join(ROOT, "voyager", "env", "mineflayer", "node_modules")
    proc = subprocess.run(["node", "-e", js], cwd=ROOT, capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "ok" in proc.stdout


def test_obtain_toss_reserves_bom_cobble():
    js = r"""
    const fs = require("fs");
    const tossed = [];
    const stacks = [
      { name: "cobblestone", type: 22, count: 20 },
      { name: "dirt", type: 3, count: 64 },
    ];
    const bot = {
      inventory: { items: () => stacks.slice() },
      inventoryUsed: () => 32,
      toss: async (type, _m, n) => { tossed.push([type, n]); },
      _obtainState: { rootItem: "furnace", rootNeed: 1 },
      entity: { position: { x: 0, y: 64, z: 0 }, health: 20 },
      findBlock: () => null,
      chat() {},
    };
    const mcData = require("minecraft-data")("1.19");
    eval(fs.readFileSync("voyager/control_primitives_extra/obtain.js", "utf8"));
    await _obtainMaybeToss(bot);
    const cobbleToss = tossed.filter((t) => t[0] === 22).reduce((s, t) => s + t[1], 0);
    if (cobbleToss > 4) throw new Error("tossed too much cobble " + cobbleToss + " tossed=" + JSON.stringify(tossed));
    console.log("ok tossed=" + JSON.stringify(tossed));
    """
    env = dict(os.environ)
    env["NODE_PATH"] = os.path.join(ROOT, "voyager", "env", "mineflayer", "node_modules")
    proc = subprocess.run(
        ["node", "-e", "void (async () => {" + js + "})()"],
        cwd=ROOT, capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "ok" in proc.stdout


def test_gather_walks_far_not_exploreuntil_20s():
    g = open(os.path.join(EXTRA, "gather.js"), encoding="utf-8").read()
    assert "_gatherWalkFar" in g
    assert "exploreUntil(bot, dir, Math.min(20" not in g
    o = open(os.path.join(EXTRA, "obtain.js"), encoding="utf-8").read()
    assert "phase_seconds" in o
    assert "_obtainBomNeed" in o
    assert "_obtainIsEnclosed" in o
    m = open(os.path.join(EXTRA, "mineOre.js"), encoding="utf-8").read()
    assert "no_progress y=" in m
    assert "stall >=" in m


def test_obtain_minedeep_still_caps_scan_at_32():
    src = open(os.path.join(EXTRA, "obtain.js"), encoding="utf-8").read()
    fn = src.split("async function _obtainMineDeep", 1)[1].split("async function ", 1)[0]
    assert "scan(32)" in fn
    assert "scan(48)" not in fn
    assert "maxDistance: 64" not in fn


def test_obtain_mixed_planks_golden_sword_leaf_not_null():
    """r2 seed 001 golden_sword/compass: 1 oak + 2 jungle planks made firstLeaf
    return null (haveP=3 for generic planks vs need 3 oak) → 4s not_found."""
    js = r"""
    const fs = require("fs");
    const mcData = require("minecraft-data")("1.19");
    const items = [
      { name: "stick", type: mcData.itemsByName.stick.id, count: 2 },
      { name: "jungle_planks", type: mcData.itemsByName.jungle_planks.id, count: 2 },
      { name: "oak_planks", type: mcData.itemsByName.oak_planks.id, count: 1 },
      { name: "oak_log", type: mcData.itemsByName.oak_log.id, count: 1 },
      { name: "crafting_table", type: mcData.itemsByName.crafting_table.id, count: 1 },
    ];
    const bot = {
      inventory: { items: () => items },
      entity: { position: { x: 74, y: 103, z: -232 }, health: 20 },
      findBlock: () => null,
      findBlocks: () => [],
      chat() {},
    };
    eval(fs.readFileSync("voyager/control_primitives_extra/obtain.js", "utf8"));
    const leaf = _obtainFirstLeaf(bot, "golden_sword", 1, 0);
    if (!leaf) throw new Error("firstLeaf null (r2 not_found)");
    const ok = (leaf.kind === "craft" && (leaf.item === "wooden_pickaxe" || String(leaf.item).endsWith("_planks")))
      || (leaf.kind === "gather" && leaf.name === "log")
      || (leaf.kind === "gather" && leaf.name === "cobblestone");
    if (!ok) throw new Error("unexpected leaf=" + JSON.stringify(leaf));
    const compass = _obtainFirstLeaf(bot, "compass", 1, 0);
    if (!compass) throw new Error("compass firstLeaf null");
    console.log("ok leaf=" + JSON.stringify(leaf) + " compass=" + JSON.stringify(compass));
    """
    env = dict(os.environ)
    env["NODE_PATH"] = os.path.join(ROOT, "voyager", "env", "mineflayer", "node_modules")
    proc = subprocess.run(["node", "-e", js], cwd=ROOT, capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "ok" in proc.stdout


def test_obtain_spruce_logs_first_leaf_crafts_not_gathers():
    """r3 seed 099: 16 spruce logs must not keep gathering (oak recipe lock)."""
    js = r"""
    const fs = require("fs");
    const mcData = require("minecraft-data")("1.19");
    const items = [
      { name: "spruce_log", type: mcData.itemsByName.spruce_log.id, count: 16 },
    ];
    const bot = {
      inventory: { items: () => items },
      entity: { position: { x: 0, y: 64, z: 0 }, health: 20 },
      findBlock: () => null,
      findBlocks: () => [],
      chat() {},
      _obtainState: { rootItem: "diamond_pickaxe", rootNeed: 1 },
    };
    eval(fs.readFileSync("voyager/control_primitives_extra/obtain.js", "utf8"));
    const leaf = _obtainFirstLeaf(bot, "diamond_pickaxe", 1, 0);
    if (!leaf) throw new Error("null leaf");
    if (leaf.kind === "gather" && leaf.name === "log") throw new Error("still gathering logs with 16 spruce: " + JSON.stringify(leaf));
    if (!(leaf.kind === "craft" && String(leaf.item).endsWith("_planks"))) {
      throw new Error("want craft planks, got " + JSON.stringify(leaf));
    }
    console.log("ok leaf=" + JSON.stringify(leaf));
    """
    env = dict(os.environ)
    env["NODE_PATH"] = os.path.join(ROOT, "voyager", "env", "mineflayer", "node_modules")
    proc = subprocess.run(["node", "-e", js], cwd=ROOT, capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "ok" in proc.stdout


def test_obtain_leaves_prefer_vanilla_primitives_and_rotate_strategy():
    text = open(os.path.join(EXTRA, "obtain.js"), encoding="utf-8").read()
    for name in ("mineBlock(", "exploreUntil(", "craftItem(", "smeltItem(", "placeItem("):
        assert name in text, name
    assert "[obtain] strategies " in text
    assert "_obtainRotateStrategy" in text
    assert "_obtainTunnelTo" in text
    assert "no_progress" in text
    # rotate is the in-call response to no_progress (do not abort the call)
    idx = text.find("indexOf(\"no_progress\")")
    assert idx >= 0
    assert "_obtainRotateStrategy" in text[idx:idx + 2500]


def test_obtain_mc_harness_world_spawn_non_099():
    path = os.path.join(ROOT, "_cc0924", "test_obtain_mc.py")
    text = open(path, encoding="utf-8").read()
    assert "ZS_SPAWN" in text
    assert "capture_world_spawn" in text or "world_spawn" in text
    assert "20260902099" in text
    assert "70 {z}" not in text
    assert "{x} 70 {z}" not in text


def test_obtain_returns_before_python_step_timeout():
    """In-flight vanilla primitives must not push past Python's 600s abort."""
    text = open(os.path.join(EXTRA, "obtain.js"), encoding="utf-8").read()
    assert "_obtainRemainMs" in text
    assert "RETURN_BUFFER_MS" in text
    assert "Promise.race" in text
    # pilot v2: the budget timer counts down from the (step-capped) deadline; equal to budgetSeconds without a stamp
    assert ("515000" in text or "budgetSeconds * 1000 - RETURN_BUFFER_MS" in text
            or "deadline - Date.now() - RETURN_BUFFER_MS" in text)


def test_obtain_mc_harness_hung_does_not_abort_job():
    """A hung call is one failed attempt; keep using the remaining 12-call budget."""
    path = os.path.join(ROOT, "_cc0924", "test_obtain_mc.py")
    text = open(path, encoding="utf-8").read()
    idx = text.find("if hung:")
    assert idx >= 0
    chunk = text[idx: text.find("if died:", idx)]
    assert "continue" in chunk
    assert "break" not in chunk


def test_obtain_log_strategies_never_descend():
    """r4: gather:log must not rotate onto descend (trees are not underground)."""
    js = r"""
    const fs = require("fs");
    eval(fs.readFileSync("voyager/control_primitives_extra/obtain.js", "utf8"));
    const list = (typeof _obtainStrategies === "function") ? _obtainStrategies("gather:log") : [];
    if (list.indexOf("descend") >= 0) throw new Error("descend in log strategies: " + JSON.stringify(list));
    if (list.indexOf("vanilla") < 0 || list.indexOf("explore") < 0) throw new Error("missing surface strats: " + JSON.stringify(list));
    console.log("ok " + JSON.stringify(list));
    """
    env = dict(os.environ)
    env["NODE_PATH"] = os.path.join(ROOT, "voyager", "env", "mineflayer", "node_modules")
    proc = subprocess.run(["node", "-e", js], cwd=ROOT, capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "ok" in proc.stdout


def test_obtain_bom_includes_pickaxe_handles_and_margin():
    """r4: diamond pickaxe BOM must cover table + every pickaxe handle + 2-4 extra logs."""
    js = r"""
    const fs = require("fs");
    const mcData = require("minecraft-data")("1.19");
    const bot = {
      inventory: { items: () => [] },
      entity: { position: { x: 0, y: 64, z: 0 }, health: 20 },
      findBlock: () => null,
      findBlocks: () => [],
      chat() {},
    };
    eval(fs.readFileSync("voyager/control_primitives_extra/obtain.js", "utf8"));
    const bom = _obtainBomNeed(bot, "diamond_pickaxe", 1);
    if (!(bom.log >= 8)) throw new Error("bom.log too small (need handles+margin) log=" + bom.log);
    console.log("ok log=" + bom.log + " cobble=" + bom.cobblestone);
    """
    env = dict(os.environ)
    env["NODE_PATH"] = os.path.join(ROOT, "voyager", "env", "mineflayer", "node_modules")
    proc = subprocess.run(["node", "-e", js], cwd=ROOT, capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "ok" in proc.stdout


def test_obtain_abort_stops_collectblock():
    """r4: vanilla mineBlock/collectBlock must be cancelled when the obtain budget fires."""
    text = open(os.path.join(EXTRA, "obtain.js"), encoding="utf-8").read()
    assert "_obtainAbortMotions" in text
    assert "cancelTask" in text
    assert "__obtainAborted" in text
    assert "collectBlock" in text


def test_obtain_death_resyncs_bom():
    """r4: after death, restock flag clears so BOM is recomputed from the empty bag."""
    text = open(os.path.join(EXTRA, "obtain.js"), encoding="utf-8").read()
    assert "death resync" in text or "_obtainOnDeath" in text
    assert "stocked = false" in text or 'stocked = !1' in text


def test_obtain_safe_dig_skips_cliff():
    """r4: do not dig a floor block with air under it (fall), besides lava/feet."""
    text = open(os.path.join(EXTRA, "mineOre.js"), encoding="utf-8").read()
    assert "cliff" in text
    assert "_mineOreIsFeet" in text
    src = open(os.path.join(EXTRA, "obtain.js"), encoding="utf-8").read()
    assert "_mineOreSafeDig" in src


def test_obtain_mc_harness_tp_retries_until_near_spawn():
    """r4: /tp must be verified; >2 blocks away retries up to 3, else harness fail."""
    path = os.path.join(ROOT, "_cc0924", "test_obtain_mc.py")
    text = open(path, encoding="utf-8").read()
    assert "ensure_at_spawn" in text or "tp_retry" in text
    assert "harness_spawn" in text
    assert "start_inv" in text
    assert "obtain_mc_runs_r5.jsonl" in text
    assert "obtain_mc_r5.log" in text
    assert "> 2" in text or "distance" in text.lower() or "near_spawn" in text


def _node_obtain(js_body):
    env = dict(os.environ)
    env["NODE_PATH"] = os.path.join(ROOT, "voyager", "env", "mineflayer", "node_modules")
    proc = subprocess.run(["node", "-e", js_body], cwd=ROOT, capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    return proc.stdout


def test_obtain_craft_smelt_strategies_vanilla_only():
    """r5: movement rotation is for gather/mine leaves, not craft/smelt."""
    js = r"""
    const fs = require("fs");
    eval(fs.readFileSync("voyager/control_primitives_extra/obtain.js", "utf8"));
    const craft = _obtainStrategies("craft:furnace");
    const smelt = _obtainStrategies("smelt:raw_iron");
    const gather = _obtainStrategies("gather:cobblestone");
    if (craft.some((s) => s !== "vanilla")) throw new Error("craft strats " + JSON.stringify(craft));
    if (smelt.some((s) => s !== "vanilla")) throw new Error("smelt strats " + JSON.stringify(smelt));
    if (gather.indexOf("vanilla") < 0 || gather.indexOf("explore") < 0) throw new Error("gather " + JSON.stringify(gather));
    console.log("ok craft=" + JSON.stringify(craft) + " smelt=" + JSON.stringify(smelt) + " gather=" + JSON.stringify(gather));
    """
    out = _node_obtain(js)
    assert "ok" in out


def test_obtain_furnace_with_one_cobble_gathers_deficit():
    """r5: 1 cobblestone cannot craft a furnace; firstLeaf must gather the missing 7+."""
    js = r"""
    const fs = require("fs");
    const mcData = require("minecraft-data")("1.19");
    const items = [
      { name: "cobblestone", type: mcData.itemsByName.cobblestone.id, count: 1 },
      { name: "crafting_table", type: mcData.itemsByName.crafting_table.id, count: 1 },
      { name: "wooden_pickaxe", type: mcData.itemsByName.wooden_pickaxe.id, count: 1 },
    ];
    const bot = {
      inventory: { items: () => items },
      entity: { position: { x: 0, y: 64, z: 0 }, health: 20 },
      findBlock: () => null,
      findBlocks: () => [],
      chat() {},
      _obtainState: { rootItem: "furnace", rootNeed: 1 },
    };
    eval(fs.readFileSync("voyager/control_primitives_extra/obtain.js", "utf8"));
    const leaf = _obtainFirstLeaf(bot, "furnace", 1, 0);
    if (!leaf) throw new Error("null leaf");
    if (!(leaf.kind === "gather" && leaf.name === "cobblestone")) {
      throw new Error("want gather cobblestone, got " + JSON.stringify(leaf));
    }
    if (!(leaf.n >= 7)) throw new Error("deficit n=" + leaf.n);
    const miss = (typeof _obtainMissingIngs === "function") ? _obtainMissingIngs(bot, "furnace", 1) : [];
    if (!miss.length) throw new Error("missingIngs empty");
    const cob = miss.find((m) => m.name === "cobblestone");
    if (!cob || cob.gap < 7) throw new Error("missingIngs " + JSON.stringify(miss));
    console.log("ok leaf=" + JSON.stringify(leaf) + " miss=" + JSON.stringify(miss));
    """
    out = _node_obtain(js)
    assert "ok" in out


def test_obtain_smelt_without_fuel_does_not_return_smelt_leaf():
    """r5: 3 raw_iron + furnace but no coal/planks/logs → get fuel, do not smelt yet.
    Sticks must not count as furnace fuel (smelt.js never burns them)."""
    js = r"""
    const fs = require("fs");
    const mcData = require("minecraft-data")("1.19");
    const items = [
      { name: "raw_iron", type: mcData.itemsByName.raw_iron.id, count: 3 },
      { name: "furnace", type: mcData.itemsByName.furnace.id, count: 1 },
      { name: "stick", type: mcData.itemsByName.stick.id, count: 2 },
      { name: "crafting_table", type: mcData.itemsByName.crafting_table.id, count: 1 },
      { name: "stone_pickaxe", type: mcData.itemsByName.stone_pickaxe.id, count: 1 },
    ];
    const bot = {
      inventory: { items: () => items },
      entity: { position: { x: 0, y: 16, z: 0 }, health: 20 },
      findBlock: (opts) => {
        if (opts && opts.matching === mcData.blocksByName.furnace.id) {
          return { name: "furnace", position: { x: 0, y: 16, z: 1, distanceTo() { return 1; } } };
        }
        return null;
      },
      findBlocks: () => [],
      chat() {},
      _obtainState: { rootItem: "iron_ingot", rootNeed: 3 },
    };
    eval(fs.readFileSync("voyager/control_primitives_extra/obtain.js", "utf8"));
    const leaf = _obtainFirstLeaf(bot, "iron_ingot", 3, 0);
    if (!leaf) throw new Error("null leaf");
    if (leaf.kind === "smelt") throw new Error("smelted with no real fuel: " + JSON.stringify(leaf));
    const fuelish = (leaf.kind === "gather" && (leaf.name === "log" || leaf.name === "coal"))
      || (leaf.kind === "craft" && String(leaf.item).endsWith("_planks"));
    if (!fuelish) throw new Error("want fuel leaf, got " + JSON.stringify(leaf));
    const units = _obtainFuelUnits(bot);
    if (units > 0) throw new Error("sticks counted as fuel units=" + units);
    console.log("ok leaf=" + JSON.stringify(leaf) + " fuelUnits=" + units);
    """
    out = _node_obtain(js)
    assert "ok" in out


def test_obtain_bom_counts_smelt_fuel():
    """r5: diamond pickaxe BOM must reserve fuel units for the 3 iron smelts."""
    js = r"""
    const fs = require("fs");
    const mcData = require("minecraft-data")("1.19");
    const bot = {
      inventory: { items: () => [] },
      entity: { position: { x: 0, y: 64, z: 0 }, health: 20 },
      findBlock: () => null,
      findBlocks: () => [],
      chat() {},
    };
    eval(fs.readFileSync("voyager/control_primitives_extra/obtain.js", "utf8"));
    const bom = _obtainBomNeed(bot, "diamond_pickaxe", 1);
    if (!((bom.fuel || 0) >= 3)) throw new Error("bom.fuel missing/too small " + JSON.stringify(bom));
    if (!(bom.log >= 8)) throw new Error("bom.log should still cover handles+fuel+margin log=" + bom.log);
    console.log("ok bom=" + JSON.stringify(bom));
    """
    out = _node_obtain(js)
    assert "ok" in out


def test_obtain_toss_keeps_cobble_separate_from_deepslate():
    """r5: cobbled_deepslate must not satisfy the cobblestone BOM reserve."""
    js = r"""
    const fs = require("fs");
    const tossed = [];
    const stacks = [
      { name: "cobblestone", type: 22, count: 8 },
      { name: "cobbled_deepslate", type: 8, count: 64 },
      { name: "dirt", type: 3, count: 64 },
      { name: "granite", type: 1, count: 64 },
    ];
    const bot = {
      inventory: { items: () => stacks.slice() },
      inventoryUsed: () => 32,
      toss: async (type, _m, n) => { tossed.push([type, n]); },
      _obtainState: { rootItem: "furnace", rootNeed: 1 },
      entity: { position: { x: 0, y: 64, z: 0 }, health: 20 },
      findBlock: () => null,
      chat() {},
    };
    const mcData = require("minecraft-data")("1.19");
    eval(fs.readFileSync("voyager/control_primitives_extra/obtain.js", "utf8"));
    await _obtainMaybeToss(bot);
    const cobbleToss = tossed.filter((t) => t[0] === 22).reduce((s, t) => s + t[1], 0);
    if (cobbleToss > 0) throw new Error("tossed cobblestone needed for furnace " + cobbleToss + " tossed=" + JSON.stringify(tossed));
    console.log("ok tossed=" + JSON.stringify(tossed));
    """
    env = dict(os.environ)
    env["NODE_PATH"] = os.path.join(ROOT, "voyager", "env", "mineflayer", "node_modules")
    proc = subprocess.run(
        ["node", "-e", "void (async () => {" + js + "})()"],
        cwd=ROOT, capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "ok" in proc.stdout


def test_obtain_craft_fail_reexpands_not_rotates():
    """r5: craft/smelt no_progress must re-expand missing ings, not rotate walk strategies."""
    text = open(os.path.join(EXTRA, "obtain.js"), encoding="utf-8").read()
    assert "_obtainMissingIngs" in text
    assert "_obtainRecoverCraftOrSmelt" in text
    assert "reexpand" in text
    assert "missing_fuel" in text
    idx = text.find("async function obtain")
    body = text[idx: idx + 12000]
    # rotate remains for gather/mineDeep; craft/smelt must go through recover
    assert "_obtainRecoverCraftOrSmelt" in body
    assert "_obtainIsMoveLeaf" in text or "leaf.kind === \"gather\"" in body


def test_obtain_done_requires_inventory_count():
    """r3fix: obtain must not report done when the bag is short of the target."""
    text = open(os.path.join(EXTRA, "obtain.js"), encoding="utf-8").read()
    assert 'String(reason) === "done" && haveNow < need' in text
    smelt = text.split("async function _obtainSmeltVanilla", 1)[1].split("async function _obtainGatherWithStrategy", 1)[0]
    assert "reason: \"partial\"" in smelt
    path = os.path.join(ROOT, "_cc0924", "test_obtain_mc.py")
    harness = open(path, encoding="utf-8").read()
    assert "parse_obtain_contract" in harness
    assert "[contract] obtain " in harness
    assert "obtain_lines[-1]" in harness


def test_obtain_twobyTwo_craft_calls_craftitem_without_table():
    """2x2 recipes (planks/sticks/table) must call craftItem even with no placed table."""
    text = open(os.path.join(EXTRA, "obtain.js"), encoding="utf-8").read()
    fn = text.split("async function _obtainCraftVanilla", 1)[1].split("async function _obtainSmeltVanilla", 1)[0]
    assert "needsTable" in fn
    assert "craftItem" in fn
    # the no-table branch must still invoke craftItem
    assert "else if (typeof craftItem" in fn or "2x2" in fn


def test_obtain_mc_harness_js_snippets_node_check():
    """Every env.step JS fragment in the obtain harness must parse under node --check."""
    import importlib.util
    import tempfile

    path = os.path.join(ROOT, "_cc0924", "test_obtain_mc.py")
    spec = importlib.util.spec_from_file_location("test_obtain_mc_harness", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fails = []
    for name, js in mod.harness_js_snippets():
        wrapped = "async function __check() {\n" + js + "\n}\n"
        fd, tmp = tempfile.mkstemp(suffix=".js")
        os.write(fd, wrapped.encode("utf-8"))
        os.close(fd)
        proc = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
        os.unlink(tmp)
        if proc.returncode != 0:
            fails.append(f"{name}: {proc.stderr.strip()}")
    assert not fails, "\n".join(fails)


def test_obtain_mc_harness_fresh_world_per_scene():
    path = os.path.join(ROOT, "_cc0924", "test_obtain_mc.py")
    text = open(path, encoding="utf-8").read()
    assert "fresh_world" in text
    assert "OBTAIN_SCENES" in text
    assert "harness_check" in text
    assert "w{wid}_{seed}_{scene" in text or 'f"w{wid}_{seed}_{scene' in text
    assert "js_empty_bag" in text
    assert "prepare_start" in text


def _mock_bot_js(kind):
    extra_items = ""
    target = "stone_pickaxe"
    if kind == "craft":
        extra_items = """
      { name: "cobblestone", type: mcData.itemsByName.cobblestone.id, count: 8 },
      { name: "stick", type: mcData.itemsByName.stick.id, count: 4 },
      { name: "oak_planks", type: mcData.itemsByName.oak_planks.id, count: 8 },
      { name: "crafting_table", type: mcData.itemsByName.crafting_table.id, count: 1 },
      { name: "wooden_pickaxe", type: mcData.itemsByName.wooden_pickaxe.id, count: 1 },
        """
        target = "stone_pickaxe"
    else:
        extra_items = """
      { name: "raw_iron", type: mcData.itemsByName.raw_iron.id, count: 3 },
      { name: "furnace", type: mcData.itemsByName.furnace.id, count: 1 },
      { name: "oak_planks", type: mcData.itemsByName.oak_planks.id, count: 16 },
      { name: "stone_pickaxe", type: mcData.itemsByName.stone_pickaxe.id, count: 1 },
      { name: "crafting_table", type: mcData.itemsByName.crafting_table.id, count: 1 },
        """
        target = "iron_ingot"
    return r"""
    const fs = require("fs");
    const { Vec3 } = require("vec3");
    const mcData = require("minecraft-data")("1.19");
    globalThis.GoalBlock = class GoalBlock { constructor(x, y, z) { this.x = x; this.y = y; this.z = z; } };
    globalThis.GoalLookAtBlock = class GoalLookAtBlock { constructor() {} };
    globalThis.GoalPlaceBlock = class GoalPlaceBlock { constructor() {} };
    const items = [
%s
    ];
    const air = { name: "air", boundingBox: "empty" };
    const ground = { name: "grass_block", boundingBox: "block" };
    const pos = new Vec3(0, 64, 0);
    const bot = {
      inventory: {
        items: () => items.slice(),
        slots: [],
        findInventoryItem: (id) => items.find((i) => i.type === id) || null,
      },
      inventoryUsed: () => items.length,
      entity: { position: pos, health: 20 },
      heldItem: null,
      pathfinder: { setGoal() {}, stop() {}, isMoving() { return false; }, goto: async () => {} },
      chat(m) { this._chats.push(String(m)); },
      _chats: [],
      waitForTicks: async () => { await new Promise((r) => setImmediate(r)); },
      equip: async () => {},
      dig: async () => {},
      toss: async () => {},
      tossStack: async () => {},
      placeBlock: async () => {},
      look: async () => {},
      lookAt: async () => {},
      setControlState() {},
      stopDigging() {},
      recipesFor: () => [],
      craft: async () => {},
      findBlock(opts) {
        const id = opts && opts.matching;
        if (id === mcData.blocksByName.crafting_table.id) {
          return { name: "crafting_table", position: new Vec3(0, 64, 1) };
        }
        if (id === mcData.blocksByName.furnace.id) {
          return { name: "furnace", position: new Vec3(0, 64, 1) };
        }
        return null;
      },
      findBlocks: () => [],
      blockAt(p) {
        if (p && p.y < 64) return ground;
        return air;
      },
      _obtainState: {},
    };
    eval(fs.readFileSync("voyager/control_primitives_extra/obtain.js", "utf8"));
    const t0 = Date.now();
    const r = await obtain(bot, "%s", 1);
    const dt = Date.now() - t0;
    const loops = (bot._obtainState && bot._obtainState.loops) || 0;
    if (dt > 60000) throw new Error("too slow ms=" + dt + " r=" + JSON.stringify(r));
    if (loops > 10) throw new Error("too many loops=" + loops + " r=" + JSON.stringify(r));
    if (!r || r.ok) throw new Error("expected fail, got " + JSON.stringify(r));
    const reason = String(r.reason || "");
    if (reason.indexOf("blocked:") !== 0) throw new Error("want blocked:* got " + reason + " loops=" + loops);
    console.log("ok loops=" + loops + " reason=" + reason + " ms=" + dt);
    """ % (extra_items, target)


def test_obtain_craft_no_progress_blocks_within_10_loops():
    """Materials present but craft never succeeds → blocked:<kind>:<item> in ≤10 loops / 60s."""
    js = "void (async () => {\n" + _mock_bot_js("craft") + "\n})().catch((e) => { console.error(e); process.exit(1); });"
    env = dict(os.environ)
    env["NODE_PATH"] = os.path.join(ROOT, "voyager", "env", "mineflayer", "node_modules")
    proc = subprocess.run(["node", "-e", js], cwd=ROOT, capture_output=True, text=True, env=env, timeout=70)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "ok loops=" in proc.stdout
    assert "blocked:" in proc.stdout


def test_obtain_smelt_no_progress_blocks_within_10_loops():
    """Smelt leaf stuck with no_progress → blocked:smelt:* in ≤10 loops / 60s."""
    js = "void (async () => {\n" + _mock_bot_js("smelt") + "\n})().catch((e) => { console.error(e); process.exit(1); });"
    env = dict(os.environ)
    env["NODE_PATH"] = os.path.join(ROOT, "voyager", "env", "mineflayer", "node_modules")
    proc = subprocess.run(["node", "-e", js], cwd=ROOT, capture_output=True, text=True, env=env, timeout=70)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "ok loops=" in proc.stdout
    assert "blocked:" in proc.stdout


def test_obtain_minedeep_own_peak_not_deepest_in_tree():
    src = open(os.path.join(EXTRA, "obtain.js"), encoding="utf-8").read()
    fn = src.split("async function _obtainMineDeep", 1)[1].split("async function ", 1)[0]
    assert "let destY = spec.peakY" in fn
    assert "if (s.peakY < destY) destY = s.peakY" not in fn


def test_obtain_gen_not_deleted_in_finally():
    src = open(os.path.join(EXTRA, "obtain.js"), encoding="utf-8").read()
    body = src.split("async function obtain", 1)[1].split("function _obtainIsSurfaceGather", 1)[0]
    assert "__obtainGen" in body
    assert "delete globalThis.__obtainAborted" not in body
    assert "_obtainWaitStopped" in body

