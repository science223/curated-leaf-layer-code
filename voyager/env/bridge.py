import os.path
import time
import warnings
from typing import SupportsFloat, Any, Tuple, Dict

import requests
import json

import gymnasium as gym
from gymnasium.core import ObsType

import voyager.utils as U

from .minecraft_launcher import MinecraftInstance
from .process_monitor import SubprocessMonitor

# 桥接 POST 的重试参数。桥接会在服务端 pause 存盘期间偶发掉线，
# 见 实验方案.md §4.1（2026-09-02 实测：两次 smoke 崩一次）。
BRIDGE_POST_RETRIES = 3
BRIDGE_POST_RETRY_SLEEP = 1.0
BRIDGE_POST_TIMEOUT = 10

# 09-13 暂停状态校验（--pause-verify，默认关）。/pause 是切换式命令，Python 只凭自记的
# server_paused 猜服务器状态；v24（机器人为 op、/pause 首次生效）实测部分运行里记录与实际
# 相反 → 执行代码时世界冻结（熔炉不走、机器人不动）。开关打开时，每次执行前用 /tick_probe
# 看服务器是否真的在走（冻结时不发 update_time），不在走就再切换，最多 PAUSE_VERIFY_MAX_TOGGLES 次。
PAUSE_VERIFY_MAX_TOGGLES = 3
PAUSE_VERIFY_PROBE_MS = 3000


class StepExecutionTimeout(RuntimeError):
    """生成的代码在 mineflayer 里跑超了 /step 的读超时，但桥接本身是活的。

    区分依据是 requests 抛的异常类型本身，不需要再探活：
      - ReadTimeout  —— 连接已建立、请求已被接受，只是没在超时内返回，
                        说明桥接活着，是**代码跑太久**
      - ConnectionError —— 连都连不上，那才是桥接死了

    为什么要分开：env_dead_after 的本意是"环境死了就别耗下去"。
    把"模型写了个 5×120 秒的探索循环"算成环境死亡，会让一次运气不好的
    出题直接废掉整个运行——2026-09-04 的 A:t3 就是这么死的：
    连续 5 次 `Mine 1 wood log` 都卡在 exploreUntil 上超时，第 9 轮就被判死。
    """


