"""Independent labels, clustered diagnostics and conditional formal pool freeze."""
import json
from collections import Counter
from .core import ROOT, read_jsonl, write_json, write_jsonl, ensure_run_manifest
from .day2_protocol import label_response, extract, numeric, VERSION
from .day2_evaluate import stats, confidence, pct
from .dapo_common import BASE, RUN, REPORT, config, sha
from .dapo_length import length_summary

SOURCE_REPORT=ROOT/'reports/dapo'

def abstain_on_source_issue(row):
    if row['source_issue']:
        row['correct']=None
        if row['verification_status']!='truncated':row['verification_status']='source_question_unresolved'
    return row

def gate_result(m, gate, old_seconds):
    checks={
        'coverage':m['verifiable_coverage']>=gate['minimum_verifiable_coverage'],
        'correct_count':m['correct']>=gate['minimum_correct'],
        'natural_errors':m['natural_errors']>=gate['minimum_natural_errors'],
        'error_questions':m['error_questions']>=gate['minimum_error_questions'],
        'correct_questions':m['correct_questions']>=gate['minimum_correct_questions'],
        'length':m['truncated_count']/m['total_candidates']<=gate['maximum_truncation_fraction'],
        'all_V_scores':m['V_scoring_status_counts']=={'ok':m['total_candidates']},
        'lower_cost_per_error':m['seconds_per_verified_natural_error'] is not None and m['seconds_per_verified_natural_error']<old_seconds}
    return checks,all(checks.values())

def formal_pools():
    cfg=config();inventory=read_jsonl(BASE/'eligible_families.jsonl')
    selection=json.loads((BASE/'manifests/selection_locked.json').read_text())
    assert sha(BASE/'eligible_families.jsonl')==selection['inventory_sha256']
    dev=read_jsonl(BASE/'inputs/dapo100.jsonl');assert [r['question_id'] for r in inventory[:100]]==[r['question_id'] for r in dev]
    offset=100;members={'D_dev':dev};manifest={}
    for split,size in cfg['conditional_formal_pool_sizes'].items():
        chosen=inventory[offset:offset+size];assert len(chosen)==size;offset+=size
        inputs=[{k:v for k,v in r.items() if k not in ['gold_answer','reference_consistent']}|{'task':'dapo','split':split} for r in chosen]
        labels=[{'question_id':r['question_id'],'gold_answer':r['gold_answer']} for r in chosen]
        write_jsonl(BASE/f'formal/inputs/{split}.jsonl',inputs)
        write_jsonl(BASE/f'formal/labels/{split}.jsonl',labels)
        members[split]=inputs
        manifest[split]={'size':size,'input_sha256':sha(BASE/f'formal/inputs/{split}.jsonl'),
                         'label_sha256':sha(BASE/f'formal/labels/{split}.jsonl'),
                         'question_ids':[r['question_id'] for r in inputs],
                         'family_ids':[r['family_id'] for r in inputs]}
    for field in ['question_id','question_hash','family_id']:
        sets=[{r[field] for r in rows} for rows in members.values()]
        assert len(set.union(*sets))==sum(map(len,sets)),f'{field} cross-split leakage'
    uuid_sets=[{u for r in rows for u in r['family_source_uuids']} for rows in members.values()]
    assert len(set.union(*uuid_sets))==sum(map(len,uuid_sets))
    result={'splits':manifest,'dev_excluded':100,'allocation':'Following pre-shuffled family inventory, no model-performance or label-based selection.',
            'heldout_inferred':False,'formal_training_started':False,
            'semantic_dedup_limit':selection['dedup_limit'],'final_test_is_internal_heldout_of_public_train':True}
    ensure_run_manifest(BASE/'formal/manifest.json',result)
    return result

