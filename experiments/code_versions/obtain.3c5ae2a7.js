// Recipe-tree executor. n is the TOTAL to own. 1.19 recipes + drop tables.
// Deep ores: large-radius findBlocks (loaded chunks), then peak Y scan. No strip.
// Source: Minecraft Wiki Ore / Java 1.18–1.19 height; tool tiers from Breaking.
const OBTAIN_KNOWLEDGE = JSON.parse(`{
  "source": "Minecraft Wiki page Ore and Breaking; Java Edition 1.19. https://minecraft.wiki/w/Ore https://minecraft.wiki/w/Breaking",
  "budgetSeconds": 540,
  "minDurability": 16,
  "globalMaxCalls": 16,
  "keepPlaceBlocks": 16,
  "maxEscapePerCall": 3,
  "discard": ["cobblestone", "cobbled_deepslate", "dirt", "granite", "diorite", "andesite", "tuff", "gravel", "netherrack"],
  "woodOrder": ["oak", "spruce", "birch", "jungle", "acacia", "dark_oak", "mangrove"],
  "stoneOrder": ["cobblestone", "blackstone", "cobbled_deepslate"],
  "smelt": {
    "raw_iron": "iron_ingot",
    "raw_gold": "gold_ingot",
    "raw_copper": "copper_ingot",
    "cobblestone": "stone",
    "sand": "glass",
    "kelp": "dried_kelp",
    "cactus": "green_dye",
    "netherrack": "nether_brick",
    "clay_ball": "brick",
    "beef": "cooked_beef",
    "porkchop": "cooked_porkchop",
    "chicken": "cooked_chicken",
    "mutton": "cooked_mutton",
    "cod": "cooked_cod",
    "salmon": "cooked_salmon",
    "potato": "baked_potato",
    "iron_ore": "iron_ingot",
    "gold_ore": "gold_ingot",
    "copper_ore": "copper_ingot"
  },
  "deep": {
    "diamond": {"peakY": -58, "minTool": "iron", "blocks": ["diamond_ore", "deepslate_diamond_ore"], "drops": ["diamond"]},
    "raw_gold": {"peakY": -16, "minTool": "iron", "blocks": ["gold_ore", "deepslate_gold_ore"], "drops": ["raw_gold"]},
    "redstone": {"peakY": -58, "minTool": "iron", "blocks": ["redstone_ore", "deepslate_redstone_ore"], "drops": ["redstone"]},
    "lapis_lazuli": {"peakY": 0, "minTool": "stone", "blocks": ["lapis_ore", "deepslate_lapis_ore"], "drops": ["lapis_lazuli"]},
    "raw_iron": {"peakY": 16, "minTool": "stone", "blocks": ["iron_ore", "deepslate_iron_ore"], "drops": ["raw_iron"]}
  }
}`);

function _obtainRecipeBetter(a, b) {
    const haveA = (a.ings || []).filter((i) => (i.have || 0) > 0).length;
    const haveB = (b.ings || []).filter((i) => (i.have || 0) > 0).length;
    if (haveA !== haveB) return haveA > haveB;
    const ua = a.unique != null ? a.unique : (a.ings || []).length;
    const ub = b.unique != null ? b.unique : (b.ings || []).length;
    if (ua !== ub) return ua < ub;
    const wood = (OBTAIN_KNOWLEDGE && OBTAIN_KNOWLEDGE.woodOrder) || ["oak", "spruce", "birch", "jungle", "acacia", "dark_oak", "mangrove"];
    const woodRank = (r) => {
        let best = 99;
        for (const ing of r.ings || []) {
            const n = String(ing.name || "");
            for (let i = 0; i < wood.length; i++) {
                if (n.startsWith(wood[i] + "_")) best = Math.min(best, i);
            }
        }
        return best;
    };
    const wa = woodRank(a);
    const wb = woodRank(b);
    if (wa !== wb) return wa < wb;
    // cobbled_deepslate id 9 < cobblestone 22. Min-id tiebreak used to pick
    // deepslate on an empty bag → gather missing_tool → 240s spin, no [contract].
    const stone = (OBTAIN_KNOWLEDGE && OBTAIN_KNOWLEDGE.stoneOrder) || ["cobblestone", "blackstone", "cobbled_deepslate"];
    const stoneRank = (r) => {
        let best = 99;
        for (const ing of r.ings || []) {
            const idx = stone.indexOf(String(ing.name || ""));
            if (idx >= 0) best = Math.min(best, idx);
        }
        return best;
    };
    const sa = stoneRank(a);
    const sb = stoneRank(b);
    if (sa !== sb) return sa < sb;
    const idsA = (a.ings || []).map((i) => i.id || 99999);
    const idsB = (b.ings || []).map((i) => i.id || 99999);
    const idA = idsA.length ? Math.min.apply(null, idsA) : 99999;
    const idB = idsB.length ? Math.min.apply(null, idsB) : 99999;
    return idA < idB;
}

function _obtainPhaseKeys() {
    return ["surface", "descend", "search_ore", "mine", "smelt", "craft", "path_fail", "escape"];
}

function _obtainPhaseInit(bot) {
    const seconds = {};
    for (const k of _obtainPhaseKeys()) seconds[k] = 0;
    bot._obtainPhase = { cur: "surface", curT: Date.now(), seconds: seconds };
}

function _obtainPhase(bot, name) {
    const p = bot._obtainPhase;
    if (!p) return;
    const now = Date.now();
    if (p.cur && p.seconds[p.cur] != null) {
        p.seconds[p.cur] += (now - p.curT) / 1000;
    }
    p.cur = name;
    p.curT = now;
}

function _obtainPhaseFlush(bot) {
    const p = bot._obtainPhase;
    if (!p) return {};
    _obtainPhase(bot, p.cur || "surface");
    const out = {};
    for (const k of _obtainPhaseKeys()) out[k] = Math.round((p.seconds[k] || 0) * 10) / 10;
    const line = "[obtain] phase_seconds " + JSON.stringify(out);
    try { console.log(line); } catch (err) {}
    return out;
}

function _obtainAborted(gen) {
    if (gen != null && typeof globalThis.__obtainGen === "number" && gen !== globalThis.__obtainGen) return true;
    // outside an obtain call nothing is aborted: mineOre / gather / smelt called on their own must work normally
    if (!globalThis.__obtainInCall) return false;
    if (globalThis.__obtainAborted) return true;
    if (typeof globalThis.__obtainDeadline === "number" && Date.now() >= globalThis.__obtainDeadline) return true;
    return false;
}

function _obtainRemainMs(capMs) {
    const cap = Math.max(1000, capMs || 40000);
    const RETURN_BUFFER_MS = 10000;
    if (!globalThis.__obtainInCall) return cap;
    if (globalThis.__obtainAborted) return 1;
    if (typeof globalThis.__obtainDeadline === "number") {
        const left = globalThis.__obtainDeadline - Date.now() - RETURN_BUFFER_MS;
        if (left <= 0) return 1;
        return Math.min(cap, left);
    }
    return cap;
}

function _obtainChat(bot, line, opts) {
    const text = String(line || "");
    try { console.log(text); } catch (err) {}
    const force = !!(opts && (opts.contract || opts.force));
    if (force) {
        try { bot.chat(text); } catch (err) {}
        return;
    }
    globalThis.__obtainChatState = globalThis.__obtainChatState || { last: "", t: 0 };
    const st = globalThis.__obtainChatState;
    if (text === st.last) return;
    const now = Date.now();
    if (now - (st.t || 0) < 2000) return;
    st.last = text;
    st.t = now;
    try { bot.chat(text); } catch (err) {}
}

function _obtainInvSig(bot) {
    try {
        return bot.inventory.items().map((i) => i.name + ":" + i.count).sort().join(",") || "-";
    } catch (err) {
        return "-";
    }
}

function _obtainLeafKey(leaf) {
    if (!leaf) return "unknown";
    return String(leaf.kind || "leaf") + ":" + String(leaf.name || leaf.item || "");
}

function _obtainPosKey(bot) {
    try {
        const p = bot.entity.position;
        return Math.round(p.x * 2) / 2 + "," + Math.round(p.y * 2) / 2 + "," + Math.round(p.z * 2) / 2;
    } catch (err) {
        return "?";
    }
}

async function _obtainWaitStopped(bot) {
    try { bot.pathfinder.setGoal(null); } catch (e) {}
    try { bot.pathfinder.stop(); } catch (e) {}
    try { bot.stopDigging(); } catch (e) {}
    const cap = Date.now() + 3000;
    while (Date.now() < cap) {
        let moving = false;
        try {
            if (bot.pathfinder && typeof bot.pathfinder.isMoving === "function") moving = !!bot.pathfinder.isMoving();
        } catch (e) {}
        if (!moving) break;
        try { await bot.waitForTicks(2); } catch (e) { break; }
    }
}

async function _obtainGoto(bot, goal) {
    if (_obtainAborted()) return false;
    if (!bot.pathfinder || typeof bot.pathfinder.goto !== "function") return false;
    try { bot.pathfinder.setGoal(null); } catch (e) {}
    try {
        await bot.pathfinder.goto(goal);
        return true;
    } catch (err) {
        return false;
    }
}

async function _obtainRetreat(bot, n, timedOut) {
    if (!bot.entity || !bot.entity.position) return false;
    const p = bot.entity.position.floored();
    const heading = (bot._obtainState && bot._obtainState.heading) || 0;
    const dirs = [[1, 0], [-1, 0], [0, 1], [0, -1]];
    const d = dirs[heading % dirs.length];
    const back = [-d[0], -d[1]];
    for (let i = 1; i <= (n || 3); i++) {
        if (timedOut && timedOut()) return false;
        const dest = p.offset(back[0] * i, 0, back[1] * i);
        const blk = bot.blockAt(dest);
        if (blk && blk.boundingBox === "block") {
            try { await bot.dig(blk); } catch (err) {}
        }
        const head = bot.blockAt(dest.offset(0, 1, 0));
        if (head && head.boundingBox === "block") {
            try { await bot.dig(head); } catch (err) {}
        }
        try {
            if (typeof _mineOreWalk === "function") await _mineOreWalk(bot, dest, 800);
        } catch (err) {}
    }
    return true;
}

async function _obtainClearWorkspace(bot, leaf, timedOut) {
    if (!bot.entity || !bot.entity.position) return false;
    const p = bot.entity.position.floored();
    const dig = async (pos) => {
        try {
            const b = bot.blockAt(pos);
            if (!b || b.name === "air" || b.name === "cave_air" || b.name === "void_air" || b.name === "bedrock") return;
            if (typeof _mineOreSafeDig === "function") await _mineOreSafeDig(bot, pos, { seal: false });
            else await bot.dig(b);
        } catch (err) {}
    };
    for (let dx = -1; dx <= 1; dx++) {
        for (let dz = -1; dz <= 1; dz++) {
            if (timedOut && timedOut()) return false;
            if (dx === 0 && dz === 0) {
                await dig(p.offset(0, 1, 0));
                continue;
            }
            await dig(p.offset(dx, 0, dz));
            await dig(p.offset(dx, 1, dz));
        }
    }
    const placeName = (leaf && leaf.kind === "smelt") ? "furnace" : "crafting_table";
    let placed = await _obtainEnsurePlaced(bot, placeName, timedOut);
    if (placed) return true;
    await _obtainRetreat(bot, 3, timedOut);
    placed = await _obtainEnsurePlaced(bot, placeName, timedOut);
    return !!placed;
}

function _obtainLogPicks(bot) {
    try {
        const picks = bot.inventory.items().filter((i) => i.name && i.name.endsWith("_pickaxe"));
        for (const p of picks) {
            console.log("[obtain] pick " + p.name + " dura=" + _obtainDura(p));
        }
        if (!picks.length) console.log("[obtain] pick none");
    } catch (err) {}
}

function _obtainLowestPick(bot, minTier) {
    const TIER = { wooden: 0, golden: 1, stone: 1, iron: 2, diamond: 3, netherite: 4 };
    const minIdx = TIER[minTier] == null ? 0 : TIER[minTier];
    const picks = bot.inventory.items().filter((i) => i.name && i.name.endsWith("_pickaxe"));
    picks.sort((a, b) => {
        const ta = TIER[a.name.split("_")[0]] || 0;
        const tb = TIER[b.name.split("_")[0]] || 0;
        if (ta !== tb) return ta - tb;
        return _obtainDura(b) - _obtainDura(a);
    });
    for (const p of picks) {
        const t = TIER[p.name.split("_")[0]];
        if (t != null && t >= minIdx && _obtainDura(p) >= (OBTAIN_KNOWLEDGE.minDurability || 16)) return p;
    }
    return null;
}

function _obtainBlockMinPick(blockName) {
    const n = String(blockName || "");
    if (n.indexOf("diamond") >= 0 || n.indexOf("gold") >= 0 || n.indexOf("redstone") >= 0) return "iron";
    if (n.indexOf("iron_ore") >= 0 || n === "raw_iron") return "stone";
    return "wooden";
}

async function _obtainEquipDigTool(bot, blockName) {
    const min = _obtainBlockMinPick(blockName);
    const pick = _obtainLowestPick(bot, min) || _obtainLowestPick(bot, "wooden");
    if (!pick) return null;
    try { await bot.equip(pick, "hand"); } catch (err) {}
    return pick;
}

