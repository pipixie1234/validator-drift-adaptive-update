"""Descriptive task diagnosis, independent labels, question-cluster uncertainty."""
import json
from collections import Counter,defaultdict
from .core import ROOT,read_jsonl,write_json,write_jsonl,digest
from .day2_protocol import label_response,VERSION

def div(x,y):return x/y if y else None

def stats(rows):
    labelled=[r for r in rows if r['correct'] is not None]
    scored=[r for r in labelled if r.get('accepted') is not None]
    c=sum(r['correct'] for r in labelled);w=len(labelled)-c
    vc=sum(r['correct'] for r in scored);vw=len(scored)-vc
    tp=sum(r['correct'] and r['accepted'] for r in scored);fn=vc-tp
    fp=sum(not r['correct'] and r['accepted'] for r in scored);tn=vw-fp
    wrong=Counter(r['question_id'] for r in labelled if not r['correct'])
    qs={r['question_id'] for r in rows};verified_counts=Counter(r['question_id'] for r in labelled)
    return {'questions':len(qs),'total_candidates':len(rows),'verified':len(labelled),'unknown':len(rows)-len(labelled),'V_scored_verified':len(scored),'V_unscored_verified':len(labelled)-len(scored),'verifiable_coverage':div(len(labelled),len(rows)),'correct':c,'natural_errors':w,'G_accuracy_verified':div(c,len(labelled)),'G_correctness_lower_bound_all_outputs':div(c,len(rows)),'G_correctness_upper_bound_all_outputs':div(c+len(rows)-len(labelled),len(rows)),'TP':tp,'FN':fn,'FP':fp,'TN':tn,'false_accept_rate':div(fp,vw),'false_reject_rate':div(fn,vc),'accepted_error_fraction':div(fp,tp+fp),'acceptance_rate_verified':div(tp+fp,len(scored)),'acceptance_rate_all_scored':div(sum(r.get('accepted') is True for r in rows),sum(r.get('accepted') is not None for r in rows)),'V_accuracy':div(tp+tn,len(scored)),'all_accept_baseline_accuracy':div(vc,len(scored)),'balanced_accuracy':(tp/vc+tn/vw)/2 if vc and vw else None,'error_questions':len(wrong),'error_question_fraction':div(len(wrong),len(qs)),'question_error_histogram':dict(Counter(wrong.get(q,0) for q in qs)),'max_errors_per_question':max(wrong.values(),default=0),'largest_5_error_question_share':div(sum(sorted(wrong.values(),reverse=True)[:5]),w),'fully_verified_questions':sum(verified_counts[q]==4 for q in qs),'questions_with_unknowns':sum(verified_counts[q]<4 for q in qs)}

def confidence(rows,task,reps=1000):
    import numpy as np
    rng=np.random.default_rng(42);groups=defaultdict(list)
    for r in rows:groups[r['question_id']].append(r)
    strata=defaultdict(list)
    for q,rs in groups.items():strata[(rs[0].get('type'),rs[0].get('level')) if task=='math' else ('all',)].append(q)
    names=['G_accuracy_verified','false_accept_rate','false_reject_rate','accepted_error_fraction','acceptance_rate_verified','error_question_fraction'];values={k:[] for k in names}
    for _ in range(reps):
        # Unique bootstrap cluster ids preserve multiplicity in question-level metrics.
        sampled=[];j=0
        for qs in strata.values():
            for q in rng.choice(qs,len(qs),replace=True):
                sampled.extend([{**r,'question_id':f'bootstrap:{j}'} for r in groups[q]]);j+=1
        m=stats(sampled)
        for k in names:
            if m[k] is not None:values[k].append(m[k])
    return {'method':'stratified question-cluster bootstrap by type/level' if task=='math' else 'question-cluster bootstrap','replicates':reps,'ci95':{k:np.quantile(v,[.025,.975]).tolist() if v else None for k,v in values.items()},'valid_replicates':{k:len(v) for k,v in values.items()}}

