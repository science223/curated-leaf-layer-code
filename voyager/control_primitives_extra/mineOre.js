// Mine n MORE ore drops at the 1.19 generation height: mineOre(bot, "diamond", 3);
// n is how many MORE to obtain (increment ≥ n), same meaning as mineBlock count.
// Stair down (no fall), then deterministic strip: dig-pad-walk at fixed spacing.
// Probe neighbors for liquid before digging; never dig the block underfoot.
const MINEORE_KNOWLEDGE = JSON.parse(`{
  "source": "Minecraft Wiki page Ore and Java Edition 1.18; Caves & Cliffs height distribution is unchanged in 1.19. https://minecraft.wiki/w/Ore https://minecraft.wiki/w/Java_Edition_1.18",
  "stripSpacing": 3,
  "tunnelHeight": 2,
  "budgetSeconds": 240,
  "keepPlaceBlocks": 16,
  "minDurability": 16,
  "discard": ["cobblestone", "cobbled_deepslate", "deepslate", "tuff", "diorite", "andesite", "granite"],
  "heights": {
    "diamond": {"minY": -64, "maxY": 16, "peakY": -59, "minTool": "iron", "drops": "diamond"},
    "gold": {"minY": -64, "maxY": 32, "peakY": -16, "minTool": "iron", "drops": "raw_gold"},
    "redstone": {"minY": -64, "maxY": 15, "peakY": -59, "minTool": "iron", "drops": "redstone"},
    "lapis": {"minY": -32, "maxY": 32, "peakY": 0, "minTool": "stone", "drops": "lapis_lazuli"},
    "iron": {"minY": -64, "maxY": 72, "peakY": -24, "minTool": "stone", "drops": "raw_iron"}
  }
}`);

