async function killOneSkeleton(bot) {
  // Check if a skeleton is nearby
  let skeleton = bot.nearestEntity(entity => entity.name === "skeleton" && entity.position.distanceTo(bot.entity.position) < 32);
  if (!skeleton) {
    bot.chat("No skeleton nearby, exploring...");
    const directions = [new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1), new Vec3(1, 0, 1), new Vec3(-1, 0, -1), new Vec3(1, 0, -1), new Vec3(-1, 0, 1)];
    const dir = directions[Math.floor(Math.random() * directions.length)];
    skeleton = await exploreUntil(bot, dir, 60, () => {
      return bot.nearestEntity(entity => entity.name === "skeleton" && entity.position.distanceTo(bot.entity.position) < 32);
    });
  }
  if (!skeleton) {
    bot.chat("Failed to find any skeleton.");
    return;
  }
  bot.chat("Found skeleton, attacking...");
  await killMob(bot, "skeleton", 300);
  bot.chat("Skeleton killed.");
}