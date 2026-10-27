# 跨轮验证器前置实验：第一天

本项目完成用户文档中的环境与最小验证阶段。第一天只运行 G0/V0 在 C 开发集的诊断和 A/B 短训练检查；不启动正式 G1/G2、Vrand/Vtarget 或最终 T 测试。已上传模型只读，所有新文件都在本项目中。

项目根目录：`/root/workspace/cross_round_pilot`。
G：`/root/workspace/Qwen2.5-Math-1.5B-Instruct`。
V0：`/root/workspace/Qwen3-4B-Instruct-2507`。

## 环境

使用 Python 3.11 的 `.venv`。为避免在低速网络下重复下载数 GB 文件，虚拟环境通过 `--system-site-packages` 复用系统预装且已实际验证的 `torch==2.7.1+cu126` 和 CUDA 动态库；应用新增依赖安装在项目 `.venv`。因此该环境仍依赖原机器基础环境，完整继承版本记录在 `reports/pip-freeze.txt`，不是一个完全脱离系统的环境。未改系统驱动或全局 pip 配置。初始使用阿里云约 0.3 MB/s；按用户要求实测六个国内源后，改用约 1.93 MiB/s 的华为云，并复用 129 MiB 已下载完整 wheel（明确作为本地安装文件输入）。

```bash
cd /root/workspace/cross_round_pilot
source .venv/bin/activate
python -m pip check
```

重建同类服务器环境：

```bash
python3.11 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -i https://repo.huaweicloud.com/repository/pypi/simple/ -r requirements.txt -c constraints.txt
```

完整实际版本锁定在 `requirements-lock.txt`。不同机器需先按照官方配套安装 Torch；不能直接忽略本项目 `+cu126` 约束而让 pip 悄悄改装 Torch。本机无需再次安装 Torch。若之后确实需要下载 CUDA wheel，已检查可访问的国内索引为 `https://mirrors.nju.edu.cn/pytorch/whl/cu126/`，索引可达不代表每个 wheel 已完成带宽验证。

在 VS Code 中选择解释器 `/root/workspace/cross_round_pilot/.venv/bin/python`。项目的 `scripts/pilot` 不依赖当前工作目录，也不要求激活环境。

## 第一天命令

首次准备数据需网络，下载 GSM8K 官方仓库的 train/test JSONL 后保存 SHA256、原始行号、ID 和规范化问题哈希。固定 seed=42；A/B/C 来自 train，T 来自 test。T 只划分保存，不用于第一天推理、提示开发或训练。模型输入 JSONL 不含标准答案，独立标签另存 `data/labels/`。

```bash
scripts/pilot prepare
scripts/pilot inspect
PYTHONPATH=src .venv/bin/python -m pytest tests -q
scripts/day1.sh
```

也可以逐步运行：

```bash
scripts/pilot generate --split C --limit 50 --tag G0_C50
scripts/pilot score --input G0_C50 --tag V0_G0_C50
scripts/pilot generate --split A --limit 4 --tag G0_A_smoke
scripts/pilot generate --split B --limit 24 --tag G0_B_smoke
scripts/pilot train_smoke --role G
scripts/pilot train_smoke --role V
scripts/pilot report
scripts/pilot audit
```

`generate` 和 `score` 的每条记录及时 flush/fsync。缓存身份包括模型文件身份、adapter 内容摘要、题目/候选 ID、提示和设置；逐题固定随机种子，恢复时重生成未完成题目并按候选缓存键去重。不要同时对同一个 tag 启动多个进程。GPU 工作严格串行，G/V 不同时驻留。`scripts/day1.sh` 可跳过已成功的训练；失败的训练若已保存 adapter，应保留并改名归档后重新运行，避免覆盖已有证据。

短训练 checkpoint 只命名为 `smoke_G_adapter` / `smoke_V_adapter`，绝不作为正式 G1、Vrand 或 Vtarget。G 使用 A 的自然回答检查 response-only SFT 路径；V 使用 B 中独立核验的自然回答进行标签训练。本次 B 固定前缀从8题扩至24题，58条可核验回答全部正确，最终短训练仅覆盖 A 标签，B 标签训练仍未验证；没有制造错误回答。短训练允许有限次循环微批以检查梯度，不能冒充正式数据规模。正式跨轮筛选、完整训练与最终 3×3 评价属于第二天及之后，需要继续实现和运行。