class VoyagerEnv(gym.Env):
    def __init__(
        self,
        mc_port=None,
        azure_login=None,
        server_host="http://127.0.0.1",
        server_port=3000,
        request_timeout=150,
        log_path="./logs",
        pause_verify=False,
    ):
        if not mc_port and not azure_login:
            raise ValueError("Either mc_port or azure_login must be specified")
        if mc_port and azure_login:
            warnings.warn(
                "Both mc_port and mc_login are specified, mc_port will be ignored"
            )
        self.mc_port = mc_port
        self.azure_login = azure_login
        self.server = f"{server_host}:{server_port}"
        self.server_port = server_port
        self.request_timeout = request_timeout
        self.log_path = log_path
        self.mineflayer = self.get_mineflayer_process(server_port)
        if azure_login:
            self.mc_instance = self.get_mc_instance()
        else:
            self.mc_instance = None
        self.has_reset = False
        self.reset_options = None
        self.connected = False
        self.server_paused = False
        self.pause_verify = bool(pause_verify)
        self.pause_mismatch_count = 0

    def get_mineflayer_process(self, server_port):
        U.f_mkdir(self.log_path, "mineflayer")
        file_path = os.path.abspath(os.path.dirname(__file__))
        return SubprocessMonitor(
            commands=[
                "node",
                U.f_join(file_path, "mineflayer/index.js"),
                str(server_port),
            ],
            name="mineflayer",
            ready_match=r"Server started on port (\d+)",
            log_path=U.f_join(self.log_path, "mineflayer"),
        )

    def get_mc_instance(self):
        print("Creating Minecraft server")
        U.f_mkdir(self.log_path, "minecraft")
        return MinecraftInstance(
            **self.azure_login,
            mineflayer=self.mineflayer,
            log_path=U.f_join(self.log_path, "minecraft"),
        )

    def check_process(self):
        if self.mc_instance and not self.mc_instance.is_running:
            # if self.mc_instance:
            #     self.mc_instance.check_process()
            #     if not self.mc_instance.is_running:
            print("Starting Minecraft server")
            self.mc_instance.run()
            self.mc_port = self.mc_instance.port
            self.reset_options["port"] = self.mc_instance.port
            print(f"Server started on port {self.reset_options['port']}")
        retry = 0
        while not self.mineflayer.is_running:
            print("Mineflayer process has exited, restarting")
            self.mineflayer.run()
            if not self.mineflayer.is_running:
                # 2026-09-11 审计 P0-4：retry 原来从不自增，端口被残留 node 占住时
                # 这里无限循环，不报错不退出，看护器因进程活着永不介入。
                retry += 1
                if retry > 3:
                    raise RuntimeError(
                        "Mineflayer process failed to start after "
                        f"{retry} attempts (port {self.server_port} busy? "
                        "see logs/mineflayer/)"
                    )
                else:
                    time.sleep(1)
                    continue
            print(self.mineflayer.ready_line)
            res = requests.post(
                f"{self.server}/start",
                json=self.reset_options,
                timeout=self.request_timeout,
            )
            if res.status_code != 200:
                try:
                    detail = res.json()
                except Exception:
                    detail = res.text
                self.mineflayer.stop()
                raise RuntimeError(
                    f"Minecraft server reply with code {res.status_code}: {detail}"
                )
            return res.json()

    def step(
        self,
        code: str,
        programs: str = "",
    ) -> Tuple[ObsType, SupportsFloat, bool, bool, Dict[str, Any]]:
        if not self.has_reset:
            raise RuntimeError("Environment has not been reset yet")
        self.check_process()
        self.unpause()
        data = {
            "code": code,
            "programs": programs,
        }
        try:
            res = requests.post(
                f"{self.server}/step", json=data, timeout=self.request_timeout
            )
        except requests.exceptions.ReadTimeout as err:
            # 桥接接受了请求却没在超时内返回 = 生成的代码跑太久，不是环境死了
            raise StepExecutionTimeout(
                f"生成的代码在 {self.request_timeout}s 内没跑完（桥接仍在响应）。"
                f"按任务失败处理，不计入 env_dead_after。"
            ) from err
        if res.status_code != 200:
            # 2026-09-11 审计 P0-3：mineflayer 侧断线/被踢时现在会立即回 503 并带原因
            # （以前只 console.log，Python 等满 300s 当成 StepExecutionTimeout）。
            # 把原因带进异常，learn() 按真崩溃计数，日志与 iterations.jsonl.error 可见。
            try:
                detail = res.json()
            except Exception:
                detail = (res.text or "")[:300]
            raise RuntimeError(
                f"Failed to step Minecraft server (HTTP {res.status_code}): {detail}"
            )
        returned_data = res.json()
        self.pause()
        return json.loads(returned_data)

    def render(self):
        raise NotImplementedError("render is not implemented")

    def reset(
        self,
        *,
        seed=None,
        options=None,
    ) -> Tuple[ObsType, Dict[str, Any]]:
        if options is None:
            options = {}

        if options.get("inventory", {}) and options.get("mode", "hard") != "hard":
            raise RuntimeError("inventory can only be set when options is hard")

        self.reset_options = {
            "port": self.mc_port,
            "reset": options.get("mode", "hard"),
            "inventory": options.get("inventory", {}),
            "equipment": options.get("equipment", []),
            "spread": options.get("spread", False),
            "waitTicks": options.get("wait_ticks", 5),
            "position": options.get("position", None),
        }

        self.unpause()
        self.mineflayer.stop()
        time.sleep(1)  # wait for mineflayer to exit

        returned_data = self.check_process()
        self.has_reset = True
        self.connected = True
        # All the reset in step will be soft
        self.reset_options["reset"] = "soft"
        self.pause()
        return json.loads(returned_data)

    def close(self):
        self.unpause()
        if self.connected:
            res = self._post_bridge("/stop")
            # 桥接已死时 res 为 None：进程本来就没了，直接视为已断开
            if res is None or res.status_code == 200:
                self.connected = False
        if self.mc_instance:
            self.mc_instance.stop()
        self.mineflayer.stop()
        return not self.connected

    def _post_bridge(self, path):
        """向 mineflayer 桥接发 POST。

        桥接可能在服务端 pause 存盘期间掉线（bot lost connection），
        此时 self.mineflayer.is_running 还是陈旧的 True，直接 post 会抛
        ConnectionError 并终止整个运行。这里改为重试若干次后返回 None，
        由调用方复位状态，交给 check_process() 的重启循环接管。
        """
        last_err = None
        for attempt in range(BRIDGE_POST_RETRIES):
            try:
                return requests.post(
                    f"{self.server}{path}", timeout=BRIDGE_POST_TIMEOUT
                )
            except (
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
            ) as err:
                last_err = err
                if attempt < BRIDGE_POST_RETRIES - 1:
                    time.sleep(BRIDGE_POST_RETRY_SLEEP)
        print(
            f"[bridge] {path} 连不上，重试 {BRIDGE_POST_RETRIES} 次后放弃："
            f"{last_err}。交给 check_process() 重启 mineflayer。"
        )
        return None

    def pause(self):
        if self.mineflayer.is_running and not self.server_paused:
            res = self._post_bridge("/pause")
            if res is None:
                # 桥接已死：维持 server_paused 原值，重启后由 reset 重新建立
                return self.server_paused
            if res.status_code == 200:
                self.server_paused = True
        return self.server_paused

    def unpause(self):
        if self.pause_verify:
            return self._unpause_verified()
        if self.mineflayer.is_running and self.server_paused:
            res = self._post_bridge("/pause")
            if res is None:
                # 桥接已死：暂停状态无从维持，复位以免后续误判仍处于暂停
                self.server_paused = False
                return self.server_paused
            if res.status_code == 200:
                self.server_paused = False
            else:
                print(res.json())
        return self.server_paused

    def _tick_probe(self):
        """服务器此刻是否在走：True / False；桥接不可用或机器人不在线时 None（判断不了）。"""
        try:
            res = requests.post(
                f"{self.server}/tick_probe",
                json={"wait_ms": PAUSE_VERIFY_PROBE_MS},
                timeout=BRIDGE_POST_TIMEOUT,
            )
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            return None
        if res.status_code != 200:
            return None
        return res.json().get("ticking")

    def _unpause_verified(self):
        """unpause 的校验版：以服务器是否真的在走为准，不以自记状态为准。"""
        if not self.mineflayer.is_running:
            return self.server_paused
        for _ in range(PAUSE_VERIFY_MAX_TOGGLES + 1):
            if self.server_paused:
                res = self._post_bridge("/pause")
                if res is None or res.status_code != 200:
                    # 与原 unpause 相同：切换没发出去就判断不了，交给后续流程
                    self.server_paused = False
                    return self.server_paused
                self.server_paused = False
            ticking = self._tick_probe()
            if ticking is None or ticking:
                return self.server_paused
            self.pause_mismatch_count += 1
            print(
                f"[bridge] pause-verify：记录为运行但服务器实际暂停（第 {self.pause_mismatch_count} 次），再切换一次"
            )
            self.server_paused = True
        print("[bridge] pause-verify：多次切换后仍未确认服务器在走，按原流程继续")
        return self.server_paused
