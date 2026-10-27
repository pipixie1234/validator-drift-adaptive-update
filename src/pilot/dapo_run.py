"""Serial, resumable inference with original local G0/V0, no adapters or gold."""
import argparse
import time
from .core import ROOT, read_jsonl, recover_jsonl, append_jsonl, digest, cache_identity, ensure_run_manifest, prompt_ids
from .runtime import load, model_identity, seed_all, conditional_label_scores
from .dapo_common import BASE, RUN, config, messages, sha, available_output_tokens

def main():
    import torch
    cfg=config();p=argparse.ArgumentParser();p.add_argument('stage',choices=['generate','score']);p.add_argument('--limit',type=int);a=p.parse_args()
    role='G' if a.stage=='generate' else 'V'
    base,adapter=model_identity(role);assert adapter is None
    from .core import CFG
    assert cfg[role+'_MODEL_DIR']==CFG[role+'_MODEL_DIR']
    locked=__import__('json').loads((BASE/'manifests/selection_locked.json').read_text())
    assert sha(ROOT/'configs/dapo_diagnostic.json')==locked['config_sha256']
    assert sha(BASE/'inputs/dapo100.jsonl')==locked['inputs_sha256']
    source=BASE/'inputs/dapo100.jsonl' if role=='G' else RUN/'G0_dapo100.jsonl'
    rows=read_jsonl(source);assert rows
    if role=='V':assert len(rows)==400,'V scores only after the fixed development run is complete'
    if a.limit:rows=rows[:a.limit]
    output=RUN/f'{role}0_dapo100.jsonl';settings=cfg['generation'] if role=='G' else cfg['verifier']
    ensure_run_manifest(output.with_suffix('.manifest.json'),{'role':role,'model_fingerprint':base,'adapter':None,
        'settings':settings,'prompt_template':messages(role,'{question}','{response}'),
        'source_sha256':sha(source),'config_sha256':locked['config_sha256'],
        'runner_sha256':sha(ROOT/'src/pilot/dapo_run.py'),'runtime_sha256':sha(ROOT/'src/pilot/runtime.py'),
        'common_sha256':sha(ROOT/'src/pilot/dapo_common.py')})
    previous=recover_jsonl(output);done={r['cache_key'] for r in previous}
    assert len(done)==len(previous),'Duplicate inference cache records'
    start=time.perf_counter();model,tok=load(role);torch.cuda.synchronize();loaded=time.perf_counter()
    if role=='G':assert model.config.max_position_embeddings==cfg['G_native_context']
    else:assert model.config.max_position_embeddings>=settings['max_context']
    torch.cuda.reset_peak_memory_stats();new=0
    for index,row in enumerate(rows):
        msgs=messages(role,row['question'],row.get('text'))
        identity=cache_identity(role,base,None,row['question_id'] if role=='G' else row['candidate_id'],msgs,settings)
        if role=='G':
            keys=[digest({'question_key':identity,'candidate_index':j}) for j in range(4)]
            if all(k in done for k in keys):continue
            seed_all(cfg['seed']+int(digest(row['question_id'])[:8],16)%1000000)
            ids=prompt_ids(tok,msgs)
            assert len(ids)==row['prompt_tokens_prechecked']
            assert settings['max_new_tokens']<=available_output_tokens(len(ids),model.config.max_position_embeddings)
            x=torch.tensor([ids],device='cuda');torch.cuda.synchronize();tick=time.perf_counter()
            with torch.inference_mode():
                sequences=model.generate(input_ids=x,attention_mask=torch.ones_like(x),**settings,pad_token_id=tok.pad_token_id)
            torch.cuda.synchronize();seconds=time.perf_counter()-tick
            eos=model.generation_config.eos_token_id;eos=[eos] if isinstance(eos,int) else eos
            for j,sequence in enumerate(sequences):
                if keys[j] in done:continue
                tokens=sequence[len(ids):].tolist();stop=next((k for k,t in enumerate(tokens) if t in eos),None)
                n=stop+1 if stop is not None else len(tokens)
                result={**row,'G_round':'G0','candidate_id':f"{row['question_id']}:dapo:G0:{j}",
                    'candidate_index':j,'text':tok.decode(tokens[:n],skip_special_tokens=True),
                    'truncated':stop is None and len(tokens)>=settings['max_new_tokens'],'output_tokens':n,
                    'prompt_tokens':len(ids),'generation_seconds_for_question':seconds,
                    'model_fingerprint':base,'adapter_fingerprint':None,'prompt_sha256':digest(msgs),
                    'question_cache_key':identity,'cache_key':keys[j],'protocol':cfg['protocol']}
                assert 'gold_answer' not in result
                append_jsonl(output,result);done.add(keys[j]);new+=1
            print(f'DAPO G {index+1}/{len(rows)} candidates={len(done)} seconds={seconds:.2f}',flush=True)
        else:
            if identity in done:continue
            n=len(prompt_ids(tok,msgs));tick=time.perf_counter()
            if n+4>settings['max_context']:scores={'score':None,'accepted':None,'scoring_status':'over_context','prompt_tokens':n}
            else:scores=conditional_label_scores(model,tok,msgs,settings['labels']);scores['scoring_status']='ok'
            torch.cuda.synchronize()
            append_jsonl(output,{k:row[k] for k in ['question_id','candidate_id','split','task','G_round']}|scores|
                {'V_version':'V0','model_fingerprint':base,'adapter_fingerprint':None,'prompt_sha256':digest(msgs),
                 'cache_key':identity,'seconds':time.perf_counter()-tick})
            done.add(identity);new+=1
            if (index+1)%20==0:print(f'DAPO V {index+1}/{len(rows)}',flush=True)
    torch.cuda.synchronize()
    append_jsonl(RUN/f'{role}_invocations.jsonl',{'requested_records':len(rows),'new_records':new,
        'total_records':len(done),'load_seconds':loaded-start,'loop_seconds':time.perf_counter()-loaded,
        'total_seconds':time.perf_counter()-start,'peak_allocated_bytes':torch.cuda.max_memory_allocated(),
        'model_fingerprint':base,'settings':settings})

if __name__=='__main__':main()