def evaluate(name):
    import numpy as np
    from scipy.stats import rankdata
    g=read_jsonl(ROOT/f'runs/day2/G0_{name}.jsonl');v=read_jsonl(ROOT/f'runs/day2/V0_{name}.jsonl');expected=400 if name=='gsm8k_C100' else 280
    assert len(g)==len(v)==expected and len({r['candidate_id'] for r in g})==expected
    byid={r['candidate_id']:r for r in v};gold={r['question_id']:r for r in read_jsonl(ROOT/f'data/day2/labels/{name}.jsonl')};joined=[]
    for i,r in enumerate(g):
        answer=gold[r['question_id']]['gold_answer'];lab=label_response(r['text'],r['truncated'],answer,r['task']) if answer is not None else {'status':'reference_unresolved','value':None,'correct':None,'comparison':'reference_unresolved','format':None}
        row={**r,**byid[r['candidate_id']],'G_model_fingerprint':r['model_fingerprint'],'V_model_fingerprint':byid[r['candidate_id']]['model_fingerprint'],'gold_answer':answer,'correct':lab['correct'],'verification_status':lab['status'],'extracted_answer':lab['value'],'comparison':lab['comparison'],'answer_format':lab.get('format')};joined.append(row)
        if (i+1)%40==0:print(f'Independent labels {name}: {i+1}/{len(g)}',flush=True)
    m=stats(joined);m['verification_status_counts']=dict(Counter(r['verification_status'] for r in joined));m['truncated_count']=sum(r['truncated'] for r in joined);m['answer_format_counts']=dict(Counter(r['answer_format'] or 'none' for r in joined));m['length_tokens']={k:float(fn([r['output_tokens'] for r in joined])) for k,fn in [('mean',np.mean),('median',np.median),('max',np.max),('p95',lambda x:np.quantile(x,.95))]}
    eligible=[r for r in joined if r['correct'] is not None];p=sum(r['correct'] for r in eligible);n=len(eligible)-p
    m['auroc']=float((sum(rank for rank,r in zip(rankdata([r['score'] for r in eligible]),eligible) if r['correct'])-p*(p+1)/2)/(p*n)) if p and n else None
    m['uncertainty']=confidence(joined,'math' if name=='math70' else 'gsm8k')
    m['length_by_label']={label:{'n':len(rs),'mean':float(np.mean([r['output_tokens'] for r in rs])) if rs else None} for label,rs in [('correct',[r for r in joined if r['correct'] is True]),('wrong',[r for r in joined if r['correct'] is False]),('unknown',[r for r in joined if r['correct'] is None])]}
    m['errors_by_question']=[{'question_id':q,'errors':n,'type':next(r.get('type') for r in joined if r['question_id']==q),'level':next(r.get('level') for r in joined if r['question_id']==q)} for q,n in Counter(r['question_id'] for r in joined if r['correct'] is False).most_common()]
    if name=='gsm8k_C100':
        ids=[r['question_id'] for r in read_jsonl(ROOT/'data/day2/inputs/gsm8k_C100.jsonl')];m['first_50_under_day2_protocol']=stats([r for r in joined if r['question_id'] in set(ids[:50])]);m['remaining_50_under_day2_protocol']=stats([r for r in joined if r['question_id'] in set(ids[50:])])
    else:
        m['by_level']={level:stats([r for r in joined if r.get('level')==level]) for level in sorted({r['level'] for r in joined})};m['by_type']={kind:stats([r for r in joined if r.get('type')==kind]) for kind in sorted({r['type'] for r in joined})}
    write_jsonl(ROOT/f'runs/day2/labels_{name}.jsonl',[{k:r[k] for k in ['question_id','candidate_id','gold_answer','correct','verification_status','extracted_answer','comparison','answer_format']} for r in joined]);write_json(ROOT/f'reports/day2/{name}_metrics.json',m)
    return joined,m

def pct(x):return '未定义' if x is None else f'{100*x:.2f}%'

