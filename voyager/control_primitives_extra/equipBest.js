// Equip the best armor you currently have: equipBest(bot);
// leather < gold < chainmail < iron < diamond < netherite. Already best → no-op success.
async function equipBest(bot) {
    const report = (ok, reason) => {
        bot.chat(`[contract] equipBest ${ok ? "ok" : "fail"} ${reason}`);
        try {
            globalThis.__skillOutcomes = globalThis.__skillOutcomes || [];
            globalThis.__skillOutcomes.push({ name: "equipBest", ok: !!ok, reason: String(reason), source: "contract" });
        } catch (err) {}
        return { ok: !!ok, reason: String(reason) };
    };
    const TIER = { leather: 0, golden: 1, chainmail: 2, iron: 3, diamond: 4, netherite: 5 };
    const SLOTS = {
        head: "helmet",
        torso: "chestplate",
        legs: "leggings",
        feet: "boots",
    };
    const equippedName = (slot) => {
        try {
            const dest = bot.getEquipmentDestSlot(slot);
            const it = bot.inventory.slots[dest];
            return it ? it.name : null;
        } catch (err) {
            return null;
        }
    };
    const tierOf = (itemName) => {
        if (!itemName) return -1;
        const parts = itemName.split("_");
        return TIER[parts[0]] == null ? -1 : TIER[parts[0]];
    };
    let changed = false;
    let missing = false;
    for (const slot of Object.keys(SLOTS)) {
        const kind = SLOTS[slot];
        const worn = equippedName(slot);
        const candidates = bot.inventory.items().filter((i) => i.name.endsWith(`_${kind}`));
        if (worn) {
            const def = mcData.itemsByName[worn];
            if (def) candidates.push({ name: worn, type: def.id, count: 1 });
        }
        if (candidates.length === 0) continue;
        candidates.sort((a, b) => tierOf(b.name) - tierOf(a.name));
        const best = candidates[0];
        if (worn === best.name) continue;
        try {
            const item = bot.inventory.items().find((i) => i.name === best.name);
            if (!item) {
                missing = true;
                continue;
            }
            await bot.equip(item, slot);
            changed = true;
        } catch (err) {
            missing = true;
        }
    }
    const allBest = Object.keys(SLOTS).every((slot) => {
        const kind = SLOTS[slot];
        const worn = equippedName(slot);
        const invBest = bot.inventory.items()
            .filter((i) => i.name.endsWith(`_${kind}`))
            .concat(worn ? [{ name: worn }] : [])
            .sort((a, b) => tierOf(b.name) - tierOf(a.name))[0];
        if (!invBest) return true;
        return worn === invBest.name;
    });
    if (allBest && !changed) return report(true, "already_satisfied");
    if (allBest) return report(true, "done");
    if (missing) return report(false, "missing_materials");
    return report(false, "equip_failed");
}
