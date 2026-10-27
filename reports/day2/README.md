# 第二天诊断入口

固定使用原始 G0/V0，本次不训练模型，不动 GSM8K T，不下载 MATH test。运行阶段以 `runs/day2/state.json` 和日志为准，只有全部680个候选及评分完成后才会生成最终比较和任务决定。

```bash
cd /root/workspace/cross_round_pilot
scripts/pilot day2_prepare
PYTHONPATH=src .venv/bin/python -m pytest tests -q
scripts/pilot day2_audit_old
scripts/day2.sh
scripts/pilot day2_audit
```

实时查看：

```bash
scripts/pilot day2_status
tail -f runs/day2/pipeline.log
```

- `day1_50_failed_audit.md` / `day1_50_failed_audit_summary.json`：原失败50条逐条审查及原/恢复组比较，不改写第一天原始文件。
- `day2_report.md`：最终中文任务比较。
- `gsm8k_C100_metrics.json` / `math70_metrics.json`：明确分母、错误题目分布、长度、问题聚类区间。
- `day2_cases.md` / `confusion_comparison.png`：代表性案例及混淆矩阵。
- `task_decision.json`：按生成前设定门槛选择主任务。满足条件时保存 `configs/formal_protocol.json`；未通过则不假装冻结或启动正式训练。
- `reference_parser_precheck.json`：固定抽样后的参考表达式支持范围；未据此替换题目。
- `artifact_audit.json`：题目隔离、模型谱系、缓存与真值分离审计。

MATH固定revision：`21a5633873b6a120296cce3e2df9d5550074f4a3`，训练部分每个题型×Level1—5抽2题，共70题。只按输入的文本可解性和难度元数据预先排除图片、未知难度和重复问题；不看模型生成结果选题，也不按参考答案能否解析选题。所有70题专用于开发，未来不得用于正式G/V训练或最终测试。这里的ID来自固定revision的训练子集行号，并非冒充原始竞赛题号。

两任务均使用相同原生盒式提示、4候选、temperature=0.7、top_p=0.9、max_new_tokens=1536，V用同一A/B提示及阈值0.5。第一天的生成提示和上限不同，因此旧/新生成差异不归因于单一修改；旧50条审计则使用同一原始回答和旧V分数，专门隔离解析规则的影响。

数学核验采用显式答案提取与受限SymPy LaTeX Lark解析，不调用 `eval` / `exec`，不运行模型生成的Python。不支持、歧义、超时或无法判断等价的形式保持unknown。符号和单位规则均记录在独立 `day2_protocol.py`；不修改第一天 `core.py`。训练集难度试验存在预训练污染可能，不能当作独立无污染泛化成绩。

本次协议开发中修复了两类实际发现的表示问题：SymPy Lark的π/复数单位处理，以及解题正文Markdown `#### Step` 标题与答案标记的区分。集合按数学集合语义去重，未知矩阵和区间并集仍不判错。修正不依赖候选是否符合参考答案，不影响G/V输入；最终标签统一用同一版解析器重算，源文件SHA256随正式决定保存。

当前比较的解释边界：旧50题的原输出重审用于隔离解析影响；新C100与MATH70用于同协议任务比较。新旧输出的G提示、最大长度与V提示均存在变化，因此不以新旧差异证明某一修改有因果效果。任务选择门槛提前记录，解析器语义则属于开发集上的协议修正，不冒充完全盲法预注册。
