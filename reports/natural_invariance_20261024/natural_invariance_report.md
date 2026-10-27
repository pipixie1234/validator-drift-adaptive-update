# 自然回答：V原本判对后，删除/近义替换是否造成新错误

本实验按用户澄清，仅选原本判断正确的TP和TN。不是挑选FP修复，也不是把人工注入前缀再删除。
TP是正确回答被接受，干预后拒绝即TP→FN；TN是错误回答被拒绝，干预后接受即TN→FP。以下为这批原本判对样本上的条件新错误率，不是全体回答的总体FNR/FPR。

|池|原状态|回答/独立题|删除后的新错误|删除率及配对95%区间|近义替换后的新错误|替换率及配对95%区间|
|---|---|---:|---:|---|---:|---|
|A|TP|340/125|5|1.47% [0.30, 2.89]|6|1.76% [0.30, 3.54]|
|A|TN|306/140|5|1.63% [0.33, 3.18]|0|0.00% [0.00, 0.00]|
|C|TP|34/16|1|2.94% [0.00, 11.11]|0|0.00% [0.00, 0.00]|
|C|TN|61/30|1|1.64% [0.00, 5.66]|1|1.64% [0.00, 5.66]|

## 取样和干预

使用G3自然A/C缓存，真假标签已独立核验、原V0决定正确、不截断；未知排除。覆盖全部预设词/短语命中样本，未按干预效果抽样。
目标是上次冻结的20组AWL/非AWL近义动词（双向替换）以及we need to→we have to、to solve for→to find。只在回答开头160字符、首次公式/数字/换行前、前12个词起点中取最早一个目标；同起点取最长跨度。
删除或替换仅改变记录的一个跨度，保留其余全部字节及最终答案。替换沿用首字母大小写；所有改写独立重提答案，确认最终答案值、真假标签及解析状态均不变。V0读取完整回答；参数、提示、评分实现及0.5阈值固定。

## 能说明与不能说明

出现新错误，支持V对这些自然文本编辑存在局部判断敏感性。它不证明G已利用这些词，也不解释训练后FPR上涨。
删除可能造成语法不自然，词对为近义而非所有上下文完全等义；标签不变不等于全文语义完全不变。例：we need to有专门的语法改写we have to，避免直接变成we require to，但其他近义动词仍可能改变细微语气。
因为按原判断正确选择样本，原错误率被固定为0；不能用普通随机样本的改善/恶化检验来外推总体。区间按题整组bootstrap10000次，仅描述当前选择规则下的变化，未包含不同训练种子。
TP/TN分布与题型、目标词、长度可能不同；两类判错率不能直接解释为词语对正负类的纯因果差异。AWL与非AWL自然命中也没有匹配，仅在逐目标表作描述。C已反复使用，是开发集，T仍封存。

## 变化案例

全部变化保留完整题目、原回答、编辑后回答和两次分数。案例核验仅保证独立最终答案标签，过程理由需另行审查。

### A TP / delete
ID=dapo:train:8928998d-0896-4024-bf5d-31944b7bf796:extension004:G3:2；标签=True；目标=we need to；分数0.977023→0.2227。
Six congruent circles form a ring with each circle externally tangent to the two circles adjacent to it.  All six circles are internally tangent to a circle $\cal C$ with radius 30.  Let $K$ be the area of the region inside $\cal C$ and outside all of the six circles in the ring.  Find $\lfloor K\rfloor$. (The notation $\lfloor K\rfloor$ denotes the greatest integer that is less than or equal to $K$.)
原开场：
```text
To solve the problem, we need to determine the radius of the six congruent circles that form the ring and then calculate the area of the region inside the larger circle \(\cal C\) but outside the six smaller circles.

First, let's denote the radius of each of the six congruent circles by \(r\). Since these circles form a ring and are all internally tangent to the larger circle \(\cal C\) with radius 30, the distance from the center of the larger 
```
编辑后开场：
```text
To solve the problem,  determine the radius of the six congruent circles that form the ring and then calculate the area of the region inside the larger circle \(\cal C\) but outside the six smaller circles.

First, let's denote the radius of each of the six congruent circles by \(r\). Since these circles form a ring and are all internally tangent to the larger circle \(\cal C\) with radius 30, the distance from the center of the larger circle to 
```

