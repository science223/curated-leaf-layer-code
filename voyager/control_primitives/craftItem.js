async function craftItem(bot, name, count = 1) {
    // return if name is not string
    if (typeof name !== "string") {
        throw new Error("name for craftItem must be a string");
    }
    // return if count is not number
    if (typeof count !== "number") {
        throw new Error("count for craftItem must be a number");
    }
    const itemByName = mcData.itemsByName[name];
    if (!itemByName) {
        throw new Error(`No item named ${name}`);
    }
    const withTimeout = (promise, ms, msg) => {
        let timer;
        const timeout = new Promise((_, reject) => {
            timer = setTimeout(() => {
                try {
                    bot.pathfinder.setGoal(null);
                } catch (e) {}
                reject(new Error(msg));
            }, ms);
        });
        return Promise.race([Promise.resolve(promise), timeout]).finally(() =>
            clearTimeout(timer)
        );
    };
    const craftingTable = bot.findBlock({
        matching: mcData.blocksByName.crafting_table.id,
        maxDistance: 32,
    });
    if (!craftingTable) {
        bot.chat("Craft without a crafting table");
    } else {
        try {
            await withTimeout(
                bot.pathfinder.goto(
                    new GoalLookAtBlock(craftingTable.position, bot.world)
                ),
                30000,
                "pathfinder timeout (30s) while moving to crafting table"
            );
        } catch (err) {
            bot.chat(`Error reaching crafting table: ${err.message}`);
        }
    }
    const recipe = bot.recipesFor(itemByName.id, null, 1, craftingTable)[0];
    if (recipe) {
        bot.chat(`I can make ${name}`);
        try {
            await withTimeout(
                bot.craft(recipe, count, craftingTable),
                30000,
                `craft timeout (30s) for ${name}`
            );
            bot.chat(`I did the recipe for ${name} ${count} times`);
        } catch (err) {
            bot.chat(`I cannot do the recipe for ${name} ${count} times`);
        }
    } else {
        failedCraftFeedback(bot, name, itemByName, craftingTable);
        _craftItemFailCount++;
        if (_craftItemFailCount > 10) {
            throw new Error(
                "craftItem failed too many times, check chat log to see what happened"
            );
        }
    }
}
