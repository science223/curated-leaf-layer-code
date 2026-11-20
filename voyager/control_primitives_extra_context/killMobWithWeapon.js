//! USE-RULE: - Use `killMobWithWeapon(bot, name, timeout)` to kill mobs with the best weapon you own. Do not hand-write weapon selection.
// Kill 1 chicken with the best sword you own: killMobWithWeapon(bot, "chicken", 1);
// killMob does NOT equip a weapon by itself, so this equips the best sword first, then explores to find the mob.
async function killMobWithWeapon(bot, mobName, count = 1, timeout = 300) {
    const SWORDS = ["netherite_sword", "diamond_sword", "iron_sword", "stone_sword", "golden_sword", "wooden_sword"];
    const sword = SWORDS.map(n => bot.inventory.items().find(i => i.name === n)).find(Boolean);
    if (sword) await bot.equip(sword, "hand");
    const find = () => bot.nearestEntity(e => e.name === mobName && e.position.distanceTo(bot.entity.position) < 32);
    let mob = find();
    for (let i = 0; i < 4 && !mob; i++) mob = await exploreUntil(bot, new Vec3(1, 0, 0), 60, find);
    if (!mob) return;
    await killMob(bot, mobName, timeout);
}
