async function mineIronOre(bot) {
  const targetCount = 3;
  const pickaxeNames = ["stone_pickaxe", "iron_pickaxe", "diamond_pickaxe", "netherite_pickaxe"];

  // 1) Check existing raw_iron
  let ironCount = bot.inventory.items().filter(i => i.name === "raw_iron").reduce((s, i) => s + i.count, 0);
  if (ironCount >= targetCount) {
    bot.chat(`Already have ${ironCount} raw iron, no need to mine.`);
    return;
  }

  // 2) Ensure we have a suitable pickaxe
  const hasPickaxe = bot.inventory.items().some(i => pickaxeNames.includes(i.name));
  if (!hasPickaxe) {
    bot.chat("No suitable pickaxe (stone or better) found. Cannot mine iron ore.");
    return;
  }
  let remaining = targetCount - ironCount;
  bot.chat(`Need ${remaining} more iron ore.`);

  // 3) Mine iron ore until we have enough
  let attempts = 0;
  while (remaining > 0 && attempts < 15) {
    attempts++;
    let ironBlock = bot.findBlock({
      matching: mcData.blocksByName["iron_ore"].id,
      maxDistance: 32
    });
    if (!ironBlock) {
      bot.chat("No iron ore nearby, exploring downward...");
      const directions = [new Vec3(0, -1, 0), new Vec3(1, -1, 0), new Vec3(-1, -1, 0), new Vec3(0, -1, 1), new Vec3(0, -1, -1), new Vec3(1, -1, 1), new Vec3(-1, -1, -1)];
      const dir = directions[Math.floor(Math.random() * directions.length)];
      ironBlock = await exploreUntil(bot, dir, 60, () => {
        return bot.findBlock({
          matching: mcData.blocksByName["iron_ore"].id,
          maxDistance: 32
        });
      });
    }
    if (!ironBlock) {
      bot.chat("Failed to find any iron ore this attempt.");
      continue;
    }
    bot.chat("Found iron ore, mining...");
    await mineBlock(bot, "iron_ore", remaining);
    ironCount = bot.inventory.items().filter(i => i.name === "raw_iron").reduce((s, i) => s + i.count, 0);
    remaining = targetCount - ironCount;
    if (remaining > 0) {
      bot.chat(`Still need ${remaining} more iron ore.`);
    }
  }
  bot.chat(`Done mining iron ore. Total raw iron: ${ironCount}.`);
}