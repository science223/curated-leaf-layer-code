// Smelt 8 more raw_iron: smelt(bot, "raw_iron", 8);
// n is how many MORE to smelt (increment), same meaning as smeltItem count.
// Crafts/places a furnace and picks fuel (coal, charcoal, then planks/logs) if missing.
// Fewer inputs than n → smelt what it can and return partial.
async function smelt(bot, item, n = 1) {
    const report = (ok, reason) => {
        bot.chat(`[contract] smelt ${ok ? "ok" : "fail"} ${reason}`);
        try {
            globalThis.__skillOutcomes = globalThis.__skillOutcomes || [];
            globalThis.__skillOutcomes.push({ name: "smelt", ok: !!ok, reason: String(reason), source: "contract" });
        } catch (err) {}
        return { ok: !!ok, reason: String(reason) };
    };
    if (typeof item !== "string") return report(false, "unknown_item");
    if (typeof n !== "number") n = 1;
    const countOf = (name) => {
        const def = mcData.itemsByName[name];
        if (!def) return 0;
        return bot.inventory.items().filter((i) => i.type === def.id).reduce((s, i) => s + i.count, 0);
    };
    if (!mcData.itemsByName[item]) return report(false, "unknown_item");
    const output = _smeltOutput(item);
    if (!output) return report(false, "unknown_recipe");
    const startOut = countOf(output);
    const want = Math.max(0, Math.floor(Number(n) || 0));
    if (!(want > 0)) return report(true, "already_satisfied");
    const haveMat = countOf(item);
    if (haveMat < 1) return report(false, "missing_materials");
    let need = Math.min(want, haveMat);
    const short = haveMat < want;

    let furnace = bot.findBlock({ matching: mcData.blocksByName.furnace.id, maxDistance: 32 });
    if (furnace && bot.entity && bot.entity.position && typeof bot.entity.position.distanceTo === "function"
        && furnace.position && bot.entity.position.distanceTo(furnace.position) > 6 && countOf("furnace") >= 1) {
        const placed = await _smeltPlaceNearby(bot, "furnace");
        if (placed) furnace = placed;
    }
    if (!furnace) {
        if (countOf("furnace") < 1) {
            try {
                await craftItem(bot, "furnace", 1);
            } catch (err) {
                // need 8 cobblestone
            }
        }
        if (countOf("furnace") >= 1) {
            furnace = await _smeltPlaceNearby(bot, "furnace");
        }
    }
    if (!furnace) return report(false, "missing_furnace");

    const fuel = _smeltPickFuel(bot);
    if (!fuel) return report(false, "missing_fuel");
    const SMELT_BUDGET_MS = (typeof globalThis.__obtainDeadline === "number" && globalThis.__obtainDeadline > Date.now())
        ? Math.max(1000, globalThis.__obtainDeadline - Date.now())
        : 240000;
    // pilot v3: a direct smelt call also ends >= 60 s before the 600 s step limit (stamp absent -> unchanged)
    const _smeltStepLeft = (typeof globalThis.__stepStart === "number" && Date.now() - globalThis.__stepStart < 600000)
        ? globalThis.__stepStart + 540000 - Date.now() : Infinity;
    const SMELT_BUDGET_CAPPED_MS = Math.max(1000, Math.min(SMELT_BUDGET_MS, _smeltStepLeft));
    const maxByTime = Math.max(1, Math.floor((SMELT_BUDGET_CAPPED_MS - 30000) / 15000));
    let capped = false;
    if (need > maxByTime) {
        need = maxByTime;
        capped = true;
    }
    try {
        await smeltItem(bot, item, fuel, need);
    } catch (err) {
        const gained = countOf(output) - startOut;
        if (gained >= want) return report(true, "done");
        if (short || capped || gained > 0) return report(false, "partial");
        return report(false, "smelt_failed");
    }
    const gained = countOf(output) - startOut;
    if (gained >= want) return report(true, "done");
    if (short || capped || gained > 0) return report(false, "partial");
    return report(false, "smelt_failed");
}