async function mineOre(bot, name, count = 1) {
    const report = (ok, reason) => {
        bot.chat(`[contract] mineOre ${ok ? "ok" : "fail"} ${reason}`);
        try {
            globalThis.__skillOutcomes = globalThis.__skillOutcomes || [];
            globalThis.__skillOutcomes.push({ name: "mineOre", ok: !!ok, reason: String(reason), source: "contract" });
        } catch (err) {}
        return { ok: !!ok, reason: String(reason) };
    };
    if (typeof name !== "string") return report(false, "unknown_item");
    if (typeof count !== "number") count = 1;
    const spec = _mineOreSpec(name);
    if (!spec) return report(false, "unknown_item");
    const countOf = (itemName) => {
        const def = mcData.itemsByName[itemName];
        if (!def) return 0;
        return bot.inventory.items().filter((i) => i.type === def.id).reduce((s, i) => s + i.count, 0);
    };
    const have = () => spec.drops.reduce((s, d) => s + countOf(d), 0);
    const startHave = have();
    const target = startHave + Math.max(0, Math.floor(Number(count) || 0));
    if (!(count > 0)) return report(true, "already_satisfied");

    const tool = _mineOreFindPick(bot, spec.minTool);
    if (!tool || _mineOreRemaining(tool) < MINEORE_KNOWLEDGE.minDurability) {
        return report(false, "missing_tool");
    }
    try {
        await _mineOreTimed(bot.equip(tool, "hand"), 4000, "equip_timeout");
    } catch (err) {
        return report(false, "missing_tool");
    }

    const deadline = Math.min(Date.now() + MINEORE_KNOWLEDGE.budgetSeconds * 1000, (typeof globalThis.__stepStart === "number" && Date.now() - globalThis.__stepStart < 600000) ? globalThis.__stepStart + 540000 : Infinity);
    const myGen = globalThis.__obtainGen;
    const myOp = globalThis.__obtainOpSeq || 0;
    const timedOut = () => Date.now() >= deadline
        || (typeof myGen === "number" && globalThis.__obtainGen !== myGen)
        || (!!globalThis.__obtainInCall && !!globalThis.__obtainAborted)
        // abandoned by obtain's race (it bumps the op sequence): stop instead of fighting the next action
        || (!!globalThis.__obtainInCall && (globalThis.__obtainOpSeq || 0) !== myOp);
    const key = spec.key;
    bot._mineOreState = bot._mineOreState || {};
    let st = bot._mineOreState[key];
    if (!st || typeof st.x !== "number") {
        const p = bot.entity.position.floored();
        st = { x: p.x, y: p.y, z: p.z, dx: 1, dz: 0, length: 0, strip: 0, atY: false };
        bot._mineOreState[key] = st;
    }
    if (st.atY) {
        const yNow = Math.floor(bot.entity.position.y);
        if (yNow > spec.peakY + 8 || yNow < spec.peakY - 4) st.atY = false;
    }

    const save = () => {
        const p = bot.entity.position.floored();
        st.x = p.x;
        st.y = p.y;
        st.z = p.z;
        bot._mineOreState[key] = st;
    };

    try {
        if (!st.atY) {
            const yNow = Math.floor(bot.entity.position.y);
            let reached = false;
            if (yNow > spec.peakY + 1) {
                reached = await _mineOreStairDown(bot, spec.peakY, timedOut, save);
            } else if (yNow < spec.peakY - 1) {
                reached = await _mineOreStairUp(bot, spec.peakY, timedOut, save);
            } else {
                reached = true;
            }
            if (reached) st.atY = true;
            save();
            if (have() >= target) return report(true, "done");
            if (timedOut()) return report(false, "timeout");
            if (!st.atY) return report(false, "timeout");
        }

    while (have() < target && !timedOut()) {
            if (!_mineOreToolOk(bot, spec.minTool)) return report(false, "missing_tool");
            await _mineOreTossJunk(bot);
            const nearby = _mineOreFindNearby(bot, spec);
            if (nearby) {
                await _mineOreVein(bot, nearby, spec, timedOut);
                save();
                continue;
            }
            const step = await _mineOreStripStep(bot, st, timedOut);
            save();
            if (step === "liquid") {
                const ndx = -st.dz;
                const ndz = st.dx;
                st.dx = ndx;
                st.dz = ndz;
                if (st.dx === 0 && st.dz === 0) st.dx = -1;
                st.length = 0;
                st.strip += 1;
            } else if (step === "blocked") {
                // next parallel strip: shift sideways by stripSpacing, reverse heading
                const sideX = -st.dz;
                const sideZ = st.dx;
                st.dx = -st.dx;
                st.dz = -st.dz;
                st.length = 0;
                st.strip += 1;
                const p = bot.entity.position.floored();
                await _mineOreTunnelToward(bot, p.offset(sideX * MINEORE_KNOWLEDGE.stripSpacing, 0, sideZ * MINEORE_KNOWLEDGE.stripSpacing), timedOut);
            } else {
                st.length += 1;
                if (st.length >= 64) {
                    const sideX = -st.dz;
                    const sideZ = st.dx;
                    st.dx = -st.dx;
                    st.dz = -st.dz;
                    st.length = 0;
                    st.strip += 1;
                    const p = bot.entity.position.floored();
                    await _mineOreTunnelToward(bot, p.offset(sideX * MINEORE_KNOWLEDGE.stripSpacing, 0, sideZ * MINEORE_KNOWLEDGE.stripSpacing), timedOut);
                }
            }
        }
        if (have() >= target) return report(true, "done");
        if (timedOut()) return report(false, "timeout");
        return report(false, "not_found");
    } catch (err) {
        save();
        if (have() >= target) return report(true, "done");
        if (timedOut()) return report(false, "timeout");
        return report(false, "fail");
    }
}

function _mineOreSpec(name) {
    const n = String(name || "").toLowerCase().replace(/^minecraft:/, "");
    const table = MINEORE_KNOWLEDGE.heights;
    const alias = {
        diamond: "diamond", diamond_ore: "diamond", deepslate_diamond_ore: "diamond",
        gold: "gold", gold_ore: "gold", raw_gold: "gold", deepslate_gold_ore: "gold", nether_gold_ore: "gold",
        redstone: "redstone", redstone_ore: "redstone", deepslate_redstone_ore: "redstone",
        lapis: "lapis", lapis_lazuli: "lapis", lapis_ore: "lapis", lapis_lazuli_ore: "lapis",
        deepslate_lapis_ore: "lapis", deepslate_lapis_lazuli_ore: "lapis",
        iron: "iron", iron_ore: "iron", raw_iron: "iron", deepslate_iron_ore: "iron",
    };
    const key = alias[n];
    if (!key || !table[key]) return null;
    const h = table[key];
    const blocks = {
        diamond: ["diamond_ore", "deepslate_diamond_ore"],
        gold: ["gold_ore", "deepslate_gold_ore"],
        redstone: ["redstone_ore", "deepslate_redstone_ore"],
        lapis: ["lapis_ore", "deepslate_lapis_ore"],
        iron: ["iron_ore", "deepslate_iron_ore"],
    }[key];
    return { key, blocks, drops: [h.drops], minTool: h.minTool, peakY: h.peakY, minY: h.minY, maxY: h.maxY };
}

