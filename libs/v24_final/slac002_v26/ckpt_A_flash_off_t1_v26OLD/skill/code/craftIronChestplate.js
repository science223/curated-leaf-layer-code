async function craftIronChestplate(bot) {
  const targetIngots = 8;

  // 1) Count current iron ingots
  let ironIngotCount = bot.inventory.items().filter(i => i.name === "iron_ingot").reduce((s, i) => s + i.count, 0);
  bot.chat(`Current iron ingots: ${ironIngotCount}`);

  // 2) If not enough, mine iron ore and smelt
  if (ironIngotCount < targetIngots) {
    const needed = targetIngots - ironIngotCount;
    bot.chat(`Need ${needed} more iron ingots.`);

    // Ensure we have a suitable pickaxe
    const pickaxeNames = ["stone_pickaxe", "iron_pickaxe", "diamond_pickaxe", "netherite_pickaxe"];
    const hasPickaxe = bot.inventory.items().some(i => pickaxeNames.includes(i.name));
    if (!hasPickaxe) {
      bot.chat("No suitable pickaxe available.");
      return;
    }

    // Count raw iron we already have
    let rawIronCount = bot.inventory.items().filter(i => i.name === "raw_iron").reduce((s, i) => s + i.count, 0);
    let rawIronNeeded = needed - rawIronCount;

    // Mine iron ore if needed
    if (rawIronNeeded > 0) {
      let attempts = 0;
      while (rawIronNeeded > 0 && attempts < 10) {
        attempts++;
        let ironBlock = bot.findBlock({
          matching: mcData.blocksByName["iron_ore"].id,
          maxDistance: 32
        });
        if (!ironBlock) {
          bot.chat("No iron ore nearby, exploring...");
          const directions = [new Vec3(0, -1, 0), new Vec3(1, -1, 0), new Vec3(-1, -1, 0), new Vec3(0, -1, 1), new Vec3(0, -1, -1)];
          const dir = directions[Math.floor(Math.random() * directions.length)];
          ironBlock = await exploreUntil(bot, dir, 60, () => {
            return bot.findBlock({
              matching: mcData.blocksByName["iron_ore"].id,
              maxDistance: 32
            });
          });
        }
        if (!ironBlock) {
          bot.chat("Failed to find iron ore.");
          return;
        }
        bot.chat("Mining iron ore...");
        await mineBlock(bot, "iron_ore", rawIronNeeded);
        rawIronCount = bot.inventory.items().filter(i => i.name === "raw_iron").reduce((s, i) => s + i.count, 0);
        rawIronNeeded = needed - rawIronCount;
      }
      if (rawIronCount < needed) {
        bot.chat(`Not enough raw iron (${rawIronCount}/${needed}).`);
        return;
      }
    }

    // Ensure we have fuel (coal or charcoal)
    let fuelName = null;
    if (bot.inventory.items().some(i => i.name === "coal")) {
      fuelName = "coal";
    } else if (bot.inventory.items().some(i => i.name === "charcoal")) {
      fuelName = "charcoal";
    }
    if (!fuelName) {
      bot.chat("No fuel, mining coal ore...");
      let coalBlock = bot.findBlock({
        matching: mcData.blocksByName["coal_ore"].id,
        maxDistance: 32
      });
      if (!coalBlock) {
        const directions = [new Vec3(0, -1, 0), new Vec3(1, -1, 0), new Vec3(-1, -1, 0)];
        const dir = directions[Math.floor(Math.random() * directions.length)];
        coalBlock = await exploreUntil(bot, dir, 60, () => {
          return bot.findBlock({
            matching: mcData.blocksByName["coal_ore"].id,
            maxDistance: 32
          });
        });
      }
      if (!coalBlock) {
        bot.chat("Failed to find coal ore.");
        return;
      }
      await mineBlock(bot, "coal_ore", 1);
      if (bot.inventory.items().some(i => i.name === "coal")) {
        fuelName = "coal";
      }
    }
    if (!fuelName) {
      bot.chat("Still no fuel available.");
      return;
    }

    // Ensure we have a furnace placed
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

    // Smelt raw iron into iron ingots
    bot.chat(`Smelting ${needed} raw iron...`);
    await smeltItem(bot, "raw_iron", fuelName, needed);
    ironIngotCount = bot.inventory.items().filter(i => i.name === "iron_ingot").reduce((s, i) => s + i.count, 0);
    if (ironIngotCount < targetIngots) {
      bot.chat(`Still not enough iron ingots (${ironIngotCount}/${targetIngots}).`);
      return;
    }
  }

  // 3) Ensure a crafting table block is nearby
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

  // 4) Craft the iron chestplate
  bot.chat("Crafting iron chestplate...");
  await craftItem(bot, "iron_chestplate", 1);
  const chestplateCount = bot.inventory.items().filter(i => i.name === "iron_chestplate").reduce((s, i) => s + i.count, 0);
  bot.chat(`Done. Total iron chestplates: ${chestplateCount}.`);
}