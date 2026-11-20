// Gather 8 more oak_log: gather(bot, "oak_log", 8);
// n is how many MORE to obtain (increment ≥ n), same meaning as mineBlock count.
// Picks a tool tier and 3 explore directions from the item, mines what is nearby,
// then explores 3 directions × 60s. Same block position failing twice is blacklisted.
async function gather(bot, name, n = 1) {
    const report = (ok, reason) => {
        bot.chat(`[contract] gather ${ok ? "ok" : "fail"} ${reason}`);
        try {
            globalThis.__skillOutcomes = globalThis.__skillOutcomes || [];
            globalThis.__skillOutcomes.push({ name: "gather", ok: !!ok, reason: String(reason), source: "contract" });
        } catch (err) {}
        return { ok: !!ok, reason: String(reason) };
    };
    if (typeof name !== "string") return report(false, "unknown_item");
    if (typeof n !== "number") n = 1;
    const spec = _gatherSpec(name);
    if (!spec) return report(false, "unknown_item");
    const countOf = (itemName) => {
        const def = mcData.itemsByName[itemName];
        if (!def) return 0;
        return bot.inventory.items().filter((i) => i.type === def.id).reduce((s, i) => s + i.count, 0);
    };
    const have = () => spec.drops.reduce((s, d) => s + countOf(d), 0);
    const startHave = have();
    const target = startHave + Math.max(0, Math.floor(Number(n) || 0));
    if (!(n > 0)) return report(true, "already_satisfied");

    const need = _gatherRequiredTool(spec);
    if (need) {
        const tool = _gatherFindTool(bot, need.kind, need.min);
        if (!tool) return report(false, "missing_tool");
        try {
            await bot.equip(tool, "hand");
        } catch (err) {
            return report(false, "missing_tool");
        }
    }

    const deadline = (typeof globalThis.__obtainDeadline === "number" && globalThis.__obtainDeadline > Date.now())
        ? globalThis.__obtainDeadline
        : Date.now() + 540000;
    const deadline2 = Math.min(deadline, (typeof globalThis.__stepStart === "number" && Date.now() - globalThis.__stepStart < 600000) ? globalThis.__stepStart + 540000 : Infinity);
    const myGen = globalThis.__obtainGen;
    const myOp = globalThis.__obtainOpSeq || 0;
    const timedOut = () => Date.now() >= deadline2
        || (typeof myGen === "number" && globalThis.__obtainGen !== myGen)
        || (!!globalThis.__obtainInCall && !!globalThis.__obtainAborted)
        // abandoned by obtain's race (it bumps the op sequence): stop instead of fighting the next action
        || (!!globalThis.__obtainInCall && (globalThis.__obtainOpSeq || 0) !== myOp);
    const rawDist = Number(globalThis.__gatherMaxDistance);
    const maxDist = Math.min(32, (rawDist > 0 ? rawDist : 32));
    const exploreDirs = _gatherExploreDirs(spec);

    const failCounts = new Map();
    const blacklisted = new Set();
    let stallFails = 0;
    let stallT0 = Date.now();
    let stallHave = startHave;
    const stallMs = (spec.drops || []).some((d) => String(d).endsWith("_log") || String(d).endsWith("_stem"))
        ? 300000
        : 90000;
    const posKey = (p) => {
        if (!p || p.x == null || p.y == null || p.z == null) return "";
        return `${Math.floor(p.x)},${Math.floor(p.y)},${Math.floor(p.z)}`;
    };
    const findTarget = () => {
        try {
            return bot.findBlock({
                matching: (b) => {
                    if (!b || !spec.blocks.includes(b.name)) return false;
                    // mineflayer palette probe: Block.fromStateId(id, 0) has no position
                    if (!b.position) return true;
                    return !blacklisted.has(posKey(b.position));
                },
                maxDistance: maxDist,
            });
        } catch (err) {
            return null;
        }
    };

    const tryMine = async (block) => {
        if (timedOut()) return;
        if (!block || !block.position || !posKey(block.position)) return;
        const k = posKey(block.position);
        const before = have();
        try {
            const needTool = _gatherRequiredTool(spec);
            if (needTool) {
                const tool = _gatherFindTool(bot, needTool.kind, needTool.min);
                if (!tool) {
                    stallFails += 1;
                    return;
                }
                try { await bot.equip(tool, "hand"); } catch (err) {}
            }
            const work = (async () => {
                try {
                    await bot.dig(block);
                } catch (err) {
                    if (needTool) throw err;
                    if (bot.collectBlock && typeof bot.collectBlock.collect === "function") {
                        await bot.collectBlock.collect(block, { ignoreNoPath: true, count: 1 });
                    } else {
                        throw err;
                    }
                }
            })();
            let timer;
            // walking to a log 10+ blocks away takes >5 s; a timed-out collect used to keep running and the next
            // block started another one (09-26: 11+ overlapping collects). Stop it and let it settle first.
            const digMs = (typeof _obtainRemainMs === "function")
                ? Math.max(1, Math.min(20000, _obtainRemainMs(20000)))
                : 20000;
            try {
                await Promise.race([
                    work,
                    new Promise((_, reject) => {
                        timer = setTimeout(() => reject(new Error("gather dig timeout")), digMs);
                    }),
                ]).finally(() => { if (timer) clearTimeout(timer); });
            } catch (raceErr) {
                try { bot.pathfinder.setGoal(null); } catch (e) {}
                try { bot.stopDigging(); } catch (e) {}
                try { await Promise.race([work.catch(() => {}), new Promise((r) => setTimeout(r, 2500))]); } catch (e) {}
                throw raceErr;
            }
            try {
                const p = block.position;
                bot.setControlState("forward", true);
                bot.setControlState("sprint", true);
                const lim = Date.now() + 800;
                while (Date.now() < lim) {
                    if (bot.entity.position.distanceTo(p.offset(0.5, 0, 0.5)) < 1.6) break;
                    await bot.waitForTicks(1);
                }
            } catch (err) {
            } finally {
                try { bot.setControlState("forward", false); } catch (e) {}
                try { bot.setControlState("sprint", false); } catch (e) {}
            }
        } catch (err) {
            try { bot.pathfinder.setGoal(null); } catch (e) {}
            try { bot.pathfinder.stop(); } catch (e) {}
            try { bot.stopDigging(); } catch (e) {}
            try {
                if (bot.collectBlock && typeof bot.collectBlock.cancelTask === "function") {
                    bot.collectBlock.cancelTask();
                }
            } catch (e) {}
            failCounts.set(k, (failCounts.get(k) || 0) + 1);
            blacklisted.add(k);
            stallFails += 1;
            return;
        }
        if (have() <= before) {
            failCounts.set(k, (failCounts.get(k) || 0) + 1);
            if (failCounts.get(k) >= 2) blacklisted.add(k);
            stallFails += 1;
        } else {
            stallFails = 0;
            stallT0 = Date.now();
            stallHave = have();
        }
    };

    const stalled = () => {
        if (have() > stallHave) {
            stallHave = have();
            stallFails = 0;
            stallT0 = Date.now();
            return false;
        }
        return Date.now() - stallT0 >= stallMs;
    };

    let nearby = findTarget();
    let nearbyTries = 0;
    while (nearby && have() < target && !timedOut() && nearbyTries < 64) {
        nearbyTries += 1;
        await tryMine(nearby);
        if (have() >= target) break;
        nearby = findTarget();
        if (!nearby) break;
    }
    if (have() >= target) return report(true, "done");

    if (spec.toolKind === "pickaxe" && have() < target && !timedOut()) {
        for (let i = 0; i < 12 && have() < target && !timedOut(); i++) {
            if (stalled()) break;
            const p = bot.entity.position.floored();
            const candidates = [
                p.offset(1, 0, 0), p.offset(-1, 0, 0), p.offset(0, 0, 1), p.offset(0, 0, -1),
                p.offset(1, -1, 0), p.offset(-1, -1, 0), p.offset(0, -1, 1), p.offset(0, -1, -1),
            ];
            let dug = false;
            for (const pos of candidates) {
                const b = bot.blockAt(pos);
                if (!b || b.name === "air" || b.name === "cave_air") continue;
                const ok = spec.blocks.includes(b.name)
                    || b.name === "dirt" || b.name === "grass_block" || b.name === "andesite"
                    || b.name === "diorite" || b.name === "granite" || b.name === "tuff";
                if (!ok) continue;
                await tryMine(b);
                dug = true;
                break;
            }
            const more = findTarget();
            if (more) await tryMine(more);
            if (!dug && !more) break;
        }
    }
    if (have() >= target) return report(true, "done");
    const dirs = exploreDirs.slice(0, 4);
    let exploreFails = 0;
    for (const dir of dirs) {
        if (have() >= target || timedOut()) break;
        if (stalled()) return report(false, "no_progress");
        let block = findTarget();
        if (!block) {
            try {
                block = await _gatherWalkFar(bot, dir, findTarget, timedOut);
            } catch (err) {
                exploreFails += 1;
                if (exploreFails >= dirs.length) return report(false, "no_progress");
                continue;
            }
        }
        if (!block) {
            exploreFails += 1;
            if (exploreFails >= dirs.length) return report(false, "no_progress");
            continue;
        }
        const beforeMine = have();
        await tryMine(block);
        if (have() <= beforeMine) {
            exploreFails += 1;
            if (exploreFails >= 3) return report(false, "no_progress");
        } else {
            exploreFails = 0;
        }
        while (have() < target && !timedOut()) {
            if (stalled()) return report(false, "no_progress");
            const more = findTarget();
            if (!more) break;
            await tryMine(more);
            if (timedOut()) break;
        }
    }
    if (have() >= target) return report(true, "done");
    globalThis.__gatherDirShift = (Number(globalThis.__gatherDirShift) || 0) + 3;
    if (timedOut()) return report(false, "timeout");
    if (stalled()) return report(false, "no_progress");
    return report(false, "not_found");
}

