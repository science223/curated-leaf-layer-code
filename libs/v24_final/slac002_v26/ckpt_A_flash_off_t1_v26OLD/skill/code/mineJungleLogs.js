async function mineJungleLogs(bot) {
  const targetCount = 3;
  const logName = "jungle_log";

  // Count existing jungle logs in inventory
  const existing = bot.inventory.items().filter(item => item.name === logName).reduce((sum, item) => sum + item.count, 0);
  let remaining = targetCount - existing;
  if (remaining <= 0) {
    bot.chat(`Already have ${existing} ${logName}, no need to mine.`);
    return;
  }
  bot.chat(`Need ${remaining} more ${logName}.`);
  while (remaining > 0) {
    // Try to find a nearby jungle log
    let logBlock = bot.findBlock({
      matching: block => block.name === logName,
      maxDistance: 32
    });
    if (!logBlock) {
      bot.chat("No jungle log nearby, exploring...");
      const directions = [new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1), new Vec3(1, 0, 1), new Vec3(-1, 0, -1), new Vec3(1, 0, -1), new Vec3(-1, 0, 1)];
      const dir = directions[Math.floor(Math.random() * directions.length)];
      logBlock = await exploreUntil(bot, dir, 60, () => {
        return bot.findBlock({
          matching: block => block.name === logName,
          maxDistance: 32
        });
      });
    }
    if (!logBlock) {
      bot.chat("Failed to find any jungle log.");
      return;
    }
    bot.chat(`Found ${logName}, mining...`);
    await mineBlock(bot, logName, remaining);

    // Recount after mining
    const nowCount = bot.inventory.items().filter(item => item.name === logName).reduce((sum, item) => sum + item.count, 0);
    remaining = targetCount - nowCount;
    if (remaining > 0) {
      bot.chat(`Still need ${remaining} more ${logName}.`);
    }
  }
  bot.chat(`Done mining ${targetCount} ${logName}.`);
}