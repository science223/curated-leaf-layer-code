async function equipDiamondLeggings(bot) {
  // Check if diamond leggings are already equipped on the legs slot
  const equipped = bot.inventory.slots[6]; // legs armor slot index in inventory window
  if (equipped && equipped.name === "diamond_leggings") {
    bot.chat("Diamond leggings are already equipped.");
    return;
  }

  // Find diamond leggings in inventory
  const leggings = bot.inventory.findInventoryItem(mcData.itemsByName["diamond_leggings"].id);
  if (!leggings) {
    bot.chat("No diamond leggings in inventory, cannot equip.");
    return;
  }
  bot.chat("Equipping diamond leggings...");
  await bot.equip(leggings, "legs");
  bot.chat("Diamond leggings equipped.");
}