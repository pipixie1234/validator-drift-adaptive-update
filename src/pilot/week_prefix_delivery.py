"""Assemble completed prefix reports and original vector PDFs into week delivery."""
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

from .core import ROOT,write_json
from .formal_common import utc

REPORT=ROOT/'reports/week_final_20261026'
BACKUP=ROOT/'runs/week_final_20261026/delivery_before_prefix_integration'
DEPENDENCY=ROOT/'.tools/pdf_bundle'
CHAPTERS=[
 ('fpr_patterns_20261024','EFP_SITE_report.md','EFP_SITE_figures.pdf','EFP触发与SITE分层','EFP triggers and input strata'),
 ('prefix_paraphrase_20261024','paraphrase_report.md','paraphrase_figures.pdf','前缀改写与自然原位替换','Prefix paraphrases and natural substitutions'),
 ('awl_comparison_20261024','AWL_comparison_report.md','AWL_comparison_figures.pdf','AWL词表内外对照','AWL versus non-AWL matched words'),
 ('natural_invariance_20261024','natural_invariance_report.md','natural_invariance_figures.pdf','原TP/TN自然删除与替换','Natural edits on originally correct judgments'),
 ('prefix_length_20261025','prefix_length_report.md','prefix_length_figures.pdf','前缀长度与连续评分','Prefix length and score sensitivity'),
 ('closing_diagnostics_20261025','supplementary_evidence_report.md','closing_diagnostics_figures.pdf','训练链、对称编辑与答案改错','Training chain, symmetric edits and answer control'),
 ('cross_round_prefix_20261025','cross_round_prefix_report.md','cross_round_prefix_figures.pdf','同前缀在G0—G3的结果','Same-prefix results across G0, G1, G2 and G3'),
 ('prefix_fnr_20261025','prefix_FNR_report.md','prefix_FNR_figures.pdf','G3正确回答上的误拒补测','Prefix effects on false rejections of correct G3 answers'),
]


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def pdf_tools():
    if str(DEPENDENCY) not in sys.path:sys.path.insert(0,str(DEPENDENCY))
    from pypdf import PdfReader,PdfWriter
    return PdfReader,PdfWriter


def table(head,rows):
    return '\n'.join(['| '+' | '.join(head)+' |','| '+' | '.join(['---']*len(head))+' |']+
        ['| '+' | '.join(map(str,row))+' |' for row in rows])+'\n'


def page_digest(page):
    contents=page.get_contents()
    return hashlib.sha256(contents.get_data() if contents else b'').hexdigest()


def verify_figures(directory=REPORT):
    Reader,_=pdf_tools();manifest=json.loads((directory/'figure_sections.json').read_text())
    merged=Reader(directory/'week_final_figures.pdf');assert len(merged.pages)==manifest['total_pages']
    total=0
    for s in manifest['sections']:
        source=directory/s['source_pdf'];assert sha(source)==s['source_sha256']
        reader=Reader(source);assert len(reader.pages)==s['pages'] and s['start_page']==total+1
        for original,current in zip(reader.pages,merged.pages[total:total+len(reader.pages)]):
            assert page_digest(original)==page_digest(current)
            assert list(original.mediabox)==list(current.mediabox)
        total+=len(reader.pages);assert s['end_page']==total
    assert total==len(merged.pages) and manifest['sections'][0]['pages']==6
    return {'total_pages':total,'sections':len(manifest['sections']),
            'all_source_page_content_streams_and_page_sizes_preserved':True,'original_T_pages':6}


