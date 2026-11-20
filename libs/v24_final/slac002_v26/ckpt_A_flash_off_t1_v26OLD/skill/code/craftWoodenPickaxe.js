async function craftWoodenPickaxe(bot) {
  const plankNames = ["oak_planks", "birch_planks", "spruce_planks", "jungle_planks", "acacia_planks", "dark_oak_planks", "mangrove_planks", "cherry_planks", "bamboo_planks", "crimson_planks", "warped_planks"];
  const logNames = ["oak_log", "birch_log", "spruce_log", "jungle_log", "acacia_log", "dark_oak_log", "mangrove_log", "cherry_log"];

  // 1) Ensure we have a crafting_table item in inventory
  let tableItem = bot.inventory.findInventoryItem(mcData.itemsByName["crafting_table"].id);
  if (!tableItem) {
    bot.chat("No crafting table item, crafting one...");
    let plankCount = bot.inventory.items().filter(i => plankNames.includes(i.name)).reduce((s, i) => s + i.count, 0);
    if (plankCount < 4) {
      let logItem = bot.inventory.items().find(i => logNames.includes(i.name));
      if (!logItem) {
        bot.chat("No logs, mining one...");
        await mineOneLog(bot);
        logItem = bot.inventory.items().find(i => logNames.includes(i.name));
      }
      if (!logItem) {
        bot.chat("Failed to obtain a log.");
        return;
      }
      const plankName = logItem.name.replace("_log", "_planks");
      await craftItem(bot, plankName, 1);
    }
    await craftItem(bot, "crafting_table", 1);
    tableItem = bot.inventory.findInventoryItem(mcData.itemsByName["crafting_table"].id);
  }

  // 2) Ensure a crafting_table block exists nearby; if not, place it
  let tableBlock = bot.findBlock({
    matching: mcData.blocksByName.crafting_table.id,
    maxDistance: 32
  });
  if (!tableBlock) {
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

  // 3) Ensure we have 3 planks and 2 sticks
  let plankCount = bot.inventory.items().filter(i => plankNames.includes(i.name)).reduce((s, i) => s + i.count, 0);
  if (plankCount < 3) {
    let logItem = bot.inventory.items().find(i => logNames.includes(i.name));
    if (!logItem) {
      bot.chat("Not enough planks, mining a log...");
      await mineOneLog(bot);
      logItem = bot.inventory.items().find(i => logNames.includes(i.name));
    }
    if (!logItem) {
      bot.chat("Failed to obtain a log.");
      return;
    }
    const plankName = logItem.name.replace("_log", "_planks");
    await craftItem(bot, plankName, 1);
  }
  let stickCount = bot.inventory.items().filter(i => i.name === "stick").reduce((s, i) => s + i.count, 0);
  if (stickCount < 2) {
    bot.chat("Crafting sticks...");
    await craftItem(bot, "stick", 1);
  }

  // 4) Craft the wooden pickaxe
  bot.chat("Crafting wooden pickaxe...");
  await craftItem(bot, "wooden_pickaxe", 1);
  bot.chat("Crafted 1 wooden pickaxe.");
}