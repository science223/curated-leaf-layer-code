async function mineThreeDiamondOre(bot) {
  const goodPickaxes = ["iron_pickaxe", "diamond_pickaxe", "netherite_pickaxe"];
  const countDiamonds = () => bot.inventory.items().filter(i => i.name === "diamond").reduce((s, i) => s + i.count, 0);

  // 1) Ensure we have a suitable pickaxe
  const hasPickaxe = bot.inventory.items().some(i => goodPickaxes.includes(i.name));
  if (!hasPickaxe) {
    bot.chat("No iron-or-better pickaxe found. Cannot mine diamond ore.");
    return;
  }
  bot.chat("Have a suitable pickaxe, searching for diamond ore...");

  // 2) Free inventory space so drops can be picked up
  const junkNames = ["cobblestone", "andesite", "diorite", "granite", "gravel", "dirt", "sand"];
  let attemptsFree = 0;
  while (bot.inventory.emptySlotCount() < 3 && attemptsFree < 20) {
    attemptsFree++;
    const junk = bot.inventory.items().find(i => junkNames.includes(i.name));
    if (!junk) break;
    bot.chat(`Inventory tight, dropping ${junk.name} x${junk.count} to make room.`);
    await bot.toss(junk.type, null, junk.count);
  }

  // 3) Mine diamond ore until we have mined 3 blocks
  const before = countDiamonds();
  let minedCount = 0;
  let attempts = 0;
  while (minedCount < 3 && attempts < 30) {
    attempts++;
    let diamondBlock = bot.findBlock({
      matching: mcData.blocksByName["diamond_ore"].id,
      maxDistance: 32
    });
    if (!diamondBlock) {
      bot.chat(`No diamond ore nearby, exploring (attempt ${attempts})...`);
      const directions = [new Vec3(0, -1, 0), new Vec3(1, -1, 0), new Vec3(-1, -1, 0), new Vec3(0, -1, 1), new Vec3(0, -1, -1), new Vec3(1, -1, 1), new Vec3(-1, -1, -1)];
      const dir = directions[Math.floor(Math.random() * directions.length)];
      diamondBlock = await exploreUntil(bot, dir, 60, () => {
        return bot.findBlock({
          matching: mcData.blocksByName["diamond_ore"].id,
          maxDistance: 32
        });
      });
    }
    if (!diamondBlock) {
      bot.chat("Failed to find any diamond ore this attempt.");
      continue;
    }
    bot.chat("Found diamond ore, mining...");
    await mineBlock(bot, "diamond_ore", 1);
    minedCount++;
    bot.chat(`Mined ${minedCount} diamond ore block(s) so far.`);
  }

  // 4) Verify
  const after = countDiamonds();
  bot.chat(`Done. Mined ${minedCount} diamond ore block(s). Diamonds gained: ${after - before}. Total diamonds in inventory: ${after}.`);
}