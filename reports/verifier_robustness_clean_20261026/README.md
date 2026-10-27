# 验证器修复实验交付

[主报告](robust_verifier_report.md) · [五页图册](robust_verifier_figures.pdf) · [Excel](robust_verifier_review.xlsx)

四组均从V0独立更新；主要结果分别核算修复/新增FP、FN，辅以自然输入保持、留出增强前缀及新E60有效题迁移。

[四类变化CSV](repair_damage.csv) · [增强组相对原文组的四类变化](contrast_repair_damage.csv) · [完整变化案例](changed_cases_full.jsonl) · [2×2指标差值](factorial_comparisons.csv) · [新E标签与截断统计](E_label_summary.json)

题面问题整题隔离，原E64题完整原始缓存保留；隔离回答和参考比较标签另存quarantined_E_*，不作为回答未知或G错误。

旧数据、模型、周报均保留，主修订训练和评分产物位于runs/verifier_robustness_clean_20261026；被替代初轮在runs/verifier_robustness_20261026。复核入口：scripts/pilot robust_verifier_clean verify。
