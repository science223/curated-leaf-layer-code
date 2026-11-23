#!/usr/bin/env python3
"""E9 lineage bookkeeping for run_e9_lineage.sh (预注册_循环内完整生命周期_2026-09-29), v3 after Codex review 2.

  e9_commit.py state <ledger> <pool>
      prints "<ledger_calls> <ledger_sha> <pool_sha>"; ledger_sha is leaf_inject.ledger_sha of the entries.
  e9_commit.py commit <results.jsonl> <tag> <ledger> <attempts_dir> <expected_base_sha>
      Game-level rollback: an attempt books into its own working copy (<ledger>.work.<id>) and never writes the committed
      ledger. After a game ends DONE this reads the game's last result row and checks that the game started from the
      ledger the lineage had committed before it (row loaded sha == <expected_base_sha>, the previous commit), and that
      the game's working copy -- still in place, or already archived in <attempts_dir> -- holds exactly the entries
      whose hash the row reports, was based on that ledger and carries the lineage's id. Then it commits the copy
      atomically (unless the committed ledger already is that copy: a runner that stopped between the commit and its
      log line) and moves every working copy of the game to <attempts_dir>. Prints "ok"; exits 1 with the reason.
"""
import glob
import hashlib
import json
import os
import shutil
import sys


def ledger_sha(entries):  # identical to voyager/governance/leaf_inject.ledger_sha (E9 patch)
    return hashlib.sha256(json.dumps(entries or {}, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def ledger_calls(entries):
    return sum(int((e or {}).get("calls") or 0) for e in (entries or {}).values())


def load(path):
    data = json.load(open(path, encoding="utf-8"))
    return data, ((data.get("entries") if isinstance(data.get("entries"), dict) else data) or {})


def file_sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]


def state(ledger, pool):
    _, e = load(ledger)
    return f"{ledger_calls(e)} {ledger_sha(e)} {file_sha(pool)}"


def fail(msg):
    print("ERR " + msg)
    sys.exit(1)


def commit(results, tag, ledger, attempts, expected_base):
    row = None
    for line in open(results, encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            if r.get("attempt_id") == tag:
                row = r
    if row is None:
        fail(f"no result row for {tag}")
    cdata, centries = load(ledger)
    committed = ledger_sha(centries)
    loaded, final, work = row.get("gov_ledger_loaded_sha"), row.get("gov_ledger_final_sha"), row.get("gov_ledger_work_file")
    if loaded is None or final is None:
        fail(f"{tag}: the row carries no ledger hashes (lineage ledger not active?)")
    if loaded != expected_base:
        fail(f"{tag}: the game did not start from the previous commit (loaded {loaded}, previous commit {expected_base})")
    if committed not in (expected_base, final):
        fail(f"{tag}: the committed ledger {committed} is neither the previous commit nor this game's result")
    if work:
        path = work if os.path.exists(work) else os.path.join(attempts, os.path.basename(work))
        if not os.path.exists(path):
            fail(f"{tag}: working copy {os.path.basename(work)} missing")
        w, wentries = load(path)
        if ledger_sha(wentries) != final or w.get("base_sha") != loaded:
            fail(f"{tag}: working copy does not match the row (final {final}, base {w.get('base_sha')})")
        if w.get("lineage_id") != cdata.get("lineage_id"):
            fail(f"{tag}: working copy of lineage {w.get('lineage_id')!r}, ledger of {cdata.get('lineage_id')!r}")
        if committed == expected_base and final != expected_base:
            tmp = ledger + ".commit.tmp"
            with open(tmp, "w", encoding="utf-8") as fp:
                json.dump(w, fp, ensure_ascii=False)
            os.replace(tmp, ledger)
    elif final != loaded:
        fail(f"{tag}: the row reports bookings ({loaded} -> {final}) but no working copy")
    os.makedirs(attempts, exist_ok=True)
    for f in glob.glob(ledger + ".work.*"):
        shutil.move(f, os.path.join(attempts, os.path.basename(f)))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "state" and len(sys.argv) == 4:
        print(state(sys.argv[2], sys.argv[3]))
    elif cmd == "commit" and len(sys.argv) == 7:
        commit(*sys.argv[2:7])
        print("ok")
    else:
        print(__doc__)
        sys.exit(2)
