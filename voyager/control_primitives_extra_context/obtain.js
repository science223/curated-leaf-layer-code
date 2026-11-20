//! USE-RULE: - Prefer `obtain(bot, item, n)` for the final goal (n = total to own). If unfinished, call obtain again or handle the reported shortage. Do not hand-write the recipe tree.
async function obtain(bot, item, n = 1) {
    return { ok: false, reason: "context_stub" };
}
