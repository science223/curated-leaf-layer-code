"""v29 零样本启动器离线测试：不起 Minecraft、不调 API。起服和 worker 都换成假的。"""
import collections
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("launch_zs", ROOT / "_cc0911" / "launch_zs.py")
Z = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(Z)


def test_build_jobs_counts_tags_and_balance():
    jobs = Z.build_jobs(Z.PLANS["zs_existing_v41"])
    assert len(jobs) == 8 * 4 * 3
    assert len({j["tag"] for j in jobs}) == len(jobs)
    by_machine = collections.Counter(j["machine"] for j in jobs)
    assert set(by_machine) == {"admin1", "slac002"}
    # 两台机器上各臂比例接近（机器快慢不和臂混在一起）
    for m in by_machine:
        mine = [j for j in jobs if j["machine"] == m]
        share_old = sum(1 for j in mine if j["preset"] == "OLD") / len(mine)
        assert 0.4 <= share_old <= 0.6, (m, share_old)
    # 每个库每个任务正好 3 次
    per = collections.Counter((j["lib_id"], j["task_code"]) for j in jobs)
    assert set(per.values()) == {3}


def test_zs_args_library_paths_and_flags():
    plan = Z.PLANS["zs_existing_v41"]
    jobs = {j["lib_id"]: j for j in Z.build_jobs(plan)}
    out = pathlib.Path("/tmp/x")
    a = Z.zs_args(jobs["OLD_w1"], 25610, 3110, plan, "admin1", out)
    assert a[a.index("--library-src") + 1].startswith("/mnt/e/homework/research/voyager/_audit_0911/")
    assert a[a.index("--world-seed") + 1] == "20260902099"
    assert a[a.index("--arm") + 1] == "OLD"
    assert a[a.index("--attempt-id") + 1] == jobs["OLD_w1"]["tag"]
    assert "--empty-library" not in a
    b = Z.zs_args(jobs["EMPTY_C1p"], 25620, 3120, plan, "slac002", out)
    assert "--empty-library" in b and "--library-src" not in b
    assert b[b.index("--arm") + 1] == "C1p"
    c = Z.zs_args(jobs["C1p_w2"], 25620, 3120, plan, "slac002", out)
    assert c[c.index("--library-src") + 1].startswith("/home/user/zs_libs/_audit_0911/")


def test_result_row_takes_last_matching_and_skips_garbage():
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        (d / Z.RESULTS_NAME).write_text(
            json.dumps({"attempt_id": "A", "stopped_reason": "crash"}) + "\n"
            + "not json\n\n"
            + json.dumps({"attempt_id": "B", "stopped_reason": "cap"}) + "\n"
            + json.dumps({"attempt_id": "A", "stopped_reason": "success"}) + "\n",
            encoding="utf-8")
        assert Z.result_row(d, "A")["stopped_reason"] == "success"
        assert Z.result_row(d, "B")["stopped_reason"] == "cap"
        assert Z.result_row(d, "C") is None


class _FakeSrv:
    def poll(self):
        return None


import contextlib  # noqa: E402  （放这里是为了紧挨下面的夹具）


