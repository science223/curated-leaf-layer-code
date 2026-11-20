const fs = require("fs");
const express = require("express");
const bodyParser = require("body-parser");
const mineflayer = require("mineflayer");

const skills = require("./lib/skillLoader");
const { initCounter, getNextTime } = require("./lib/utils");
const obs = require("./lib/observation/base");
const OnChat = require("./lib/observation/onChat");
const OnError = require("./lib/observation/onError");
const { Voxels, BlockRecords } = require("./lib/observation/voxels");
const Status = require("./lib/observation/status");
const Inventory = require("./lib/observation/inventory");
const OnSave = require("./lib/observation/onSave");
const Chests = require("./lib/observation/chests");
const { plugin: tool } = require("mineflayer-tool");

let bot = null;
const MC_HOST = process.env.MC_HOST || "172.25.144.1";

// 2026-09-11 审计 P0-3：正在处理中的 /step 请求。bot 被踢/断线时以前只
// console.log，HTTP 永不回包，Python 等满 300s 拿 ReadTimeout，把"被踢"当成
// "代码跑太久"（不计崩溃）再走硬重置回灌库存。现在断线立刻给 503 + 原因。
let pendingStep = null;

function failPendingStep(reason) {
    if (pendingStep) {
        const p = pendingStep;
        pendingStep = null;
        try {
            p.fail(reason);
        } catch (e) {
            console.log("[Mineflayer] failPendingStep error:", e.message || e);
        }
    }
}

const app = express();

app.use(bodyParser.json({ limit: "50mb" }));
app.use(bodyParser.urlencoded({ limit: "50mb", extended: false }));

function installChatThrottle(bot) {
    if (typeof bot.chat !== "function") return;

    const CHAT_SPAM_PER_MESSAGE = 20;
    const CHAT_SPAM_DECAY_PER_SECOND = 20;
    const CHAT_SPAM_SAFE_THRESHOLD = 160;
    const _origChat = bot.chat.bind(bot);
    const queue = [];
    let pumping = false;
    let spam = 0;
    let lastUpdate = Date.now();
    let closed = false;

    function updateSpam() {
        const now = Date.now();
        const elapsedSeconds = Math.max(0, now - lastUpdate) / 1000;
        spam = Math.max(0, spam - elapsedSeconds * CHAT_SPAM_DECAY_PER_SECOND);
        lastUpdate = now;
    }

    function wait(ms) {
        return new Promise((resolve) => setTimeout(resolve, ms));
    }

    async function waitUntilSafe() {
        while (!closed) {
            updateSpam();
            const projected = spam + CHAT_SPAM_PER_MESSAGE;
            if (projected <= CHAT_SPAM_SAFE_THRESHOLD) return;
            const excess = projected - CHAT_SPAM_SAFE_THRESHOLD;
            const waitMs = Math.ceil(
                (excess / CHAT_SPAM_DECAY_PER_SECOND) * 1000
            );
            await wait(waitMs);
        }
    }

    async function pumpChatQueue() {
        if (pumping) return;
        pumping = true;
        while (queue.length > 0) {
            const item = queue.shift();
            try {
                await waitUntilSafe();
                updateSpam();
                await Promise.resolve(_origChat(item.message));
                spam += CHAT_SPAM_PER_MESSAGE;
                item.resolve();
            } catch (err) {
                console.log("[Mineflayer] Throttled chat send failed:", err.message || err);
                item.resolve();
            }
        }
        pumping = false;
    }

    bot.chat = (message) => {
        // 2026-09-10：原来对 "/" 开头的命令直接放行不限流。硬重置一次性发
        // /clear、/kill、每种物品一条 /give、/item replace、/tp，库存种类多时同
        // tick 超过原版 200 的反刷屏阈值，"Kicked for spamming"→重置超时→连续
        // 5 次→environment_censored，专杀高产运行。现在命令也走队列限流。
        return new Promise((resolve) => {
            queue.push({ message, resolve });
            pumpChatQueue().catch((err) => {
                console.log("[Mineflayer] Chat throttle pump failed:", err.message || err);
            });
        });
    };

    bot.once("end", () => {
        closed = true;
    });
}

