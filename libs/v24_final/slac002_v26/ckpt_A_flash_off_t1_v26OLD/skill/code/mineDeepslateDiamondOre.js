async function mineDeepslateDiamondOre(bot) {
  const goodPickaxes = ["iron_pickaxe", "diamond_pickaxe", "netherite_pickaxe"];

  // 1) Ensure we have a suitable pickaxe
  const hasPickaxe = bot.inventory.items().some(i => goodPickaxes.includes(i.name));
  if (!hasPickaxe) {
    bot.chat("No iron-or-better pickaxe found. Cannot mine deepslate diamond ore.");
    return;
  }
  bot.chat("Have a suitable pickaxe, searching for deepslate diamond ore...");

  // 2) Check if deepslate diamond ore is already nearby
  let diamondBlock = bot.findBlock({
    matching: mcData.blocksByName["deepslate_diamond_ore"].id,
    maxDistance: 32
  });

  // 3) If not found, explore (prefer downward) until found
  let attempts = 0;
  while (!diamondBlock && attempts < 20) {
    attempts++;
    bot.chat(`No deepslate diamond ore nearby, exploring (attempt ${attempts})...`);
    const directions = [new Vec3(0, -1, 0), new Vec3(1, -1, 0), new Vec3(-1, -1, 0), new Vec3(0, -1, 1), new Vec3(0, -1, -1), new Vec3(1, 0, 1), new Vec3(-1, 0, -1)];
    const dir = directions[Math.floor(Math.random() * directions.length)];
    diamondBlock = await exploreUntil(bot, dir, 60, () => {
      return bot.findBlock({
        matching: mcData.blocksByName["deepslate_diamond_ore"].id,
        maxDistance: 32
      });
    });
  }
  if (!diamondBlock) {
    bot.chat("Failed to find any deepslate diamond ore after exploring.");
    return;
  }

  // 4) Mine the deepslate diamond ore
  bot.chat("Found deepslate diamond ore, mining...");
  await mineBlock(bot, "deepslate_diamond_ore", 1);

  // 5) Verify
  const diamondCount = bot.inventory.items().filter(i => i.name === "diamond").reduce((s, i) => s + i.count, 0);
  bot.chat(`Done. Total diamonds in inventory: ${diamondCount}.`);
}