@contextlib.contextmanager
def fake_cluster(tmp, *, lanes, worker, start_mc=None, port_free=None, stop_proc=None, plan=None):
    """把起服、worker、端口、机器信息都换成假的；退出时全部还原。

    lanes: {lane: (mc_port, bridge_port)}；worker(out_dir, tag, args, env) 返回一个真进程。
    """
    L = Z.L
    names = ("start_mc", "start_worker", "load_ports", "detect_machine", "find_java17",
             "build_env", "port_free", "stop_proc")
    saved = {k: getattr(L, k) for k in names}
    saved_root, saved_sleep = Z.ROOT, Z.time.sleep

    def default_start_mc(out_dir, tag, mc_port, seed, java, env, op_bot=False):
        lane = out_dir / f"{tag}_server"
        lane.mkdir(exist_ok=True)
        (lane / "server.properties").write_text(f"level-seed={seed}\n")
        return _FakeSrv(), None

    Z.ROOT = pathlib.Path(tmp)
    L.start_mc = start_mc or default_start_mc
    L.start_worker = worker
    L.load_ports = lambda: {"machines": {"admin1": {"max_parallel": len(lanes), "lanes": {
        str(ln): {"mc": mc, "bridge": br} for ln, (mc, br) in lanes.items()}}}}
    L.detect_machine = lambda ports, override=None: "admin1"
    L.find_java17 = lambda cfg: "/usr/bin/java"
    L.build_env = lambda key_env: {}
    L.port_free = port_free or (lambda p: True)
    L.stop_proc = stop_proc or (lambda p, grace=30: None)
    Z.time.sleep = lambda s: saved_sleep(0.05)
    if plan:
        Z.PLANS["_t"] = plan
    try:
        yield pathlib.Path(tmp) / "runs_0911" / "_t"
    finally:
        for k, v in saved.items():
            setattr(L, k, v)
        Z.ROOT, Z.time.sleep = saved_root, saved_sleep
        Z.PLANS.pop("_t", None)


def _plan(libs, **kw):
    base = dict(libs=libs, tasks=[("gs", "Craft 1 golden sword")], trials=1, max_rounds=5,
                model=None, api_base=None, key_env="K", providers="",
                smoke=False, op_bot=True, machine_weights={"admin1": 1})
    base.update(kw)
    return base


def _worker_writing(reason, *, exit_code=0, extra=None, sleep_s=0):
    """返回一个 worker 假函数：写一行结果（可加字段），可选先睡一会儿。"""
    def worker(out_dir, tag, args, env):
        row = {"attempt_id": tag, "stopped_reason": reason, "rounds_used": 3}
        row.update(extra or {})
        code = ("import json,sys,time;time.sleep(%r);"
                "open(sys.argv[1],'a').write(json.dumps(%r)+'\\n');sys.exit(%d)"
                % (sleep_s, row, exit_code))
        return subprocess.Popen([sys.executable, "-c", code, str(out_dir / Z.RESULTS_NAME)])
    return worker