// --- MOCK_BOT=1 only: fake bot + spawn-without-Minecraft. Not called when unset. ---
function makeMockBot(body) {
    const { EventEmitter } = require("events");
    const { Vec3 } = require("vec3");
    const bot = new EventEmitter();
    const chatLog = [];
    const pendingEvents = [];
    const invItems = [];

    bot.version = "1.19";
    bot.waitTicks = body && body.waitTicks != null ? body.waitTicks : 1;
    bot.globalTickCounter = 0;
    bot.stuckTickCounter = 0;
    bot.stuckPosList = [];
    bot.cumulativeObs = [];
    bot.iron_pickaxe = false;
    bot.health = 20;
    bot.food = 20;
    bot.foodSaturation = 5;
    bot.oxygenLevel = 20;
    bot.heldItem = null;
    bot.entities = {};
    bot.time = { timeOfDay: 1000 };
    bot.physicsEnabled = true;

    bot.entity = {
        position: new Vec3(0, 64, 0),
        velocity: new Vec3(0, 0, 0),
        yaw: 0,
        pitch: 0,
        onGround: true,
        username: "bot",
        timeSinceOnGround: 0,
        isInWater: false,
        isInLava: false,
        isInWeb: false,
        isCollidedHorizontally: false,
        isCollidedVertically: false,
    };

    bot.inventory = {
        slots: new Array(45).fill(null),
        items() {
            return invItems.slice();
        },
    };

    bot.inventoryUsed = () =>
        bot.inventory.slots.slice(9, 45).filter((item) => item !== null).length;
    bot.pathfinder = {
        isMoving: () => false,
        setMovements() {},
    };
    bot.findBlocks = () => [];
    bot.findBlock = () => null;
    bot.blockAt = () => null;

    try {
        bot.registry = require("minecraft-data")(bot.version);
    } catch (e) {
        bot.registry = {
            blocksByName: {},
            itemsByName: {},
            blocksArray: [],
        };
        console.log("[MOCK_BOT] minecraft-data load failed:", e.message || e);
    }

    function snapshotObserveData() {
        const inv = {};
        for (const it of invItems) {
            if (it && it.name) {
                inv[it.name] = (inv[it.name] || 0) + (it.count || 0);
            }
        }
        const pos = bot.entity.position;
        return {
            voxels: [],
            status: {
                health: bot.health,
                food: bot.food,
                saturation: bot.foodSaturation,
                oxygen: bot.oxygenLevel,
                position: { x: pos.x, y: pos.y, z: pos.z },
                velocity: { x: 0, y: 0, z: 0 },
                yaw: bot.entity.yaw,
                pitch: bot.entity.pitch,
                onGround: bot.entity.onGround,
                equipment: [null, null, null, null, null, null],
                name: bot.entity.username,
                biome: "plains",
                entities: {},
                timeOfDay: "day",
                inventoryUsed: bot.inventoryUsed(),
                elapsedTime: bot.globalTickCounter,
            },
            inventory: inv,
            nearbyChests: {},
            blockRecords: [],
        };
    }

    bot.chat = (msg) => {
        chatLog.push(String(msg));
        const data = snapshotObserveData();
        data.onChat = String(msg);
        pendingEvents.push(["onChat", data]);
        bot.cumulativeObs = pendingEvents;
    };

    bot.observe = () => {
        const data = snapshotObserveData();
        pendingEvents.push(["observe", data]);
        const events = pendingEvents.slice();
        pendingEvents.length = 0;
        bot.cumulativeObs = [];
        return JSON.stringify(events);
    };

    bot.waitForTicks = (n) => {
        const ticks = Number(n);
        const ms = (Number.isFinite(ticks) ? Math.max(0, ticks) : 0) * 5;
        return new Promise((resolve) => setTimeout(resolve, ms));
    };

    bot.end = () => {
        bot.emit("end");
    };

    bot.on("error", (err) => {
        const data = snapshotObserveData();
        data.onError =
            typeof err === "string" ? err : (err && err.message) || String(err);
        pendingEvents.push(["onError", data]);
        bot.cumulativeObs = pendingEvents;
    });

    bot.mock = {
        chatLog,
        sleep(ms) {
            return new Promise((resolve) => setTimeout(resolve, ms));
        },
        throw(msg) {
            throw new Error(msg);
        },
        disconnect(_reason) {
            throw new Error(
                "bot.mock.disconnect was not wired to onDisconnect"
            );
        },
        gain(item, n) {
            const count = n == null ? 1 : n;
            invItems.push({ name: item, count });
            const idx = bot.inventory.slots.findIndex((s) => s === null);
            if (idx >= 0) {
                bot.inventory.slots[idx] = { name: item, count };
            }
        },
    };

    return bot;
}

