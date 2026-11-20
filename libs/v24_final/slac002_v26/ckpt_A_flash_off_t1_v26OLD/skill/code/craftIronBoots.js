async function craftIronBoots(bot) {
  // 1) Ensure we have 4 iron ingots
  let ironCount = bot.inventory.items().filter(i => i.name === "iron_ingot").reduce((s, i) => s + i.count, 0);
  if (ironCount < 4) {
    bot.chat(`Not enough iron ingots (have ${ironCount}, need 4).`);
    return;
  }

  // 2) Ensure a crafting table block is nearby; place one if needed
  let tableBlock = bot.findBlock({
    matching: mcData.blocksByName.crafting_table.id,
    maxDistance: 32
  });
  if (!tableBlock) {
    const tableItem = bot.inventory.findInventoryItem(mcData.itemsByName["crafting_table"].id);
    if (!tableItem) {
      bot.chat("No crafting table item available.");
      return;
    }
    bot.chat("Placing crafting table...");
    const pos = bot.entity.position.offset(1, 0, 0);
    await placeItem(bot, "crafting_table", pos);
    tableBlock = bot.findBlock({
      matching: mcData.blocksByName.crafting_table.id,
      maxDistance: 32
    });
    if (!tableBlock) {
      bot.chat("Failed to place crafting table.");
      return;
    }
  }

  // 3) Craft the iron boots
  bot.chat("Crafting iron boots...");
  await craftItem(bot, "iron_boots", 1);
  const bootsCount = bot.inventory.items().filter(i => i.name === "iron_boots").reduce((s, i) => s + i.count, 0);
  bot.chat(`Done. Total iron boots: ${bootsCount}.`);
}