async function _obtainMaybeKeepStonePick(bot, timedOut) {
    // Always carry a working low-tier pickaxe plus a spare for stone/deepslate: the iron one must not wear out on stone
    // (09-26 seed 099), and running out of pickaxes underground with no wood is a dead end (09-26 seed 010: two calls
    // stuck at y=35 with no pickaxe).
    const low = bot.inventory.items().filter((i) => i.name === "stone_pickaxe" || i.name === "wooden_pickaxe");
    const good = low.filter((i) => _obtainDura(i) >= 16);
    const fresh = low.filter((i) => _obtainDura(i) >= 80);
    if (fresh.length >= 1 && good.length >= 2) return false;
    bot._obtainState.stonePickTries = (bot._obtainState.stonePickTries || 0) + 1;
    if (bot._obtainState.stonePickTries > 3) return false;
    const stone = _obtainCount(bot, "cobblestone") + _obtainCount(bot, "cobbled_deepslate") + _obtainCount(bot, "blackstone");
    if (stone < 3) return false;
    if (_obtainCount(bot, "stick") < 2 && _obtainCount(bot, "planks") < 1 && _obtainCount(bot, "log") < 1) return false;
    try { console.log("[obtain] keep a spare stone_pickaxe good=" + good.length); } catch (err) {}
    await _obtainDoLeaf(bot, { kind: "craft", item: "stone_pickaxe", n: _obtainCount(bot, "stone_pickaxe") + 1 }, timedOut);
    return true;
}

async function _obtainMaybeReplaceIronPick(bot, timedOut) {
    // judge by the best iron pickaxe (a fresh one may sit in a later slot) and replace at most once per call
    const irons = bot.inventory.items().filter((i) => i.name === "iron_pickaxe");
    if (!irons.length) return false;
    const iron = irons.reduce((a, b) => (_obtainDura(b) > _obtainDura(a) ? b : a));
    if ((bot._obtainState.ironReplaceTries || 0) >= 1) return false;
    if (_obtainDura(iron) >= 40) return false;
    if (_obtainCount(bot, "iron_ingot") < 3) return false;
    if (_obtainCount(bot, "stick") < 2) return false;
    bot._obtainState.ironReplaceTries = (bot._obtainState.ironReplaceTries || 0) + 1;
    try { console.log("[obtain] replace iron_pickaxe dura=" + _obtainDura(iron)); } catch (err) {}
    await _obtainDoLeaf(bot, { kind: "craft", item: "iron_pickaxe", n: _obtainCount(bot, "iron_pickaxe") + 1 }, timedOut);
    return true;
}

function _obtainWoodToolFuel(bot) {
    const keep = { wooden_pickaxe: true };
    return bot.inventory.items().find((i) => {
        const n = String(i.name || "");
        if (keep[n]) return false;
        return n === "wooden_axe" || n === "wooden_sword" || n === "wooden_shovel" || n === "wooden_hoe"
            || n === "wooden_pickaxe" && _obtainCount(bot, "wooden_pickaxe") > 1;
    });
}

async function _obtainUndergroundFuel(bot, need, timedOut) {
    const want = Math.max(1, need || 1);
    if (_obtainFuelUnits(bot) >= want) return { ok: true, reason: "have_fuel" };
    const haveL = _obtainCount(bot, "log");
    const haveP = _obtainCount(bot, "planks");
    if (haveL >= 1 && haveP < want) {
        const w = _obtainWoodKind(bot);
        await _obtainDoLeaf(bot, { kind: "craft", item: w + "_planks", n: haveP + 4 }, timedOut);
        if (_obtainFuelUnits(bot) >= want) return { ok: true, reason: "crafted_planks" };
    }
    try {
        const coalIds = [];
        if (mcData.blocksByName.coal_ore) coalIds.push(mcData.blocksByName.coal_ore.id);
        if (mcData.blocksByName.deepslate_coal_ore) coalIds.push(mcData.blocksByName.deepslate_coal_ore.id);
        const spots = coalIds.length ? (bot.findBlocks({ matching: coalIds, maxDistance: 32, count: 4 }) || []) : [];
        if (spots.length) {
            const pos = spots[0];
            await _obtainEquipDigTool(bot, "coal_ore");
            if (typeof _mineOreSafeDig === "function") await _mineOreSafeDig(bot, pos);
            else {
                const b = bot.blockAt(pos);
                if (b) await bot.dig(b);
            }
            if (_obtainFuelUnits(bot) >= 1) return { ok: true, reason: "mined_coal" };
        }
    } catch (err) {}
    const woodTool = _obtainWoodToolFuel(bot);
    if (woodTool) return { ok: true, reason: "wood_tool_fuel", item: woodTool.name };
    return { ok: false, reason: "missing_fuel" };
}

function _obtainAbortMotions(bot) {
    // an abandoned collect keeps walking its target list in the background; empty it (public Targets.clear,
    // exactly what the plugin does when a collect ends) so that loop exits at once
    try {
        const canClear = !!(bot.collectBlock && bot.collectBlock.targets && typeof bot.collectBlock.targets.clear === "function");
        if (!globalThis.__obtainClearLogged) {
            globalThis.__obtainClearLogged = true;
            try { console.log("[obtain] collectBlock.targets.clear available=" + canClear); } catch (e) {}
        }
        if (canClear) bot.collectBlock.targets.clear();
    } catch (e) {}
    try { bot.pathfinder.setGoal(null); } catch (e) {}
    try { bot.pathfinder.stop(); } catch (e) {}
    try { bot.stopDigging(); } catch (e) {}
    try {
        if (bot.collectBlock && typeof bot.collectBlock.cancelTask === "function") {
            const p = bot.collectBlock.cancelTask();
            if (p && typeof p.then === "function") {
                Promise.race([p, new Promise((r) => setTimeout(r, 1200))]).catch(() => {});
            }
        }
    } catch (e) {}
}

async function _obtainRace(bot, promiseOrThunk, capMs, label) {
    // Launch only while there is time: a promise built before this check runs unsupervised after the deadline
    // (09-26: 11+ overlapping collectBlock tasks). Pass a thunk () => primitive(...) so the check comes first.
    if (_obtainAborted()) {
        _obtainAbortMotions(bot);
        return false;
    }
    const ms = _obtainRemainMs(capMs);
    const isThunk = typeof promiseOrThunk === "function";
    if (isThunk && globalThis.__obtainInCall && ms < 2000) return false;
    let promise;
    try {
        promise = isThunk ? promiseOrThunk() : promiseOrThunk;
    } catch (err) {
        return false;
    }
    let timer = null;
    try {
        await Promise.race([
            promise,
            new Promise((_, rej) => {
                timer = setTimeout(() => rej(new Error((label || "op") + " timeout")), ms);
            }),
        ]);
        return true;
    } catch (err) {
        // cancel the abandoned action: gather/mineOre loops watch __obtainOpSeq and stop when it moves
        globalThis.__obtainOpSeq = (globalThis.__obtainOpSeq || 0) + 1;
        _obtainAbortMotions(bot);
        // let the abandoned action settle so two actions never share the pathfinder
        try {
            await Promise.race([Promise.resolve(promise).catch(() => {}), new Promise((r) => setTimeout(r, 2500))]);
        } catch (e) {}
        return false;
    } finally {
        if (timer) clearTimeout(timer);
    }
}

function _obtainEnableMovements(bot) {
    try {
        if (typeof Movements !== "function") return;
        const mv = new Movements(bot, mcData);
        mv.canDig = true;
        mv.allowSprinting = true;
        mv.allowParkour = false;
        bot.pathfinder.setMovements(mv);
    } catch (err) {}
}

function _obtainSolid(bot, pos) {
    try {
        const b = bot.blockAt(pos);
        return !!(b && b.boundingBox === "block");
    } catch (err) {
        return false;
    }
}

function _obtainIsEnclosed(bot) {
    if (!bot.entity || !bot.entity.position) return false;
    const p = bot.entity.position.floored();
    const sides = [p.offset(1, 0, 0), p.offset(-1, 0, 0), p.offset(0, 0, 1), p.offset(0, 0, -1)];
    const sidesHead = [p.offset(1, 1, 0), p.offset(-1, 1, 0), p.offset(0, 1, 1), p.offset(0, 1, -1)];
    const top = p.offset(0, 2, 0);
    const sideOk = sides.every((s) => _obtainSolid(bot, s)) && sidesHead.every((s) => _obtainSolid(bot, s));
    return sideOk && _obtainSolid(bot, top);
}

function _obtainShouldEscape(bot) {
    const st = bot._obtainState || {};
    const cap = (OBTAIN_KNOWLEDGE && OBTAIN_KNOWLEDGE.maxEscapePerCall) || 3;
    if ((st.escapeN || 0) >= cap) return false;
    if (!_obtainIsEnclosed(bot)) return false;
    const p = bot.entity && bot.entity.position;
    if (!p) return false;
    const prev = st.lastPos;
    const moved = !!(prev && p.distanceTo(prev) > 0.45);
    if (moved) return false;
    return (st.pathFails || 0) >= 2;
}

async function _obtainTryEscape(bot, timedOut) {
    if (!_obtainShouldEscape(bot)) return false;
    return _obtainEscape(bot, timedOut);
}

function _obtainBomNeed(bot, item, needTotal, depth) {
    const out = { log: 0, cobblestone: 0, coal: 0 };
    if (depth == null) depth = 0;
    const add = (k, n) => {
        if (!(n > 0)) return;
        out[k] = (out[k] || 0) + n;
    };
    const walk = (it, n, d) => {
        if (d > 24 || !(n > 0) || !it) return;
        const have = _obtainCount(bot, it);
        const gap = Math.max(0, n - have);
        if (gap <= 0) return;
        if (it === "log" || it === "wood" || it === "logs" || it.endsWith("_log") || it.endsWith("_stem")) {
            add("log", gap);
            return;
        }
        if (it === "planks" || it.endsWith("_planks") || it === "wood_planks") {
            const haveP = _obtainCount(bot, "planks");
            const g = Math.max(0, n - haveP);
            if (g <= 0) return;
            const logsHave = _obtainCount(bot, "log");
            const still = Math.max(0, g - logsHave * 4);
            if (still > 0) add("log", Math.ceil(still / 4));
            return;
        }
        if (it === "stick" || it === "sticks") {
            const haveS = _obtainCount(bot, "stick");
            const g = Math.max(0, n - haveS);
            if (g <= 0) return;
            walk("planks", Math.ceil(g / 4) * 2, d + 1);
            return;
        }
        if (it === "cobblestone" || it === "stone" || it === "cobbled_deepslate" || it === "blackstone") {
            add("cobblestone", gap);
            return;
        }
        if (it === "coal") {
            add("coal", gap);
            return;
        }
        const deep = _obtainDeepSpec(it);
        if (deep) {
            add("deep:" + deep.key, gap);
            return;
        }
        const smeltIn = _obtainSmeltInput(it);
        if (smeltIn && it !== smeltIn) {
            walk(smeltIn, gap, d + 1);
            walk("furnace", 1, d + 1);
            add("fuel", gap);
            walk("log", Math.max(1, Math.ceil(gap / 8)), d + 1);
            return;
        }
        const rec = _obtainPickRecipe(bot, it);
        if (rec) {
            const crafts = Math.ceil(gap / Math.max(1, rec.resultCount));
            for (const ing of rec.ings) walk(ing.name, ing.n * crafts, d + 1);
            if (rec.needsTable) walk("crafting_table", 1, d + 1);
            return;
        }
        if (mcData.blocksByName[it] || mcData.itemsByName[it]) add(it, gap);
    };
    walk(item, needTotal, depth);
    const hasDeep = Object.keys(out).some((k) => k.indexOf("deep:") === 0);
    if (hasDeep) {
        add("log", 2);
        add("cobblestone", 8);
    }
    return out;
}