async function _gatherWalkFar(bot, dir, findTarget, timedOut) {
    const dead = timedOut || (() => false);
    if (!bot.entity || !bot.entity.position) return null;
    const dx = dir && dir.x ? Math.sign(dir.x) : 0;
    const dy = dir && dir.y ? Math.sign(dir.y) : 0;
    const dz = dir && dir.z ? Math.sign(dir.z) : 0;
    if (dx === 0 && dy === 0 && dz === 0) return findTarget();
    const start = bot.entity.position.clone();
    const far = dy < 0 ? 24 : 150;
    const cap = Date.now() + (dy < 0 ? 20000 : 90000);
    const yaw = Math.atan2(-dx, -dz);
    try { await bot.look(yaw, dy < 0 ? 0.6 : 0, true); } catch (err) {}
    try {
        if (typeof GoalNearXZ === "function") {
            bot.pathfinder.setGoal(new GoalNearXZ(
                bot.entity.position.x + dx * 28,
                bot.entity.position.z + dz * 28,
                2
            ));
            bot.pathfinder.setGoal(null);
        }
    } catch (err) {}
    let steps = 0;
    let lastScanAt = 0;
    try {
        bot.setControlState("forward", true);
        bot.setControlState("sprint", true);
        bot.setControlState("jump", true);
    } catch (err) {}
    try {
        while (Date.now() < cap && !dead() && steps < 400) {
            steps += 1;
            const travel = Math.hypot(bot.entity.position.x - start.x, bot.entity.position.z - start.z);
            if (dy >= 0 && (travel - lastScanAt >= 16 || lastScanAt === 0)) {
                lastScanAt = travel;
            }
            const hit = findTarget();
            if (hit) return hit;
            try { await bot.look(yaw, dy < 0 ? 0.6 : 0, true); } catch (err) {}
            if (travel >= far) break;
            if (dy < 0 && start.y - bot.entity.position.y >= 6) break;
            try { await bot.waitForTicks(4); } catch (err) { break; }
        }
    } finally {
        try { bot.setControlState("forward", false); } catch (e) {}
        try { bot.setControlState("sprint", false); } catch (e) {}
        try { bot.setControlState("jump", false); } catch (e) {}
        try { bot.pathfinder.setGoal(null); } catch (e) {}
    }
    return findTarget();
}

