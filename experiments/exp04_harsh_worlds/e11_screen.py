"""E11 seed screening (预注册_E11_恶劣出生点孤立评测_2026-10-06 §2): spawn terrain only, no obtain, no model call.
For each seed: a fresh world, the isolated harness's own capture_world_spawn (the same spawn logic as the evaluation),
then one JS step that records the biome of the block at the spawn's feet, the block below and the logs within 32 blocks.
Adapted from tools_0926/screen_seeds.py (same VoyagerEnv start, same harness calls).
Usage (inside ~/wt_e11): python e11_screen.py <first_index> <stride> <mc_port> <bridge_port> <out.jsonl>
Seeds = 20260903000 + i for i in range(first_index, 240, stride)."""
import json
import os
import sys
import time

REPO = os.path.expanduser(os.environ.get("E11_REPO", "~/wt_e11"))
sys.path.insert(0, os.path.join(REPO, "_cc0924"))
sys.path.insert(0, REPO)
os.chdir(REPO)
import test_obtain_mc as T  # noqa: E402
import test_skills_mc as SM  # noqa: E402
from voyager.env import VoyagerEnv  # noqa: E402

first, stride, MC, BR, OUT = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
SEEDS = [20260903000 + i for i in range(first, 240, stride)]
SM.MC_PORT, SM.BR_PORT = MC, BR
SM.LOG_PATH = os.path.splitext(OUT)[0] + ".log"
T.LOG_PATH = SM.LOG_PATH
done = set()
if os.path.exists(OUT):
    for line in open(OUT):
        try:
            done.add(json.loads(line)["seed"])
        except Exception:
            pass
JS = """
const p = bot.entity.position.floored();
const feet = bot.blockAt(p);
const below = bot.blockAt(p.offset(0, -1, 0));
const logs = bot.findBlocks({ matching: (b) => b && (b.name.endsWith('_log') || b.name.endsWith('_stem')), maxDistance: 32, count: 64 });
const water = bot.findBlocks({ matching: (b) => b && b.name === 'water', maxDistance: 16, count: 400 });
let biome = '?'; try { biome = (feet && feet.biome && feet.biome.name) || (below && below.biome && below.biome.name) || '?'; } catch (e) {}
await bot.chat(`[screen] pos=${p.x},${p.y},${p.z} below=${below ? below.name : '?'} logs32=${logs.length} water16=${water.length} biome=${biome}`);
await bot.waitForTicks(10);
"""
for seed in SEEDS:
    if seed in done:
        continue
    work = os.path.join(os.path.dirname(OUT), "screen_work", str(seed))
    os.makedirs(work, exist_ok=True)
    SM.OUR_PIDS.clear()
    SM._CLEANED = False
    rec = {"seed": seed}
    env = None
    try:
        srv = SM.start_mc(work, MC, seed)
        SM.record_pid(srv.pid, "minecraft-java")
        env = VoyagerEnv(mc_port=MC, server_port=BR, request_timeout=300, log_path=work, pause_verify=True)
        env.reset(options={"mode": "hard", "wait_ticks": 20})
        T.hold_unpaused(env)
        env.step(SM.boot_code(), programs="")
        time.sleep(3)
        spawn = T.capture_world_spawn(env, seed)
        x, y, z = spawn["x"], spawn["y"], spawn["z"]
        env.step(f'await bot.chat("/tp @s {x} {y} {z}"); await bot.waitForTicks(40);', programs="")
        obs = env.step(JS, programs="")
        line = next((c for c in SM.chats(obs) if str(c).startswith("[screen]")), "")
        kv = dict(tok.split("=", 1) for tok in line.split()[1:] if "=" in tok)
        rec.update({"spawn": spawn, "line": line, "below": kv.get("below"), "biome": kv.get("biome"),
                    "logs32": int(kv.get("logs32", "0") or 0), "water16": int(kv.get("water16", "0") or 0)})
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"[:300]
    finally:
        try:
            if env:
                env.close()
        except Exception:
            pass
        SM.kill_ours()
        time.sleep(5)
    print(json.dumps(rec), flush=True)
    with open(OUT, "a") as fp:
        fp.write(json.dumps(rec) + "\n")
print("SCREEN DONE", flush=True)