async function obtain(bot, item, count = 1) {
    const report = async (ok, reason) => {
        const need = Math.max(1, Math.floor(Number(count) || 1));
        const haveNow = (typeof item === "string") ? _obtainCount(bot, item) : 0;
        if (ok && String(reason) === "done" && haveNow < need) {
            ok = false;
            reason = "not_found";
        }
        try { await _obtainWaitStopped(bot); } catch (err) {}
        try { _obtainLogPicks(bot); } catch (err) {}
        try {
            const ph = _obtainPhaseFlush(bot);
            try { bot.chat("[obtain] phase_seconds " + JSON.stringify(ph)); } catch (e) {}
        } catch (err) {}
        try {
            const tried = (bot._obtainState && bot._obtainState.triedStrategies) || [];
            const sline = "[obtain] strategies " + JSON.stringify(tried);
            try { console.log(sline); } catch (e) {}
            try { bot.chat(sline); } catch (e) {}
        } catch (err) {}
        const line = `[contract] obtain ${ok ? "ok" : "fail"} ${reason}`;
        try { console.log(line); } catch (err) {}
        // throttled chat resolves only when sent; never let a chat backlog hold obtain past its budget
        try { await Promise.race([Promise.resolve(bot.chat(line)), new Promise((r) => setTimeout(r, 3000))]); } catch (err) {}
        try {
            globalThis.__skillOutcomes = globalThis.__skillOutcomes || [];
            globalThis.__skillOutcomes.push({ name: "obtain", ok: !!ok, reason: String(reason), source: "contract" });
        } catch (err) {}
        return { ok: !!ok, reason: String(reason) };
    };
    if (typeof item !== "string") return report(false, "unknown_item");
    if (typeof count !== "number") count = 1;
    const needTotal = Math.max(1, Math.floor(Number(count) || 0));
    const countOf = (name) => _obtainCount(bot, name);
    try {
        console.log("[obtain] enter", item, count, "inv", bot.inventory.items().map((i) => i.name + ":" + i.count).join(","));
    } catch (err) {}
    if (countOf(item) >= needTotal) return report(true, "already_satisfied");

    bot._obtainGlobal = bot._obtainGlobal || { calls: 0, t0: Date.now(), item: item };
    if (bot._obtainGlobal.item !== item) {
        bot._obtainGlobal = { calls: 0, t0: Date.now(), item: item };
    }
    bot._obtainGlobal.calls += 1;
    if (bot._obtainGlobal.calls > OBTAIN_KNOWLEDGE.globalMaxCalls) return report(false, "global_cap");
    if (Date.now() - bot._obtainGlobal.t0 > OBTAIN_KNOWLEDGE.globalMaxCalls * OBTAIN_KNOWLEDGE.budgetSeconds * 1000) {
        return report(false, "global_cap");
    }

    const RETURN_BUFFER_MS = 10000;
    // pilot v2: inside a zero-shot step, everything obtain does ends >= 60 s before the 600 s step limit
    // (__stepStart is stamped by the harness; absent in the ceiling harness -> unchanged 540 s budget)
    const stepCap = (typeof globalThis.__stepStart === "number" && Date.now() - globalThis.__stepStart < 600000) ? globalThis.__stepStart + 540000 : Infinity;
    const deadline = Math.min(Date.now() + OBTAIN_KNOWLEDGE.budgetSeconds * 1000, stepCap);
    if (deadline - Date.now() < 30000) return report(false, "step_budget");
    globalThis.__obtainGen = (globalThis.__obtainGen || 0) + 1;
    const myGen = globalThis.__obtainGen;
    globalThis.__obtainInCall = true;
    globalThis.__obtainDeadline = deadline;
    globalThis.__obtainAborted = false;
    const timedOut = () => _obtainAborted(myGen) || Date.now() >= deadline || Date.now() + RETURN_BUFFER_MS >= deadline;
    bot._obtainState = bot._obtainState || { atY: {}, heading: 0, oreSpots: [], escapeN: 0, pathFails: 0 };
    bot._obtainState.rootItem = item;
    bot._obtainState.rootNeed = needTotal;
    bot._obtainState.escapeN = bot._obtainState.escapeN || 0;
    bot._obtainState.triedStrategies = bot._obtainState.triedStrategies || [];
    bot._obtainState.stratIdx = bot._obtainState.stratIdx || {};
    // per call, like leafBlack: an ore spot that failed once (e.g. from another height) must be retried next call
    bot._obtainState.oreBlack = {};
    // per call: a leaf blocked by a local condition (e.g. no room for a table) must be retried next call
    bot._obtainState.leafBlack = {};
    bot._obtainState.stallUpgrade = {};
    bot._obtainState.stonePickTries = 0;
    bot._obtainState.ironReplaceTries = 0;
    bot._obtainState.placeTries = 0;
    bot._obtainState.loops = 0;
    _obtainPhaseInit(bot);
    _obtainEnableMovements(bot);
    let didSurfaceStock = !!(bot._obtainState && bot._obtainState.stocked);
    let stallKey = "";
    let stallN = 0;
    let stallT0 = 0;
    let deaths = 0;
    let early = null;

    const pickLeaf = () => {
        const skip = bot._obtainState.leafBlack || {};
        const first = _obtainFirstLeaf(bot, item, needTotal, 0);
        if (first && !skip[_obtainLeafKey(first)]) return first;
        if (first && skip[_obtainLeafKey(first)] && (first.kind === "craft" || first.kind === "smelt")) {
            const miss = first.kind === "craft" ? _obtainMissingIngs(bot, first.item || first.name, first.n || 1) : [];
            const fuelOk = first.kind !== "smelt" || _obtainFuelUnits(bot) >= 1;
            if ((!miss || !miss.length) && fuelOk) return null;
        }
        try {
            const bom = _obtainBomNeed(bot, item, needTotal);
            for (const k of Object.keys(bom || {})) {
                if (!(bom[k] > 0)) continue;
                let alt = null;
                if (k.indexOf("deep:") === 0) {
                    const spec = _obtainDeepSpec(k.slice(5));
                    if (spec) alt = { kind: "mineDeep", name: spec.key, n: bom[k], spec: spec };
                } else if (k === "log") alt = { kind: "gather", name: "log", n: bom[k] };
                else if (k === "cobblestone") alt = { kind: "gather", name: "cobblestone", n: bom[k] };
                else if (k === "coal") alt = { kind: "gather", name: "coal", n: bom[k] };
                if (alt && !skip[_obtainLeafKey(alt)]) return alt;
            }
        } catch (err) {}
        return first && skip[_obtainLeafKey(first)] ? null : first;
    };

    const noteStall = async (leaf, r) => {
        const key = _obtainLeafKey(leaf);
        const stallCap = leaf.kind === "mineDeep" ? 12 : 3;
        if (stallKey !== key) {
            stallKey = key;
            stallN = 1;
            stallT0 = Date.now();
        } else {
            stallN += 1;
        }
        if (stallN < stallCap && Date.now() - stallT0 < (leaf.kind === "mineDeep" ? 180000 : 60000)) return false;
        if (!bot._obtainState.stallUpgrade[key]) {
            bot._obtainState.stallUpgrade[key] = true;
            stallN = 0;
            stallT0 = Date.now();
            if (!_obtainIsMoveLeaf(leaf)) {
                await _obtainClearWorkspace(bot, leaf, timedOut);
                await _obtainRecoverCraftOrSmelt(bot, leaf, r || { reason: "no_progress" }, timedOut);
            } else {
                _obtainRotateStrategy(bot, key);
            }
            return false;
        }
        bot._obtainState.leafBlack[key] = true;
        stallN = 0;
        stallKey = "";
        const alt = pickLeaf();
        if (!alt || _obtainLeafKey(alt) === key) {
            early = { ok: false, reason: "blocked:" + key };
            return true;
        }
        return false;
    };

    let budgetTimer = null;
    try {
        const work = (async () => {
        let leaf = null;
        while (countOf(item) < needTotal && !timedOut() && !early) {
            try {
            bot._obtainState.loops = (bot._obtainState.loops || 0) + 1;
            try { await bot.waitForTicks(2); } catch (err) {}
            if (timedOut()) break;
            if (!bot.entity || (bot.entity.health != null && bot.entity.health <= 0)) {
                deaths += 1;
                try { await bot.waitForTicks(40); } catch (err) {}
                continue;
            }
            try { bot._obtainState.lastPos = bot.entity.position.clone(); } catch (err) {}
            try { await _obtainMaybeReplaceIronPick(bot, timedOut); } catch (err) {}
            try { await _obtainMaybeKeepStonePick(bot, timedOut); } catch (err) {}
            const full = await _obtainMaybeToss(bot);
            if (full) { early = { ok: false, reason: "inv_full" }; return; }
            leaf = pickLeaf();
            if (!leaf) {
                if (countOf(item) >= needTotal) break;
                const skipped = Object.keys(bot._obtainState.leafBlack || {});
                if (skipped.length) {
                    early = { ok: false, reason: "blocked:" + skipped[0] };
                    return;
                }
                try { console.log("[obtain] leaf_null recover log"); } catch (err) {}
                _obtainRotateStrategy(bot, "gather:log");
                await _obtainDoLeaf(bot, { kind: "gather", name: "log", n: 2 }, timedOut);
                continue;
            }
            const leafName = leaf.name || leaf.item || leaf.kind || "leaf";
            const leafLine = `[obtain] leaf ${leaf.kind} ${leaf.name || leaf.item || ""} n=${leaf.n || 0}`;
            try { console.log(leafLine); } catch (err) {}
            _obtainChat(bot, leafLine);
            if (leaf.kind === "mineDeep" && !didSurfaceStock) {
                const yHere = bot.entity && bot.entity.position ? bot.entity.position.y : 64;
                const tooled = !!_obtainBestPick(bot, "wooden");
                if (tooled && yHere < 50 && _obtainWoodEquiv(bot) >= 2) {
                    didSurfaceStock = true;
                    bot._obtainState.stocked = true;
                } else {
                    didSurfaceStock = true;
                    bot._obtainState.stocked = true;
                    _obtainPhase(bot, "surface");
                    const sr = await _obtainStockSurface(bot, item, needTotal, timedOut);
                    if (sr && String(sr.reason || "").indexOf("no_progress") === 0) {
                        _obtainRotateStrategy(bot, "gather:" + (sr.leaf || "log"));
                    }
                    continue;
                }
            }
            if (leaf.kind === "gather" && _obtainIsSurfaceGather(leaf.name)) {
                _obtainPhase(bot, "surface");
                const up = await _obtainStandOnGround(bot, timedOut);
                if (!up && bot.entity && bot.entity.position.y < 55) {
                    const escaped = await _obtainTryEscape(bot, timedOut);
                    if (!escaped && _obtainShouldEscape(bot)) {
                        _obtainRotateStrategy(bot, "gather:" + leafName);
                    }
                }
            }
            if (leaf.kind === "mineDeep") {
                await _obtainReturnToMine(bot, timedOut);
            }
            const posBefore = bot.entity && bot.entity.position ? bot.entity.position.clone() : null;
            const sigBefore = _obtainInvSig(bot);
            const before = _obtainCount(bot, leafName);
            const r = await _obtainDoLeaf(bot, leaf, timedOut);
            if (r && r.reason === "inv_full") { early = { ok: false, reason: "inv_full" }; return; }
            if (r && r.reason === "unknown_item") { early = { ok: false, reason: "unknown_item" }; return; }
            const after = _obtainCount(bot, leafName);
            const posAfter = bot.entity && bot.entity.position;
            const moved = !!(posBefore && posAfter && posAfter.distanceTo(posBefore) > 0.6);
            const invChanged = _obtainInvSig(bot) !== sigBefore || after > before;
            const progressed = !!(r && r.ok) || invChanged || (moved && _obtainIsMoveLeaf(leaf));
            if (r && (r.reason === "missing_fuel" || r.reason === "missing_materials")) {
                await _obtainRecoverCraftOrSmelt(bot, leaf, r, timedOut);
            } else if (r && String(r.reason || "").indexOf("no_progress") === 0) {
                if (leaf.kind === "gather" && leaf.name === "log" && _obtainWoodEquiv(bot) >= 4) {
                    /* wood already in bag */
                } else if (leaf.kind === "gather" && leaf.name === "cobblestone" && _obtainCount(bot, "cobblestone") >= 8) {
                    /* cobble already in bag */
                } else if (!_obtainIsMoveLeaf(leaf)) {
                    await _obtainRecoverCraftOrSmelt(bot, leaf, r, timedOut);
                } else {
                    const escaped = await _obtainTryEscape(bot, timedOut);
                    if (escaped && !timedOut()) {
                        const r2 = await _obtainDoLeaf(bot, leaf, timedOut);
                        if (r2 && r2.ok) {
                            stallKey = "";
                            stallN = 0;
                            continue;
                        }
                        if (r2 && r2.reason === "inv_full") { early = { ok: false, reason: "inv_full" }; return; }
                        if (r2 && r2.reason === "unknown_item") { early = { ok: false, reason: "unknown_item" }; return; }
                    }
                    _obtainRotateStrategy(bot, String(leaf.kind) + ":" + String(leafName));
                }
            } else if (r && r.reason === "missing_tool") {
                _obtainPhase(bot, "craft");
            }
            if (!progressed) {
                bot._obtainState.pathFails = (bot._obtainState.pathFails || 0) + 1;
                if (await noteStall(leaf, r)) return;
            } else {
                stallKey = "";
                stallN = 0;
                bot._obtainState.pathFails = 0;
            }
            } catch (err) {
                try { console.log("[obtain] err " + String((err && err.message) || err).slice(0, 120)); } catch (e) {}
                try { bot.pathfinder.setGoal(null); } catch (e) {}
                try { await bot.waitForTicks(20); } catch (e) {}
                try {
                    if (leaf && await noteStall(leaf, { reason: "no_progress" })) return;
                } catch (e2) {
                    try { console.log("[obtain] noteStall err " + String((e2 && e2.message) || e2).slice(0, 120)); } catch (e3) {}
                }
            }
        }
        })();
        await Promise.race([
            work,
            new Promise((resolve) => {
                budgetTimer = setTimeout(() => resolve("budget"),
                    Math.max(1000, deadline - Date.now() - RETURN_BUFFER_MS));
            }),
        ]);
        globalThis.__obtainAborted = true;
        _obtainAbortMotions(bot);
        // drain the abandoned main loop so its last action cannot overlap the next call
        try { await Promise.race([work.catch(() => {}), new Promise((r) => setTimeout(r, 2500))]); } catch (err) {}
    } finally {
        if (budgetTimer) clearTimeout(budgetTimer);
        try { globalThis.__obtainAborted = true; } catch (err) {}
        try { globalThis.__obtainInCall = false; } catch (err) {}
        try { delete globalThis.__obtainDeadline; } catch (err) {}
        try {
            if (globalThis.__obtainGen === myGen) globalThis.__obtainGen = myGen + 1;
        } catch (err) {}
        _obtainAbortMotions(bot);
        try { await _obtainWaitStopped(bot); } catch (err) {}
    }
    if (early) return report(!!early.ok, early.reason);
    if (countOf(item) >= needTotal) return report(true, "done");
    if (Date.now() + RETURN_BUFFER_MS >= deadline) return report(false, "timeout");
    return report(false, "not_found");
}

