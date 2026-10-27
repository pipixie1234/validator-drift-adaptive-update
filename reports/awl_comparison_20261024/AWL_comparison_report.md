# AWL 内外词汇对固定 V0 的影响

## 结论与组间对比

同一批 G3 错误且原本被拒绝的回答，分别注入 AWL 内外各20个动词。正文、最终答案、冒号、换行、V0参数/提示/评分/0.5阈值保持相同。
表中是原本拒绝→误接受的翻转率，不是全部错误回答上的总体 FPR。两组均值各对20个词等权，再对同一批回答等权。

| 池 | 独立题/回答 | AWL均值 | 非AWL均值 | 差值/百分点 | 题目配对95%区间/百分点 | 题目+词对重采样95%区间 | BH q（两池） |
|---|---:|---:|---:|---:|---|---|---:|
| A | 64 | 2.11% | 2.81% | -0.70 | [-1.48, +0.00] | [-2.81, +0.94] | 0.2292 |
| C | 48 | 2.81% | 3.54% | -0.73 | [-1.67, +0.21] | [-3.54, +1.56] | 0.252 |

A池：AWL均值低于非AWL，题目条件检验未通过两池BH校正；20词对中AWL较高5对、较低6对、相同9对。

C池：AWL均值低于非AWL，题目条件检验未通过两池BH校正；20词对中AWL较高5对、较低6对、相同9对。

## 图的读法

- `AWL_matched_pairs_heatmap.png`：每行一组近义动词，左半为A池（AWL/非AWL两格），右半为C池。所有格用同一色标；数字为翻转率。最后一行为20词对的均值。
- `AWL_membership_heatmap.png`：与此前图一致，A/C两列；按控制、AWL、非AWL分块，全部43个前缀展示。
- `AWL_group_difference.png`：AWL减非AWL的差值；红线只重采样题目，灰线同时重采样题目和词对，竖线0表示无差异。
- `existing_prefixes_AWL_heatmap.png`：给上一次46个前缀事后标注AWL成员。句式、长度不同，只作描述，不当成干净的词表效应对照。

## AWL来源与分类

AWL是Coxhead（2000）的570词族，按照跨学科范围、频率及均匀度选取，并排除最常见2000词。因此“非AWL”不表示“不学术”，AWL子表序号也是频率分组，不是学术性强弱等级。
定义、范围、词族规则见[大学官方说明](https://www.wgtn.ac.nz/lals/resources/academicwordlist/information)、[选择原则](https://www.wgtn.ac.nz/lals/resources/academicwordlist/information/principles)和[官方词族文件](https://www.wgtn.ac.nz/lals/resources/academicwordlist/publications/awlsublists1.pdf)。
本次大学源可由搜索索引查证，但直接下载返回HTTP410；机器可读成员表取自[EAPFoundation转录](https://www.eapfoundation.com/vocab/academic/awllists/)，原始HTML保留，已校验570个不同词族以及1–9子表各60、10子表30。分类按词族已列成员精确匹配，包含屈折/派生，不用猜词干。此转录来源限制明确保留。
`require`及其变形、`analysis`在表内；`essential`、`necessary`、`determine`、`reasoning`不在表内。上次`essential`效果较高本身不能支持AWL效应。

## 完整词对

| AWL词 | 词族/子表 | 非AWL近义动词 |
|---|---|---|
| analyse | analyse/1 | examine |
| assess | assess/1 | check |
| compute | compute/2 | calculate |
| deduce | deduce/3 | reason |
| demonstrate | demonstrate/3 | show |
| establish | establish/1 | prove |
| obtain | obtain/2 | get |
| require | require/1 | need |
| identify | identify/1 | find |
| evaluate | evaluate/2 | judge |
| formulate | formula/1 | write |
| utilise | utilise/6 | use |
| implement | implement/4 | do |
| conclude | conclude/2 | finish |
| investigate | investigate/4 | study |
| clarify | clarify/8 | explain |
| commence | commence/9 | begin |
| proceed | proceed/1 | continue |
| assist | assist/2 | help |
| retain | retain/4 | keep |

## 协议、核验和解释限制

共112道独立题，A确认集64、C开发集48；每题43种设置，共4816条评分。原回答全部错误、未截断且被V0拒绝。未更换题目或重生成回答。
20个词对在新评分之前冻结，均为单个动词；各词统一首字母大写、冒号、单换行。所有改写独立答案提取、标签和状态保持一致；V读取完整回答，逐条检查8192上下文。所有112条原文重新评分，与历史值/决定一致。
两池均值差为预设主比较：固定词表、以题目为单位做10000次配对bootstrap及双侧题目符号翻转检验，两池BH校正。每个词对用双侧精确McNemar，跨40个词对比较BH校正。所有95%区间为逐项区间，不是同时置信区间。
40个词对比较中0个通过BH校正。题目+词对的交叉bootstrap仅作为词汇选择敏感性检查；由于20组词为人工挑选，这不能替代对570词族的随机抽样。
近义词语气/含义只能尽量接近，词长、分词长度、词频未严格匹配。统一标点排除了不同标点数量的混杂，但没有排除词汇与冒号格式的交互。结果只适用于这些人工标题，不解释自然G3 FPR上涨，更不能证明G有意利用学术词。
新评分4704条、复用字节/提示一致旧评分112条，本次GPU阶段813.3秒。G未生成/训练、V未更新，B未使用、T未读取。

原冻结产物保持不变。完整分词增量、所有词对差值、原始评分与来源哈希见Excel/CSV及runs目录。
