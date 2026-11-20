// Fill one bucket from a source block: collectFluid(bot, "lava");
// Only source blocks (level 0). No empty bucket → missing_bucket.
const COLLECTFLUID_KNOWLEDGE = JSON.parse(`{
  "source": "Minecraft Wiki pages Fluid and Bucket. A source block is still liquid with level 0; flowing liquid cannot fill a bucket. In Java 1.18+ (unchanged in 1.19) cave lava seas replace air below Y=-54; surface water sits near sea level Y=63. https://minecraft.wiki/w/Lava https://minecraft.wiki/w/Bucket",
  "budgetSeconds": 240,
  "sourceLevel": 0,
  "lavaTargetY": -50,
  "waterTargetY": 63,
  "missingReason": "missing_bucket"
}`);

async function collectFluid(bot, fluid) {
    const report = (ok, reason) => {
        bot.chat(`[contract] collectFluid ${ok ? "ok" : "fail"} ${reason}`);
        try {
            globalThis.__skillOutcomes = globalThis.__skillOutcomes || [];
            globalThis.__skillOutcomes.push({ name: "collectFluid", ok: !!ok, reason: String(reason), source: "contract" });
        } catch (err) {}
        return { ok: !!ok, reason: String(reason) };
    };
    const kind = _collectFluidKind(fluid);
    if (!kind) return report(false, "unknown_item");
    const filledName = kind === "lava" ? "lava_bucket" : "water_bucket";
    const countOf = (itemName) => {
        const def = mcData.itemsByName[itemName];
        if (!def) return 0;
        return bot.inventory.items().filter((i) => i.type === def.id).reduce((s, i) => s + i.count, 0);
    };
    if (countOf(filledName) >= 1) return report(true, "already_satisfied");
    if (countOf("bucket") < 1) return report(false, "missing_bucket");

    const deadline = Math.min(Date.now() + COLLECTFLUID_KNOWLEDGE.budgetSeconds * 1000, (typeof globalThis.__stepStart === "number" && Date.now() - globalThis.__stepStart < 600000) ? globalThis.__stepStart + 540000 : Infinity);
    const timedOut = () => Date.now() >= deadline;
    const targetY = kind === "lava" ? COLLECTFLUID_KNOWLEDGE.lavaTargetY : COLLECTFLUID_KNOWLEDGE.waterTargetY;

    bot._collectFluidState = bot._collectFluidState || {};
    let st = bot._collectFluidState[kind];
    if (!st) st = { atY: false, dx: 1, dz: 0, length: 0 };
    bot._collectFluidState[kind] = st;

    const save = () => {
        bot._collectFluidState[kind] = st;
    };

    try {
        if (kind === "lava" && !st.atY && Math.floor(bot.entity.position.y) > targetY + 2) {
            const reached = await _mineOreStairDown(bot, targetY, timedOut, save);
            if (reached) st.atY = true;
            save();
        } else if (kind === "water") {
            st.atY = true;
        } else {
            st.atY = Math.floor(bot.entity.position.y) <= targetY + 2;
        }

        while (!timedOut()) {
            if (countOf(filledName) >= 1) return report(true, "done");
            if (countOf("bucket") < 1) return report(false, "missing_bucket");
            const src = _collectFluidFindSource(bot, kind);
            if (src) {
                const ok = await _collectFluidFill(bot, src, kind, timedOut);
                if (ok || countOf(filledName) >= 1) return report(true, "done");
            }
            if (kind === "lava" && !st.atY) {
                const reached = await _mineOreStairDown(bot, targetY, timedOut, save);
                if (reached) st.atY = true;
                save();
                continue;
            }
            const step = await _mineOreStripStep(bot, st, timedOut, { seal: false });
            if (step === "liquid") {
                const src2 = _collectFluidFindSource(bot, kind);
                if (src2) {
                    const ok = await _collectFluidFill(bot, src2, kind, timedOut);
                    if (ok || countOf(filledName) >= 1) return report(true, "done");
                }
                const ndx = -st.dz;
                const ndz = st.dx;
                st.dx = ndx;
                st.dz = ndz || (ndx === 0 ? 1 : 0);
                st.length = 0;
            } else if (step === "blocked") {
                const ndx = -st.dz;
                const ndz = st.dx;
                st.dx = ndx;
                st.dz = ndz;
                st.length = 0;
            } else {
                st.length += 1;
                if (st.length >= 48) {
                    st.dx = -st.dx;
                    st.dz = -st.dz;
                    st.length = 0;
                }
            }
            save();
        }
        if (countOf(filledName) >= 1) return report(true, "done");
        return report(false, "timeout");
    } catch (err) {
        save();
        if (countOf(filledName) >= 1) return report(true, "done");
        if (timedOut()) return report(false, "timeout");
        return report(false, "fail");
    }
}

