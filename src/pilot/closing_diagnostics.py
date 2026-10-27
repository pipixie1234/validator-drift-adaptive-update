"""Close lexical diagnostics with training-chain, symmetric edits and answer controls."""
import argparse
import gc
import json
import re
import time
from collections import Counter
from pathlib import Path

from .core import ROOT, read_jsonl, recover_jsonl, write_json, write_jsonl, append_jsonl, digest, ensure_run_manifest, prompt_ids
from .formal_common import protocol, messages, sha, utc, gpu_lock, WorkBudget, verify_fixed
from .fpr_pattern_diagnostics import load as source_rows, CONFIG as SOURCE_CONFIG
from .g3_common import RUN as G3_RUN
from .natural_invariance import RUN as NATURAL_RUN, PLAN as NATURAL_PLAN, intervention_span, edited, validate as natural_validate
from .prefix_paraphrase import RUN as PARAPHRASE_RUN, PLAN as PARAPHRASE_PLAN
from .day2_protocol import label_response, boxes, numeric

RUN=ROOT/'runs/closing_diagnostics_20261025_v2'
REPORT=ROOT/'reports/closing_diagnostics_20261025'
PLAN=ROOT/'configs/closing_diagnostics_20261025_v2.json'
SELECTED=G3_RUN/'stage_train/train/G2/selected.jsonl'
SELECTION_MANIFEST=G3_RUN/'stage_train/train/G2/selection.manifest.json'
CACHE_PATHS=[PARAPHRASE_RUN/'V0_scores.jsonl',NATURAL_RUN/'V0_scores.jsonl']
PATTERNS=['we need to','to solve for','solve for the','to determine the probability',
          'we can','we will','we must','it is essential to',
          "let's reason step by step","let's solve this problem step by step",'reasoning','analysis','thought process']


def natural_features(text):
    from .awl_comparison import lexical_pairs
    opening=re.split(r'[$\\\d\n]',text[:160],maxsplit=1)[0]
    words=list(re.finditer(r"[A-Za-z]+(?:['’][A-Za-z]+)?",opening))
    vals=[m.group().lower().replace('’',"'") for m in words];out={}
    for pattern in PATTERNS:
        target=pattern.split();out[pattern]=any(vals[i:i+len(target)]==target for i in range(min(12,len(vals))))
    awl={a for a,b in lexical_pairs()};non={b for a,b in lexical_pairs()}
    out['AWL_word_any']=any(w in awl for w in vals[:12]);out['nonAWL_word_any']=any(w in non for w in vals[:12])
    return out


def corrupt_final(row):
    """Replace one unambiguous boxed final value; keep every other byte."""
    text=row['text'];found=boxes(text)
    if len(found)!=1:return None,'not_one_box'
    if re.search(r'(?m)^\s*####\s*\S',text):return None,'additional_hash_assertion'
    f=found[0];value=numeric(f['value']);n=int(value)
    if str(n)!=value:return None,'not_integer'
    wrong=n+1;a=f['end']-1-len(f['value']);b=f['end']-1
    assert text[a:b]==f['value']
    changed=text[:a]+str(wrong)+text[b:]
    lab=label_response(changed,False,row['gold_answer'],'gsm8k')
    assert lab['correct'] is False and numeric(lab['value'])==str(wrong)
    assert changed[:a]==text[:a] and changed[a+len(str(wrong)):]==text[b:]
    return {'text':changed,'span':[a,b],'old_value':value,'new_value':str(wrong),'new_label':lab},None


