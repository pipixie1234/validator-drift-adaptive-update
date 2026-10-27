"""Natural TP/TN robustness: remove or paraphrase a frozen opening span."""
import argparse
import gc
import json
import re
import time
from collections import Counter
from pathlib import Path

import numpy as np

from .core import ROOT, read_jsonl, recover_jsonl, write_json, write_jsonl, append_jsonl, digest, ensure_run_manifest, prompt_ids
from .formal_common import protocol, messages, sha, utc, gpu_lock, WorkBudget, verify_fixed
from .fpr_pattern_diagnostics import load as source_rows, CONFIG as SOURCE_CONFIG, write_csv
from .awl_comparison import lexical_pairs, PLAN as AWL_PLAN
from .prefix_paraphrase import RUN as PREVIOUS, PLAN as PREVIOUS_PLAN, validate as previous_validate
from .day2_protocol import label_response

RUN=ROOT/'runs/natural_invariance_20261024'
REPORT=ROOT/'reports/natural_invariance_20261024'
PLAN=ROOT/'configs/natural_invariance_20261024.json'


def intervention_span(text):
    """First eligible span in the same frozen opening window, never math content."""
    opening=re.split(r'[$\\\d\n]',text[:160],maxsplit=1)[0]
    words=list(re.finditer(r"[A-Za-z]+(?:['’][A-Za-z]+)?",opening))
    swaps={a:b for a,b in lexical_pairs()}|{b:a for a,b in lexical_pairs()}
    options=[]
    for i,m in enumerate(words[:12]):
        word=m.group().lower()
        if word in swaps:
            options.append({'start':m.start(),'end':m.end(),'target':word,'replacement':swaps[word],
                            'type':'AWL_word' if word in {a for a,b in lexical_pairs()} else 'nonAWL_word'})
        for phrase,replacement in [('we need to','we have to'),('to solve for','to find')]:
            width=len(phrase.split())
            if i+width>len(words):continue
            a,b=m.start(),words[i+width-1].end()
            if re.fullmatch(r'\s+'.join(phrase.split()),opening[a:b],re.I):
                options.append({'start':a,'end':b,'target':phrase,'replacement':replacement,'type':'natural_phrase'})
    return min(options,key=lambda x:(x['start'],-(x['end']-x['start']))) if options else None


def edited(row,variant):
    if variant=='original':return row['text']
    span=row['edit_span'];a,b=span['start'],span['end']
    replacement='' if variant=='delete' else span['replacement']
    if replacement and row['text'][a].isupper():replacement=replacement[0].upper()+replacement[1:]
    return row['text'][:a]+replacement+row['text'][b:]


def validate():
    cfg=json.loads(PLAN.read_text());verify_fixed();previous_validate()
    assert all(sha(ROOT/p)==h for p,h in cfg['source_sha256'].items())
    assert sha(RUN/'answers.jsonl')==cfg['answers_sha256']
    return cfg


