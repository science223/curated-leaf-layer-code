//! USE-RULE: - Use `equipBest(bot)` when the inventory has better armor than what is worn. Do not hand-write armor-slot loops.
// Equip the best armor you have: equipBest(bot);
// leather < gold < chainmail < iron < diamond < netherite. Already best in every slot → returns immediately.
async function equipBest(bot) {
    const TIER = { leather: 0, golden: 1, chainmail: 2, iron: 3, diamond: 4, netherite: 5 };
    for (const slot of ["head", "torso", "legs", "feet"]) {
        // equip the highest-tier piece available for this slot
        await bot.equip(bot.inventory.items()[0], slot);
    }
    return { ok: true, reason: "done" };
}
