async function mineOneDiamondOre(bot) {
  const goodPickaxes = ["iron_pickaxe", "diamond_pickaxe", "netherite_pickaxe"];
  const countDiamonds = () => bot.inventory.items().filter(i => i.name === "diamond").reduce((s, i) => s + i.count, 0);

  // 1) Ensure we have a suitable pickaxe
  const hasPickaxe = bot.inventory.items().some(i => goodPickaxes.includes(i.name));
  if (!hasPickaxe) {
    bot.chat("No iron-or-better pickaxe found. Cannot mine diamond ore.");
    return;
  }
  bot.chat("Have a suitable pickaxe, searching for diamond ore...");

  // 2) Free inventory space so the drop can be picked up
  const junkNames = ["cobblestone", "andesite", "diorite", "granite", "gravel", "dirt", "sand"];
  let attemptsFree = 0;
  while (bot.inventory.emptySlotCount() < 2 && attemptsFree < 20) {
    attemptsFree++;
    const junk = bot.inventory.items().find(i => junkNames.includes(i.name));
    if (!junk) break;
    bot.chat(`Inventory tight, dropping ${junk.name} x${junk.count} to make room.`);
    await bot.toss(junk.type, null, junk.count);
  }

  // 3) Find and mine diamond ore until we have mined one
  const before = countDiamonds();
  let mined = false;
  let attempts = 0;
  while (!mined && attempts < 20) {
    attempts++;
    let diamondBlock = bot.findBlock({
      matching: mcData.blocksByName["diamond_ore"].id,
      maxDistance: 32
    });
    if (!diamondBlock) {
      bot.chat(`No diamond ore nearby, exploring downward (attempt ${attempts})...`);
      const directions = [new Vec3(0, -1, 0), new Vec3(1, -1, 0), new Vec3(-1, -1, 0), new Vec3(0, -1, 1), new Vec3(0, -1, -1), new Vec3(1, -1, 1), new Vec3(-1, -1, -1)];
      const dir = directions[Math.floor(Math.random() * directions.length)];
      diamondBlock = await exploreUntil(bot, dir, 60, () => {
        return bot.findBlock({
          matching: mcData.blocksByName["diamond_ore"].id,
          maxDistance: 32
        });
      });
    }
    if (!diamondBlock) {
      bot.chat("Failed to find any diamond ore this attempt.");
      continue;
    }
    bot.chat("Found diamond ore, mining...");
    await mineBlock(bot, "diamond_ore", 1);
    if (countDiamonds() > before) {
      mined = true;
    } else {
      bot.chat("Mined ore but no diamond collected yet, retrying...");
    }
  }

  // 4) Verify
  if (mined) {
    bot.chat(`Done. Total diamonds in inventory: ${countDiamonds()}.`);
  } else {
    bot.chat("Failed to obtain a diamond after exploring.");
  }
}