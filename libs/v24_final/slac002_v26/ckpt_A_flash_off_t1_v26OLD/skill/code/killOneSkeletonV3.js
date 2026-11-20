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

  // Equip best available sword and shield
  const swordNames = ["netherite_sword", "diamond_sword", "iron_sword", "stone_sword", "wooden_sword", "golden_sword"];
  let swordItem = null;
  for (const name of swordNames) {
    const item = bot.inventory.findInventoryItem(mcData.itemsByName[name].id);
    if (item) {
      swordItem = item;
      break;
    }
  }
  if (swordItem) {
    await bot.equip(swordItem, "hand");
  } else {
    bot.chat("No sword found, attacking with bare hands.");
  }
  const shieldItem = bot.inventory.findInventoryItem(mcData.itemsByName["shield"].id);
  if (shieldItem) {
    await bot.equip(shieldItem, "off-hand");
  }
  bot.chat("Found skeleton, attacking...");
  await killMob(bot, "skeleton", 300);
  bot.chat("Skeleton killed.");
}