def prepare():
    RUN.mkdir(exist_ok=True);REPORT.mkdir(exist_ok=True)
    if PLAN.exists():return validate()
    verify_fixed();natural_validate();allrows=source_rows();jobs=[];checks=[]
    baseline={s['candidate_id']:s for g in ['G2','G3'] for split in ['A','C'] for s in read_jsonl(G3_RUN/f'V0_{g}_{split}.jsonl')}
    selected=read_jsonl(SELECTED);manifest=json.loads(SELECTION_MANIFEST.read_text())
    g2={r['candidate_id']:r for r in allrows if r['G_round']=='G2' and r['split']=='A'}
    assert manifest['truth_used'] is False and manifest['selected_ids']==[r['candidate_id'] for r in selected]
    assert len(selected)==511 and len({r['question_id'] for r in selected})==511
    for r in selected:
        s=g2[r['candidate_id']]
        assert r['question_id']==s['question_id'] and r['text']==s['text'] and s['accepted']
        assert r['selection_used_gold'] is False
    write_jsonl(RUN/'all_source_answers.jsonl',allrows)
    touched=[]
    for r in allrows:
        span=intervention_span(r['text'])
        if span is None:continue
        r={**r,'edit_span':span};touched.append(r)
        old=label_response(r['text'],r['truncated'],r['gold_answer'],'gsm8k')
        assert old['correct'] is r['correct'] and old['value']==r['extracted_answer']
        for v in ['original','delete','synonym']:
            text=edited(r,v);lab=label_response(text,r['truncated'],r['gold_answer'],'gsm8k')
            assert all(lab[k]==old[k] for k in ['correct','value','status'])
            jobs.append({'arm':'symmetric','candidate_id':r['candidate_id'],'question_id':r['question_id'],
                'split':r['split'],'G_round':r['G_round'],'variant':v,'correct':r['correct'],
                'question':r['question'],'text':text,'original_text_sha256':digest(r['text']),'edit_target':span['target'],
                'final_answer_preserved':True,'original_accepted':r['accepted'],'original_score':r['score']})
        checks.append({'candidate_id':r['candidate_id'],'all_surface_labels_and_statuses_preserved':True})
    tp=[r for r in read_jsonl(NATURAL_RUN/'answers.jsonl') if r['baseline_cell']=='TP'];answer_rows=[];exclusions=[]
    for r in tp:
        changed,reason=corrupt_final(r)
        if reason:
            exclusions.append({'candidate_id':r['candidate_id'],'split':r['split'],'reason':reason});continue
        assert r['correct'] is True and r['accepted'] and not r['truncated']
        answer_rows.append({**r,'final_corruption':changed})
        jobs.append({'arm':'answer_control','candidate_id':r['candidate_id'],'question_id':r['question_id'],
            'split':r['split'],'G_round':'G3','variant':'wrong_final_answer','correct':False,
            'question':r['question'],'text':changed['text'],'original_text_sha256':digest(r['text']),
            'old_final_value':changed['old_value'],'new_final_value':changed['new_value'],
            'original_accepted':True,'original_score':r['score'],'final_answer_preserved':False})
    # Fresh sentinels for each available round/pool/truth/decision; other originals use provenance-checked caches.
    sentinel=set()
    for g in ['G2','G3']:
        for split in ['A','C']:
            for truth in [True,False,None]:
                for accepted in [True,False]:
                    rr=[r for r in touched if r['G_round']==g and r['split']==split and r['correct'] is truth and r['accepted']==accepted]
                    if rr:sentinel.add(min(rr,key=lambda r:digest('closing-sentinel:'+r['candidate_id']))['candidate_id'])
    write_jsonl(RUN/'surface_answers.jsonl',touched);write_jsonl(RUN/'answer_control_answers.jsonl',answer_rows)
    write_jsonl(RUN/'jobs.jsonl',jobs);write_jsonl(RUN/'label_checks.jsonl',checks)
    write_json(RUN/'answer_control_exclusions.json',exclusions)
    paths=[SOURCE_CONFIG,NATURAL_PLAN,PARAPHRASE_PLAN,SELECTED,SELECTION_MANIFEST,Path(__file__),
           ROOT/'src/pilot/natural_invariance.py',ROOT/'src/pilot/runtime.py',*CACHE_PATHS]
    paths += [G3_RUN/f'V0_{g}_{s}.jsonl' for g in ['G2','G3'] for s in ['A','C']]
    cfg={'created_at':utc(),'purpose':'Close three outstanding diagnostics before moving on; not an additional G update',
        'source_sha256':{str(p.relative_to(ROOT)):sha(p) for p in paths},
        'input_sha256':{p.name:sha(p) for p in RUN.glob('*.jsonl')},
        'chain':{'primary':'Same-engine G2 A598 -> fixedV0 accepted -> actual511 SFT examples -> G3 A598',
                 'patterns':PATTERNS+['AWL_word_any','nonAWL_word_any'],
                 'window':'First160chars, stop at first formula/digit/newline; starts among first12 words; case-insensitive exact word sequences',
                 'question_matched':'Repeat chain on actual selected511 questions, average within question before averaging questions; paired bootstrap',
                 'truth_role':'Join independent truth for quality records only, never select/retrain by truth',
                 'original_G0_G1':'Not mixed into primary G2/G3 because generation engine changed'},
        'symmetric':{'rules':'Exact previous intervention_span/edited functions; one first eligible opening span',
                     'all_cells':'All naturally matching answers: TP/FN/FP/TN and unknown; no decision-based sample filter',
                     'nonmatching':'Unchanged full-pool cached V0 scores; full A598 and C95 denominators',
                     'variants':['original','delete','synonym'],
                     'contrast':'FPR_G3-FPR_G2 before/after same edit; reduction = original gap minus edited gap. Descriptive counterfactual, not identified training mediation.'},
        'answer_control':{'cohort':'Same G3 natural TP cohort as preceding invariance experiment; single box and no additional hash assertion',
                          'change':'Change only boxed integer value n to n+1, preserving all other bytes; independently verify new final answer is wrong; no unjustified 0..999 restriction',
                          'comparison':'On exactly same eligible TP answers, compare sensitivity to final-answer corruption vs deletion/synonym; originals accepted',
                          'limit':'Reasoning may now contradict final number; final-answer-only judging protocol still fixes label; no claim of full solution validation',
                          'exclusions':exclusions},
        'fixed_V0':{'fingerprint':protocol()['models']['V']['fingerprint'],'adapter':None,'threshold':.5,'scoring':protocol()['V_scoring'],'max_context':8192},
        'counts':{'all_source':len(allrows),'surface_answers':len(touched),'answer_controls':len(answer_rows),'logical_jobs':len(jobs),
                  'fresh_original_sentinels':len(sentinel)},'fresh_sentinel_candidate_ids':sorted(sentinel),
        'bootstrap':10000,'seed':20261025,'intervals':'Paired whole questions, exploratory pointwise intervals; training-seed variation not included',
        'preparation_revision':{'superseded_unscored_plan':'configs/closing_diagnostics_20261025.json',
            'reason':'Inspecting existing references revealed valid integers outside0..999; before any new V scoring remove that unnecessary eligibility restriction. Old preparation and source snapshot preserved.'},
        'B_used':False,'T_read':False,'G_generation':False,'G_training':False,'V_update':False}
    ensure_run_manifest(PLAN,cfg);validate();write_json(RUN/'state.json',{'status':'prepared',**cfg['counts']})
    return cfg