def guide(sections):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig=plt.figure(figsize=(8.27,11.69));fig.patch.set_facecolor('white')
    fig.text(.07,.94,'Week final: figure guide',fontsize=20,weight='bold')
    fig.text(.07,.90,'Original final-T evaluation followed by completed prefix diagnostics',fontsize=10)
    fig.text(.07,.86,'Section',fontsize=11,weight='bold');fig.text(.83,.86,'Pages',fontsize=11,weight='bold')
    for i,s in enumerate(sections):
        y=.815-i*.060
        fig.text(.07,y,s['title_en'],fontsize=10)
        fig.text(.83,y,f'{s["start_page"]}-{s["end_page"]}',fontsize=10)
    fig.text(.07,.13,'Prefix experiments use existing A/C development answers and fixed V0.\n'
        'Conditional TN-to-FP flips, full-error FPR and correct-answer FNR\n'
        'have different denominators; see the accompanying reports.\n'
        'No new generation, verifier scoring or training was performed for this assembly.',fontsize=10,linespacing=1.6)
    fig.savefig(REPORT/'prefix_figures_guide.pdf');plt.close(fig)


def findings(toc):
    return '''# 前缀与表述敏感性：全部既有发现及实验汇总

本报告将八组已完成诊断放到week_final交付中；仅整理旧缓存与图表，没有新增生成、V评分或训练。所有前缀干预均使用固定原V0，不能与最终T中Vrand/Vtarget更新后的结果混为同一实验。旧报告中的“T仍封存”是其运行时的历史状态；目前T已完成最终评价，本次没有对T增加前缀干预。

## 目前能支持什么

- **局部表述敏感性存在。** 保持题目、数学正文及最终答案不变，某些前缀可令原TN变FP；部分前缀也使正确回答的FNR净下降。
- **不同词形、句式与格式差别明显。** 不能把所有前缀概括成相同接受偏移，推理标题和step-by-step类表达有时增加误拒。
- **没有支持“学术词越多越有效”或“越短越有效”的一般机制。** AWL对照无明确组间优势，长度关联受句式、词义及格式影响。
- **没有识别出G主要靠这些开场词利用V、导致自然FPR上涨的机制。** 训练链未呈一致富集；对称删除/替换后，A跨轮差距的大部分保留。同前缀跨G阶段的翻转率也未一致单调上升。

## 先分清三个分母

| 名称 | 分母 | 说明 |
|---|---|---|
| TN→FP注入翻转率 | 注入前已知错误且V拒绝的回答 | 测局部可触发性，不是全池FPR |
| FPR | 该题池/阶段全部已知错误回答 | 既要计原TN变FP，也要计原FP变TN |
| FNR | 该题池/阶段全部已知正确回答 | 既计FN→TP修复，也计TP→FN新增错误 |

未知标签不当错误。A是G反馈训练域，C是反复使用的开发集；同题多个候选的区间按题聚类。不同实验的A64/C48、四阶段A64/C29等队列不能直接相加；逻辑评分中存在完全相同输入复用，也不能把各批评分数相加当成独立样本量。

## 1. 最初EFP触发与SITE输入分层

G3原TN各每题1条，A64/C48，共112条，12设置、1344条评分。we need to使TN→FP翻转A12.50%、C18.75%；逗号+换行为17.19%、20.83%。原文重评分复现拒绝。部分具体前缀相对中性控制通过原检验族的BH校正，支持这些文本干预有影响，不代表万能钥匙。

SITE中A多种题型/符号组的条件FPR上升，但“该组变化−其余题变化”的交互均未通过BH校正，未确认某题型独有退化。固定V参数不变时，也不能将条件风险变化称为V参数能力下降。

[完整EFP/SITE报告](prefix_sources/fpr_patterns_20261024/EFP_SITE_report.md)

## 2. 同义、模态、句式与标点改写

沿用112条原TN，测试46种前缀/控制；另对自然命中we need to的498条G3回答做原位改写。we can、we will、we have to、require等均可影响部分决定，但扩大比较族后，不能据少数较高点位确定最优词。

在自然错误子集中，A原FPR48.41%，改we should为48.76%、we have to为47.35%、require为47.00%；C原38.98%，三者均37.29%。自然改写FPR差异没有通过该实验的BH校正；存在双向翻转，净变化小不代表逐条判断完全稳定。

[完整改写报告与46种设置](prefix_sources/prefix_paraphrase_20261024/paraphrase_report.md)

## 3. AWL词表内外对照

固定同一112条原TN，20组AWL/表外近义动词统一单词+冒号+换行格式。平均翻转率A为AWL2.11%/非AWL2.81%，C为2.81%/3.54%；两池主要检验和40个逐词对比较均未通过BH校正。

不支持所测AWL成员比配对表外词更易触发。AWL成员身份也不是学术性强弱量表；本实验没有排除所有学术语体因素，词义、词频和token长度未完全匹配。

[完整AWL报告](prefix_sources/awl_comparison_20261024/AWL_comparison_report.md)

## 4. 原本判对的自然回答：删除/替换后会不会判错

选择自然命中表达且原V判对的741条TP/TN。删除后，A的TP→FN为5/340=1.47%、TN→FP为5/306=1.63%；C为1/34=2.94%、1/61=1.64%。近义替换也有少量新错误，完整双向案例均保留。

这说明自然表达编辑也存在局部敏感性。因为只选原判对者，原错误率被固定为0；这些条件新错误率不能直接称总体FPR/FNR。删除可能损伤语法，近义表达也不保证每个上下文完全等义。

[自然TP/TN报告与案例](prefix_sources/natural_invariance_20261024/natural_invariance_report.md)

## 5. 长度能否解释偏转

复用缓存，无新评分。主38前缀中，字符数与翻转率Spearman相关A−0.414/C−0.470，但token数仅−0.121/−0.202。we can与Reasoning:同为3token，效果却相差较大；更长的it is essential to也可有较高翻转率。

控制句式类别后趋势减弱，同时重采样题目与前缀的区间跨0；同格式40个单词标题未复现一致强关系。因此有描述性字符长度趋势，尚未识别纯长度因果效应。

[完整长度报告](prefix_sources/prefix_length_20261025/prefix_length_report.md)

## 6. 这些词是否被训练链放大、是否解释自然跨轮差距

在实际511道SFT题的每题等权视图，we need to占比沿“G2候选→V接受→SFT→G3”为15.51%→15.85%→16.24%→14.63%；to solve for为4.31%→4.19%→4.70%→5.92%。未见这些模式在筛选、训练及输出中一致放大；某表达增长本身不证明V筛选驱动。

| A池对称编辑 | G2 FPR | G3 FPR | 跨轮差/百分点 | 比原差缩小/百分点 |
|---|---:|---:|---:|---:|
| 原文 | 50.15% | 57.84% | +7.69 | 0 |
| 删除自然开场 | 50.00% | 57.12% | +7.12 | 0.57，95%区间[−0.24,+1.42] |
| 近义替换 | 50.22% | 57.12% | +6.90 | 0.79，探索性区间[+0.28,+1.36] |

自然差距大部分仍在，不能将缩小量直接叫作训练中介的因果贡献，C也未显示一致对应缩小。另把唯一盒内答案n改为n+1，V拒绝A336/339、C34/34；正文与答案的不一致可能帮助检出，不能推广成自然错误99%检出率或独立重新解题能力。

[三项补充诊断与全部对照](prefix_sources/closing_diagnostics_20261025/supplementary_evidence_report.md)

## 7. 同一前缀在G0/G1/G2/G3上怎么变化

同一A64/C29题，每题每阶段取1条原TN，四阶段372条回答×相同21设置，共7812条评分。18个非控制前缀等权平均翻转率：

| 池 | G0 | G1 | G2 | G3 |
|---|---:|---:|---:|---:|
| A | 5.99% | 8.85% | 9.03% | 8.68% |
| C | 5.36% | 12.84% | 11.49% | 12.84% |

G2→G3的均值差A−0.35个百分点，配对95%区间[−6.25,+5.73]；C+1.34，[−9.58,+12.84]。36个逐前缀/池比较无通过BH者；未见一致单调上涨。G0/G1原生成引擎为Transformers，G2/G3为vLLM，四点不能当作纯训练次数效应。

[全部21种前缀四阶段表及连续评分](prefix_sources/prefix_results_overview_20261026/prefix_results_overview.md) · [统一Excel](prefix_sources/prefix_results_overview_20261026/prefix_results_overview.xlsx)

## 8. 正确回答上的误拒补测

G3全部已知正确且未截断回答A839/C147，21设置，不按原V决定筛选。

| 前缀 | A FNR原→后 | C FNR原→后 |
|---|---:|---:|
| we need to | 7.75%→6.91% | 14.29%→10.88% |
| we will | 7.75%→6.79% | 14.29%→9.52% |
| it is essential to | 7.75%→6.20% | 14.29%→8.84% |
| 逗号+换行 | 7.75%→6.08% | 14.29%→8.16% |

A有10个前缀的FNR下降通过主要BH检验，C有4个。Thought process、Analysis、原step-by-step句在A增加误拒，不能说所有前缀都减少FNR。错误注入子集与正确组题目覆盖不同，不能将两边直接合成全池FPR/准确率；G0/G1/G2正确回答的对应注入轨迹尚未补测，本次不新增。

[完整FNR表、两向翻转和校正结果](prefix_sources/prefix_fnr_20261025/prefix_FNR_report.md)

## 与最终T评价的关系

week_final的T是自然输出评价，统一了四个G的推理引擎；T上G0→G2的V0 FNR上升3.65个百分点、BA下降3.24个百分点，点位区间未跨0，FPR增幅区间跨0。这个方向与部分前缀注入导致的FNR下降不同，进一步说明不能用一个“更容易接受”的表达机制概括所有自然跨轮变化。

现有材料足以收束为“部分局部表达可影响判断，尚未确认为自然漂移的主要来源”。没有完成新的100题×4回答完整面板；本次按安排只整理已有证据，原数据和结果都保留。

## 图册页码与原报告

'''+toc+'''
[打开合并图册](week_final_figures.pdf) · [原T六页图单独保留](week_final_T_figures.pdf) · [返回七天收尾报告](week_final_report.md)
'''


