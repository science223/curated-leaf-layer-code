//! USE-RULE: - Use `gather(bot, name, n)` for mine/collect/gather/chop tasks. Do not hand-write explore+mine loops.
// Gather 8 oak_log: gather(bot, "oak_log", 8);
// Logs/dirt/sand: no tool required. Leaves: shears. Stone/ore/andesite: pickaxe. Missing required tool → missing_tool immediately.
async function gather(bot, name, n = 1) {
    const have = (item) => bot.inventory.items().filter(i => i.name === item).reduce((s, i) => s + i.count, 0);
    if (have(name) >= n) return { ok: true, reason: "already_satisfied" };
    const posKey = (p) => `${Math.floor(p.x)},${Math.floor(p.y)},${Math.floor(p.z)}`;
    const blacklisted = new Set();
    const findTarget = () => bot.findBlock({
        matching: (b) => b.name === name && !blacklisted.has(posKey(b.position)),
        maxDistance: 32,
    });
    const block = findTarget();
    if (block) await bot.collectBlock.collect(block, { ignoreNoPath: true, count: 1 });
    return { ok: have(name) >= n, reason: have(name) >= n ? "done" : "not_found" };
}
