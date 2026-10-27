"""Complete G3 correct-answer prefix intervention; fixed original V0 scorer."""
import argparse
import gc
import json
import time
from pathlib import Path

from .core import ROOT,read_jsonl,recover_jsonl,write_json,write_jsonl,append_jsonl,digest,ensure_run_manifest,prompt_ids
from .formal_common import protocol,messages,sha,utc,gpu_lock,WorkBudget,verify_fixed
from .fpr_pattern_diagnostics import load as source_rows,CONFIG as SOURCE_CONFIG
from .cross_round_prefix import variants
from .day2_protocol import label_response

RUN=ROOT/'runs/prefix_fnr_20261025'
REPORT=ROOT/'reports/prefix_fnr_20261025'
PLAN=ROOT/'configs/prefix_fnr_20261025.json'


def correct_population(rows):
    """Do not condition selection on the original V decision."""
    return sorted([r for r in rows if r['G_round']=='G3' and r['correct'] is True and not r['truncated']],
                  key=lambda r:(r['split'],r['question_id'],r['candidate_id']))


def validate():
    cfg=json.loads(PLAN.read_text());verify_fixed()
    assert all(sha(ROOT/n)==h for n,h in cfg['source_sha256'].items())
    assert all(sha(RUN/n)==h for n,h in cfg['input_sha256'].items())
    return cfg


def prepare():
    RUN.mkdir(exist_ok=True);REPORT.mkdir(exist_ok=True)
    if PLAN.exists():return validate()
    verify_fixed();rr=correct_population(source_rows());vv=variants()
    from transformers import AutoTokenizer
    tok=AutoTokenizer.from_pretrained(protocol()['models']['V']['path'],local_files_only=True)
    checks=[]
    for i,r in enumerate(rr):
        before=label_response(r['text'],False,r['gold_answer'],'gsm8k')
        assert before['correct'] is True and before['value']==r['extracted_answer']
        lengths=[]
        for v in vv:
            after=label_response(v['prefix']+r['text'],False,r['gold_answer'],'gsm8k')
            assert all(before[k]==after[k] for k in ['correct','value','status'])
            n=len(prompt_ids(tok,messages('V',r['question'],v['prefix']+r['text'])))
            assert n+4<=8192;lengths.append(n)
        checks.append({'candidate_id':r['candidate_id'],'all21_answers_and_labels_preserved':True,'max_V_prompt_tokens':max(lengths)})
        if (i+1)%100==0:print(f'prepare {i+1}/{len(rr)}',flush=True)
    support={s:{'responses':sum(r['split']==s for r in rr),'questions':len({r['question_id'] for r in rr if r['split']==s}),
                'original_FN':sum(r['split']==s and not r['accepted'] for r in rr),
                'original_TP':sum(r['split']==s and r['accepted'] for r in rr)} for s in ['A','C']}
    assert support['A']['responses']==839 and support['C']['responses']==147
    write_jsonl(RUN/'answers.jsonl',rr);write_jsonl(RUN/'answer_context_checks.jsonl',checks)
    paths=[SOURCE_CONFIG,ROOT/'src/pilot/runtime.py',ROOT/'src/pilot/cross_round_prefix.py',Path(__file__)]
    source_cfg=json.loads(SOURCE_CONFIG.read_text())
    source_hashes={str(p.relative_to(ROOT)):sha(p) for p in paths}|source_cfg['source_sha256']
    cfg={'created_at':utc(),'purpose':'Measure net FNR change under the same21 prefix settings, including FN rescue and TP damage',
         'scope':'ALL independently verified correct nontruncated G3 answers in frozen clean A598/C95; no conditioning on original V acceptance, words or future intervention scores',
         'support':support,'variants':vv,'source_sha256':source_hashes,
         'input_sha256':{p.name:sha(p) for p in RUN.glob('*.jsonl')},
         'fixed_V0':{'fingerprint':protocol()['models']['V']['fingerprint'],'adapter':None,'threshold':.5,
                     'scoring':protocol()['V_scoring'],'max_context':8192},
         'primary':'FNR(prefix)-FNR(original), 18 triggers x2 pools, two-sided question-cluster influence Student-t test, BH36; paired whole-question bootstrap10000',
         'secondary':'FNR(prefix)-FNR(neutral_control), separate BH36; exact response-level McNemar descriptive only because sibling answers correlated',
         'transitions':'All65A/21C original FN can become TP; all774A/126C original TP can become FN. Net FN=new_FN-rescued_FN. Include both groups without balancing or duplication.',
         'reuse':'Original controls freshly rescored for every candidate; nonoriginal scores reused only for identical full question+prefix+body prompts inside this run',
         'comparison':'Prior G3 wrong-answer injection nA64/C48 shown separately as conditional TN->FP, not whole-pool FPR. Different question coverage and denominators; not a joint accuracy experiment.',
         'limits':['C repeated-use development, not final test','A G training domain; descriptive G3-only intervention, not proof of training mediation',
                   'Final-answer reference labels not proof all reasoning correct','Zero observed flips or degenerate bootstrap is not population zero risk',
                   'Prefix list inherited from prior exploratory findings; report all21 settings and controls'],
         'bootstrap':10000,'seed':20261025,'logical_scores':len(rr)*len(vv),
         'G_generation':False,'G_training':False,'V_update':False,'B_used':False,'T_read':False}
    ensure_run_manifest(PLAN,cfg);validate()
    write_json(RUN/'state.json',{'status':'prepared','support':support,'planned_scores':cfg['logical_scores']})
    return cfg


