> 当前实例已完成双卡扩容并恢复四进程生成。日常验证请用 `scripts/pilot formal_multigpu_revision`，运行说明见 README.md。下文保留原停机交接记录。

# 当前实例 GPU 扩容交接

停机准备时间：2026-10-21T19:56:32.105431+08:00。

已经停止全部实验控制器及GPU工作进程，释放运行锁；服务器关机由用户在平台操作。磁盘保留由用户确认。G1训练已经完成，当前恢复点是generate_G1_B，有效缓存113题、452条候选。已完成分片已汇入正式结果；已保存回答不重做，尚未落盘的进行中题目可以重新计算。G0的A/B/C及V0评分、G1 adapter和训练数据完整保留。T封存。

开机后先确认`/root/workspace`保留，在项目目录运行：

```bash
cd /root/workspace/cross_round_pilot
nvidia-smi
scripts/pilot formal_resize_stop verify
scripts/pilot formal_status
```

验证脚本只读核对停止时缓存及G1文件哈希、原协议与执行修订，不启动GPU。硬件扩容后需要配置每个进程所用物理卡，并按实际卡数累计GPU小时；现有双进程程序仍仅使用一张卡。先让助手识别新硬件、测试并记录新的执行修订，再在tmux启动完整流程。tmux不会跨服务器重启保留进程。

原始采样、每题4候选、长度、固定V0提示/评分/0.5阈值和训练方案保持冻结。不要删除runs、修改原始代码冻结清单或从头训练G1。停机清单为`runs/formal/hardware_resize/stop_manifest.json`，包含恢复所需缓存和检查点哈希。
