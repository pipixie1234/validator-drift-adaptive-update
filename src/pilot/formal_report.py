"""Incremental/final fixed-V0 cross-round report; T is never evaluated."""
import argparse
import json
from collections import Counter
import numpy as np
from .core import ROOT,read_jsonl,write_json
from .runtime import model_identity
from .formal_common import RUN,REPORT,PROTOCOL,protocol,inputs,output_path,verify_fixed,sha,utc
from .formal_labels import joined
from .day2_evaluate import pct

def paired_deltas(old,new,reps=1000):
    qs=sorted({r['question_id'] for r in old});assert set(qs)=={r['question_id'] for r in new}
    def array(rows):
        result=[]
        for q in qs:
            rs=[r for r in rows if r['question_id']==q]
            c=sum(r['correct'] is True for r in rs);w=sum(r['correct'] is False for r in rs)
            result.append([c,w,sum(r['correct'] is True and not r['accepted'] for r in rs),
                sum(r['correct'] is False and r['accepted'] for r in rs),sum(r['accepted'] for r in rs),len(rs)])
        return np.array(result,dtype=float)
    a,b=array(old),array(new);rng=np.random.default_rng(42);idx=rng.integers(0,len(qs),size=(reps,len(qs)))
    names={'FNR':(2,0),'FPR':(3,1),'G_accuracy_verified':(0,None),'retention_all':(4,5)};out={}
    for key,(num,den) in names.items():
        def calculate(v):
            denominator=v[...,0]+v[...,1] if den is None else v[...,den]
            return np.divide(v[...,num],denominator,out=np.full_like(denominator,np.nan),where=denominator>0)
        point=float(calculate(b.sum(0))-calculate(a.sum(0)));values=calculate(b[idx].sum(1))-calculate(a[idx].sum(1))
        finite=values[np.isfinite(values)];out[key]={'delta_vs_G0':point if np.isfinite(point) else None,
            'paired_question_bootstrap_ci95':np.quantile(finite,[.025,.975]).tolist() if len(finite) else None,
            'valid_bootstrap_replicates':len(finite),'replicates':reps}
    return out

def transition0(old,new):
    a={r['question_id']:r for r in old if r['candidate_index']==0};b={r['question_id']:r for r in new if r['candidate_index']==0}
    counts=Counter();unknown=0
    for q,r in a.items():
        s=b[q]
        if r['correct'] is None or s['correct'] is None:unknown+=1;continue
        key=('old_correct_' if r['accepted']==r['correct'] else 'old_wrong_')+('new_correct' if s['accepted']==s['correct'] else 'new_wrong')
        counts[key]+=1
    aa=counts['old_correct_new_correct'];bb=counts['old_correct_new_wrong'];cc=counts['old_wrong_new_correct'];dd=counts['old_wrong_new_wrong']
    return {'a_old_correct_new_correct':aa,'b_old_correct_new_wrong':bb,'c_old_wrong_new_correct':cc,'d_old_wrong_new_wrong':dd,
        'unknown_pairs_excluded':unknown,'retained_judgement_correctness':aa/(aa+bb) if aa+bb else None,
        'degraded_judgement_correctness':bb/(aa+bb) if aa+bb else None,'repaired_judgement_correctness':cc/(cc+dd) if cc+dd else None,
        'interpretation':'FixedV on candidate_index0 across different G answers. Answers and truth may change; descriptive transition, not V forgetting on fixed cached answers.'}

