# DAPO 开发诊断

阶段安排：保留第二天全部产物与原冻结配置，暂缓正式 G1/G2；固定英语开发100题，每题4候选。原始G0/V0不加载adapter，仅提高生成与完整评分长度上限。

数据：`open-r1/DAPO-Math-17k-Processed` 的英语训练部分，固定revision `31dd309567e3da778038cc87d868b6097a3ccf68`，文件SHA256见 `data/dapo/manifests/selection_locked.json`。来源UUID与原始包装prompt校验哈希均保留。没有下载任何公开测试集。

预先配置：`configs/dapo_diagnostic.json`。3072输出token，G总上下文4096；前20题长度预检也是固定100题的一部分。任何截断保持未知。

在项目目录执行：

```bash
scripts/pilot dapo_status
# 已固定抽样后可断点续跑；禁止重新执行prepare改变抽样。
bash scripts/dapo.sh
```

完成后查看：`dapo_report.md`、`dapo100_metrics.json`、`length_pilot.json`、`length_full.json`、`dapo_cases.md`、`artifact_audit.json`。协议修订位于 `configs/protocol_revisions/003_dapo_diagnostic.json`；仅在预先门槛全部通过时建立正式A/B/T池，仍不开始参数训练。最终评价池只保存，不能用来选题、调提示或比较配置。

旧配置 `configs/formal_protocol.json` 继续作为第二天冻结快照保留；下一阶段读取 `configs/current_protocol.json` 所指修订。重跑第二天汇总脚本会写旧冻结文件，需避免在第三阶段混用。
