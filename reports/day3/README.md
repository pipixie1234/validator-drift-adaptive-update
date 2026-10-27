# Day 3 交付

先阅读 [day3_report.md](day3_report.md)。当前正式入口是 `configs/current_protocol.json` 指向的 `configs/protocol_revisions/003_dapo_day3.json`；原 `configs/formal_protocol.json` 是第二天历史快照，保持原样。

- 原100题400候选保留在 `runs/dapo`；4道坏题的全部16候选隔离，主分析为96题384候选，见 `C_valid_metrics.json`。
- 补充32条长度重放位于 `runs/day3/length_replay`，不混入主结果；决策见 `length_decision.json`。
- 正式G输出预算：`min(3840,4096-实际提示token数)`，各轮同规则；V完整上下文8192；阈值0.5。
- `working_point.json` 保存开发集风险/保留率与每题选一条的SFT质量预演；实际A池质量尚未产生，不能用预演或工程smoke冒充。
- `data/dapo/formal/manifest.json` 保存互斥A600/B200/T300；全部开发100题及近似题族排除，T尚未推理。
- `day3_review.xlsx` 与 `review_records.jsonl` 保存全部原文和待审字段；控制字符在Excel显式转义、JSONL保持原文。V理由和G过程审查尚未开展。
- G/V短训练检查均通过，V自然标签8A+8B；这些adapter不能进入正式训练谱系。

项目目录下可断点重跑：

```bash
bash scripts/day3.sh
```

成功的短检查直接复用，不重复优化器step。禁止调用旧 `scripts/day2.sh` 或 `scripts/dapo.sh` 去覆盖第三阶段状态；第三天协议以本README入口为准。

后续G0/C应按新的正式长度设置缓存，以便与G1/G2使用同一设置比较；原3072诊断仅为开发材料。训练时保存实际入选样本的正确/错误/未知比例，标准答案仅用于事后质量记录。
