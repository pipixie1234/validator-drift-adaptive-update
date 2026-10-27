# vLLM备用环境

vLLM 0.10.0+cu126，Torch 2.7.1+cu126，CUDA构建12.6，Transformers4.56.2；独立环境cross_round_pilot/.venv-vllm。原训练环境版本保持不变。

2026-10-22安装和CPU导入通过。2026-10-24补做GPU生成与Oracle LoRA兼容检查，G0和LoRA各8条独立合成回答均完成、解析正确；无A/B/C/T题目参与。

入口：scripts/pilot-vllm。测试细节见gpu_smoke.json、independent_smoke_outputs.jsonl及gpu_smoke_20261024.log。

LoRA运行时为BF16，原PEFT为FP32适配器；这是兼容性测试，不是逐数值等价性证明，也不是与Transformers匹配的吞吐基准。此环境未替换原实验引擎。