function _gatherExploreDirs(spec) {
    const horiz = [
        new Vec3(1, 0, 0), new Vec3(-1, 0, 0),
        new Vec3(0, 0, 1), new Vec3(0, 0, -1),
        new Vec3(1, 0, 1), new Vec3(-1, 0, 1),
        new Vec3(1, 0, -1), new Vec3(-1, 0, -1),
    ];
    const down = [
        new Vec3(0, -1, 0), new Vec3(1, -1, 0), new Vec3(-1, -1, 0),
        new Vec3(0, -1, 1), new Vec3(0, -1, -1), new Vec3(1, -1, 1),
    ];
    const first = spec && spec.dirs && spec.dirs[0];
    const base = first && first.y < 0 ? down : horiz;
    const n = base.length;
    const shift = ((Number(globalThis.__gatherDirShift) || 0) % n + n) % n;
    const out = [];
    for (let i = 0; i < n; i++) out.push(base[(shift + i) % n]);
    return out;
}

function _gatherFindTool(bot, kind, minTier) {
    if (kind === "shears" || minTier === "shears") {
        return bot.inventory.items().find((i) => i.name === "shears") || null;
    }
    const TIER = { wooden: 0, golden: 1, stone: 1, iron: 2, diamond: 3, netherite: 4 };
    const minIdx = TIER[minTier] == null ? 0 : TIER[minTier];
    const tools = bot.inventory.items().filter((i) => {
        const parts = i.name.split("_");
        return parts.length === 2 && parts[1] === kind && TIER[parts[0]] != null && TIER[parts[0]] >= minIdx;
    });
    tools.sort((a, b) => TIER[b.name.split("_")[0]] - TIER[a.name.split("_")[0]]);
    return tools[0] || null;
}

