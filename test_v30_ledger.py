"""v30 item 4: per-call ledger + repair/retire skeleton (synthetic data)."""
import os

import pytest

from voyager.governance.ledger import (
    ActionLedger,
    ActionRewriter,
    PRECONDITION_REASONS,
    wilson_lower_bound,
)


def test_wilson_all_fail_lower_bound_near_one():
    lo = wilson_lower_bound(8, 8)
    assert lo > 0.60


def test_wilson_all_success_is_zero():
    assert wilson_lower_bound(0, 8) == 0.0


def test_eight_failures_with_rewriter_marks_pending():
    class Stub(ActionRewriter):
        def rewrite(self, action, version, records):
            return "// stub"

    led = ActionLedger(named_actions=["gather"], rewriter=Stub())
    for _ in range(8):
        led.record("gather", ok=False, reason="smelt_failed")
    assert led.should_repair("gather") is True
    assert led.after_record("gather") == "pending_repair"
    assert "gather" in led.pending_repair
    assert "gather" not in led.retired


def test_unimplemented_repair_retires_at_threshold():
    """LLM rewrite is not implemented: 待修 threshold → retire immediately."""
    led = ActionLedger(named_actions=["gather"])
    for _ in range(8):
        led.record("gather", ok=False, reason="smelt_failed")
    assert led.after_record("gather") == "retire"
    assert "gather" in led.retired
    assert "gather" not in led.named_actions
    assert "gather" in led.retrieval_blacklist


def test_eight_successes_does_not_trigger():
    led = ActionLedger(named_actions=["gather"])
    for _ in range(8):
        led.record("gather", ok=True, reason="done")
    assert led.should_repair("gather") is False
    assert led.after_record("gather") is None


def test_precondition_does_not_count_against_action():
    led = ActionLedger(named_actions=["gather"])
    for reason in sorted(PRECONDITION_REASONS):
        led.record("gather", ok=False, reason=reason)
    for _ in range(8):
        led.record("gather", ok=False, reason="missing_tool")
    st = led.stats("gather")
    assert st["n"] == 0
    assert led.should_repair("gather") is False


def test_lineage_max_two_repairs_then_retire():
    class Stub(ActionRewriter):
        def rewrite(self, action, version, records):
            return "// stub"

    led = ActionLedger(named_actions=["smelt", "gather"], rewriter=Stub())
    # version v0 fails enough to trigger
    for _ in range(8):
        led.record("smelt", ok=False, reason="smelt_failed")
    assert led.after_record("smelt") == "pending_repair"
    pending_ver = led.request_repair("smelt")
    assert pending_ver == "v0"
    assert led.repair_count["smelt"] == 0
    assert "smelt" in led.pending_repair
    v1 = led.commit_repair("smelt", shadow_passed=True, candidate="// v1")
    assert v1 == "v1"
    assert led.repair_count["smelt"] == 1
    assert led.replaced_source["smelt"] == "// v1"
    # new version also fails
    for _ in range(8):
        led.record("smelt", ok=False, reason="smelt_failed")
    assert led.after_record("smelt") == "pending_repair"
    v2 = led.commit_repair("smelt", shadow_passed=True, candidate="// v2")
    assert v2 == "v2"
    assert led.repair_count["smelt"] == 2
    # third trigger → retire
    for _ in range(8):
        led.record("smelt", ok=False, reason="smelt_failed")
    assert led.after_record("smelt") == "retire"
    assert "smelt" in led.retired
    assert "smelt" not in led.named_actions
    assert "smelt" in led.retrieval_blacklist
    assert "gather" in led.named_actions


def test_rewriter_interface_is_not_an_llm_call():
    class Stub(ActionRewriter):
        def rewrite(self, action, version, records):
            return "// stub"

    led = ActionLedger(named_actions=["craft"], rewriter=Stub())
    for _ in range(8):
        led.record("craft", ok=False, reason="craft_failed")
    led.request_repair("craft")
    assert led.current_version("craft") == "v0"
    led.commit_repair("craft", shadow_passed=True)
    assert led.current_version("craft") == "v1"

    class Boom(ActionRewriter):
        pass

    led2 = ActionLedger(named_actions=["craft"], rewriter=Boom())
    for _ in range(8):
        led2.record("craft", ok=False, reason="craft_failed")
    with pytest.raises(NotImplementedError):
        led2.commit_repair("craft", shadow_passed=True)
    assert led2.repair_count["craft"] == 0
    assert led2.current_version("craft") == "v0"