def prepare():
    RUN.mkdir(exist_ok=True);REPORT.mkdir(exist_ok=True)
    if PLAN.exists():return validate()
    verify_fixed();previous_validate();rows=[];checks=[]
    for r in source_rows():
        if r['G_round']!='G3' or r['correct'] is None or r['accepted']!=r['correct']:continue
        span=intervention_span(r['text'])
        if span is None:continue
        assert not r['truncated'];r={**r,'edit_span':span,'baseline_cell':'TP' if r['correct'] else 'TN'}
        lab=label_response(r['text'],False,r['gold_answer'],'gsm8k')
        assert lab['correct'] is r['correct'] and lab['value']==r['extracted_answer']
        for v in ['delete','synonym']:
            text=edited(r,v);new=label_response(text,False,r['gold_answer'],'gsm8k')
            assert all(new[k]==lab[k] for k in ['correct','value','status'])
            a,b=span['start'],span['end'];assert text.startswith(r['text'][:a]) and text.endswith(r['text'][b:])
        rows.append(r);checks.append({'candidate_id':r['candidate_id'],'baseline_cell':r['baseline_cell'],
                                     'answer_status_label_preserved':True,'only_recorded_span_edited':True})
    assert rows
    write_jsonl(RUN/'answers.jsonl',rows);write_jsonl(RUN/'label_preservation.jsonl',checks)
    paths=[SOURCE_CONFIG,AWL_PLAN,PREVIOUS_PLAN,PREVIOUS/'V0_scores.jsonl',PREVIOUS/'natural_answers.jsonl',
           Path(__file__),ROOT/'src/pilot/runtime.py',RUN/'label_preservation.jsonl']
    cfg={'created_at':utc(),'purpose':'User clarified: select naturally occurring correctly judged TP and TN, then test whether deletion/paraphrase causes a new mistake',
      'selection':'ALL G3 A/C independently known true/false, V0 decision correct, nontruncated, with an eligible natural opening span; no injected responses',
      'variants':['original','delete','synonym'],'window':'Before first formula/backslash/digit/newline, first160chars; span begins among first12 word positions',
      'edit_rule':'Only first eligible span; earliest start, longest tie. Preserve every byte outside span. Match first-letter case of replacement.',
      'lexical_pairs':[{'AWL':a,'nonAWL':b} for a,b in lexical_pairs()],
      'phrases':{'we need to':'we have to','to solve for':'to find'},
      'counts':{s:{c:sum(r['split']==s and r['baseline_cell']==c for r in rows) for c in ['TP','TN']} for s in ['A','C']},
      'scores':3*len(rows),'answers_sha256':sha(RUN/'answers.jsonl'),
      'source_sha256':{str(p.relative_to(ROOT)):sha(p) for p in paths},
      'fixed_V0':{'fingerprint':protocol()['models']['V']['fingerprint'],'adapter':None,'threshold':.5,'scoring':protocol()['V_scoring'],'max_context':8192},
      'metrics':'TP->FN and TN->FP conditional new-error rates; whole-question bootstrap10000 seed20261024. Not full-pool FNR/FPR; selection fixes baseline error to zero.',
      'reuse':'Only exact complete prompt/text matches from validated previous natural experiment; all current original controls freshly rescored',
      'limitations':['Repeated-use C, not untouched test','Natural span selection and chosen vocabulary, not all G3 responses',
        'Deletion can damage grammar; near-synonym nuances differ. Final answer label invariance does not guarantee full semantic invariance.',
        'Comparisons between AWL and nonAWL hit subgroups are not randomized or matched; descriptive only',
        'Effect establishes response sensitivity under this edit, not G learning to exploit a prefix or cause of cross-round drift'],
      'B_used':False,'T_read':False,'G_generation':False,'G_training':False,'V_update':False,'seed':20261024}
    ensure_run_manifest(PLAN,cfg);validate();write_json(RUN/'state.json',{'status':'prepared','counts':cfg['counts'],'scores':cfg['scores']});return cfg


def run():
    import torch
    from .runtime import load, model_identity, conditional_label_scores
    cfg=prepare();rows=read_jsonl(RUN/'answers.jsonl');out=RUN/'V0_scores.jsonl'
    done={s['cache_key'] for s in recover_jsonl(out)}
    prior_data={r['candidate_id']:r for r in read_jsonl(PREVIOUS/'natural_answers.jsonl')}
    previous={}
    for s in read_jsonl(PREVIOUS/'V0_scores.jsonl'):
        if s['arm']=='natural':previous[s['prompt_sha256']]=s
    new_count=reused_count=0
    with gpu_lock():
        budget=WorkBudget('natural_invariance/fixed_V0');start=time.perf_counter()
        model,tok=load('V');fp,adapter=model_identity('V');assert fp==cfg['fixed_V0']['fingerprint'] and adapter is None
        for i,r in enumerate(rows):
            for variant in cfg['variants']:
                key=digest({'plan':sha(PLAN),'candidate_id':r['candidate_id'],'variant':variant})
                if key in done:continue
                budget.check();text=edited(r,variant);msgs=messages('V',r['question'],text);ph=digest(msgs)
                n=len(prompt_ids(tok,msgs));assert n+4<=8192
                prev=previous.get(ph) if variant!='original' else None
                if prev:
                    assert prev['model_fingerprint']==fp and prev['adapter_fingerprint'] is None and prev['threshold']==.5
                    assert prev['edited_text_sha256']==digest(text)
                    sc={k:prev[k] for k in ['log_likelihood_A','log_likelihood_B','score','accepted','label_ids','prompt_tokens']}
                    assert sc['prompt_tokens']==n;source='identical_previous_natural';reused_count+=1
                else:
                    sc=conditional_label_scores(model,tok,msgs,cfg['fixed_V0']['scoring']['labels']);source='new_V0_score';new_count+=1
                if variant=='original':assert sc['accepted']==r['accepted'] and abs(sc['score']-r['score'])<2e-6
                append_jsonl(out,{'candidate_id':r['candidate_id'],'question_id':r['question_id'],'split':r['split'],
                    'baseline_cell':r['baseline_cell'],'correct':r['correct'],'variant':variant,'edit_target':r['edit_span']['target'],
                    **sc,'new_error':sc['accepted']!=r['correct'],'source':source,'model_fingerprint':fp,'adapter_fingerprint':None,
                    'threshold':.5,'text_sha256':digest(text),'prompt_sha256':ph,'cache_key':key,'recorded_at':utc()})
                done.add(key)
                if source=='new_V0_score':budget.charge(r['candidate_id']+'/'+variant)
            write_json(RUN/'state.json',{'status':'scoring','answers_done':i+1,'answers_total':len(rows),'scores':len(done),'planned_scores':cfg['scores']})
            if (i+1)%25==0:print(f'Natural invariance {i+1}/{len(rows)}; {len(done)}/{cfg["scores"]}',flush=True)
        assert len(done)==cfg['scores'] and model_identity('V')==(fp,None)
        write_json(RUN/'compute.json',{'seconds_this_invocation':time.perf_counter()-start,'new_scores':new_count,'reused_scores':reused_count,'logical_scores':len(done),'V0_unchanged':True})
        budget.charge('finalization');del model;gc.collect();torch.cuda.empty_cache()
    validate();report()


