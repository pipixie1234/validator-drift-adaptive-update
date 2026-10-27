# Validator Drift & Adaptive Update

**迭代合成数学数据中的验证器可靠性：跨轮漂移、前缀敏感性与修复探索。**

本项目研究：生成器持续利用验证器筛选的数据进行SFT更新后，固定验证器能否继续可靠判断其回答；保持数学正文不变的前缀表达如何影响判断；以及SFT/DPO更新验证器能修复多少FP/FN、同时新增多少FP/FN。

原七天方案的核心实验已完成，后续补充前缀诊断和四组验证器修复对照。此仓库发布代码、冻结配置、测试、数据划分清单和汇总材料；模型权重、虚拟环境、原始数据与完整运行缓存保留在实验服务器。

## 从这里阅读

- [研究总结](reports/teacher_brief_20261027/研究总结.md) · [11页总结PDF](reports/teacher_brief_20261027/研究总结.pdf)
- [19页可编辑汇报PPT](reports/teacher_brief_20261027/导师汇报.pptx) · [同版PDF](reports/teacher_brief_20261027/导师汇报.pdf)
- [关键实验结果Excel](reports/teacher_brief_20261027/关键实验结果.xlsx)
- [原七天流程与T留出结果](reports/week_final_20261026/week_final_report.md)
- [前缀发现与全部诊断](reports/week_final_20261026/prefix_findings_report.md)
- [SFT/DPO验证器修复](reports/verifier_robustness_clean_20261026/robust_verifier_report.md)
- [研究方案](docs/一周前置实验方案_20261020.md) · [复现与产物说明](docs/REPRODUCIBILITY.md)

## 主要结果

| 实验 | 观察 |
|---|---|
| 固定V0跨轮评价，T299 | G0→G2：BA 72.79%→69.55%；差−3.24个百分点，按题配对95%区间[−6.06,−0.39] |
| 完整前缀面板，C95/G3 | 注入`we can`：FPR 54.59%→65.14%，FNR 14.29%→8.16%；数学正文与最终答案不变 |
| 修复对照，E60/G3/we can | DPO_clean修复13个FP、新增8个FN；DPO_aug修复12个FP、新增7个FN；两组净修复各5条 |

FPR是错误回答中被接受的比例，FNR是正确回答中被拒绝的比例。C为开发集，T为原七天留出，E为修复实验新题池；各自的结果分别报告。当前DPO减少部分误接受，同时增加误拒，前缀增强尚未显示优于普通DPO的明确净收益。

## 项目结构

```text
src/pilot/                 生成、独立答案核验、V评分、训练及统计分析
configs/                   各阶段冻结协议、采样和训练设置
tests/                     答案规则、掩码、选择与统计检查
scripts/                   命令入口、后台运行和材料导出
reproducibility/manifests/  原数据划分和选择清单副本
reports/                   阶段报告、图表、汇总表和导师材料
docs/                      研究方案、复现说明与历史首页
```

生成器为`Qwen2.5-Math-1.5B-Instruct`，验证器为`Qwen3-4B-Instruct-2507`。主任务为DAPO英语数学题，每题4候选。G0→G1→G2为原SFT轨迹，G3为扩展；A用于G更新，B用于V更新，C用于开发，T用于原流程最终评价。

## 环境与检查

原环境为Python 3.11、PyTorch 2.7.1+cu126、Transformers 4.56.2、PEFT 0.17.1。完整服务器快照在`requirements-lock.txt`；`requirements.txt`和`constraints.txt`保留原实验依赖。

在已安装匹配Torch/CUDA的服务器上：

```bash
python3.11 -m venv --system-site-packages .venv
.venv/bin/python -m pip install \
  -i https://mirrors.huaweicloud.com/repository/pypi/simple \
  -r requirements.txt -c constraints.txt
.venv/bin/python -m pip install sympy lark

PYTHONPATH=src .venv/bin/python -m pytest tests/test_day2_protocol.py tests/test_dapo.py -q
```

`scripts/pilot <模块> ...`使用项目`.venv`，`scripts/pilot-vllm`使用独立`.venv-vllm`。其余测试包含依赖本地模型、题池或历史缓存的集成检查，运行前需恢复相应产物。

冻结配置包含原服务器路径和SHA256；在新机器完整复跑前，应按照[复现说明](docs/REPRODUCIBILITY.md)准备模型、数据和独立运行目录。历史配置不直接覆盖为新路径。

## 研究与方法来源

项目贡献集中在具体问题、内容保持的对照设计、真实回答数据，以及跨轮风险和修复损伤分析。修复数据增强借鉴[AdvJudge-Zero](https://arxiv.org/html/2512.17375v3)；DPO采用[标准参考相对偏好目标](https://arxiv.org/abs/2305.18290)。[完整总结](reports/teacher_brief_20261027/研究总结.md)说明本项目工作与相关文献的关系。

早期首页作为历史记录保存在[docs/history](docs/history/README_day3_snapshot.md)。其中的阶段状态以当时记录为准，当前完成状态见本页和最新报告。
