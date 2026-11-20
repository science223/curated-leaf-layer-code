async function craftIronPickaxe(bot) {
  const plankNames = ["oak_planks", "birch_planks", "spruce_planks", "jungle_planks", "acacia_planks", "dark_oak_planks", "mangrove_planks", "cherry_planks", "bamboo_planks", "crimson_planks", "warped_planks"];
  const logNames = ["oak_log", "birch_log", "spruce_log", "jungle_log", "acacia_log", "dark_oak_log", "mangrove_log", "cherry_log"];

  // 1) Ensure we have 3 iron ingots
  let ironIngotCount = bot.inventory.items().filter(i => i.name === "iron_ingot").reduce((s, i) => s + i.count, 0);
  if (ironIngotCount < 3) {
    let rawIronCount = bot.inventory.items().filter(i => i.name === "raw_iron").reduce((s, i) => s + i.count, 0);
    if (rawIronCount < 3 - ironIngotCount) {
      bot.chat("Not enough raw iron, mining iron ore...");
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
        bot.chat("Failed to find iron ore.");
        return;
      }
      await mineBlock(bot, "iron_ore", 3 - ironIngotCount);
      rawIronCount = bot.inventory.items().filter(i => i.name === "raw_iron").reduce((s, i) => s + i.count, 0);
    }
    // Ensure we have a furnace
    let furnaceBlock = bot.findBlock({
      matching: mcData.blocksByName.furnace.id,
      maxDistance: 32
    });
    if (!furnaceBlock) {
      const furnaceItem = bot.inventory.findInventoryItem(mcData.itemsByName["furnace"].id);
      if (!furnaceItem) {
        bot.chat("No furnace available.");
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
    // Ensure we have fuel (coal or charcoal)
    let fuelName = null;
    if (bot.inventory.items().some(i => i.name === "coal")) fuelName = "coal";else if (bot.inventory.items().some(i => i.name === "charcoal")) fuelName = "charcoal";
    if (!fuelName) {
      bot.chat("No fuel (coal/charcoal) available.");
      return;
    }
    const toSmelt = Math.min(rawIronCount, 3 - ironIngotCount);
    bot.chat(`Smelting ${toSmelt} raw iron...`);
    await smeltItem(bot, "raw_iron", fuelName, toSmelt);
    ironIngotCount = bot.inventory.items().filter(i => i.name === "iron_ingot").reduce((s, i) => s + i.count, 0);
    if (ironIngotCount < 3) {
      bot.chat(`Still not enough iron ingots (have ${ironIngotCount}).`);
      return;
    }
  }

  // 2) Ensure we have 2 sticks
  let stickCount = bot.inventory.items().filter(i => i.name === "stick").reduce((s, i) => s + i.count, 0);
  if (stickCount < 2) {
    let plankCount = bot.inventory.items().filter(i => plankNames.includes(i.name)).reduce((s, i) => s + i.count, 0);
    if (plankCount < 2) {
      let logItem = bot.inventory.items().find(i => logNames.includes(i.name));
      if (!logItem) {
        bot.chat("No logs, mining one...");
        let logBlock = bot.findBlock({
          matching: b => logNames.includes(b.name),
          maxDistance: 32
        });
        if (!logBlock) {
          const dir = new Vec3(1, 0, 0);
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
      if (!logItem) {
        bot.chat("Failed to obtain a log for planks.");
        return;
      }
      const plankName = logItem.name.replace("_log", "_planks");
      await craftItem(bot, plankName, 1);
    }
    bot.chat("Crafting sticks...");
    await craftItem(bot, "stick", 1);
    stickCount = bot.inventory.items().filter(i => i.name === "stick").reduce((s, i) => s + i.count, 0);
    if (stickCount < 2) {
      bot.chat(`Still not enough sticks (have ${stickCount}).`);
      return;
    }
  }

  // 3) Ensure a crafting table block is nearby; place one if needed
  let tableBlock = bot.findBlock({
    matching: mcData.blocksByName.crafting_table.id,
    maxDistance: 32
  });
  if (!tableBlock) {
    let tableItem = bot.inventory.findInventoryItem(mcData.itemsByName["crafting_table"].id);
    if (!tableItem) {
      bot.chat("No crafting table item, crafting one...");
      let plankCount = bot.inventory.items().filter(i => plankNames.includes(i.name)).reduce((s, i) => s + i.count, 0);
      if (plankCount < 4) {
        let logItem = bot.inventory.items().find(i => logNames.includes(i.name));
        if (!logItem) {
          bot.chat("No logs, mining one...");
          let logBlock = bot.findBlock({
            matching: b => logNames.includes(b.name),
            maxDistance: 32
          });
          if (!logBlock) {
            const dir = new Vec3(1, 0, 0);
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
        if (!logItem) {
          bot.chat("Failed to obtain a log for crafting table.");
          return;
        }
        const plankName = logItem.name.replace("_log", "_planks");
        await craftItem(bot, plankName, 1);
      }
      await craftItem(bot, "crafting_table", 1);
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

  // 4) Craft the iron pickaxe
  bot.chat("Crafting iron pickaxe...");
  await craftItem(bot, "iron_pickaxe", 1);
  bot.chat("Crafted 1 iron pickaxe.");
}