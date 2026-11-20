// Kill 1 chicken (equips the best sword first): killMobWithWeapon(bot, "chicken", 1);
// Equips the best available sword, explores to find the mob when none is nearby, then kills it.
async function killMobWithWeapon(bot, mobName, count = 1, timeout = 300) {
    const SWORDS = ["netherite_sword", "diamond_sword", "iron_sword", "stone_sword", "golden_sword", "wooden_sword"];
    // Equip the best sword we have; bare hands still work but are much slower.
    const sword = SWORDS.map((n) => bot.inventory.items().find((i) => i.name === n)).find(Boolean);
    if (sword) {
        await bot.equip(sword, "hand");
    } else {
        bot.chat("No sword in inventory; attacking bare-handed.");
    }
    const findMob = () => bot.nearestEntity((e) => e.name === mobName && e.position.distanceTo(bot.entity.position) < 32);
    let mob = findMob();
    const dirs = [new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1)];
    let attempts = 0;
    while (!mob && attempts < 4) {
        attempts++;
        mob = await exploreUntil(bot, dirs[Math.floor(Math.random() * dirs.length)], 60, findMob);
    }
    if (!mob) {
        bot.chat(`Could not find any ${mobName} after exploring.`);
        return;
    }
    await killMob(bot, mobName, timeout);
}
