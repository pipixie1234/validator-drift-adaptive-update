from collections import Counter
import math
from .core import *

def ratio(n,d):return n/d if d else None

def metrics(rows):
    tp=sum(r['correct'] and r['accepted'] for r in rows);tn=sum(not r['correct'] and not r['accepted'] for r in rows);fp=sum(not r['correct'] and r['accepted'] for r in rows);fn=sum(r['correct'] and not r['accepted'] for r in rows)
    return {'n':len(rows),'correct':tp+fn,'incorrect':tn+fp,'TP':tp,'TN':tn,'FP':fp,'FN':fn,'G_accuracy':ratio(tp+fn,len(rows)),'V_accuracy':ratio(tp+tn,len(rows)),'false_accept_rate':ratio(fp,tn+fp),'false_reject_rate':ratio(fn,tp+fn),'accepted_error_fraction':ratio(fp,tp+fp),'acceptance_rate':ratio(tp+fp,len(rows)),'balanced_accuracy':None if not (tp+fn and tn+fp) else .5*(tp/(tp+fn)+tn/(tn+fp))}

def fmt(x):return '未定义' if x is None else f'{100*x:.2f}%'

def main():
    import numpy as np
    from scipy.stats import rankdata
    generated=read_jsonl(ROOT/'runs/day1/G0_C50.jsonl');scores=read_jsonl(ROOT/'runs/day1/V0_G0_C50.jsonl');gold={x['question_id']:x['gold_answer'] for x in read_jsonl(ROOT/'data/labels/C.jsonl')};byid={x['candidate_id']:x for x in scores}
    assert len(generated)==CFG['day1_questions']*CFG['generation']['num_return_sequences']
    assert len(byid)==len(generated)
    rows=[];joined=[]
    for g in generated:
        s=byid[g['candidate_id']];correct=None if g['verification_status']!='verified_numeric' else g['extracted_answer']==gold[g['question_id']]
        item={**g,**s,'correct':correct,'gold_answer':gold[g['question_id']]};joined.append(item)
        if correct is not None and s['accepted'] is not None:rows.append(item)
    m=metrics(rows);status=dict(Counter(g['verification_status'] for g in generated));m['independently_wrong_question_count']=len({r['question_id'] for r in rows if not r['correct']});m['total_candidates']=len(generated);m['verifiable_coverage']=len(rows)/len(generated);m['format_status_counts']=status;m['truncated_count']=sum(g['truncated'] for g in generated);m['format_compliant_count']=sum(g['format_compliant'] for g in generated);m['answer_format_counts']=dict(Counter(g['answer_format'] or 'unparsed' for g in generated));m['strict_hash_only_metrics']=metrics([r for r in rows if r['format_compliant']]);m['protocol_deviation']='G did not follow required #### format; independently verifiable terminal numeric boxes are an explicitly reported C diagnostic extension.'
    positives=sum(r['correct'] for r in rows);negatives=len(rows)-positives
    if positives and negatives:
        ranks=rankdata([r['score'] for r in rows]);m['auroc']=(sum(r for r,x in zip(ranks,rows) if x['correct'])-positives*(positives+1)/2)/(positives*negatives)
    else:m['auroc']=None
    # Paired question clusters: include all 4 responses of each sampled question, including failures.
    rng=np.random.default_rng(CFG['seed']);qids=list(dict.fromkeys(g['question_id'] for g in generated));groups={q:[r for r in rows if r['question_id']==q] for q in qids};intervals={k:[] for k in ['G_accuracy','false_accept_rate','false_reject_rate','accepted_error_fraction','acceptance_rate']}
    for _ in range(1000):
        b=metrics([r for q in rng.choice(qids,len(qids),replace=True) for r in groups[q]])
        for k in intervals:
            if b[k] is not None:intervals[k].append(b[k])
    m['bootstrap_valid_replicates']={k:len(v) for k,v in intervals.items()}
    m['question_cluster_bootstrap_95ci']={k:np.quantile(v,[.025,.975]).tolist() if v else None for k,v in intervals.items()}
    m['output_length_tokens']={'min':min(r['output_tokens'] for r in generated),'median':float(np.median([r['output_tokens'] for r in generated])),'p95':float(np.quantile([r['output_tokens'] for r in generated],.95)),'max':max(r['output_tokens'] for r in generated),'mean':float(np.mean([r['output_tokens'] for r in generated]))}
    m['operational_G_accuracy_format_failure_as_wrong']=sum(r['correct'] is True and r['format_compliant'] for r in joined)/len(joined)
    m['G_accuracy_unverifiable_as_wrong']=sum(r['correct'] is True for r in joined)/len(joined)
    m['operational_acceptance_all_scored']=ratio(sum(r['accepted'] is True for r in joined),sum(r['accepted'] is not None for r in joined))
    write_json(ROOT/'reports/day1_metrics.json',m)
    write_jsonl(ROOT/'runs/day1/independent_labels_C50.jsonl',[{k:r[k] for k in ['question_id','candidate_id','verification_status','extracted_answer','correct','gold_answer']} for r in joined])
    chosen=[];seen=set()
    buckets=[('误接受',lambda r:r['correct'] is False and r['accepted']),('误拒绝',lambda r:r['correct'] is True and not r['accepted']),('自然错误且拒绝',lambda r:r['correct'] is False and not r['accepted']),('正确且接受',lambda r:r['correct'] is True and r['accepted']),('无法核验',lambda r:r['correct'] is None)]
    for category,condition in buckets:
        for r in [x for x in joined if condition(x)][:6]:
            chosen.append((category,r));seen.add(r['candidate_id'])
    for r in joined:
        if len(chosen)>=24:break
        if r['candidate_id'] not in seen:chosen.append(('补充样本',r));seen.add(r['candidate_id'])
    chosen=chosen[:24]
    cases=['# 第一日 C 开发集案例\n','所有标签仅核验最终数值；不证明推理步骤正确。诊断仅为假设，未用于改动 T。\n']
    for i,(category,r) in enumerate(chosen,1):
        cases += [f"## {i}. {category} — {r['candidate_id']}\n",f"题目：{r['question']}\n",f"候选回答：\n\n```text\n{r['text']}\n```\n",f"核验：提取={r['extracted_answer']}；标准最终答案={r['gold_answer']}；状态={r['verification_status']}；正确={r['correct']}。V0 接受={r['accepted']}，代理分数={r['score']}。\n",'诊断假设：需人工核对候选计算、问题理解和验证器独立计算；仅凭分数无法归因内部机制。\n']
    (ROOT/'reports/day1_cases.md').write_text('\n'.join(cases))
    gp=json.loads((ROOT/'runs/day1/G0_C50_performance.json').read_text());vp=json.loads((ROOT/'runs/day1/V0_G0_C50_performance.json').read_text());gs=json.loads((ROOT/'runs/day1/smoke_G_report.json').read_text());vs=json.loads((ROOT/'runs/day1/smoke_V_report.json').read_text())
    bpool=read_jsonl(ROOT/'runs/day1/G0_B_smoke.jsonl');bgold={x['question_id']:x['gold_answer'] for x in read_jsonl(ROOT/'data/labels/B.jsonl')};bvalid=[x for x in bpool if x['verification_status']=='verified_numeric'];bcounts={'A':sum(x['extracted_answer']==bgold[x['question_id']] for x in bvalid),'B':sum(x['extracted_answer']!=bgold[x['question_id']] for x in bvalid)};bquestions=len({x['question_id'] for x in bpool})
    # Sum saved per-question measurements; robust to completed/resumed invocations.
    qtime={r['question_id']:r['question_generation_seconds'] for r in generated};gtime=sum(qtime.values());vtime=sum(r['seconds'] for r in scores)
    estimate={'full_G_candidates':12000,'full_V_candidate_scores':19200,'G_hours':gtime/len(generated)*12000/3600,'V_hours':vtime/len(scores)*19200/3600,'G_training_hours_linear_1024_examples':gs['training_seconds']/16*1024/3600,'V_training_hours_linear_1536_examples':vs['training_seconds']/16*1536/3600,'caveat':'Linear extrapolation from C and two-step smoke; does not include preprocessing, loads, disk, variable output lengths or contention. Formal run must recheck per-stage budget.'}
    estimate['linear_total_hours']=sum(estimate[k] for k in ['G_hours','V_hours','G_training_hours_linear_1024_examples','V_training_hours_linear_1536_examples']);write_json(ROOT/'reports/budget_estimate.json',estimate)
    protocol={'config_sha256':digest(CFG),'G_system':G_SYSTEM,'G_full_prompt_template':messages('G','{question}'),'V_system':V_SYSTEM,'labels':CFG['verifier']['labels'],'score_rule':'sum conditional token log likelihood over full A/B continuation, no EOS; normalize the two scores with softmax; A >= B accepted','threshold':0.5,'format_rule':'Strict #### numeric line plus explicit C diagnostic extension for a unique terminal numeric boxed answer; compliance reported separately; exact Fraction equality; truncated responses never count as verified natural errors','T_used_for_generation_scoring_training_or_selection':False,'day1_scope_only':True};write_json(ROOT/'reports/protocol_day1.json',protocol)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,ax=plt.subplots(figsize=(5,4));a=np.array([[m['TP'],m['FN']],[m['FP'],m['TN']]]);ax.imshow(a,cmap='Blues');ax.set_xticks([0,1],['Accept','Reject']);ax.set_yticks([0,1],['Correct','Incorrect'])
    for (y,x),value in np.ndenumerate(a):ax.text(x,y,str(value),ha='center',va='center')
    ax.set_title('C development set: G0 / V0');fig.tight_layout();fig.savefig(ROOT/'reports/day1_confusion.png',dpi=160);plt.close(fig)
    lines=['# 第一天实验报告（2026-10-20）','', '完成范围：环境与模型核验、GSM8K 固定划分、C 集 50 题×4 候选、V0 条件似然评分、G/V 各 2 步 LoRA 冒烟训练。正式 G1/G2 与随机/定向 V 更新尚未开展。T 仅预先划分保存，未用于推理、诊断或训练。','',f"候选共 {len(generated)} 条，可独立核验 {len(rows)} 条；格式状态：{status}。超长截断 {m['truncated_count']} 条。",'', '| 指标（可核验且完成评分子集） | 结果 | 分母 |','|---|---:|---:|',f"| G 最终答案正确率 | {fmt(m['G_accuracy'])} | {m['n']} |",f"| 错误被接受比例 | {fmt(m['false_accept_rate'])} | {m['incorrect']} 个自然错误 |",f"| 正确被拒绝比例 | {fmt(m['false_reject_rate'])} | {m['correct']} 个正确回答 |",f"| 接受集错误比例 | {fmt(m['accepted_error_fraction'])} | {m['TP']+m['FP']} 个接受样本 |",f"| 保留率 | {fmt(m['acceptance_rate'])} | {m['n']} |",f"| V 总准确率 | {fmt(m['V_accuracy'])} | {m['n']} |",f"| V 平衡准确率 | {fmt(m['balanced_accuracy'])} | 两类分别计算后平均 |",'',f"TP={m['TP']}，FN={m['FN']}，FP={m['FP']}，TN={m['TN']}；AUROC={m['auroc']}。不能将只判正确的高总体准确率当作错误识别有效的证据。",'',f"严格操作性 G 正确率（不合规 #### 格式计错）：{fmt(m['operational_G_accuracy_format_failure_as_wrong'])}。盒式诊断扩展下、仅不可核验回答计错的 G 正确率：{fmt(m['G_accuracy_unverifiable_as_wrong'])}。可核验覆盖率：{fmt(m['verifiable_coverage'])}。完整输出上的 V 接受率：{fmt(m['operational_acceptance_all_scored'])}。",'', f"格式限制：要求 G 以 #### 结束，但 C 上三次格式诊断仍主要使用盒式答案。本次独立核验加入严格的唯一末尾数值盒解析；#### 合规 {m['format_compliant_count']}/{len(generated)}，格式分布 {m['answer_format_counts']}。这是已披露的开发期协议扩展，不能声称原定严格格式协议通过。严格 #### 子集指标另存 strict_hash_only_metrics。原始诊断输出全部保留。",'', '问题聚类 bootstrap（1000 次）的开发集区间保存在 day1_metrics.json。无正例/负例或无接受回答导致指标未定义的重复不会纳入对应区间；有效重复数量单独记录。该区间不覆盖训练随机性，不是最终测试成绩。','',f"生成累计耗时 {gtime:.1f} 秒，{sum(x['output_tokens'] for x in generated)/gtime:.1f} 输出 token/s；平均长度 {sum(x['output_tokens'] for x in generated)/len(generated):.1f} token。生成峰值 allocated={gp['peak_allocated_bytes']/2**30:.2f} GiB。",f"V 评分累计耗时 {vtime:.1f} 秒，{len(scores)/vtime:.2f} 回答/s；评分峰值 allocated={vp['peak_allocated_bytes']/2**30:.2f} GiB。",'',f"G/V 短训练：有限 loss、非零梯度、仅 LoRA 参数更新、基座无梯度且版本计数不变、prompt/padding 掩码、保存重载全部通过。耗时分别 {gs['training_seconds']:.1f}/{vs['training_seconds']:.1f} 秒；峰值 allocated {gs['peak_allocated_bytes']/2**30:.2f}/{vs['peak_allocated_bytes']/2**30:.2f} GiB。",f"短训练 V 标签计数：{vs['label_counts']}。这些 adapter 只用于工程检查，不命名为正式 G1 或 Vrand/Vtarget，也不能据此声称训练改善。", f"B 起始样本只有可核验正例，因此按固定前缀扩到最多24题。本次实际 {bquestions} 题、{len(bvalid)} 条可核验回答，独立标签计数 {bcounts}。原始仅正例短训练保存在 smoke_V_positive_only_*，扩样与标签覆盖详情见 V_smoke_class_coverage.json；V 的 B 标签训练覆盖={vs['label_counts'].get('B',0)>0}。",'',f"按当前实测线性推算，一周完整范围约 {estimate['linear_total_hours']:.2f} GPU 工作小时（详见 budget_estimate.json）；存在加载、输出长度与训练长度差异，后续每阶段仍需复核，不自动扩展任务。",'', '可核验子集可能因格式与题目难度相关而存在选择偏差，子集正确率不能自动推广到所有输出。', '', '当前结论仅涉及协议可运行性与 V0 在 C 开发集的表现。还没有跨轮轨迹，无法判断可靠性漂移或定向更新是否有效。最终答案正确不证明推理过程正确，代理分数不等于校准概率。','', '安装采用项目 venv 并复用系统 Torch，应用依赖先经阿里云、测速后切换为华为云国内镜像；完整版本在 requirements-lock.txt，系统继承关系和 GPU/驱动/runtime/Toolkit 分别记录在 environment.json。网络测速在 network.json；镜像比较在 network_mirror_comparison.json；安装日志在 runs/day1/install.log、install_huawei.log 和 install_huawei_local.log。','', '工程检查记录：首次运行 24 项检查中 1 项发现 Unicode 行分隔符读取缺陷；修复后 25 项全部通过；盒式解析扩展后新增边界检查并再次通过最终39项检查。原始失败日志保存在 runs/day1/tests_initial_failure.log；未用该缺陷运行生成或训练。','', '下一天：先解决或正式约定盒式输出的协议地位，复核自然错误数量与 V0 混淆矩阵，冻结正式协议后按固定 V0 反馈开始 G0→G1→G2。当前请求只执行第一天，未启动正式跨轮训练。']
    (ROOT/'reports/day1_report.md').write_text('\n'.join(lines)+'\n')
    write_json(ROOT/'runs/day1/state.json',{'status':'complete','scope':'day1','candidate_count':len(generated),'V_scores':len(scores),'G_smoke':True,'V_smoke':True,'V_negative_label_training_verified':vs['label_counts'].get('B',0)>0,'formal_rounds_started':False,'T_used':False,'strict_format_compliant_count':m['format_compliant_count'],'formal_protocol_requires_review':True})
    print(json.dumps(m,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()
