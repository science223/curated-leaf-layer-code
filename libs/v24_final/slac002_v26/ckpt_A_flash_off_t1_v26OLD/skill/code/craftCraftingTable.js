async function craftCraftingTable(bot) {
  // Check if we already have a crafting table
  const existing = bot.inventory.findInventoryItem(mcData.itemsByName["crafting_table"].id);
  if (existing) {
    bot.chat("Already have a crafting table.");
    return;
  }

  // Ensure we have at least 4 jungle_planks
  let planks = bot.inventory.items().filter(i => i.name === "jungle_planks").reduce((s, i) => s + i.count, 0);
  if (planks < 4) {
    const needed = 4 - planks;
    const logsNeeded = Math.ceil(needed / 4);
    const logs = bot.inventory.items().filter(i => i.name === "jungle_log").reduce((s, i) => s + i.count, 0);
    if (logs < logsNeeded) {
      bot.chat("Not enough jungle logs, mining more...");
      await mineBlock(bot, "jungle_log", logsNeeded - logs);
    }
    bot.chat("Crafting jungle planks...");
    await craftItem(bot, "jungle_planks", logsNeeded);
  }
  bot.chat("Crafting crafting table...");
  await craftItem(bot, "crafting_table", 1);
  bot.chat("Crafted 1 crafting table.");
}