function _mineOreFindPick(bot, minTier) {
    const TIER = { wooden: 0, golden: 1, stone: 1, iron: 2, diamond: 3, netherite: 4 };
    const minIdx = TIER[minTier] == null ? 2 : TIER[minTier];
    const tools = bot.inventory.items().filter((i) => {
        const parts = i.name.split("_");
        return parts.length === 2 && parts[1] === "pickaxe" && TIER[parts[0]] != null && TIER[parts[0]] >= minIdx;
    });
    tools.sort((a, b) => {
        const ta = TIER[a.name.split("_")[0]] - TIER[b.name.split("_")[0]];
        if (ta !== 0) return ta;
        return _mineOreRemaining(b) - _mineOreRemaining(a);
    });
    return tools[0] || null;
}

function _mineOreRemaining(item) {
    if (!item) return 0;
    const maxD = item.maxDurability || 0;
    const used = item.durabilityUsed || 0;
    if (!(maxD > 0)) return 999;
    return maxD - used;
}

function _mineOreToolOk(bot, minTier) {
    const held = bot.heldItem;
    const TIER = { wooden: 0, golden: 1, stone: 1, iron: 2, diamond: 3, netherite: 4 };
    const minIdx = TIER[minTier] == null ? 2 : TIER[minTier];
    if (held && held.name.endsWith("_pickaxe")) {
        const t = TIER[held.name.split("_")[0]];
        if (t != null && t >= minIdx && _mineOreRemaining(held) >= MINEORE_KNOWLEDGE.minDurability) return true;
    }
    const tool = _mineOreFindPick(bot, minTier);
    return !!(tool && _mineOreRemaining(tool) >= MINEORE_KNOWLEDGE.minDurability);
}

async function _mineOreTossJunk(bot) {
    const keep = MINEORE_KNOWLEDGE.keepPlaceBlocks;
    const names = MINEORE_KNOWLEDGE.discard;
    let placeCount = 0;
    for (const it of bot.inventory.items()) {
        if (it.name === "cobblestone" || it.name === "cobbled_deepslate") placeCount += it.count;
    }
    for (const it of bot.inventory.items().slice()) {
        if (names.indexOf(it.name) < 0) continue;
        if ((it.name === "cobblestone" || it.name === "cobbled_deepslate") && placeCount <= keep) continue;
        const extra = (it.name === "cobblestone" || it.name === "cobbled_deepslate")
            ? Math.max(0, placeCount - keep)
            : it.count;
        if (extra <= 0) continue;
        try {
            await _mineOreTimed(bot.toss(it.type, null, Math.min(it.count, extra)), 4000, "toss_timeout");
            if (it.name === "cobblestone" || it.name === "cobbled_deepslate") {
                placeCount -= Math.min(it.count, extra);
            }
        } catch (err) {}
    }
}

function _mineOreIsLiquid(block) {
    if (!block || !block.name) return false;
    return block.name === "lava" || block.name === "water" || block.name === "flowing_lava" || block.name === "flowing_water";
}

function _mineOreProbe(bot, pos) {
    const dirs = [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]];
    for (const d of dirs) {
        const n = bot.blockAt(pos.offset(d[0], d[1], d[2]));
        if (_mineOreIsLiquid(n)) return n;
    }
    return null;
}

function _mineOreIsFeet(bot, pos) {
    const feet = bot.entity.position.floored();
    return pos.x === feet.x && pos.z === feet.z && pos.y === feet.y - 1;
}

async function _mineOrePlaceAt(bot, pos) {
    const names = ["cobblestone", "cobbled_deepslate", "dirt", "netherrack", "stone"];
    let item = null;
    for (const n of names) {
        item = bot.inventory.items().find((i) => i.name === n);
        if (item) break;
    }
    if (!item) return false;
    try {
        await _mineOreTimed(bot.equip(item, "hand"), 4000, "equip_timeout");
    } catch (err) {
        return false;
    }
    const faces = [
        new Vec3(0, -1, 0), new Vec3(0, 1, 0),
        new Vec3(1, 0, 0), new Vec3(-1, 0, 0),
        new Vec3(0, 0, 1), new Vec3(0, 0, -1),
    ];
    for (const face of faces) {
        const refPos = pos.minus(face);
        const ref = bot.blockAt(refPos);
        if (!ref || ref.name === "air" || _mineOreIsLiquid(ref) || ref.boundingBox !== "block") continue;
        try {
            await _mineOreTimed(bot.placeBlock(ref, face), 3000, "place_timeout");
            return true;
        } catch (err) {}
    }
    return false;
}

