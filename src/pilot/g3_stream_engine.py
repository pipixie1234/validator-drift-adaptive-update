"""Continuous vLLM requests, final-only n=4, atomic completed-question caches."""
import argparse
import importlib.metadata
import json
import time
from pathlib import Path
from .core import ROOT,read_jsonl,write_json,write_jsonl,prompt_ids,digest,ensure_run_manifest
from .formal_common import protocol,messages,sha,utc,gpu_lock,WorkBudget
from .runtime import model_identity
from .g3_common import RUN,REVISION,ENGINE,pool,adapter,raw,status,validate
from .g3_engine import request_seed


def generate(name):
 from transformers import AutoTokenizer
 from vllm import LLM,SamplingParams
 from vllm.sampling_params import RequestOutputKind
 from vllm.lora.request import LoRARequest
 cfg=validate();assert cfg['engine']=='vllm'
 directory=RUN/f'engine/{name}_continuous';directory.mkdir(parents=True,exist_ok=True)
 base,ah=model_identity('G',adapter(name));tok=AutoTokenizer.from_pretrained(protocol()['models']['G']['path'],local_files_only=True)
 manifest={'revision_sha256':sha(REVISION),'code_sha256':sha(Path(__file__)),
  'engine':'vllm','version':importlib.metadata.version('vllm'),'settings':ENGINE,
  'model_fingerprint':base,'adapter_fingerprint':ah,'G_round':name,
  'queue':'A598 then C95; final-only request outputs with4 candidates; preserve all finished question caches',
  'sampling':{'n':4,'temperature':.7,'top_p':.9,'top_k':50,'stop_token_ids':[151645,151643]},
  'B_used':False,'T_read':False}
 ensure_run_manifest(directory/'manifest.json',manifest)
 tasks={};saved={'A':{},'C':{}};orders={}
 for split in ['A','C']:
  rows=pool(split);orders[split]={r['question_id']:i for i,r in enumerate(rows)}
  for row in rows:
   key=digest({'question':row['question_id'],'split':split});cache=directory/(key+'.json')
   if cache.exists():
    old=json.loads(cache.read_text());assert old['manifest_sha256']==sha(directory/'manifest.json')
    assert len(old['records'])==4 and all(r['question_id']==row['question_id'] for r in old['records'])
    saved[split][row['question_id']]=old['records']
   else:tasks[str(len(tasks))]=(split,row,cache)
 def flush():
  for split,byq in saved.items():
   records=[r for q in sorted(byq,key=lambda q:orders[split][q]) for r in byq[q]]
   write_jsonl(raw(name,split),records)
 if not tasks:flush();print('Continuous vLLM generation cached complete.',flush=True);return
 with gpu_lock():
  budget=WorkBudget('g3_extension/vllm_continuous/'+name);started=time.perf_counter()
  llm=LLM(model=protocol()['models']['G']['path'],tokenizer=protocol()['models']['G']['path'],**ENGINE)
  request=LoRARequest('extension_'+name,1,str(adapter(name)));loaded=time.perf_counter()
  prompts={}
  for request_id,(split,row,path) in tasks.items():
   ids=prompt_ids(tok,messages('G',row['question']));assert 0<len(ids)<=1024
   cap=min(3840,4096-len(ids));prompts[request_id]=(ids,cap)
   params=SamplingParams(n=4,temperature=.7,top_p=.9,top_k=50,max_tokens=cap,
    seed=request_seed(row['question_id']),stop_token_ids=[151645,151643],output_kind=RequestOutputKind.FINAL_ONLY)
   llm.llm_engine.add_request(request_id,{'prompt_token_ids':ids},params,lora_request=request)
  last=time.perf_counter();new_tokens=0;new_questions=0
  status('generate_'+name,engine='vllm_continuous',candidates={s:len(v)*4 for s,v in saved.items()},expected={'A':2392,'C':380})
  while llm.llm_engine.has_unfinished_requests():
   budget.check()
   if (RUN/'cancel').exists():raise RuntimeError('Extension cancelled; finished question caches retained')
   for output in llm.llm_engine.step():
    if not output.finished:continue
    request_id=output.request_id;split,row,cache=tasks[request_id];ids,cap=prompts[request_id]
    assert output.prompt_token_ids==ids and len(output.outputs)==4
    records=[]
    for completion in sorted(output.outputs,key=lambda x:x.index):
     j=completion.index;tokens=list(completion.token_ids)
     assert j in range(4) and 0<len(tokens)<=cap and len(ids)+len(tokens)<=4096
     records.append({**row,'split':split,'G_round':name,
      'candidate_id':f'{row["question_id"]}:extension004:{name}:{j}',
      'candidate_index':j,'text':tok.decode(tokens,skip_special_tokens=True),'output_tokens':len(tokens),
      'prompt_tokens':len(ids),'max_new_tokens':cap,'truncated':completion.finish_reason=='length',
      'finish_reason':completion.finish_reason,'stop_reason':completion.stop_reason,
      'generation_seed':request_seed(row['question_id']),'model_fingerprint':base,'adapter_fingerprint':ah,
      'prompt_sha256':digest(messages('G',row['question'])),'engine':'vllm_continuous',
      'engine_settings_sha256':digest(ENGINE),'protocol':'004_G3_extension_vllm',
      'recorded_at':utc(),'generated_token_ids':tokens,
      'cache_key':digest({'protocol':sha(REVISION),'adapter':ah,'question':row['question_id'],
       'index':j,'seed':request_seed(row['question_id']),'engine':digest(ENGINE)})})
    assert row['question_id'] not in saved[split]
    write_json(cache,{'manifest_sha256':sha(directory/'manifest.json'),'records':records})
    saved[split][row['question_id']]=records;new_tokens+=sum(r['output_tokens'] for r in records);new_questions+=1
   now=time.perf_counter()
   if now-last>=10:
    flush();budget.charge('periodic_progress')
    progress={'engine':'vllm_continuous','candidates':{s:len(v)*4 for s,v in saved.items()},
     'expected':{'A':2392,'C':380},'new_questions':new_questions,
     'completed_output_tokens_per_second':new_tokens/max(now-loaded,.001)}
    status('generate_'+name,**progress);print(json.dumps(progress),flush=True);last=now
  flush();assert {s:len(v) for s,v in saved.items()}=={'A':598,'C':95}
  assert model_identity('G',adapter(name))==(base,ah)
  write_json(directory/'complete.json',{'completed_at':utc(),'total_seconds':time.perf_counter()-started,
   'load_seconds':loaded-started,'new_questions':new_questions,'new_output_tokens':new_tokens,
   'candidates':{s:len(v)*4 for s,v in saved.items()},'disk_adapter_unchanged':True})
  budget.charge('finalization')


def main():
 p=argparse.ArgumentParser();p.add_argument('--round',required=True,choices=['G2','G3']);a=p.parse_args();generate(a.round)

if __name__=='__main__':main()
