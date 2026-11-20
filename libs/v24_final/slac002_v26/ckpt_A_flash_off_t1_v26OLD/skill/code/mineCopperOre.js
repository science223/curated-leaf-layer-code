async function mineCopperOre(bot) {
  const targetCopper = 3;
  const pickaxeNames = ["stone_pickaxe", "iron_pickaxe", "diamond_pickaxe", "netherite_pickaxe", "golden_pickaxe"];

  // 1) Ensure we have a suitable pickaxe (stone or better)
  const hasPickaxe = bot.inventory.items().some(i => pickaxeNames.includes(i.name));
  if (!hasPickaxe) {
    bot.chat("No suitable pickaxe (stone or better) found. Cannot mine copper ore.");
    return;
  }

  // 2) Count existing raw_copper
  let copperCount = bot.inventory.items().filter(i => i.name === "raw_copper").reduce((s, i) => s + i.count, 0);
  let remaining = targetCopper - copperCount;
  if (remaining <= 0) {
    bot.chat(`Already have ${copperCount} raw copper, no need to mine.`);
    return;
  }
  bot.chat(`Need ${remaining} more raw copper.`);

  // 3) Mine copper ore until we have enough
  let attempts = 0;
  while (remaining > 0 && attempts < 10) {
    attempts++;
    let copperBlock = bot.findBlock({
      matching: mcData.blocksByName["copper_ore"].id,
      maxDistance: 32
    });
    if (!copperBlock) {
      bot.chat("No copper ore nearby, exploring...");
      const directions = [new Vec3(0, -1, 0), new Vec3(1, -1, 0), new Vec3(-1, -1, 0), new Vec3(0, -1, 1), new Vec3(0, -1, -1), new Vec3(1, 0, 1), new Vec3(-1, 0, -1)];
      const dir = directions[Math.floor(Math.random() * directions.length)];
      copperBlock = await exploreUntil(bot, dir, 60, () => {
        return bot.findBlock({
          matching: mcData.blocksByName["copper_ore"].id,
          maxDistance: 32
        });
      });
    }
    if (!copperBlock) {
      bot.chat("Failed to find any copper ore.");
      return;
    }
    bot.chat("Found copper ore, mining...");
    // Mine a few ore blocks; each drops 2-3 raw copper
    await mineBlock(bot, "copper_ore", Math.max(1, Math.ceil(remaining / 2)));
    copperCount = bot.inventory.items().filter(i => i.name === "raw_copper").reduce((s, i) => s + i.count, 0);
    remaining = targetCopper - copperCount;
    if (remaining > 0) {
      bot.chat(`Still need ${remaining} more raw copper.`);
    }
  }
  bot.chat(`Done mining copper ore. Total raw copper: ${copperCount}.`);
}