async function runMockSpawn(req, res, hooks) {
    bot = makeMockBot(req.body);
    bot.waitTicks = req.body.waitTicks;
    bot.globalTickCounter = 0;
    bot.stuckTickCounter = 0;
    bot.stuckPosList = [];
    bot.iron_pickaxe = false;
    bot.mock.disconnect = (reason) => hooks.onDisconnect(reason);

    try {
        installChatThrottle(bot);
        console.log("[Mineflayer] Chat throttle installed");
    } catch (e) {
        console.log("[Mineflayer] Chat throttle install failed:", e.message || e);
    }

    let itemTicks = 1;
    if (req.body.reset === "hard") {
        bot.chat("/clear @s");
        bot.chat("/kill @s");
        const inventory = req.body.inventory ? req.body.inventory : {};
        const equipment = req.body.equipment
            ? req.body.equipment
            : [null, null, null, null, null, null];
        for (let key in inventory) {
            bot.chat(`/give @s minecraft:${key} ${inventory[key]}`);
            itemTicks += 1;
        }
        const equipmentNames = [
            "armor.head",
            "armor.chest",
            "armor.legs",
            "armor.feet",
            "weapon.mainhand",
            "weapon.offhand",
        ];
        for (let i = 0; i < 6; i++) {
            if (i === 4) continue;
            if (equipment[i]) {
                bot.chat(
                    `/item replace entity @s ${equipmentNames[i]} with minecraft:${equipment[i]}`
                );
                itemTicks += 1;
            }
        }
    }

    if (req.body.position) {
        bot.chat(
            `/tp @s ${req.body.position.x} ${req.body.position.y} ${req.body.position.z}`
        );
    }

    if (bot.inventory.items().find((item) => item.name === "iron_pickaxe")) {
        bot.iron_pickaxe = true;
    }

    // mock: skip pathfinder/tool/pvp plugins, obs.inject, skills.inject
    // (fake bot already has observe() / chat / pathfinder stubs).
    // skip initCounter(bot) — it reads bot.time.timeOfDay from mineflayer.

    if (req.body.spread) {
        bot.chat(`/spreadplayers ~ ~ 0 300 under 80 false @s`);
        await bot.waitForTicks(bot.waitTicks);
    }

    await bot.waitForTicks(bot.waitTicks * itemTicks);

    if (!hooks.isResponseSent()) {
        hooks.markResponseSent();
        res.json(bot.observe());
    }

    bot.chat("/gamerule keepInventory true");
    bot.chat("/gamerule doDaylightCycle false");
}