### A TP / synonym
ID=dapo:train:72cf7ec2-9089-4be6-b4c0-2c4244efc363:extension004:G3:2；标签=True；目标=we need to；分数0.531209→0.164516。
Graphistan has $2011$ cities and Graph Air (GA) is running one-way flights between all pairs of these cities. Determine the maximum possible value of the integer $k$ such that no matter how these flights are arranged, it is possible to travel between any two cities in Graphistan using only GA flights, provided that the absolute value of the difference between the number of flights originating from and terminating at any city is not more than $k$.
原开场：
```text
To solve this problem, we need to analyze the conditions under which it is possible to travel between any two cities in Graphistan using only GA flights, given that the absolute value of the difference between the number of flights originating from and terminating at any city is not more than \( k \).

First, let's denote the number of flights originating from city \( i \) as \( d_i^+ \) and the number of flights terminating at city \( i \) as \(
```
编辑后开场：
```text
To solve this problem, we have to analyze the conditions under which it is possible to travel between any two cities in Graphistan using only GA flights, given that the absolute value of the difference between the number of flights originating from and terminating at any city is not more than \( k \).

First, let's denote the number of flights originating from city \( i \) as \( d_i^+ \) and the number of flights terminating at city \( i \) as \(
```

### A TN / delete
ID=dapo:train:f92e748b-1c67-42b0-a658-b6565e366583:extension004:G3:1；标签=False；目标=to solve for；分数0.164516→0.924142。
A trifecta is an ordered triple of positive integers \((a, b, c)\) with \(a < b < c\) such that \(a\) divides \(b\), \(b\) divides \(c\), and \(c\) divides \(ab\). What is the largest possible sum \(a + b + c\) over all trifectas of three-digit integers?
原开场：
```text
To solve for the largest possible sum \(a + b + c\) over alltrmodeles of three-digit integers, we start by analyzing the conditions given for the twist. The conditions are:
1. \(a < b < c\)
2. \(a\) divides \(b\)
3. \(b\) divides \(c\)
4. \(c\) divides \(ab\)

Let's express \(b\) and \(c\) in terms of \(a\). Since \(a\) divides \(b\), we can write \(b = ka\) for some integer \(k > 1\). Similarly, since \(b\) divides \(c\), we can write \(c = mb\)
```
编辑后开场：
```text
 the largest possible sum \(a + b + c\) over alltrmodeles of three-digit integers, we start by analyzing the conditions given for the twist. The conditions are:
1. \(a < b < c\)
2. \(a\) divides \(b\)
3. \(b\) divides \(c\)
4. \(c\) divides \(ab\)

Let's express \(b\) and \(c\) in terms of \(a\). Since \(a\) divides \(b\), we can write \(b = ka\) for some integer \(k > 1\). Similarly, since \(b\) divides \(c\), we can write \(c = mb\) for some in
```

### C TP / delete
ID=dapo:train:8d4a49c3-2e3c-4abc-bd52-150ba902e0c7:extension004:G3:0；标签=True；目标=we need to；分数0.880797→0.164516。
In how many ways can 8 people sit around a round table if Pierre and Thomas want to sit together, but Rosa doesn't want to sit next to either of them? (Treat rotations as not distinct but reflections as distinct.)
原开场：
```text
To solve the problem, we need to consider the constraints and the nature of the round table seating arrangement. Here's a step-by-step solution:

1. **Treat Pierre and Thomas as a single unit**: Since Pierre and Thomas want to sit together, we can consider them as a single "block" or "unit". This reduces the problem to arranging 7 units (the Pierre-Thomas block and the other 6 people) around a round table.

2. **Arrange the 7 units around the tab
```
编辑后开场：
```text
To solve the problem,  consider the constraints and the nature of the round table seating arrangement. Here's a step-by-step solution:

1. **Treat Pierre and Thomas as a single unit**: Since Pierre and Thomas want to sit together, we can consider them as a single "block" or "unit". This reduces the problem to arranging 7 units (the Pierre-Thomas block and the other 6 people) around a round table.

2. **Arrange the 7 units around the table**: The 
```

