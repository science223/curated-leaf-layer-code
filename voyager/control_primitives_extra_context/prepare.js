//! USE-RULE: - Use `prepare(bot)` before a trip that needs wood and a crafting table.
// Prepare: logs ≥ 4 or planks ≥ 12, and a crafting table. From an empty bag, mine ~6 logs first. Exactly 4 logs and no table → mine extra (table costs 1 log), then craft the table. prepare(bot);
// Already satisfied → returns immediately.
async function prepare(bot) {
    const logs = bot.inventory.items().filter(i => i.name.endsWith("_log")).reduce((s, i) => s + i.count, 0);
    const planks = bot.inventory.items().filter(i => i.name.endsWith("_planks")).reduce((s, i) => s + i.count, 0);
    const table = bot.inventory.items().filter(i => i.name === "crafting_table").reduce((s, i) => s + i.count, 0);
    if ((logs >= 4 || planks >= 12) && table >= 1) return { ok: true, reason: "already_satisfied" };
    if (logs < 6 && planks < 16) await mineBlock(bot, "oak_log", Math.max(6 - logs, 1));
    await craftItem(bot, "crafting_table", 1);
    return { ok: true, reason: "done" };
}