app.post("/start", (req, res) => {
    if (bot) onDisconnect("Restarting bot");
    bot = null;
    console.log(req.body);

    // --- 重试参数 ---
    const MAX_RETRIES = 5;
    const RETRY_DELAY_MS = 3000;
    let attempt = 0;
    let responseSent = false;

    function tryConnect() {
        attempt++;
        console.log(`[Mineflayer] Connection attempt ${attempt}/${MAX_RETRIES} ...`);

        bot = mineflayer.createBot({
            host: MC_HOST,              // Windows 宿主机虚拟网卡 IP
            port: req.body.port,         // 局域网开放的端口
            username: "bot",
            version: "1.19",             // 必须与 MC 版本一致
            auth: "offline",             // 绕过正版验证
            hideErrors: false,
            disableChatSigning: true,
            checkTimeoutInterval: 60 * 60 * 1000,
        });

        // 订阅事件
        bot.waitTicks = req.body.waitTicks;
        bot.globalTickCounter = 0;
        bot.stuckTickCounter = 0;
        bot.stuckPosList = [];
        bot.iron_pickaxe = false;

        bot.on("kicked", onDisconnect);
        bot.on("mount", () => { bot.dismount(); });

        // --- 连接失败处理（含重试）---
        bot.once("error", (e) => {
            console.log(`[Mineflayer] Error on attempt ${attempt}: ${e.message}`);
            bot = null;
            if (attempt < MAX_RETRIES) {
                console.log(`[Mineflayer] Retrying in ${RETRY_DELAY_MS}ms ...`);
                setTimeout(tryConnect, RETRY_DELAY_MS);
            } else {
                if (!responseSent) {
                    responseSent = true;
                    res.status(400).json({ error: `Failed after ${MAX_RETRIES} attempts: ${e.message}` });
                }
            }
        });

        bot.once("spawn", async () => {
            // spawn 后移除 error 重试监听，防止正常运行期间触发重试
            bot.removeAllListeners("error");
            bot.on("error", (e) => {
                console.log("[Mineflayer] Runtime error:", e.message);
            });
            try {
                installChatThrottle(bot);
                console.log("[Mineflayer] Chat throttle installed");
            } catch (e) {
                console.log("[Mineflayer] Chat throttle install failed:", e.message || e);
            }

            let itemTicks = 1;
            if (req.body.reset === "hard") {
                bot.chat("/clear @s");
                bot.chat("/kill @s");
                const inventory = req.body.inventory ? req.body.inventory : {};
                const equipment = req.body.equipment
                    ? req.body.equipment
                    : [null, null, null, null, null, null];
                for (let key in inventory) {
                    bot.chat(`/give @s minecraft:${key} ${inventory[key]}`);
                    itemTicks += 1;
                }
                const equipmentNames = [
                    "armor.head",
                    "armor.chest",
                    "armor.legs",
                    "armor.feet",
                    "weapon.mainhand",
                    "weapon.offhand",
                ];
                for (let i = 0; i < 6; i++) {
                    if (i === 4) continue;
                    if (equipment[i]) {
                        bot.chat(
                            `/item replace entity @s ${equipmentNames[i]} with minecraft:${equipment[i]}`
                        );
                        itemTicks += 1;
                    }
                }
            }

            if (req.body.position) {
                bot.chat(
                    `/tp @s ${req.body.position.x} ${req.body.position.y} ${req.body.position.z}`
                );
            }

            if (bot.inventory.items().find((item) => item.name === "iron_pickaxe")) {
                bot.iron_pickaxe = true;
            }

            const { pathfinder } = require("mineflayer-pathfinder");
            const tool = require("mineflayer-tool").plugin;
            const collectBlock = require("mineflayer-collectblock").plugin;
            const pvp = require("mineflayer-pvp").plugin;
            const minecraftHawkEye = require("minecrafthawkeye").default;
            bot.loadPlugin(pathfinder);
            bot.loadPlugin(tool);
            console.log("Debug - collectBlock type:", typeof collectBlock);
            bot.loadPlugin(collectBlock);
            bot.loadPlugin(pvp);
            bot.loadPlugin(minecraftHawkEye);

            obs.inject(bot, [
                OnChat,
                OnError,
                Voxels,
                Status,
                Inventory,
                OnSave,
                Chests,
                BlockRecords,
            ]);
            skills.inject(bot);

            if (req.body.spread) {
                bot.chat(`/spreadplayers ~ ~ 0 300 under 80 false @s`);
                await bot.waitForTicks(bot.waitTicks);
            }

            await bot.waitForTicks(bot.waitTicks * itemTicks);

            if (!responseSent) {
                responseSent = true;
                res.json(bot.observe());
            }

            initCounter(bot);
            bot.chat("/gamerule keepInventory true");
            bot.chat("/gamerule doDaylightCycle false");
        });
    }

    // 启动首次连接
    if (process.env.MOCK_BOT === "1") {
        runMockSpawn(req, res, {
            isResponseSent: () => responseSent,
            markResponseSent: () => {
                responseSent = true;
            },
            onDisconnect,
        }).catch((e) => {
            console.log("[MOCK_BOT] start failed:", e && e.message ? e.message : e);
            if (!responseSent) {
                responseSent = true;
                try {
                    res.status(500).json({
                        error: String(e && e.message ? e.message : e),
                    });
                } catch (err) {
                    console.log("[MOCK_BOT] start error after response:", err.message || err);
                }
            }
        });
    } else {
        tryConnect();
    }

    function onDisconnect(message) {
        if (bot && bot.viewer) {
            bot.viewer.close();
        }
        if (bot) bot.end();
        console.log("[Mineflayer] disconnected:", message);
        bot = null;
        // 2026-09-11 审计 P0-3 / 04 C-8：让等在 HTTP 上的请求立刻知道。
        failPendingStep("bot disconnected: " + message);
        if (!responseSent) {
            responseSent = true;
            res.status(503).json({ error: "bot disconnected before ready: " + message });
        }
    }
});

