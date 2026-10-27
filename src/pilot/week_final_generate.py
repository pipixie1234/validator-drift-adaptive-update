"""All final T generators use the same continuous vLLM runtime and budget."""
import argparse
import importlib.metadata
import json
import time
from pathlib import Path

from .core import read_jsonl,write_json,write_jsonl,prompt_ids,digest,ensure_run_manifest
from .formal_common import protocol,messages,sha,utc,gpu_lock,WorkBudget
from .runtime import model_identity
from .week_final_common import RUN,PLAN,GS,validate,unseal,gadapter,state


def generate(g):
    from transformers import AutoTokenizer
    from vllm import LLM,SamplingParams
    from vllm.sampling_params import RequestOutputKind
    from vllm.lora.request import LoRARequest
    cfg=validate();rows=unseal();settings=cfg['G_engine']['settings'];a=gadapter(g)
    base,ad=model_identity('G',a);tok=AutoTokenizer.from_pretrained(protocol()['models']['G']['path'],local_files_only=True)
    directory=RUN/'generation'/g;directory.mkdir(parents=True,exist_ok=True)
    manifest={'plan_sha256':sha(PLAN),'code_sha256':sha(Path(__file__)),'vllm_version':importlib.metadata.version('vllm'),
              'engine':settings,'G':g,'base_fingerprint':base,'adapter_fingerprint':ad}
    ensure_run_manifest(directory/'manifest.json',manifest);saved={};tasks={};order={r['question_id']:i for i,r in enumerate(rows)}
    for row in rows:
        cache=directory/(digest(row['question_id'])+'.json')
        if cache.exists():
            old=json.loads(cache.read_text());assert old['manifest_sha256']==sha(directory/'manifest.json') and len(old['records'])==4
            saved[row['question_id']]=old['records']
        else:tasks[str(len(tasks))]=(row,cache)
    def flush():write_jsonl(RUN/f'{g}_T.jsonl',[r for q in sorted(saved,key=order.get) for r in saved[q]])
    if not tasks:flush();return
    with gpu_lock():
        budget=WorkBudget('week_final/generate/'+g);tick=time.perf_counter()
        llm=LLM(model=protocol()['models']['G']['path'],tokenizer=protocol()['models']['G']['path'],**settings)
        request=LoRARequest('week_final_'+g,1,str(a)) if a else None;prompts={}
        for request_id,(r,path) in tasks.items():
            ids=prompt_ids(tok,messages('G',r['question']));assert 0<len(ids)<=1024
            cap=min(3840,4096-len(ids));seed=42+int(digest(r['question_id'])[:8],16)%1000000;prompts[request_id]=(ids,cap,seed)
            params=SamplingParams(n=4,temperature=.7,top_p=.9,top_k=50,max_tokens=cap,seed=seed,
                stop_token_ids=[151645,151643],output_kind=RequestOutputKind.FINAL_ONLY)
            llm.llm_engine.add_request(request_id,{'prompt_token_ids':ids},params,lora_request=request)
        last=time.perf_counter();completed_new_tokens=0
        while llm.llm_engine.has_unfinished_requests():
            budget.check()
            for output in llm.llm_engine.step():
                if not output.finished:continue
                r,cache=tasks[output.request_id];ids,cap,seed=prompts[output.request_id]
                assert output.prompt_token_ids==ids and len(output.outputs)==4;records=[]
                for c in sorted(output.outputs,key=lambda c:c.index):
                    tokens=list(c.token_ids);assert 0<len(tokens)<=cap and len(ids)+len(tokens)<=4096
                    records.append({**r,'split':'T','G_round':g,'candidate_index':c.index,
                        'candidate_id':r['question_id']+f':weekfinal005:{g}:{c.index}',
                        'text':tok.decode(tokens,skip_special_tokens=True),'output_tokens':len(tokens),'prompt_tokens':len(ids),
                        'max_new_tokens':cap,'truncated':c.finish_reason=='length','finish_reason':c.finish_reason,
                        'stop_reason':c.stop_reason,'generated_token_ids':tokens,'generation_seed':seed,
                        'model_fingerprint':base,'adapter_fingerprint':ad,'engine':'vllm_continuous',
                        'prompt_sha256':digest(messages('G',r['question'])),'plan_sha256':sha(PLAN),
                        'cache_key':digest({'plan':sha(PLAN),'G':g,'question':r['question_id'],'index':c.index}),
                        'recorded_at':utc()})
                assert r['question_id'] not in saved and not any(any(k in s for k in ['correct','gold_answer','reference']) for s in records)
                write_json(cache,{'manifest_sha256':sha(directory/'manifest.json'),'records':records});saved[r['question_id']]=records
                completed_new_tokens+=sum(x['output_tokens'] for x in records)
            if time.perf_counter()-last>=10:
                flush();budget.charge('progress');state('T_generation',G=g,questions_done=len(saved),questions_total=300,
                    candidates_done=4*len(saved),completed_new_output_tokens=completed_new_tokens)
                print(g,'T questions',len(saved),'/300',flush=True);last=time.perf_counter()
        flush();assert len(saved)==300 and model_identity('G',a)==(base,ad)
        write_json(directory/'complete.json',{'seconds_this_invocation':time.perf_counter()-tick,'questions':300,
                    'candidates':1200,'new_output_tokens':completed_new_tokens,'weights_unchanged':True,'at':utc()})
        budget.charge('finalization')


def main():
    p=argparse.ArgumentParser();p.add_argument('--round',choices=GS,required=True);a=p.parse_args();generate(a.round)


if __name__=='__main__':main()