def main():
    Reader,Writer=pdf_tools()
    assert BACKUP.exists(),'Create original delivery backup before integration'
    original=BACKUP/'reports/week_final_20261026';source_records=[]
    for directory in [r[0] for r in CHAPTERS]+['prefix_results_overview_20261026']:
        src=ROOT/'reports'/directory;dest=REPORT/'prefix_sources'/directory
        for p in sorted(src.rglob('*')):
            if not p.is_file():continue
            target=dest/p.relative_to(src);target.parent.mkdir(parents=True,exist_ok=True)
            expected=sha(p);shutil.copy2(p,target);assert sha(target)==expected
            source_records.append({'source':str(p.relative_to(ROOT)),'copy':str(target.relative_to(REPORT)),'sha256':expected})
    shutil.copyfile(original/'week_final_figures.pdf',REPORT/'week_final_T_figures.pdf')
    assert len(Reader(REPORT/'week_final_T_figures.pdf').pages)==6
    sections=[{'title_cn':'原最终T统一评价','title_en':'Final T: original evaluation',
        'source_pdf':'week_final_T_figures.pdf','pages':6,'start_page':1,'end_page':6,'report':'week_final_report.md'},
        {'title_cn':'合并图册目录','title_en':'Figure guide (this page)','source_pdf':'prefix_figures_guide.pdf',
         'pages':1,'start_page':7,'end_page':7,'report':'week_final_figures_index.md'}]
    page=8
    for directory,md,pdf,cn,en in CHAPTERS:
        path=Path('prefix_sources')/directory/pdf;count=len(Reader(REPORT/path).pages)
        sections.append({'title_cn':cn,'title_en':en,'source_pdf':str(path),'pages':count,
            'start_page':page,'end_page':page+count-1,'report':str(Path('prefix_sources')/directory/md)})
        page+=count
    guide(sections)
    writer=Writer()
    for s in sections:
        s['source_sha256']=sha(REPORT/s['source_pdf'])
        writer.append(str(REPORT/s['source_pdf']),outline_item=s['title_cn'],import_outline=False)
    writer.add_metadata({'/Title':'Week final evaluation and prefix diagnostics','/Subject':'Original vector figures; existing experiments only'})
    target=REPORT/'week_final_figures.pdf';temp=target.with_suffix('.tmp.pdf')
    with temp.open('wb') as f:writer.write(f)
    writer.close();temp.replace(target)
    write_json(REPORT/'figure_sections.json',{'at':utc(),'total_pages':page-1,'sections':sections,
        'original_T_first_six_pages':True,'new_inference':False,'merge_library':'pypdf5.9.0',
        'installation':'.tools/pdf_bundle, isolated from scientific environment'})
    verified=verify_figures()
    toc=table(['部分','合并图册页码','原报告'],[[s['title_cn'],f'{s["start_page"]}—{s["end_page"]}',
        '[查看]('+s['report']+')'] for s in sections])
    (REPORT/'week_final_figures_index.md').write_text('# 合并图册目录\n\n页码按PDF实际页序，从1开始。前6页为原T评价，第7页为图册目录，后续为已有前缀诊断。\n\n'+toc,encoding='utf-8')
    (REPORT/'prefix_findings_report.md').write_text(findings(toc),encoding='utf-8')
    maintext=(original/'week_final_report.md').read_text()
    maintext=maintext.replace('[六页图表](week_final_figures.pdf)：','[合并图册](week_final_figures.pdf)：原T部分为第1—6页，')
    maintext+='\n\n## 前缀与表述敏感性：既有实验统一汇总\n\n'+\
        '八组已完成诊断的报告、原图、表格与连续评分已归入本目录，原报告逐字保存。'+\
        '局部前缀敏感性存在，部分前缀同时诱发TN→FP并减少G3正确回答的误拒；AWL、纯长度、G利用开场词导致自然漂移等一般机制仍未确立。'+\
        '同前缀四阶段测试已有完整表，但不是100题×4回答的全体面板。此次仅整理，不新增评分、训练或标签。\n\n'+\
        '[前缀全部发现与实验总报告](prefix_findings_report.md) · '+\
        '[G0—G3同前缀完整表](prefix_sources/prefix_results_overview_20261026/prefix_results_overview.md) · '+\
        '[统一前缀Excel](prefix_sources/prefix_results_overview_20261026/prefix_results_overview.xlsx)\n\n'+toc
    (REPORT/'week_final_report.md').write_text(maintext,encoding='utf-8')
    (REPORT/'README.md').write_text('# 七天实验与前缀诊断统一交付\n\n'+
        '- [七天收尾主报告](week_final_report.md)\n- [前缀全部发现与实验总报告](prefix_findings_report.md)\n'+
        '- [合并图册](week_final_figures.pdf)：'+str(page-1)+'页，含原T六页及此前八组诊断；[页码目录](week_final_figures_index.md)。\n'+
        '- [前缀统一Excel](prefix_sources/prefix_results_overview_20261026/prefix_results_overview.xlsx)\n'+
        '- [T指标Excel](week_final_metrics.xlsx) · [V理由与G步骤审查表](week_review.xlsx)\n'+
        '- 原前缀报告、图表和表格的完整副本在prefix_sources/；原T六页图单独保留为week_final_T_figures.pdf。\n\n'+
        'T已完成首次最终评价；前缀附录使用此前A/C开发诊断，不含新T干预。原始模型和缓存保留，人工复核仍待做。'+
        '整合前的周报、图册及归档备份在runs/week_final_20261026/delivery_before_prefix_integration。\n',encoding='utf-8')
    for r in source_records:
        assert sha(ROOT/r['source'])==r['sha256'] and sha(REPORT/r['copy'])==r['sha256']
    write_json(REPORT/'prefix_integration_audit.json',{'at':utc(),'status':'passed',**verified,
        'source_reports':8,'source_copies':len(source_records),'source_files_unchanged':True,
        'new_G_generation':0,'new_V_scores':0,'new_training_steps':0,'source_files':source_records,
        'prior_delivery_backup':str(BACKUP.relative_to(ROOT)),'merged_pdf_sha256':sha(target)})
    shutil.copyfile(Path(__file__),REPORT/'week_prefix_delivery.py')
    from .week_final_report import package_delivery
    package_delivery(True)
    print(json.dumps({'integrated':True,**verified,'source_files_copied':len(source_records)},ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()
