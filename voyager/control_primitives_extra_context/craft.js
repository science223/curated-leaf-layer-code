//! USE-RULE: - Use `craft(bot, item, n)` for craft tasks. n is the count to own, not recipe repeats. If a crafting table is already nearby, use it; do not place a second one.
// Craft 4 sticks from 2 planks: craft(bot, "stick", 4);
// Alias: craftItemSafe(bot, item, n). Already holding enough → returns immediately.
async function craft(bot, item, n = 1) {
    const have = (name) => bot.inventory.items().filter(i => i.name === name).reduce((s, i) => s + i.count, 0);
    if (have(item) >= n) return { ok: true, reason: "already_satisfied" };
    const table = bot.findBlock({ matching: mcData.blocksByName.crafting_table.id, maxDistance: 32 });
    if (!table && have("crafting_table") >= 1) {
        await placeItem(bot, "crafting_table", bot.entity.position.floored().offset(1, 0, 0));
    }
    const recipe = bot.recipesFor(mcData.itemsByName[item].id, null, 1, table)[0] || bot.recipesFor(mcData.itemsByName[item].id, null, 1, null)[0];
    const perCraft = Math.max(1, (recipe && recipe.result && recipe.result.count) || 1);
    await craftItem(bot, item, Math.ceil((n - have(item)) / perCraft));
    return { ok: have(item) >= n, reason: have(item) >= n ? "done" : "craft_failed" };
}
async function craftItemSafe(bot, item, n = 1) { return craft(bot, item, n); }
