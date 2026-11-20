async function mineThreeDiamonds(bot) {
  const goodPickaxes = ["iron_pickaxe", "diamond_pickaxe", "netherite_pickaxe"];
  const countDiamonds = () => bot.inventory.items().filter(i => i.name === "diamond").reduce((s, i) => s + i.count, 0);

  // 1) Ensure we have a suitable pickaxe
  const hasPickaxe = bot.inventory.items().some(i => goodPickaxes.includes(i.name));
  if (!hasPickaxe) {
    bot.chat("No iron-or-better pickaxe found. Cannot mine diamond ore.");
    return;
  }
  bot.chat("Have a suitable pickaxe, searching for diamond ore...");

  // 2) Free inventory space so drops can be picked up
  const junkNames = ["cobblestone", "cobbled_deepslate", "andesite", "diorite", "granite", "gravel", "dirt", "sand", "tuff", "deepslate", "stone"];
  let attemptsFree = 0;
  while (bot.inventory.emptySlotCount() < 3 && attemptsFree < 40) {
    attemptsFree++;
    const junk = bot.inventory.items().find(i => junkNames.includes(i.name));
    if (!junk) break;
    bot.chat(`Inventory tight, dropping ${junk.name} x${junk.count} to make room.`);
    await bot.toss(junk.type, null, junk.count);
  }
  bot.chat(`Free slots now: ${bot.inventory.emptySlotCount()}`);

  // 3) Mine diamond ore until we have gained 3 diamonds
  const before = countDiamonds();
  let attempts = 0;
  while (countDiamonds() - before < 3 && attempts < 40) {
    attempts++;

    // Make sure we still have room for drops
    if (bot.inventory.emptySlotCount() < 1) {
      const junk = bot.inventory.items().find(i => junkNames.includes(i.name));
      if (junk) {
        bot.chat(`Dropping ${junk.name} x${junk.count} to keep space.`);
        await bot.toss(junk.type, null, junk.count);
      }
    }
    let diamondBlock = bot.findBlock({
      matching: block => block.name === "diamond_ore" || block.name === "deepslate_diamond_ore",
      maxDistance: 32
    });
    if (!diamondBlock) {
      bot.chat(`No diamond ore nearby, exploring (attempt ${attempts})...`);
      const directions = [new Vec3(0, -1, 0), new Vec3(1, -1, 0), new Vec3(-1, -1, 0), new Vec3(0, -1, 1), new Vec3(0, -1, -1), new Vec3(1, -1, 1), new Vec3(-1, -1, -1), new Vec3(1, 0, 1), new Vec3(-1, 0, -1)];
      const dir = directions[Math.floor(Math.random() * directions.length)];
      diamondBlock = await exploreUntil(bot, dir, 60, () => {
        return bot.findBlock({
          matching: block => block.name === "diamond_ore" || block.name === "deepslate_diamond_ore",
          maxDistance: 32
        });
      });
    }
    if (!diamondBlock) {
      bot.chat("Failed to find any diamond ore this attempt.");
      continue;
    }
    bot.chat(`Found ${diamondBlock.name}, mining...`);
    await mineBlock(bot, diamondBlock.name, 1);
    bot.chat(`Diamonds so far: ${countDiamonds() - before} (total ${countDiamonds()}).`);
  }

  // 4) Verify
  const gained = countDiamonds() - before;
  bot.chat(`Done. Diamonds gained: ${gained}. Total diamonds in inventory: ${countDiamonds()}.`);
}