def test_lifecycle_done_retry_fail_and_cleanup(monkeypatch=None):
    """两库 × 1 任务 × 1 次：库 1 第一次就 success；库 2 前两次 crash、第三次 cap；
    再加一个起服总是失败的库 3 → 重试用完记 FAIL。世界目录全部删掉。"""
    L = Z.L
    saved = {k: getattr(L, k) for k in ("start_mc", "start_worker", "load_ports", "detect_machine",
                                         "find_java17", "build_env", "port_free", "stop_proc")}
    saved_root, saved_sleep = Z.ROOT, Z.time.sleep
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        Z.ROOT = tmp
        attempts = collections.Counter()
        worlds_seen = []

        def fake_start_mc(out_dir, tag, mc_port, seed, java, env, op_bot=False):
            if "LIB3" in tag:
                return None, "Minecraft 启动即退出"
            lane = out_dir / f"{tag}_server"
            assert not lane.exists(), "起服前世界目录应已删除（全新世界）"
            lane.mkdir()
            (lane / "server.properties").write_text(f"level-seed={seed}\n")
            worlds_seen.append((tag, seed))
            return _FakeSrv(), None

        def fake_start_worker(out_dir, tag, args, env):
            attempts[tag] += 1
            n = attempts[tag]
            reason = "success" if "LIB1" in tag else ("crash" if n <= 2 else "cap")
            row = {"attempt_id": tag, "stopped_reason": reason, "rounds_used": 7}
            code = ("import json,sys;open(sys.argv[1],'a').write(json.dumps(%r)+'\\n');"
                    "sys.exit(0 if %r in ('success','cap') else 3)") % (row, reason)
            return subprocess.Popen([sys.executable, "-c", code, str(out_dir / Z.RESULTS_NAME)])

        L.start_mc = fake_start_mc
        L.start_worker = fake_start_worker
        L.load_ports = lambda: {"machines": {"admin1": {"max_parallel": 2, "lanes": {
            "0": {"mc": 1, "bridge": 2}, "1": {"mc": 3, "bridge": 4}}}}}
        L.detect_machine = lambda ports, override=None: "admin1"
        L.find_java17 = lambda cfg: "/usr/bin/java"
        L.build_env = lambda key_env: {}
        L.port_free = lambda p: True
        L.stop_proc = lambda p, grace=30: None
        Z.time.sleep = lambda s: saved_sleep(0.05)
        Z.PLANS["_t"] = dict(
            libs={"LIB1": ("OLD", "x/ck1"), "LIB2": ("C1p", "x/ck2"), "LIB3": ("OLD", None)},
            tasks=[("gs", "Craft 1 golden sword")], trials=1, max_rounds=5,
            model=None, api_base=None, key_env="K", providers="",
            smoke=False, op_bot=True, machine_weights={"admin1": 1},
        )
        try:
            assert Z.main(["--plan", "_t"]) == 0
            out = tmp / "runs_0911" / "_t"
            assert (out / "ZS_LIB1_gs_t1.DONE").exists()
            assert json.loads((out / "ZS_LIB2_gs_t1.DONE").read_text())["reason"] == "cap"
            assert (out / "ZS_LIB2_gs_t1.retries").read_text() == "2"
            assert attempts["ZS_LIB2_gs_t1"] == 3
            assert (out / "ZS_LIB3_gs_t1.FAIL").exists()
            assert not list(out.glob("*_server")), "世界目录应全部删除"
            assert all(seed == 20260902099 for _, seed in worlds_seen)
            assert not (out / "launcher.pid").exists()
            # 再跑一遍：全部已 DONE/FAIL，不应再起任何作业
            before = sum(attempts.values())
            assert Z.main(["--plan", "_t"]) == 0
            assert sum(attempts.values()) == before
        finally:
            for k, v in saved.items():
                setattr(L, k, v)
            Z.ROOT, Z.time.sleep = saved_root, saved_sleep
            Z.PLANS.pop("_t", None)


def test_dry_run_prints_commands_only():
    L = Z.L
    saved = {k: getattr(L, k) for k in ("start_mc", "start_worker", "load_ports", "detect_machine",
                                         "find_java17", "port_free")}
    saved_root = Z.ROOT
    with tempfile.TemporaryDirectory() as tmp:
        Z.ROOT = pathlib.Path(tmp)
        L.start_mc = L.start_worker = lambda *a, **k: (_ for _ in ()).throw(AssertionError("dry-run 不应起服"))
        L.load_ports = lambda: {"machines": {"admin1": {"max_parallel": 3, "lanes": {
            "0": {"mc": 1, "bridge": 2}, "8": {"mc": 5, "bridge": 6, "use": "smoke"}}}}}
        L.detect_machine = lambda ports, override=None: "admin1"
        L.find_java17 = lambda cfg: "/usr/bin/java"
        L.port_free = lambda p: True
        try:
            assert Z.main(["--plan", "zs_smoke_v41", "--dry-run"]) == 0
            log = (pathlib.Path(tmp) / "runs_0911" / "zs_smoke_v41" / "launcher.log").read_text(encoding="utf-8")
            assert "CMD ZS_OLD_w1_gs_t1" in log and "run_zeroshot.py" in log and "--max-rounds 5" in log
        finally:
            for k, v in saved.items():
                setattr(L, k, v)
            Z.ROOT = saved_root


