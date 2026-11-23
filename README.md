# Code: Online Curation of the Skill Library in an Embodied Code Agent

This archive holds the code that ran the experiments of the paper:
- the adapted Voyager loop;
- the `obtain` executor;
- the curated leaf layer (harvest, tournament, online ledger, fallback);
- the isolated-evaluation harness;
- the launcher with the plans of the in-loop experiments;
- the scripts that set up and ran each experiment, and the earlier versions of the files that changed while the
  experiments went on (`experiments/`, added 2026-11-24; see `experiments/README.md`).

The preregistrations, result files and analysis scripts are in the supplementary material, not here.

The code is a fork of Voyager (Wang et al., 2023; MIT license, `LICENSE`). Identifiers were anonymized for review:
- user home paths read `/home/user`;
- personal names were removed from comments.

Comments written during the project are partly in Chinese.

## Where each part of the paper lives

| Paper | File |
|---|---|
| Voyager's loop, the Action Agent and the Critic Agent, the 57-skill library | `voyager/`, `voyager/agents/`, `libs/v24_final/.../ckpt_A_flash_off_t1_v26OLD` |
| Rule A (canonical subgoals) and the no-grant switches | `voyager/zs_noise.py`, `_cc0924/zs_canonical_subgoals.json`, `run_zeroshot.py` / `voyager/zeroshot.py` |
| The nine named actions; `obtain` (Algorithm 1) | `voyager/control_primitives_extra/*.js` (`obtain.js`), `voyager/named_action_docs.py`, `voyager/v30_flags.py` |
| The curated leaf layer, online ledger, quarantine and fallback (Algorithm 2) | `voyager/governance/ledger.py`, `voyager/governance/leaf_inject.py`, `voyager/control_primitives_extra/obtain.js` |
| Harvest, safety gates, classification | `_cc0924/gov_harvest.py`, `_cc0924/gov_js_rewrite.js`, `_cc0924/gov_leaf_alias.py`, `_cc0924/gov_pool.py` |
| The execution tournament (Algorithm 3) | `_cc0924/gov_tournament.py`, `_cc0924/gov_select.py` |
| The harvested candidates (output of `gov_harvest.py`, the harness's default pool) | `_gov/candidates.json` |
| The candidate pools of the isolated evaluation | `_gov/pool_screened.json` (Screened), `_gov/pool_random.json` (Random), `_gov/pool_system.json` (System), `_gov/pool_full.json` (all harvested candidates) |
| Writing leaves on demand (Algorithm 4) and the series that grow the leaf layer | `experiments/leaf_writing/` (`gen_leaves.py`, `e9_grow.py`, `e9_commit.py`), with the series runners in `experiments/exp11_*` and `experiments/exp13_*` to `experiments/exp15_*` |
| The frozen pools of Experiment 17 | `_gov/pool_e15_e12_L*.json` (written by DeepSeek), `_gov/pool_e15_e13_L*.json` (written by Qwen3.8-27B) |
| Isolated evaluation (hand / prim / gov leaves on fixed scenes, no language model) | `_cc0924/test_obtain_mc.py` (with `_cc0924/test_skills_mc.py`) |
| In-loop experiments (plans `zs_ruleA_*`, `zs_e4_*`, `zs_e8_*`, `zs_e9*`, `zs_e10n_*`, `zs_e12*`, `zs_e13*`, `zs_e15_*`; named by the internal experiment numbers, which `docs/names.md` of the supplementary material maps to the paper's Experiments 1 to 17) | `_cc0911/launch_zs.py`, `_cc0911/launch_0911.py`, `_cc0911/ports_0911.json` |
| The scripts that ran each experiment, by the paper's numbers | `experiments/` |
| Unit tests | `test_*.py` (pytest) |

## Requirements

- Linux (the experiments ran on Ubuntu under WSL 2).
- Python 3.10.
- Node.js 18.
- Java 17.
- A Minecraft 1.19 Fabric server, as in Voyager's installation guide (`installation/`).

The server jars and the mods are not redistributed. `mc_template/MODS_USED.txt` lists the files that were used. Put the server, its `libraries/` and the mods into `mc_template/`, next to the configuration files given there, and accept the Minecraft EULA yourself (`eula.txt`).

Install the versions the experiments ran with:
- `requirements_lock.txt` pins them;
- `requirements.txt` is Voyager's unpinned list, and today it installs a `langchain` that the code cannot import.

The steps below:
- CPU torch is enough: it only embeds skill descriptions.
- The lock is a complete `pip freeze`, so it is installed as it is, with `--no-deps`. Through pip's resolver it does not install: `pip check` reports four conflicts around `langchain-classic` and `pydantic-settings`, the same four as in the environment that ran the experiments.
- The launchers run Hugging Face offline, so the embedding model is cached once.

```bash
python3.10 -m venv venv && . venv/bin/activate
pip install -U pip
pip install torch==2.10.0 --index-url https://download.pytorch.org/whl/cpu
pip install --no-deps -r requirements_lock.txt
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"
(cd voyager/env/mineflayer && npm install)
export JAVA17=/path/to/java-17/bin/java
```

Both the harness and the launcher read `JAVA17`.

## Unit tests

```bash
python -m pytest -q -p no:cacheprovider test_*.py
```

The tests need `node` and the bridge's `node_modules`, but no Minecraft server and no language model. Nine tests in three files fail. They failed in the same way in the tree that ran the experiments, so they are outdated, not broken by the release:
- `test_v30_js.py`: seven tests of earlier `obtain.js` details (`test_obtain_bom_includes_pickaxe_handles_and_margin`, `test_obtain_death_resyncs_bom`, `test_obtain_safe_dig_skips_cliff`, `test_obtain_mc_harness_tp_retries_until_near_spawn`, `test_obtain_bom_counts_smelt_fuel`, `test_obtain_toss_keeps_cobble_separate_from_deepslate`, `test_obtain_twobyTwo_craft_calls_craftitem_without_table`);
- `test_v30_skill_layer.py::test_skill_js_has_no_task_names`: `obtain.js` now names the item `clock`;
- `test_v30_flags.py::test_prompts_and_run_meta_match_pre_task_baseline`: it compares against a copy of the pre-change tree (`~/v30_baseline_ref`) that is not part of this archive.

The other seven files pass in full.

## Isolated evaluation: one game, no language model

The harness starts its own Minecraft server from `mc_template/` on the given ports. It plays `obtain` for the scenes of `OBTAIN_SCENES` in the worlds of `OBTAIN_SEEDS`. Where leaves come from is set by `OBTAIN_LEAF_SOURCE`:
- `hand`: hand-written leaves;
- `prim`: Voyager's primitives;
- `gov`: the curated layer, with the pool of `GOV_CANDIDATES`.

The switches below are those of the paper's in-loop curation:
- quiescence 1,500 ms (cap 6,000);
- late fence;
- full ledger;
- quarantine per world;
- fallback on.

```bash
export OBTAIN_WORKERS=1 OBTAIN_WORKER_ID=0 OBTAIN_MAX_CALLS=8
export OBTAIN_LEAF_SOURCE=gov GOV_ONLINE=1 GOV_RETIRE_K=2 GOV_CANDIDATES=$PWD/_gov/pool_screened.json
export GOV_LEDGER_DIR=$PWD/_gov/ledger_demo GOV_LEDGER_LINEAGE=L1 OBTAIN_EXPERIMENT_ID=demo
export GOV_QUIESCE_MS=1500 GOV_QUIESCE_CAP_MS=6000 GOV_LATE_FENCE=1 GOV_LEDGER_MODE=full GOV_RETIRE_MODE=quarantine GOV_FALLBACK=on
export OBTAIN_MC_PORT=25759 OBTAIN_BR_PORT=3259 OBTAIN_RESULT_JSONL=$PWD/_cc0924/demo.jsonl
OBTAIN_SEEDS=20260902010 OBTAIN_SCENES=golden_sword python _cc0924/test_obtain_mc.py --phase full --worker 0
```

The result row (`demo.jsonl`) records each `obtain` call, the leaf deliveries by source and the ledger state. `GOV_RESUME=1` continues a lineage ledger across games. The game needs about 4 GB of memory. In the test of this archive it took about 7 minutes.

## The loop

`run_zeroshot.py` plays one game of the loop on one world. The launcher builds the command lines of a plan and runs them on lanes, which are pairs of ports from `_cc0911/ports_0911.json`.

The model is reached through an OpenAI-compatible endpoint:
- `--model-name` and `--api-base` set the model and the endpoint;
- `--api-key-env` names the variable that holds the key.

The paper used DeepSeek's API, except in Experiment 15 (internal E13), which used a local Ollama server. The plans `zs_e16_*` belong to an internal experiment that was stopped and is not in the paper.

```bash
E13_API_BASE=http://<ollama-host>:11434/v1 python _cc0911/launch_zs.py --plan zs_e13n_sys --machine local --ignore-machine --dry-run
```

`--dry-run` prints the 45 command lines of the Hand arm of Experiment 15 without running them or starting a server (checked on 2026-11-23: `SUMMARY jobs=45`).

`_cc0911/ports_0911.json` lists the machines and their lanes:
- `admin1` and `slac002` are the two machines that ran the experiments. The plans assign their jobs to these two.
- `local` stands for any other machine, with lanes on ports 25630–25639.

On another machine, pass `--machine local --ignore-machine`; this runs all jobs of the plan there.

## License

The code added by the authors is released under the MIT license, as is Voyager (`LICENSE`).