## 协议

- G 提示要求以 `#### 数值` 结束，但 C 格式诊断中模型坚持输出盒式答案。分别报告 #### 格式合规率和最终数值可核验率；在保留严格 #### 解析的同时，开发期显式加入唯一、位于回答末尾的数值 `\boxed{...}` 解析。支持整数、小数、负数、规范千位分隔和简单分数；用有理数精确比较。严格 #### 子集指标单独保留，不能声称原定格式要求已经通过。
- 正文最后一个数字不能充当答案。不执行生成代码。无标记、歧义、非法数字、截断分别记录，不作为已核验自然错误。
- V 只看问题和候选回答。A=最终数值正确，B=错误；用真实 chat template 下完整标签续写的条件 log likelihood 之和比较，softmax 仅为代理分数。原始阈值固定为 0.5，C 诊断未自动改动阈值。
- 训练通过完整 chat template 构造，核验生成前缀完全一致；只监督 assistant 内容和结束 token，prompt 和 padding 均为 -100。超长训练样本跳过并记录，不静默裁切。
- 保存与重载后对同一输入 logits 做实际比较；检查有限 loss、有效梯度、仅 LoRA 参数可训练、基座无梯度及参数版本未变。
- 可核验子集单独报告指标与分母；另外报告把格式失败计错的操作性 G 正确率。按问题聚类 bootstrap 1000 次，不将同题 4 个回答视为独立题目。

## 交付位置

- `reports/day1_report.md`：中文结果、限制与下一步。
- `reports/day1_metrics.json`、`day1_confusion.png`：开发集指标和混淆矩阵。
- `reports/day1_cases.md`：24 条真实可读案例，包含核验依据与诊断不确定性。
- `reports/environment.json`、`network.json`：GPU/驱动/runtime/Toolkit、权重结构与网络实测；`network_mirror_comparison.json` 为后续六个国内镜像同文件测速。
- `reports/budget_estimate.json`：基于实测的后续工作量线性外推。
- `reports/protocol_day1.json`、`code-sha256.txt`：协议与代码校验摘要。
- `runs/day1/`：全部原始候选、V 分数、独立标签、训练日志、adapter、可恢复状态。
- `data/manifests/splits.json`：固定划分和原始数据 SHA256。

最终答案标签不等于推理步骤标签；开发集结果不能证明跨轮漂移或定向更新有效。第一天报告不提供尚未执行的正式跨轮曲线或 3×3 最终成绩。

核对过的官方资料：[PyTorch 配套版本](https://pytorch.org/get-started/previous-versions/)、[Qwen3 模型说明](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507)、[GSM8K 原始数据](https://github.com/openai/grade-school-math)、[PEFT LoRA](https://huggingface.co/docs/peft/developer_guides/lora)。

## 第二天诊断与主任务决定

第二天已完成原50条不可核验回答审计、完整GSM8K C100及MATH训练70题（7题型×5难度各2题）的同协议比较，共680个新候选和680条V0评分。

- 原200条回答重审：188/198正确，完整输出正确率界限94%—95%，未显示解析筛选是高正确率的主要来源。
- GSM8K：393/400可核验，14个错误来自6题。
- MATH：262/280可核验，33个错误来自14题。
- 按预先设定门槛选MATH为主任务，GSM8K保留简单对照。原G/V未换，未加载smoke adapter，最终测试集未使用。
- 已冻结主任务与生成/评分/独立标签协议，尚未创建正式数据池或启动参数更新。

[第二天报告](reports/day2/day2_report.md)、[恢复与运行说明](reports/day2/README.md)、[原50条逐条审计](reports/day2/day1_50_failed_audit.md)、[冻结协议](configs/formal_protocol.json)。

```bash
scripts/pilot day2_status
scripts/pilot day2_audit
```


## Day3：DAPO 正式协议

已完成96道有效开发题主分析、4道题整体隔离、独立补充长度检查、V工作点、A600/B200/T300题池及自然正负短LoRA检查。当前入口：[Day3报告](reports/day3/day3_report.md)、[正式协议](configs/protocol_revisions/003_dapo_day3.json)、[审查材料](reports/day3/day3_review.xlsx)。旧formal_protocol.json保留为Day2历史快照，正式阶段读取current_protocol.json。尚未启动G1/G2或对T推理。
