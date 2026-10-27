"""Fixed V0 prefix perturbations on question-matched TN answers from G0..G3."""
import argparse
import gc
import json
import time
from collections import defaultdict,Counter
from pathlib import Path

from .core import ROOT,read_jsonl,recover_jsonl,write_json,write_jsonl,append_jsonl,digest,ensure_run_manifest,prompt_ids
from .formal_common import protocol,messages,sha,utc,gpu_lock,WorkBudget,verify_fixed
from .fpr_pattern_diagnostics import load as late_rows,group_name,CONFIG as LATE_CONFIG
from .g3_common import RUN as LATE_RUN,pool
from .prefix_paraphrase import PLAN as PARAPHRASE_PLAN,RUN as PARAPHRASE_RUN,prefix_variants,validate as paraphrase_validate
from .efp_intervention import WORK as EFP_RUN,PLAN as EFP_PLAN
from .day2_protocol import label_response

RUN=ROOT/'runs/cross_round_prefix_20261025'
REPORT=ROOT/'reports/cross_round_prefix_20261025'
PLAN=ROOT/'configs/cross_round_prefix_20261025.json'
ROUNDS=['G0','G1','G2','G3']


def variants():
    old=prefix_variants();by={v['name']:v for v in old}
    names=['original','blank_control','neutral_control','to_solve_for','we_need_to',
        'we_can','we_will','we_have_to','we_should','we_must','it_is_essential_to',
        'thought_process','reasoning','analysis','original_step_by_step','reason_step_by_step']
    out=[by[n] for n in names]
    out += [{'name':'solve_for_the','prefix':'solve for the\n','category':'EFP_discovered'},
            {'name':'to_determine_probability','prefix':'To determine the probability\n','category':'EFP_discovered'},
            {'name':'symbol_colon','prefix':':\n','category':'punctuation'},
            {'name':'symbol_period','prefix':'.\n','category':'punctuation'},
            {'name':'symbol_comma','prefix':',\n','category':'punctuation'}]
    assert len(out)==len({v['prefix'] for v in out})==len({v['name'] for v in out})==21
    return out


def source():
    rows=late_rows();paths=[LATE_CONFIG,PARAPHRASE_PLAN,EFP_PLAN,PARAPHRASE_RUN/'V0_scores.jsonl',EFP_RUN/'EFP_V0_scores.jsonl']
    for g in ['G0','G1']:
        for split in ['A','C']:
            directory=ROOT/'runs/formal';allowed={r['question_id'] for r in pool(split)}
            paths += [directory/f'{p}{g}_{split}.jsonl' for p in ['', 'V0_', 'labels_']]
            vv={r['candidate_id']:r for r in read_jsonl(directory/f'V0_{g}_{split}.jsonl')}
            ll={r['candidate_id']:r for r in read_jsonl(directory/f'labels_{g}_{split}.jsonl')}
            rr=[r for r in read_jsonl(directory/f'{g}_{split}.jsonl') if r['question_id'] in allowed]
            assert len(rr)==4*len(allowed)
            for r in rr:
                s=vv[r['candidate_id']];lab=ll[r['candidate_id']]
                assert s['model_fingerprint']==protocol()['models']['V']['fingerprint'] and s['adapter_fingerprint'] is None and s['threshold']==.5
                assert s['question_id']==r['question_id']==lab['question_id']
                assert s['prompt_sha256']==digest(messages('V',r['question'],r['text']))
                assert s['accepted']==(s['score']>=.5)
                rows.append({**r,**lab,'G_round':g,'split':split,'accepted':s['accepted'],'score':s['score']})
    paths += [LATE_RUN/f'{p}{g}_{s}.jsonl' for p in ['', 'V0_', 'labels_'] for g in ['G2','G3'] for s in ['A','C']]
    for r in rows:r['source_engine']='Transformers' if r['G_round'] in ['G0','G1'] else 'vLLM'
    return rows,paths


def choose(rows,split,cap):
    eligible={g:defaultdict(list) for g in ROUNDS}
    for r in rows:
        if r['split']!=split or r['correct'] is not False or r['accepted'] or r['truncated']:continue
        if split=='A' and group_name(r['question_id'])!='A_confirmation':continue
        eligible[r['G_round']][r['question_id']].append(r)
    common=set.intersection(*(set(eligible[g]) for g in ROUNDS))
    qs=sorted(common,key=lambda q:digest('cross-round-prefix-20261025:'+q))[:cap]
    picked=[]
    for q in qs:
        for g in ROUNDS:
            # Same frozen per-candidate hash as original EFP; independent of score margin and new results.
            picked.append(min(eligible[g][q],key=lambda r:digest('EFP-candidate:'+r['candidate_id'])))
    return picked,{'common_questions':len(common),'selected_questions':len(qs),
                   'eligible_questions':{g:len(eligible[g]) for g in ROUNDS},'question_ids':qs}


