async function equipIronLeggings(bot) {
  // Check if iron leggings are already equipped on the legs slot (index 6)
  const equipped = bot.inventory.slots[6];
  if (equipped && equipped.name === "iron_leggings") {
    bot.chat("Iron leggings are already equipped.");
    return;
  }

  // Find iron leggings in inventory
  const leggings = bot.inventory.findInventoryItem(mcData.itemsByName["iron_leggings"].id);
  if (!leggings) {
    bot.chat("No iron leggings in inventory, cannot equip.");
    return;
  }
  bot.chat("Equipping iron leggings...");
  await bot.equip(leggings, "legs");
  bot.chat("Iron leggings equipped.");
}