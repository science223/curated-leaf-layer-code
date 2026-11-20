async function openAndCheckChestAt(bot, chestPosition) {
  const chestBlock = bot.blockAt(chestPosition);
  if (!chestBlock || chestBlock.name !== "chest") {
    bot.chat(`No chest found at (${chestPosition.x}, ${chestPosition.y}, ${chestPosition.z}).`);
    return;
  }

  // Clear any solid block directly above the chest so it can open
  const abovePos = chestPosition.offset(0, 1, 0);
  const above = bot.blockAt(abovePos);
  if (above && above.name !== "air" && above.name !== "cave_air" && above.name !== "water") {
    bot.chat(`Block ${above.name} above chest is blocking it, mining it...`);
    try {
      await mineBlock(bot, above.name, 1);
    } catch (err) {
      bot.chat(`Could not clear block above chest: ${err.message}`);
    }
  }
  bot.chat(`Moving adjacent to chest at (${chestPosition.x}, ${chestPosition.y}, ${chestPosition.z})...`);
  const goals = [new GoalGetToBlock(chestPosition.x, chestPosition.y, chestPosition.z), new GoalNear(chestPosition.x, chestPosition.y, chestPosition.z, 1), new GoalNear(chestPosition.x, chestPosition.y, chestPosition.z, 2)];
  let reached = false;
  for (const goal of goals) {
    try {
      await bot.pathfinder.goto(goal);
      reached = true;
      break;
    } catch (err) {
      bot.chat(`Path attempt failed: ${err.message}. Trying alternative...`);
    }
  }
  if (!reached) {
    bot.chat("Could not reach the chest.");
    return;
  }

  // Re-check the block in case we moved
  const blockNow = bot.blockAt(chestPosition);
  if (!blockNow || blockNow.name !== "chest") {
    bot.chat("Chest is no longer at the expected position.");
    return;
  }

  // Look precisely at the chest center before opening
  try {
    await bot.lookAt(chestPosition.offset(0.5, 0.5, 0.5), true);
  } catch (err) {
    // ignore look errors
  }
  bot.chat("Opening chest...");
  let chest;
  try {
    chest = await bot.openContainer(blockNow);
  } catch (err) {
    bot.chat(`Failed to open chest: ${err.message}`);
    return;
  }
  const items = chest.containerItems();
  if (items.length === 0) {
    bot.chat("The chest is empty.");
  } else {
    bot.chat(`Chest contains ${items.length} item stack(s):`);
    for (const item of items) {
      bot.chat(`- ${item.name} x${item.count}`);
    }
  }
  chest.close();
  bot.chat("Chest closed.");
}

async function openUnknownChestAt57_51_206(bot) {
  const chestPosition = new Vec3(57, 51, -206);
  await openAndCheckChestAt(bot, chestPosition);
}