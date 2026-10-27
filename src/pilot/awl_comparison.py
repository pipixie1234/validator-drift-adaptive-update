"""Prespecified AWL/non-AWL lexical pairs on unchanged wrong G3 answers."""
import argparse
import gc
import json
import re
import time
from collections import Counter
from pathlib import Path

from .core import ROOT, read_jsonl, recover_jsonl, write_json, write_jsonl, append_jsonl, digest, ensure_run_manifest, prompt_ids
from .formal_common import protocol, messages, sha, utc, gpu_lock, WorkBudget, verify_fixed
from .day2_protocol import label_response
from .prefix_paraphrase import RUN as PRIOR, PLAN as PRIOR_PLAN, validate as prior_validate

RUN=ROOT/'runs/awl_comparison_20261024'
REPORT=ROOT/'reports/awl_comparison_20261024'
PLAN=ROOT/'configs/awl_comparison_20261024.json'


def lexical_pairs():
    # Fixed without inspecting any new scores. All single words, same POS.
    return [('analyse','examine'),('assess','check'),('compute','calculate'),
      ('deduce','reason'),('demonstrate','show'),('establish','prove'),
      ('obtain','get'),('require','need'),('identify','find'),('evaluate','judge'),
      ('formulate','write'),('utilise','use'),('implement','do'),('conclude','finish'),
      ('investigate','study'),('clarify','explain'),('commence','begin'),
      ('proceed','continue'),('assist','help'),('retain','keep')]


def membership():
    families=json.loads((RUN/'awl_570_transcription.json').read_text())
    assert len(families)==570 and len({r['headword'] for r in families})==570
    assert Counter(r['sublist'] for r in families)=={**{i:60 for i in range(1,10)},10:30}
    out={}
    for f in families:
        assert f['headword'] in f['forms']
        for word in f['forms']:
            assert re.fullmatch(r'[A-Za-z-]+',word),word
            out.setdefault(word.lower(),[]).append({'headword':f['headword'],'sublist':f['sublist']})
    return out


def variants():
    members=membership()
    vv=[{'name':n,'prefix':p,'group':'control','word':None,'pair':None,'awl_families':[]}
        for n,p in [('original',''),('colon_control',':\n'),('neutral_control','Response text:\n')]]
    for i,(awl,non) in enumerate(lexical_pairs()):
        assert awl in members and non not in members,(awl,non)
        for group,word in [('AWL',awl),('nonAWL',non)]:
            vv.append({'name':word,'prefix':word.capitalize()+':\n','group':group,'word':word,
                       'pair':i,'awl_families':members.get(word,[])})
    assert len(vv)==43 and len({v['name'] for v in vv})==43
    assert len({v['prefix'] for v in vv})==43
    return vv


def validate():
    cfg=json.loads(PLAN.read_text());verify_fixed();prior_validate()
    assert all(sha(ROOT/p)==h for p,h in cfg['source_sha256'].items())
    assert sha(RUN/'answers.jsonl')==cfg['answers_sha256']
    assert cfg['variants']==variants()
    return cfg


def prepare():
    RUN.mkdir(exist_ok=True);REPORT.mkdir(exist_ok=True)
    if PLAN.exists():return validate()
    prior_validate();vv=variants();rr=read_jsonl(PRIOR/'prefix_answers.jsonl')
    assert Counter(r['split'] for r in rr)=={'A':64,'C':48}
    assert len({r['question_id'] for r in rr})==112
    checks=[]
    for r in rr:
        assert r['correct'] is False and not r['accepted'] and not r['truncated']
        old=label_response(r['text'],False,r['gold_answer'],'gsm8k')
        assert old['correct'] is False and old['value']==r['extracted_answer']
        for v in vv:
            new=label_response(v['prefix']+r['text'],False,r['gold_answer'],'gsm8k')
            assert all(new[k]==old[k] for k in ['correct','value','status'])
        checks.append({'candidate_id':r['candidate_id'],'variants':len(vv),'answer_status_label_preserved':True})
    write_jsonl(RUN/'answers.jsonl',rr);write_jsonl(RUN/'label_preservation.jsonl',checks)
    paths=[PRIOR_PLAN,PRIOR/'prefix_answers.jsonl',PRIOR/'V0_scores.jsonl',
        RUN/'awl_source.html',RUN/'awl_570_transcription.json',RUN/'sources.json',
        ROOT/'src/pilot/runtime.py',Path(__file__),RUN/'label_preservation.jsonl']
    cfg={'created_at':utc(),'purpose':'Contrast prespecified near-synonym AWL/nonAWL verb headers, holding body and format fixed',
      'variants':vv,'pairs':[{'AWL':a,'nonAWL':b} for a,b in lexical_pairs()],
      'prefix_template':'{Capitalized_single_word}:\\n','samples':{'A':64,'C':48},'scores':112*len(vv),
      'source_sha256':{str(p.relative_to(ROOT)):sha(p) for p in paths},'answers_sha256':sha(RUN/'answers.jsonl'),
      'membership_rule':'Lowercase exact member of 570-family transcription including derived/inflected forms; no heuristic stemming',
      'lexical_selection':'20 manually chosen single-verb near-synonym pairs; membership checked; not a random sample of AWL; no new-score-driven changes',
      'main_contrast':'Per answer, mean acceptance over20 AWL headers minus mean over20 nonAWL headers; 10000 whole-question bootstrap, permutation sign flips, BH across A/C',
      'lexical_sensitivity':'Additional crossed bootstrap of questions AND lexical pairs, keeping AWL/nonAWL paired; sensitivity only, purposive vocabulary not representative',
      'individual_pairs':'Two-sided exact McNemar across same answers, BH across20*2 pools',
      'controls':'unchanged original, punctuation-only colon, neutral Response text; include all controls in plots',
      'fixed_V0':{'fingerprint':protocol()['models']['V']['fingerprint'],'adapter':None,'threshold':.5,
                    'scoring':protocol()['V_scoring'],'max_context':8192},
      'reuse':'Exact same question+response+prefix with provenance from completed previous fixed V0 experiment only. Freshly rescore every original.',
      'limits':['AWL membership is not a monotone scale of academic tone; excludes most frequent2000 English words',
                'Approximate synonyms may differ in nuance; morphology/word length/subword length/frequency not matched',
                'Artificial standalone headers, selected original wrong rejections; not natural FPR or G learning mechanism',
                '20 chosen pairs do not establish a claim about all570 families'],
      'seed':20261024,'bootstrap':10000,'B_used':False,'T_read':False,'G_training':False,'G_generation':False}
    ensure_run_manifest(PLAN,cfg);validate()
    write_json(RUN/'state.json',{'status':'prepared','planned_scores':cfg['scores']});return cfg


