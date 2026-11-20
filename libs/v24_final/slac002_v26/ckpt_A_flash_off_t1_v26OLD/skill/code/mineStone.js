async function mineStone(bot) {
  // Ensure we have a pickaxe
  const pickaxeNames = ["wooden_pickaxe", "stone_pickaxe", "iron_pickaxe", "diamond_pickaxe", "netherite_pickaxe", "golden_pickaxe"];
  let hasPickaxe = bot.inventory.items().some(i => pickaxeNames.includes(i.name));
  if (!hasPickaxe) {
    bot.chat("No pickaxe found, crafting a wooden pickaxe...");
    // Ensure we have planks and sticks
    const plankNames = ["oak_planks", "birch_planks", "spruce_planks", "jungle_planks", "acacia_planks", "dark_oak_planks", "mangrove_planks", "cherry_planks", "bamboo_planks", "crimson_planks", "warped_planks"];
    const logNames = ["oak_log", "birch_log", "spruce_log", "jungle_log", "acacia_log", "dark_oak_log", "mangrove_log", "cherry_log"];
    let plankCount = bot.inventory.items().filter(i => plankNames.includes(i.name)).reduce((s, i) => s + i.count, 0);
    if (plankCount < 3) {
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
      if (logItem) {
        const plankName = logItem.name.replace("_log", "_planks");
        await craftItem(bot, plankName, 1);
      }
    }
    let stickCount = bot.inventory.items().filter(i => i.name === "stick").reduce((s, i) => s + i.count, 0);
    if (stickCount < 2) {
      await craftItem(bot, "stick", 1);
    }
    await craftItem(bot, "wooden_pickaxe", 1);
    hasPickaxe = bot.inventory.items().some(i => pickaxeNames.includes(i.name));
    if (!hasPickaxe) {
      bot.chat("Failed to craft a pickaxe.");
      return;
    }
  }

  // Find stone
  let stoneBlock = bot.findBlock({
    matching: mcData.blocksByName["stone"].id,
    maxDistance: 32
  });
  if (!stoneBlock) {
    bot.chat("No stone nearby, exploring downward...");
    stoneBlock = await exploreUntil(bot, new Vec3(0, -1, 0), 60, () => {
      return bot.findBlock({
        matching: mcData.blocksByName["stone"].id,
        maxDistance: 32
      });
    });
  }
  if (!stoneBlock) {
    bot.chat("Failed to find any stone.");
    return;
  }
  bot.chat("Found stone, mining 3...");
  await mineBlock(bot, "stone", 3);
  bot.chat("Mined 3 stone.");
}