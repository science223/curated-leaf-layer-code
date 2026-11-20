async function equipIronBoots(bot) {
  // Find iron boots in inventory
  const boots = bot.inventory.findInventoryItem(mcData.itemsByName["iron_boots"].id);
  if (!boots) {
    bot.chat("No iron boots in inventory, cannot equip.");
    return;
  }

  // Check if already equipped on feet
  const equipped = bot.inventory.slots[5]; // feet slot index in inventory window
  if (equipped && equipped.name === "iron_boots") {
    bot.chat("Iron boots are already equipped.");
    return;
  }
  bot.chat("Equipping iron boots...");
  await bot.equip(boots, "feet");
  bot.chat("Iron boots equipped.");
}