//! USE-RULE: - Use `mineWithTool(bot, blockNames, count, dropName, minTool)` to mine ores. Do not hand-write pickaxe checks before mining.
// Mine 3 redstone ore with a good enough pickaxe: mineWithTool(bot, ["redstone_ore", "deepslate_redstone_ore"], 3, "redstone", "iron_pickaxe");
// Mining without a good enough pickaxe drops nothing, so it equips one first and refuses if absent.
// Ores are underground: when none is nearby it explores DOWNWARD, not across the surface.
async function mineWithTool(bot, blockNames, count = 1, dropName = null, minTool = "wooden_pickaxe") {
    const TIERS = ["wooden_pickaxe", "stone_pickaxe", "iron_pickaxe", "diamond_pickaxe", "netherite_pickaxe"];
    const names = Array.isArray(blockNames) ? blockNames : [blockNames];
    const product = dropName || names[0];
    const have = (n) => bot.inventory.items().filter(i => i.name === n).reduce((s, i) => s + i.count, 0);
    if (have(product) >= count) return;                        // already done
    const pick = bot.inventory.items().find(i => TIERS.slice(TIERS.indexOf(minTool)).includes(i.name));
    if (!pick) return;                                         // no adequate pickaxe: mining would drop nothing
    await bot.equip(pick, "hand");
    const find = () => bot.findBlock({ matching: b => names.includes(b.name), maxDistance: 32 });
    for (let i = 0; i < 8 && have(product) < count; i++) {
        const block = find() || await exploreUntil(bot, new Vec3(0, -1, 0), 60, find);
        if (block) await mineBlock(bot, block.name, count - have(product));
    }
}