function _obtainIsSurfaceGather(name) {
    const n = String(name || "");
    return n === "log" || n === "wood" || n === "logs" || n.endsWith("_log") || n.endsWith("_stem")
        || n === "sand" || n === "dirt" || n === "gravel" || n === "clay";
}

function _obtainWoodEquiv(bot) {
    return _obtainCount(bot, "log") + Math.floor(_obtainCount(bot, "planks") / 4);
}

function _obtainIsWoodCraft(name) {
    const n = String(name || "");
    return n.endsWith("_planks") || n === "planks" || n === "stick" || n === "sticks"
        || n === "crafting_table" || n === "wooden_pickaxe" || n === "wooden_axe"
        || n === "wooden_sword" || n === "wooden_shovel" || n === "wooden_hoe";
}

function _obtainIsAirBlock(block) {
    if (!block) return true;
    const n = String(block.name || "");
    if (n === "air" || n === "cave_air" || n === "void_air") return true;
    return block.boundingBox === "empty";
}

async function _obtainStockSurface(bot, item, needTotal, timedOut) {
    const dead = timedOut || (() => false);
    _obtainPhase(bot, "surface");
    try {
        const up = await _obtainStandOnGround(bot, dead);
        if (!up && bot.entity && bot.entity.position.y < 55) {
            await _obtainTryEscape(bot, dead);
        }
        const bom = _obtainBomNeed(bot, item, needTotal);
        const wantLogs = Math.max(8, bom.log || 0);
        const wantFuel = Math.max(0, bom.fuel || 0);
        if (wantFuel > 0 && _obtainFuelUnits(bot) < wantFuel) {
            try {
                const coalIds = [];
                if (mcData.blocksByName.coal_ore) coalIds.push(mcData.blocksByName.coal_ore.id);
                if (mcData.blocksByName.deepslate_coal_ore) coalIds.push(mcData.blocksByName.deepslate_coal_ore.id);
                const spots = coalIds.length ? (bot.findBlocks({ matching: coalIds, maxDistance: 32, count: 4 }) || []) : [];
                if (spots.length) {
                    await _obtainEquipDigTool(bot, "coal_ore");
                    if (typeof _mineOreSafeDig === "function") await _mineOreSafeDig(bot, spots[0]);
                }
            } catch (err) {}
            if (_obtainFuelUnits(bot) < wantFuel && _obtainWoodEquiv(bot) < wantLogs) {
                const gap = Math.max(1, Math.ceil((wantFuel - _obtainFuelUnits(bot)) / 4));
                await _obtainDoLeaf(bot, { kind: "gather", name: "log", n: gap }, dead);
            }
        }
        let r = null;
        for (let k = 0; k < 3 && !dead() && _obtainWoodEquiv(bot) < wantLogs; k++) {
            const gap = Math.max(1, wantLogs - _obtainWoodEquiv(bot));
            r = await _obtainDoLeaf(bot, { kind: "gather", name: "log", n: gap }, dead);
            if (!(r && r.ok)) break;
        }
        if (_obtainWoodEquiv(bot) < wantLogs) {
            if (r && r.ok) {
                /* have enough */
            } else if (r && String(r.reason || "").indexOf("no_progress") === 0) {
                if (_obtainWoodEquiv(bot) < 4) {
                    const escaped = await _obtainTryEscape(bot, dead);
                    if (!escaped) return { ok: false, reason: "no_progress", leaf: "log" };
                }
            }
        }
        for (let i = 0; i < 24 && !dead(); i++) {
            const leaf = _obtainFirstLeaf(bot, item, needTotal, 0);
            if (!leaf) break;
            if (leaf.kind === "mineDeep") break;
            const woodCraft = leaf.kind === "craft" && _obtainIsWoodCraft(leaf.item || leaf.name);
            const surfaceG = leaf.kind === "gather" && _obtainIsSurfaceGather(leaf.name);
            const cobbleG = leaf.kind === "gather" && (leaf.name === "cobblestone" || leaf.name === "stone");
            const furnaceC = leaf.kind === "craft" && (leaf.item === "furnace" || leaf.name === "furnace");
            const stonePick = leaf.kind === "craft" && String(leaf.item || "").indexOf("pickaxe") >= 0;
            if (!woodCraft && !surfaceG && !cobbleG && !furnaceC && !stonePick) break;
            if (cobbleG) {
                const n = Math.max(leaf.n || 1, bom.cobblestone || 8);
                const r = await _obtainDoLeaf(bot, { kind: "gather", name: "cobblestone", n: n }, dead);
                if (r && String(r.reason || "").indexOf("no_progress") === 0) {
                    const escaped = await _obtainTryEscape(bot, dead);
                    if (!escaped) {
                        _obtainRotateStrategy(bot, "gather:cobblestone");
                        continue;
                    }
                }
                continue;
            }
            const r = await _obtainDoLeaf(bot, leaf, dead);
            if (r && (r.reason === "missing_fuel" || r.reason === "missing_materials")) {
                await _obtainRecoverCraftOrSmelt(bot, leaf, r, dead);
                continue;
            }
            if (r && String(r.reason || "").indexOf("no_progress") === 0) {
                if (!_obtainIsMoveLeaf(leaf)) {
                    await _obtainRecoverCraftOrSmelt(bot, leaf, r, dead);
                    continue;
                }
                const escaped = await _obtainTryEscape(bot, dead);
                if (!escaped) return { ok: false, reason: "no_progress", leaf: leaf.name || leaf.item || "log" };
                continue;
            }
            if (r && r.reason === "inv_full") return r;
            if (r && r.reason === "missing_tool") continue;
        }
        if (_obtainWoodEquiv(bot) >= Math.min(8, wantLogs)) return { ok: true, reason: "done" };
        return { ok: true, reason: "partial" };
    } catch (err) {}
    return { ok: false, reason: "not_found" };
}

async function _obtainStandOnGround(bot, timedOut) {
    const dead = timedOut || (() => false);
    try {
        const p = bot.entity.position;
        if (p.y >= 60) return true;
        bot._obtainState = bot._obtainState || { atY: {}, heading: 0 };
        bot._obtainState.minePos = {
            x: Math.floor(p.x) + 0.5,
            y: Math.floor(p.y),
            z: Math.floor(p.z) + 0.5,
        };
        const cap = Date.now() + 60000;
        const t = () => dead() || Date.now() >= cap;
        try {
            bot.pathfinder.setGoal(new GoalBlock(Math.floor(p.x), 70, Math.floor(p.z)));
            const lim = Date.now() + Math.min(15000, _obtainRemainMs(15000));
            while (Date.now() < lim && !t()) {
                if (bot.entity.position.y >= 60) {
                    try { bot.pathfinder.setGoal(null); } catch (e) {}
                    return true;
                }
                await bot.waitForTicks(8);
            }
            try { bot.pathfinder.setGoal(null); } catch (e) {}
        } catch (err) {}
        await _obtainGoY(bot, 70, t);
        if (bot.entity.position.y < 60) {
            await _obtainTryEscape(bot, t);
        }
        return bot.entity.position.y >= 60;
    } catch (err) {
        return false;
    }
}

async function _obtainEscape(bot, timedOut) {
    const dead = timedOut || (() => false);
    bot._obtainState = bot._obtainState || { escapeN: 0 };
    const capN = (OBTAIN_KNOWLEDGE && OBTAIN_KNOWLEDGE.maxEscapePerCall) || 3;
    if ((bot._obtainState.escapeN || 0) >= capN) return false;
    if (!_obtainIsEnclosed(bot)) return false;
    bot._obtainState.escapeN = (bot._obtainState.escapeN || 0) + 1;
    _obtainPhase(bot, "escape");
    try { console.log("[obtain] escape"); } catch (err) {}
    if (!bot.entity || !bot.entity.position) return false;
    const start = bot.entity.position.clone();
    const cap = Date.now() + 20000;
    const t = () => dead() || Date.now() >= cap;
    const dig = async (pos) => {
        try {
            if (typeof _mineOreSafeDig === "function") {
                await _mineOreSafeDig(bot, pos, { seal: false });
                return;
            }
            const b = bot.blockAt(pos);
            if (b && !_obtainIsAirBlock(b)) await bot.dig(b);
        } catch (err) {}
    };
    try {
        const p = bot.entity.position.floored();
        await dig(p.offset(1, 0, 0));
        await dig(p.offset(-1, 0, 0));
        await dig(p.offset(0, 0, 1));
        await dig(p.offset(0, 0, -1));
        await dig(p.offset(0, 1, 0));
        await dig(p.offset(0, 2, 0));
    } catch (err) {}
    try {
        if (typeof _mineOreStairUp === "function") {
            const targetY = Math.floor(start.y) + 4;
            if (Math.floor(bot.entity.position.y) < targetY) {
                await _mineOreStairUp(bot, targetY, t, null);
            }
        } else {
            for (let i = 0; i < 6 && !t(); i++) {
                const p = bot.entity.position.floored();
                await dig(p.offset(0, 2, 0));
                if (typeof _mineOrePlaceAt === "function") {
                    try {
                        bot.setControlState("jump", true);
                        await bot.waitForTicks(3);
                        await _mineOrePlaceAt(bot, p);
                    } finally {
                        try { bot.setControlState("jump", false); } catch (e) {}
                    }
                }
                await bot.waitForTicks(4);
                if (bot.entity.position.y >= start.y + 4) break;
            }
        }
    } catch (err) {}
    try {
        const here = bot.entity.position;
        return here.distanceTo(start) > 0.8 || Math.abs(here.y - start.y) > 0.4;
    } catch (err) {
        return false;
    }
}

async function _obtainReturnToMine(bot, timedOut) {
    const dead = timedOut || (() => false);
    try {
        const m = bot._obtainState && bot._obtainState.minePos;
        if (!m) return;
        const y = bot.entity.position.y;
        if (y <= m.y + 4) return;
        const cap = Date.now() + 90000;
        const t = () => dead() || Date.now() >= cap;
        await _obtainGoY(bot, Math.floor(m.y), t);
        try {
            bot.pathfinder.setGoal(new GoalBlock(Math.floor(m.x), Math.floor(m.y), Math.floor(m.z)));
            const lim = Date.now() + 20000;
            while (Date.now() < lim && !t()) {
                const here = bot.entity.position;
                const dx = here.x - m.x;
                const dy = here.y - m.y;
                const dz = here.z - m.z;
                if (Math.sqrt(dx * dx + dy * dy + dz * dz) < 3) break;
                await bot.waitForTicks(8);
            }
        } catch (err) {}
        try { bot.pathfinder.setGoal(null); } catch (err) {}
        try { console.log("[obtain] returnToMine y=" + Math.floor(bot.entity.position.y)); } catch (err) {}
    } catch (err) {}
}

function _obtainCount(bot, name) {
    const n = String(name || "").replace(/^minecraft:/, "");
    const sum = (pred) => bot.inventory.items().filter(pred).reduce((s, i) => s + i.count, 0);
    if (n === "log" || n === "wood" || n === "logs") {
        return sum((i) => i.name.endsWith("_log") || i.name.endsWith("_stem"));
    }
    if (n === "planks" || n === "wood_planks") {
        return sum((i) => i.name.endsWith("_planks"));
    }
    const def = mcData.itemsByName[n];
    if (!def) {
        if (n.endsWith("_log")) return sum((i) => i.name.endsWith("_log") || i.name.endsWith("_stem"));
        if (n.endsWith("_planks")) return sum((i) => i.name.endsWith("_planks"));
        return 0;
    }
    return bot.inventory.items().filter((i) => i.type === def.id).reduce((s, i) => s + i.count, 0);
}

function _obtainDura(item) {
    if (!item) return 0;
    if (typeof _mineOreRemaining === "function") return _mineOreRemaining(item);
    const max = item.maxDurability || 0;
    const used = item.durabilityUsed || 0;
    if (max) return Math.max(0, max - used);
    return 32;
}

function _obtainBestPick(bot, minTier) {
    return _obtainLowestPick(bot, minTier);
}

function _obtainDeepSpec(name) {
    const n = String(name || "").toLowerCase().replace(/^minecraft:/, "");
    const table = OBTAIN_KNOWLEDGE.deep;
    if (table[n]) return Object.assign({ key: n }, table[n]);
    const alias = {
        diamond_ore: "diamond", deepslate_diamond_ore: "diamond",
        gold_ore: "raw_gold", deepslate_gold_ore: "raw_gold", gold: "raw_gold",
        redstone_ore: "redstone", deepslate_redstone_ore: "redstone",
        lapis: "lapis_lazuli", lapis_ore: "lapis_lazuli", deepslate_lapis_ore: "lapis_lazuli",
        iron_ore: "raw_iron", deepslate_iron_ore: "raw_iron", iron: "raw_iron",
    };
    const key = alias[n];
    if (key && table[key]) return Object.assign({ key: key }, table[key]);
    return null;
}

