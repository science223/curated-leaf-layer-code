async function smeltRawIron(bot) {
  const targetCount = 2;

  // 1) Check raw_iron in inventory
  const rawIronCount = bot.inventory.items().filter(i => i.name === "raw_iron").reduce((s, i) => s + i.count, 0);
  if (rawIronCount < targetCount) {
    bot.chat(`Not enough raw_iron (${rawIronCount}/${targetCount}). Cannot smelt.`);
    return;
  }

  // 2) Ensure we have fuel (coal or charcoal); mine coal_ore if needed
  let fuelName = null;
  let coalCount = bot.inventory.items().filter(i => i.name === "coal").reduce((s, i) => s + i.count, 0);
  let charcoalCount = bot.inventory.items().filter(i => i.name === "charcoal").reduce((s, i) => s + i.count, 0);
  if (coalCount > 0) {
    fuelName = "coal";
  } else if (charcoalCount > 0) {
    fuelName = "charcoal";
  } else {
    bot.chat("No fuel found, mining coal ore...");
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
    if (!coalBlock) {
      bot.chat("Failed to find coal ore for fuel. Cannot smelt.");
      return;
    }
    await mineBlock(bot, "coal_ore", 1);
    coalCount = bot.inventory.items().filter(i => i.name === "coal").reduce((s, i) => s + i.count, 0);
    if (coalCount > 0) {
      fuelName = "coal";
    } else {
      bot.chat("Failed to obtain coal. Cannot smelt.");
      return;
    }
  }

  // 3) Ensure a furnace block is nearby; otherwise place one from inventory
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

  // 4) Smelt the raw iron
  bot.chat(`Smelting ${targetCount} raw_iron using ${fuelName}...`);
  await smeltItem(bot, "raw_iron", fuelName, targetCount);
  const ingotCount = bot.inventory.items().filter(i => i.name === "iron_ingot").reduce((s, i) => s + i.count, 0);
  bot.chat(`Done. Total iron_ingot: ${ingotCount}.`);
}