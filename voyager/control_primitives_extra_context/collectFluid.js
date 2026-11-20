//! USE-RULE: - Use `collectFluid(bot, fluid)` for water or lava. Only source blocks fill a bucket. Missing empty bucket → missing_bucket.
// Fill a lava bucket from a source block: collectFluid(bot, "lava");
async function collectFluid(bot, fluid) {
    return { ok: false, reason: "missing_bucket" };
}
