# 固定 V0 的两轮 SFT 轨迹

本次执行 G0正式B/C基线 → G0在A上生成/固定V0筛选 → G1_SFT → G1的B/C与A → G2_SFT → G2的B/C。T封存。

当前冻结协议：`configs/protocol_revisions/003_dapo_day3.json`。执行清单：`configs/formal_execution.json`。开跑前审计记录：`preflight_audit.json`。

在服务器重连后：

```bash
tmux attach -t dapo_sft
# Ctrl-b 后按 d 可离开会话，任务继续；Ctrl-b 后按数字切换窗口。
cd /root/workspace/cross_round_pilot
scripts/pilot formal_status
tail -n 30 runs/formal/controller.log
```

`pipeline`窗口负责完整阶段控制；`baseline`窗口显示先启动的G0/B生成。执行修订001将后续G生成改为两个独立进程，互斥分配不同题目，每题仍生成4条。修订002在扩容后改为两张卡、每卡两个G工作进程；协调器独占GPU阶段锁，仅允许它管理的四个G工作进程并行。V评分和SFT训练仍按阶段执行。断网/聊天关闭不终止tmux。训练每16个优化器step保存adapter、优化器与随机状态，恢复后不复制样本；两轮均最多512条、1epoch。

结果随着阶段完成更新到 `progress_report.md`。全部完成后自动生成 `final_report.md`、`cross_round_metrics.json`、`final_artifact_audit.json` 与图表，checkpoint位于 `runs/formal/checkpoints/G1_SFT`、`G2_SFT`。在这些文件出现前不能声称已获得完整跨轮结果。

每轮实际SFT质量记录在 `G1_SFT_data_quality.json` 和 `G2_SFT_data_quality.json`；先落盘无真值的选样，再核验整个A池并记录质量。错误和未知不会据真答案从训练集偷偷删除。G2继承G1的adapter，V0模型/提示/似然实现/0.5阈值始终固定。

若进程因错误或预算停止，先查看 `runs/formal/state.json` 和对应阶段log，不要删除缓存。修复外部原因后，可在tmux新窗口执行 `bash scripts/formal_tmux.sh` 恢复。控制器有独占锁，重复启动不会并发训练。若程序/配置指纹变化会拒绝混用缓存，需要保留原产物并明确修订，不能直接改指纹绕过检查。

GPU工作全局上限40小时，保守预留此前2小时；达到上限时保存状态停止，不自动改轮次长度/删样本/增加GPU或关机。日志中的记录工作时间不是租赁账单。

## 双进程执行修订001

用户授权保留所有已经生成的回答，仅改变后续G生成的调度。原始`formal_run.py`、逐题种子、提示、采样与长度规则、V0评分实现和0.5阈值均保持不变。原始代码冻结清单不覆盖更新；变更前代码和切换时已保存输出的哈希记录均归档。审计入口为`configs/execution_revisions/001_two_generator_processes.json`。

八题影子测速结果：32条回答文本、长度和截断状态均与已有C缓存相同；并行窗口合计吞吐约341 token/s，原单进程实测约173–201 token/s。样本较小，后续速度和逐token一致性需按实际记录判断。测速回答单独保存在`runs/formal/parallel_benchmark/`，不写回正式C。

工作进程各自写`runs/formal/parallel/<轮次_题池>/attempt_*/worker_*/`，协调器是正式结果唯一写入者；完整题目落盘后才汇入正式JSONL。重启会保留原前缀并恢复已完成的分片题目。进度中`worker_pids`显示两个工作进程。预算按单张GPU被占用的墙钟时间记一次，不能把两个重叠进程的时长相加冒充两张卡的GPU小时。

## 双卡四进程执行修订002（当前）

已核验两张24GB RTX4090和停机前保存的G1 adapter；G1/B原113题452条缓存完整保留，从剩余87题继续。GPU0两个进程，GPU1两个进程，按题互斥分片，每题仍生成4条候选。进程通过CUDA_VISIBLE_DEVICES绑定单张物理卡UUID，原冻结生成函数中的cuda:0对应各自可见的卡。V0评分与SFT仍依原阶段顺序在GPU0执行，提示、评分实现、阈值、采样、长度、训练超参数不变。

四进程使用真实G1 adapter重放4道已缓存B题；16条文本、长度、截断、种子和缓存键全部一致，正式缓存未改写。104项测试通过。运行代码和修订001的历史归档均保留；修订002清单位于configs/execution_revisions/002_two_gpus_four_generator_processes.json。

`tmux attach -t dapo_sft`，窗口pipeline为控制器，status为进度，gpu为物理卡负载。`scripts/pilot formal_multigpu_revision`可以审计当前执行代码历史、扩容前数据前缀与G1检查点，支持缓存继续追加。停机专用的formal_resize_stop verify只适用于首次续跑前，恢复后请用新审计入口。

多卡阶段的work_ledger.jsonl记录seconds为GPU秒；两张卡同时工作10秒记20GPU秒，同卡两个进程不重复计数。一张卡全部工作进程结束后停止为它计时。V评分和SFT阶段仍按原单卡工作计时，停机时间不计入；全局40GPU小时及2小时历史预留保持不变，不能当作租赁账单。