async function _mineOreSeal(bot, liquid) {
    if (!liquid || !liquid.position) return;
    await _mineOrePlaceAt(bot, liquid.position);
}

async function _mineOreSafeDig(bot, pos, opts) {
    const seal = !opts || opts.seal !== false;
    const block = bot.blockAt(pos);
    if (!block || block.name === "air" || block.name === "cave_air" || block.name === "void_air") return "air";
    if (block.name === "bedrock" || block.name === "barrier") return "blocked";
    if (_mineOreIsLiquid(block)) {
        if (seal) await _mineOreSeal(bot, block);
        return "liquid";
    }
    if (_mineOreIsFeet(bot, pos)) return "feet";
    const liq = _mineOreProbe(bot, pos);
    if (liq) {
        if (seal) await _mineOreSeal(bot, liq);
        return "liquid";
    }
    const held = bot.heldItem;
    const n = String(block.name || "");
    const dirt = n === "dirt" || n === "grass_block" || n === "sand" || n === "gravel" || n === "clay";
    if (dirt) {
        const sh = bot.inventory.items().find((i) => i.name.endsWith("_shovel"));
        if (sh) {
            try { await _mineOreTimed(bot.equip(sh, "hand"), 4000, "equip_timeout"); } catch (err) {}
        }
    } else {
        const needIron = n.indexOf("diamond") >= 0 || n.indexOf("gold") >= 0 || n.indexOf("redstone") >= 0;
        const needStone = n.indexOf("iron_ore") >= 0 || n === "raw_iron";
        const min = needIron ? "iron" : (needStone ? "stone" : "wooden");
        const pick = _mineOreFindPick(bot, min);
        if (pick && (!held || held !== pick)) {
            try { await _mineOreTimed(bot.equip(pick, "hand"), 4000, "equip_timeout"); } catch (err) {}
        }
    }
    try {
        let timer;
        const digMs = (typeof _obtainRemainMs === "function")
            ? Math.max(1, Math.min(8000, _obtainRemainMs(8000)))
            : 8000;
        await Promise.race([
            bot.dig(block),
            new Promise((_, rej) => {
                timer = setTimeout(() => rej(new Error("dig_timeout")), digMs);
            }),
        ]).finally(() => { if (timer) clearTimeout(timer); });
        return "ok";
    } catch (err) {
        try { bot.stopDigging(); } catch (e) {}
        return "fail";
    }
}

async function _mineOreWalk(bot, pos, ms) {
    const limitMs = ms || 1500;
    try {
        if (typeof _obtainAborted === "function" && _obtainAborted()) return;
        bot.pathfinder.setGoal(null);
    } catch (err) {}
    try {
        if (typeof _obtainAborted === "function" && _obtainAborted()) return;
        bot.pathfinder.setGoal(new GoalBlock(pos.x, pos.y, pos.z));
    } catch (err) {}
    const goal = pos.offset(0.5, 0, 0.5);
    const limit = Date.now() + limitMs;
    const start = bot.entity.position.clone();
    try {
        await bot.lookAt(goal.offset(0, 0.5, 0), true);
        bot.setControlState("forward", true);
        bot.setControlState("sprint", true);
        bot.setControlState("sneak", false);
        while (Date.now() < limit) {
            const here = bot.entity.position;
            if (here.distanceTo(goal) < 0.9) break;
            await new Promise((r) => setTimeout(r, 40));
        }
        if (bot.entity.position.distanceTo(start) < 0.2 && bot.entity.position.distanceTo(goal) > 1.2) {
            try { bot.pathfinder.setGoal(null); } catch (e) {}
        }
    } catch (err) {
    } finally {
        try { bot.setControlState("forward", false); } catch (e) {}
        try { bot.setControlState("sprint", false); } catch (e) {}
        try { bot.pathfinder.setGoal(null); } catch (e) {}
    }
}

