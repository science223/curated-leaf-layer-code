async function killOneCreeper(bot) {
  // Check if a creeper is nearby
  let creeper = bot.nearestEntity(entity => entity.name === "creeper" && entity.position.distanceTo(bot.entity.position) < 32);
  if (!creeper) {
    bot.chat("No creeper nearby, exploring...");
    const directions = [new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1), new Vec3(1, 0, 1), new Vec3(-1, 0, -1), new Vec3(1, 0, -1), new Vec3(-1, 0, 1)];
    const dir = directions[Math.floor(Math.random() * directions.length)];
    creeper = await exploreUntil(bot, dir, 60, () => {
      return bot.nearestEntity(entity => entity.name === "creeper" && entity.position.distanceTo(bot.entity.position) < 32);
    });
  }
  if (!creeper) {
    bot.chat("Failed to find any creeper.");
    return;
  }

  // Equip best available sword
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

  // Equip shield for protection
  const shieldItem = bot.inventory.findInventoryItem(mcData.itemsByName["shield"].id);
  if (shieldItem) {
    await bot.equip(shieldItem, "off-hand");
  }
  bot.chat("Found creeper, attacking...");
  await killMob(bot, "creeper", 300);
  bot.chat("Creeper killed.");
}