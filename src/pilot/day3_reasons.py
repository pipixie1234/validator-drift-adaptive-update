"""Four independent rationale generations for cost/material preparation, never A/B score evidence."""
import json
import re
import time
from .core import ROOT,read_jsonl,append_jsonl,write_json,ensure_run_manifest,prompt_ids,digest
from .runtime import load,model_identity
from .dapo_common import RUN,sha

OUT=ROOT/'runs/day3'
SYSTEM=('Judge whether the final answer in the candidate correctly answers the question. '
        'Solve or check the relevant mathematics independently. Explain a concise, checkable reason tied to the candidate. '
        'Judge final-answer correctness, not whether every intermediate step is valid. '
        'End with exactly VERDICT: A for a correct final answer or VERDICT: B for an incorrect final answer.')

def main():
    import torch
    labels={r['candidate_id']:r for r in read_jsonl(RUN/'labels_dapo100.jsonl')}
    scores={r['candidate_id']:r for r in read_jsonl(RUN/'V0_dapo100.jsonl')}
    raw=read_jsonl(RUN/'G0_dapo100.jsonl');chosen=[];strata=[]
    for name,correct,accepted in [('TP',True,True),('FN',True,False),('FP',False,True),('TN',False,False)]:
        eligible=[r for r in raw if labels[r['candidate_id']]['correct'] is correct and scores[r['candidate_id']]['accepted'] is accepted]
        if eligible:chosen.append(eligible[0]);strata.append(name)
    assert chosen
    fp,adapter=model_identity('V');assert adapter is None
    settings={'max_new_tokens':768,'do_sample':False,'max_context':8192}
    ensure_run_manifest(OUT/'rationale_benchmark.manifest.json',{'protocol':'reason_then_verdict_cost_v1',
        'system':SYSTEM,'settings':settings,'model_fingerprint':fp,'adapter':None,
        'selected':list(zip(strata,[r['candidate_id'] for r in chosen])),
        'selection':'First verified candidate in each available main A/B confusion stratum, development only; purposive sample, no prevalence claim.',
        'raw_G_sha256':sha(RUN/'G0_dapo100.jsonl'),'runner_sha256':sha(ROOT/'src/pilot/day3_reasons.py'),
        'meaning':'Separate generative protocol. Explanations are not the true basis of the earlier A/B-only judgement; no rationale validity labels supplied by server.'})
    output=OUT/'V0_rationale_benchmark.jsonl';done={r['candidate_id'] for r in read_jsonl(output)}
    if len(done)==len(chosen):print('Rationale benchmark cached.');return
    start=time.perf_counter();model,tok=load('V');torch.cuda.synchronize();loaded=time.perf_counter()
    for stratum,row in zip(strata,chosen):
        if row['candidate_id'] in done:continue
        msgs=[{'role':'system','content':SYSTEM},{'role':'user','content':f"Question:\n{row['question']}\n\nCandidate response:\n{row['text']}"}]
        ids=prompt_ids(tok,msgs);assert len(ids)+768<=8192
        x=torch.tensor([ids],device='cuda');torch.cuda.reset_peak_memory_stats();torch.cuda.synchronize();tick=time.perf_counter()
        with torch.inference_mode():seq=model.generate(input_ids=x,attention_mask=torch.ones_like(x),max_new_tokens=768,do_sample=False,pad_token_id=tok.pad_token_id)[0]
        torch.cuda.synchronize();seconds=time.perf_counter()-tick;tokens=seq[len(ids):].tolist()
        eos=model.generation_config.eos_token_id;eos=[eos] if isinstance(eos,int) else eos
        stop=next((i for i,t in enumerate(tokens) if t in eos),None);n=stop+1 if stop is not None else len(tokens)
        truncated=stop is None and n>=768;text=tok.decode(tokens[:n],skip_special_tokens=True)
        match=re.search(r'VERDICT:\s*([AB])\s*$',text)
        append_jsonl(output,{'question_id':row['question_id'],'candidate_id':row['candidate_id'],
            'split':'DAPO_D','G_round':'G0','V_version':'V0','protocol':'reason_then_verdict_cost_v1',
            'sample_stratum_main_AB':stratum,'model_fingerprint':fp,'adapter_fingerprint':None,
            'text':text,'prompt_sha256':digest(msgs),'prompt_tokens':len(ids),'output_tokens':n,
            'truncated':truncated,'verdict':match[1] if match and not truncated else None,
            'seconds':seconds,'peak_allocated_bytes':torch.cuda.max_memory_allocated(),
            'rationale_validity_label':None,'review_status':'pending_local_assistant_and_user_review'})
        print(f'Rationale benchmark {stratum}: tokens={n}, truncated={truncated}, seconds={seconds:.2f}',flush=True)
    write_json(OUT/'rationale_benchmark_timing.json',{'model_load_seconds':loaded-start,
        'total_seconds_this_invocation':time.perf_counter()-start,'not_part_of_main_AB_metrics':True})

if __name__=='__main__':main()
