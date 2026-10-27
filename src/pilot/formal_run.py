"""Resumable local G generation and immutable V0 scoring; inference never reads gold."""
import argparse
import time
from .core import ROOT,read_jsonl,recover_jsonl,append_jsonl,write_json,ensure_run_manifest,prompt_ids,digest,cache_identity
from .runtime import load,model_identity,seed_all,conditional_label_scores
from .formal_common import RUN,PLAN,PROTOCOL,protocol,plan,inputs,input_path,output_path,adapter_path,messages,verify_fixed,gpu_lock,WorkBudget,progress,formal_generation_settings,sha,utc

def run(stage,round_name,split):
    import torch
    role='G' if stage=='generate' else 'V'
    with gpu_lock():
        verify_fixed();p=protocol();adapter=adapter_path(round_name) if role=='G' else None
        base,ah=model_identity(role,adapter);assert role!='V' or ah is None
        source=input_path(split) if role=='G' else output_path(round_name,split)
        rows=inputs(split) if role=='G' else read_jsonl(source)
        if role=='V':assert len(rows)==len(inputs(split))*4
        output=output_path(round_name,split,role)
        settings=p['sampling'] if role=='G' else p['V_scoring']
        ensure_run_manifest(output.with_suffix('.manifest.json'),{'role':role,'G_round':round_name,'split':split,
            'source_sha256':sha(source),'effective_question_ids':[r['question_id'] for r in inputs(split)],
            'model_fingerprint':base,'adapter_fingerprint':ah,'settings':settings,
            'prompt_templates':messages(role,'{question}','{response}'),'output_budget_rule':p['G_output_budget_rule'] if role=='G' else None,
            'protocol_sha256':sha(PROTOCOL),'execution_sha256':sha(PLAN),
            'runner_sha256':sha(ROOT/'src/pilot/formal_run.py'),'common_sha256':sha(ROOT/'src/pilot/formal_common.py'),
            'fixed_scoring_sha256':sha(ROOT/'src/pilot/runtime.py')})
        old=recover_jsonl(output);done={r['cache_key'] for r in old};assert len(done)==len(old)
        expected=len(rows)*4 if role=='G' else len(rows)
        if len(old)==expected:
            print(f'Cached {role}/{round_name}/{split}: {expected}',flush=True);return
        budget=WorkBudget(f'{role}/{round_name}/{split}');start=time.perf_counter();model,tok=load(role,adapter)
        torch.cuda.synchronize();loaded=time.perf_counter();torch.cuda.reset_peak_memory_stats();new=0
        for index,row in enumerate(rows):
            msgs=messages(role,row['question'],row.get('text'))
            if role=='G':
                ids=prompt_ids(tok,msgs);actual=formal_generation_settings(len(ids))
                seed=plan()['seed']+int(digest(row['question_id'])[:8],16)%1000000
                identity=cache_identity(role,base,ah,row['question_id'],msgs,dict(actual,seed=seed))
                keys=[digest({'question_key':identity,'candidate_index':j}) for j in range(4)]
                if all(k in done for k in keys):continue
                budget.check();seed_all(seed);x=torch.tensor([ids],device='cuda');torch.cuda.synchronize();tick=time.perf_counter()
                with torch.inference_mode():sequences=model.generate(input_ids=x,attention_mask=torch.ones_like(x),**actual,pad_token_id=tok.pad_token_id)
                torch.cuda.synchronize();seconds=time.perf_counter()-tick
                eos=model.generation_config.eos_token_id;eos=[eos] if isinstance(eos,int) else eos
                for j,seq in enumerate(sequences):
                    if keys[j] in done:continue
                    tokens=seq[len(ids):].tolist();stop=next((k for k,t in enumerate(tokens) if t in eos),None);n=stop+1 if stop is not None else len(tokens)
                    result={**row,'split':split,'G_round':round_name,'candidate_id':f"{row['question_id']}:formal:{round_name}:{j}",
                        'candidate_index':j,'text':tok.decode(tokens[:n],skip_special_tokens=True),'output_tokens':n,'prompt_tokens':len(ids),
                        'max_new_tokens':actual['max_new_tokens'],'truncated':stop is None and n>=actual['max_new_tokens'],
                        'generation_seconds_for_question':seconds,'generation_seed':seed,'model_fingerprint':base,'adapter_fingerprint':ah,
                        'prompt_sha256':digest(msgs),'cache_key':keys[j],'question_cache_key':identity,'protocol':'003_DAPO_formal',
                        'recorded_at':utc()}
                    assert not any(k in result for k in ['gold_answer','correct','reference'])
                    append_jsonl(output,result);done.add(keys[j]);new+=1
                budget.charge(row['question_id'])
                print(f'{round_name}/{split} G {index+1}/{len(rows)} candidates={len(done)} seconds={seconds:.2f}',flush=True)
                progress(f'generate_{round_name}_{split}',questions_complete=len(done)//4,questions_expected=len(rows),candidates=len(done))
            else:
                identity=cache_identity('V',base,None,row['candidate_id'],msgs,settings)
                if identity in done:continue
                budget.check();n=len(prompt_ids(tok,msgs));assert n+4<=settings['max_context'],'Never truncate V candidate input'
                torch.cuda.synchronize();tick=time.perf_counter();scores=conditional_label_scores(model,tok,msgs,settings['labels']);torch.cuda.synchronize()
                append_jsonl(output,{k:row[k] for k in ['question_id','candidate_id','split','G_round']}|scores|
                    {'V_version':'V0','model_fingerprint':base,'adapter_fingerprint':None,'scoring_status':'ok','cache_key':identity,
                    'prompt_sha256':digest(msgs),'seconds':time.perf_counter()-tick,'threshold':.5,'recorded_at':utc()})
                done.add(identity);new+=1;budget.charge(row['candidate_id'])
                if (index+1)%40==0:
                    print(f'{round_name}/{split} V0 {index+1}/{len(rows)}',flush=True)
                    progress(f'score_{round_name}_{split}',scores_complete=len(done),scores_expected=len(rows))
        assert len(done)==expected
        append_jsonl(RUN/'inference_invocations.jsonl',{'role':role,'G_round':round_name,'split':split,'new_records':new,
            'total_records':len(done),'load_seconds':loaded-start,'loop_seconds':time.perf_counter()-loaded,
            'peak_allocated_bytes':torch.cuda.max_memory_allocated(),'base_fingerprint':base,'adapter_fingerprint':ah,'ended_at':utc()})
        budget.charge('invocation_finalization')
        assert model_identity(role,adapter)==(base,ah)

def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['generate','score']);p.add_argument('--round',dest='round_name',choices=['G0','G1','G2'],required=True);p.add_argument('--split',choices=['A','B','C'],required=True);a=p.parse_args()
    run(a.stage,a.round_name,a.split)

if __name__=='__main__':main()
