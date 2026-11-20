// Craft 1 shield using a nearby table if there is one: craft(bot, "shield", 1);
// Alias: craftItemSafe(bot, item, n). Already holding enough → no-op success.
async function craft(bot, item, n = 1) {
    const report = (ok, reason) => {
        bot.chat(`[contract] craft ${ok ? "ok" : "fail"} ${reason}`);
        try {
            globalThis.__skillOutcomes = globalThis.__skillOutcomes || [];
            globalThis.__skillOutcomes.push({ name: "craft", ok: !!ok, reason: String(reason), source: "contract" });
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
    if (countOf(item) >= n) return report(true, "already_satisfied");

    let table = bot.findBlock({ matching: mcData.blocksByName.crafting_table.id, maxDistance: 32 });
    if (table && bot.entity && bot.entity.position && typeof bot.entity.position.distanceTo === "function"
        && table.position && bot.entity.position.distanceTo(table.position) > 6 && countOf("crafting_table") >= 1) {
        const placed = await _craftPlaceNearby(bot, "crafting_table");
        if (placed) table = placed;
    }
    if (!table && countOf("crafting_table") >= 1) {
        const placed = await _craftPlaceNearby(bot, "crafting_table");
        if (placed) table = placed;
    }
    const needItems = n - countOf(item);
    const recipe = bot.recipesFor(mcData.itemsByName[item].id, null, 1, table)[0]
        || bot.recipesFor(mcData.itemsByName[item].id, null, 1, null)[0];
    const perCraft = Math.max(1, (recipe && recipe.result && recipe.result.count) || 1);
    const times = Math.ceil(needItems / perCraft);
    try {
        await craftItem(bot, item, times);
    } catch (err) {
        if (countOf(item) >= n) return report(true, "done");
        return report(false, "craft_failed");
    }
    if (countOf(item) >= n) return report(true, "done");
    if (!recipe) return report(false, "missing_materials");
    return report(false, "craft_failed");
}

async function craftItemSafe(bot, item, n = 1) {
    return craft(bot, item, n);
}

async function _craftPlaceNearby(bot, name) {
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
            // next spot
        }
    }
    return null;
}
