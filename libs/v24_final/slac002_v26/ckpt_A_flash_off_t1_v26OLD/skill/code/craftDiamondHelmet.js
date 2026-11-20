async function craftDiamondHelmet(bot) {
  const countItem = name => bot.inventory.items().filter(i => i.name === name).reduce((s, i) => s + i.count, 0);

  // 1) Ensure we have at least 5 diamonds
  const diamondCount = countItem("diamond");
  if (diamondCount < 5) {
    bot.chat(`Not enough diamonds (have ${diamondCount}, need 5). Cannot craft diamond helmet.`);
    return;
  }
  bot.chat(`Have ${diamondCount} diamonds.`);

  // 2) Ensure a crafting table block is nearby; place one if needed
  let tableBlock = bot.findBlock({
    matching: mcData.blocksByName.crafting_table.id,
    maxDistance: 32
  });
  if (!tableBlock) {
    const tableItem = bot.inventory.findInventoryItem(mcData.itemsByName["crafting_table"].id);
    if (!tableItem) {
      bot.chat("No crafting table item available. Cannot craft diamond helmet.");
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

  // 3) Craft the diamond helmet
  bot.chat("Crafting diamond helmet...");
  await craftItem(bot, "diamond_helmet", 1);

  // 4) Verify
  const helmetCount = countItem("diamond_helmet");
  bot.chat(`Done. Total diamond helmets: ${helmetCount}.`);
}