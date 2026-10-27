import argparse
import time
from .core import *
from .runtime import *

def main():
    import torch
    parser=argparse.ArgumentParser();parser.add_argument('--input',default='G0_C50');parser.add_argument('--tag',default='V0_G0_C50');parser.add_argument('--adapter');args=parser.parse_args()
    rows=read_jsonl(ROOT/f'runs/day1/{args.input}.jsonl');assert rows
    model,tok=load('V',args.adapter);fp,ah=model_identity('V',args.adapter)
    out=ROOT/f'runs/day1/{args.tag}.jsonl'
    ensure_run_manifest(out.with_suffix('.manifest.json'),{'role':'V','model':fp,'adapter':ah,'settings':CFG['verifier'],'prompt':V_SYSTEM,'input_sha256':hashlib.sha256((ROOT/f'runs/day1/{args.input}.jsonl').read_bytes()).hexdigest()})
    completed={r['cache_key'] for r in recover_jsonl(out)}
    torch.cuda.reset_peak_memory_stats();start=time.perf_counter();processed=0;tokens=0
    for i,row in enumerate(rows):
        msgs=messages('V',row['question'],row['text']);settings=CFG['verifier'];key=cache_identity('V',fp,ah,row['candidate_id'],msgs,settings)
        if key in completed:continue
        n=len(prompt_ids(tok,msgs));t=time.perf_counter()
        if n+4>settings['max_context']:
            scores={'score':None,'accepted':None,'scoring_status':'over_context'}
        else:
            scores=conditional_label_scores(model,tok,msgs,settings['labels']);scores['scoring_status']='ok';tokens+=n
        torch.cuda.synchronize()
        append_jsonl(out,{k:row[k] for k in ['question_id','split','G_round','candidate_id','verification_status','extracted_answer']}|scores|{'V_version':'V0' if not args.adapter else args.tag,'cache_key':key,'model_fingerprint':fp,'adapter_fingerprint':ah,'seconds':time.perf_counter()-t})
        completed.add(key);processed+=1
        if (i+1)%10==0:print(f'V {i+1}/{len(rows)}',flush=True)
    write_json(ROOT/f'runs/day1/{args.tag}_performance.json',{'stage':'verification','seconds_this_invocation':time.perf_counter()-start,'processed_this_invocation':processed,'prompt_tokens_this_invocation':tokens,'peak_allocated_bytes':torch.cuda.max_memory_allocated(),'model_fingerprint':fp})

if __name__=='__main__':main()
