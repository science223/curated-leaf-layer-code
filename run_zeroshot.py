#!/usr/bin/env python3
"""v29 零样本泛化测试的命令行入口；逻辑在 voyager/zeroshot.py（参数、结果行、退出码见该文件）。

例（强模型、C1′ 世界 1 的库、4 个任务各 3 次）：
    python run_zeroshot.py --arm C1p \\
        --library-src /path/to/ckpt_A_flash_off_t1_v28NEW \\
        --mc-port 25565 --server-port 3000 \\
        --out zs_results.jsonl --work-dir zs_work
空库对照：把 --library-src 换成 --empty-library。
弱模型：加 --model-name deepseek/deepseek-v4-flash-0731 --api-base https://openrouter.ai/api/v1
        --api-key-env OPENROUTER_API_KEY --provider-order DeepInfra,Novita（与 run_voyager.py 同义）。
"""
import os
import sys

# 与 run_voyager.py 相同：导入 voyager 会立刻加载本机缓存的 MiniLM，先设离线。
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_DATASETS_OFFLINE", "1")
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
# 两个源运行都开着运行时探针；必须在导入 voyager.agents.skill 之前设。
# 显式设成 0 时 voyager.zeroshot 会以配置错误退出（退出码 1）。
os.environ.setdefault("RUNTIME_CALL_PROBE", "1")


if __name__ == "__main__":
    # venv 是 ~/Voyager/venv 的软链、装着那一份 voyager 的 editable 包；本目录放最前，用这一份代码。
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from voyager.zeroshot import main

        _code = main(sys.argv[1:])
    except SystemExit as _exit:  # argparse：参数错 2、--help 0
        _code = _exit.code if isinstance(_exit.code, int) else (0 if _exit.code is None else 1)
    except BaseException:  # noqa: BLE001 —— 兜住一切，保证下面的强制退出执行得到
        import traceback

        traceback.print_exc()
        _code = 1
    sys.stdout.flush()
    sys.stderr.flush()
    # 与 run_voyager.py 同：强制退出，不等残留的非守护线程；也跳过 chromadb 的 atexit
    # persist（否则进程退出时它会把 parquet 重写进各次尝试的库副本）。
    os._exit(_code)
