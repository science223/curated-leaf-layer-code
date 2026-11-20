//! USE-RULE: - Use `smelt(bot, item, n)` for smelt/cook tasks. Do not hand-write furnace setup or fuel picking.
// Smelt 8 raw_iron: smelt(bot, "raw_iron", 8);
// Crafts/places a furnace and picks fuel if missing. Logs → charcoal. No furnace recipe → unknown_recipe. Already holding enough of the product → returns immediately.
async function smelt(bot, item, n = 1) {
    const have = (name) => bot.inventory.items().filter(i => i.name === name).reduce((s, i) => s + i.count, 0);
    const output = item.endsWith("_log") || item.endsWith("_stem") ? "charcoal" : (item.startsWith("raw_") ? item.replace("raw_", "") + (item.includes("iron") || item.includes("gold") || item.includes("copper") ? "_ingot" : "") : item);
    if (have(output) >= n) return { ok: true, reason: "already_satisfied" };
    const haveMat = have(item);
    const need = n - have(output);
    if (haveMat < 1 || haveMat < need) return { ok: false, reason: "missing_materials" };
    await smeltItem(bot, item, "coal", Math.min(need, 14));
    return { ok: have(output) >= n, reason: have(output) >= n ? "done" : "smelt_failed" };
}
