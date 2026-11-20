async function mineCoalOre(bot) {
  const targetCoal = 3;
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

  // 2) Count existing coal
  let coalCount = bot.inventory.items().filter(i => i.name === "coal").reduce((s, i) => s + i.count, 0);
  let remaining = targetCoal - coalCount;
  if (remaining <= 0) {
    bot.chat(`Already have ${coalCount} coal, no need to mine.`);
    return;
  }
  bot.chat(`Need ${remaining} more coal.`);

  // 3) Mine coal ore until we have enough (bounded attempts)
  let attempts = 0;
  while (remaining > 0 && attempts < 10) {
    attempts++;
    let coalBlock = bot.findBlock({
      matching: mcData.blocksByName["coal_ore"].id,
      maxDistance: 32
    });
    if (!coalBlock) {
      bot.chat("No coal ore nearby, exploring...");
      const directions = [new Vec3(0, -1, 0), new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1), new Vec3(1, 0, 1), new Vec3(-1, 0, -1)];
      const dir = directions[Math.floor(Math.random() * directions.length)];
      coalBlock = await exploreUntil(bot, dir, 60, () => bot.findBlock({
        matching: mcData.blocksByName["coal_ore"].id,
        maxDistance: 32
      }));
    }
    if (!coalBlock) {
      bot.chat("Failed to find any coal ore.");
      return;
    }
    bot.chat("Found coal ore, mining...");
    await mineBlock(bot, "coal_ore", remaining);
    coalCount = bot.inventory.items().filter(i => i.name === "coal").reduce((s, i) => s + i.count, 0);
    remaining = targetCoal - coalCount;
    if (remaining > 0) {
      bot.chat(`Still need ${remaining} more coal.`);
    }
  }
  bot.chat(`Done mining coal. Total coal: ${coalCount}.`);
}