async function craftBow(bot) {
  // 1) Ensure we have 3 strings
  let stringCount = bot.inventory.items().filter(i => i.name === "string").reduce((s, i) => s + i.count, 0);
  bot.chat(`Current string count: ${stringCount}`);
  let attempts = 0;
  while (stringCount < 3 && attempts < 10) {
    attempts++;
    let spider = bot.nearestEntity(entity => entity.name === "spider" && entity.position.distanceTo(bot.entity.position) < 32);
    if (!spider) {
      bot.chat("No spider nearby, exploring...");
      const directions = [new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1), new Vec3(1, 0, 1), new Vec3(-1, 0, -1)];
      const dir = directions[Math.floor(Math.random() * directions.length)];
      spider = await exploreUntil(bot, dir, 60, () => {
        return bot.nearestEntity(entity => entity.name === "spider" && entity.position.distanceTo(bot.entity.position) < 32);
      });
    }
    if (!spider) {
      bot.chat("Failed to find a spider.");
      break;
    }
    bot.chat("Killing spider...");
    await killMob(bot, "spider", 300);
    stringCount = bot.inventory.items().filter(i => i.name === "string").reduce((s, i) => s + i.count, 0);
    bot.chat(`String count now: ${stringCount}`);
  }
  if (stringCount < 3) {
    bot.chat(`Not enough string (have ${stringCount}, need 3).`);
    return;
  }

  // 2) Ensure we have 3 sticks
  const plankNames = ["oak_planks", "birch_planks", "spruce_planks", "jungle_planks", "acacia_planks", "dark_oak_planks", "mangrove_planks", "cherry_planks", "bamboo_planks", "crimson_planks", "warped_planks"];
  const logNames = ["oak_log", "birch_log", "spruce_log", "jungle_log", "acacia_log", "dark_oak_log", "mangrove_log", "cherry_log"];
  let stickCount = bot.inventory.items().filter(i => i.name === "stick").reduce((s, i) => s + i.count, 0);
  if (stickCount < 3) {
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
          const directions = [new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1)];
          const dir = directions[Math.floor(Math.random() * directions.length)];
          logBlock = await exploreUntil(bot, dir, 60, () => bot.findBlock({
            matching: b => logNames.includes(b.name),
            maxDistance: 32
          }));
        }
        if (!logBlock) {
          bot.chat("Failed to find a log.");
          return;
        }
        await mineBlock(bot, logBlock.name, 1);
        logItem = bot.inventory.items().find(i => logNames.includes(i.name));
      }
      if (!logItem) {
        bot.chat("Failed to obtain a log.");
        return;
      }
      const plankName = logItem.name.replace("_log", "_planks");
      bot.chat(`Crafting ${plankName}...`);
      await craftItem(bot, plankName, 1);
      plankCount = bot.inventory.items().filter(i => plankNames.includes(i.name)).reduce((s, i) => s + i.count, 0);
    }
    if (plankCount < 2) {
      bot.chat("Not enough planks to craft sticks.");
      return;
    }
    bot.chat("Crafting sticks...");
    await craftItem(bot, "stick", 1);
    stickCount = bot.inventory.items().filter(i => i.name === "stick").reduce((s, i) => s + i.count, 0);
  }
  if (stickCount < 3) {
    bot.chat(`Not enough sticks (have ${stickCount}, need 3).`);
    return;
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

  // 4) Craft the bow
  bot.chat("Crafting bow...");
  await craftItem(bot, "bow", 1);
  const bowCount = bot.inventory.items().filter(i => i.name === "bow").reduce((s, i) => s + i.count, 0);
  bot.chat(`Done. Total bows: ${bowCount}.`);
}