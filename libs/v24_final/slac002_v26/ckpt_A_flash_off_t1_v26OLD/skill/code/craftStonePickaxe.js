async function craftStonePickaxe(bot) {
  const plankNames = ["oak_planks", "birch_planks", "spruce_planks", "jungle_planks", "acacia_planks", "dark_oak_planks", "mangrove_planks", "cherry_planks", "bamboo_planks", "crimson_planks", "warped_planks"];
  const logNames = ["oak_log", "birch_log", "spruce_log", "jungle_log", "acacia_log", "dark_oak_log", "mangrove_log", "cherry_log"];

  // 1) Ensure we have 3 cobblestone
  let cobbleCount = bot.inventory.items().filter(i => i.name === "cobblestone").reduce((s, i) => s + i.count, 0);
  if (cobbleCount < 3) {
    bot.chat("Not enough cobblestone, mining more...");
    await mineBlock(bot, "cobblestone", 3 - cobbleCount);
    cobbleCount = bot.inventory.items().filter(i => i.name === "cobblestone").reduce((s, i) => s + i.count, 0);
    if (cobbleCount < 3) {
      bot.chat("Failed to obtain enough cobblestone.");
      return;
    }
  }

  // 2) Ensure we have 2 sticks
  let stickCount = bot.inventory.items().filter(i => i.name === "stick").reduce((s, i) => s + i.count, 0);
  if (stickCount < 2) {
    // Need planks to craft sticks (2 planks -> 4 sticks)
    let plankCount = bot.inventory.items().filter(i => plankNames.includes(i.name)).reduce((s, i) => s + i.count, 0);
    if (plankCount < 2) {
      // Craft planks from logs
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
      bot.chat("Failed to obtain enough sticks.");
      return;
    }
  }

  // 3) Ensure a crafting table block is nearby
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

  // 4) Craft the stone pickaxe
  bot.chat("Crafting stone pickaxe...");
  await craftItem(bot, "stone_pickaxe", 1);
  bot.chat("Crafted 1 stone pickaxe.");
}