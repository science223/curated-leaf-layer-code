async function craftShield(bot) {
  const plankNames = ["oak_planks", "birch_planks", "spruce_planks", "jungle_planks", "acacia_planks", "dark_oak_planks", "mangrove_planks", "cherry_planks", "bamboo_planks", "crimson_planks", "warped_planks"];
  const logNames = ["oak_log", "birch_log", "spruce_log", "jungle_log", "acacia_log", "dark_oak_log", "mangrove_log", "cherry_log"];

  // 1) Ensure we have 1 iron ingot
  let ironCount = bot.inventory.items().filter(i => i.name === "iron_ingot").reduce((s, i) => s + i.count, 0);
  if (ironCount < 1) {
    bot.chat("No iron ingot available, cannot craft shield.");
    return;
  }

  // 2) Ensure we have 6 wooden planks
  let plankCount = bot.inventory.items().filter(i => plankNames.includes(i.name)).reduce((s, i) => s + i.count, 0);
  if (plankCount < 6) {
    bot.chat(`Need ${6 - plankCount} more planks, crafting from logs...`);
    let logItem = bot.inventory.items().find(i => logNames.includes(i.name));
    if (!logItem) {
      bot.chat("No logs, mining one...");
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
    const planksNeeded = 6 - plankCount;
    const craftsNeeded = Math.ceil(planksNeeded / 4);
    bot.chat(`Crafting ${plankName} x${craftsNeeded}...`);
    await craftItem(bot, plankName, craftsNeeded);
    plankCount = bot.inventory.items().filter(i => plankNames.includes(i.name)).reduce((s, i) => s + i.count, 0);
    if (plankCount < 6) {
      bot.chat(`Still not enough planks (have ${plankCount}).`);
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

  // 4) Craft the shield
  bot.chat("Crafting shield...");
  await craftItem(bot, "shield", 1);
  const shieldCount = bot.inventory.items().filter(i => i.name === "shield").reduce((s, i) => s + i.count, 0);
  bot.chat(`Done. Total shields: ${shieldCount}.`);
}