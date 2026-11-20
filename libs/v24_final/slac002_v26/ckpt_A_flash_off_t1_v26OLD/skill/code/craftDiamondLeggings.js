async function craftDiamondLeggings(bot) {
  const countItem = name => bot.inventory.items().filter(i => i.name === name).reduce((s, i) => s + i.count, 0);

  // 1) Ensure we have at least 7 diamonds
  let diamondCount = countItem("diamond");
  if (diamondCount < 7) {
    bot.chat(`Have ${diamondCount} diamond(s), need 7. Mining more...`);
    const goodPickaxes = ["iron_pickaxe", "diamond_pickaxe", "netherite_pickaxe"];
    if (!bot.inventory.items().some(i => goodPickaxes.includes(i.name))) {
      bot.chat("No suitable pickaxe to mine diamond ore.");
      return;
    }
    let attempts = 0;
    while (countItem("diamond") < 7 && attempts < 20) {
      attempts++;
      let diamondBlock = bot.findBlock({
        matching: b => b.name === "diamond_ore" || b.name === "deepslate_diamond_ore",
        maxDistance: 32
      });
      if (!diamondBlock) {
        bot.chat(`No diamond ore nearby, exploring (attempt ${attempts})...`);
        const directions = [new Vec3(0, -1, 0), new Vec3(1, -1, 0), new Vec3(-1, -1, 0), new Vec3(0, -1, 1), new Vec3(0, -1, -1), new Vec3(1, -1, 1), new Vec3(-1, -1, -1)];
        const dir = directions[Math.floor(Math.random() * directions.length)];
        diamondBlock = await exploreUntil(bot, dir, 60, () => bot.findBlock({
          matching: b => b.name === "diamond_ore" || b.name === "deepslate_diamond_ore",
          maxDistance: 32
        }));
      }
      if (!diamondBlock) {
        bot.chat("Failed to find diamond ore this attempt.");
        continue;
      }
      bot.chat(`Found ${diamondBlock.name}, mining...`);
      await mineBlock(bot, diamondBlock.name, 1);
    }
    diamondCount = countItem("diamond");
    if (diamondCount < 7) {
      bot.chat(`Failed to obtain enough diamonds (have ${diamondCount}).`);
      return;
    }
  }
  bot.chat(`Have ${diamondCount} diamonds.`);

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
    const pos = bot.entity.position.offset(1, 0, 0);
    await placeItem(bot, "crafting_table", pos);
    tableBlock = bot.findBlock({
      matching: mcData.blocksByName.crafting_table.id,
      maxDistance: 32
    });
    if (!tableBlock) {
      bot.chat("Failed to place crafting table.");
      return;
    }
  }

  // 3) Craft the diamond leggings
  bot.chat("Crafting diamond leggings...");
  await craftItem(bot, "diamond_leggings", 1);

  // 4) Verify
  const leggingsCount = countItem("diamond_leggings");
  bot.chat(`Done. Total diamond leggings: ${leggingsCount}.`);
}