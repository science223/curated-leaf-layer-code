async function mineDiamondOre(bot) {
  const goodPickaxes = ["iron_pickaxe", "diamond_pickaxe", "netherite_pickaxe"];

  // 1) Ensure we have a suitable pickaxe
  let hasPickaxe = bot.inventory.items().some(i => goodPickaxes.includes(i.name));
  if (!hasPickaxe) {
    bot.chat("No iron-or-better pickaxe found. Cannot mine diamond ore.");
    return;
  }
  bot.chat("Have a suitable pickaxe, searching for diamond ore...");

  // 2) Check if diamond ore is already nearby
  let diamondBlock = bot.findBlock({
    matching: mcData.blocksByName["diamond_ore"].id,
    maxDistance: 32
  });

  // 3) If not found, explore downward toward diamond levels
  let attempts = 0;
  while (!diamondBlock && attempts < 20) {
    attempts++;
    bot.chat(`No diamond ore nearby, exploring downward (attempt ${attempts})...`);
    const dir = new Vec3(0, -1, 0);
    diamondBlock = await exploreUntil(bot, dir, 60, () => {
      return bot.findBlock({
        matching: mcData.blocksByName["diamond_ore"].id,
        maxDistance: 32
      });
    });
  }
  if (!diamondBlock) {
    bot.chat("Failed to find any diamond ore after exploring.");
    return;
  }

  // 4) Mine the diamond ore
  bot.chat("Found diamond ore, mining...");
  await mineBlock(bot, "diamond_ore", 1);

  // 5) Verify
  const diamondCount = bot.inventory.items().filter(i => i.name === "diamond").reduce((s, i) => s + i.count, 0);
  bot.chat(`Done. Total diamonds in inventory: ${diamondCount}.`);
}