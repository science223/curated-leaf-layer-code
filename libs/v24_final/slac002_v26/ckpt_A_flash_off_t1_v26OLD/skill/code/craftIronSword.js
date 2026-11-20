async function craftIronSword(bot) {
  const plankNames = ["oak_planks", "birch_planks", "spruce_planks", "jungle_planks", "acacia_planks", "dark_oak_planks", "mangrove_planks", "cherry_planks", "bamboo_planks", "crimson_planks", "warped_planks"];
  const logNames = ["oak_log", "birch_log", "spruce_log", "jungle_log", "acacia_log", "dark_oak_log", "mangrove_log", "cherry_log"];

  // 1) Ensure we have 2 iron ingots
  let ironCount = bot.inventory.items().filter(i => i.name === "iron_ingot").reduce((s, i) => s + i.count, 0);
  if (ironCount < 2) {
    bot.chat(`Not enough iron ingots (have ${ironCount}, need 2).`);
    return;
  }

  // 2) Ensure we have 1 stick
  let stickCount = bot.inventory.items().filter(i => i.name === "stick").reduce((s, i) => s + i.count, 0);
  if (stickCount < 1) {
    bot.chat("No sticks, crafting some...");
    // Need 2 planks to craft sticks
    let plankCount = bot.inventory.items().filter(i => plankNames.includes(i.name)).reduce((s, i) => s + i.count, 0);
    if (plankCount < 2) {
      // Need a log to craft planks
      let logItem = bot.inventory.items().find(i => logNames.includes(i.name));
      if (!logItem) {
        bot.chat("No logs, searching for a tree...");
        let logBlock = bot.findBlock({
          matching: b => logNames.includes(b.name),
          maxDistance: 32
        });
        if (!logBlock) {
          const directions = [new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1), new Vec3(1, 0, 1), new Vec3(-1, 0, -1)];
          const dir = directions[Math.floor(Math.random() * directions.length)];
          logBlock = await exploreUntil(bot, dir, 60, () => bot.findBlock({
            matching: b => logNames.includes(b.name),
            maxDistance: 32
          }));
        }
        if (!logBlock) {
          bot.chat("Failed to find a log.");
          return;
        }
        await mineBlock(bot, logBlock.name, 1);
        logItem = bot.inventory.items().find(i => logNames.includes(i.name));
      }
      if (!logItem) {
        bot.chat("Failed to obtain a log.");
        return;
      }
      const plankName = logItem.name.replace("_log", "_planks");
      bot.chat(`Crafting ${plankName}...`);
      await craftItem(bot, plankName, 1);
      plankCount = bot.inventory.items().filter(i => plankNames.includes(i.name)).reduce((s, i) => s + i.count, 0);
      if (plankCount < 2) {
        bot.chat("Failed to obtain enough planks.");
        return;
      }
    }
    bot.chat("Crafting sticks...");
    await craftItem(bot, "stick", 1);
    stickCount = bot.inventory.items().filter(i => i.name === "stick").reduce((s, i) => s + i.count, 0);
    if (stickCount < 1) {
      bot.chat("Failed to obtain sticks.");
      return;
    }
  }

  // 3) Ensure a crafting table block is nearby
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

  // 4) Craft the iron sword
  bot.chat("Crafting iron sword...");
  await craftItem(bot, "iron_sword", 1);
  const swordCount = bot.inventory.items().filter(i => i.name === "iron_sword").reduce((s, i) => s + i.count, 0);
  bot.chat(`Done. Total iron swords: ${swordCount}.`);
}