function _obtainSmeltInput(output) {
    const map = OBTAIN_KNOWLEDGE.smelt;
    for (const k of Object.keys(map)) {
        if (map[k] === output) return k;
    }
    return null;
}

function _obtainFuelUnits(bot) {
    let u = 0;
    let woodPicks = 0;
    for (const it of bot.inventory.items()) {
        if (it.name === "coal" || it.name === "charcoal") u += it.count * 8;
        else if (it.name.endsWith("_planks")) u += it.count;
        else if (it.name.endsWith("_log") || it.name.endsWith("_stem")) u += it.count;
        else if (it.name === "lava_bucket") u += it.count * 100;
        else if (it.name === "wooden_axe" || it.name === "wooden_sword" || it.name === "wooden_shovel" || it.name === "wooden_hoe") u += it.count;
        else if (it.name === "wooden_pickaxe") woodPicks += it.count;
    }
    u += Math.max(0, woodPicks - 1);
    return u;
}

function _obtainRecipes(itemName) {
    const def = mcData.itemsByName[itemName];
    if (!def || !mcData.recipes) return [];
    const list = mcData.recipes[def.id] || [];
    const out = [];
    for (const r of list) {
        const counts = new Map();
        const add = (id, n) => {
            if (id == null) return;
            const it = mcData.items[id];
            if (!it) return;
            counts.set(it.name, (counts.get(it.name) || 0) + n);
        };
        if (r.inShape) {
            for (const row of r.inShape) {
                for (const cell of row) {
                    const id = cell && typeof cell === "object" ? cell.id : cell;
                    add(id, 1);
                }
            }
        } else if (r.ingredients) {
            for (const cell of r.ingredients) {
                const id = cell && typeof cell === "object" ? cell.id : cell;
                add(id, 1);
            }
        } else {
            continue;
        }
        const ings = [];
        for (const [nm, n] of counts) {
            const it = mcData.itemsByName[nm];
            ings.push({ name: nm, n: n, id: it ? it.id : 99999, have: 0 });
        }
        const resultCount = (r.result && r.result.count) || 1;
        const needsTable = !!(r.inShape && r.inShape.length > 2);
        out.push({ ings: ings, unique: ings.length, resultCount: resultCount, needsTable: needsTable });
    }
    return out;
}

function _obtainHaveForIng(bot, name) {
    const n = String(name || "");
    if (n.endsWith("_planks") && n !== "planks") {
        const w = n.slice(0, -7);
        return _obtainCount(bot, n) + _obtainCount(bot, w + "_log") * 4;
    }
    if (n === "planks" || n === "wood_planks") {
        return _obtainCount(bot, "planks") + _obtainCount(bot, "log") * 4;
    }
    return _obtainCount(bot, n);
}

function _obtainPickRecipe(bot, itemName) {
    const recs = _obtainRecipes(itemName);
    for (const r of recs) {
        for (const ing of r.ings) {
            ing.have = _obtainCount(bot, ing.name);
            ing.equiv = _obtainHaveForIng(bot, ing.name);
        }
        r.satisfied = (r.ings || []).every((i) => (i.equiv != null ? i.equiv : i.have) >= i.n);
    }
    recs.sort((a, b) => {
        if (!!a.satisfied !== !!b.satisfied) return a.satisfied ? -1 : 1;
        const score = (r) => (r.ings || []).reduce((s, i) => {
            const eq = (i.equiv != null ? i.equiv : i.have) || 0;
            return s + Math.min(1, eq / Math.max(1, i.n));
        }, 0);
        const sa = score(a);
        const sb = score(b);
        if (Math.abs(sa - sb) > 0.01) return sb - sa;
        return _obtainRecipeBetter(a, b) ? -1 : 1;
    });
    return recs[0] || null;
}

function _obtainWoodKind(bot) {
    const order = OBTAIN_KNOWLEDGE.woodOrder;
    for (const w of order) {
        if (_obtainCount(bot, w + "_log") > 0 || _obtainCount(bot, w + "_planks") > 0) return w;
    }
    const any = bot.inventory.items().find((i) => i.name.endsWith("_log") || i.name.endsWith("_planks"));
    if (any) return any.name.split("_")[0];
    return order[0];
}

function _obtainFirstLeaf(bot, item, needTotal, depth) {
    if (depth > 24) return { kind: "unknown", name: item, n: 1 };
    const have = _obtainCount(bot, item);
    if (have >= needTotal) return null;
    const gap = needTotal - have;

    if (item === "log" || item === "wood" || item.endsWith("_log") || item.endsWith("_stem")) {
        const haveAny = _obtainCount(bot, "log");
        const specificLog = item !== "log" && item !== "wood" && item !== "logs"
            && (item.endsWith("_log") || item.endsWith("_stem"));
        if (specificLog && haveAny >= 4 && _obtainCount(bot, item) < needTotal) {
            const w = _obtainWoodKind(bot);
            return { kind: "craft", item: w + "_planks", n: _obtainCount(bot, w + "_planks") + 4 };
        }
        let n = gap;
        try {
            const st = bot._obtainState || {};
            if (st.rootItem) {
                const bom = _obtainBomNeed(bot, st.rootItem, st.rootNeed || 1);
                if ((bom.log || 0) > n) n = bom.log;
            }
        } catch (err) {}
        if (haveAny >= n) {
            const w = _obtainWoodKind(bot);
            return { kind: "craft", item: w + "_planks", n: _obtainCount(bot, w + "_planks") + 4 };
        }
        return { kind: "gather", name: "log", n: Math.max(1, n) };
    }
    if (item === "planks" || item.endsWith("_planks")) {
        const specific = item !== "planks" && item !== "wood_planks" && item.endsWith("_planks");
        const haveP = specific ? _obtainCount(bot, item) : _obtainCount(bot, "planks");
        if (haveP >= needTotal) return null;
        const g = needTotal - haveP;
        const logName = specific ? (item.replace(/_planks$/, "") + "_log") : "log";
        const logs = _obtainCount(bot, logName);
        if (logs * 4 + haveP >= needTotal) {
            const w = specific ? item.replace(/_planks$/, "") : _obtainWoodKind(bot);
            return { kind: "craft", item: w + "_planks", n: _obtainCount(bot, w + "_planks") + g };
        }
        const anyLogs = _obtainCount(bot, "log");
        if (anyLogs > 0) {
            const w = _obtainWoodKind(bot);
            return { kind: "craft", item: w + "_planks", n: _obtainCount(bot, w + "_planks") + g };
        }
        let n = Math.max(1, Math.ceil((g - logs * 4) / 4));
        try {
            const st = bot._obtainState || {};
            if (st.rootItem) {
                const bom = _obtainBomNeed(bot, st.rootItem, st.rootNeed || 1);
                if ((bom.log || 0) > n) n = bom.log;
            }
        } catch (err) {}
        return { kind: "gather", name: "log", n: n };
    }
    if (item === "stick" || item === "sticks") {
        const haveS = _obtainCount(bot, "stick");
        if (haveS >= needTotal) return null;
        const g = needTotal - haveS;
        const needPlanks = Math.ceil(g / 2) * 2;
        if (_obtainCount(bot, "planks") < needPlanks) {
            return _obtainFirstLeaf(bot, "planks", needPlanks, depth + 1);
        }
        return { kind: "craft", item: "stick", n: needTotal };
    }
    if (item === "cobblestone" || item === "stone" || item === "cobbled_deepslate" || item === "blackstone") {
        const pick = _obtainBestPick(bot, "wooden");
        if (!pick) return _obtainFirstLeaf(bot, "wooden_pickaxe", 1, depth + 1);
        let n = gap;
        try {
            const st = bot._obtainState || {};
            if (st.rootItem) {
                const bom = _obtainBomNeed(bot, st.rootItem, st.rootNeed || 1);
                if ((bom.cobblestone || 0) > n) n = bom.cobblestone;
            }
        } catch (err) {}
        return { kind: "gather", name: "cobblestone", n: Math.max(1, n) };
    }
    if (item === "coal") {
        const pick = _obtainBestPick(bot, "wooden");
        if (!pick) return _obtainFirstLeaf(bot, "wooden_pickaxe", 1, depth + 1);
        return { kind: "gather", name: "coal", n: gap };
    }
    if (item === "sand" || item === "dirt" || item === "gravel" || item === "clay") {
        return { kind: "gather", name: item, n: gap };
    }

    const deep = _obtainDeepSpec(item);
    if (deep) {
        const pick = _obtainBestPick(bot, deep.minTool);
        if (!pick) {
            return _obtainFirstLeaf(bot, deep.minTool + "_pickaxe", Math.max(1, _obtainCount(bot, deep.minTool + "_pickaxe") + 1), depth + 1);
        }
        return { kind: "mineDeep", name: deep.key, n: gap, spec: deep };
    }

    const smeltIn = _obtainSmeltInput(item);
    if (smeltIn && item !== smeltIn) {
        const haveIn = _obtainCount(bot, smeltIn);
        if (haveIn < gap) return _obtainFirstLeaf(bot, smeltIn, haveIn + gap, depth + 1);
        if (_obtainCount(bot, "furnace") < 1) {
            const near = bot.findBlock({ matching: mcData.blocksByName.furnace.id, maxDistance: 32 });
            if (!near) return _obtainFirstLeaf(bot, "furnace", 1, depth + 1);
        }
        if (_obtainFuelUnits(bot) < gap) {
            if (_obtainCount(bot, "coal") < 1 && _obtainCount(bot, "planks") < gap) {
                const y = bot.entity && bot.entity.position ? bot.entity.position.y : 64;
                if (y < 50 && _obtainCount(bot, "log") >= 1) {
                    const w = _obtainWoodKind(bot);
                    return { kind: "craft", item: w + "_planks", n: _obtainCount(bot, w + "_planks") + 4 };
                }
                if (y < 50) {
                    return { kind: "gather", name: "coal", n: 1 };
                }
                return _obtainFirstLeaf(bot, "log", Math.max(1, Math.ceil(gap / 4) + _obtainCount(bot, "log")), depth + 1);
            }
        }
        return { kind: "smelt", item: smeltIn, n: gap };
    }

    const rec = _obtainPickRecipe(bot, item);
    if (rec) {
        const crafts = Math.ceil(gap / Math.max(1, rec.resultCount));
        for (const ing of rec.ings) {
            const needIng = ing.n * crafts;
            if (_obtainCount(bot, ing.name) < needIng) {
                return _obtainFirstLeaf(bot, ing.name, needIng, depth + 1);
            }
        }
        if (rec.needsTable) {
            const table = bot.findBlock({ matching: mcData.blocksByName.crafting_table.id, maxDistance: 32 });
            if (!table && _obtainCount(bot, "crafting_table") < 1) {
                return _obtainFirstLeaf(bot, "crafting_table", 1, depth + 1);
            }
        }
        return { kind: "craft", item: item, n: needTotal };
    }

    if (mcData.blocksByName[item] || mcData.itemsByName[item]) {
        return { kind: "gather", name: item, n: gap };
    }
    return { kind: "unknown", name: item, n: gap };
}

async function _obtainDoLeaf(bot, leaf, timedOut) {
    if (!leaf) return { ok: false, reason: "unknown_item" };
    const leafKey = String(leaf.kind) + ":" + String(leaf.name || leaf.item || "");
    const strat = _obtainCurrentStrategy(bot, leafKey);
    try {
        const line = "[obtain] strategy " + strat + " " + leafKey;
        console.log(line);
        _obtainChat(bot, line);
    } catch (err) {}
    if (leaf.kind === "gather") {
        _obtainPhase(bot, "surface");
        return _obtainGatherWithStrategy(bot, leaf, strat, timedOut);
    }
    if (leaf.kind === "craft") {
        _obtainPhase(bot, "craft");
        return _obtainCraftVanilla(bot, leaf.item, leaf.n, timedOut);
    }
    if (leaf.kind === "smelt") {
        _obtainPhase(bot, "smelt");
        return _obtainSmeltVanilla(bot, leaf.item, leaf.n, timedOut);
    }
    if (leaf.kind === "mineDeep") {
        _obtainPhase(bot, "search_ore");
        return _obtainMineDeep(bot, leaf.spec || _obtainDeepSpec(leaf.name), leaf.n, timedOut, strat);
    }
    return { ok: false, reason: "unknown_item" };
}

async function _obtainMaybeToss(bot) {
    const used = () => (typeof bot.inventoryUsed === "function" ? bot.inventoryUsed() : bot.inventory.items().length);
    if (used() < 32) return false;
    const keep = OBTAIN_KNOWLEDGE.keepPlaceBlocks;
    let reserve = {};
    try {
        const st = bot._obtainState || {};
        if (st.rootItem) reserve = _obtainBomNeed(bot, st.rootItem, st.rootNeed || 1) || {};
    } catch (err) {}
    const reserveCobble = Math.max(keep, reserve.cobblestone || 0);
    let cobble = 0;
    for (const it of bot.inventory.items()) {
        if (it.name === "cobblestone" || it.name === "cobbled_deepslate") cobble += it.count;
    }
    for (const it of bot.inventory.items().slice()) {
        if (OBTAIN_KNOWLEDGE.discard.indexOf(it.name) < 0) continue;
        const reserved = reserve[it.name] || 0;
        let n = it.count;
        if (it.name === "cobblestone" || it.name === "cobbled_deepslate") {
            const extra = Math.max(0, cobble - reserveCobble);
            n = Math.min(it.count, extra);
            cobble -= n;
        } else if (reserved > 0) {
            const have = _obtainCount(bot, it.name);
            n = Math.min(it.count, Math.max(0, have - reserved));
        }
        if (n <= 0) continue;
        try { await bot.toss(it.type, null, n); } catch (err) {}
    }
    return used() >= 35;
}

