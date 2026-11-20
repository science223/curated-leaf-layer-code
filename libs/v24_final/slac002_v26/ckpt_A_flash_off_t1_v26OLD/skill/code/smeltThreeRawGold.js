async function smeltThreeRawGold(bot) {
  const targetCount = 3;

  // 1) Ensure we have 3 raw_gold
  let rawGoldCount = bot.inventory.items().filter(i => i.name === "raw_gold").reduce((s, i) => s + i.count, 0);
  if (rawGoldCount < targetCount) {
    bot.chat(`Not enough raw_gold (${rawGoldCount}/${targetCount}). Mining gold ore...`);
    const pickaxeNames = ["iron_pickaxe", "diamond_pickaxe", "netherite_pickaxe"];
    const hasPickaxe = bot.inventory.items().some(i => pickaxeNames.includes(i.name));
    if (!hasPickaxe) {
      bot.chat("No suitable pickaxe available. Cannot mine gold ore.");
      return;
    }
    let attempts = 0;
    while (rawGoldCount < targetCount && attempts < 10) {
      attempts++;
      let goldBlock = bot.findBlock({
        matching: b => b.name === "gold_ore" || b.name === "deepslate_gold_ore",
        maxDistance: 32
      });
      if (!goldBlock) {
        const directions = [new Vec3(0, -1, 0), new Vec3(1, -1, 0), new Vec3(-1, -1, 0), new Vec3(0, -1, 1), new Vec3(0, -1, -1)];
        const dir = directions[Math.floor(Math.random() * directions.length)];
        goldBlock = await exploreUntil(bot, dir, 60, () => bot.findBlock({
          matching: b => b.name === "gold_ore" || b.name === "deepslate_gold_ore",
          maxDistance: 32
        }));
      }
      if (!goldBlock) {
        bot.chat("Failed to find gold ore.");
        break;
      }
      await mineBlock(bot, goldBlock.name, targetCount - rawGoldCount);
      rawGoldCount = bot.inventory.items().filter(i => i.name === "raw_gold").reduce((s, i) => s + i.count, 0);
    }
    if (rawGoldCount < targetCount) {
      bot.chat(`Still not enough raw_gold (${rawGoldCount}/${targetCount}).`);
      return;
    }
  }
  bot.chat(`Have ${rawGoldCount} raw_gold, smelting ${targetCount}.`);

  // 2) Determine a reliable fuel
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
  if (!fuelName) {
    bot.chat("No good fuel found. Looking for coal ore...");
    let coalBlock = bot.findBlock({
      matching: b => b.name === "coal_ore" || b.name === "deepslate_coal_ore",
      maxDistance: 32
    });
    if (!coalBlock) {
      const directions = [new Vec3(0, -1, 0), new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1)];
      const dir = directions[Math.floor(Math.random() * directions.length)];
      coalBlock = await exploreUntil(bot, dir, 60, () => bot.findBlock({
        matching: b => b.name === "coal_ore" || b.name === "deepslate_coal_ore",
        maxDistance: 32
      }));
    }
    if (coalBlock) {
      bot.chat("Found coal ore, mining...");
      await mineBlock(bot, coalBlock.name, 1);
      fuelName = findFuel();
    }
  }
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

  // 3) Ensure a furnace is nearby; otherwise place one from inventory
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

  // 4) Re-verify fuel is actually in inventory right before smelting
  const fuelItem = bot.inventory.findInventoryItem(mcData.itemsByName[fuelName].id);
  if (!fuelItem) {
    bot.chat(`Fuel ${fuelName} disappeared from inventory. Aborting.`);
    return;
  }

  // 5) Smelt the raw gold
  bot.chat(`Smelting ${targetCount} raw_gold using ${fuelName}...`);
  await smeltItem(bot, "raw_gold", fuelName, targetCount);
  const ingotCount = bot.inventory.items().filter(i => i.name === "gold_ingot").reduce((s, i) => s + i.count, 0);
  bot.chat(`Done. Total gold_ingot: ${ingotCount}.`);
}