def run():
    import torch
    from .runtime import load,model_identity,conditional_label_scores
    cfg=prepare();rr=read_jsonl(RUN/'answers.jsonl');out=RUN/'V0_scores.jsonl'
    saved=recover_jsonl(out);done={s['cache_key'] for s in saved};assert len(done)==len(saved)
    ensure_run_manifest(RUN/'runtime.manifest.json',{'plan_sha256':sha(PLAN),'runner_sha256':sha(Path(__file__)),
                                                  'runtime_sha256':sha(ROOT/'src/pilot/runtime.py')})
    cache={s['prompt_sha256']:s for s in saved};fresh=reused=0
    with gpu_lock():
        budget=WorkBudget('prefix_fnr/fixed_V0');start=time.perf_counter()
        model,tok=load('V');fp,ad=model_identity('V');assert fp==cfg['fixed_V0']['fingerprint'] and ad is None
        for i,r in enumerate(rr):
            for v in cfg['variants']:
                key=digest({'plan':sha(PLAN),'candidate_id':r['candidate_id'],'variant':v['name']})
                if key in done:continue
                budget.check();text=v['prefix']+r['text'];msgs=messages('V',r['question'],text);ph=digest(msgs)
                old=cache.get(ph) if v['name']!='original' else None
                if old:
                    assert old['text_sha256']==digest(text)
                    sc={k:old[k] for k in ['log_likelihood_A','log_likelihood_B','score','accepted','label_ids','prompt_tokens']}
                    origin='same_run_exact_full_prompt';reused+=1
                else:
                    n=len(prompt_ids(tok,msgs));assert n+4<=8192
                    sc=conditional_label_scores(model,tok,msgs,cfg['fixed_V0']['scoring']['labels'])
                    origin='new_original_V0_score';fresh+=1
                assert sc['prompt_tokens']+4<=8192 and sc['accepted']==(sc['score']>=.5)
                if v['name']=='original':assert sc['accepted']==r['accepted'] and abs(sc['score']-r['score'])<2e-6
                s={'question_id':r['question_id'],'candidate_id':r['candidate_id'],'split':r['split'],'G_round':'G3',
                   'variant':v['name'],'prefix':v['prefix'],'correct':True,'source_score':r['score'],
                   'source_accepted':r['accepted'],**sc,'source':origin,'text_sha256':digest(text),
                   'prompt_sha256':ph,'cache_key':key,'model_fingerprint':fp,'adapter_fingerprint':None,'threshold':.5,'at':utc()}
                append_jsonl(out,s);cache[ph]=s;done.add(key)
                if not old:budget.charge(r['candidate_id']+'/'+v['name'])
            write_json(RUN/'state.json',{'status':'scoring','answers_done':i+1,'answers_total':len(rr),'scores':len(done),
                       'planned_scores':cfg['logical_scores'],'new_this_invocation':fresh,'reused_this_invocation':reused,'at':utc()})
            if (i+1)%5==0:print(f'correct answers {i+1}/{len(rr)}; scores {len(done)}/{cfg["logical_scores"]}',flush=True)
        assert len(done)==cfg['logical_scores'] and model_identity('V')==(fp,ad)
        write_json(RUN/'compute.json',{'seconds_this_invocation':time.perf_counter()-start,'new_scores_this_invocation':fresh,
                   'reused_this_invocation':reused,'logical_scores':len(done),'V0_unchanged':True})
        budget.charge('finalization');del model;gc.collect();torch.cuda.empty_cache()
    validate()
    from .prefix_fnr_report import main as report
    report()


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['prepare','run']);args=p.parse_args()
    try:
        if args.stage=='prepare':print(json.dumps(prepare()['support'],ensure_ascii=False))
        else:run()
    except Exception as e:
        RUN.mkdir(exist_ok=True);write_json(RUN/'state.json',{'status':'failed','error':str(e),'at':utc(),'resume':'scripts/pilot prefix_fnr run'});raise


if __name__=='__main__':main()
