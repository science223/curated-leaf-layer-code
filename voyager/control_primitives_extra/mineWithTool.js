// Mine 3 redstone ore with the right pickaxe: mineWithTool(bot, ["redstone_ore", "deepslate_redstone_ore"], 3, "redstone", "iron_pickaxe");
// Equips a pickaxe of at least the required tier, searches downward when nothing is nearby, then mines. Returns early if you already have enough.
async function mineWithTool(bot, blockNames, count = 1, dropName = null, minTool = "wooden_pickaxe") {
    const TIERS = ["wooden_pickaxe", "stone_pickaxe", "iron_pickaxe", "diamond_pickaxe", "netherite_pickaxe"];
    const names = Array.isArray(blockNames) ? blockNames : [blockNames];
    const product = dropName || names[0];
    const countOf = (name) => {
        const def = mcData.itemsByName[name];
        if (!def) return 0;
        return bot.inventory.items().filter((i) => i.type === def.id).reduce((s, i) => s + i.count, 0);
    };
    // Already done? Do nothing.
    const have = countOf(product);
    if (have >= count) {
        bot.chat(`Already have ${have} ${product}.`);
        return;
    }
    // Equip a pickaxe at or above the required tier; abort if none — mining without it drops nothing.
    const minIdx = Math.max(TIERS.indexOf(minTool), 0);
    const usable = TIERS.slice(minIdx);
    const pick = bot.inventory.items().find((i) => usable.includes(i.name));
    if (!pick) {
        bot.chat(`No ${minTool} or better; cannot mine ${names[0]}.`);
        return;
    }
    await bot.equip(pick, "hand");
    const findOre = () => bot.findBlock({ matching: (b) => names.includes(b.name), maxDistance: 32 });
    let attempts = 0;
    while (countOf(product) < count && attempts < 8) {
        attempts++;
        let block = findOre();
        if (!block) {
            // Ores are underground: search downward, not across the surface.
            const dirs = [new Vec3(0, -1, 0), new Vec3(1, -1, 0), new Vec3(-1, -1, 0), new Vec3(0, -1, 1), new Vec3(0, -1, -1)];
            block = await exploreUntil(bot, dirs[Math.floor(Math.random() * dirs.length)], 60, findOre);
        }
        if (!block) continue;
        await mineBlock(bot, block.name, count - countOf(product));
    }
    if (countOf(product) < count) {
        bot.chat(`Only got ${countOf(product)} ${product} out of ${count}.`);
    }
}
