async function killOnePig(bot) {
  // Check if a pig is nearby
  let pig = bot.nearestEntity(entity => entity.name === "pig" && entity.position.distanceTo(bot.entity.position) < 32);
  if (!pig) {
    bot.chat("No pig nearby, exploring...");
    const directions = [new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1), new Vec3(1, 0, 1), new Vec3(-1, 0, -1)];
    const dir = directions[Math.floor(Math.random() * directions.length)];
    pig = await exploreUntil(bot, dir, 60, () => {
      return bot.nearestEntity(entity => entity.name === "pig" && entity.position.distanceTo(bot.entity.position) < 32);
    });
  }
  if (!pig) {
    bot.chat("Failed to find any pig.");
    return;
  }
  bot.chat("Found pig, attacking...");
  await killMob(bot, "pig", 300);
  bot.chat("Pig killed.");
}