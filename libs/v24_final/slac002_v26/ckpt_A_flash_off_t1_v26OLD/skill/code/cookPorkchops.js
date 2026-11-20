async function cookPorkchops(bot) {
  const targetCount = 2;

  // 0) Already done?
  let cookedCount = bot.inventory.items().filter(i => i.name === "cooked_porkchop").reduce((s, i) => s + i.count, 0);
  if (cookedCount >= targetCount) {
    bot.chat(`Already have ${cookedCount} cooked porkchops.`);
    return;
  }

  // 1) Ensure we have enough raw porkchops
  let porkchopCount = bot.inventory.items().filter(i => i.name === "porkchop").reduce((s, i) => s + i.count, 0);
  let attempts = 0;
  while (porkchopCount < targetCount && attempts < 5) {
    attempts++;
    bot.chat(`Need ${targetCount - porkchopCount} more raw porkchop(s), hunting pigs...`);
    let pig = bot.nearestEntity(entity => entity.name === "pig" && entity.position.distanceTo(bot.entity.position) < 32);
    if (!pig) {
      const directions = [new Vec3(1, 0, 1), new Vec3(-1, 0, 1), new Vec3(1, 0, -1), new Vec3(-1, 0, -1), new Vec3(1, 0, 0), new Vec3(0, 0, 1)];
      const dir = directions[Math.floor(Math.random() * directions.length)];
      pig = await exploreUntil(bot, dir, 60, () => {
        return bot.nearestEntity(entity => entity.name === "pig" && entity.position.distanceTo(bot.entity.position) < 32);
      });
    }
    if (!pig) {
      bot.chat("Could not find any pig.");
      break;
    }
    bot.chat("Found a pig, killing it...");
    await killMob(bot, "pig", 300);
    await bot.waitForTicks(20);
    porkchopCount = bot.inventory.items().filter(i => i.name === "porkchop").reduce((s, i) => s + i.count, 0);
  }
  if (porkchopCount < targetCount) {
    bot.chat(`Still not enough raw porkchops (${porkchopCount}/${targetCount}).`);
    return;
  }
  bot.chat(`Have ${porkchopCount} raw porkchops, preparing to cook.`);

  // 2) Ensure a furnace block is nearby
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  if (!furnaceBlock) {
    const furnaceItem = bot.inventory.findInventoryItem(mcData.itemsByName["furnace"].id);
    if (!furnaceItem) {
      bot.chat("No furnace item available.");
      return;
    }
    bot.chat("Placing furnace...");
    const pos = bot.entity.position.offset(1, 0, 0);
    await placeItem(bot, "furnace", pos);
    furnaceBlock = bot.findBlock({
      matching: mcData.blocksByName.furnace.id,
      maxDistance: 32
    });
    if (!furnaceBlock) {
      bot.chat("Failed to place furnace.");
      return;
    }
  }

  // 3) Ensure we have a valid fuel in inventory
  const fuelCandidates = ["coal", "charcoal", "oak_planks", "birch_planks", "spruce_planks", "jungle_planks", "acacia_planks", "dark_oak_planks", "stick"];
  let fuelName = null;
  for (const name of fuelCandidates) {
    const itemDef = mcData.itemsByName[name];
    if (!itemDef) continue;
    const invItem = bot.inventory.findInventoryItem(itemDef.id, null);
    if (invItem && invItem.count > 0) {
      fuelName = name;
      break;
    }
  }

  // 3a) If no fuel, try to mine coal ore
  if (!fuelName) {
    bot.chat("No fuel in inventory, trying to mine coal ore...");
    const pickaxeNames = ["wooden_pickaxe", "stone_pickaxe", "iron_pickaxe", "diamond_pickaxe", "netherite_pickaxe", "golden_pickaxe"];
    const hasPickaxe = bot.inventory.items().some(i => pickaxeNames.includes(i.name));
    if (hasPickaxe) {
      let coalBlock = bot.findBlock({
        matching: mcData.blocksByName["coal_ore"].id,
        maxDistance: 32
      });
      if (!coalBlock) {
        const directions = [new Vec3(0, -1, 0), new Vec3(1, -1, 0), new Vec3(-1, -1, 0), new Vec3(0, -1, 1), new Vec3(0, -1, -1)];
        const dir = directions[Math.floor(Math.random() * directions.length)];
        coalBlock = await exploreUntil(bot, dir, 60, () => {
          return bot.findBlock({
            matching: mcData.blocksByName["coal_ore"].id,
            maxDistance: 32
          });
        });
      }
      if (coalBlock) {
        bot.chat("Found coal ore, mining...");
        await mineBlock(bot, "coal_ore", 1);
      }
    }
    for (const name of fuelCandidates) {
      const itemDef = mcData.itemsByName[name];
      if (!itemDef) continue;
      const invItem = bot.inventory.findInventoryItem(itemDef.id, null);
      if (invItem && invItem.count > 0) {
        fuelName = name;
        break;
      }
    }
  }

  // 3b) If still no fuel, craft planks from a log
  if (!fuelName) {
    bot.chat("Still no fuel, trying to obtain planks from a log...");
    const logNames = ["oak_log", "birch_log", "spruce_log", "jungle_log", "acacia_log", "dark_oak_log", "mangrove_log", "cherry_log"];
    let logItem = bot.inventory.items().find(i => logNames.includes(i.name));
    if (!logItem) {
      let logBlock = bot.findBlock({
        matching: b => logNames.includes(b.name),
        maxDistance: 32
      });
      if (!logBlock) {
        const directions = [new Vec3(1, 0, 1), new Vec3(-1, 0, 1), new Vec3(1, 0, -1), new Vec3(-1, 0, -1)];
        const dir = directions[Math.floor(Math.random() * directions.length)];
        logBlock = await exploreUntil(bot, dir, 60, () => bot.findBlock({
          matching: b => logNames.includes(b.name),
          maxDistance: 32
        }));
      }
      if (logBlock) {
        await mineBlock(bot, logBlock.name, 1);
      }
      logItem = bot.inventory.items().find(i => logNames.includes(i.name));
    }
    if (logItem) {
      const plankName = logItem.name.replace("_log", "_planks");
      await craftItem(bot, plankName, 1);
      const plankDef = mcData.itemsByName[plankName];
      const invItem = bot.inventory.findInventoryItem(plankDef.id, null);
      if (invItem && invItem.count > 0) {
        fuelName = plankName;
      }
    }
  }
  if (!fuelName) {
    bot.chat("No fuel available, cannot cook.");
    return;
  }
  bot.chat(`Using ${fuelName} as fuel.`);

  // 4) Smelt the porkchops
  bot.chat(`Cooking ${targetCount} porkchops...`);
  await smeltItem(bot, "porkchop", fuelName, targetCount);
  cookedCount = bot.inventory.items().filter(i => i.name === "cooked_porkchop").reduce((s, i) => s + i.count, 0);
  bot.chat(`Done. Total cooked porkchops: ${cookedCount}.`);
}