function _collectFluidKind(fluid) {
    const n = String(fluid || "").toLowerCase().replace(/^minecraft:/, "");
    if (n === "lava" || n === "lava_bucket") return "lava";
    if (n === "water" || n === "water_bucket") return "water";
    return null;
}

function _collectFluidLevel(block) {
    if (!block) return null;
    if (block._properties && block._properties.level != null) return Number(block._properties.level);
    try {
        const props = typeof block.getProperties === "function" ? block.getProperties() : null;
        if (props && props.level != null) return Number(props.level);
    } catch (err) {}
    if (block.metadata != null) return Number(block.metadata);
    return 0;
}

function _collectFluidIsSource(block, kind) {
    if (!block) return false;
    if (block.name !== kind && block.name !== `flowing_${kind}`) return false;
    const level = _collectFluidLevel(block);
    return level === COLLECTFLUID_KNOWLEDGE.sourceLevel || level === 0;
}

function _collectFluidFindSource(bot, kind) {
    try {
        return bot.findBlock({
            matching: (b) => _collectFluidIsSource(b, kind),
            maxDistance: 16,
        });
    } catch (err) {
        return null;
    }
}

async function _collectFluidFill(bot, src, kind, timedOut) {
    if (!src || !src.position) return false;
    const bucket = bot.inventory.items().find((i) => i.name === "bucket");
    if (!bucket) return false;
    const filledName = kind === "lava" ? "lava_bucket" : "water_bucket";
    const countFilled = () => bot.inventory.items().filter((i) => i.name === filledName).reduce((s, i) => s + i.count, 0);
    const distTo = () => bot.entity.position.distanceTo(src.position.offset(0.5, 0.5, 0.5));
    let stand = _collectFluidStandPos(bot, src.position);
    if (stand) await _mineOreWalk(bot, stand, 8000);
    if (distTo() > 4.5 && typeof _mineOreTunnelToward === "function") {
        await _mineOreTunnelToward(bot, src.position, timedOut || (() => false));
        stand = _collectFluidStandPos(bot, src.position);
        if (stand) await _mineOreWalk(bot, stand, 4000);
    }
    if (distTo() > 5) return false;
    try {
        await _mineOreTimed(bot.equip(bucket, "hand"), 4000, "equip_timeout");
    } catch (err) {
        return false;
    }
    try {
        await bot.lookAt(src.position.offset(0.5, 0.5, 0.5), true);
    } catch (err) {}
    try {
        await _mineOreTimed(bot.activateBlock(src), 4000, "activate_timeout");
    } catch (err) {
        try { await bot.activateItem(); } catch (e) {}
    }
    await _mineOreWaitTicks(bot, 10);
    return countFilled() >= 1;
}

function _collectFluidStandPos(bot, lavaPos) {
    const dirs = [[1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1], [1, 0, 1], [-1, 0, 1], [1, 0, -1], [-1, 0, -1]];
    const usable = (p) => {
        const feet = bot.blockAt(p);
        const head = bot.blockAt(p.offset(0, 1, 0));
        const floor = bot.blockAt(p.offset(0, -1, 0));
        if (!feet || !head || !floor) return false;
        const airish = (b) => b.name === "air" || b.name === "cave_air" || b.name === "void_air";
        return airish(feet) && airish(head) && floor.boundingBox === "block" && !_mineOreIsLiquid(floor);
    };
    for (const d of dirs) {
        const p = lavaPos.offset(d[0], 0, d[2]);
        if (usable(p)) return p;
    }
    for (const d of dirs) {
        const p = lavaPos.offset(d[0], 1, d[2]);
        if (usable(p)) return p;
    }
    return lavaPos.offset(1, 1, 0);
}
