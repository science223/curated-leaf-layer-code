async function equipIronChestplate(bot) {
  // Find iron chestplate in inventory
  const chestplate = bot.inventory.findInventoryItem(mcData.itemsByName["iron_chestplate"].id);
  if (!chestplate) {
    bot.chat("No iron chestplate in inventory, cannot equip.");
    return;
  }

  // Check if already equipped on torso
  const equipped = bot.inventory.slots[6]; // torso slot index in inventory window
  if (equipped && equipped.name === "iron_chestplate") {
    bot.chat("Iron chestplate is already equipped.");
    return;
  }
  bot.chat("Equipping iron chestplate...");
  await bot.equip(chestplate, "torso");
  bot.chat("Iron chestplate equipped.");
}