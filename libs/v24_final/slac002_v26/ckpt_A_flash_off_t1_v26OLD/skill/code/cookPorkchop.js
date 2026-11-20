async function cookPorkchop(bot) {
  const targetCount = 1;

  // 0) Already done?
  let cookedCount = bot.inventory.items().filter(i => i.name === "cooked_porkchop").reduce((s, i) => s + i.count, 0);
  if (cookedCount >= targetCount) {
    bot.chat(`Already have ${cookedCount} cooked porkchops.`);
    return;
  }

  // 1) Ensure we have a raw porkchop
  let porkchopCount = bot.inventory.items().filter(i => i.name === "porkchop").reduce((s, i) => s + i.count, 0);
  if (porkchopCount < targetCount) {
    bot.chat(`Not enough raw porkchops (${porkchopCount}/${targetCount}).`);
    return;
  }

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
    const offsets = [new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1)];
    for (const off of offsets) {
      const pos = bot.entity.position.offset(off.x, off.y, off.z).floored();
      try {
        await placeItem(bot, "furnace", pos);
      } catch (e) {
        continue;
      }
      furnaceBlock = bot.findBlock({
        matching: mcData.blocksByName.furnace.id,
        maxDistance: 32
      });
      if (furnaceBlock) break;
    }
    if (!furnaceBlock) {
      bot.chat("Failed to place furnace.");
      return;
    }
  }

  // 3) Determine a fuel source
  const plankNames = ["oak_planks", "birch_planks", "spruce_planks", "jungle_planks", "acacia_planks", "dark_oak_planks", "mangrove_planks", "cherry_planks", "bamboo_planks", "crimson_planks", "warped_planks"];
  const logNames = ["oak_log", "birch_log", "spruce_log", "jungle_log", "acacia_log", "dark_oak_log", "mangrove_log", "cherry_log"];
  function findFuel() {
    const invNames = new Set(bot.inventory.items().map(i => i.name));
    if (invNames.has("coal")) return "coal";
    if (invNames.has("charcoal")) return "charcoal";
    for (const n of plankNames) if (invNames.has(n)) return n;
    for (const n of logNames) if (invNames.has(n)) return n;
    return null;
  }
  let fuelName = findFuel();

  // 3a) If no fuel, try to mine coal ore
  if (!fuelName) {
    bot.chat("No fuel in inventory, looking for coal ore...");
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
        fuelName = findFuel();
      }
    }
  }

  // 3b) If still no fuel, mine a log and craft planks
  if (!fuelName) {
    bot.chat("No coal found, looking for a log to make planks...");
    let logBlock = bot.findBlock({
      matching: b => logNames.includes(b.name),
      maxDistance: 32
    });
    if (!logBlock) {
      const directions = [new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1)];
      const dir = directions[Math.floor(Math.random() * directions.length)];
      logBlock = await exploreUntil(bot, dir, 60, () => bot.findBlock({
        matching: b => logNames.includes(b.name),
        maxDistance: 32
      }));
    }
    if (logBlock) {
      bot.chat(`Mining ${logBlock.name}...`);
      await mineBlock(bot, logBlock.name, 1);
      const plankName = logBlock.name.replace("_log", "_planks");
      await craftItem(bot, plankName, 1);
      fuelName = findFuel();
    }
  }
  if (!fuelName) {
    bot.chat("Could not obtain any valid fuel. Cannot cook.");
    return;
  }
  bot.chat(`Using ${fuelName} as fuel.`);

  // 4) Smelt the porkchop
  bot.chat(`Cooking ${targetCount} porkchop...`);
  await smeltItem(bot, "porkchop", fuelName, targetCount);
  cookedCount = bot.inventory.items().filter(i => i.name === "cooked_porkchop").reduce((s, i) => s + i.count, 0);
  bot.chat(`Done. Total cooked porkchops: ${cookedCount}.`);
}