def test_port_busy_lane_excluded_at_start_not_whole_queue():
    """起跑前 lane0 的端口就被占：只停用 lane0，作业照样在 lane1 全部跑完，一个 FAIL 都没有。"""
    started = []

    def worker(out_dir, tag, args, env):
        started.append(tag)
        return _worker_writing("success")(out_dir, tag, args, env)

    with tempfile.TemporaryDirectory() as tmp:
        with fake_cluster(tmp, lanes={0: (1, 2), 1: (3, 4)}, worker=worker,
                          port_free=lambda p: p not in (1, 2),
                          plan=_plan({"L1": ("OLD", "x/a"), "L2": ("C1p", "x/b")})) as out:
            assert Z.main(["--plan", "_t"]) == 0
            assert sorted(p.name for p in out.glob("*.DONE")) == ["ZS_L1_gs_t1.DONE", "ZS_L2_gs_t1.DONE"]
            assert not list(out.glob("*.FAIL"))
            assert len(started) == 2
            assert "本次不用这些 lane" in (out / "launcher.log").read_text(encoding="utf-8")


def test_port_busy_midrun_requeues_job_to_another_lane():
    """起跑时端口都空闲，跑起来以后 lane0 的端口被别人占：作业放回队列换 lane1，不记 FAIL。"""
    calls = {"n": 0}

    def port_free(p):
        calls["n"] += 1
        return not (p in (1, 2) and calls["n"] > 4)   # 前 4 次是起跑前的检查

    with tempfile.TemporaryDirectory() as tmp:
        with fake_cluster(tmp, lanes={0: (1, 2), 1: (3, 4)}, worker=_worker_writing("cap"),
                          port_free=port_free,
                          plan=_plan({"L1": ("OLD", "x/a")})) as out:
            assert Z.main(["--plan", "_t"]) == 0
            assert (out / "ZS_L1_gs_t1.DONE").exists()
            assert not list(out.glob("*.FAIL"))
            log = (out / "launcher.log").read_text(encoding="utf-8")
            assert "放回队列" in log and "lane1" in log


def test_refuses_to_start_when_a_job_pid_is_alive():
    """还有作业在跑（pid 活着）时拒绝起跑，不碰任何东西。"""
    with tempfile.TemporaryDirectory() as tmp:
        with fake_cluster(tmp, lanes={0: (1, 2)},
                          worker=lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该起 worker")),
                          plan=_plan({"L1": ("OLD", "x/a")})) as out:
            out.mkdir(parents=True, exist_ok=True)
            (out / "ZS_L1_gs_t1.pid").write_text(str(os.getpid()))
            assert Z.main(["--plan", "_t"]) == 1
            assert not list(out.glob("*.DONE")) and not list(out.glob("*.FAIL"))
            assert not (out / "launcher.pid").exists()


def test_library_changed_row_is_not_counted_as_done():
    """worker 退出码 4 或结果行写了 library_changed：不算成功，重试用完记 FAIL。"""
    with tempfile.TemporaryDirectory() as tmp:
        with fake_cluster(tmp, lanes={0: (1, 2)},
                          worker=_worker_writing("cap", exit_code=4, extra={"library_changed": True}),
                          plan=_plan({"L1": ("OLD", "x/a")})) as out:
            assert Z.main(["--plan", "_t"]) == 0
            assert not (out / "ZS_L1_gs_t1.DONE").exists()
            assert "library_changed" in (out / "ZS_L1_gs_t1.FAIL").read_text(encoding="utf-8")


def test_job_timeout_kills_worker_and_marks_fail():
    """单作业超过墙钟上限：强停、重试，最后记 job_timeout。"""
    def stop_proc(p, grace=30):
        if p is not None and p.poll() is None and hasattr(p, "kill"):   # 假服务器没有 kill
            p.kill()
            p.wait(timeout=10)

    with tempfile.TemporaryDirectory() as tmp:
        with fake_cluster(tmp, lanes={0: (1, 2)}, worker=_worker_writing("cap", sleep_s=30),
                          stop_proc=stop_proc, plan=_plan({"L1": ("OLD", "x/a")})) as out:
            assert Z.main(["--plan", "_t", "--job-timeout-h", "0.0004"]) == 0   # 约 1.4 秒
            assert not (out / "ZS_L1_gs_t1.DONE").exists()
            assert "job_timeout" in (out / "ZS_L1_gs_t1.FAIL").read_text(encoding="utf-8")