def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--diagnostic-only',action='store_true');args=parser.parse_args()
    import numpy as np
    cfg=config();g=read_jsonl(RUN/'G0_dapo100.jsonl');v=read_jsonl(RUN/'V0_dapo100.jsonl')
    assert len(g)==len(v)==400 and len({r['candidate_id'] for r in g})==400
    assert {r['candidate_id'] for r in g}=={r['candidate_id'] for r in v}
    byid={r['candidate_id']:r for r in v};gold={r['question_id']:r['gold_answer'] for r in read_jsonl(BASE/'labels/dapo100.jsonl')}
    source_review=json.loads((SOURCE_REPORT/'source_quality_review.json').read_text())
    assert source_review['input_sha256']==sha(BASE/'inputs/dapo100.jsonl')
    assert source_review['V_scores_available_at_review']==0 and source_review['reviewed_questions']==100
    issues={r['question_id']:r for r in source_review['issues']}
    labelled=[]
    for r in g:
        # DAPO source references are integers; reuse day2 exact-rational path unchanged.
        lab=label_response(r['text'],r['truncated'],gold[r['question_id']],'gsm8k')
        parsed=extract(r['text'],r['truncated'],'gsm8k')
        try:
            normalized=numeric(lab['value']) if lab['value'] is not None else None
            answer_type='integer' if normalized is not None and '/' not in normalized else 'rational' if normalized is not None else 'unresolved'
        except (ValueError,ZeroDivisionError):answer_type='unsupported_or_ambiguous_expression'
        labelled.append({**r,**byid[r['candidate_id']],'gold_answer':gold[r['question_id']],
            'prompt_tokens':r['prompt_tokens'],'V_prompt_tokens':byid[r['candidate_id']]['prompt_tokens'],
            'source_gold_correct':lab['correct'],'source_gold_status':lab['status'],
            'source_issue':issues.get(r['question_id']),
            'parse_status':parsed['status'],'answer_type':answer_type,'grader_version':VERSION+':DAPO_exact_rational_source_abstention_v3',
            'label_source':'automatic_source_gold_comparison' if r['question_id'] not in issues else 'assistant_source_validity_abstention',
            'correct':lab['correct'],'verification_status':lab['status'],
            'extracted_answer':lab['value'],'comparison':lab['comparison'],'answer_format':lab.get('format')})
    source_gold_only=stats(labelled)
    for row in labelled:abstain_on_source_issue(row)
    write_jsonl(RUN/'labels_dapo100.jsonl',[{k:r[k] for k in ['question_id','candidate_id','gold_answer','correct','verification_status','extracted_answer','comparison','answer_format','source_gold_correct','source_gold_status','source_issue','parse_status','answer_type','grader_version','label_source']} |
        {'unresolved_reason':r['verification_status'] if r['correct'] is None else None,
         'revision_history':[{'from_source_gold_correct':r['source_gold_correct'],'to':None,'reason':r['source_issue']['category'],'review_file':'reports/dapo/source_quality_review.json'}] if r['source_issue'] else []} for r in labelled])
    m=stats(labelled);m['correct_questions']=len({r['question_id'] for r in labelled if r['correct'] is True})
    m['source_gold_only_sensitivity']=source_gold_only
    m['source_unresolved_questions']=len(issues)
    m['source_quality_review_sha256']=sha(SOURCE_REPORT/'source_quality_review.json')
    m['mixed_correct_and_error_questions']=len({r['question_id'] for r in labelled if r['correct'] is True}&{r['question_id'] for r in labelled if r['correct'] is False})
    m['verification_status_counts']=dict(Counter(r['verification_status'] for r in labelled))
    m['V_scoring_status_counts']=dict(Counter(r['scoring_status'] for r in labelled))
    m['truncated_count']=sum(r['truncated'] for r in labelled);m['length']=length_summary(labelled)
    m['V_max_prompt_tokens']=max(r['V_prompt_tokens'] for r in labelled)
    m['length_by_label']={key:{'n':len(rs),'mean':float(np.mean([r['output_tokens'] for r in rs])) if rs else None}
        for key,rs in [('correct',[r for r in labelled if r['correct'] is True]),('wrong',[r for r in labelled if r['correct'] is False]),('unknown',[r for r in labelled if r['correct'] is None])]}
    m['by_length']={key:stats(rs) for key,rs in [
        ('completed_le1536',[r for r in labelled if not r['truncated'] and r['output_tokens']<=1536]),
        ('completed_gt1536',[r for r in labelled if not r['truncated'] and r['output_tokens']>1536]),
        ('truncated',[r for r in labelled if r['truncated']])]}
    m['uncertainty']=confidence(labelled,'dapo')
    generation_seconds=sum({r['question_id']:r['generation_seconds_for_question'] for r in g}.values())
    scoring_seconds=sum(r['seconds'] for r in v)
    m['performance']={'G_model_seconds':generation_seconds,'V_scoring_seconds':scoring_seconds,
        'invocations':{role:read_jsonl(RUN/f'{role}_invocations.jsonl') for role in ['G','V']}}
    m['budget_projection']={'assumed_G_candidates':12000,'assumed_V_scores':19200,
        'G_inference_hours':generation_seconds/400*12000/3600,
        'V_scoring_hours':scoring_seconds/400*19200/3600,
        'basis':'Original weekly candidate counts as a reference only; excludes parameter training, loads and CPU preparation; later G output lengths may change. No formal job authorized by this projection.'}
    m['seconds_per_verified_natural_error']=(generation_seconds+scoring_seconds)/m['natural_errors'] if m['natural_errors'] else None
    m['seconds_per_error_question']=(generation_seconds+scoring_seconds)/m['error_questions'] if m['error_questions'] else None
    previous={name:json.loads((ROOT/f'reports/day2/{name}_metrics.json').read_text()) for name in ['gsm8k_C100','math70']}
    cost={}
    for name,old in previous.items():
        perf=old['performance'];seconds=perf['generation_model_seconds']+perf['V_loop_wall_seconds']
        cost[name]={'total_inference_seconds':seconds,'natural_errors':old['natural_errors'],
            'error_questions':old['error_questions'],'seconds_per_verified_natural_error':seconds/old['natural_errors'],
            'seconds_per_error_question':seconds/old['error_questions']}
    checks,suitable=gate_result(m,cfg['feasibility_gate'],cost['math70']['seconds_per_verified_natural_error'])
    m['cost_comparison_day2']=cost;m['feasibility_checks']=checks;m['suitable']=suitable
    write_json(REPORT/'dapo100_metrics.json',m);write_json(REPORT/'length_full.json',m['length'])
    if args.diagnostic_only:
        write_report(m,previous,cfg,{},labelled)
        print(json.dumps({'diagnostic_only':True,'checks':checks,'suitable':suitable,'verified':m['verified'],'natural_errors':m['natural_errors']},indent=2));return
    pools=formal_pools() if suitable else None
    revision={'revision':'003_dapo_diagnostic','supersedes_for_future_work':'configs/formal_protocol.json',
        'preserved_previous_protocol_sha256':sha(ROOT/'configs/formal_protocol.json'),
        'reason':'User requested harder, cheaper naturally erroneous responses and explicit length sufficiency before any G1/G2 training.',
        'G1_G2_status':'paused','formal_training_started':False,'development_complete':True,
        'DAPO_suitable':suitable,'checks':checks,'pre_registered_gate':cfg['feasibility_gate'],
        'next_primary_task':'DAPO English' if suitable else 'undecided; retain previous MATH decision as archived evidence',
        'pools_constructed':pools is not None,'pool_sizes':cfg['conditional_formal_pool_sizes'] if pools else None,
        'protocol_config':cfg,'answer_protocol_version':VERSION,'answer_comparison':'Unchanged day2 boxed extraction and exact-rational GSM path for integer DAPO references; truncation always unknown.',
        'changes_from_day2':{'task':'DAPO English development100 / 4 candidates','G_max_new_tokens':[1536,3072],
            'V_max_context':[4096,8192],'weights_prompts_sampling_thresholds':'unchanged',
            'source_validity':'Four source-question issues flagged after fixed sampling and before V scoring; all their labels unknown, raw source-gold sensitivity retained.'},
        'G0_model_fingerprint':g[0]['model_fingerprint'],'V0_model_fingerprint':v[0]['model_fingerprint'],
        'source_manifest_sha256':sha(BASE/'manifests/selection_locked.json'),
        'metrics_sha256':sha(REPORT/'dapo100_metrics.json'),
        'primary_metrics_file':str((REPORT/'dapo100_metrics.json').relative_to(ROOT)),
        'primary_run_directory':str(RUN.relative_to(ROOT)),
        'source_quality_review_sha256':sha(SOURCE_REPORT/'source_quality_review.json'),
        'source_quality_abstention_amendment':source_review['protocol_amendment'],
        'day3_smoke_config_sha256':sha(ROOT/'configs/day3_smoke.json') if (ROOT/'configs/day3_smoke.json').exists() else None,
        'updated_research_plan_snapshot_sha256':sha(ROOT/'reports/day3/research_plan_snapshot_20261020.md') if (ROOT/'reports/day3/research_plan_snapshot_20261020.md').exists() else None,
        'code_sha256':{str(path.relative_to(ROOT)):sha(path) for path in [ROOT/'src/pilot/day2_protocol.py',ROOT/'src/pilot/runtime.py',*sorted((ROOT/'src/pilot').glob('dapo_*.py'))]},
        'test_inference':False,'new_heldout_scope':'Internal heldout of public DAPO train; approximate lexical dedup; possible pretraining contamination.',
        'training_limitations':'No optimizer or training length protocol launched. Before formal updates, verify natural positive/negative LoRA smoke and freeze per-round token/optimizer budgets.'}
    ensure_run_manifest(ROOT/'configs/protocol_revisions/003_dapo_diagnostic.json',revision)
    write_json(ROOT/'configs/current_protocol.json',{'active_revision':'configs/protocol_revisions/003_dapo_diagnostic.json',
        'formal_training':'paused','previous_frozen_config_preserved':'configs/formal_protocol.json'})
    write_json(ROOT/'configs/stage_status.json',{'phase':'DAPO_development_complete','formal_G1_G2_training':'paused',
        'formal_V_updates':'paused','formal_training_started':False,'protocol_revision':'configs/protocol_revisions/003_dapo_diagnostic.json',
        'DAPO_suitable':suitable,'formal_pools_created':suitable})
    write_report(m,previous,cfg,revision,labelled)
    write_json(RUN/'state.json',{'status':'diagnostic_complete','candidates':400,'V_scores':400,
        'DAPO_suitable':suitable,'formal_pools_created':suitable,'formal_training_started':False,'T_inferred':False})
    print(json.dumps({'verified':m['verified'],'correct':m['correct'],'natural_errors':m['natural_errors'],
        'error_questions':m['error_questions'],'truncated':m['truncated_count'],'checks':checks,'suitable':suitable},ensure_ascii=False,indent=2))

