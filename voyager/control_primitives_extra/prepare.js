// Prepare a trip: logs ≥ 4 or planks ≥ 12, and a crafting table. prepare(bot);
// Already satisfied → no-op success.
async function prepare(bot) {
    const report = (ok, reason) => {
        bot.chat(`[contract] prepare ${ok ? "ok" : "fail"} ${reason}`);
        try {
            globalThis.__skillOutcomes = globalThis.__skillOutcomes || [];
            globalThis.__skillOutcomes.push({ name: "prepare", ok: !!ok, reason: String(reason), source: "contract" });
        } catch (err) {}
        return { ok: !!ok, reason: String(reason) };
    };
    const countOf = (name) => {
        const def = mcData.itemsByName[name];
        if (!def) return 0;
        return bot.inventory.items().filter((i) => i.type === def.id).reduce((s, i) => s + i.count, 0);
    };
    const logs = () => bot.inventory.items().filter((i) => i.name.endsWith("_log") || i.name.endsWith("_stem")).reduce((s, i) => s + i.count, 0);
    const planks = () => bot.inventory.items().filter((i) => i.name.endsWith("_planks")).reduce((s, i) => s + i.count, 0);
    const ready = () => (logs() >= 4 || planks() >= 12) && countOf("crafting_table") >= 1;
    if (ready()) return report(true, "already_satisfied");

    const hasTable = countOf("crafting_table") >= 1;
    const want = _prepareMineWant(logs(), planks(), hasTable);
    if (want > 0) {
        const woods = ["oak_log", "spruce_log", "birch_log", "jungle_log", "acacia_log", "dark_oak_log", "mangrove_log"];
        const findLog = () => bot.findBlock({ matching: (b) => woods.includes(b.name) || b.name.endsWith("_log"), maxDistance: 32 });
        let block = findLog();
        if (!block) {
            try {
                block = await exploreUntil(bot, new Vec3(1, 0, 0), 60, findLog);
            } catch (err) {
                // try other dirs below
            }
        }
        if (!block) {
            for (const dir of [new Vec3(-1, 0, 0), new Vec3(0, 0, 1)]) {
                try {
                    block = await exploreUntil(bot, dir, 60, findLog);
                } catch (err) {}
                if (block) break;
            }
        }
        if (block) {
            try {
                // empty bag: mine 6 as buffer; 4 logs no table: mine extra for the table cost.
                const mineN = hasTable ? want : Math.max(6 - logs(), want);
                if (mineN > 0) await mineBlock(bot, block.name, mineN);
            } catch (err) {
                // fall through to check
            }
        }
    }
    if (planks() < 4 && logs() >= 1) {
        const logItem = bot.inventory.items().find((i) => i.name.endsWith("_log"));
        if (logItem) {
            const plankName = logItem.name.replace("_log", "_planks");
            try {
                await craftItem(bot, plankName, 1);
            } catch (err) {}
        }
    }
    if (countOf("crafting_table") < 1) {
        try {
            await craftItem(bot, "crafting_table", 1);
        } catch (err) {}
    }
    if (ready()) return report(true, "done");
    if (logs() < 4 && planks() < 12) return report(false, "missing_materials");
    if (countOf("crafting_table") < 1) return report(false, "missing_materials");
    return report(false, "prepare_failed");
}

function _prepareMineWant(logCount, plankCount, hasTable) {
    const logsNeeded = hasTable ? 4 : 5;
    const planksNeeded = hasTable ? 12 : 16;
    if (logCount >= logsNeeded || plankCount >= planksNeeded) return 0;
    return Math.max(logsNeeded - logCount, 0);
}