async function _obtainGoY(bot, peakY, timedOut) {
    const y = Math.floor(bot.entity.position.y);
    if (Math.abs(y - peakY) <= 2) return true;
    if (typeof _mineOreStairDown === "function" && y > peakY + 1) {
        await _mineOreStairDown(bot, peakY, timedOut, null);
        return Math.floor(bot.entity.position.y) <= peakY + 2;
    }
    if (typeof _mineOreStairUp === "function" && y < peakY - 1) {
        await _mineOreStairUp(bot, peakY, timedOut, null);
        return Math.abs(Math.floor(bot.entity.position.y) - peakY) <= 3;
    }
    try {
        if (_obtainAborted()) return false;
        bot.pathfinder.setGoal(new GoalBlock(
            Math.floor(bot.entity.position.x), peakY, Math.floor(bot.entity.position.z)
        ));
        const limit = Date.now() + 30000;
        while (Date.now() < limit && !timedOut()) {
            if (Math.abs(bot.entity.position.y - peakY) < 2.5) break;
            await bot.waitForTicks(4);
        }
    } catch (err) {}
    try { bot.pathfinder.setGoal(null); } catch (err) {}
    return Math.abs(Math.floor(bot.entity.position.y) - peakY) <= 4;
}

async function _obtainMineOne(bot, pos, spec) {
    const block = bot.blockAt(pos);
    if (!block) return false;
    const pick = _obtainLowestPick(bot, spec.minTool);
    if (!pick) return false;
    try { await bot.equip(pick, "hand"); } catch (err) { return false; }
    if (typeof _mineOreSafeDig === "function") {
        const r = await _mineOreSafeDig(bot, pos);
        if (r === "ok") {
            try { if (typeof _mineOreWalk === "function") await _mineOreWalk(bot, pos, 800); } catch (err) {}
            return true;
        }
        return false;
    }
    try {
        const dug = await _obtainRace(bot, () => bot.dig(block), 8000, "dig");
        if (!dug) throw new Error("dig timeout");
        try { if (typeof _mineOreWalk === "function") await _mineOreWalk(bot, pos, 800); } catch (err) {}
        return true;
    } catch (err) {
        try {
            if (bot.collectBlock && typeof bot.collectBlock.collect === "function") {
                await _obtainRace(bot, () => bot.collectBlock.collect(block, { ignoreNoPath: true, count: 1 }), 20000, "collectBlock");
                return true;
            }
        } catch (e2) {}
        return false;
    }
}

function _obtainOnlyHighPick(bot) {
    // true when stone/deepslate would have to be dug with an iron (or better) pickaxe
    const lp = _obtainLowestPick(bot, "wooden");
    return !!(lp && /^(iron|diamond|netherite)_/.test(lp.name));
}

async function _obtainMineDeep(bot, spec, n, timedOut, strat) {
    if (!spec) return { ok: false, reason: "unknown_item" };
    strat = strat || _obtainCurrentStrategy(bot, "mineDeep:" + ((spec && spec.key) || "ore"));
    const countOf = (nm) => _obtainCount(bot, nm);
    const have = () => spec.drops.reduce((s, d) => s + countOf(d), 0);
    const start = have();
    const target = start + Math.max(1, n);
    let destY = spec.peakY;
    const extraSpecs = [];
    try {
        const st = bot._obtainState || {};
        const bom = st.rootItem ? _obtainBomNeed(bot, st.rootItem, st.rootNeed || 1) : {};
        for (const k of Object.keys(bom)) {
            if (k.indexOf("deep:") !== 0) continue;
            const s = _obtainDeepSpec(k.slice(5));
            if (!s) continue;
            extraSpecs.push(s);
        }
    } catch (err) {}
    const ids = [];
    const allSpecs = [spec].concat(extraSpecs.filter((s) => s.key !== spec.key));
    for (const s of allSpecs) {
        for (const bn of s.blocks || []) {
            if (mcData.blocksByName[bn]) ids.push(mcData.blocksByName[bn].id);
        }
    }
    const oreNames = new Set([].concat(...allSpecs.map((x) => x.blocks || [])));
    const scan = (dist) => {
        try {
            return bot.findBlocks({ matching: ids, maxDistance: dist, count: 12 }) || [];
        } catch (err) {
            return [];
        }
    };
    const distTo = (pos) => bot.entity.position.distanceTo(pos.offset(0.5, 0.5, 0.5));
    const sampleFound = (spots) => {
        try {
            return (spots || []).slice(0, 3).map((p) => {
                const b = bot.blockAt(p);
                return String(b && b.name || "?") + "@y" + Math.floor(p.y) + "d" + Math.floor(distTo(p));
            }).join(",");
        } catch (err) {
            return "";
        }
    };
    const remember = (spots) => {
        bot._obtainState = bot._obtainState || { oreSpots: [] };
        bot._obtainState.oreSpots = bot._obtainState.oreSpots || [];
        for (const p of spots || []) {
            bot._obtainState.oreSpots.push({ x: p.x, y: p.y, z: p.z, key: spec.key });
            if (bot._obtainState.oreSpots.length > 24) bot._obtainState.oreSpots.shift();
        }
    };
    const tryFound = async (spots) => {
        _obtainPhase(bot, "mine");
        remember(spots);
        const black = bot._obtainState.oreBlack || {};
        // same height first (the tunnel walker is horizontal), then nearest; at most one height change per pass
        const yHere = () => Math.floor(bot.entity.position.y);
        const off = (q) => (Math.abs(q.y - yHere()) <= 1 ? 0 : 1);
        const ordered = (spots || []).slice().sort((a, b) => (off(a) - off(b)) || (distTo(a) - distTo(b)));
        let heightMoves = 0;
        for (const pos of ordered) {
            if (timedOut() || have() >= target) break;
            const bk = pos.x + "," + pos.y + "," + pos.z;
            if (black[bk]) continue;
            // a remembered spot may already be mined (air): never tunnel to it or hand its name to mineBlock
            const cur = bot.blockAt(pos);
            if (!cur || !oreNames.has(cur.name)) {
                black[bk] = true;
                bot._obtainState.oreBlack = black;
                continue;
            }
            if (!_obtainLowestPick(bot, spec.minTool)) return { ok: false, reason: "missing_tool" };
            const pick = _obtainLowestPick(bot, spec.minTool);
            try { await bot.equip(pick, "hand"); } catch (err) {}
            const goal = pos.offset(0.5, 0.5, 0.5);
            let dist = bot.entity.position.distanceTo(goal);
            if (dist > 32) continue;
            // the tunnel walker cannot climb a shaft or dig under its feet: reach the ore's height by stairs first
            const dyOre = Math.abs(pos.y - Math.floor(bot.entity.position.y));
            if (dist > 4.2 && dyOre >= 2 && (heightMoves >= 1 || dyOre > 10 || (spec.blocks || []).indexOf(cur.name) < 0)) {
                continue;
            }
            if (dist > 4.2 && dyOre >= 2) {
                heightMoves += 1;
                try { await _obtainGoY(bot, pos.y, timedOut); } catch (err) {}
                if (timedOut()) break;
                dist = bot.entity.position.distanceTo(goal);
            }
            if (dist > 4.2) {
                _obtainPhase(bot, "mine");
                const reached = await _obtainTunnelTo(bot, pos, timedOut);
                dist = bot.entity.position.distanceTo(goal);
                if (!reached && dist > 5.5) {
                    black[bk] = true;
                    bot._obtainState.oreBlack = black;
                    continue;
                }
            }
            const blk = bot.blockAt(pos);
            if (blk && oreNames.has(blk.name) && strat === "vanilla" && typeof mineBlock === "function" && dist <= 5.5) {
                await _obtainRace(bot, () => mineBlock(bot, blk.name, 1), 20000, "mineBlock");
                if (have() >= target) break;
                dist = bot.entity.position.distanceTo(goal);
            }
            if (bot.entity.position.distanceTo(goal) > 5.5) {
                const reached = await _obtainTunnelTo(bot, pos, timedOut);
                if (!reached && bot.entity.position.distanceTo(goal) > 5.5) {
                    black[bk] = true;
                    bot._obtainState.oreBlack = black;
                    continue;
                }
            }
            await _obtainMineOne(bot, pos, spec);
            await _obtainMaybeToss(bot);
        }
        return null;
    };
    let yNow = Math.floor(bot.entity.position.y);
    let found = scan(32);
    try { console.log("[obtain] mineDeep", spec.key, "y=" + yNow, "scan32", found.length, sampleFound(found)); } catch (err) {}
    if (strat === "explore" && !found.length) {
        _obtainPhase(bot, "search_ore");
        const pos = await _obtainExploreFor(bot, ids, 40, timedOut);
        if (pos) found = [pos].concat(scan(32));
    }
    const key = spec.key || (spec.drops && spec.drops[0]) || "ore";
    const atPeak = Math.abs(yNow - destY) <= 8;
    if (!atPeak) {
        const nearby = found.filter((p) => distTo(p) <= 16);
        const earlyClose = await tryFound(nearby);
        if (earlyClose) return earlyClose;
        if (have() >= target) return { ok: true, reason: "done" };
        if (!timedOut()) {
            _obtainPhase(bot, "descend");
            const p0 = bot.entity.position.floored();
            bot._obtainState.minePos = bot._obtainState.minePos || {
                x: p0.x + 0.5, y: destY, z: p0.z + 0.5,
            };
            let guard = 0;
            while (Math.floor(bot.entity.position.y) > destY + 2 && !timedOut() && guard < 20) {
                guard += 1;
                if (_obtainOnlyHighPick(bot)) {
                    return have() > start ? { ok: true, reason: "partial" } : { ok: false, reason: "need_stone_pick" };
                }
                const yBefore = Math.floor(bot.entity.position.y);
                const slice = Math.max(destY, yBefore - 8);
                await _obtainGoY(bot, slice, timedOut);
                const mid = scan(32);
                if (mid.length) {
                    const r = await tryFound(mid);
                    if (r) return r;
                    if (have() >= target) return { ok: true, reason: "done" };
                }
                if (Math.floor(bot.entity.position.y) >= yBefore) break;
            }
            yNow = Math.floor(bot.entity.position.y);
            bot._obtainState.atY[key] = Math.abs(yNow - destY) <= 8;
            try { console.log("[obtain] mineDeep atY", yNow, "peak", destY); } catch (err) {}
            found = scan(32);
            try { console.log("[obtain] mineDeep afterGoY y=" + yNow, "n", found.length, sampleFound(found)); } catch (err) {}
        }
    }
    const early = await tryFound(found);
    if (early) return early;
    if (have() >= target) return { ok: true, reason: "done" };
    if (timedOut()) return { ok: false, reason: "timeout" };
    _obtainPhase(bot, "search_ore");
    bot._obtainState.tunnel = bot._obtainState.tunnel || { dx: 1, dz: 0, length: 0 };
    const st = bot._obtainState.tunnel;
    const pick2 = _obtainBestPick(bot, spec.minTool) || _obtainBestPick(bot, "wooden");
    if (pick2) {
        try { await bot.equip(pick2, "hand"); } catch (err) {}
    }
    for (let step = 0; step < 160 && have() < target && !timedOut(); step++) {
        if (_obtainOnlyHighPick(bot)) {
            return have() > start ? { ok: true, reason: "partial" } : { ok: false, reason: "need_stone_pick" };
        }
        if (step % 12 === 0) {
            const more = scan(32);
            const e2 = await tryFound(more);
            if (e2) return e2;
            if (have() >= target) break;
            try {
                const cave = bot.findBlock({
                    matching: (b) => b && b.name === "cave_air",
                    maxDistance: 32,
                });
                if (cave && cave.position && distTo(cave.position) > 3 && typeof _mineOreTunnelToward === "function") {
                    await _mineOreTunnelToward(bot, cave.position, timedOut);
                    const fromCave = scan(32);
                    const e3 = await tryFound(fromCave);
                    if (e3) return e3;
                }
            } catch (err) {}
        }
        if (typeof _mineOreStripStep === "function") {
            const r = await _mineOreStripStep(bot, st, timedOut);
            if (r === "liquid" || r === "blocked") {
                const ndx = -st.dz;
                const ndz = st.dx;
                st.dx = ndx || -st.dx || 1;
                st.dz = ndz;
                if (st.dx === 0 && st.dz === 0) st.dx = 1;
            }
            st.length += 1;
        } else {
            break;
        }
        const p = bot.entity.position.floored();
        bot._obtainState.minePos = { x: p.x + 0.5, y: p.y, z: p.z + 0.5 };
    }
    if (have() >= target) return { ok: true, reason: "done" };
    if (have() > start) return { ok: true, reason: "partial" };
    if (timedOut()) return { ok: false, reason: "timeout" };
    return { ok: false, reason: "no_progress" };
}