def run():
    import torch
    from .runtime import load, model_identity, conditional_label_scores
    cfg=prepare();out=RUN/'V0_scores.jsonl';old=recover_jsonl(out);done={s['cache_key'] for s in old}
    assert len(done)==len(old)
    prior_cfg=json.loads(PRIOR_PLAN.read_text())
    prior_prefix={v['name']:v['prefix'] for v in prior_cfg['variants']['prefix']}
    previous={(s['candidate_id'],prior_prefix[s['variant']]):s for s in read_jsonl(PRIOR/'V0_scores.jsonl') if s['arm']=='prefix'}
    answers=read_jsonl(RUN/'answers.jsonl');new_count=reused_count=0
    with gpu_lock():
        budget=WorkBudget('awl_comparison/fixed_V0');start=time.perf_counter()
        model,tok=load('V');fp,adapter=model_identity('V');assert fp==cfg['fixed_V0']['fingerprint'] and adapter is None
        for i,r in enumerate(answers):
            for v in cfg['variants']:
                key=digest({'plan':sha(PLAN),'candidate_id':r['candidate_id'],'variant':v['name']})
                if key in done:continue
                budget.check();text=v['prefix']+r['text'];msgs=messages('V',r['question'],text)
                n=len(prompt_ids(tok,msgs));assert n+4<=8192
                prev=previous.get((r['candidate_id'],v['prefix'])) if v['name']!='original' else None
                if prev:
                    assert prev['model_fingerprint']==fp and prev['adapter_fingerprint'] is None and prev['threshold']==.5
                    assert prev['edited_text_sha256']==digest(text) and prev['prompt_sha256']==digest(msgs)
                    sc={k:prev[k] for k in ['log_likelihood_A','log_likelihood_B','score','accepted','label_ids','prompt_tokens']}
                    assert sc['prompt_tokens']==n;reused_count+=1;source='identical_prior_prefix'
                else:
                    sc=conditional_label_scores(model,tok,msgs,cfg['fixed_V0']['scoring']['labels'])
                    new_count+=1;source='new_V0_score'
                if v['name']=='original':assert sc['accepted']==r['accepted'] and abs(sc['score']-r['score'])<2e-6
                append_jsonl(out,{'candidate_id':r['candidate_id'],'question_id':r['question_id'],'split':r['split'],
                    'variant':v['name'],'group':v['group'],'correct':False,**sc,'source':source,
                    'model_fingerprint':fp,'adapter_fingerprint':None,'threshold':.5,'text_sha256':digest(text),
                    'prompt_sha256':digest(msgs),'cache_key':key,'recorded_at':utc()})
                done.add(key)
                if source=='new_V0_score':budget.charge(r['candidate_id']+'/'+v['name'])
            write_json(RUN/'state.json',{'status':'scoring','answers_done':i+1,'answers_total':len(answers),
                'scores':len(done),'planned_scores':cfg['scores'],'new_this_invocation':new_count,'reused_this_invocation':reused_count})
            if (i+1)%4==0:print(f'AWL {i+1}/112 answers, {len(done)}/{cfg["scores"]} scores',flush=True)
        assert len(done)==cfg['scores'] and model_identity('V')==(fp,None)
        write_json(RUN/'compute.json',{'seconds_this_invocation':time.perf_counter()-start,'new_scores_this_invocation':new_count,
            'reused_scores_this_invocation':reused_count,'logical_scores':len(done),'V0_unchanged':True})
        budget.charge('finalization');del model;gc.collect();torch.cuda.empty_cache()
    validate()
    from .awl_comparison_report import main as report
    report()


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['prepare','run']);a=p.parse_args()
    try:
        if a.stage=='prepare':print(json.dumps({'prepared':True,'scores':prepare()['scores']}))
        else:run()
    except Exception as e:
        write_json(RUN/'state.json',{'status':'failed','error':str(e),'at':utc(),'resume':'scripts/pilot awl_comparison run'});raise

if __name__=='__main__':main()