def test_stale_minecraft_log_is_rotated_before_start():
    """起服前把旧的 MC 日志改名，免得旧日志里的 'Done (' 骗过就绪检测。"""
    with tempfile.TemporaryDirectory() as tmp:
        with fake_cluster(tmp, lanes={0: (1, 2)}, worker=_worker_writing("success"),
                          plan=_plan({"L1": ("OLD", "x/a")})) as out:
            out.mkdir(parents=True, exist_ok=True)
            (out / "ZS_L1_gs_t1_minecraft.log").write_text("Done (12.3s)! For help, type help\n")
            assert Z.main(["--plan", "_t"]) == 0
            rotated = list(out.glob("ZS_L1_gs_t1_minecraft.log.*"))
            assert len(rotated) == 1 and "Done (" in rotated[0].read_text(encoding="utf-8")


def test_trials_max_and_ignore_machine_in_dry_run():
    """本机全包 + 只跑每题第 1 次：正好 8 库 × 4 任务 = 32 个作业，全是 _t1，且排在最前。"""
    L = Z.L
    saved = {k: getattr(L, k) for k in ("start_mc", "start_worker", "load_ports", "detect_machine",
                                         "find_java17", "port_free")}
    saved_root = Z.ROOT
    with tempfile.TemporaryDirectory() as tmp:
        Z.ROOT = pathlib.Path(tmp)
        L.start_mc = L.start_worker = lambda *a, **k: (_ for _ in ()).throw(AssertionError("dry-run 不应起服"))
        L.load_ports = lambda: {"machines": {"admin1": {"max_parallel": 3, "lanes": {
            "0": {"mc": 1, "bridge": 2}, "1": {"mc": 3, "bridge": 4}, "2": {"mc": 5, "bridge": 6}}}}}
        L.detect_machine = lambda ports, override=None: "admin1"
        L.find_java17 = lambda cfg: "/usr/bin/java"
        L.port_free = lambda p: True
        try:
            assert Z.main(["--plan", "zs_existing_v41", "--dry-run", "--ignore-machine", "--trials-max", "1"]) == 0
            log = (pathlib.Path(tmp) / "runs_0911" / "zs_existing_v41" / "launcher.log").read_text(encoding="utf-8")
            tags = [ln.split()[2] for ln in log.splitlines() if " CMD " in ln]
            assert len(tags) == 32, len(tags)
            assert all(t.endswith("_t1") for t in tags)
            assert len({t.split("_t1")[0].split("_", 1)[1].rsplit("_", 1)[0] for t in tags}) == 8
        finally:
            for k, v in saved.items():
                setattr(L, k, v)
            Z.ROOT = saved_root
    # 顺序：不加 --trials-max 时，第 1 次的 32 个作业本来就排在最前
    jobs = Z.build_jobs(Z.PLANS["zs_existing_v41"])
    assert all(j["trial"] == 1 for j in jobs[:32]) and all(j["trial"] > 1 for j in jobs[32:])