def validate():
    cfg=json.loads(PLAN.read_text());verify_fixed();paraphrase_validate()
    assert all(sha(ROOT/n)==h for n,h in cfg['source_sha256'].items())
    assert all(sha(RUN/n)==h for n,h in cfg['input_sha256'].items())
    return cfg


def prepare():
    RUN.mkdir(exist_ok=True);REPORT.mkdir(exist_ok=True)
    if PLAN.exists():return validate()
    verify_fixed();paraphrase_validate();rr,paths=source();vv=variants();picked=[];support={}
    for split,cap in [('A',64),('C',48)]:
        chosen,counts=choose(rr,split,cap);picked+=chosen;support[split]=counts
    from transformers import AutoTokenizer
    tok=AutoTokenizer.from_pretrained(protocol()['models']['V']['path'],local_files_only=True)
    checks=[]
    for r in picked:
        old=label_response(r['text'],False,r['gold_answer'],'gsm8k')
        assert old['correct'] is False and old['value']==r['extracted_answer']
        for v in vv:
            text=v['prefix']+r['text'];new=label_response(text,False,r['gold_answer'],'gsm8k')
            assert all(new[k]==old[k] for k in ['correct','value','status'])
            assert len(prompt_ids(tok,messages('V',r['question'],text)))+4<=8192
        checks.append({'candidate_id':r['candidate_id'],'wrong_final_answer_preserved':True,'all21_full_contexts_fit':True})
    write_jsonl(RUN/'answers.jsonl',picked);write_jsonl(RUN/'answer_context_checks.jsonl',checks)
    write_json(RUN/'support.json',support)
    paths += [Path(__file__),ROOT/'src/pilot/runtime.py']
    cfg={'created_at':utc(),'purpose':'Compare fixed-V0 prefix flip rates on outputs from G0/G1/G2/G3',
        'support':support,'variants':vv,'source_sha256':{str(p.relative_to(ROOT)):sha(p) for p in paths},
        'input_sha256':{p.name:sha(p) for p in RUN.glob('*.jsonl')},
        'selection':'Intersection of questions with >=1 verified wrong, originally V0-rejected, nontruncated answer in ALL4 stages; one hash-selected answer per question per stage. A only previous confirmation half. Fixed question hash capA64/C48; no score-margin tuning.',
        'engine_boundary':'G0/G1 original Transformers, G2/G3 vLLM; four-stage curve descriptive across G1->G2 boundary. Primary within-engine G2->G3; secondary within-engine G0->G1.',
        'prefix_scope':'All original12 EFP settings plus9 discussed verbal/synonym/header variants;21 total including3 controls; no new mined prefixes',
        'fixed_V0':{'fingerprint':protocol()['models']['V']['fingerprint'],'adapter':None,'threshold':.5,'max_context':8192,'scoring':protocol()['V_scoring']},
        'metrics':'Conditional TN->FP flip rate; NOT full-pool FPR. Same questions across stages, different initially rejected wrong answers.',
        'contrasts':'G3-G2 primary and G1-G0 secondary; paired whole-question bootstrap and two-sided exact McNemar, BH across18 triggers*2pools separately per round contrast. G3-G0 descriptive engine-confounded only.',
        'interaction':'(trigger-neutral)_after minus (trigger-neutral)_before; paired bootstrap descriptive',
        'baseline_checks':'Every chosen original freshly rescored and matched to historical score and rejection; label and full context verified for all variants',
        'reuse':'Only exact prefix/body/question full-prompt matches with original V0 fingerprints/runtime; no original-control reuse',
        'limits':['Conditioning on TN in all stages selects a common rejected-error subpopulation, not all model errors',
                  'Error content, final wrong answer and original rejection confidence can still differ within the same question',
                  'C repeatedly used development, not untouched final test','21 prefixes fixed but chosen from prior exploratory findings; not a fresh confirmatory prefix discovery'],
        'bootstrap':10000,'seed':20261025,'counts':{'answers':len(picked),'logical_scores':len(picked)*len(vv)},
        'B_used':False,'T_read':False,'G_generation':False,'G_training':False,'V_update':False}
    ensure_run_manifest(PLAN,cfg);validate();write_json(RUN/'state.json',{'status':'prepared','counts':cfg['counts'],'support':support})
    return cfg


