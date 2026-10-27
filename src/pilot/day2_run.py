"""Sequential local-only G0 generation or V0 scoring under frozen day2 settings."""
import argparse
import time
from .core import ROOT,read_jsonl,recover_jsonl,write_json,append_jsonl,digest,cache_identity,ensure_run_manifest,prompt_ids
from .runtime import load,model_identity,seed_all,conditional_label_scores

def config():return __import__('json').loads((ROOT/'configs/day2.json').read_text())
def messages(role,question,response=None):
    cfg=config()
    return [{'role':'system','content':cfg['G_system']},{'role':'user','content':question+cfg['G_user_suffix']}] if role=='G' else [{'role':'system','content':cfg['V_system']},{'role':'user','content':f'Question:\n{question}\n\nCandidate response:\n{response}'}]

def main():
    import torch
    cfg=config()
    from .core import CFG as initial_cfg
    assert all(cfg[f'{role}_MODEL_DIR']==initial_cfg[f'{role}_MODEL_DIR'] for role in ['G','V']), 'Day2 must use original G0/V0 paths'
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['generate','score']);parser.add_argument('--task',choices=['gsm8k_C100','math70'],required=True);parser.add_argument('--limit',type=int);a=parser.parse_args()
    role='G' if a.stage=='generate' else 'V';base,adapter=model_identity(role);assert adapter is None
    source=ROOT/f'data/day2/inputs/{a.task}.jsonl' if role=='G' else ROOT/f'runs/day2/G0_{a.task}.jsonl';rows=read_jsonl(source);assert rows
    if a.limit:rows=rows[:a.limit]
    output=ROOT/f'runs/day2/{"G0" if role=="G" else "V0"}_{a.task}.jsonl';settings=cfg['generation'] if role=='G' else cfg['verifier']
    ensure_run_manifest(output.with_suffix('.manifest.json'),{'model_fingerprint':base,'adapter':None,'role':role,'settings':settings,'prompt_template':messages(role,'{question}','{response}'),'input_sha256':__import__('hashlib').sha256(source.read_bytes()).hexdigest(),'config_sha256':digest(cfg)})
    previous=recover_jsonl(output);done={r['cache_key'] for r in previous};stage_start=time.perf_counter();model,tok=load(role);torch.cuda.synchronize();load_seconds=time.perf_counter()-stage_start;start=time.perf_counter();torch.cuda.reset_peak_memory_stats();new_count=0
    for index,row in enumerate(rows):
        msgs=messages(role,row['question'],row.get('text'));identity=cache_identity(role,base,None,row['question_id'] if role=='G' else row['candidate_id'],msgs,settings)
        if role=='G':
            keys=[digest({'question_key':identity,'candidate_index':j}) for j in range(settings['num_return_sequences'])]
            if all(k in done for k in keys):continue
            seed_all(cfg['seed']+int(digest(row['question_id'])[:8],16)%1000000);ids=prompt_ids(tok,msgs);x=torch.tensor([ids],device='cuda');torch.cuda.synchronize();tick=time.perf_counter()
            with torch.inference_mode():sequences=model.generate(input_ids=x,attention_mask=torch.ones_like(x),**settings,pad_token_id=tok.pad_token_id)
            torch.cuda.synchronize();seconds=time.perf_counter()-tick;eos=model.generation_config.eos_token_id;eos=[eos] if isinstance(eos,int) else eos
            for j,sequence in enumerate(sequences):
                if keys[j] in done:continue
                tokens=sequence[len(ids):].tolist();stop=next((k for k,t in enumerate(tokens) if t in eos),None);n=stop+1 if stop is not None else len(tokens)
                result={**row,'G_round':'G0','candidate_id':f"{row['question_id']}:day2:G0:{j}",'candidate_index':j,'text':tok.decode(tokens[:n],skip_special_tokens=True),'truncated':stop is None and len(tokens)>=settings['max_new_tokens'],'output_tokens':n,'prompt_tokens':len(ids),'generation_seconds_for_question':seconds,'model_fingerprint':base,'adapter_fingerprint':None,'prompt_sha256':digest(msgs),'question_cache_key':identity,'cache_key':keys[j],'protocol':'day2_boxed_v2'}
                append_jsonl(output,result);done.add(keys[j]);new_count+=1
            print(f'{a.task} G {index+1}/{len(rows)} candidates={len(done)} seconds={seconds:.2f}',flush=True)
        else:
            if identity in done:continue
            n=len(prompt_ids(tok,msgs));tick=time.perf_counter()
            if n+4>settings['max_context']:scores={'score':None,'accepted':None,'scoring_status':'over_context'}
            else:scores=conditional_label_scores(model,tok,msgs,settings['labels']);scores['scoring_status']='ok'
            torch.cuda.synchronize()
            append_jsonl(output,{k:row[k] for k in ['question_id','candidate_id','split','task','G_round']}|scores|{'V_version':'V0','model_fingerprint':base,'adapter_fingerprint':None,'prompt_sha256':digest(msgs),'cache_key':identity,'seconds':time.perf_counter()-tick})
            done.add(identity);new_count+=1
            if (index+1)%20==0:print(f'{a.task} V {index+1}/{len(rows)}',flush=True)
    torch.cuda.synchronize()
    performance_path=ROOT/f'runs/day2/{role}_{a.task}_performance.json'
    if new_count==0 and performance_path.exists():
        append_jsonl(ROOT/f'runs/day2/{role}_{a.task}_resume_overheads.jsonl',{'model_load_seconds':load_seconds,'loop_wall_seconds':time.perf_counter()-start,'new_records':0,'note':'Cache-only resume; original measured performance retained.'})
        print('All requested candidates cached; retained original performance report.',flush=True)
        return
    write_json(performance_path,{'model_load_seconds':load_seconds,'loop_wall_seconds_this_invocation':time.perf_counter()-start,'total_wall_seconds_this_invocation':time.perf_counter()-stage_start,'new_records_this_invocation':new_count,'timing_covers_all_saved_records':new_count==len(done),'total_records':len(done),'peak_allocated_bytes':torch.cuda.max_memory_allocated(),'model_fingerprint':base,'settings':settings})

if __name__=='__main__':main()