function _minToolTier(names, kind) {
    const TIER = { wooden: 0, golden: 1, stone: 1, iron: 2, diamond: 3, netherite: 4 };
    const suffix = "_" + kind;
    let minIdx = 99;
    let minName = null;
    for (const t of names || []) {
        if (!t || !t.endsWith(suffix)) continue;
        const prefix = t.slice(0, -suffix.length);
        if (TIER[prefix] == null) continue;
        if (TIER[prefix] < minIdx) {
            minIdx = TIER[prefix];
            minName = prefix;
        }
    }
    return minName;
}

function _harvestToolNames(blk) {
    if (!blk || !blk.harvestTools || !mcData.items) return [];
    return Object.keys(blk.harvestTools).map((id) => {
        const it = mcData.items[id];
        return it && it.name;
    }).filter(Boolean);
}

function _gatherRequiredTool(spec) {
    if (!spec) return null;
    for (const b of spec.blocks || []) {
        const blk = mcData.blocksByName && mcData.blocksByName[b];
        const names = _harvestToolNames(blk);
        const pick = names.filter((n) => n.endsWith("_pickaxe"));
        if (pick.length) return { kind: "pickaxe", min: _minToolTier(pick, "pickaxe") || "wooden" };
        if (names.indexOf("shears") >= 0) return { kind: "shears", min: "shears" };
        const shovel = names.filter((n) => n.endsWith("_shovel"));
        if (shovel.length) return { kind: "shovel", min: _minToolTier(shovel, "shovel") || "wooden" };
        const axe = names.filter((n) => n.endsWith("_axe") && !n.endsWith("_pickaxe"));
        if (axe.length) return { kind: "axe", min: _minToolTier(axe, "axe") || "wooden" };
        const hoe = names.filter((n) => n.endsWith("_hoe"));
        if (hoe.length) return { kind: "hoe", min: _minToolTier(hoe, "hoe") || "wooden" };
        if (b === "leaves" || b.endsWith("_leaves") || (blk && Array.isArray(blk.drops) && blk.drops.length === 0 && String(blk.material || "").indexOf("leaves") >= 0)) {
            return { kind: "shears", min: "shears" };
        }
    }
    if (spec.minTool) return { kind: spec.toolKind, min: spec.minTool };
    return null;
}

