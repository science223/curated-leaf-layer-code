async function craftFurnace(bot) {
  // 1) Ensure we have 8 cobblestone
  let cobbleCount = bot.inventory.items().filter(i => i.name === "cobblestone").reduce((s, i) => s + i.count, 0);
  if (cobbleCount < 8) {
    const needed = 8 - cobbleCount;
    bot.chat(`Need ${needed} more cobblestone, mining stone...`);
    let stoneBlock = bot.findBlock({
      matching: mcData.blocksByName["stone"].id,
      maxDistance: 32
    });
    if (!stoneBlock) {
      bot.chat("No stone nearby, exploring...");
      const directions = [new Vec3(0, -1, 0), new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1)];
      const dir = directions[Math.floor(Math.random() * directions.length)];
      stoneBlock = await exploreUntil(bot, dir, 60, () => bot.findBlock({
        matching: mcData.blocksByName["stone"].id,
        maxDistance: 32
      }));
    }
    if (!stoneBlock) {
      bot.chat("Failed to find stone.");
      return;
    }
    await mineBlock(bot, "stone", needed);
    cobbleCount = bot.inventory.items().filter(i => i.name === "cobblestone").reduce((s, i) => s + i.count, 0);
    if (cobbleCount < 8) {
      bot.chat(`Still not enough cobblestone (${cobbleCount}/8).`);
      return;
    }
  }

  // 2) Ensure a crafting table block is nearby
  let tableBlock = bot.findBlock({
    matching: mcData.blocksByName.crafting_table.id,
    maxDistance: 32
  });
  if (!tableBlock) {
    let tableItem = bot.inventory.findInventoryItem(mcData.itemsByName["crafting_table"].id);
    if (!tableItem) {
      bot.chat("No crafting table item, crafting one...");
      // Need 4 planks
      const plankNames = ["oak_planks", "birch_planks", "spruce_planks", "jungle_planks", "acacia_planks", "dark_oak_planks", "mangrove_planks", "cherry_planks", "bamboo_planks", "crimson_planks", "warped_planks"];
      const logNames = ["oak_log", "birch_log", "spruce_log", "jungle_log", "acacia_log", "dark_oak_log", "mangrove_log", "cherry_log"];
      let plankCount = bot.inventory.items().filter(i => plankNames.includes(i.name)).reduce((s, i) => s + i.count, 0);
      if (plankCount < 4) {
        let logItem = bot.inventory.items().find(i => logNames.includes(i.name));
        if (!logItem) {
          bot.chat("No logs, mining one...");
          let logBlock = bot.findBlock({
            matching: b => logNames.includes(b.name),
            maxDistance: 32
          });
          if (!logBlock) {
            const dir = new Vec3(1, 0, 0);
            logBlock = await exploreUntil(bot, dir, 60, () => bot.findBlock({
              matching: b => logNames.includes(b.name),
              maxDistance: 32
            }));
          }
          if (logBlock) {
            await mineBlock(bot, logBlock.name, 1);
          }
          logItem = bot.inventory.items().find(i => logNames.includes(i.name));
        }
        if (!logItem) {
          bot.chat("Failed to obtain a log for crafting table.");
          return;
        }
        const plankName = logItem.name.replace("_log", "_planks");
        await craftItem(bot, plankName, 1);
      }
      await craftItem(bot, "crafting_table", 1);
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

  // 3) Craft the furnace
  bot.chat("Crafting furnace...");
  await craftItem(bot, "furnace", 1);
  const furnaceCount = bot.inventory.items().filter(i => i.name === "furnace").reduce((s, i) => s + i.count, 0);
  bot.chat(`Done. Total furnaces: ${furnaceCount}.`);
}