def final_audit():
    verify_fixed();p=protocol();checks={}
    source_freeze=RUN/'source_code_sha256.json'
    execution_revision=None
    if source_freeze.exists():
        from .formal_multigpu_revision import verify_execution_revision
        execution_revision=verify_execution_revision()
    split_manifest=json.loads((ROOT/p['formal_split_manifest']).read_text())
    for name,record in split_manifest['splits'].items():
        assert sha(ROOT/f'data/dapo/formal/inputs/{name}.jsonl')==record['input_sha256']
        assert sha(ROOT/f'data/dapo/formal/labels/{name}.jsonl')==record['labels_sha256']
    for round_name in ['G0','G1','G2']:
        adapter=RUN/f'checkpoints/{round_name}_SFT' if round_name!='G0' else None
        expected_ah=model_identity('G',adapter)[1] if adapter else None
        for split in ['B','C']+(['A'] if round_name in ['G0','G1'] else []):
            g=read_jsonl(output_path(round_name,split));v=read_jsonl(output_path(round_name,split,'V'));expected=len(inputs(split))*4
            assert len(g)==len(v)==expected and len({r['candidate_id'] for r in g})==expected
            assert Counter(r['question_id'] for r in g)==Counter({r['question_id']:4 for r in inputs(split)})
            assert {r['candidate_id'] for r in g}=={r['candidate_id'] for r in v}
            assert all(r['adapter_fingerprint']==expected_ah and r['model_fingerprint']==p['models']['G']['fingerprint'] for r in g)
            assert all(r['model_fingerprint']==p['models']['V']['fingerprint'] and r['adapter_fingerprint'] is None and r['threshold']==.5 for r in v)
            assert all(r['prompt_tokens']+r['output_tokens']<=4096 for r in g)
            assert all(r['prompt_tokens']+4<=8192 for r in v)
            assert all('gold_answer' not in r and 'correct' not in r for r in g+v)
            labs={r['candidate_id']:r for r in read_jsonl(RUN/f'labels_{round_name}_{split}.jsonl')}
            assert len(labs)==expected
            assert all(not r['truncated'] or labs[r['candidate_id']]['correct'] is None for r in g)
            checks[f'{round_name}_{split}']={'questions':len(inputs(split)),'candidates':expected,'V0_scores':expected}
    training={r:json.loads((RUN/f'train/{r}/report.json').read_text()) for r in ['G1','G2']}
    assert training['G2']['parent_adapter_fingerprint']==training['G1']['final_adapter_fingerprint']
    assert training['G1']['parent_adapter_fingerprint'] is None
    assert training['G2']['parent_probe_max_logit_difference_before_update'] is not None
    for r,record in training.items():
        assert record['epochs']==1 and record['selected_examples']<=512 and record['base_frozen'] and record['only_LoRA_updated']
        assert record['consumed_examples_once'] and record['save_reload_passed'] and record['truth_used_by_trainer'] is False
        assert record['optimizer_steps']==(record['selected_examples']+7)//8
    result={'passed':True,'checked_at':utc(),'formal_protocol_sha256':sha(PROTOCOL),'stage_counts':checks,
        'fixed_V0_parameters_prompts_scoring_threshold':True,'G2_continues_G1':True,'smoke_adapters_used':False,
        'T_inferred':False,'old3072_baseline_used':False,'known_invalid_C_questions_excluded':4,
        'training_quality_labels_not_used_to_filter':True,
        'execution_revision':execution_revision['revision'] if execution_revision else None,
        'original_outputs_preserved_across_execution_revision':True if execution_revision else None}
    write_json(REPORT/'final_artifact_audit.json',result);return result,training

def figures(metrics):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    for split in ['B','C']:
        fig,axes=plt.subplots(1,3,figsize=(10,3.4))
        for ax,r in zip(axes,['G0','G1','G2']):
            m=metrics[f'{r}_{split}'];mat=np.array([[m['TP'],m['FN']],[m['FP'],m['TN']]])
            ax.imshow(mat,cmap='Blues');ax.set_xticks([0,1],['Accept','Reject']);ax.set_yticks([0,1],['Correct','Wrong']);ax.set_title(f'V0 / {r} / {split}')
            for (y,x),value in np.ndenumerate(mat):ax.text(x,y,str(value),ha='center',va='center')
        fig.tight_layout();fig.savefig(REPORT/f'confusion_{split}.png',dpi=160);plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(10,4))
    for ax,split in zip(axes,['B','C']):
        for field,label in [('false_reject_rate','FNR'),('false_accept_rate','FPR')]:
            vals=[metrics[f'{r}_{split}'][field] for r in ['G0','G1','G2']]
            ax.plot(range(3),[np.nan if x is None else x for x in vals],marker='o',label=label)
        ax.set_xticks(range(3),['G0','G1_SFT','G2_SFT']);ax.set_ylim(0,1);ax.set_title(f'Fixed V0 / {split}');ax.legend();ax.set_ylabel('Conditional error rate')
    fig.tight_layout();fig.savefig(REPORT/'fixed_V0_FNR_FPR.png',dpi=160);plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(9,4))
    for ax,split in zip(axes,['B','C']):
        values=np.array([[metrics[f'{r}_{split}'][k] if metrics[f'{r}_{split}'][k] is not None else np.nan for r in ['G0','G1','G2']]
            for k in ['false_reject_rate','false_accept_rate','acceptance_rate_all_scored']])
        im=ax.imshow(np.ma.masked_invalid(values),vmin=0,vmax=1,cmap='viridis');ax.set_xticks(range(3),['G0','G1','G2'])
        ax.set_yticks(range(3),['FNR','FPR','Retention']);ax.set_title(f'Fixed V0 / {split}')
        for (y,x),value in np.ndenumerate(values):ax.text(x,y,'NA' if np.isnan(value) else f'{value:.3f}',ha='center',va='center',color='white')
    fig.colorbar(im,ax=axes.ravel().tolist(),fraction=.035,pad=.04);fig.savefig(REPORT/'fixed_V0_heatmap.png',dpi=160,bbox_inches='tight');plt.close(fig)

