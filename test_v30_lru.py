"""v30 item 1: split bookkeeping from LRU eviction. Default keeps current behavior."""
import os
import tempfile

os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("OPENAI_API_BASE", "https://api.deepseek.com/v1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


class FakeEmbeddings:
    def __init__(self, *args, **kwargs):
        pass

    def embed_documents(self, texts):
        return [[float(len(t))] for t in texts]

    def embed_query(self, text):
        return [float(len(text))]


import langchain.embeddings as langchain_embeddings

langchain_embeddings.HuggingFaceEmbeddings = FakeEmbeddings

from voyager.agents.skill import SkillManager  # noqa: E402


def _info(name):
    return {
        "task": f"do {name}",
        "program_name": name,
        "program_code": f"async function {name}(bot) {{ return true; }}",
        "fingerprint": [],
        "raw_fingerprint": [],
        "task_intent": {},
        "skill_intent": {},
        "fingerprint_confidence": "unknown",
        "fingerprint_filter_reason": "test",
        "intent_consistency": "test",
    }


def _mgr(tmp, capacity=3, lru_evict=True):
    mgr = SkillManager(
        model_name="deepseek-chat",
        ckpt_dir=str(tmp),
        resume=False,
        curation_enabled=False,
        call_detect="text_nodefs",
        governance="dynamic",
        governance_capacity=capacity,
        lru_evict=lru_evict,
    )
    mgr.generate_skill_description = lambda n, c: f"desc of {n}"
    return mgr


def test_lru_evict_default_still_evicts():
    with tempfile.TemporaryDirectory() as tmp:
        mgr = _mgr(tmp, capacity=3)
        assert mgr.lru_evict is True
        for i, name in enumerate(["s0", "s1", "s2"]):
            mgr.cur_iter = i
            mgr.add_new_skill(_info(name))
            mgr._dynamic_after_outcomes([])
        mgr.cur_iter = 3
        mgr.add_new_skill(_info("s3"))
        mgr._dynamic_after_outcomes([{"name": "s0", "ok": True}])
        snap = mgr.governor.snapshot()["skills"]
        assert snap["s1"].get("evicted_task") == 3, snap["s1"]
        assert "s1" not in mgr.skills


def test_lru_evict_false_records_but_does_not_remove():
    with tempfile.TemporaryDirectory() as tmp:
        mgr = _mgr(tmp, capacity=3, lru_evict=False)
        for i, name in enumerate(["s0", "s1", "s2"]):
            mgr.cur_iter = i
            mgr.add_new_skill(_info(name))
            mgr._dynamic_after_outcomes([])
        mgr.cur_iter = 3
        mgr.add_new_skill(_info("s3"))
        mgr._dynamic_after_outcomes([{"name": "s0", "ok": True}])
        snap = mgr.governor.snapshot()["skills"]
        assert snap["s0"]["exec_count"] == 1
        assert snap["s0"]["last_exec_task"] == 3
        for name in ("s0", "s1", "s2", "s3"):
            assert name in mgr.skills, name
            assert snap[name].get("evicted_task") is None, snap[name]
        assert mgr._apply_lru_evictions(3) == []
