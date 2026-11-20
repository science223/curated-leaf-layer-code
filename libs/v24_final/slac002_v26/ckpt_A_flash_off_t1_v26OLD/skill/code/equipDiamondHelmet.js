async function equipDiamondHelmet(bot) {
  // 1) Find the diamond helmet in the inventory
  const helmet = bot.inventory.findInventoryItem(mcData.itemsByName["diamond_helmet"].id);
  if (!helmet) {
    bot.chat("No diamond helmet in inventory, cannot equip.");
    return;
  }

  // 2) Check if a diamond helmet is already equipped on the head slot
  const equipped = bot.inventory.slots[5]; // head slot index in inventory window
  if (equipped && equipped.name === "diamond_helmet") {
    bot.chat("Diamond helmet is already equipped.");
    return;
  }

  // 3) Equip the diamond helmet
  bot.chat("Equipping diamond helmet...");
  await bot.equip(helmet, "head");
  bot.chat("Diamond helmet equipped.");
}