def reusable(cfg):
    out={};fields=['log_likelihood_A','log_likelihood_B','score','accepted','label_ids','prompt_tokens']
    for s in read_jsonl(PARAPHRASE_RUN/'V0_scores.jsonl'):
        if s['arm']!='prefix':continue
        assert s['model_fingerprint']==cfg['fixed_V0']['fingerprint'] and s['adapter_fingerprint'] is None and s['threshold']==.5
        out[s['prompt_sha256']]={**{k:s[k] for k in fields},'text_sha256':s['edited_text_sha256'],'source':'previous_paraphrase_exact_prefix'}
    answers={r['candidate_id']:r for r in read_jsonl(EFP_RUN/'EFP_probe_answers.jsonl')}
    for s in read_jsonl(EFP_RUN/'EFP_V0_scores.jsonl'):
        assert s['model_fingerprint']==cfg['fixed_V0']['fingerprint'] and s['adapter_fingerprint'] is None and s['threshold']==.5
        r=answers[s['candidate_id']];text=s['prefix']+r['text'];ph=digest(messages('V',r['question'],text))
        assert s['intervened_body_sha256']==digest(text)
        out[ph]={**{k:s[k] for k in fields},'text_sha256':digest(text),'source':'previous_EFP_exact_prefix'}
    return out


def run():
    import torch
    from .runtime import load,model_identity,conditional_label_scores
    cfg=prepare();rr=read_jsonl(RUN/'answers.jsonl');out=RUN/'V0_scores.jsonl'
    saved=recover_jsonl(out);done={s['cache_key'] for s in saved};assert len(done)==len(saved)
    previous=reusable(cfg);fresh=reused_count=0
    with gpu_lock():
        budget=WorkBudget('cross_round_prefix/fixed_V0');start=time.perf_counter()
        model,tok=load('V');fp,ad=model_identity('V');assert fp==cfg['fixed_V0']['fingerprint'] and ad is None
        for i,r in enumerate(rr):
            for v in cfg['variants']:
                key=digest({'plan':sha(PLAN),'candidate_id':r['candidate_id'],'variant':v['name']})
                if key in done:continue
                budget.check();text=v['prefix']+r['text'];msgs=messages('V',r['question'],text);ph=digest(msgs);th=digest(text)
                old=previous.get(ph) if v['name']!='original' else None
                if old:
                    assert old['text_sha256']==th
                    sc={k:old[k] for k in ['log_likelihood_A','log_likelihood_B','score','accepted','label_ids','prompt_tokens']}
                    origin=old['source'];reused_count+=1
                else:
                    n=len(prompt_ids(tok,msgs));assert n+4<=8192
                    sc=conditional_label_scores(model,tok,msgs,cfg['fixed_V0']['scoring']['labels']);origin='new_original_V0_score';fresh+=1
                assert sc['prompt_tokens']+4<=8192
                if v['name']=='original':assert sc['accepted'] is False and abs(sc['score']-r['score'])<2e-6
                append_jsonl(out,{'question_id':r['question_id'],'candidate_id':r['candidate_id'],'split':r['split'],
                    'G_round':r['G_round'],'source_engine':r['source_engine'],'variant':v['name'],'prefix':v['prefix'],
                    'correct':False,'source_score':r['score'],'source_accepted':False,**sc,'source':origin,
                    'text_sha256':th,'prompt_sha256':ph,'cache_key':key,'model_fingerprint':fp,
                    'adapter_fingerprint':None,'threshold':.5,'recorded_at':utc()})
                done.add(key)
                previous[ph]={**sc,'text_sha256':th,'source':'same_run_exact_complete_prompt'}
                if origin=='new_original_V0_score':budget.charge(r['candidate_id']+'/'+v['name'])
            write_json(RUN/'state.json',{'status':'scoring','answers_done':i+1,'answers_total':len(rr),'scores':len(done),
                'planned_scores':cfg['counts']['logical_scores'],'fresh':fresh,'reused':reused_count})
            if (i+1)%8==0:print(f'Cross-round prefix {i+1}/{len(rr)} answers, {len(done)}/{cfg["counts"]["logical_scores"]} scores; new={fresh}',flush=True)
        assert len(done)==cfg['counts']['logical_scores'] and model_identity('V')==(fp,None)
        write_json(RUN/'compute.json',{'seconds_this_invocation':time.perf_counter()-start,'new_scores_this_invocation':fresh,
            'reused_this_invocation':reused_count,'logical_scores':len(done),'V0_unchanged':True})
        budget.charge('finalization');del model;gc.collect();torch.cuda.empty_cache()
    validate()
    from .cross_round_prefix_report import main as report
    report()


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['prepare','run']);a=p.parse_args()
    try:
        if a.stage=='prepare':
            cfg=prepare();print(json.dumps({'counts':cfg['counts'],'support':{s:{k:v for k,v in d.items() if k!='question_ids'} for s,d in cfg['support'].items()}},ensure_ascii=False))
        else:run()
    except Exception as e:
        write_json(RUN/'state.json',{'status':'failed','error':str(e),'at':utc(),'resume':'scripts/pilot cross_round_prefix run'});raise

if __name__=='__main__':main()