def refresh(final=False):
    metrics={}
    for r in ['G0','G1','G2']:
        for s in ['B','C']:
            path=REPORT/f'{r}_{s}_metrics.json'
            if path.exists():metrics[f'{r}_{s}']=json.loads(path.read_text())
    rows=['# 固定 V0 的正式生成器更新轨迹','',f'更新时间（UTC）：{utc()}。状态：'+('全部阶段完成。' if final else '运行中；下表仅含已经完成并核验的阶段。'),'',
        '所有轮次采用同一冻结提示、采样、上下文预算和V0阈值0.5。T保持封存，旧3072诊断不进入此表。C原100题中4题整体隔离，正式C仅96题。', '',
        '| 数据/轮次 | 正确/错误/未知 | G正确率（可核验） | V误拒FNR | V误接受FPR | 全候选保留率 | 平均输出token | 截断 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for key,m in metrics.items():rows.append(f"| {key} | {m['correct']}/{m['natural_errors']}/{m['unknown']} | {pct(m['G_accuracy_verified'])} | {pct(m['false_reject_rate'])} | {pct(m['false_accept_rate'])} | {pct(m['acceptance_rate_all_scored'])} | {m['length_tokens']['mean']:.1f} | {m['truncated']} |")
    rows+=['','未定不作为负例；条件错误率仅在可核验子集上计算。各JSON同时报告正确率识别上下界、接受集标签覆盖与风险界、全部接受基线、平衡准确率、错误涉及独立题数及按题聚类区间。','',
        '## 实际 SFT 数据与训练','']
    for r in ['G1','G2']:
        path=REPORT/f'{r}_SFT_data_quality.json'
        if path.exists():
            q=json.loads(path.read_text());a=q['actual_SFT_quality']
            rows.append(f"{r}_SFT：实际入选{a['selected']}条，正确{a['correct']}、错误{a['wrong']}、未知{a['unknown']}，有标签错误率{pct(a['verified_error_fraction'])}；全部入选风险界[{pct(a['all_selected_error_lower_bound'])}, {pct(a['all_selected_error_upper_bound'])}]。先按V0筛选并每题随机选1条落盘，再读独立标签计质量，未按真答案剔除错误或未知。整A池2400条均计标签成本。")
        train=RUN/f'train/{r}/report.json'
        if train.exists():
            t=json.loads(train.read_text());rows.append(f"{r}_SFT训练：{t['selected_examples']}条×1epoch，{t['optimizer_steps']}个优化器step，输入{t['input_tokens']}token、监督{t['supervised_tokens']}token；step计算累计{t['step_compute_seconds_all']:.2f}秒，末次调用含保存重载{t['total_seconds_this_invocation']:.2f}秒，峰值allocated{t['peak_allocated_bytes_this_invocation']/2**30:.2f}GiB；保存重载检查={t['save_reload_passed']}。")
    rows+=['','这些是“不完美验证反馈下的生成器更新”，不预设生成器能力提升。G正确率变化与V条件错误率变化分开记录；条件样本组成改变也会影响FNR/FPR，不能自动解释为因果机制。','']
    revision_path=ROOT/'configs/execution_revisions/001_two_generator_processes.json'
    if revision_path.exists():
        rows+=['执行方式修订001：用户授权将后续G生成改为两个独立进程，按题互斥分片，每题仍生成4条；原始生成函数、逐题种子、提示、采样、长度规则及V0评分均保持冻结。切换前已落盘回答按字节前缀校验保留；影子测速32条回答的文本、长度和截断状态均与缓存一致。小样本一致不保证全部后续回答逐token不变。原代码及开跑冻结清单保留，新增代码和修改项由独立修订清单审计。详细测速与切换记录见runs/formal/parallel_benchmark和runs/formal/execution_revisions。','']
    if (ROOT/'configs/execution_revisions/002_two_gpus_four_generator_processes.json').exists():
        rows+=['执行方式修订002：用户在保留磁盘的原实例扩容为两张RTX4090，每卡绑定两个独立G进程，共四个进程按题分片。停机前G1/B的113题452条回答及全部既有缓存、G1检查点按哈希和追加前缀保护。实际G1 adapter双卡重放16条的文本、长度、截断、种子和缓存键均与原结果相同。新生成仍调用原冻结函数，V评分与SFT训练实现不变。多卡阶段按每秒仍有工作进程的不同物理GPU数累计GPU时间，同卡两个进程不重复计数，停机时段不计入工作预算；全局仍为40GPU小时，含此前2小时保守预留。','']
    if not final:
        (REPORT/'progress_report.md').write_text('\n'.join(rows)+'\n');return
    assert len(metrics)==6;aud,training=final_audit();details={}
    for split in ['B','C']:
        baseline=joined('G0',split)
        details[split]={r:{'paired_delta_vs_G0':paired_deltas(baseline,joined(r,split)),
            'candidate0_transition_vs_G0':transition0(baseline,joined(r,split))} for r in ['G1','G2']}
    write_json(REPORT/'cross_round_metrics.json',{'metrics':metrics,'paired_descriptive_analysis':details,'training':training,'audit':aud})
    figures(metrics)
    rows+=['## 相对 G0 的有符号变化','', '| 数据/轮次 | ΔFNR | ΔFPR |','|---|---:|---:|']
    for split,rounds in details.items():
        for r,d in rounds.items():rows.append(f"| {split}/{r} | {pct(d['paired_delta_vs_G0']['FNR']['delta_vs_G0'])} | {pct(d['paired_delta_vs_G0']['FPR']['delta_vs_G0'])} |")
    rows+=['','成对按题bootstrap区间和预定candidate_index0的描述性判断转移见cross_round_metrics.json。G答案及真值可能跨轮改变；这个转移不能直接称为固定回答上的V遗忘。单条训练轨迹不控制训练随机性。','',
        '## 冻结性、成本与限制','',
        'G1从原G0新建LoRA；G2实际继承G1的adapter并通过初始probe一致性检查。两轮均重新初始化AdamW矩，保持相同超参数。V0从未加载adapter或接受参数更新；提示、条件标签似然实现和0.5阈值均由SHA256守护。基座权重未更新，工程smoke adapter不在此谱系中。',
        '全部A/B/C生成、评分及训练的已记录阶段工作见runs/formal/work_ledger.jsonl；恢复可能重做最后未保存的一段，回滚日志保留。预算包括历史工作保守预留2小时，全局上限40小时，不能解释为租赁实例账单。',
        '独立标签为固定来源答案和冻结解析规则的比较，并非每题经人工证明；未知、截断和不支持格式保留。近似题族去重不能保证语义去重或清除预训练暴露。B/C为已固定训练池和开发池，尚未使用T，不能将这些结果称为最终测试成绩。','',
        '图表：confusion_B.png、confusion_C.png、fixed_V0_FNR_FPR.png、fixed_V0_heatmap.png。实际训练质量和完整标签/回答均保留，可按candidate_id复查。']
    (REPORT/'final_report.md').write_text('\n'.join(rows)+'\n')
    (REPORT/'progress_report.md').write_text('\n'.join(rows)+'\n')
    cases=['# 固定V0跨轮代表案例','']
    for r in ['G0','G1','G2']:
        rs=joined(r,'C')
        for label,cond in [('误接受',lambda x:x['correct'] is False and x['accepted']),('误拒绝',lambda x:x['correct'] is True and not x['accepted']),('未知',lambda x:x['correct'] is None)]:
            for row in [x for x in rs if cond(x)][:2]:cases.extend([f"## {r}/{label}/{row['candidate_id']}",row['question'],f"```text\n{row['text']}\n```",f"来源答案={row['gold_answer']}；独立标签={row['correct']}；V0接受={row['accepted']}；分数={row['score']}；核验状态={row['verification_status']}。",''])
    (REPORT/'cases.md').write_text('\n'.join(cases)+'\n')

def main():
    p=argparse.ArgumentParser();p.add_argument('--final',action='store_true');a=p.parse_args();refresh(a.final)

if __name__=='__main__':main()
