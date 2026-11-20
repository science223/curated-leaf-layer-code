async function craftIronHelmet(bot) {
  // 1) Ensure we have 5 iron ingots
  const ironCount = bot.inventory.items().filter(i => i.name === "iron_ingot").reduce((s, i) => s + i.count, 0);
  if (ironCount < 5) {
    bot.chat(`Not enough iron ingots (have ${ironCount}, need 5).`);
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

  // 3) Craft the iron helmet
  bot.chat("Crafting iron helmet...");
  await craftItem(bot, "iron_helmet", 1);
  const helmetCount = bot.inventory.items().filter(i => i.name === "iron_helmet").reduce((s, i) => s + i.count, 0);
  bot.chat(`Done. Total iron helmets: ${helmetCount}.`);
}