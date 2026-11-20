async function craftDiamondSword(bot) {
  const countItem = name => bot.inventory.items().filter(i => i.name === name).reduce((s, i) => s + i.count, 0);
  const plankNames = ["oak_planks", "birch_planks", "spruce_planks", "jungle_planks", "acacia_planks", "dark_oak_planks", "mangrove_planks", "cherry_planks", "bamboo_planks", "crimson_planks", "warped_planks"];
  const logNames = ["oak_log", "birch_log", "spruce_log", "jungle_log", "acacia_log", "dark_oak_log", "mangrove_log", "cherry_log"];
  const junkNames = ["cobblestone", "andesite", "diorite", "granite", "gravel", "dirt", "sand", "copper_block", "magma_block"];

  // Helper: free inventory space so mined drops can be picked up
  async function freeSpace(minSlots = 2) {
    let attempts = 0;
    while (bot.inventory.emptySlotCount() < minSlots && attempts < 20) {
      attempts++;
      const junk = bot.inventory.items().find(i => junkNames.includes(i.name));
      if (!junk) break;
      bot.chat(`Inventory tight, dropping ${junk.name} x${junk.count} to make room.`);
      await bot.toss(junk.type, null, junk.count);
    }
  }

  // Helper: ensure we have a log item in inventory (mine one if needed)
  async function ensureLog() {
    let logItem = bot.inventory.items().find(i => logNames.includes(i.name));
    if (logItem) return logItem;
    bot.chat("No logs, searching for a tree...");
    let logBlock = bot.findBlock({
      matching: b => logNames.includes(b.name),
      maxDistance: 32
    });
    if (!logBlock) {
      const directions = [new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1), new Vec3(1, 0, 1), new Vec3(-1, 0, -1)];
      const dir = directions[Math.floor(Math.random() * directions.length)];
      logBlock = await exploreUntil(bot, dir, 60, () => bot.findBlock({
        matching: b => logNames.includes(b.name),
        maxDistance: 32
      }));
    }
    if (!logBlock) return null;
    await mineBlock(bot, logBlock.name, 1);
    return bot.inventory.items().find(i => logNames.includes(i.name)) || null;
  }

  // Helper: ensure we have at least `n` planks (any wood type)
  async function ensurePlanks(n) {
    let plankCount = bot.inventory.items().filter(i => plankNames.includes(i.name)).reduce((s, i) => s + i.count, 0);
    while (plankCount < n) {
      const logItem = await ensureLog();
      if (!logItem) return false;
      const plankName = logItem.name.replace("_log", "_planks");
      await craftItem(bot, plankName, 1);
      plankCount = bot.inventory.items().filter(i => plankNames.includes(i.name)).reduce((s, i) => s + i.count, 0);
    }
    return true;
  }

  // 1) Ensure we have 2 diamonds
  let diamondCount = countItem("diamond");
  if (diamondCount < 2) {
    bot.chat(`Have ${diamondCount} diamond(s), need 2. Mining more...`);
    const goodPickaxes = ["iron_pickaxe", "diamond_pickaxe", "netherite_pickaxe"];
    if (!bot.inventory.items().some(i => goodPickaxes.includes(i.name))) {
      bot.chat("No suitable pickaxe to mine diamond ore.");
      return;
    }
    await freeSpace(2);
    let attempts = 0;
    while (countItem("diamond") < 2 && attempts < 20) {
      attempts++;
      let diamondBlock = bot.findBlock({
        matching: b => b.name === "diamond_ore" || b.name === "deepslate_diamond_ore",
        maxDistance: 32
      });
      if (!diamondBlock) {
        bot.chat(`No diamond ore nearby, exploring downward (attempt ${attempts})...`);
        const directions = [new Vec3(0, -1, 0), new Vec3(1, -1, 0), new Vec3(-1, -1, 0), new Vec3(0, -1, 1), new Vec3(0, -1, -1), new Vec3(1, -1, 1), new Vec3(-1, -1, -1)];
        const dir = directions[Math.floor(Math.random() * directions.length)];
        diamondBlock = await exploreUntil(bot, dir, 60, () => bot.findBlock({
          matching: b => b.name === "diamond_ore" || b.name === "deepslate_diamond_ore",
          maxDistance: 32
        }));
      }
      if (!diamondBlock) {
        bot.chat("Failed to find diamond ore this attempt.");
        continue;
      }
      bot.chat(`Found ${diamondBlock.name}, mining...`);
      await mineBlock(bot, diamondBlock.name, 1);
      if (bot.inventory.emptySlotCount() < 1) {
        await freeSpace(1);
      }
    }
    diamondCount = countItem("diamond");
    if (diamondCount < 2) {
      bot.chat(`Failed to obtain enough diamonds (have ${diamondCount}).`);
      return;
    }
  }
  bot.chat(`Have ${diamondCount} diamonds.`);

  // 2) Ensure we have 1 stick
  if (countItem("stick") < 1) {
    bot.chat("No sticks, crafting some...");
    if (!(await ensurePlanks(2))) {
      bot.chat("Failed to obtain planks for sticks.");
      return;
    }
    await craftItem(bot, "stick", 1);
    if (countItem("stick") < 1) {
      bot.chat("Failed to obtain sticks.");
      return;
    }
  }
  bot.chat(`Have ${countItem("stick")} stick(s).`);

  // 3) Ensure a crafting table block is nearby
  let tableBlock = bot.findBlock({
    matching: mcData.blocksByName.crafting_table.id,
    maxDistance: 32
  });
  if (!tableBlock) {
    let tableItem = bot.inventory.findInventoryItem(mcData.itemsByName["crafting_table"].id);
    if (!tableItem) {
      bot.chat("No crafting table item, crafting one...");
      if (!(await ensurePlanks(4))) {
        bot.chat("Failed to obtain planks for crafting table.");
        return;
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

  // 4) Craft the diamond sword
  bot.chat("Crafting diamond sword...");
  await craftItem(bot, "diamond_sword", 1);

  // 5) Verify
  const swordCount = countItem("diamond_sword");
  bot.chat(`Done. Total diamond swords: ${swordCount}.`);
}