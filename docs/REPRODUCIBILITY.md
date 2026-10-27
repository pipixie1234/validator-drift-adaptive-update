# 代码、冻结协议与实验产物

## Git发布范围

仓库包含规范源码、运行脚本、测试、冻结配置、研究方案、划分清单、报告和选定汇总表。发布清单见`reproducibility/code_release_manifest.json`。

以下内容继续保留在实验服务器，不随Git上传：虚拟环境、下载缓存、模型权重、LoRA adapter、优化器、`data/`数据正文、完整`runs/`缓存、逐回答JSONL、大型逐条评分CSV及ZIP归档。文档中对应路径用于定位服务器记录。完整历史复算需要这些原始产物。

## 主要代码入口

| 阶段 | 模块 |
|---|---|
| 数据与独立答案规则 | `prepare.py`、`day2_protocol.py`、`dapo_prepare.py`、`day3_protocol.py` |
| 固定V0的G更新 | `formal_select.py`、`formal_train.py`、`formal_pipeline.py` |
| 多进程、双卡与vLLM | `formal_parallel.py`、`formal_multigpu.py`、`g3_pipeline.py` |
| Oracle / Random | `oracle_control.py`、`random_control.py`、`cache_followup.py` |
| 前缀与自然编辑 | `efp_intervention.py`、`prefix_paraphrase.py`、`natural_invariance.py`、`closing_diagnostics.py` |
| 完整前缀面板 | `prefix_full_panel.py`、`prefix_full_panel_report.py` |
| 七天T评价 | `week_final_run.py`、`week_final_report.py`、`week_final_verify.py` |
| SFT/DPO修复 | `robust_verifier_clean.py`、`robust_verifier_train.py`、`robust_verifier_eval.py` |
| 导师材料 | `scripts/build_teacher_brief.py` |

模块位于`src/pilot/`。主修复实验入口为`robust_verifier_clean`；初轮修复与题目质量修订后的主试验分别保存，含义见对应报告。

## 新环境运行

1. 安装匹配的Torch/CUDA及项目依赖。原机器复用系统Torch；`requirements-lock.txt`是包含系统继承包的环境快照。
2. 根据配置准备本地模型。模型身份与指纹见冻结配置。
3. 从原数据来源重建题池，或恢复原服务器的`data/`与`runs/`。划分清单副本在`reproducibility/manifests/`。
4. 新实验使用新的配置和运行目录。正式模块校验模型、源码与输入哈希；路径或实现变动后不向旧缓存追加结果。
5. 先检查标签、长度、掩码和保存重载，再运行GPU任务；长任务使用tmux。

数据来源：[GSM8K](https://github.com/openai/grade-school-math)、[MATH](https://huggingface.co/datasets/EleutherAI/hendrycks_math)、[DAPO处理题池](https://huggingface.co/datasets/open-r1/DAPO-Math-17k-Processed)。

## 不依赖模型权重的检查

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_day2_protocol.py tests/test_dapo.py -q
```

其余测试包含依赖历史缓存、模型tokenizer与题池的集成审计。上述小测试用于核验解析及协议逻辑。

## 材料导出

`scripts/build_teacher_brief.py`只读汇总表，不调用模型。额外依赖为`python-pptx`、`reportlab`、`openpyxl`、`fonttools`、`PyMuPDF`和`pypdf`。原机器将报告依赖隔离安装在`.cache/teacher_reporting_vendor`，将Noto Sans SC可变TTF置于`.cache/teacher_reporting_fonts/NotoSansSC.ttf`。

字体来源：[Google Fonts](https://github.com/google/fonts/tree/main/ofl/notosanssc)，按其OFL许可使用。准备所需表格和字体后运行：

```bash
.venv/bin/python scripts/build_teacher_brief.py
```

脚本重建图表、总结PDF、PPT和Excel，检查来源哈希、字体字符覆盖、页数与归档一致性。该步骤会重新导出导师材料，不执行训练。