app.post("/step", async (req, res) => {
    let response_sent = false;
    if (!bot) {
        res.status(503).json({ error: "bot not connected (disconnected earlier)" });
        return;
    }
    pendingStep = {
        fail(reason) {
            if (!response_sent) {
                response_sent = true;
                res.status(503).json({ error: reason });
            }
        },
    };
    function otherError(err) {
        console.log("Uncaught Error");
        bot.emit("error", handleError(err));
        bot.waitForTicks(bot.waitTicks).then(() => {
            if (!response_sent) {
                response_sent = true;
                // 2026-09-11 审计 P0-1：这条早回包路径原来不经 attachSkillCalls，
                // 回给 Python 的观测没有 skillCalls/哨兵 → runtime 判定静默为零。
                // 0910 批实测 11–47% 的行没有哨兵，大半来自这里。
                res.json(attachSkillCalls(bot.observe()));
            }
        });
    }

    process.on("uncaughtException", otherError);

    const mcData = require("minecraft-data")(bot.version);
    mcData.itemsByName["leather_cap"] = mcData.itemsByName["leather_helmet"];
    mcData.itemsByName["leather_tunic"] = mcData.itemsByName["leather_chestplate"];
    mcData.itemsByName["leather_pants"] = mcData.itemsByName["leather_leggings"];
    mcData.itemsByName["leather_boots"] = mcData.itemsByName["leather_boots"];
    mcData.itemsByName["lapis_lazuli_ore"] = mcData.itemsByName["lapis_ore"];
    mcData.blocksByName["lapis_lazuli_ore"] = mcData.blocksByName["lapis_ore"];
    const {
        Movements,
        goals: {
            Goal, GoalBlock, GoalNear, GoalXZ, GoalNearXZ, GoalY,
            GoalGetToBlock, GoalLookAtBlock, GoalBreakBlock,
            GoalCompositeAny, GoalCompositeAll, GoalInvert,
            GoalFollow, GoalPlaceBlock,
        },
        pathfinder, Move, ComputedPath, PartiallyComputedPath,
        XZCoordinates, XYZCoordinates, SafeBlock, GoalPlaceBlockOptions,
    } = require("mineflayer-pathfinder");
    const { Vec3 } = require("vec3");

    const movements = new Movements(bot, mcData);
    bot.pathfinder.setMovements(movements);

    bot.globalTickCounter = 0;
    bot.stuckTickCounter = 0;
    bot.stuckPosList = [];

    function onTick() {
        bot.globalTickCounter++;
        if (bot.pathfinder.isMoving()) {
            bot.stuckTickCounter++;
            if (bot.stuckTickCounter >= 100) {
                onStuck(1.5);
                bot.stuckTickCounter = 0;
            }
        }
    }

    bot.on("physicTick", onTick);

    let _craftItemFailCount = 0;
    let _killMobFailCount = 0;
    let _mineBlockFailCount = 0;
    let _placeItemFailCount = 0;
    let _smeltItemFailCount = 0;

    const code = req.body.code;
    const programs = req.body.programs;
    bot.cumulativeObs = [];
    // 运行时调用计数：由 programs 末尾注入的包装函数写入（见 SkillManager.programs）。
    // 每步清零——统计的是"本轮真实调用"，不是累计值。
    globalThis.__skillCalls = {};
    globalThis.__skillOutcomes = [];
    await bot.waitForTicks(bot.waitTicks);
    const r = await evaluateCode(code, programs);
    process.off("uncaughtException", otherError);
    if (r !== "success") {
        bot.emit("error", handleError(r));
    }
    await returnItems();
    await bot.waitForTicks(bot.waitTicks);
    if (!response_sent) {
        response_sent = true;
        res.json(attachSkillCalls(bot.observe()));
    }
    pendingStep = null;
    bot.removeListener("physicTick", onTick);

    // bot.observe() 返回的是 JSON 字符串（Python 侧再 json.loads 一次）。
    // 这里把本轮运行时调用计数并进去；解析失败就原样返回，绝不影响主链路。
    function attachSkillCalls(obsStr) {
        try {
            const obs = JSON.parse(obsStr);
            // 🔴 obs 是**事件数组** [[type, data], ...]（bot.observe() 返回
            // JSON.stringify(bot.cumulativeObs)，cumulativeObs 是数组）。
            // 原来写成 obs.skillCalls = ... 是往数组上挂具名属性，而
            // JSON.stringify 对数组只序列化索引元素 —— 该属性被静默丢掉，
            // 无报错无警告，Python 侧永远收到 {}。
            // 2026-09-07 哨兵测试查出：库里有 10 个技能、注入确实发生，
            // 但回传的计数一次都没到 Python，全部栽在这一行。
            // Python 侧取的是 last_events[-1][1].get("skillCalls")，
            // 所以必须挂进**最后一个事件的 data 对象**。
            if (Array.isArray(obs) && obs.length > 0) {
                const last = obs[obs.length - 1];
                if (Array.isArray(last) && last.length > 1
                    && last[1] && typeof last[1] === "object") {
                    last[1].skillCalls = globalThis.__skillCalls || {};
                    last[1].skillOutcomes = globalThis.__skillOutcomes || [];
                }
            }
            return JSON.stringify(obs);
        } catch (e) {
            return obsStr;
        }
    }

    async function evaluateCode(code, programs) {
        try {
            await eval("(async () => {" + programs + "\n" + code + "})()");
            return "success";
        } catch (err) {
            return err;
        }
    }

    function onStuck(posThreshold) {
        const currentPos = bot.entity.position;
        bot.stuckPosList.push(currentPos);
        if (bot.stuckPosList.length === 5) {
            const oldestPos = bot.stuckPosList[0];
            const posDifference = currentPos.distanceTo(oldestPos);
            if (posDifference < posThreshold) {
                teleportBot();
            }
            bot.stuckPosList.shift();
        }
    }

    function teleportBot() {
        const blocks = bot.findBlocks({
            matching: (block) => { return block.type === 0; },
            maxDistance: 1,
            count: 27,
        });
        if (blocks) {
            const randomIndex = Math.floor(Math.random() * blocks.length);
            const block = blocks[randomIndex];
            bot.chat(`/tp @s ${block.x} ${block.y} ${block.z}`);
        } else {
            bot.chat("/tp @s ~ ~1.25 ~");
        }
    }

    function returnItems() {
        bot.chat("/gamerule doTileDrops false");
        const crafting_table = bot.findBlock({
            matching: mcData.blocksByName.crafting_table.id,
            maxDistance: 128,
        });
        if (crafting_table) {
            bot.chat(`/setblock ${crafting_table.position.x} ${crafting_table.position.y} ${crafting_table.position.z} air destroy`);
            bot.chat("/give @s crafting_table");
        }
        const furnace = bot.findBlock({
            matching: mcData.blocksByName.furnace.id,
            maxDistance: 128,
        });
        if (furnace) {
            bot.chat(`/setblock ${furnace.position.x} ${furnace.position.y} ${furnace.position.z} air destroy`);
            bot.chat("/give @s furnace");
        }
        if (bot.inventoryUsed() >= 32) {
            if (!bot.inventory.items().find((item) => item.name === "chest")) {
                bot.chat("/give @s chest");
            }
        }
        if (bot.iron_pickaxe && !bot.inventory.items().find((item) => item.name === "iron_pickaxe")) {
            bot.chat("/give @s iron_pickaxe");
        }
        bot.chat("/gamerule doTileDrops true");
    }

    function handleError(err) {
        let stack = err.stack;
        if (!stack) { return err; }
        console.log(stack);
        const final_line = stack.split("\n")[1];
        const regex = /<anonymous>:(\d+):\d+\)/;
        const programs_length = programs.split("\n").length;
        let match_line = null;
        for (const line of stack.split("\n")) {
            const match = regex.exec(line);
            if (match) {
                const line_num = parseInt(match[1]);
                if (line_num >= programs_length) {
                    match_line = line_num - programs_length;
                    break;
                }
            }
        }
        if (!match_line) { return err.message; }
        let f_line = final_line.match(/\((?<file>.*):(?<line>\d+):(?<pos>\d+)\)/);
        if (f_line && f_line.groups && fs.existsSync(f_line.groups.file)) {
            const { file, line, pos } = f_line.groups;
            const f = fs.readFileSync(file, "utf8").split("\n");
            let source = file + `:${line}\n${f[line - 1].trim()}\n `;
            const code_source = "at " + code.split("\n")[match_line - 1].trim() + " in your code";
            return source + err.message + "\n" + code_source;
        } else if (f_line && f_line.groups && f_line.groups.file.includes("<anonymous>")) {
            const { file, line, pos } = f_line.groups;
            let source = "Your code" + `:${match_line}\n${code.split("\n")[match_line - 1].trim()}\n `;
            let code_source = "";
            if (line < programs_length) {
                source = "In your program code: " + programs.split("\n")[line - 1].trim() + "\n";
                code_source = `at line ${match_line}:${code.split("\n")[match_line - 1].trim()} in your code`;
            }
            return source + err.message + "\n" + code_source;
        }
        return err.message;
    }
});

