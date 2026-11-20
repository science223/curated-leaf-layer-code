async function craftLightningRod(bot) {
  // 1) Ensure we have at least 3 copper ingots
  let copperCount = bot.inventory.items().filter(i => i.name === "copper_ingot").reduce((s, i) => s + i.count, 0);
  if (copperCount < 3) {
    bot.chat(`Not enough copper ingots (have ${copperCount}, need 3).`);
    return;
  }
  bot.chat(`Have ${copperCount} copper ingots.`);

  // 2) Ensure a crafting table block is nearby
  let tableBlock = bot.findBlock({
    matching: mcData.blocksByName.crafting_table.id,
    maxDistance: 32
  });
  if (!tableBlock) {
    const tableItem = bot.inventory.findInventoryItem(mcData.itemsByName["crafting_table"].id);
    if (!tableItem) {
      bot.chat("No crafting table item available.");
      return;
    }
    bot.chat("Placing crafting table...");

    // Try several candidate positions around the bot, placing on top of a solid block
    const base = bot.entity.position.floored();
    const candidates = [base.offset(1, 0, 0), base.offset(-1, 0, 0), base.offset(0, 0, 1), base.offset(0, 0, -1), base.offset(1, 0, 1), base.offset(-1, 0, -1), base.offset(1, 0, -1), base.offset(-1, 0, 1)];
    for (const candidate of candidates) {
      const below = bot.blockAt(candidate.offset(0, -1, 0));
      const atPos = bot.blockAt(candidate);
      if (below && below.name !== "air" && below.name !== "water" && below.name !== "lava" && atPos && (atPos.name === "air" || atPos.name === "water")) {
        try {
          await placeItem(bot, "crafting_table", candidate);
        } catch (e) {
          bot.chat(`Placement failed at candidate, trying next.`);
          continue;
        }
        tableBlock = bot.findBlock({
          matching: mcData.blocksByName.crafting_table.id,
          maxDistance: 32
        });
        if (tableBlock) break;
      }
    }
    if (!tableBlock) {
      bot.chat("Failed to place crafting table.");
      return;
    }
  }

  // 3) Craft the lightning rod
  bot.chat("Crafting lightning rod...");
  await craftItem(bot, "lightning_rod", 1);
  const rodCount = bot.inventory.items().filter(i => i.name === "lightning_rod").reduce((s, i) => s + i.count, 0);
  bot.chat(`Done. Total lightning rods: ${rodCount}.`);
}