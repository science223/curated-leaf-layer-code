async function craftSticks(bot) {
  const plankNames = ["oak_planks", "birch_planks", "spruce_planks", "jungle_planks", "acacia_planks", "dark_oak_planks", "mangrove_planks", "cherry_planks", "bamboo_planks", "crimson_planks", "warped_planks"];
  const logNames = ["oak_log", "birch_log", "spruce_log", "jungle_log", "acacia_log", "dark_oak_log", "mangrove_log", "cherry_log"];

  // 1) Ensure we have at least 2 planks
  let plankCount = bot.inventory.items().filter(i => plankNames.includes(i.name)).reduce((s, i) => s + i.count, 0);
  if (plankCount < 2) {
    bot.chat("Not enough planks, gathering logs...");
    let logItem = bot.inventory.items().find(i => logNames.includes(i.name));
    if (!logItem) {
      let logBlock = bot.findBlock({
        matching: b => logNames.includes(b.name),
        maxDistance: 32
      });
      if (!logBlock) {
        const directions = [new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1)];
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
    await craftItem(bot, plankName, 1);
    plankCount = bot.inventory.items().filter(i => plankNames.includes(i.name)).reduce((s, i) => s + i.count, 0);
    if (plankCount < 2) {
      bot.chat("Still not enough planks.");
      return;
    }
  }

  // 2) Craft sticks (1 recipe yields 4 sticks)
  bot.chat("Crafting sticks...");
  await craftItem(bot, "stick", 1);
  const stickCount = bot.inventory.items().filter(i => i.name === "stick").reduce((s, i) => s + i.count, 0);
  bot.chat(`Done. Total sticks: ${stickCount}.`);
}