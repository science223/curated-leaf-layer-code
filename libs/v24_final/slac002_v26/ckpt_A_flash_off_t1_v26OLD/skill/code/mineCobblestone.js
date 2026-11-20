async function mineCobblestone(bot) {
  const pickaxeNames = ["wooden_pickaxe", "stone_pickaxe", "iron_pickaxe", "diamond_pickaxe", "netherite_pickaxe", "golden_pickaxe"];
  const plankNames = ["oak_planks", "birch_planks", "spruce_planks", "jungle_planks", "acacia_planks", "dark_oak_planks", "mangrove_planks", "cherry_planks", "bamboo_planks", "crimson_planks", "warped_planks"];
  const logNames = ["oak_log", "birch_log", "spruce_log", "jungle_log", "acacia_log", "dark_oak_log", "mangrove_log", "cherry_log"];

  // 1) Ensure we have a pickaxe
  let hasPickaxe = bot.inventory.items().some(i => pickaxeNames.includes(i.name));
  if (!hasPickaxe) {
    bot.chat("No pickaxe found, crafting a wooden pickaxe...");
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

  // 2) Count current cobblestone
  const target = 8;
  let cobbleCount = bot.inventory.items().filter(i => i.name === "cobblestone").reduce((s, i) => s + i.count, 0);
  let needed = target - cobbleCount;
  if (needed <= 0) {
    bot.chat(`Already have ${cobbleCount} cobblestone, no need to mine.`);
    return;
  }
  bot.chat(`Need ${needed} more cobblestone, mining stone...`);

  // 3) Find stone and mine
  let attempts = 0;
  while (needed > 0 && attempts < 10) {
    attempts++;
    let stoneBlock = bot.findBlock({
      matching: mcData.blocksByName["stone"].id,
      maxDistance: 32
    });
    if (!stoneBlock) {
      bot.chat("No stone nearby, exploring...");
      const directions = [new Vec3(0, -1, 0), new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1), new Vec3(1, 0, 1), new Vec3(-1, 0, -1)];
      const dir = directions[Math.floor(Math.random() * directions.length)];
      stoneBlock = await exploreUntil(bot, dir, 60, () => bot.findBlock({
        matching: mcData.blocksByName["stone"].id,
        maxDistance: 32
      }));
    }
    if (!stoneBlock) {
      bot.chat("Failed to find any stone.");
      return;
    }
    await mineBlock(bot, "stone", needed);
    cobbleCount = bot.inventory.items().filter(i => i.name === "cobblestone").reduce((s, i) => s + i.count, 0);
    needed = target - cobbleCount;
    if (needed > 0) {
      bot.chat(`Still need ${needed} more cobblestone.`);
    }
  }
  bot.chat(`Done mining cobblestone. Total cobblestone: ${cobbleCount}.`);
}