async function _mineOreStairDown(bot, targetY, timedOut, save) {
    let dx = 1;
    let dz = 0;
    let turns = 0;
    let steps = 0;
    let lastY = Math.floor(bot.entity.position.y);
    let stall = 0;
    while (Math.floor(bot.entity.position.y) > targetY + 0.2 && !timedOut()
            && !(typeof _obtainAborted === "function" && _obtainAborted())) {
        const p = bot.entity.position.floored();
        if (p.y <= targetY) return true;
        if (steps > 220 || stall >= 8 || turns >= 12) {
            try {
                const line = `[mineOre] no_progress y=${p.y} target=${targetY}`;
                try { console.log(line); } catch (e) {}
                if (typeof _obtainChat === "function") _obtainChat(bot, line);
            } catch (err) {}
            return Math.floor(bot.entity.position.y) <= targetY + 1;
        }
        if (steps % 16 === 0) {
            try {
                const line = `[mineOre] y=${p.y} target=${targetY}`;
                try { console.log(line); } catch (e) {}
                if (typeof _obtainChat === "function") _obtainChat(bot, line);
            } catch (err) {}
        }
        steps += 1;
        const front = p.offset(dx, 0, dz);
        const frontDown = p.offset(dx, -1, dz);
        const floorNext = p.offset(dx, -2, dz);
        // 2-high clearance then step down: dig front (head/body) and front-down (new feet air)
        const r1 = await _mineOreSafeDig(bot, front);
        if (r1 === "liquid") {
            const nx = -dz;
            const nz = dx;
            dx = nx;
            dz = nz;
            turns += 1;
            continue;
        }
        const r2 = await _mineOreSafeDig(bot, frontDown);
        if (r2 === "liquid") {
            const nx = -dz;
            const nz = dx;
            dx = nx;
            dz = nz;
            turns += 1;
            continue;
        }
        await _mineOreSafeDig(bot, front.offset(0, 1, 0));
        const floorBlk = bot.blockAt(floorNext);
        if (!floorBlk || floorBlk.name === "air" || _mineOreIsLiquid(floorBlk)) {
            if (_mineOreIsLiquid(floorBlk)) await _mineOreSeal(bot, floorBlk);
            else await _mineOrePlaceAt(bot, floorNext);
        }
        await _mineOreWalk(bot, frontDown, 1500);
        if (save) save();
        const yNow = Math.floor(bot.entity.position.y);
        if (yNow >= p.y) {
            const nx = -dz;
            const nz = dx;
            dx = nx;
            dz = nz;
            turns += 1;
            if (yNow >= lastY) stall += 1;
        } else {
            turns = 0;
            stall = 0;
            lastY = yNow;
        }
    }
    return Math.floor(bot.entity.position.y) <= targetY + 1;
}

async function _mineOreStairUp(bot, targetY, timedOut, save) {
    let dx = 1;
    let dz = 0;
    let turns = 0;
    let steps = 0;
    let lastY = Math.floor(bot.entity.position.y);
    let stall = 0;
    while (Math.floor(bot.entity.position.y) < targetY - 0.2 && !timedOut()
            && !(typeof _obtainAborted === "function" && _obtainAborted())) {
        const p = bot.entity.position.floored();
        if (p.y >= targetY) return true;
        if (steps > 80 || stall >= 6 || turns >= 8) {
            try {
                const line = `[mineOre] no_progress y=${p.y} target=${targetY}`;
                try { console.log(line); } catch (e) {}
                if (typeof _obtainChat === "function") _obtainChat(bot, line);
            } catch (err) {}
            return Math.floor(bot.entity.position.y) >= targetY - 1;
        }
        if (steps % 16 === 0) {
            try {
                const line = `[mineOre] y=${p.y} target=${targetY}`;
                try { console.log(line); } catch (e) {}
                if (typeof _obtainChat === "function") _obtainChat(bot, line);
            } catch (err) {}
        }
        steps += 1;
        const stepPos = p.offset(dx, 0, dz);
        const newFeet = p.offset(dx, 1, dz);
        const newHead = p.offset(dx, 2, dz);
        const r1 = await _mineOreSafeDig(bot, newFeet);
        if (r1 === "liquid") {
            const nx = -dz;
            const nz = dx;
            dx = nx;
            dz = nz;
            turns += 1;
            continue;
        }
        const r2 = await _mineOreSafeDig(bot, newHead);
        if (r2 === "liquid") {
            const nx = -dz;
            const nz = dx;
            dx = nx;
            dz = nz;
            turns += 1;
            continue;
        }
        const stepBlk = bot.blockAt(stepPos);
        if (!stepBlk || stepBlk.name === "air" || _mineOreIsLiquid(stepBlk)) {
            if (_mineOreIsLiquid(stepBlk)) await _mineOreSeal(bot, stepBlk);
            else await _mineOrePlaceAt(bot, stepPos);
        }
        await _mineOreWalk(bot, newFeet, 1500);
        if (save) save();
        const yNow = Math.floor(bot.entity.position.y);
        if (yNow <= p.y) {
            const nx = -dz;
            const nz = dx;
            dx = nx;
            dz = nz;
            turns += 1;
            if (yNow <= lastY) stall += 1;
        } else {
            turns = 0;
            stall = 0;
            lastY = yNow;
        }
    }
    return Math.floor(bot.entity.position.y) >= targetY - 1;
}

