async function smeltRawIron(bot) {
  const targetCount = 1;

  // 1) Check raw_iron in inventory
  let rawIronCount = bot.inventory.items().filter(i => i.name === "raw_iron").reduce((s, i) => s + i.count, 0);
  if (rawIronCount < targetCount) {
    bot.chat(`Not enough raw_iron (${rawIronCount}/${targetCount}). Mining iron ore...`);
    let ironBlock = bot.findBlock({
      matching: mcData.blocksByName["iron_ore"].id,
      maxDistance: 32
    });
    if (!ironBlock) {
      const directions = [new Vec3(0, -1, 0), new Vec3(1, -1, 0), new Vec3(-1, -1, 0), new Vec3(0, -1, 1), new Vec3(0, -1, -1)];
      const dir = directions[Math.floor(Math.random() * directions.length)];
      ironBlock = await exploreUntil(bot, dir, 60, () => bot.findBlock({
        matching: mcData.blocksByName["iron_ore"].id,
        maxDistance: 32
      }));
    }
    if (!ironBlock) {
      bot.chat("Failed to find iron ore. Cannot smelt.");
      return;
    }
    await mineBlock(bot, "iron_ore", targetCount - rawIronCount);
    rawIronCount = bot.inventory.items().filter(i => i.name === "raw_iron").reduce((s, i) => s + i.count, 0);
    if (rawIronCount < targetCount) {
      bot.chat(`Still not enough raw_iron (${rawIronCount}/${targetCount}).`);
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

  // 3) Determine a valid fuel from inventory
  const fuelPriority = ["coal", "charcoal", "oak_planks", "birch_planks", "spruce_planks", "jungle_planks", "acacia_planks", "dark_oak_planks", "mangrove_planks", "cherry_planks", "bamboo_planks", "crimson_planks", "warped_planks", "stick", "oak_log", "birch_log", "spruce_log", "jungle_log", "acacia_log", "dark_oak_log", "mangrove_log", "cherry_log"];
  let fuelName = null;
  for (const name of fuelPriority) {
    if (bot.inventory.items().some(i => i.name === name)) {
      fuelName = name;
      break;
    }
  }
  if (!fuelName) {
    bot.chat("No valid fuel found in inventory. Cannot smelt.");
    return;
  }

  // 4) Smelt the raw iron
  bot.chat(`Smelting ${targetCount} raw_iron using ${fuelName}...`);
  await smeltItem(bot, "raw_iron", fuelName, targetCount);
  const ingotCount = bot.inventory.items().filter(i => i.name === "iron_ingot").reduce((s, i) => s + i.count, 0);
  bot.chat(`Done. Total iron_ingot: ${ingotCount}.`);
}