function _obtainStrategies(leafKey) {
    const k = String(leafKey || "");
    const kind = k.indexOf(":") >= 0 ? k.split(":")[0] : k;
    const name = k.indexOf(":") >= 0 ? k.split(":").slice(1).join(":") : k;
    if (kind === "craft" || kind === "smelt") {
        return ["vanilla"];
    }
    if (_obtainIsSurfaceGather(name) || name === "log" || k.indexOf("gather:log") === 0) {
        return ["vanilla", "explore"];
    }
    return ["vanilla", "explore", "descend", "tunnel"];
}

function _obtainIsMoveLeaf(leaf) {
    const k = leaf && leaf.kind;
    return k === "gather" || k === "mineDeep";
}

function _obtainMissingIngs(bot, item, n) {
    const rec = _obtainPickRecipe(bot, item);
    const haveItem = _obtainCount(bot, item);
    const gap = Math.max(0, (n || 1) - haveItem);
    if (gap <= 0) return [];
    if (!rec) return [{ name: item, need: n, have: haveItem, gap: gap }];
    const crafts = Math.ceil(gap / Math.max(1, rec.resultCount));
    const miss = [];
    for (const ing of rec.ings || []) {
        const needIng = ing.n * crafts;
        const haveIng = _obtainCount(bot, ing.name);
        if (haveIng < needIng) {
            miss.push({ name: ing.name, need: needIng, have: haveIng, gap: needIng - haveIng });
        }
    }
    return miss;
}

async function _obtainMakePlaceSpot(bot, timedOut) {
    if (!bot.entity || !bot.entity.position) return false;
    const p = bot.entity.position.floored();
    const dig = async (pos) => {
        try {
            const work = () => ((typeof _mineOreSafeDig === "function")
                ? _mineOreSafeDig(bot, pos, { seal: false })
                : (async () => {
                    const b = bot.blockAt(pos);
                    if (b && !_obtainIsAirBlock(b)) await _obtainRace(bot, () => bot.dig(b), 4000, "placeSpotDig");
                })());
            await _obtainRace(bot, work, 4000, "placeSpotDig");
        } catch (err) {}
    };
    try {
        await dig(p.offset(1, 0, 0));
        await dig(p.offset(1, 1, 0));
        await dig(p.offset(-1, 0, 0));
        await dig(p.offset(-1, 1, 0));
    } catch (err) {}
    return _obtainEnsurePlaced(bot, "crafting_table", timedOut);
}

async function _obtainRecoverCraftOrSmelt(bot, leaf, r, timedOut) {
    const item = (leaf && (leaf.item || leaf.name)) || "";
    const reason = (r && r.reason) || "no_progress";
    try { console.log("[obtain] reexpand", leaf && leaf.kind, item, reason); } catch (e) {}
    if ((leaf && leaf.kind === "smelt") || reason === "missing_fuel") {
        const need = Math.max(1, (leaf && leaf.n) || 1);
        if (_obtainFuelUnits(bot) < need) {
            const y = bot.entity && bot.entity.position ? bot.entity.position.y : 64;
            if (y < 50) {
                const ug = await _obtainUndergroundFuel(bot, need, timedOut);
                if (ug && ug.ok) return;
            }
            const haveP = _obtainCount(bot, "planks");
            const haveL = _obtainCount(bot, "log");
            if (haveL >= 1 && haveP < need) {
                const w = _obtainWoodKind(bot);
                await _obtainDoLeaf(bot, { kind: "craft", item: w + "_planks", n: haveP + 4 }, timedOut);
                return;
            }
            const yNow = bot.entity && bot.entity.position ? bot.entity.position.y : 64;
            if (yNow >= 50) {
                await _obtainDoLeaf(bot, { kind: "gather", name: "log", n: Math.max(1, Math.ceil(need / 4)) }, timedOut);
            }
            return;
        }
    }
    if (leaf && leaf.kind === "craft") {
        const miss = _obtainMissingIngs(bot, item, leaf.n || 1);
        if (miss.length) {
            const ing = miss[0];
            try { console.log("[obtain] missing_ing " + ing.name + " n=" + ing.gap); } catch (e) {}
            const sub = _obtainFirstLeaf(bot, ing.name, ing.need, 0);
            if (sub && !(sub.kind === "craft" && sub.item === item)) {
                await _obtainDoLeaf(bot, sub, timedOut);
                return;
            }
        }
        const rec = _obtainPickRecipe(bot, item);
        if (rec && rec.needsTable) {
            bot._obtainState = bot._obtainState || {};
            bot._obtainState.placeTries = (bot._obtainState.placeTries || 0) + 1;
            if (bot._obtainState.placeTries <= 3) {
                await _obtainMakePlaceSpot(bot, timedOut);
            }
        }
    }
}

function _obtainCurrentStrategy(bot, leafKey) {
    bot._obtainState = bot._obtainState || { stratIdx: {}, triedStrategies: [] };
    bot._obtainState.stratIdx = bot._obtainState.stratIdx || {};
    bot._obtainState.triedStrategies = bot._obtainState.triedStrategies || [];
    const list = _obtainStrategies(leafKey);
    const idx = bot._obtainState.stratIdx[leafKey] || 0;
    const s = list[((idx % list.length) + list.length) % list.length];
    if (bot._obtainState.triedStrategies.indexOf(s) < 0) {
        bot._obtainState.triedStrategies.push(s);
    }
    return s;
}

function _obtainRotateStrategy(bot, leafKey) {
    bot._obtainState = bot._obtainState || { stratIdx: {}, triedStrategies: [] };
    bot._obtainState.stratIdx = bot._obtainState.stratIdx || {};
    bot._obtainState.stratIdx[leafKey] = (bot._obtainState.stratIdx[leafKey] || 0) + 1;
    const s = _obtainCurrentStrategy(bot, leafKey);
    try { console.log("[obtain] rotate no_progress:" + leafKey + " -> " + s); } catch (err) {}
    return s;
}

function _obtainStrategyBudgetMs(strat, leafName) {
    const logs = leafName === "log" || String(leafName).endsWith("_log") || String(leafName).endsWith("_stem");
    if (strat === "vanilla") return logs ? 90000 : 45000;
    if (strat === "explore") return logs ? 90000 : 50000;
    if (strat === "descend") return 90000;
    if (strat === "tunnel") return 60000;
    return 45000;
}

function _obtainExploreDir(bot) {
    const dirs = [
        new Vec3(1, 0, 0), new Vec3(-1, 0, 0),
        new Vec3(0, 0, 1), new Vec3(0, 0, -1),
        new Vec3(1, 0, 1), new Vec3(-1, 0, 1),
        new Vec3(1, 0, -1), new Vec3(-1, 0, -1),
        new Vec3(0, -1, 0),
    ];
    bot._obtainState = bot._obtainState || {};
    bot._obtainState.heading = (bot._obtainState.heading || 0) + 1;
    return dirs[bot._obtainState.heading % dirs.length];
}

function _obtainBlockIds(names) {
    const ids = [];
    for (const n of names || []) {
        if (mcData.blocksByName[n]) ids.push(mcData.blocksByName[n].id);
    }
    return ids;
}

function _obtainGatherBlocks(name) {
    const n = String(name || "");
    const woods = ["oak", "spruce", "birch", "jungle", "acacia", "dark_oak", "mangrove"];
    if (n === "log" || n === "wood" || n === "logs") {
        return woods.flatMap((w) => [w + "_log", "stripped_" + w + "_log"]);
    }
    if (n === "cobblestone" || n === "stone") {
        return ["stone", "cobblestone"];
    }
    if (typeof _gatherSpec === "function") {
        const spec = _gatherSpec(n);
        if (spec && spec.blocks) return spec.blocks;
    }
    if (n.endsWith("_log") || n.endsWith("_stem")) return [n];
    return [n];
}

function _obtainIsPlant(block) {
    if (!block) return false;
    const n = String(block.name || "");
    return n === "grass" || n === "tall_grass" || n === "fern" || n.endsWith("_fern")
        || n.endsWith("_flower") || n === "dandelion" || n === "poppy"
        || n === "snow" || n === "snow_layer" || n.endsWith("_carpet")
        || n === "torch" || n.endsWith("_sapling") || n === "dead_bush"
        || n === "sugar_cane" || n === "kelp" || n === "seagrass" || n === "vine";
}

async function _obtainWalkLine(bot, dir, ids, far, scanEvery, timedOut) {
    const dead = timedOut || (() => false);
    if (!bot.entity || !bot.entity.position) return null;
    const dx = dir && dir.x ? Math.sign(dir.x) : 0;
    const dz = dir && dir.z ? Math.sign(dir.z) : 0;
    if (dx === 0 && dz === 0) return null;
    const start = bot.entity.position.clone();
    const maxFar = far > 0 ? far : 150;
    const every = scanEvery > 0 ? scanEvery : 16;
    const scan = () => {
        try {
            const found = bot.findBlocks({ matching: ids, maxDistance: 32, count: 1 });
            return found && found.length ? found[0] : null;
        } catch (err) {
            return null;
        }
    };
    const already = scan();
    if (already) return already;
    const yaw = Math.atan2(-dx, -dz);
    try { await bot.look(yaw, 0, true); } catch (err) {}
    let lastScanAt = 0;
    const cap = Date.now() + _obtainRemainMs(90000);
    try {
        bot.setControlState("forward", true);
        bot.setControlState("sprint", true);
        bot.setControlState("jump", true);
        while (Date.now() < cap && !dead() && !_obtainAborted()) {
            try { await bot.look(yaw, 0, true); } catch (err) {}
            const travel = Math.hypot(bot.entity.position.x - start.x, bot.entity.position.z - start.z);
            if (travel - lastScanAt >= every || lastScanAt === 0) {
                lastScanAt = travel;
                const hit = scan();
                if (hit) return hit;
            }
            if (travel >= maxFar) break;
            try { await bot.waitForTicks(4); } catch (err) { break; }
        }
    } finally {
        try { bot.setControlState("forward", false); } catch (e) {}
        try { bot.setControlState("sprint", false); } catch (e) {}
        try { bot.setControlState("jump", false); } catch (e) {}
        _obtainAbortMotions(bot);
    }
    return scan();
}

async function _obtainExploreFor(bot, ids, seconds, timedOut) {
    const hit = () => {
        try {
            const found = bot.findBlocks({ matching: ids, maxDistance: 32, count: 1 });
            return found && found.length ? found[0] : null;
        } catch (err) {
            return null;
        }
    };
    const already = hit();
    if (already) return already;
    if (typeof exploreUntil !== "function") return null;
    const dir = _obtainExploreDir(bot);
            const cap = Math.max(8, Math.min(seconds || 30, 90));
    try {
        const exploreP = exploreUntil(bot, dir, cap, () => {
            if (timedOut && timedOut()) return true;
            if (_obtainAborted()) return true;
            const p = hit();
            return p ? (bot.blockAt(p) || true) : false;
        });
        let exploreTimer = null;
        const block = await Promise.race([
            exploreP,
            new Promise((resolve) => {
                exploreTimer = setTimeout(() => resolve(null), _obtainRemainMs(cap * 1000));
            }),
        ]);
        if (exploreTimer) clearTimeout(exploreTimer);
        if (block && block.position) return block.position;
        _obtainAbortMotions(bot);
    } catch (err) {
        _obtainAbortMotions(bot);
    }
    return hit();
}

async function _obtainVanillaMineBlock(bot, blockName, count) {
    if (typeof mineBlock !== "function") return false;
    if (_obtainAborted()) return false;
    return _obtainRace(bot, () => mineBlock(bot, blockName, 1), 40000, "mineBlock");
}

async function _obtainTunnelTo(bot, dest, timedOut) {
    if (!dest) return false;
    const goal = dest.floored ? dest.floored() : dest;
    const key = goal.x + "," + goal.y + "," + goal.z;
    bot._obtainState = bot._obtainState || { oreBlack: {} };
    bot._obtainState.oreBlack = bot._obtainState.oreBlack || {};
    const cap = Date.now() + 60000;
    const t = () => (timedOut && timedOut()) || Date.now() >= cap;
    let stall = 0;
    let lastDist = 999;
    while (!t() && !_obtainAborted()) {
        const p = bot.entity.position.floored();
        const dist = p.distanceTo(goal);
        if (dist <= 3) return true;
        if (dist >= lastDist - 0.05) stall += 1;
        else stall = 0;
        lastDist = dist;
        if (stall >= 14) {
            bot._obtainState.oreBlack[key] = true;
            return false;
        }
        const dx = Math.sign(goal.x - p.x);
        const dy = Math.sign(goal.y - p.y);
        const dz = Math.sign(goal.z - p.z);
        let nx = dx;
        let ny = 0;
        let nz = dx ? 0 : dz;
        if (nx === 0 && nz === 0) ny = dy;
        const feet = p.offset(nx, ny, nz);
        const head = feet.offset(0, 1, 0);
        const dig = async (pos) => {
            if (typeof _mineOreSafeDig === "function") return _mineOreSafeDig(bot, pos);
            const b = bot.blockAt(pos);
            if (!b || b.name === "air" || b.name === "cave_air" || b.name === "void_air") return "air";
            if (b.name === "bedrock") return "blocked";
            try { await bot.dig(b); return "ok"; } catch (err) { return "fail"; }
        };
        const r1 = await dig(feet);
        const r2 = await dig(head);
        if (r1 === "liquid" || r2 === "liquid") {
            bot._obtainState.oreBlack[key] = true;
            return false;
        }
        if (typeof _mineOreWalk === "function") await _mineOreWalk(bot, feet, 1200);
        else {
            try {
                bot.setControlState("forward", true);
                await bot.waitForTicks(8);
            } finally {
                try { bot.setControlState("forward", false); } catch (e) {}
            }
        }
    }
    return bot.entity.position.floored().distanceTo(goal) <= 4;
}

