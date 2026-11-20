async function smeltRawCopper(bot) {
  const targetCount = 11;

  // 1) Ensure we have enough raw_copper
  let rawCopperCount = bot.inventory.items().filter(i => i.name === "raw_copper").reduce((s, i) => s + i.count, 0);
  if (rawCopperCount < targetCount) {
    bot.chat(`Not enough raw_copper (${rawCopperCount}/${targetCount}). Mining copper ore...`);
    const pickaxeNames = ["stone_pickaxe", "iron_pickaxe", "diamond_pickaxe", "netherite_pickaxe", "golden_pickaxe"];
    const hasPickaxe = bot.inventory.items().some(i => pickaxeNames.includes(i.name));
    if (!hasPickaxe) {
      bot.chat("No suitable pickaxe available. Cannot mine copper ore.");
      return;
    }
    let attempts = 0;
    while (rawCopperCount < targetCount && attempts < 10) {
      attempts++;
      let copperBlock = bot.findBlock({
        matching: mcData.blocksByName["copper_ore"].id,
        maxDistance: 32
      });
      if (!copperBlock) {
        bot.chat("No copper ore nearby, exploring...");
        const directions = [new Vec3(0, -1, 0), new Vec3(1, -1, 0), new Vec3(-1, -1, 0), new Vec3(0, -1, 1), new Vec3(0, -1, -1)];
        const dir = directions[Math.floor(Math.random() * directions.length)];
        copperBlock = await exploreUntil(bot, dir, 60, () => bot.findBlock({
          matching: mcData.blocksByName["copper_ore"].id,
          maxDistance: 32
        }));
      }
      if (!copperBlock) {
        bot.chat("Failed to find copper ore.");
        break;
      }
      bot.chat("Found copper ore, mining...");
      await mineBlock(bot, "copper_ore", Math.max(1, Math.ceil((targetCount - rawCopperCount) / 2)));
      rawCopperCount = bot.inventory.items().filter(i => i.name === "raw_copper").reduce((s, i) => s + i.count, 0);
    }
    if (rawCopperCount < targetCount) {
      bot.chat(`Still not enough raw_copper (${rawCopperCount}/${targetCount}).`);
      return;
    }
  }

  // 2) Ensure a furnace block is nearby; otherwise place one from inventory
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  if (!furnaceBlock) {
    const furnaceItem = bot.inventory.findInventoryItem(mcData.itemsByName["furnace"].id);
    if (!furnaceItem) {
      bot.chat("No furnace item in inventory and no furnace nearby. Cannot smelt.");
      return;
    }
    bot.chat("Placing furnace...");
    const offsets = [new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1), new Vec3(1, 0, 1), new Vec3(-1, 0, -1)];
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

  // 3) Determine a reliable fuel
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

  // If no good fuel, mine coal_ore
  if (!fuelName) {
    bot.chat("No good fuel found. Looking for coal ore...");
    let coalBlock = bot.findBlock({
      matching: mcData.blocksByName["coal_ore"].id,
      maxDistance: 32
    });
    if (!coalBlock) {
      const directions = [new Vec3(0, -1, 0), new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1)];
      const dir = directions[Math.floor(Math.random() * directions.length)];
      coalBlock = await exploreUntil(bot, dir, 60, () => bot.findBlock({
        matching: mcData.blocksByName["coal_ore"].id,
        maxDistance: 32
      }));
    }
    if (coalBlock) {
      bot.chat("Found coal ore, mining...");
      await mineBlock(bot, "coal_ore", 1);
      fuelName = findFuel();
    }
  }

  // If still no fuel, get a log and craft planks
  if (!fuelName) {
    bot.chat("No coal found. Looking for a log to make planks...");
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
    bot.chat("Could not obtain any valid fuel. Cannot smelt.");
    return;
  }

  // 4) Smelt the raw copper
  bot.chat(`Smelting ${targetCount} raw_copper using ${fuelName}...`);
  await smeltItem(bot, "raw_copper", fuelName, targetCount);
  const ingotCount = bot.inventory.items().filter(i => i.name === "copper_ingot").reduce((s, i) => s + i.count, 0);
  bot.chat(`Done. Total copper_ingot: ${ingotCount}.`);
}