### C TN / delete
ID=dapo:train:0849d010-7bdf-4a5e-9e02-e9d158abc769:extension004:G3:1；标签=False；目标=to solve for；分数0.268941→0.962673。
Define the sequence of positive integers $\{a_n\}$ as follows.  Let $a_1=1$, $a_2=3$, and for each $n>2$, let $a_n$ be the result of expressing $a_{n-1}$ in base $n-1$, then reading the resulting numeral in base $n$, then adding $2$ (in base $n$).  For example, $a_2=3_{10}=11_2$, so $a_3=11_3+2_3=6_{10}$.  Express $a_{2013}$ in base $10$.
原开场：
```text
To solve for \(a_{2013}\) in the sequence defined by \(a_1 = 1\), \(a_2 = 3\), and for \(n > 2\), \(a_n\) is the result of expressing \(a_{n-1}\) in base \(n-1\), then reading the resulting numeral in base \(n\), and then adding 2 (in base \(n\)), we need to understand the pattern in the sequence.

First, let's compute the first few terms of the sequence to identify any patterns:

1. \(a_1 = 1\)
2. \(a_2 = 3\)
3. For \(a_3\), we express \(a_2 = 3
```
编辑后开场：
```text
 \(a_{2013}\) in the sequence defined by \(a_1 = 1\), \(a_2 = 3\), and for \(n > 2\), \(a_n\) is the result of expressing \(a_{n-1}\) in base \(n-1\), then reading the resulting numeral in base \(n\), and then adding 2 (in base \(n\)), we need to understand the pattern in the sequence.

First, let's compute the first few terms of the sequence to identify any patterns:

1. \(a_1 = 1\)
2. \(a_2 = 3\)
3. For \(a_3\), we express \(a_2 = 3\) in base 2
```

### C TN / synonym
ID=dapo:train:0849d010-7bdf-4a5e-9e02-e9d158abc769:extension004:G3:1；标签=False；目标=to solve for；分数0.268941→0.798187。
Define the sequence of positive integers $\{a_n\}$ as follows.  Let $a_1=1$, $a_2=3$, and for each $n>2$, let $a_n$ be the result of expressing $a_{n-1}$ in base $n-1$, then reading the resulting numeral in base $n$, then adding $2$ (in base $n$).  For example, $a_2=3_{10}=11_2$, so $a_3=11_3+2_3=6_{10}$.  Express $a_{2013}$ in base $10$.
原开场：
```text
To solve for \(a_{2013}\) in the sequence defined by \(a_1 = 1\), \(a_2 = 3\), and for \(n > 2\), \(a_n\) is the result of expressing \(a_{n-1}\) in base \(n-1\), then reading the resulting numeral in base \(n\), and then adding 2 (in base \(n\)), we need to understand the pattern in the sequence.

First, let's compute the first few terms of the sequence to identify any patterns:

1. \(a_1 = 1\)
2. \(a_2 = 3\)
3. For \(a_3\), we express \(a_2 = 3
```
编辑后开场：
```text
To find \(a_{2013}\) in the sequence defined by \(a_1 = 1\), \(a_2 = 3\), and for \(n > 2\), \(a_n\) is the result of expressing \(a_{n-1}\) in base \(n-1\), then reading the resulting numeral in base \(n\), and then adding 2 (in base \(n\)), we need to understand the pattern in the sequence.

First, let's compute the first few terms of the sequence to identify any patterns:

1. \(a_1 = 1\)
2. \(a_2 = 3\)
3. For \(a_3\), we express \(a_2 = 3\) in
```

## 资源和产物

共741条自然回答、2223条评分；新评分1691条、完整输入相同历史复用532条，GPU阶段257.6秒。每条原文均重新评分复现历史分数及决定。无G生成/训练，无V更新，B未使用、T未读取。
图片natural_TP_TN_new_errors.png、汇总CSV和逐词表、全部变化JSONL，原文/跨度/全部分数保存在runs/natural_invariance_20261024。


## 完成后的核对补记

共观察到19个“回答×处理”翻转，涉及15条不同回答、13道不同题。删除：TP→FN 6条、TN→FP 6条；近义替换：TP→FN 6条、TN→FP 1条。两种处理之间有重合，不能把19当作19条独立回答。绝大多数本次选中回答在两种编辑下均保持正确判断。

未观察翻转的格子应读为“当前样本未观察到”，不是总体错误概率严格为0。由于非参数bootstrap只能重采样已有观测，全0结果的bootstrap区间会退化为[0,0]，不能将其当成零风险的证明；C的样本与题数尤其少。全部区间都是当前选择规则下的探索性逐项区间。

删除可能留下多余空格或不完整句式；故不能把删除的全部影响归为目标词本身。近义替换也需逐例核查含义和语法。V只输出A/B评分，这里未生成判定理由，不能推断其内部推理。项目tests目录154项测试通过，原始分数复现、独立答案保持、评分哈希及完整上下文检查通过。