def write_report(m,previous,cfg,revision,labelled):
    rows=['# DAPO 英语开发诊断','',
        f"100题×4候选已完成。适用门槛通过={m['suitable']}；正式 G1/G2 及 V 参数更新继续暂停。第二天全部结果和 configs/formal_protocol.json 保留，未来阶段状态见新增的第003次协议记录。",'',
        '| 指标 | GSM8K C100 | MATH70 | DAPO100 |','|---|---:|---:|---:|']
    for label,key,percent in [('可核验覆盖率','verifiable_coverage',True),('正确回答','correct',False),('自然错误回答','natural_errors',False),('错误涉及独立题目','error_questions',False),('G正确率（可核验子集）','G_accuracy_verified',True),('V误接受率 FP/(FP+TN)','false_accept_rate',True),('V误拒绝率 FN/(TP+FN)','false_reject_rate',True),('接受集错误比例','accepted_error_fraction',True),('截断回答','truncated_count',False)]:
        vals=[previous['gsm8k_C100'][key],previous['math70'][key],m[key]]
        rows.append('| '+label+' | '+' | '.join(pct(v) if percent else str(v) for v in vals)+' |')
    rows+=['',f"DAPO V混淆计数：TP={m['TP']}，FN={m['FN']}，FP={m['FP']}，TN={m['TN']}。可核验分母={m['verified']}/400，未知={m['unknown']}。正确答案覆盖{m['correct_questions']}道独立题；同题兼有正确与自然错误候选={m['mixed_correct_and_error_questions']}题。",'',
        f"每题已核验错误数→题目数：{m['question_error_histogram']}。错误最多5题占全部错误的{pct(m['largest_5_error_question_share'])}，同题4个候选不能当作4道独立问题。",'',
        f"完整400条的正确率识别区间为[{pct(m['G_correctness_lower_bound_all_outputs'])}, {pct(m['G_correctness_upper_bound_all_outputs'])}]；未知回答不进入V两类错误率分母。区间估计采用1000次按题聚类bootstrap，见 dapo100_metrics.json。",'',
        f"题干审查发现{m['source_unresolved_questions']}道明确来源问题：缺少所求目标、含π的结果却要求普通有理数、缺失dot plot、以及数值求值与系数和要求冲突且来源标签不符。所有候选和推理成本保留，只把这些题的正确性设为未定。单纯按来源整数匹配的敏感性结果：{m['source_gold_only_sensitivity']}。",'',
        '来源质量审查在固定抽样之后、V评分之前完成，检查了100道题文；并非生成前预注册的规则，这项保守弃权作为本次协议修订的明确增补，不能声称完全预注册。没有因此替换题目、修改提示或选择V更有利的分母。未标记的96题仍可能有未发现的来源标签问题。', '',
        '## 长度检查','',
        '生成前固定3072输出token；G原生总上下文4096，入选题目提示不超过1024 token。未改RoPE或模型权重。前20题是固定100题的一部分，不另挑简单短题；同批候选缓存后继续余下80题。长度判断只使用终止/EOS和token统计，不按正确率换上限。',
        f"完整样本长度：{m['length']}。",'',
        '超过原1536上限但在3072内正常结束的回答单列统计；1536前缀仅用于估计旧上限会影响多少回答，不生成额外错误标签。3072仍截断的回答，无论是否已出现boxed，都保持未知。低截断比例仅支持该开发样本下上限基本可用，不证明任何题目均已充分思考。',
        f"解析状态：{m['verification_status_counts']}。V评分状态：{m['V_scoring_status_counts']}。",'',
        'V窗口由4096提高到8192，以完整接收长候选；不截去题目、推理前文或候选尾部。除两处长度上限和数据载体外，沿用第二天G/V权重、提示、4候选、温度0.7、top_p0.9、A/B条件似然及0.5阈值。', '',
        '## 获取自然错误的成本','',
        '| 数据 | 推理秒/已核验自然错误 | 推理秒/出现错误的独立题 |','|---|---:|---:|']
    for name,cost in m['cost_comparison_day2'].items():rows.append(f"| {name} | {cost['seconds_per_verified_natural_error']:.2f} | {cost['seconds_per_error_question']:.2f} |")
    def fmt(v):return '未定义' if v is None else f'{v:.2f}'
    rows += [f"| DAPO100 | {fmt(m['seconds_per_verified_natural_error'])} | {fmt(m['seconds_per_error_question'])} |",'',
        '成本包含全部开发候选（含未知）的G生成及V评分，G同题4候选批次仅计一次。不含下载、模型加载、CPU标注及训练；MATH的V用完整循环墙钟，DAPO用逐条评分耗时，存在小量计时口径差异。任务、题目分布和长度上限同时改变，此处是当前服务器上的描述性预算比较，不能归因为单独的数据集难度或推断后续G1/G2的成本。', '',
        f"固定候选数预算参考：{m['budget_projection']}。即使每条错误更便宜，单条响应更长仍可能增加相同候选总量的整体开销，正式训练前还需确定各轮总token与候选预算。",'',
        '## 预先门槛与后续安排','',f"预先门槛：{cfg['feasibility_gate']}。逐项结果：{m['feasibility_checks']}。",'',
        ('通过后按预先打乱的剩余题族顺序建立A_G_update600、B_V_update200、T_final300；100道开发题及其词面近似题族全部排除。没有按模型表现重新抽题，也没有对这些正式池运行推理或训练。' if m['suitable'] else '门槛未全部通过，未建立正式题池，也未启动训练；本轮保留诊断证据，后续协议需另行确定。'),'',
        '协议修订只新增 configs/protocol_revisions/003_dapo_diagnostic.json 和 current_protocol.json；第二天冻结文件没有覆盖。研究目标仍是验证器的可靠跨轮泛化。', '',
        '## 数据来源、隔离及限制','',
        '[DAPO原始数据](https://huggingface.co/datasets/BytedTsinghua-SIA/DAPO-Math-17k)；[Open-R1去重英语派生版](https://huggingface.co/datasets/open-r1/DAPO-Math-17k-Processed)；[DAPO论文 §3.5](https://dapo-sia.github.io/static/pdf/dapo_paper.pdf)。论文说明将答案重写为整数，本次完整保留这些数学改写条件，不对答案做mod1000等额外变换。',
        f"使用派生版固定revision {cfg['revision']} 的en/train文件，14116行，SHA256 {cfg['file_sha256']}；5.2MB经hf-mirror国内镜像下载，实测约1.13MiB/s，未下载新模型、原始299MB重复文件或任何公开测试集。派生版未固定父数据集revision，本次只固定实际使用的parquet与保留原始prompt/UUID。",'',
        '逐行验证干净prompt与source_prompt仅差已知Answer格式包装，直接使用干净prompt，保留其余字符。来源英语子集额外检查无CJK；在抽样前排除显式图示依赖、超上下文题及接触旧A/B/C/T、MATH70的词面题族。相同规范化题文、相同来源UUID及高三词片段包含率通过连通分量分组，每族取一个代表。只承诺记录规则下的隔离；源数据没有原始竞赛题号，近似检索可能漏掉改写或语义重复，不能保证消除跨数据集同题与预训练污染。', '',
        '标准整数来自公开训练数据；独立核验仅指用来源答案做精确有理数比较、与V分数分离，不表示人工重解并认证了所有原题。已对固定顺序下最早几道出现自然错误的题目中的5题作额外数学推导和精确计算，均支持来源标签，详见 source_spotchecks.json；这是助手抽查，不是独立人工标注研究，也不覆盖100题。模型仍可能给出数学上合理但与错误/有歧义来源标签不一致的答案；案例与未知原文留存，最终可靠性研究仍需对关键误接受题作独立复核。', '',
        '旧GSM8K T未推理，未下载或评估MATH test/AIME测试集；若新T题池建立，它只是公开DAPO训练数据中的内部留出，尚未生成回答。正式训练仍需在启动前明确LoRA更新的token/优化器预算，并检查自然正负样本，不沿用第一天仅正标签smoke的结论。']
    (REPORT/'dapo_report.md').write_text('\n'.join(rows)+'\n')
    lines=['# DAPO 诊断案例','', '按固定输出顺序各取前三条；仅用于阅读，不改变样本和标签。','']
    for name,condition in [('误接受',lambda r:r['correct'] is False and r['accepted']),('误拒绝',lambda r:r['correct'] is True and not r['accepted']),('正确接受',lambda r:r['correct'] is True and r['accepted']),('正确拒绝错误',lambda r:r['correct'] is False and not r['accepted']),('未定',lambda r:r['correct'] is None)]:
        for r in [r for r in labelled if condition(r)][:3]:
            lines += [f"## {name} / {r['candidate_id']}",r['question'],f"```text\n{r['text']}\n```",
                f"来源标准={r['gold_answer']}；提取={r['extracted_answer']}；核验={r['correct']}；状态={r['verification_status']}；V接受={r['accepted']}；分数={r['score']}；长度={r['output_tokens']}；截断={r['truncated']}。",'']
    (REPORT/'dapo_cases.md').write_text('\n'.join(lines)+'\n')

if __name__=='__main__':main()
