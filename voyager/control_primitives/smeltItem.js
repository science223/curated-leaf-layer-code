async function smeltItem(bot, itemName, fuelName, count = 1) {
    if (typeof itemName !== "string" || typeof fuelName !== "string") {
        throw new Error("itemName or fuelName for smeltItem must be a string");
    }
    if (typeof count !== "number") {
        throw new Error("count for smeltItem must be a number");
    }
    const item = mcData.itemsByName[itemName];
    const fuel = mcData.itemsByName[fuelName];
    if (!item) {
        throw new Error(`No item named ${itemName}`);
    }
    if (!fuel) {
        throw new Error(`No item named ${fuelName}`);
    }
    const furnaceBlock = bot.findBlock({
        matching: mcData.blocksByName.furnace.id,
        maxDistance: 32,
    });
    if (!furnaceBlock) {
        throw new Error("No furnace nearby");
    } else {
        await bot.pathfinder.goto(
            new GoalLookAtBlock(furnaceBlock.position, bot.world)
        );
    }
    const furnace = await bot.openFurnace(furnaceBlock);
    let success_count = 0;
    const timeout = Date.now() + count * 15 * 1000 + 30 * 1000;
    try {
        if (count > 0) {
            const availableInputCount = bot.inventory
                .items()
                .filter((inventoryItem) => inventoryItem.type === item.id)
                .reduce((total, inventoryItem) => total + inventoryItem.count, 0);
            if (availableInputCount <= 0) {
                bot.chat(`No ${itemName} to smelt in inventory`);
            } else {
                const inputCount = Math.min(count, availableInputCount);
                if (furnace.outputItem()) {
                    await furnace.takeOutput();
                }
                await furnace.putInput(item.id, null, inputCount);
                while (success_count < inputCount) {
                    if (Date.now() > timeout) {
                        throw new Error(`smeltItem timed out while smelting ${itemName}`);
                    }
                    if (furnace.fuelSeconds < 15 && furnace.fuelItem()?.name !== fuelName) {
                        const fuelItem = bot.inventory.findInventoryItem(fuel.id, null);
                        if (!fuelItem) {
                            bot.chat(`No ${fuelName} as fuel in inventory`);
                            break;
                        }
                        const remaining = inputCount - success_count;
                        const fuelCount = Math.min(Math.ceil(remaining / 8), fuelItem.count);
                        await furnace.putFuel(fuel.id, null, fuelCount);
                        await bot.waitForTicks(20);
                        if (!furnace.fuel && furnace.fuelItem()?.name !== fuelName) {
                            throw new Error(`${fuelName} is not a valid fuel`);
                        }
                    }
                    const outputItem = furnace.outputItem();
                    if (outputItem) {
                        await furnace.takeOutput();
                        success_count += outputItem.count;
                    } else if (!furnace.inputItem()) {
                        if (Date.now() + 15 * 1000 > timeout) {
                            throw new Error(`smeltItem timed out while smelting ${itemName}`);
                        }
                        await bot.waitForTicks(20 * 15);
                        const finalOutput = furnace.outputItem();
                        if (finalOutput) {
                            await furnace.takeOutput();
                            success_count += finalOutput.count;
                        }
                        break;
                    } else {
                        await bot.waitForTicks(20);
                    }
                }
            }
        }
    } finally {
        furnace.close();
    }
    if (success_count > 0) bot.chat(`Smelted ${success_count} ${itemName}.`);
    else {
        bot.chat(`Failed to smelt ${itemName}, please check the fuel and input.`);
        _smeltItemFailCount++;
        if (_smeltItemFailCount > 10) {
            throw new Error(
                `smeltItem failed too many times, please check the fuel and input.`
            );
        }
    }
}