function _smeltPickFuel(bot) {
    const order = ["coal", "charcoal", "oak_planks", "spruce_planks", "birch_planks",
        "jungle_planks", "acacia_planks", "dark_oak_planks", "mangrove_planks",
        "oak_log", "spruce_log", "birch_log", "jungle_log", "acacia_log", "dark_oak_log", "mangrove_log"];
    for (const name of order) {
        if (bot.inventory.items().some((i) => i.name === name)) return name;
    }
    const anyPlank = bot.inventory.items().find((i) => i.name.endsWith("_planks"));
    if (anyPlank) return anyPlank.name;
    const anyLog = bot.inventory.items().find((i) => i.name.endsWith("_log"));
    if (anyLog) return anyLog.name;
    const woodTool = bot.inventory.items().find((i) =>
        i.name === "wooden_axe" || i.name === "wooden_sword" || i.name === "wooden_shovel" || i.name === "wooden_hoe");
    if (woodTool) return woodTool.name;
    return null;
}

async function _smeltPlaceNearby(bot, name) {
    const base = bot.entity.position.floored();
    const offsets = [];
    for (let dx = -2; dx <= 2; dx++)
        for (let dy = -1; dy <= 1; dy++)
            for (let dz = -2; dz <= 2; dz++)
                if (dx || dz) offsets.push(new Vec3(dx, dy, dz));
    offsets.sort((a, b) => Math.abs(a.x) + Math.abs(a.y) + Math.abs(a.z) - (Math.abs(b.x) + Math.abs(b.y) + Math.abs(b.z)));
    for (const off of offsets) {
        const pos = base.offset(off.x, off.y, off.z);
        const below = bot.blockAt(pos.offset(0, -1, 0));
        const here = bot.blockAt(pos);
        if (!below || ["air", "water", "lava"].includes(below.name)) continue;
        if (!here || here.name !== "air") continue;
        try {
            await placeItem(bot, name, pos);
            const blk = bot.blockAt(pos);
            if (blk && blk.name === name) return blk;
        } catch (err) {
            // next
        }
    }
    return null;
}

const _SMELT_OUT = {
    raw_iron: "iron_ingot", raw_copper: "copper_ingot", raw_gold: "gold_ingot",
    cobblestone: "stone", sand: "glass", red_sand: "glass",
    porkchop: "cooked_porkchop", beef: "cooked_beef", chicken: "cooked_chicken",
    mutton: "cooked_mutton", cod: "cooked_cod", salmon: "cooked_salmon",
    potato: "baked_potato", cactus: "green_dye", clay_ball: "brick",
    ancient_debris: "netherite_scrap", netherrack: "nether_brick", kelp: "dried_kelp",
};

function _smeltOutput(item) {
    if (typeof item !== "string") return null;
    if (_SMELT_OUT[item]) return _SMELT_OUT[item];
    if (!mcData.itemsByName || !mcData.itemsByName[item]) return null;
    // 1.19 recipes.json is crafting-only; gate furnace products on itemsByName.
    if (/(_log|_wood|_stem)$/.test(item) || item === "bamboo") {
        return mcData.itemsByName.charcoal ? "charcoal" : null;
    }
    if (item.endsWith("_ore")) {
        const base = item.replace(/^deepslate_/, "").replace(/^nether_/, "").replace(/_ore$/, "");
        if (mcData.itemsByName[base + "_ingot"]) return base + "_ingot";
        if (base !== item && mcData.itemsByName[base]) return base;
    }
    if (item === "stone" && mcData.itemsByName.smooth_stone) return "smooth_stone";
    if (item === "clay" && mcData.itemsByName.terracotta) return "terracotta";
    if (item === "wet_sponge" && mcData.itemsByName.sponge) return "sponge";
    if (item === "sandstone" && mcData.itemsByName.smooth_sandstone) return "smooth_sandstone";
    if (item === "red_sandstone" && mcData.itemsByName.smooth_red_sandstone) return "smooth_red_sandstone";
    if (item === "quartz_block" && mcData.itemsByName.smooth_quartz) return "smooth_quartz";
    if (item === "chorus_fruit" && mcData.itemsByName.popped_chorus_fruit) return "popped_chorus_fruit";
    if (mcData.itemsByName["cooked_" + item]) return "cooked_" + item;
    return null;
}
