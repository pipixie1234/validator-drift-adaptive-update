import argparse
import time
from .core import *
from .runtime import *

def main():
    import torch
    parser=argparse.ArgumentParser();parser.add_argument('--split',default='C',choices=['A','B','C']);parser.add_argument('--limit',type=int,default=CFG['day1_questions']);parser.add_argument('--tag',default='G0_C50');parser.add_argument('--adapter');args=parser.parse_args()
    rows=read_jsonl(ROOT/f'data/inputs/{args.split}.jsonl')[:args.limit];out=ROOT/f'runs/day1/{args.tag}.jsonl';out.parent.mkdir(parents=True,exist_ok=True)
    model,tok=load('G',args.adapter);settings=CFG['generation'];fp,ah=model_identity('G',args.adapter)
    ensure_run_manifest(out.with_suffix('.manifest.json'),{'role':'G','model':fp,'adapter':ah,'settings':settings,'split':args.split,'prompt':messages('G','{question}'),'answer_extraction_protocol':'strict_hash_plus_terminal_numeric_box_diagnostic_v1','input_sha256':hashlib.sha256((ROOT/f'data/inputs/{args.split}.jsonl').read_bytes()).hexdigest()})
    completed={r['cache_key']:r for r in recover_jsonl(out)};torch.cuda.reset_peak_memory_stats();start=time.perf_counter();new_tokens=0
    for i,row in enumerate(rows):
        msgs=messages('G',row['question']); ids=prompt_ids(tok,msgs)
        key=cache_identity('G',fp,ah,row['question_id'],msgs,settings)
        existing=[r for r in completed.values() if r.get('question_cache_key')==key]
        if len(existing)==settings['num_return_sequences']:continue
        seed_all(CFG['seed']+int(digest(row['question_id'])[:8],16)%1000000)
        x=torch.tensor([ids],device=model.device);torch.cuda.synchronize();t=time.perf_counter()
        with torch.inference_mode(): result=model.generate(input_ids=x,attention_mask=torch.ones_like(x),**settings,pad_token_id=tok.pad_token_id)
        torch.cuda.synchronize();elapsed=time.perf_counter()-t
        for j,seq in enumerate(result):
            cid=f"{row['question_id']}:G0:{j}";ck=digest({'question_key':key,'candidate_id':j})
            if ck in completed:continue
            generated=seq[len(ids):].tolist();eos=model.generation_config.eos_token_id;eos=[eos] if isinstance(eos,int) else eos
            stop=next((k for k,tokid in enumerate(generated) if tokid in eos),None)
            nt=stop+1 if stop is not None else len(generated);truncated=stop is None and len(generated)>=settings['max_new_tokens'];text=tok.decode(generated[:nt],skip_special_tokens=True)
            extraction=extract_candidate_answer(text,truncated)
            item={**row,'G_round':'G0' if not args.adapter else args.tag,'candidate_id':cid,'text':text,'extracted_answer':extraction['answer'],'verification_status':extraction['status'],'answer_format':extraction['answer_format'],'format_compliant':extraction['format_compliant'],'truncated':truncated,'output_tokens':nt,'prompt_tokens':len(ids),'model_fingerprint':fp,'adapter_fingerprint':ah,'cache_key':ck,'question_cache_key':key,'question_generation_seconds':elapsed,'V_version':None,'accepted':None,'score':None}
            append_jsonl(out,item);completed[ck]=item;new_tokens+=nt
        print(f'G {i+1}/{len(rows)} candidates={len(completed)} question_seconds={elapsed:.2f}',flush=True)
    torch.cuda.synchronize()
    write_json(ROOT/f'runs/day1/{args.tag}_performance.json',{'stage':'generation','seconds_this_invocation':time.perf_counter()-start,'new_tokens_this_invocation':new_tokens,'peak_allocated_bytes':torch.cuda.max_memory_allocated(),'settings':settings,'model_fingerprint':fp,'candidate_count':len(completed)})

if __name__=='__main__':main()