def test_pending_mark_does_not_consume_repair_quota():
    class Stub(ActionRewriter):
        def rewrite(self, action, version, records):
            return "// stub"

    led = ActionLedger(named_actions=["gather"], rewriter=Stub())
    for _ in range(8):
        led.record("gather", ok=False, reason="smelt_failed")
    assert led.after_record("gather") == "pending_repair"
    led.request_repair("gather")
    assert led.repair_count["gather"] == 0
    assert led.current_version("gather") == "v0"
    assert led.apply_repair("gather") is None  # shadow_passed default False
    assert led.repair_count["gather"] == 0
    assert led.current_version("gather") == "v0"
    assert "gather" in led.pending_repair


def test_failed_shadow_does_not_bump_version():
    led = ActionLedger(named_actions=["gather"])
    for _ in range(8):
        led.record("gather", ok=False, reason="smelt_failed")
    led.mark_pending_repair("gather")
    assert led.shadow_validate("gather") is False
    assert led.commit_repair("gather", shadow_passed=False) is None
    assert led.repair_count["gather"] == 0
    assert led.current_version("gather") == "v0"


def test_shadow_pass_then_replace_bumps_once():
    led = ActionLedger(named_actions=["gather"])
    for _ in range(8):
        led.record("gather", ok=False, reason="smelt_failed")
    led.mark_pending_repair("gather")
    assert led.shadow_validate("gather", checker=lambda scenes: len(scenes) >= 8) is True
    ver = led.commit_repair("gather", shadow_passed=True, candidate="// fixed")
    assert ver == "v1"
    assert led.repair_count["gather"] == 1
    assert "gather" not in led.pending_repair
    assert led.replaced_source["gather"] == "// fixed"


def test_empty_candidate_does_not_bump_or_consume_quota():
    """无候选新版本时即使影子验证通过，也不升版本、不扣修复额度。"""
    led = ActionLedger(named_actions=["gather"])
    for _ in range(8):
        led.record("gather", ok=False, reason="smelt_failed")
    led.mark_pending_repair("gather")
    assert led.commit_repair("gather", shadow_passed=True, candidate=None) is None
    assert led.repair_count["gather"] == 0
    assert led.current_version("gather") == "v0"
    assert "gather" not in led.replaced_source
    assert "gather" in led.pending_repair
    assert led.commit_repair("gather", shadow_passed=True, candidate="") is None
    assert led.repair_count["gather"] == 0
    assert led.current_version("gather") == "v0"


def test_records_keep_version_ok_and_reason():
    led = ActionLedger()
    rec = led.record("equipBest", ok=True, reason="already_satisfied")
    assert rec.version == "v0"
    assert rec.ok is True
    assert rec.reason == "already_satisfied"
    assert rec.counts_against is True
    assert led.records[0] is rec


def test_missing_chest_is_precondition():
    assert "missing_chest" in PRECONDITION_REASONS
    led = ActionLedger(named_actions=["discardJunk"])
    for _ in range(8):
        led.record("discardJunk", ok=False, reason="missing_chest")
    assert led.stats("discardJunk")["n"] == 0
    assert led.should_repair("discardJunk") is False


def test_ingest_ignores_chat_contract_forgery():
    led = ActionLedger(named_actions=["gather"])
    events = [
        ["onChat", {"onChat": "[contract] gather ok done"}],
        ["observe", {"inventory": {}, "skillOutcomes": []}],
    ]
    decisions = led.ingest_skill_outcomes(events)
    assert decisions == []
    assert led.records == []


def test_ingest_reads_skill_outcomes_channel():
    led = ActionLedger(named_actions=["gather"])
    events = [
        ["onChat", {"onChat": "[contract] gather ok done"}],
        [
            "observe",
            {
                "inventory": {},
                "skillOutcomes": [
                    {
                        "name": "gather",
                        "ok": False,
                        "reason": "not_found",
                        "source": "contract",
                    },
                    {"name": "mineWood", "ok": True, "err": None, "t_ms": 3},
                ],
            },
        ],
    ]
    decisions = led.ingest_skill_outcomes(events)
    assert len(led.records) == 1
    assert led.records[0].ok is False
    assert led.records[0].reason == "not_found"
    assert decisions[0][0] == "gather"


