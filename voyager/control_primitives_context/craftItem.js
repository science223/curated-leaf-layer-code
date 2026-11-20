// Craft 8 oak_planks from 2 oak_log (do the recipe 2 times): craftItem(bot, "oak_planks", 2);
// A crafting table is only needed for 3x3 recipes. Without one nearby this
// function still crafts 2x2 recipes from your inventory (planks, sticks,
// the crafting table itself), so you can call it before you own any table.
async function craftItem(bot, name, count = 1) {
    const item = mcData.itemsByName[name];
    const craftingTable = bot.findBlock({
        matching: mcData.blocksByName.crafting_table.id,
        maxDistance: 32,
    });
    await bot.pathfinder.goto(
        new GoalLookAtBlock(craftingTable.position, bot.world)
    );
    const recipe = bot.recipesFor(item.id, null, 1, craftingTable)[0];
    await bot.craft(recipe, count, craftingTable);
}
