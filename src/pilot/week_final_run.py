"""Durable final-week pipeline: uniform T outputs, fixed verifier matrix, reasons."""
import argparse
import gc
import json
import subprocess
import time

from .core import ROOT,read_jsonl,recover_jsonl,write_json,write_jsonl,append_jsonl,digest,ensure_run_manifest
from .formal_common import messages,sha,utc,gpu_lock,WorkBudget
from .runtime import load,model_identity,conditional_label_scores
from .day2_protocol import label_response,VERSION
from .week_final_common import RUN,REPORT,PLAN,GS,VS,TLABEL,freeze,validate,unseal,vadapter,state


def label(g):
    cfg=validate();path=RUN/f'labels_{g}_T.jsonl';raw=RUN/f'{g}_T.jsonl'
    rows=read_jsonl(raw);assert len(rows)==1200
    ensure_run_manifest(path.with_suffix('.manifest.json'),{'plan_sha256':sha(PLAN),'G_sha256':sha(raw),'reference_sha256':sha(TLABEL),
       'grader_sha256':sha(ROOT/'src/pilot/day2_protocol.py')})
    if path.exists():assert len(read_jsonl(path))==1200;return
    refs={r['question_id']:r['gold_answer'] for r in read_jsonl(TLABEL)};assert len(refs)==300
    out=[]
    for r in rows:
        lab=label_response(r['text'],r['truncated'],refs[r['question_id']],'gsm8k')
        out.append({'question_id':r['question_id'],'candidate_id':r['candidate_id'],'split':'T','G_round':g,
            'correct':lab['correct'],'extracted_answer':lab['value'],'gold_answer':refs[r['question_id']],
            'verification_status':lab['status'],'unknown_reason':lab['status'] if lab['correct'] is None else None,
            'answer_format':lab.get('format'),'grader_version':VERSION+':dapo_exact_rational_v3',
            'label_source':'independent frozen source-reference final-answer comparison, never passed to G/V'})
    write_jsonl(path,out)


def score(v):
    import torch
    cfg=validate();a=vadapter(v);fp,ad=model_identity('V',a);rows=[r for g in GS for r in read_jsonl(RUN/f'{g}_T.jsonl')]
    assert len(rows)==4800 and len({r['candidate_id'] for r in rows})==4800
    d=RUN/'scoring'/v;d.mkdir(parents=True,exist_ok=True);path=d/'scores.jsonl';saved=recover_jsonl(path)
    done={r['candidate_id'] for r in saved};assert len(done)==len(saved)
    ensure_run_manifest(d/'manifest.json',{'plan_sha256':sha(PLAN),'V':v,'base':fp,'adapter':ad,
        'G_sha256':{g:sha(RUN/f'{g}_T.jsonl') for g in GS},'runtime_sha256':sha(ROOT/'src/pilot/runtime.py')})
    if len(done)==4800:return
    cache={r['prompt_sha256']:r for r in saved};fresh=reused=0
    with gpu_lock():
        budget=WorkBudget('week_final/score/'+v);tick=time.perf_counter();model,tok=load('V',a)
        for i,r in enumerate(rows):
            if r['candidate_id'] in done:continue
            budget.check();msgs=messages('V',r['question'],r['text']);ph=digest(msgs);old=cache.get(ph)
            if old:
                point={k:old[k] for k in ['score','accepted','log_likelihood_A','log_likelihood_B','prompt_tokens','label_ids']};reused+=1
            else:point=conditional_label_scores(model,tok,msgs);fresh+=1
            assert point['prompt_tokens']+4<=8192 and point['accepted']==(point['score']>=.5)
            s={'candidate_id':r['candidate_id'],'question_id':r['question_id'],'G_round':r['G_round'],'V':v,'split':'T',
                **point,'model_fingerprint':fp,'adapter_fingerprint':ad,'threshold':.5,'prompt_sha256':ph,'text_sha256':digest(r['text']),
                'source':'exact_full_prompt_same_V_cache' if old else 'new_score','plan_sha256':sha(PLAN),'at':utc()}
            append_jsonl(path,s);done.add(r['candidate_id']);cache[ph]=s
            if not old:budget.charge(r['candidate_id'])
            if len(done)%50==0:
                state('T_verifier_scoring',V=v,done=len(done),total=4800,G=r['G_round']);print(v,'T scores',len(done),'/4800',flush=True)
        assert len(done)==4800 and model_identity('V',a)==(fp,ad)
        write_json(d/'compute.json',{'seconds_this_invocation':time.perf_counter()-tick,'logical_scores':4800,'fresh':fresh,'reused':reused})
        budget.charge('finalization');del model;gc.collect();torch.cuda.empty_cache()


def run():
    freeze();unseal()
    for g in GS:
        state('launch_T_generation',G=g)
        subprocess.run([str(ROOT/'scripts/pilot-vllm'),'-m','pilot.week_final_generate','--round',g],cwd=ROOT,check=True)
        label(g)
    for v in VS:score(v)
    from .week_final_reasons import generate_reasons
    generate_reasons()
    state('T_and_reason_generation_complete_initial_review_pending')
    from .week_final_report import main as report
    report()


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['freeze','run']);a=p.parse_args()
    try:freeze() if a.stage=='freeze' else run()
    except Exception as error:
        RUN.mkdir(exist_ok=True);state('failed',error=str(error),resume='scripts/pilot week_final_run run',cached_outputs_retained=True);raise


if __name__=='__main__':main()