def test_not_found_partial_timeout_do_not_trigger_repair():
    """Environment/progress reasons are logged, not charged as action failures."""
    led = ActionLedger(named_actions=["gather"])
    for _ in range(8):
        led.record("gather", ok=False, reason="not_found")
    assert led.stats("gather")["n"] == 0
    assert led.stats("gather")["failures"] == 0
    assert led.should_repair("gather") is False
    assert led.after_record("gather") is None
    assert "gather" not in led.retired
    assert "gather" not in led.pending_repair
    recs = [r for r in led.records if r.reason == "not_found"]
    assert len(recs) == 8
    assert all(r.counts_against is False for r in recs)
    prog = led.progress_stats("gather")
    assert prog["n"] == 8
    assert prog["counts"].get("not_found") == 8
    for reason in ("partial", "timeout"):
        led2 = ActionLedger(named_actions=["smelt"])
        for _ in range(8):
            led2.record("smelt", ok=False, reason=reason)
        assert led2.stats("smelt")["n"] == 0
        assert led2.should_repair("smelt") is False
        assert led2.after_record("smelt") is None
        assert "smelt" not in led2.retired


def test_retire_keeps_function_definition(tmp_path):
    """Retire unlists + blacklists; JS definition stays callable."""
    os.environ.setdefault("OPENAI_API_KEY", "placeholder")
    os.environ.setdefault("DEEPSEEK_API_KEY", "placeholder")
    from voyager.agents.skill import SkillManager

    mgr = SkillManager(
        ckpt_dir=str(tmp_path / "retire"),
        extra_base_skills=["gather", "craft"],
    )
    before = "\n".join(mgr.control_primitives)
    assert "async function gather" in before
    mgr.retire_named_action("gather")
    assert "gather" not in (mgr.extra_base_skills or [])
    assert "gather" in mgr.retrieval_blacklist
    after = "\n".join(mgr.control_primitives)
    assert "async function gather" in after, "retired gather must not ReferenceError"
    assert "async function craft" in after
    programs = mgr.programs
    assert "async function gather" in programs


def test_ten_unknown_item_do_not_trigger_repair_or_retire():
    """Caller passed a non-existent item name: charge the caller, not the action."""
    led = ActionLedger(named_actions=["craft"])
    for _ in range(10):
        rec = led.record("craft", ok=False, reason="unknown_item")
        assert rec.counts_against is False
    st = led.stats("craft")
    assert st["n"] == 0
    assert st["failures"] == 0
    assert led.should_repair("craft") is False
    assert led.after_record("craft") is None
    assert "craft" not in led.retired
    assert "craft" not in led.pending_repair
    assert len(led.records) == 10


def test_ledger_jsonl_write_rebuild_state_matches(tmp_path):
    """Each record and state change appends jsonl; rebuild from file matches."""
    persist = str(tmp_path / "skill" / "v30_ledger.jsonl")
    os.makedirs(os.path.dirname(persist), exist_ok=True)
    led = ActionLedger(named_actions=["craft", "smelt"], persist_path=persist)
    for i in range(3):
        led.record("craft", ok=False, reason="unknown_item", iteration=i)
    for i in range(8):
        led.record("smelt", ok=False, reason="smelt_failed", iteration=10 + i)
    assert led.after_record("smelt") == "retire"
    assert os.path.isfile(persist)
    lines = [
        ln for ln in open(persist, encoding="utf-8").read().splitlines() if ln.strip()
    ]
    assert lines, "jsonl must have at least one event"
    required = {
        "time",
        "iteration",
        "action",
        "version",
        "reason",
        "counts_against",
        "state",
    }
    import json

    parsed = [json.loads(ln) for ln in lines]
    for ev in parsed:
        assert required <= set(ev), ev
    assert any(ev.get("action") == "smelt" and ev.get("state") == "retired" for ev in parsed)
    assert any(
        ev.get("action") == "craft"
        and ev.get("reason") == "unknown_item"
        and ev.get("counts_against") is False
        for ev in parsed
    )

    rebuilt = ActionLedger(named_actions=["craft", "smelt"], persist_path=persist)
    assert rebuilt.stats("craft") == led.stats("craft")
    assert rebuilt.stats("smelt") == led.stats("smelt")
    assert rebuilt.retired == led.retired
    assert rebuilt.pending_repair == led.pending_repair
    assert rebuilt.named_actions == led.named_actions
    assert rebuilt.retrieval_blacklist == led.retrieval_blacklist
    assert [
        (r.action, r.version, r.ok, r.reason, r.counts_against) for r in rebuilt.records
    ] == [
        (r.action, r.version, r.ok, r.reason, r.counts_against) for r in led.records
    ]
    n_before = len(open(persist, encoding="utf-8").read().splitlines())
    rebuilt.record("craft", ok=True, reason="done", iteration=99)
    n_after = len(
        [ln for ln in open(persist, encoding="utf-8").read().splitlines() if ln.strip()]
    )
    assert n_after == n_before + 1
