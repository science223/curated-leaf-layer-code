"""Unit tests for voyager.governance.lru.LRUGovernor (WP-G)."""
from voyager.governance.lru import LRUGovernor


def test_add_appears_in_shown_list():
    g = LRUGovernor(capacity=8)
    g.add("mineWood", 1)
    g.add("craftTable", 2)
    assert g.shown_list(2) == ["craftTable", "mineWood"]


def test_ok_outcome_refreshes_last_exec():
    g = LRUGovernor(capacity=8)
    g.add("mineWood", 1)
    assert g.score("mineWood", 5) == 4
    g.record_outcomes(5, [{"name": "mineWood", "ok": True}])
    assert g.score("mineWood", 5) == 0
    assert g.snapshot()["skills"]["mineWood"]["last_exec_task"] == 5
    assert g.snapshot()["skills"]["mineWood"]["exec_count"] == 1
    assert g.shown_list(6)[0] == "mineWood"


def test_fail_outcome_does_not_refresh_last_exec():
    g = LRUGovernor(capacity=8)
    g.add("mineWood", 1)
    g.record_outcomes(4, [{"name": "mineWood", "ok": False, "err": "boom"}])
    assert g.score("mineWood", 4) == 3
    assert g.snapshot()["skills"]["mineWood"]["last_exec_task"] is None
    assert g.snapshot()["skills"]["mineWood"]["exec_fail_count"] == 1
    assert g.snapshot()["skills"]["mineWood"]["exec_count"] == 0


def test_evict_longest_unused_first():
    g = LRUGovernor(capacity=2)
    g.add("old", 1)
    g.add("mid", 2)
    g.add("new", 3)
    evicted = g.evict_if_over(3)
    assert [row["name"] for row in evicted] == ["old"]
    assert evicted[0]["reason"] == "lru_capacity"
    assert evicted[0]["score"] == 2
    assert "old" not in g.shown_list(3)
    assert g.shown_list(3) == ["new", "mid"]


def test_tie_break_exec_count_then_created_task():
    g = LRUGovernor(capacity=1)
    g.add("rarely", 1)
    g.add("often", 1)
    g.record_outcomes(2, [{"name": "often", "ok": True}])
    g.record_outcomes(
        3,
        [
            {"name": "rarely", "ok": True},
            {"name": "often", "ok": True},
        ],
    )
    # last_exec both 3, score tied; often has exec_count 2, rarely 1
    evicted = g.evict_if_over(4)
    assert [row["name"] for row in evicted] == ["rarely"]
    assert evicted[0]["exec_count"] == 1

    g2 = LRUGovernor(capacity=1)
    g2.add("early", 1)
    g2.add("late", 2)
    g2.record_outcomes(
        3,
        [
            {"name": "early", "ok": True},
            {"name": "late", "ok": True},
        ],
    )
    # last_exec both 3, exec_count both 1 → earlier created_task first
    evicted2 = g2.evict_if_over(5)
    assert [row["name"] for row in evicted2] == ["early"]


def test_capacity_zero_or_none_never_evicts():
    for cap in (0, None):
        g = LRUGovernor(capacity=cap)
        g.add("a", 1)
        g.add("b", 2)
        g.add("c", 3)
        assert g.evict_if_over(3) == []
        assert set(g.shown_list(3)) == {"a", "b", "c"}


def test_capacity_none_never_evicts_many():
    g = LRUGovernor(capacity=None)
    for i in range(20):
        g.add(f"s{i}", i)
    assert g.evict_if_over(20) == []
    assert len(g.shown_list(20)) == 20


def test_working_set_and_recency_histogram():
    g = LRUGovernor(capacity=None)
    g.add("a", 1)
    g.add("b", 2)
    g.add("c", 3)
    g.record_outcomes(3, [{"name": "a", "ok": True}])
    g.record_outcomes(8, [{"name": "b", "ok": True}])
    # task 10: a last=3 ago=7; b last=8 ago=2; c never
    assert g.working_set(10, 5) == 1
    assert g.working_set(10, 15) == 2
    assert g.working_set(10, 30) == 2
    assert g.working_set(10, 0) == 0
    hist = g.recency_histogram(10)
    assert hist["never"] == 1
    assert hist[7] == 1
    assert hist[2] == 1
    assert 0 not in hist
    g.record_outcomes(10, [{"name": "c", "ok": True}])
    hist2 = g.recency_histogram(10)
    assert hist2["never"] == 0
    assert hist2[0] == 1
    assert g.working_set(10, 5) == 2
    g2 = LRUGovernor(capacity=0)
    g2.add("x", 1)
    g2.add("y", 2)
    assert g2.evict_if_over(9) == []
    assert g2.working_set(9, 15) == 0
    assert g2.recency_histogram(9)["never"] == 2


def test_resume_roundtrip():
    g = LRUGovernor(capacity=2)
    g.add("a", 1)
    g.add("b", 2)
    g.record_outcomes(3, [{"name": "a", "ok": True}])
    g.evict_if_over(4)
    snap = g.snapshot()
    g2 = LRUGovernor(capacity=2, state=snap)
    assert g2.snapshot() == snap
    assert g2.shown_list(4) == g.shown_list(4)
    assert g2.score("a", 5) == g.score("a", 5)


def test_remove_drops_from_shown_list():
    g = LRUGovernor(capacity=8)
    g.add("keep", 1)
    g.add("dup", 1)
    g.remove("dup")
    assert g.shown_list(2) == ["keep"]
    assert "dup" not in g.snapshot()["skills"]


def test_same_task_multiple_outcomes_refresh_once():
    g = LRUGovernor(capacity=8)
    g.add("mineWood", 1)
    g.record_outcomes(
        4,
        [
            {"name": "mineWood", "ok": True},
            {"name": "mineWood", "ok": True},
            {"name": "mineWood", "ok": True},
        ],
    )
    snap = g.snapshot()["skills"]["mineWood"]
    assert snap["last_exec_task"] == 4
    assert snap["exec_count"] == 1
    g.record_outcomes(4, [{"name": "mineWood", "ok": True}])
    assert g.snapshot()["skills"]["mineWood"]["exec_count"] == 1