def main():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    cfg=json.loads((ROOT/'configs/day2.json').read_text());results={};metrics={}
    for name in ['gsm8k_C100','math70']:results[name],metrics[name]=evaluate(name)
    gsm=metrics['gsm8k_C100'];math=metrics['math70'];gate=cfg['task_selection_rule'];feasible=math['verifiable_coverage']>=gate['math_min_verifiable_coverage'] and math['natural_errors']>=gate['math_min_natural_errors'] and math['error_questions']>=gate['math_min_error_questions'];easy=(1-gsm['G_accuracy_verified'])<gate['prefer_math_when_gsm8k_error_rate_below'] or gsm['error_questions']<gate['prefer_math_when_gsm8k_error_questions_below']
    primary='MATH' if feasible and easy else 'GSM8K' if not easy else None
    decision={'pre_registered_rule':gate,'evidence':{name:{k:metrics[name][k] for k in ['verified','unknown','verifiable_coverage','natural_errors','error_questions','G_accuracy_verified','false_accept_rate','false_reject_rate']} for name in metrics},'MATH_feasibility_passed':feasible,'GSM8K_easy_or_error_clusters_sparse':easy,'primary_task':primary,'simple_control':'GSM8K' if primary=='MATH' else None,'protocol_frozen_for_next_stage':primary is not None,'scope':'Task and inference/labeling protocol only; no formal split allocation or training trajectory started','G0_fingerprint':results['gsm8k_C100'][0]['G_model_fingerprint'],'V0_fingerprint':results['gsm8k_C100'][0]['V_model_fingerprint'],'original_model_paths':{'G':cfg['G_MODEL_DIR'],'V':cfg['V_MODEL_DIR']},'inference_config':cfg,'answer_protocol_version':VERSION,'score_implementation_sha256':__import__('hashlib').sha256((ROOT/'src/pilot/runtime.py').read_bytes()).hexdigest(),'generation_implementation_sha256':__import__('hashlib').sha256((ROOT/'src/pilot/day2_run.py').read_bytes()).hexdigest(),'parser_sha256':__import__('hashlib').sha256((ROOT/'src/pilot/day2_protocol.py').read_bytes()).hexdigest(),'T_evaluated':False,'MATH_test_downloaded':False,'all_current_math70_reserved_for_development':True,'reason':'Use MATH when it has enough independently verified errors distributed across questions and GSM8K has sparse errors; otherwise retain GSM8K if informative, or defer formal training when neither passes.'}
    write_json(ROOT/'reports/day2/task_decision.json',decision)
    if primary:write_json(ROOT/'configs/formal_protocol.json',decision)
    projection={}
    for name in metrics:
        gp=json.loads((ROOT/f'runs/day2/G_{name}_performance.json').read_text());vp=json.loads((ROOT/f'runs/day2/V_{name}_performance.json').read_text());count=metrics[name]['total_candidates'];projection[name]={'assumed_G_candidates':12000,'assumed_V_scores':19200,'G_inference_loop_hours':gp['loop_wall_seconds_this_invocation']/count*12000/3600,'V_scoring_loop_hours':vp['loop_wall_seconds_this_invocation']/count*19200/3600,'note':'Reference to original weekly sample budget only; excludes training, process/model loads and G1/G2 length changes. Formal allocations not created.'}
    write_json(ROOT/'reports/day2/budget_projection.json',projection)
    old=json.loads((ROOT/'reports/day2/day1_50_failed_audit_summary.json').read_text());rows=['# 第二天：协议修正与主任务诊断','', '研究目标保持：验证器在生成器参数更新后输出上的可靠跨轮泛化。本次仅诊断任务与冻结下一阶段协议，未开始 G1/G2 或 V 更新。','', '第一天原50题的200条输出和V0分数保持原样；重审只改变独立标签解析。新的公平对照为同一G0/V0、同一盒式提示、相同采样参数与1536 token上限下重新生成完整GSM8K C100和预抽样MATH训练70题，每题4个候选。新旧提示、长度和V提示均有差异，不能将新旧G表现变化归因为单一因素。','', '## 原50条不可核验回答审计','', f"旧可核验组：{old['original_verified']['correct']}/{old['original_verified']['verified']}，正确率{pct(old['original_verified']['accuracy_on_verified'])}；新恢复组：{old['old_50_failed_reaudited']['correct']}/{old['old_50_failed_reaudited']['verified']}，正确率{pct(old['old_50_failed_reaudited']['accuracy_on_verified'])}。合并：{old['all_200_reaudited']['correct']}/{old['all_200_reaudited']['verified']}，{pct(old['all_200_reaudited']['accuracy_on_verified'])}。", '该批完整200条输出即使将剩余2条都算错，已核验正确数188仍给出94%的下界；都算对则为95%。未显示原规则通过排除低正确率回答明显抬高结果；仍有2条未定，不能推广为不存在任何选择偏差。逐条原文、标签和状态见 day1_50_failed_audit.md。','', '## 同一协议下的任务比较','', '| 指标 | GSM8K C100 | MATH train70 |','|---|---:|---:|']
    for label,key,percent in [('可核验覆盖率','verifiable_coverage',True),('G正确率（可核验子集）','G_accuracy_verified',True),('自然错误回答数','natural_errors',False),('出现错误的独立题目数','error_questions',False),('出现错误题目占比','error_question_fraction',True),('V误接受率（错误中接受）','false_accept_rate',True),('V误拒绝率（正确中拒绝）','false_reject_rate',True),('接受集错误比例','accepted_error_fraction',True),('V准确率','V_accuracy',True),('全部接受基线准确率','all_accept_baseline_accuracy',True),('V平衡准确率','balanced_accuracy',True),('截断回答数','truncated_count',False)]:rows.append(f"| {label} | {pct(gsm[key]) if percent else gsm[key]} | {pct(math[key]) if percent else math[key]} |")
    for task_name in metrics:
        perf=json.loads((ROOT/f'runs/day2/G_{task_name}_performance.json').read_text());vperf=json.loads((ROOT/f'runs/day2/V_{task_name}_performance.json').read_text());qtime={r['question_id']:r['generation_seconds_for_question'] for r in results[task_name]};metrics[task_name]['performance']={'generation_model_seconds':sum(qtime.values()),'generation_loop_wall_seconds':perf['loop_wall_seconds_this_invocation'],'generation_model_load_seconds':perf['model_load_seconds'],'generation_peak_allocated_GiB':perf['peak_allocated_bytes']/2**30,'V_loop_wall_seconds':vperf['loop_wall_seconds_this_invocation'],'V_model_load_seconds':vperf['model_load_seconds'],'V_peak_allocated_GiB':vperf['peak_allocated_bytes']/2**30};write_json(ROOT/f'reports/day2/{task_name}_metrics.json',metrics[task_name])
    rows+=['',f"阶段墙钟、模型加载与峰值 allocated 显存：GSM8K {gsm['performance']}；MATH {math['performance']}。",'',f"GSM8K 输出长度均值/中位数/p95：{gsm['length_tokens']}；MATH：{math['length_tokens']}。",f"完整输出正确率识别区间（未知可全错或全对）：GSM8K [{pct(gsm['G_correctness_lower_bound_all_outputs'])}, {pct(gsm['G_correctness_upper_bound_all_outputs'])}]，MATH [{pct(math['G_correctness_lower_bound_all_outputs'])}, {pct(math['G_correctness_upper_bound_all_outputs'])}]。",'', '## 错误是否分散','',f"GSM8K自然错误分布：{gsm['question_error_histogram']}；MATH：{math['question_error_histogram']}。键为每题4候选中已核验错误数，值为题目数；有未知候选的题目另计在各指标JSON中。",f"错误最多5题占全部错误的比例：GSM8K {pct(gsm['largest_5_error_question_share'])}，MATH {pct(math['largest_5_error_question_share'])}。不能把同题多个错误视为独立题目证据。",'', '问题聚类bootstrap区间见两个metrics.json；MATH按题型×难度分层重抽，保持每层样本量。小层只有2题，区间不覆盖训练随机性、数据污染或人工标注误差。不同任务之间未做按题配对的因果比较。','', '## 主任务与冻结决定','',f"主任务：{primary if primary else '暂不冻结：尚未满足预先设定的可核验覆盖与错误分布条件'}；MATH可行性门槛通过={feasible}；GSM8K简单或错误簇稀少={easy}。", '判断门槛在生成前记录于 configs/day2.json，不在看到结果后挑选题目或搜索最优阈值。若选MATH，GSM8K保留为简单任务对照；这只改变实验载体，研究目标不变。冻结对象和边界见 task_decision.json / configs/formal_protocol.json；本次70道MATH题全部作为开发题，未来不得加入正式训练池或最终测试。','', '## 协议与限制','', 'G使用数学模型原生盒式提示。候选只解析显式盒/####，允许不引入冲突的单位与描述尾缀；多盒只有数值/符号等价才可接受。截断、冲突、未知表达式及无法证明等价者保持未定。GSM8K用精确有理数；MATH用受限LaTeX Lark解析和精确符号差，未执行任何生成代码。当前区间并集、矩阵等未支持形式会降低覆盖率，不能算作自然错误。','', 'V只接收题目和原始候选，不接收标准解、题型难度、生成来源或独立标签。A/B条件似然求和再归一化，阈值固定0.5；分数是未校准代理值。不能只凭高总体准确率证明错误识别有效。','', 'MATH来自固定revision的训练部分，按7题型×5难度每层2题分层抽样；在抽样前排除需要图像、难度未知及重复题，未按答案或生成表现过滤。这是文本可解、均衡分层的小样本，不能声称代表MATH总体自然分布。训练集可能出现在预训练或后训练语料中，本次结果不能当作无污染泛化成绩。','', 'GSM8K T保持封存；没有下载或使用MATH test。没有更换G/V，也没有使用第一天smoke adapter。原始模型、第一天输出和旧标签未覆盖。','', '数据来源：https://huggingface.co/datasets/EleutherAI/hendrycks_math ；解析依据：https://docs.sympy.org/latest/modules/parsing.html#experimental-latex-parsing 。','', '## 后续','', '基于冻结决定准备互斥的正式G更新池、V更新池和最终评价集；当前开发题不得流入它们。正式开始前重跑短LoRA检查（含自然正负标签），统一训练长度与token预算，并沿原G谱系进行两次真实参数更新。若任务门槛未通过，保留诊断并先补协议支持，不启动缺乏可辨识性的正式训练。']
    (ROOT/'reports/day2/day2_report.md').write_text('\n'.join(rows)+'\n')
    # Representative cases: cap per task, including correct, errors and unresolved; no alteration of evaluation sample.
    case_lines=['# 第二天可读案例','']
    for name,rs in results.items():
        selected=[];seen=set()
        for label,condition in [('误接受',lambda r:r['correct'] is False and r['accepted']),('误拒绝',lambda r:r['correct'] is True and not r['accepted']),('正确且接受',lambda r:r['correct'] is True and r['accepted']),('未定',lambda r:r['correct'] is None)]:
            for r in [r for r in rs if condition(r)][:3]:selected.append((label,r));seen.add(r['candidate_id'])
        for label,r in selected[:12]:case_lines.extend([f"## {name} / {label} / {r['candidate_id']}",r['question'],f"```text\n{r['text']}\n```",f"提取={r['extracted_answer']}；标准最终答案={r['gold_answer']}；核验={r['correct']}；方法={r['comparison']}；V接受={r['accepted']}；代理分数={r['score']}。",'标签只覆盖最终答案，不证明推理步骤或V内部机制。',''])
    (ROOT/'reports/day2/day2_cases.md').write_text('\n'.join(case_lines))
    fig,axes=plt.subplots(1,2,figsize=(9,4))
    for ax,(name,m) in zip(axes,metrics.items()):
        array=np.array([[m['TP'],m['FN']],[m['FP'],m['TN']]]);ax.imshow(array,cmap='Blues');ax.set_xticks([0,1],['Accept','Reject']);ax.set_yticks([0,1],['Correct','Incorrect']);ax.set_title(name)
        for (y,x),value in np.ndenumerate(array):ax.text(x,y,str(value),ha='center',va='center')
    fig.tight_layout();fig.savefig(ROOT/'reports/day2/confusion_comparison.png',dpi=160);plt.close(fig)
    write_json(ROOT/'runs/day2/state.json',{'status':'complete','GSM8K_questions':100,'MATH_questions':70,'candidates':680,'scores':680,'primary_task':primary,'formal_protocol_frozen':primary is not None,'T_used':False,'formal_training_started':False})
    print(json.dumps({'gsm8k':gsm,'math':math,'task_decision':decision},ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()