function _gatherSpec(name) {
    const n = String(name || "").toLowerCase();
    const down = [new Vec3(0, -1, 0), new Vec3(1, -1, 0), new Vec3(-1, -1, 0)];
    const horiz = [new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1)];
    const woods = ["oak", "spruce", "birch", "jungle", "acacia", "dark_oak", "mangrove"];
    const oreDrop = {
        coal_ore: "coal", deepslate_coal_ore: "coal",
        iron_ore: "raw_iron", deepslate_iron_ore: "raw_iron",
        copper_ore: "raw_copper", deepslate_copper_ore: "raw_copper",
        gold_ore: "raw_gold", deepslate_gold_ore: "raw_gold",
        diamond_ore: "diamond", deepslate_diamond_ore: "diamond",
        emerald_ore: "emerald", deepslate_emerald_ore: "emerald",
        lapis_ore: "lapis_lazuli", lapis_lazuli_ore: "lapis_lazuli",
        deepslate_lapis_ore: "lapis_lazuli", deepslate_lapis_lazuli_ore: "lapis_lazuli",
        redstone_ore: "redstone", deepslate_redstone_ore: "redstone",
        nether_gold_ore: "gold_nugget", nether_quartz_ore: "quartz",
    };
    const oreTier = {
        coal_ore: "wooden", deepslate_coal_ore: "wooden",
        iron_ore: "stone", deepslate_iron_ore: "stone",
        copper_ore: "stone", deepslate_copper_ore: "stone",
        gold_ore: "iron", deepslate_gold_ore: "iron",
        diamond_ore: "iron", deepslate_diamond_ore: "iron",
        emerald_ore: "iron", deepslate_emerald_ore: "iron",
        lapis_ore: "stone", lapis_lazuli_ore: "stone",
        deepslate_lapis_ore: "stone", deepslate_lapis_lazuli_ore: "stone",
        redstone_ore: "iron", deepslate_redstone_ore: "iron",
        nether_gold_ore: "wooden", nether_quartz_ore: "wooden",
    };
    if (n === "log" || n === "wood" || n === "wood_log" || n === "logs") {
        const blocks = woods.flatMap((w) => [`${w}_log`, `stripped_${w}_log`]);
        const drops = woods.flatMap((w) => [`${w}_log`, `stripped_${w}_log`]);
        return { blocks, drops, toolKind: "axe", minTool: null, dirs: horiz };
    }
    if (n.endsWith("_log") || n.endsWith("_stem")) {
        const base = n.replace(/^stripped_/, "");
        const stripped = n.startsWith("stripped_") ? n : (n.endsWith("_stem") ? `stripped_${n}` : `stripped_${n}`);
        return { blocks: [base, stripped], drops: [base, stripped], toolKind: "axe", minTool: null, dirs: horiz };
    }
    if (n === "leaves" || n.endsWith("_leaves")) {
        const blocks = n === "leaves" ? woods.map((w) => `${w}_leaves`) : [n];
        return { blocks, drops: blocks.slice(), toolKind: "shears", minTool: "shears", dirs: horiz };
    }
    if (n === "cobblestone" || n === "stone") {
        return { blocks: ["stone", "cobblestone"], drops: ["cobblestone"], toolKind: "pickaxe", minTool: "wooden", dirs: down };
    }
    if (n === "dirt" || n === "grass_block") {
        return { blocks: ["dirt", "grass_block"], drops: ["dirt"], toolKind: "shovel", minTool: null, dirs: horiz };
    }
    if (n === "sand" || n === "gravel" || n === "red_sand" || n === "clay") {
        return { blocks: [n], drops: [n === "clay" ? "clay_ball" : n], toolKind: "shovel", minTool: null, dirs: horiz };
    }
    const dropName = {
        coal: ["coal_ore", "deepslate_coal_ore"],
        raw_iron: ["iron_ore", "deepslate_iron_ore"],
        raw_copper: ["copper_ore", "deepslate_copper_ore"],
        raw_gold: ["gold_ore", "deepslate_gold_ore"],
        diamond: ["diamond_ore", "deepslate_diamond_ore"],
        emerald: ["emerald_ore", "deepslate_emerald_ore"],
        lapis_lazuli: ["lapis_ore", "lapis_lazuli_ore", "deepslate_lapis_ore"],
        redstone: ["redstone_ore", "deepslate_redstone_ore"],
    };
    if (dropName[n]) {
        const blocks = dropName[n];
        return { blocks, drops: [n], toolKind: "pickaxe", minTool: oreTier[blocks[0]] || "stone", dirs: down };
    }
    if (oreDrop[n]) {
        const pair = n.startsWith("deepslate_") ? [n, n.replace("deepslate_", "")] : [n, `deepslate_${n}`];
        return { blocks: pair, drops: [oreDrop[n]], toolKind: "pickaxe", minTool: oreTier[n] || "stone", dirs: down };
    }
    if (mcData.blocksByName[n]) {
        const blk = mcData.blocksByName[n];
        const itemName = (mcData.itemsByName[n] && n) || n;
        let toolKind = "shovel";
        let minTool = null;
        const names = _harvestToolNames(blk);
        if (names.some((t) => t.endsWith("_pickaxe"))) {
            toolKind = "pickaxe";
            minTool = _minToolTier(names, "pickaxe") || "wooden";
        } else if (names.indexOf("shears") >= 0) {
            toolKind = "shears";
            minTool = "shears";
        } else if (names.some((t) => t.endsWith("_axe") && !t.endsWith("_pickaxe"))) {
            toolKind = "axe";
            minTool = _minToolTier(names, "axe");
        } else if (names.some((t) => t.endsWith("_shovel"))) {
            toolKind = "shovel";
            minTool = _minToolTier(names, "shovel");
        } else if (n.endsWith("_leaves") || n === "leaves" || (Array.isArray(blk.drops) && blk.drops.length === 0)) {
            toolKind = "shears";
            minTool = "shears";
        } else if (n.endsWith("_log") || n.endsWith("_stem")) {
            toolKind = "axe";
            minTool = null;
        }
        return { blocks: [n], drops: [itemName], toolKind, minTool, dirs: toolKind === "pickaxe" ? down : horiz };
    }
    return null;
}