def validate():
    cfg=json.loads(PLAN.read_text());verify_fixed();natural_validate()
    assert all(sha(ROOT/n)==h for n,h in cfg['source_sha256'].items())
    assert all(sha(RUN/n)==h for n,h in cfg['input_sha256'].items())
    return cfg


def reusable_scores(cfg):
    lookup={};fields=['log_likelihood_A','log_likelihood_B','score','accepted','label_ids','prompt_tokens']
    for path in CACHE_PATHS:
        for r in read_jsonl(path):
            assert r['model_fingerprint']==cfg['fixed_V0']['fingerprint'] and r['adapter_fingerprint'] is None and r['threshold']==.5
            key=r['prompt_sha256']
            lookup[key]={**{k:r[k] for k in fields},'reuse_path':str(path.relative_to(ROOT)),
                         'text_sha256':r.get('edited_text_sha256',r.get('text_sha256'))}
    for g in ['G2','G3']:
        for split in ['A','C']:
            path=G3_RUN/f'V0_{g}_{split}.jsonl'
            for r in read_jsonl(path):
                assert r['model_fingerprint']==cfg['fixed_V0']['fingerprint'] and r['adapter_fingerprint'] is None and r['threshold']==.5
                lookup[r['prompt_sha256']]={**{k:r[k] for k in fields},'reuse_path':str(path.relative_to(ROOT)),'text_sha256':None}
    return lookup


