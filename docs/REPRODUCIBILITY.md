# Code, Frozen Protocols, and Experiment Artifacts

## Release scope

The repository contains the canonical source code, run scripts, tests, frozen configurations, research plan, split manifests, reports, and selected summary tables. The release inventory is recorded in `reproducibility/code_release_manifest.json` at the repository root.

Virtual environments, download caches, model weights, LoRA adapters, optimizer states, dataset contents under `data/`, complete `runs/` caches, per-answer JSONL files, large per-answer scoring CSVs, and ZIP archives remain on the experiment server. Paths to those artifacts in the reports refer to server-side records. Recomputing the complete historical experiments requires those artifacts.

## Main entry points

| Stage | Modules |
|---|---|
| Data preparation and independent answer checks | `prepare.py`, `day2_protocol.py`, `dapo_prepare.py`, `day3_protocol.py` |
| Generator updates with fixed V0 | `formal_select.py`, `formal_train.py`, `formal_pipeline.py` |
| Multiprocessing, dual-GPU execution, and vLLM | `formal_parallel.py`, `formal_multigpu.py`, `g3_pipeline.py` |
| Oracle and Random controls | `oracle_control.py`, `random_control.py`, `cache_followup.py` |
| Prefix interventions and natural edits | `efp_intervention.py`, `prefix_paraphrase.py`, `natural_invariance.py`, `closing_diagnostics.py` |
| Full prefix panel | `prefix_full_panel.py`, `prefix_full_panel_report.py` |
| Seven-day held-out T evaluation | `week_final_run.py`, `week_final_report.py`, `week_final_verify.py` |
| SFT/DPO verifier repair | `robust_verifier_clean.py`, `robust_verifier_train.py`, `robust_verifier_eval.py` |
| Presentation material export | `scripts/build_teacher_brief.py` |

Modules are located in `src/pilot/`, except where an explicit script path is given. The main repair study uses the `robust_verifier_clean` entry point. The initial repair trial and the main trial following the question-quality revision are stored separately, as documented in the repair report.

## Running on a new machine

1. Install the matching Torch/CUDA stack and project dependencies. The original machine reused system-installed Torch; `requirements-lock.txt` is an environment snapshot that includes inherited system packages.
2. Prepare the local models specified in the configurations. Model identities and fingerprints are recorded in the frozen protocols.
3. Rebuild question pools from the original sources, or restore `data/` and `runs/` from the experiment server. Copies of the split manifests are in `reproducibility/manifests/`.
4. Use new configurations and run directories for new experiments. Formal modules check model, source, and input hashes; after changing paths or implementations, keep new outputs separate from historical caches.
5. Check labels, sequence lengths, loss masks, and save/reload consistency before GPU runs. Use tmux for long-running jobs.

Data sources: [GSM8K](https://github.com/openai/grade-school-math), [MATH](https://huggingface.co/datasets/EleutherAI/hendrycks_math), and the [processed DAPO question pool](https://huggingface.co/datasets/open-r1/DAPO-Math-17k-Processed).

## Checks that do not require model weights

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_day2_protocol.py tests/test_dapo.py -q
```

Other tests include integration audits that depend on historical caches, model tokenizers, and question pools. The command above checks answer parsing and protocol logic.

## Exporting presentation materials

`scripts/build_teacher_brief.py` reads summary tables without calling any models. Additional dependencies are `python-pptx`, `reportlab`, `openpyxl`, `fonttools`, `PyMuPDF`, and `pypdf`. On the original machine, reporting dependencies were installed separately under `.cache/teacher_reporting_vendor`, and the Noto Sans SC variable TTF was placed at `.cache/teacher_reporting_fonts/NotoSansSC.ttf`.

The font is available from [Google Fonts](https://github.com/google/fonts/tree/main/ofl/notosanssc) under its OFL license. After preparing the required tables and font, run:

```bash
.venv/bin/python scripts/build_teacher_brief.py
```

The script rebuilds figures, the summary PDF, presentation, and Excel workbook, then checks source hashes, font glyph coverage, page counts, and archive consistency. It regenerates the presentation materials without running training.
