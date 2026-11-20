//! USE-RULE: - Use `craftWithTable(bot, name, count)` to craft anything that needs a crafting table. Do not hand-write crafting-table setup.
// Craft 1 iron_helmet using a crafting table: craftWithTable(bot, "iron_helmet", 1);
// Returns immediately if you already have enough. Crafts and places a crafting table when none is nearby.
async function craftWithTable(bot, itemName, count = 1) {
    const have = (n) => bot.inventory.items().filter(i => i.name === n).reduce((s, i) => s + i.count, 0);
    if (have(itemName) >= count) return;                       // already done
    if (have("crafting_table") < 1) {
        await craftItem(bot, "crafting_table", 1);              // needs 4 planks
        if (have("crafting_table") < 1) return;                 // cannot proceed
    }
    let table = bot.findBlock({ matching: mcData.blocksByName["crafting_table"].id, maxDistance: 32 });
    if (!table) {
        const p = bot.entity.position.floored().offset(1, 0, 0);
        await placeItem(bot, "crafting_table", p);
    }
    await craftItem(bot, itemName, count - have(itemName));
}