def run():
    import torch
    from .runtime import load,model_identity,conditional_label_scores
    cfg=prepare();jobs=read_jsonl(RUN/'jobs.jsonl');out=RUN/'V0_scores.jsonl'
    done={r['cache_key'] for r in recover_jsonl(out)};lookup=reusable_scores(cfg)
    fresh=reused=0;sentinels=set(cfg['fresh_sentinel_candidate_ids'])
    with gpu_lock():
        budget=WorkBudget('closing_diagnostics/fixed_V0');start=time.perf_counter()
        model,tok=load('V');fp,ad=model_identity('V');assert fp==cfg['fixed_V0']['fingerprint'] and ad is None
        for i,j in enumerate(jobs):
            key=digest({'plan':sha(PLAN),'arm':j['arm'],'candidate_id':j['candidate_id'],'variant':j['variant']})
            if key in done:continue
            budget.check();msgs=messages('V',j['question'],j['text']);ph=digest(msgs);th=digest(j['text'])
            prior=lookup.get(ph);force=j['variant']=='original' and j['candidate_id'] in sentinels
            if prior and not force:
                assert prior['text_sha256'] in [None,th]
                sc={k:prior[k] for k in ['log_likelihood_A','log_likelihood_B','score','accepted','label_ids','prompt_tokens']}
                origin=prior['reuse_path'];reused+=1
            else:
                n=len(prompt_ids(tok,msgs));assert n+4<=8192
                sc=conditional_label_scores(model,tok,msgs,cfg['fixed_V0']['scoring']['labels']);fresh+=1;origin='new_original_V0_score'
            assert sc['prompt_tokens']+4<=8192
            if j['variant']=='original':assert sc['accepted']==j['original_accepted'] and abs(sc['score']-j['original_score'])<2e-6
            record={k:j[k] for k in ['arm','candidate_id','question_id','split','G_round','variant','correct','original_accepted','original_score']}
            append_jsonl(out,{**record,**sc,'source':origin,'model_fingerprint':fp,'adapter_fingerprint':None,
                'threshold':.5,'text_sha256':th,'prompt_sha256':ph,'cache_key':key,'recorded_at':utc()})
            done.add(key);lookup[ph]={**sc,'reuse_path':'same_run_identical_complete_prompt','text_sha256':th}
            if origin=='new_original_V0_score':budget.charge(j['arm']+'/'+j['candidate_id']+'/'+j['variant'])
            if (i+1)%100==0:
                write_json(RUN/'state.json',{'status':'scoring','jobs_done':len(done),'planned_jobs':len(jobs),'fresh':fresh,'reused':reused})
                print(f'Closing diagnostics {len(done)}/{len(jobs)}; fresh={fresh}, reused={reused}',flush=True)
        assert len(done)==len(jobs) and model_identity('V')==(fp,None)
        write_json(RUN/'compute.json',{'seconds_this_invocation':time.perf_counter()-start,'new_scores_this_invocation':fresh,
                  'reused_this_invocation':reused,'logical_scores':len(done),'V0_unchanged':True})
        budget.charge('finalization');del model;gc.collect();torch.cuda.empty_cache()
    validate()
    from .closing_diagnostics_report import main as report
    report()


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['prepare','run']);a=p.parse_args()
    try:
        if a.stage=='prepare':print(json.dumps(prepare()['counts'],ensure_ascii=False))
        else:run()
    except Exception as e:
        write_json(RUN/'state.json',{'status':'failed','error':str(e),'at':utc(),'resume':'scripts/pilot closing_diagnostics run'});raise

if __name__=='__main__':main()
