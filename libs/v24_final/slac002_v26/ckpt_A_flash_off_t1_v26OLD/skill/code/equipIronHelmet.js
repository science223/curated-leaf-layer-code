async function equipIronHelmet(bot) {
  // Find the iron helmet in the inventory
  const helmet = bot.inventory.findInventoryItem(mcData.itemsByName["iron_helmet"].id);
  if (!helmet) {
    bot.chat("No iron helmet in inventory, cannot equip.");
    return;
  }

  // Check if it's already equipped on the head slot (slot index 5 in the inventory window)
  const equipped = bot.inventory.slots[5];
  if (equipped && equipped.name === "iron_helmet") {
    bot.chat("Iron helmet is already equipped.");
    return;
  }
  bot.chat("Equipping iron helmet...");
  await bot.equip(helmet, "head");
  bot.chat("Iron helmet equipped.");
}