def stats_for(rows,by,variant,draws=10000):
    qs=sorted({r['question_id'] for r in rows});idx={q:i for i,q in enumerate(qs)}
    qi=np.array([idx[r['question_id']] for r in rows]);values=np.array([by[r['candidate_id'],variant]['new_error'] for r in rows],float)
    count=np.bincount(qi,minlength=len(qs));errors=np.bincount(qi,weights=values,minlength=len(qs))
    rng=np.random.default_rng(20261024);draw=rng.integers(len(qs),size=(draws,len(qs)))
    boot=errors[draw].sum(1)/count[draw].sum(1);lo,hi=np.quantile(boot,[.025,.975])
    return {'responses':len(rows),'questions':len(qs),'new_errors':int(values.sum()),'new_error_rate':float(values.mean()),
        'low':float(lo),'high':float(hi),'affected_questions':len({r['question_id'] for r,e in zip(rows,values) if e}),
        'baseline_error_rate':0.,'variant':variant}


def report():
    cfg=validate();rr=read_jsonl(RUN/'answers.jsonl');ss=read_jsonl(RUN/'V0_scores.jsonl');by={(s['candidate_id'],s['variant']):s for s in ss}
    assert len(ss)==len(by)==cfg['scores'];index={r['candidate_id']:r for r in rr}
    for s in ss:
        r=index[s['candidate_id']];text=edited(r,s['variant'])
        assert s['question_id']==r['question_id'] and s['correct'] is r['correct'] and s['baseline_cell']==r['baseline_cell']
        assert s['accepted']==(s['score']>=.5) and s['new_error']==(s['accepted']!=r['correct'])
        assert s['text_sha256']==digest(text) and s['prompt_sha256']==digest(messages('V',r['question'],text))
        assert s['cache_key']==digest({'plan':sha(PLAN),'candidate_id':r['candidate_id'],'variant':s['variant']})
        assert s['model_fingerprint']==cfg['fixed_V0']['fingerprint'] and s['adapter_fingerprint'] is None and s['threshold']==.5 and s['prompt_tokens']+4<=8192
        if s['variant']=='original':assert not s['new_error'] and abs(s['score']-r['score'])<2e-6
    summary=[];subgroups=[]
    for split in ['A','C']:
        for cell in ['TP','TN']:
            rows=[r for r in rr if r['split']==split and r['baseline_cell']==cell]
            for v in ['delete','synonym']:
                summary.append({'split':split,'baseline_cell':cell,'transition':'TP_to_FN' if cell=='TP' else 'TN_to_FP',**stats_for(rows,by,v)})
            for target in sorted({r['edit_span']['target'] for r in rows}):
                selected=[r for r in rows if r['edit_span']['target']==target]
                for v in ['delete','synonym']:
                    subgroups.append({'split':split,'baseline_cell':cell,'target':target,'exploratory':True,**stats_for(selected,by,v)})
    write_csv(REPORT/'conditional_new_errors.csv',summary);write_csv(REPORT/'by_target.csv',subgroups)
    write_json(REPORT/'results.json',{'primary':summary,'exploratory_by_target':subgroups})
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    with PdfPages(REPORT/'natural_invariance_figures.pdf') as pdf:
        fig,axes=plt.subplots(1,2,figsize=(10,4.6),constrained_layout=True)
        for ax,split in zip(axes,['A','C']):
            data=np.array([[next(s['new_error_rate'] for s in summary if s['split']==split and s['baseline_cell']==c and s['variant']==v) for v in ['delete','synonym']] for c in ['TP','TN']])*100
            im=ax.imshow(data,vmin=0,vmax=max(10,max(r['new_error_rate']*100 for r in summary)),cmap='YlOrRd',aspect='auto')
            ax.set_xticks([0,1],['Delete','Near-synonym']);ax.set_yticks([0,1],['TP -> FN','TN -> FP']);ax.set_title(split)
            for (i,j),val in np.ndenumerate(data):
                cell=['TP','TN'][i];v=['delete','synonym'][j];s=next(s for s in summary if s['split']==split and s['baseline_cell']==cell and s['variant']==v)
                ax.text(j,i,f'{val:.2f}%\n{s["new_errors"]}/{s["responses"]}',ha='center',va='center')
        fig.suptitle('Natural, initially correct V0 decisions: new errors after editing\nSame answer, final label unchanged; baseline selected error = 0%')
        fig.colorbar(im,ax=axes,label='Conditional new-error rate (%)',shrink=.7)
        fig.savefig(REPORT/'natural_TP_TN_new_errors.png',dpi=180);pdf.savefig(fig);plt.close(fig)
    lines=['# 自然回答：V原本判对后，删除/近义替换是否造成新错误','','本实验按用户澄清，仅选原本判断正确的TP和TN。不是挑选FP修复，也不是把人工注入前缀再删除。',
      'TP是正确回答被接受，干预后拒绝即TP→FN；TN是错误回答被拒绝，干预后接受即TN→FP。以下为这批原本判对样本上的条件新错误率，不是全体回答的总体FNR/FPR。',
      '', '|池|原状态|回答/独立题|删除后的新错误|删除率及配对95%区间|近义替换后的新错误|替换率及配对95%区间|',
      '|---|---|---:|---:|---|---:|---|']
    for split in ['A','C']:
        for cell in ['TP','TN']:
            a=next(s for s in summary if s['split']==split and s['baseline_cell']==cell and s['variant']=='delete')
            b=next(s for s in summary if s['split']==split and s['baseline_cell']==cell and s['variant']=='synonym')
            lines.append(f'|{split}|{cell}|{a["responses"]}/{a["questions"]}|{a["new_errors"]}|{100*a["new_error_rate"]:.2f}% [{100*a["low"]:.2f}, {100*a["high"]:.2f}]|{b["new_errors"]}|{100*b["new_error_rate"]:.2f}% [{100*b["low"]:.2f}, {100*b["high"]:.2f}]|')
    lines += ['', '## 取样和干预','','使用G3自然A/C缓存，真假标签已独立核验、原V0决定正确、不截断；未知排除。覆盖全部预设词/短语命中样本，未按干预效果抽样。',
      '目标是上次冻结的20组AWL/非AWL近义动词（双向替换）以及we need to→we have to、to solve for→to find。只在回答开头160字符、首次公式/数字/换行前、前12个词起点中取最早一个目标；同起点取最长跨度。',
      '删除或替换仅改变记录的一个跨度，保留其余全部字节及最终答案。替换沿用首字母大小写；所有改写独立重提答案，确认最终答案值、真假标签及解析状态均不变。V0读取完整回答；参数、提示、评分实现及0.5阈值固定。',
      '', '## 能说明与不能说明','','出现新错误，支持V对这些自然文本编辑存在局部判断敏感性。它不证明G已利用这些词，也不解释训练后FPR上涨。',
      '删除可能造成语法不自然，词对为近义而非所有上下文完全等义；标签不变不等于全文语义完全不变。例：we need to有专门的语法改写we have to，避免直接变成we require to，但其他近义动词仍可能改变细微语气。',
      '因为按原判断正确选择样本，原错误率被固定为0；不能用普通随机样本的改善/恶化检验来外推总体。区间按题整组bootstrap10000次，仅描述当前选择规则下的变化，未包含不同训练种子。',
      'TP/TN分布与题型、目标词、长度可能不同；两类判错率不能直接解释为词语对正负类的纯因果差异。AWL与非AWL自然命中也没有匹配，仅在逐目标表作描述。C已反复使用，是开发集，T仍封存。',
      '', '## 变化案例','','全部变化保留完整题目、原回答、编辑后回答和两次分数。案例核验仅保证独立最终答案标签，过程理由需另行审查。']
    cases=[]
    for r in rr:
        for v in ['delete','synonym']:
            s=by[r['candidate_id'],v]
            if s['new_error']:
                cases.append({'original':r,'variant':v,'edited_text':edited(r,v),'edited_score':s,'original_score':by[r['candidate_id'],'original'],'human_review':'pending'})
    write_jsonl(REPORT/'all_changed_cases.jsonl',cases)
    # Balanced directional showcase, strongest score change; not a prevalence sample.
    for split in ['A','C']:
        for cell in ['TP','TN']:
            for v in ['delete','synonym']:
                options=[c for c in cases if c['original']['split']==split and c['original']['baseline_cell']==cell and c['variant']==v]
                if not options:continue
                c=max(options,key=lambda c:abs(c['edited_score']['score']-c['original']['score']));r=c['original']
                lines += ['',f'### {split} {cell} / {v}',f'ID={r["candidate_id"]}；标签={r["correct"]}；目标={r["edit_span"]["target"]}；分数{r["score"]:.6g}→{c["edited_score"]["score"]:.6g}。',
                    r['question'],'原开场：','```text',r['text'][:450],'```','编辑后开场：','```text',c['edited_text'][:450],'```']
    compute=json.loads((RUN/'compute.json').read_text())
    lines += ['', '## 资源和产物','',f'共{len(rr)}条自然回答、{len(ss)}条评分；新评分{compute["new_scores"]}条、完整输入相同历史复用{compute["reused_scores"]}条，GPU阶段{compute["seconds_this_invocation"]:.1f}秒。每条原文均重新评分复现历史分数及决定。无G生成/训练，无V更新，B未使用、T未读取。',
       '图片natural_TP_TN_new_errors.png、汇总CSV和逐词表、全部变化JSONL，原文/跨度/全部分数保存在runs/natural_invariance_20261024。']
    (REPORT/'natural_invariance_report.md').write_text('\n'.join(lines)+'\n')
    import shutil,zipfile
    for src in [PLAN,RUN/'compute.json',RUN/'label_preservation.jsonl',Path(__file__)]:shutil.copyfile(src,REPORT/src.name)
    write_json(REPORT/'audit.json',{'passed':True,'all_originals_match':True,'all_labels_preserved':True,'full_context':True,'fixed_V0':True,'scores':len(ss),'at':utc()})
    paths=sorted(p for p in REPORT.iterdir() if p.is_file() and p.suffix!='.zip' and p.name!='artifact_manifest.json')
    write_json(REPORT/'artifact_manifest.json',{p.name:sha(p) for p in paths})
    with zipfile.ZipFile(REPORT/'natural_invariance_review.zip','w',zipfile.ZIP_DEFLATED) as z:
        for p in paths+[REPORT/'artifact_manifest.json']:z.write(p,p.name)
    write_json(RUN/'state.json',{'status':'complete','report':str(REPORT/'natural_invariance_report.md'),'at':utc()})
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['prepare','run','report']);a=p.parse_args()
    try:
        if a.stage=='prepare':print(json.dumps({'prepared':True,'counts':prepare()['counts'],'scores':prepare()['scores']},ensure_ascii=False))
        elif a.stage=='run':run()
        else:report()
    except Exception as e:
        write_json(RUN/'state.json',{'status':'failed','error':str(e),'at':utc(),'resume':'scripts/pilot natural_invariance run'});raise

if __name__=='__main__':main()
