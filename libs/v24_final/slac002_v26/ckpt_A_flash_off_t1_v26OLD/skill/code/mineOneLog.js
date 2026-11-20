async function mineOneLog(bot) {
  const logNames = ["oak_log", "birch_log", "spruce_log", "jungle_log", "acacia_log", "dark_oak_log", "mangrove_log"];

  // Check if we already have a log in inventory
  for (const name of logNames) {
    const item = bot.inventory.findInventoryItem(mcData.itemsByName[name].id);
    if (item) {
      bot.chat(`Already have ${name}, no need to mine.`);
      return;
    }
  }

  // Try to find a log nearby
  let logBlock = bot.findBlock({
    matching: block => logNames.includes(block.name),
    maxDistance: 32
  });
  if (!logBlock) {
    bot.chat("No log nearby, exploring...");
    const directions = [new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1), new Vec3(1, 0, 1), new Vec3(-1, 0, -1)];
    const dir = directions[Math.floor(Math.random() * directions.length)];
    logBlock = await exploreUntil(bot, dir, 60, () => {
      return bot.findBlock({
        matching: block => logNames.includes(block.name),
        maxDistance: 32
      });
    });
  }
  if (!logBlock) {
    bot.chat("Failed to find any log.");
    return;
  }
  bot.chat(`Found ${logBlock.name}, mining...`);
  await mineBlock(bot, logBlock.name, 1);
  bot.chat("Mined 1 log.");
}