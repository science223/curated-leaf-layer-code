async function craftTorches(bot) {
  const plankNames = ["oak_planks", "birch_planks", "spruce_planks", "jungle_planks", "acacia_planks", "dark_oak_planks", "mangrove_planks", "cherry_planks", "bamboo_planks", "crimson_planks", "warped_planks"];
  const logNames = ["oak_log", "birch_log", "spruce_log", "jungle_log", "acacia_log", "dark_oak_log", "mangrove_log", "cherry_log"];

  // 1) Ensure we have at least 1 stick
  let stickCount = bot.inventory.items().filter(i => i.name === "stick").reduce((s, i) => s + i.count, 0);
  if (stickCount < 1) {
    bot.chat("Not enough sticks, crafting some...");
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
    await craftItem(bot, "stick", 1);
    stickCount = bot.inventory.items().filter(i => i.name === "stick").reduce((s, i) => s + i.count, 0);
    if (stickCount < 1) {
      bot.chat("Failed to obtain sticks.");
      return;
    }
  }

  // 2) Ensure we have at least 1 coal (or charcoal)
  let coalCount = bot.inventory.items().filter(i => i.name === "coal" || i.name === "charcoal").reduce((s, i) => s + i.count, 0);
  if (coalCount < 1) {
    bot.chat("No coal, mining coal ore...");
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
      bot.chat("Failed to find coal ore.");
      return;
    }
    await mineBlock(bot, "coal_ore", 1);
    coalCount = bot.inventory.items().filter(i => i.name === "coal" || i.name === "charcoal").reduce((s, i) => s + i.count, 0);
    if (coalCount < 1) {
      bot.chat("Failed to obtain coal.");
      return;
    }
  }

  // 3) Craft torches (1 recipe yields 4 torches)
  bot.chat("Crafting torches...");
  await craftItem(bot, "torch", 1);
  const torchCount = bot.inventory.items().filter(i => i.name === "torch").reduce((s, i) => s + i.count, 0);
  bot.chat(`Done. Total torches: ${torchCount}.`);
}