async function _obtainCraftInInventory(bot, itemName, timedOut) {
    // 2x2 inventory crafting (planks -> crafting_table), no table involved
    if (timedOut && timedOut()) return false;
    const craftOne = async (name) => {
        const def = mcData.itemsByName[name];
        if (!def) return false;
        const rec = (bot.recipesFor(def.id, null, 1, null) || [])[0];
        if (!rec) return false;
        return _obtainRace(bot, () => bot.craft(rec, 1, null), 8000, "craftInv");
    };
    if (itemName === "crafting_table" && _obtainCount(bot, "planks") < 4 && _obtainCount(bot, "log") >= 1) {
        const w = _obtainWoodKind(bot);
        await craftOne(w + "_planks");
    }
    const ok = await craftOne(itemName);
    try { console.log("[obtain] craft_in_inventory " + itemName + " ok=" + ok); } catch (err) {}
    return ok && _obtainCount(bot, itemName) >= 1;
}

async function _obtainEnsurePlaced(bot, name, timedOut) {
    const def = mcData.blocksByName[name];
    if (!def) return null;
    let blk = bot.findBlock({ matching: def.id, maxDistance: 32 });
    if (blk && bot.entity && bot.entity.position && blk.position
        && bot.entity.position.distanceTo(blk.position) <= 6) return blk;
    if (_obtainCount(bot, name) < 1 && name === "crafting_table" && blk) {
        // the only table is out of reach (e.g. left on the surface): make a new one here in the 2x2 inventory grid
        // (Voyager's craftItem would first walk to the far table, 30 s pathfinder timeout each time)
        try { await _obtainCraftInInventory(bot, "crafting_table", timedOut); } catch (err) {}
    }
    if (_obtainCount(bot, name) < 1) return blk || null;
    const base = bot.entity.position.floored();
    const offsets = [];
    for (const dy of [0, 1, -1]) {
        for (let dx = -2; dx <= 2; dx++) {
            for (let dz = -2; dz <= 2; dz++) {
                if (dx || dz) offsets.push(new Vec3(dx, dy, dz));
            }
        }
    }
    // same layer first, then the step above / below (bottom of a staircase), nearest first within a layer
    offsets.sort((a, b) => (Math.abs(a.y) - Math.abs(b.y)) || (Math.abs(a.x) + Math.abs(a.z) - (Math.abs(b.x) + Math.abs(b.z))));
    for (const off of offsets) {
        if (timedOut && timedOut()) break;
        const pos = base.offset(off.x, off.y, off.z);
        let here = bot.blockAt(pos);
        let below = bot.blockAt(pos.offset(0, -1, 0));
        if (_obtainIsPlant(here)) {
            try { await bot.dig(here); } catch (e) {}
            here = bot.blockAt(pos);
        }
        if (_obtainIsPlant(below)) {
            try { await bot.dig(below); } catch (e) {}
            below = bot.blockAt(pos.offset(0, -1, 0));
        }
        if (!below || below.boundingBox !== "block") continue;
        if (here && here.name !== "air" && here.boundingBox !== "empty") continue;
        try {
            if (typeof placeItem === "function") await _obtainRace(bot, () => placeItem(bot, name, pos), 20000, "placeItem");
        } catch (err) {}
        let placed = bot.blockAt(pos);
        if (placed && placed.name === name) return placed;
        try {
            const item = bot.inventory.findInventoryItem(mcData.itemsByName[name].id);
            if (!item) break;
            await bot.equip(item, "hand");
            await _obtainRace(bot, () => bot.placeBlock(below, new Vec3(0, 1, 0)), 8000, "placeBlock");
        } catch (err) {}
        placed = bot.blockAt(pos);
        if (placed && placed.name === name) return placed;
    }
    return bot.findBlock({ matching: def.id, maxDistance: 32 });
}

async function _obtainCraftVanilla(bot, item, n, timedOut) {
    const before = _obtainCount(bot, item);
    if (before >= n) return { ok: true, reason: "already_satisfied" };
    if (_obtainMissingIngs(bot, item, n).length) return { ok: false, reason: "missing_materials" };
    await _obtainEnsurePlaced(bot, "crafting_table", timedOut);
    const rec = _obtainPickRecipe(bot, item);
    const per = Math.max(1, (rec && rec.resultCount) || 1);
    const times = Math.max(1, Math.ceil((n - before) / per));
    if (typeof craftItem === "function") {
        await _obtainRace(bot, () => craftItem(bot, item, times), 35000, "craftItem");
    }
    if (_obtainCount(bot, item) >= n) return { ok: true, reason: "done" };
    if (timedOut && timedOut()) return { ok: false, reason: "timeout" };
    if (typeof craft === "function") {
        try {
            await _obtainRace(bot, () => craft(bot, item, n), 40000, "craft");
            if (_obtainCount(bot, item) >= n) return { ok: true, reason: "done" };
            if (_obtainCount(bot, item) > before) return { ok: true, reason: "partial" };
        } catch (err) {}
    }
    if (_obtainCount(bot, item) > before) return { ok: true, reason: "partial" };
    if (_obtainMissingIngs(bot, item, n).length) return { ok: false, reason: "missing_materials" };
    return { ok: false, reason: "no_progress" };
}

async function _obtainSmeltVanilla(bot, item, n, timedOut) {
    const outName = (OBTAIN_KNOWLEDGE.smelt && OBTAIN_KNOWLEDGE.smelt[item]) || item;
    const before = _obtainCount(bot, outName);
    if (_obtainFuelUnits(bot) < 1) return { ok: false, reason: "missing_fuel" };
    await _obtainEnsurePlaced(bot, "furnace", timedOut);
    let fuel = "oak_planks";
    if (typeof _smeltPickFuel === "function") fuel = _smeltPickFuel(bot) || fuel;
    else {
        const any = bot.inventory.items().find((i) =>
            i.name === "coal" || i.name === "charcoal" || i.name.endsWith("_planks") || i.name.endsWith("_log"));
        if (any) fuel = any.name;
    }
    if (typeof smeltItem === "function") {
        await _obtainRace(bot, () => smeltItem(bot, item, fuel, n), Math.min(120000, n * 15000 + 40000), "smeltItem");
    }
    if (_obtainCount(bot, outName) >= before + n) return { ok: true, reason: "done" };
    if (_obtainCount(bot, outName) > before) return { ok: true, reason: "partial" };
    if (timedOut && timedOut()) return { ok: false, reason: "timeout" };
    if (typeof smelt === "function") {
        try { await _obtainRace(bot, () => smelt(bot, item, n), 60000, "smelt"); } catch (err) {}
    }
    if (_obtainFuelUnits(bot) < 1) return { ok: false, reason: "missing_fuel" };
    return { ok: false, reason: "no_progress" };
}

async function _obtainGatherWithStrategy(bot, leaf, strat, timedOut) {
    const name = leaf.name;
    const want = Math.max(1, leaf.n || 1);
    const before = _obtainCount(bot, name);
    const names = _obtainGatherBlocks(name);
    const ids = _obtainBlockIds(names);
    if (_obtainIsSurfaceGather(name) && bot.entity && bot.entity.position.y < 60) {
        _obtainPhase(bot, "surface");
        await _obtainStandOnGround(bot, timedOut);
    }
    if ((strat === "descend" || strat === "tunnel") && _obtainIsSurfaceGather(name)) {
        strat = "explore";
    }
    const budget = Date.now() + _obtainStrategyBudgetMs(strat, name);
    const t = () => (timedOut && timedOut()) || Date.now() >= budget;
    const done = () => _obtainCount(bot, name) >= before + want;
    const progressed = () => _obtainCount(bot, name) > before;

    const mineAt = async (pos) => {
        if (!pos) return;
        const blk = bot.blockAt(pos);
        if (!blk) return;
        if (strat === "vanilla" || strat === "explore") {
            await _obtainVanillaMineBlock(bot, blk.name, Math.max(1, want));
            return;
        }
        const dist = bot.entity.position.distanceTo(pos.offset ? pos.offset(0.5, 0.5, 0.5) : pos);
        if (dist > 4.2) await _obtainTunnelTo(bot, pos, t);
        if (typeof _mineOreSafeDig === "function") await _mineOreSafeDig(bot, pos);
        else {
            try { await bot.dig(blk); } catch (err) {}
        }
    };

    if (strat === "vanilla") {
        try {
            const found = ids.length ? (bot.findBlocks({ matching: ids, maxDistance: 32, count: 4 }) || []) : [];
            if (found.length) {
                await mineAt(found[0]);
            }
        } catch (err) {}
        if (done()) return { ok: true, reason: "done" };
        if (progressed()) return { ok: true, reason: "partial" };
        if (typeof gather === "function") {
            const prev = globalThis.__obtainDeadline;
            try {
                globalThis.__obtainDeadline = Math.min(budget, Date.now() + _obtainRemainMs(60000));
                await _obtainRace(bot, () => gather(bot, name, want), 60000, "gather");
                if (progressed() && done()) return { ok: true, reason: "done" };
                if (progressed()) return { ok: true, reason: "partial" };
            } catch (err) {
            } finally {
                globalThis.__obtainDeadline = prev;
            }
        }
        return { ok: false, reason: "no_progress" };
    }

    if (strat === "explore") {
        if (_obtainIsSurfaceGather(name)) {
            await _obtainStandOnGround(bot, t);
            const dirs = [
                new Vec3(1, 0, 0), new Vec3(-1, 0, 0),
                new Vec3(0, 0, 1), new Vec3(0, 0, -1),
            ];
            for (let i = 0; i < dirs.length && !t() && !done(); i++) {
                const hit = await _obtainWalkLine(bot, dirs[i], ids, 150, 16, t);
                if (hit) {
                    await mineAt(hit);
                    break;
                }
            }
            if (done()) return { ok: true, reason: "done" };
            if (progressed()) return { ok: true, reason: "partial" };
            return { ok: false, reason: "no_progress" };
        }
        const pos = await _obtainExploreFor(bot, ids, 60, t);
        if (pos) await mineAt(pos);
        if (!done() && !progressed() && typeof _gatherWalkFar === "function") {
            try {
                const dir = _obtainExploreDir(bot);
                const hit = await _gatherWalkFar(bot, dir, () => {
                    try {
                        const found = bot.findBlocks({ matching: ids, maxDistance: 32, count: 1 });
                        return found && found.length ? bot.blockAt(found[0]) : null;
                    } catch (err) {
                        return null;
                    }
                }, t);
                if (hit && hit.position) await mineAt(hit.position);
            } catch (err) {}
        }
        if (done()) return { ok: true, reason: "done" };
        if (progressed()) return { ok: true, reason: "partial" };
        return { ok: false, reason: "no_progress" };
    }

    if (strat === "descend") {
        _obtainPhase(bot, "descend");
        const destY = (name === "cobblestone" || name === "stone" || name === "coal") ? 40 : 62;
        let guard = 0;
        while (!t() && !done() && guard < 16) {
            guard += 1;
            const hits = ids.length ? (bot.findBlocks({ matching: ids, maxDistance: 32, count: 4 }) || []) : [];
            if (hits.length) {
                await mineAt(hits[0]);
                if (done() || progressed()) break;
            }
            const y = Math.floor(bot.entity.position.y);
            if (y <= destY + 1) break;
            const slice = Math.max(destY, y - 8);
            await _obtainGoY(bot, slice, t);
            if (Math.floor(bot.entity.position.y) >= y) break;
        }
        if (done()) return { ok: true, reason: "done" };
        if (progressed()) return { ok: true, reason: "partial" };
        return { ok: false, reason: "no_progress" };
    }

    if (strat === "tunnel") {
        _obtainPhase(bot, "mine");
        const hits = ids.length ? (bot.findBlocks({ matching: ids, maxDistance: 32, count: 8 }) || []) : [];
        for (const pos of hits) {
            if (t() || done()) break;
            const reached = await _obtainTunnelTo(bot, pos, t);
            if (!reached) continue;
            await mineAt(pos);
        }
        if (!hits.length) {
            const p = bot.entity.position.floored();
            const fwd = p.offset(1, 0, 0);
            if (typeof _mineOreSafeDig === "function") {
                await _mineOreSafeDig(bot, fwd);
                await _mineOreSafeDig(bot, fwd.offset(0, 1, 0));
                await _mineOreSafeDig(bot, p.offset(0, -1, 0));
            }
        }
        if (done()) return { ok: true, reason: "done" };
        if (progressed()) return { ok: true, reason: "partial" };
        return { ok: false, reason: "no_progress" };
    }

    if (typeof gather === "function") {
        try {
            await _obtainRace(bot, () => gather(bot, name, want), 60000, "gather");
            if (done()) return { ok: true, reason: "done" };
            if (progressed()) return { ok: true, reason: "partial" };
        } catch (err) {}
    }
    return { ok: false, reason: "no_progress" };
}