function _mineOreFindNearby(bot, spec) {
    try {
        return bot.findBlock({
            matching: (b) => b && spec.blocks.indexOf(b.name) >= 0,
            maxDistance: 16,
        });
    } catch (err) {
        return null;
    }
}

async function _mineOreVein(bot, start, spec, timedOut) {
    const seen = new Set();
    const queue = [start.position.clone()];
    const key = (p) => `${p.x},${p.y},${p.z}`;
    while (queue.length && !timedOut()) {
        const pos = queue.shift();
        const k = key(pos);
        if (seen.has(k)) continue;
        seen.add(k);
        const blk = bot.blockAt(pos);
        if (!blk || spec.blocks.indexOf(blk.name) < 0) continue;
        const here = bot.entity.position.floored();
        if (here.distanceTo(pos) > 4) {
            await _mineOreTunnelToward(bot, pos, timedOut);
        }
        const r = await _mineOreSafeDig(bot, pos);
        if (r === "liquid") continue;
        const dirs = [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]];
        for (const d of dirs) queue.push(pos.offset(d[0], d[1], d[2]));
        if (seen.size > 32) break;
    }
}

async function _mineOreStripStep(bot, st, timedOut, opts) {
    if (timedOut()) return "timeout";
    const p = bot.entity.position.floored();
    const dx = st.dx;
    const dz = st.dz;
    const feet = p.offset(dx, 0, dz);
    const head = p.offset(dx, 1, dz);
    const floor = p.offset(dx, -1, dz);
    const rHead = await _mineOreSafeDig(bot, head, opts);
    if (rHead === "liquid") return "liquid";
    const rFeet = await _mineOreSafeDig(bot, feet, opts);
    if (rFeet === "liquid") return "liquid";
    if (rHead === "blocked" && rFeet === "blocked") return "blocked";
    const floorBlk = bot.blockAt(floor);
    if (!floorBlk || floorBlk.name === "air" || _mineOreIsLiquid(floorBlk)) {
        if (_mineOreIsLiquid(floorBlk)) {
            if (!opts || opts.seal !== false) await _mineOreSeal(bot, floorBlk);
            return "liquid";
        }
        const placed = await _mineOrePlaceAt(bot, floor);
        if (!placed) return "blocked";
    }
    await _mineOreWalk(bot, feet, 1200);
    return "ok";
}

async function _mineOreTunnelToward(bot, dest, timedOut) {
    let guard = 0;
    while (!timedOut() && guard < 48) {
        guard += 1;
        const p = bot.entity.position.floored();
        const dx = Math.sign(dest.x - p.x);
        const dz = Math.sign(dest.z - p.z);
        if (dx === 0 && dz === 0) return true;
        const st = { dx: dx || 0, dz: dx ? 0 : dz };
        if (st.dx === 0 && st.dz === 0) return true;
        const r = await _mineOreStripStep(bot, st, timedOut);
        if (r === "liquid" || r === "blocked") return false;
        if (p.distanceTo(bot.entity.position.floored()) < 0.1) return false;
    }
    return false;
}

async function _mineOreTimed(promise, ms, label) {
    let timer;
    const want = Math.max(1, ms || 4000);
    const cap = (typeof _obtainRemainMs === "function")
        ? Math.max(1, Math.min(want, _obtainRemainMs(want)))
        : want;
    try {
        return await Promise.race([
            promise,
            new Promise((_, rej) => {
                timer = setTimeout(() => rej(new Error(label || "op_timeout")), cap);
            }),
        ]);
    } finally {
        if (timer) clearTimeout(timer);
    }
}

async function _mineOreWaitTicks(bot, n) {
    try {
        await _mineOreTimed(bot.waitForTicks(n), Math.max(2000, n * 80), "ticks_timeout");
    } catch (err) {}
}
