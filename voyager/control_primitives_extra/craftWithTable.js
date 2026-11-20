// Craft 1 iron_helmet (needs a crafting table): craftWithTable(bot, "iron_helmet", 1);
// Ensures a crafting table exists and is placed nearby, then crafts. Returns early if you already have enough.
async function craftWithTable(bot, itemName, count = 1) {
    const countOf = (name) => {
        const def = mcData.itemsByName[name];
        if (!def) return 0;
        return bot.inventory.items().filter((i) => i.type === def.id).reduce((s, i) => s + i.count, 0);
    };
    // Already done? Do nothing.
    if (countOf(itemName) >= count) {
        bot.chat(`Already have ${countOf(itemName)} ${itemName}.`);
        return;
    }
    // Make sure a crafting table item is in the inventory.
    if (countOf("crafting_table") < 1) {
        await craftItem(bot, "crafting_table", 1);
        // Crafting a table needs planks; if that failed, stop here instead of
        // looping over placement spots with nothing to place.
        if (countOf("crafting_table") < 1) {
            bot.chat("No crafting table and could not craft one (need 4 planks).");
            return;
        }
    }
    // Make sure a crafting table block is within reach; place one on solid ground if not.
    let table = bot.findBlock({ matching: mcData.blocksByName["crafting_table"].id, maxDistance: 32 });
    if (!table) {
        const base = bot.entity.position.floored();
        const offsets = [];
        for (let dx = -2; dx <= 2; dx++)
            for (let dy = -1; dy <= 1; dy++)
                for (let dz = -2; dz <= 2; dz++)
                    if (dx || dy || dz) offsets.push(new Vec3(dx, dy, dz));
        offsets.sort((a, b) => Math.abs(a.x) + Math.abs(a.y) + Math.abs(a.z) - (Math.abs(b.x) + Math.abs(b.y) + Math.abs(b.z)));
        for (const off of offsets) {
            const pos = base.offset(off.x, off.y, off.z);
            const below = bot.blockAt(pos.offset(0, -1, 0));
            const here = bot.blockAt(pos);
            if (!below || below.name === "air" || below.name === "water" || below.name === "lava") continue;
            if (!here || (here.name !== "air" && here.name !== "water")) continue;
            try {
                await placeItem(bot, "crafting_table", pos);
                table = bot.findBlock({ matching: mcData.blocksByName["crafting_table"].id, maxDistance: 32 });
                if (table) break;
            } catch (err) {
                // try the next spot
            }
        }
    }
    if (!table) {
        bot.chat("Could not place a crafting table; no valid spot nearby.");
        return;
    }
    await craftItem(bot, itemName, count - countOf(itemName));
}
