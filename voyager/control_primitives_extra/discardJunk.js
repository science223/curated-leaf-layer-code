// Free inventory space: discardJunk(bot);
// Stores obvious junk (terrain blocks, flowers, rotten flesh, lower-tier duplicate tools, cobblestone beyond one
// stack) in a chest it places next to you, crafting the chest from 8 planks if you have none.
// Keeps tools (best of each kind), food, ores/ingots, wood, crafting materials and one stack of cobblestone.
async function discardJunk(bot, targetUsed = 28) {
    const report = (ok, reason) => {
        bot.chat(`[contract] discardJunk ${ok ? "ok" : "fail"} ${reason}`);
        try {
            globalThis.__skillOutcomes = globalThis.__skillOutcomes || [];
            globalThis.__skillOutcomes.push({ name: "discardJunk", ok: !!ok, reason: String(reason), source: "contract" });
        } catch (err) {}
        return { ok: !!ok, reason: String(reason) };
    };
    const used = () => bot.inventoryUsed();
    if (used() <= targetUsed) {
        bot.chat(`Inventory ${used()}/36, nothing to discard.`);
        return report(true, "already_satisfied");
    }
    const JUNK = ["dirt", "coarse_dirt", "rooted_dirt", "gravel", "sand", "red_sand", "andesite", "diorite",
        "granite", "tuff", "calcite", "deepslate", "cobbled_deepslate", "netherrack", "dripstone_block",
        "pointed_dripstone", "stone", "sandstone", "red_sandstone", "mud", "moss_block", "moss_carpet",
        "dandelion", "poppy", "blue_orchid", "allium", "azure_bluet", "red_tulip", "orange_tulip",
        "white_tulip", "pink_tulip", "oxeye_daisy", "cornflower", "lily_of_the_valley", "sunflower",
        "lilac", "rose_bush", "peony", "grass", "tall_grass", "fern", "large_fern", "dead_bush",
        "vine", "glow_lichen", "hanging_roots", "small_dripleaf", "big_dripleaf", "spore_blossom",
        "rotten_flesh", "poisonous_potato"];
    const TIER = { wooden: 0, golden: 1, stone: 1, iron: 2, diamond: 3, netherite: 4 };
    const KINDS = ["pickaxe", "axe", "shovel", "sword", "hoe"];
    const total = (name) => bot.inventory.items().filter((i) => i.name === name).reduce((s, i) => s + i.count, 0);

    // Plan: [itemTypeId, count, name]
    const plan = [];
    for (const name of new Set(bot.inventory.items().map((i) => i.name))) {
        if (JUNK.includes(name) || name === "terracotta" || name.endsWith("_terracotta")) {
            plan.push([mcData.itemsByName[name].id, total(name), name]);
        }
    }
    if (total("cobblestone") > 64) {
        plan.push([mcData.itemsByName["cobblestone"].id, total("cobblestone") - 64, "cobblestone"]);
    }
    for (const kind of KINDS) {
        const tools = bot.inventory.items().filter((i) => {
            const parts = i.name.split("_");
            return parts.length === 2 && parts[1] === kind && parts[0] in TIER;
        });
        if (tools.length <= 1) continue;
        tools.sort((a, b) => TIER[b.name.split("_")[0]] - TIER[a.name.split("_")[0]]);
        for (const t of tools.slice(1)) plan.push([t.type, 1, t.name]);
    }
    if (plan.length === 0) {
        bot.chat(`Inventory ${used()}/36 but nothing is safe to discard.`);
        return report(used() <= targetUsed, used() <= targetUsed ? "done" : "inventory_full");
    }

    let moved = 0;
    const findSpot = () => {
        const base = bot.entity.position.floored();
        const offsets = [];
        for (let dx = -2; dx <= 2; dx++)
            for (let dy = -1; dy <= 1; dy++)
                for (let dz = -2; dz <= 2; dz++)
                    if (dx !== 0 || dz !== 0) offsets.push(new Vec3(dx, dy, dz));
        offsets.sort((a, b) => Math.abs(a.x) + Math.abs(a.y) + Math.abs(a.z) - (Math.abs(b.x) + Math.abs(b.y) + Math.abs(b.z)));
        const spots = [];
        for (const off of offsets) {
            const pos = base.offset(off.x, off.y, off.z);
            const below = bot.blockAt(pos.offset(0, -1, 0));
            const here = bot.blockAt(pos);
            const above = bot.blockAt(pos.offset(0, 1, 0));
            if (!below || ["air", "water", "lava"].includes(below.name)) continue;
            if (!here || here.name !== "air" || !above || above.name !== "air") continue;
            spots.push(pos);
        }
        return spots;
    };
    // Make sure we hold a chest: craft one from 8 planks at a crafting table if needed.
    const ensureChest = async () => {
        if (total("chest") >= 1) return true;
        const planks = () => bot.inventory.items().filter((i) => i.name.endsWith("_planks")).reduce((s, i) => s + i.count, 0);
        if (planks() < 8) {
            const log = bot.inventory.items().find((i) => i.name.endsWith("_log"));
            if (log) {
                try {
                    await craftItem(bot, log.name.replace("_log", "_planks"), 2);
                } catch (err) {
                    // no planks recipe for this log
                }
            }
        }
        if (planks() < 8) return false;
        let table = bot.findBlock({ matching: mcData.blocksByName["crafting_table"].id, maxDistance: 32 });
        if (!table && total("crafting_table") >= 1) {
            for (const pos of findSpot()) {
                try {
                    await placeItem(bot, "crafting_table", pos);
                    table = bot.findBlock({ matching: mcData.blocksByName["crafting_table"].id, maxDistance: 32 });
                    if (table) break;
                } catch (err) {
                    // next spot
                }
            }
        }
        if (!table) return false;
        try {
            await craftItem(bot, "chest", 1);
        } catch (err) {
            return false;
        }
        return total("chest") >= 1;
    };
    // 1) Place our own chest next to us and deposit into it (no chest memory needed).
    const placeChest = async () => {
        if (!(await ensureChest())) return null;
        for (const pos of findSpot()) {
            try {
                await placeItem(bot, "chest", pos);
                const blk = bot.blockAt(pos);
                if (blk && blk.name === "chest") return blk;
            } catch (err) {
                // try the next spot
            }
        }
        return null;
    };
    let remaining = plan.slice();
    for (let attempt = 0; attempt < 2 && remaining.length > 0; attempt++) {
        const chestBlock = await placeChest();
        if (!chestBlock) {
            if (total("chest") < 1) return report(false, "missing_chest");
            break;
        }
        let container;
        try {
            container = await bot.openContainer(chestBlock);
        } catch (err) {
            break;
        }
        const left = [];
        for (const [type, count, name] of remaining) {
            const n = Math.min(count, total(name));
            if (n <= 0) continue;
            try {
                await container.deposit(type, null, n);
                moved += n;
            } catch (err) {
                left.push([type, count, name]); // chest full
            }
        }
        container.close();
        remaining = left.filter((p) => total(p[2]) > 0);
    }

    if (remaining.length > 0) {
        bot.chat("Could not store all junk: need a chest, or 8 planks and a crafting table.");
    }
    bot.chat(`Stored or discarded ${moved} junk items, inventory now ${used()}/36.`);
    if (used() <= targetUsed) return report(true, "done");
    return report(false, "inventory_full");
}

async function storeJunk(bot, targetUsed = 28) {
    return discardJunk(bot, targetUsed);
}
