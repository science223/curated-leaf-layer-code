async function mineOakLogs(bot) {
  const targetCount = 3;
  const logName = "oak_log";

  // Count existing oak logs
  let current = bot.inventory.items().filter(i => i.name === logName).reduce((s, i) => s + i.count, 0);
  if (current >= targetCount) {
    bot.chat(`Already have ${current} ${logName}, no need to mine.`);
    return;
  }
  bot.chat(`Need ${targetCount - current} more ${logName}.`);
  let attempts = 0;
  while (current < targetCount && attempts < 10) {
    attempts++;

    // Try to find a nearby oak log
    let logBlock = bot.findBlock({
      matching: block => block.name === logName,
      maxDistance: 32
    });
    if (!logBlock) {
      bot.chat("No oak log nearby, exploring...");
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
      bot.chat("Failed to find any oak log.");
      return;
    }
    const remaining = targetCount - current;
    bot.chat(`Found ${logName}, mining ${remaining}...`);
    await mineBlock(bot, logName, remaining);
    current = bot.inventory.items().filter(i => i.name === logName).reduce((s, i) => s + i.count, 0);
  }
  bot.chat(`Done. Total ${logName}: ${current}.`);
}