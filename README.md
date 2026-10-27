# Validator Drift & Adaptive Update

**Verifier reliability in iterative synthetic mathematics data: cross-round drift, prefix sensitivity, and repair experiments.**

This project studies whether a fixed verifier remains reliable as a generator is repeatedly updated through supervised fine-tuning (SFT) on verifier-selected answers. It also examines how short prefixes affect judgments when the mathematical response stays unchanged, and whether SFT or DPO can repair verifier errors without introducing new ones.

The original seven-day workflow is complete, followed by prefix diagnostics and a four-arm verifier repair study. This repository contains source code, frozen configurations, tests, split manifests, and summary materials. Model weights, environments, raw datasets, and complete run caches remain on the experiment server.

## Start here

- [Research summary](reports/teacher_brief_20261027/研究总结.md) · [11-page summary PDF](reports/teacher_brief_20261027/研究总结.pdf)
- [19-slide editable presentation](reports/teacher_brief_20261027/导师汇报.pptx) · [Presentation PDF](reports/teacher_brief_20261027/导师汇报.pdf)
- [Key experimental results: Excel workbook](reports/teacher_brief_20261027/关键实验结果.xlsx)
- [Seven-day workflow and held-out T results](reports/week_final_20261026/week_final_report.md)
- [Prefix findings and diagnostics](reports/week_final_20261026/prefix_findings_report.md)
- [SFT/DPO verifier repair study](reports/verifier_robustness_clean_20261026/robust_verifier_report.md)
- [Research plan](docs/一周前置实验方案_20261020.md) · [Reproducibility and artifact guide](docs/REPRODUCIBILITY.md)

Research reports, presentations, and the research plan linked above are in Chinese. Repository navigation and reproducibility documentation are in English.

## Main findings

| Experiment | Observation |
|---|---|
| Fixed-V0 evaluation across rounds, T299 | G0 → G2: balanced accuracy (BA) falls from 72.79% to 69.55%; change: −3.24 percentage points, paired question-level 95% CI [−6.06, −0.39]. |
| Full prefix panel, C95/G3 | Adding `we can` increases FPR from 54.59% to 65.14% and decreases FNR from 14.29% to 8.16%, with the mathematical response and final answer unchanged. |
| Repair controls, E60/G3/`we can` | DPO_clean repairs 13 FPs and introduces 8 FNs; DPO_aug repairs 12 FPs and introduces 7 FNs. Both achieve a net repair of 5 answers. |

FPR is the fraction of incorrect answers accepted by the verifier; FNR is the fraction of correct answers rejected. C is the development pool, T is the original held-out evaluation pool, and E is a fresh question pool for the repair study. Results are reported separately for each pool. In the current experiment, DPO reduces false acceptances while adding false rejections; prefix augmentation has not shown a clear additional net benefit over unaugmented DPO.

## Repository structure

```text
src/pilot/                 Generation, answer checking, verifier scoring, training, and analysis
configs/                   Frozen protocols, sampling settings, and training configurations
tests/                     Answer parsing, loss masks, selection, and statistical checks
scripts/                   Command wrappers, background execution, and report export
reproducibility/manifests/  Copies of the original split and selection manifests
reports/                   Stage reports, figures, summary tables, and presentation materials
docs/                      Research plan, reproducibility guide, and historical documentation
```

The generator is `Qwen2.5-Math-1.5B-Instruct`; the initial verifier is `Qwen3-4B-Instruct-2507`. The main task uses English DAPO mathematics questions with four candidates per question. G0 → G1 → G2 is the original SFT trajectory; G3 is an extension. Pool A supplies generator updates, B supplies verifier updates, C supports development, and T provides the original final evaluation.

## Environment and checks

The original environment uses Python 3.11, PyTorch 2.7.1+cu126, Transformers 4.56.2, and PEFT 0.17.1. `requirements-lock.txt` records the full server environment; `requirements.txt` and `constraints.txt` retain the original experiment dependencies.

On a server with the matching Torch/CUDA stack already installed:

```bash
python3.11 -m venv --system-site-packages .venv
.venv/bin/python -m pip install \
  -i https://mirrors.huaweicloud.com/repository/pypi/simple \
  -r requirements.txt -c constraints.txt
.venv/bin/python -m pip install sympy lark

PYTHONPATH=src .venv/bin/python -m pytest tests/test_day2_protocol.py tests/test_dapo.py -q
```

`scripts/pilot <module> ...` uses the project `.venv`; `scripts/pilot-vllm` uses a separate `.venv-vllm`. Other tests include integration checks that require local models, question pools, or historical caches.

Frozen configurations contain the original server paths and SHA256 hashes. Follow the [reproducibility guide](docs/REPRODUCIBILITY.md) to prepare models, data, and separate run directories on another machine. Keep historical configurations intact when creating new runs.

## Research contributions and method sources

The project's contributions center on the research questions, content-preserving controls, naturally generated answer data, and analysis of cross-round risk and repair-induced errors. The repair data augmentation draws on [AdvJudge-Zero](https://arxiv.org/html/2512.17375v3); DPO uses the [standard reference-relative preference objective](https://arxiv.org/abs/2305.18290). The [research summary](reports/teacher_brief_20261027/研究总结.md) explains the relationship to prior work.

The earlier README is preserved in [docs/history](docs/history/README_day3_snapshot.md). Its status statements describe that historical stage; this page and the latest reports reflect current progress.
