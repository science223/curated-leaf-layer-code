async function mineDeepslateGoldOre(bot) {
  const goodPickaxes = ["iron_pickaxe", "diamond_pickaxe", "netherite_pickaxe"];
  const countRawGold = () => bot.inventory.items().filter(i => i.name === "raw_gold").reduce((s, i) => s + i.count, 0);

  // 1) Ensure we have a suitable pickaxe
  const hasPickaxe = bot.inventory.items().some(i => goodPickaxes.includes(i.name));
  if (!hasPickaxe) {
    bot.chat("No iron-or-better pickaxe found. Cannot mine deepslate gold ore.");
    return;
  }
  bot.chat("Have a suitable pickaxe, searching for deepslate gold ore...");

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

  // 3) Find and mine deepslate gold ore until we have 3
  const targetCount = 3;
  let mined = 0;
  let attempts = 0;
  while (mined < targetCount && attempts < 20) {
    attempts++;
    let goldBlock = bot.findBlock({
      matching: block => block.name === "deepslate_gold_ore",
      maxDistance: 32
    });
    if (!goldBlock) {
      bot.chat(`No deepslate gold ore nearby, exploring (attempt ${attempts})...`);
      const directions = [new Vec3(0, -1, 0), new Vec3(1, -1, 0), new Vec3(-1, -1, 0), new Vec3(0, -1, 1), new Vec3(0, -1, -1), new Vec3(1, -1, 1), new Vec3(-1, -1, -1)];
      const dir = directions[Math.floor(Math.random() * directions.length)];
      goldBlock = await exploreUntil(bot, dir, 60, () => {
        return bot.findBlock({
          matching: block => block.name === "deepslate_gold_ore",
          maxDistance: 32
        });
      });
    }
    if (!goldBlock) {
      bot.chat("Failed to find any deepslate gold ore this attempt.");
      continue;
    }
    const remaining = targetCount - mined;
    bot.chat(`Found deepslate gold ore, mining ${remaining} more...`);
    await mineBlock(bot, "deepslate_gold_ore", remaining);
    mined = countRawGold();
    if (mined < targetCount) {
      bot.chat(`Still need ${targetCount - mined} more deepslate gold ore.`);
    }
  }

  // 4) Verify
  bot.chat(`Done. Total raw gold in inventory: ${countRawGold()}.`);
}