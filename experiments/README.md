# The scripts that ran each experiment

This folder holds the scripts that set up and ran the experiments, and the earlier versions of the code files that
changed while the experiments went on. They were copied from the two experiment machines on 2026-11-24, as they ran.

They are a record, not a ready-to-run pipeline:
- paths are those of the machines (home folders read `/home/user`; the run trees were `~/wt_*` for the loop and
  `~/V30_*` for the isolated harness, each a copy of this archive's code), and some scripts call Windows PowerShell
  from WSL;
- several scripts check the md5 of the executor, the pool, or the launcher before they start, against the files of
  their run tree. In this archive those md5s differ (see the next section), so such a check stops the script unless it
  is overridden or removed;
- setup scripts call patch scripts that are not included, because the plans they produced are now in
  `_cc0911/launch_zs.py`;
- `exp04_harsh_worlds/e11_night.sh` shuts down the whole WSL instance (`wsl.exe --terminate`) at a set time;
- the scripts name experiments by their internal identifiers; `docs/names.md` of the supplementary material maps them
  to the paper's Experiments 1 to 17;
- comments are partly in Chinese.

Only user names, a branch name, and local folder names were replaced. The md5 prefixes below are those of the files as
they ran, before that replacement, the same prefixes that `docs/experiments.md` of the supplementary material records.

## md5 prefixes in this archive

The release of 2026-10-24 replaced user home paths and personal names in every file, so some files of this archive
have a different md5 from the file that ran. Applying the same replacements to the file that ran gives exactly the
file in this archive (checked 2026-11-24):

| File | As run | In this archive |
|---|---|---|
| `_cc0924/test_obtain_mc.py` (harness) | `41485cf8` | `4b24cbe4` |
| `voyager/zeroshot.py` | `49b596b4` | `a52c2429` |
| `_gov/pool_screened.json` | `06cdfe20` | `3d9322f4` |
| `voyager/control_primitives_extra/obtain.js` (executor) | `b3f48363` | `b3f48363` |
| `voyager/governance/leaf_inject.py` | `5583ebb4` | `5583ebb4` |
| `voyager/llm_meta.py` | `3722234c` | `3722234c` |
| `run_zeroshot.py` | `59f6232e` | `59f6232e` |

The six pools `_gov/pool_e15_*.json` contain no home path and are byte for byte those that ran.

## Which code ran which experiment

The code at the top level of this archive is the code of the last experiments (internal E13 to E16). Earlier
experiments ran on earlier states of the same tree. On the files of this archive, file by file, only these differed:

| Paper (internal) | Setting | Files that differed from this archive | Versions in `code_versions/` |
|---|---|---|---|
| 1 (A) | loop | executor, loop runner, assisted-protocol helpers, LLM metadata (the curated layer did not exist yet) | `obtain.3c5ae2a7.js`, `zeroshot.fde03580.py`, `zs_noise.f4cb49bb.py`, `llm_meta.2e1531bd.py` |
| 7 (B) | loop | executor, leaf injection, loop runner, assisted-protocol helpers, LLM metadata | `obtain.6f13da60.js`, `leaf_inject.787d1ecc.py`, `zeroshot.733463f4.py`, `zs_noise.f4cb49bb.py`, `llm_meta.2e1531bd.py` |
| 8, 9 (E4, E8) | loop | executor, leaf injection, loop runner, LLM metadata | `obtain.6f13da60.js`, `leaf_inject.787d1ecc.py`, `zeroshot.6039b882.py`, `llm_meta.2e1531bd.py` |
| 10, 13, 14, 17 (E10, E9, E12, E15) | loop | loop runner, LLM metadata. The later versions only add switches for a local Ollama server, which are off with DeepSeek's API | `zeroshot.10d393d3.py`, `llm_meta.2e1531bd.py` |
| 15 (E13) | loop | none | — |
| 2 (C–F) | isolated | harness `e2d500c8` with executor `6f13da60`; the historical Hand arm: harness `8e64820b` with executor `3c5ae2a7` | `test_obtain_mc.e2d500c8.py`, `test_obtain_mc.8e64820b.py`, `obtain.6f13da60.js`, `obtain.3c5ae2a7.js` |
| 3, 4, 12 (E6, E11, S1) | isolated | harness `e2d500c8`, executor `6f13da60` | `test_obtain_mc.e2d500c8.py`, `obtain.6f13da60.js` |
| 5 (G) | isolated | harness `aae86720` (`e2d500c8` plus the ledger lineage switch `GOV_LEDGER_LINEAGE`), executor `6f13da60` | `test_obtain_mc.aae86720.py`, `obtain.6f13da60.js` |
| 11 (E3) | isolated | harness `c7a70449` (`aae86720` plus the append-only pool switch `GOV_POOL_APPEND_ONLY`), executor `6f13da60` | `test_obtain_mc.c7a70449.py`, `obtain.6f13da60.js` |
| 6, 16 (E7, E14) | isolated | none: the harness `41485cf8` and the executor `b3f48363` of this archive | — |

The plans of the loop experiments are in `_cc0911/launch_zs.py`. Each plan was dry-run with this launcher and with the
launcher that ran the experiment, and the job lines were identical (2026-11-24): `zs_ruleA_pilot_0926` (48 jobs),
`zs_ruleA_sysgov_0927` (24), `zs_e4_unseen_0928` (30), `zs_e8_nogrant_0929` (45), `zs_e9n_*` (45 + 3 × 15),
`zs_e10n_*` (2 + 3 + 45), `zs_e12n_*` (45 + 3 × 15), `zs_e13n_*` (45 + 3 × 15), and `zs_e15_*` (2 + 90). The plans
`zs_e10n_*` and `zs_e15_*` were added to the launcher on that date, copied verbatim from the launcher that ran E15.
The candidate pools of `zs_e15_*` are `_gov/pool_e15_*.json`.

## The scripts, by experiment

| Paper (internal) | Folder | What the scripts do |
|---|---|---|
| 1, 7, 8, 9 (A, B, E4, E8) | — | Only the launcher's plans. |
| 2, 5 (C–F, G) | `exp02_05_isolated_evaluation` | The tournament runs (`tourn_queue.sh`, `tourn_official.sh`), the Mixed pool and the hand-written-only tournament pool (`build_pools.py`; the screened and random pools come from `_cc0924/gov_select.py`), the queue of the isolated batch (`eval_queue.sh`), and the setup of the Unscreened series (`setup_online.sh`, `setup_online_split.sh`). |
| 3 (E6) | `exp03_confirmation_batch` | Setup, queue, merge, and health checks of the batch (`r12_*`), and the rerun rounds (`r12r_*`, `r12r2_queue.sh`). The preregistration's dated amendments say which rerun queue ran when. |
| 4 (E11) | `exp04_harsh_worlds` | Screening and selecting the harsh worlds (`e11_screen.py`, `e11_select.py`), setup, queue, the nightly stop (`e11_night.sh`), and reruns (`e11_rerun*.sh`). |
| 6 (E7) | `exp06_ledger_experiment` | Setup, one series (`run_e7_lineage.sh`), the queues (`e7_queue.sh`, then `e7_queue_thr.sh`, then `e7_queue_v4.sh`, which took over the running batch; the preregistration's amendments date each), restart and rebuild helpers, and a mechanism check of the ledger trail that prints no outcome (`check_e7_mech.sh`). |
| 10 (E10) | `exp10_screened_alone` | The round runner of the plans `zs_e10n_*`. |
| 11 (E3) | `exp11_from_zero` | One series (`run_e3_lineage.sh`): before each game, `_cc0924/e3_grow.py` writes the missing leaves with `_cc0924/gen_leaves.py` (Algorithm 4), then the harness plays the game. The runner works inside a copy of the archive with the E3 harness. |
| 12 (S1) | `exp12_one_life` | The pools of one life at its checkpoints (`stage1_build.py`, `s1_select.py`, `reid_gov.py`, which imports `gen_leaves.py` for the gates), their tournament (`tourn_s1.sh`), setup, queue, and health check. The composition check made before the run ("gate 0" in the preregistration) was not found on either machine; its result is in the preregistration. |
| 13 (E9) | `exp13_lifecycle_heldout_worlds` | One series in the loop (`e9_tools/run_e9_lineage.sh`): before each game `e9_grow.py` writes the missing leaves with `tools_0928/gen_leaves.py` (Algorithm 4), and after it `e9_commit.py` commits the ledger. |
| 14 (E12) | `exp14_lifecycle_new_worlds` | As 13, on the new worlds (`e12_tools/tools_0930/run_e12_lineage.sh`, a copy of `run_e9_lineage.sh` with the names changed from e9 to e12). |
| 15 (E13) | `exp15_smaller_llm` | As 14 with Qwen3.8-27B through Ollama (`e13_tools/tools_0930/run_e13_lineage.sh`), and the check that this LLM answered every call (`e13_llm_check.sh`). |
| 16 (E14) | `exp16_leaves_in_isolation` | Setup and the series runner of the isolated evaluation of the written leaves (`run_e14_lineage.sh`, made from Experiment 6's `run_e7_lineage.sh` for its quarantine condition, with the candidate pool of one series of Experiment 14 or 15). |
| 17 (E15) | `exp17_leaves_called_by_deepseek` | Setup and the runner of the plans `zs_e15_*`. |

The folders of Experiments 11 and 13 to 15 keep the layout in which the scripts ran, so that each runner finds its
helpers next to it, and each holds the version of the leaf writer that its experiment ran:
- `gen_leaves.py` is `49df821d` in Experiments 11 to 14. Experiment 15's `fd9fe2f0` differs from it only by the
  switches `GEN_LEAVES_MODEL`, `GEN_LEAVES_API_BASE`, and `GEN_LEAVES_API_KEY_ENV`; unset, it calls DeepSeek as before.
- `e9_grow.py` is `669f8de7` in Experiments 13 and 14. Experiment 15's `11644509` differs from it only by two fields of
  its record, the LLM and its address.
- `run_e9_lineage.sh` is `613375f4` in Experiment 13. The copy in Experiment 14's folder (`83bd7f8f`) differs from it
  only by an optional `E9_LAUNCH_ARGS`; unset, it is the same.

`common/` holds the clock probe and guard used during the batches, and `r12r3_void_check.py`, which finds gaps in the
clock record.

Not included: progress monitors, status printers, Windows orchestration scripts (`.ps1`), the patch scripts that
produced the launcher's plans, and the analysis scripts, which are in the supplementary material.