def test_result_row_only_counts_rows_written_after_the_job_started():
    """同一个 tag 重试时，上一次留下的旧行不算数：只看 offset 之后追加的行。"""
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        path = d / Z.RESULTS_NAME
        assert Z.results_offset(d) == 0
        path.write_text(json.dumps({"attempt_id": "A", "stopped_reason": "success",
                                    "pad": "x" * 200}) + "\n", encoding="utf-8")
        off = Z.results_offset(d)
        assert off > 200
        assert Z.result_row(d, "A")["stopped_reason"] == "success"      # 不给 offset：还是读全文
        assert Z.result_row(d, "A", off) is None                        # 这一刻之后还没写新行
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"attempt_id": "A", "stopped_reason": "cap"}) + "\n")
        assert Z.result_row(d, "A", off)["stopped_reason"] == "cap"      # 只认新行
        # 文件比 offset 还短（被截短/轮换）：这些行归不了属，宁可当没结果重试，也不能凭它判 DONE
        path.write_text(json.dumps({"attempt_id": "A", "stopped_reason": "env_crash"}) + "\n",
                        encoding="utf-8")
        assert Z.result_row(d, "A", off) is None
        assert Z.result_row(d, "A")["stopped_reason"] == "env_crash"   # 不给 offset 才读全文


def test_stale_result_row_does_not_make_a_crashed_job_done():
    """结果文件里已有同 tag 的旧成功行；这一次崩了、一行没写 → 记 FAIL，不能凭旧行判 DONE。"""
    def worker(out_dir, tag, args, env):
        return subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(1)"])

    with tempfile.TemporaryDirectory() as tmp:
        with fake_cluster(tmp, lanes={0: (1, 2)}, worker=worker,
                          plan=_plan({"L1": ("OLD", "x/a")})) as out:
            out.mkdir(parents=True, exist_ok=True)
            (out / Z.RESULTS_NAME).write_text(
                json.dumps({"attempt_id": "ZS_L1_gs_t1", "stopped_reason": "success",
                            "rounds_used": 3}) + "\n", encoding="utf-8")
            assert Z.main(["--plan", "_t"]) == 0
            assert not (out / "ZS_L1_gs_t1.DONE").exists()
            assert (out / "ZS_L1_gs_t1.FAIL").exists()


def test_drop_world_moves_aside_when_it_cannot_delete():
    """世界目录：能删就删；删不掉就改名挪开；两样都不成才返回 False。"""
    def boom(*a, **k):
        raise OSError("busy")

    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp)
        lane = out / "T_server"
        (lane / "world").mkdir(parents=True)
        assert Z.drop_world(out, "T") is True and not lane.exists()
        assert Z.drop_world(out, "T") is True                 # 本来就没有
        (lane / "world").mkdir(parents=True)
        saved_rmtree = Z.shutil.rmtree
        Z.shutil.rmtree = boom
        try:
            assert Z.drop_world(out, "T") is True and not lane.exists()
            assert len(list(out.glob("T_server.stale.*"))) == 1
            (lane / "world").mkdir(parents=True)
            saved_rename = pathlib.Path.rename
            pathlib.Path.rename = boom
            try:
                assert Z.drop_world(out, "T") is False and lane.exists()
            finally:
                pathlib.Path.rename = saved_rename
        finally:
            Z.shutil.rmtree = saved_rmtree


def test_dirty_world_blocks_start_and_marks_fail():
    """世界目录既删不掉也挪不开：不允许在脏世界上跑 → 不起 worker，重试用完记 FAIL。"""
    with tempfile.TemporaryDirectory() as tmp:
        with fake_cluster(tmp, lanes={0: (1, 2)},
                          worker=lambda *a, **k: (_ for _ in ()).throw(AssertionError("脏世界不该起 worker")),
                          plan=_plan({"L1": ("OLD", "x/a")})) as out:
            saved = Z.drop_world
            Z.drop_world = lambda out_dir, tag: False
            try:
                assert Z.main(["--plan", "_t"]) == 0
            finally:
                Z.drop_world = saved
            assert not (out / "ZS_L1_gs_t1.DONE").exists()
            assert "world_dir_not_removed" in (out / "ZS_L1_gs_t1.FAIL").read_text(encoding="utf-8")


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                fails += 1
                print(f"FAIL {name}: {e!r}")
    print("all passed" if not fails else f"{fails} failed")
    sys.exit(1 if fails else 0)
