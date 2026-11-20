//! USE-RULE: - Use `mineOre(bot, name, n)` for diamond, gold, redstone, lapis, and deep iron. n is how many MORE drops to obtain. Do not use gather for these ores.
// Mine 3 more diamond: mineOre(bot, "diamond", 3);
async function mineOre(bot, name, n = 1) {
    return { ok: false, reason: "not_found" };
}