app.post("/stop", (req, res) => {
    bot.end();
    res.json({ message: "Bot stopped" });
});

app.post("/pause", (req, res) => {
    if (!bot) {
        res.status(400).json({ error: "Bot not spawned" });
        return;
    }
    bot.chat("/pause");
    bot.waitForTicks(bot.waitTicks).then(() => {
        res.json({ message: "Success" });
    });
});

// 09-13 暂停状态校验（--pause-verify 才会调用）：服务器暂停时不 tick、不发 update_time
// （每 20 tick 一次），据此判断服务器是否在走，本身不切换暂停。
app.post("/tick_probe", (req, res) => {
    if (!bot || !bot.entity) {
        res.status(400).json({ error: "Bot not spawned" });
        return;
    }
    const waitMs = (req.body && req.body.wait_ms) || 3000;
    const b = bot;
    let done = false;
    let timer = null;
    const finish = (ticking) => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        b.removeListener("time", onTime);
        b.removeListener("end", onEnd);
        res.json({ ticking });
    };
    const onTime = () => finish(true);
    const onEnd = () => finish(null);
    b.on("time", onTime);
    b.once("end", onEnd);
    timer = setTimeout(() => finish(false), waitMs);
});

const DEFAULT_PORT = 3000;
const PORT = process.argv[2] || DEFAULT_PORT;
if (process.env.MOCK_BOT === "1") {
    console.log("[MOCK_BOT] mode on");
    app.get("/mock/state", (req, res) => {
        const chatLog =
            bot && bot.mock && Array.isArray(bot.mock.chatLog)
                ? bot.mock.chatLog.slice()
                : [];
        res.json({ chatLog, hasBot: !!bot });
    });
    // Disconnect during /step nulls `bot` then the rest of the (unmodified)
    // /step handler throws. Keep the process alive so a later /step can
    // return 503 "bot not connected" instead of dying and being restarted.
    process.on("unhandledRejection", (e) => {
        console.log(
            "[MOCK_BOT] unhandledRejection:",
            e && e.message ? e.message : e
        );
    });
    process.on("uncaughtException", (e) => {
        console.log(
            "[MOCK_BOT] uncaughtException:",
            e && e.message ? e.message : e
        );
    });
}
const server = app.listen(PORT, () => {
    console.log(`Server started on port ${PORT}`);
});
// 2026-09-11 审计 P0-4：端口被残留 node 占住时以前是未捕获的 EADDRINUSE，
// 进程僵在那里、is_running 恒 True、bridge.check_process 无限循环。
// 现在打印原因并以非零码退出，bridge 侧 3 次后 RuntimeError。
server.on("error", (err) => {
    console.log(`[Mineflayer] listen on port ${PORT} failed: ${err.code || ""} ${err.message || err}`);
    process.exit(1);
});
