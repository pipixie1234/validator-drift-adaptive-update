"""vLLM generation with fixed chunk caches; no V or answer labels are loaded."""
import argparse
import importlib.metadata
import json
import time
from pathlib import Path
from .core import ROOT,read_jsonl,write_json,write_jsonl,prompt_ids,digest,ensure_run_manifest
from .formal_common import messages,protocol,sha,utc,gpu_lock,WorkBudget
from .runtime import model_identity
from .g3_common import RUN,REPORT,BENCH,ENGINE,REVISION,pool,adapter,raw,status,validate


def request_seed(question_id):return 42+int(digest(question_id)[:8],16)%1000000


def generate(name,benchmark=False):
 from transformers import AutoTokenizer
 from vllm import LLM,SamplingParams
 from vllm.lora.request import LoRARequest
 if benchmark:
  b=json.loads(BENCH.read_text());assert b['engine']==ENGINE
  wanted=set(b['question_ids']);by={r['question_id']:r for r in pool('C')}
  groups=[('C',[by[q] for q in b['question_ids']])]
 else:
  cfg=validate();assert cfg['engine']=='vllm';groups=[(s,pool(s)) for s in ['A','C']]
 settings=ENGINE;tokenizer=AutoTokenizer.from_pretrained(protocol()['models']['G']['path'],local_files_only=True)
 base,ah=model_identity('G',adapter(name));settings_hash=digest(settings)
 stage='benchmark' if benchmark else 'generation'
 manifest={'engine':'vllm','version':importlib.metadata.version('vllm'),'settings':settings,
  'checkpoint':name,'base_fingerprint':base,'adapter_fingerprint':ah,
  'sampling':{'n':4,'temperature':.7,'top_p':.9,'top_k':50,'stop_token_ids':[151645,151643]},
  'tokenizer_same_as_frozen':True,'chunk_questions':16,'code_sha256':sha(Path(__file__)),
  'protocol_sha256':sha(BENCH if benchmark else REVISION)}
 directory=RUN/'benchmark/vllm' if benchmark else RUN/f'engine/{name}'
 directory.mkdir(parents=True,exist_ok=True);ensure_run_manifest(directory/'manifest.json',manifest)
 tasks=[];saved={}
 for split,rows in groups:
  saved[split]=[]
  for start in range(0,len(rows),16):
   chunk=rows[start:start+16];dest=directory/f'{split}_{start:04d}.json'
   if dest.exists():
    old=json.loads(dest.read_text());assert old['manifest_sha256']==sha(directory/'manifest.json')
    assert old['question_ids']==[r['question_id'] for r in chunk] and len(old['records'])==len(chunk)*4
    saved[split]+=old['records']
   else:tasks.append((split,start,chunk,dest))
 if not tasks:
  for split,rows in groups:
   output=directory/'generated.jsonl' if benchmark else raw(name,split)
   write_jsonl(output,saved[split])
  print('Cached complete vLLM generation.',flush=True);return
 with gpu_lock():
  budget=WorkBudget('g3_extension/'+stage+'/'+name);overall=time.perf_counter()
  status(stage+'_'+name,engine='vllm',pending_chunks=len(tasks),candidates=sum(map(len,saved.values())))
  llm=LLM(model=protocol()['models']['G']['path'],tokenizer=protocol()['models']['G']['path'],**settings)
  loaded=time.perf_counter();request=LoRARequest('extension_'+name,1,str(adapter(name)))
  for split,start,chunk,dest in tasks:
   budget.check();prompts=[];params=[];lengths=[]
   for row in chunk:
    ids=prompt_ids(tokenizer,messages('G',row['question']));assert 0<len(ids)<=1024
    cap=min(3840,4096-len(ids));lengths.append((len(ids),cap))
    prompts.append({'prompt_token_ids':ids})
    params.append(SamplingParams(n=4,temperature=.7,top_p=.9,top_k=50,
     max_tokens=cap,seed=request_seed(row['question_id']),stop_token_ids=[151645,151643]))
   tick=time.perf_counter();outputs=llm.generate(prompts,params,lora_request=request,use_tqdm=False)
   elapsed=time.perf_counter()-tick;records=[]
   assert len(outputs)==len(chunk)
   for row,out,(n,cap),prompt in zip(chunk,outputs,lengths,prompts):
    assert out.prompt_token_ids==prompt['prompt_token_ids'] and len(out.outputs)==4
    for completion in sorted(out.outputs,key=lambda x:x.index):
     j=completion.index;ids=list(completion.token_ids);assert 0<len(ids)<=cap
     text=tokenizer.decode(ids,skip_special_tokens=True)
     record={**row,'split':split,'G_round':name,
      'candidate_id':f'{row["question_id"]}:extension004:{name}:{j}',
      'candidate_index':j,'text':text,'output_tokens':len(ids),'prompt_tokens':n,
      'max_new_tokens':cap,'truncated':completion.finish_reason=='length',
      'finish_reason':completion.finish_reason,'stop_reason':completion.stop_reason,
      'generation_seed':request_seed(row['question_id']),'model_fingerprint':base,'adapter_fingerprint':ah,
      'prompt_sha256':digest(messages('G',row['question'])),'engine':'vllm',
      'engine_settings_sha256':settings_hash,'protocol':'004_G3_extension_vllm',
      'generation_seconds_for_chunk':elapsed,'generation_chunk_first_question':chunk[0]['question_id'],
      'recorded_at':utc(),'generated_token_ids':ids,
      'cache_key':digest({'protocol':manifest['protocol_sha256'],'adapter':ah,'question':row['question_id'],'index':j,'seed':request_seed(row['question_id']),'engine':settings_hash})}
     assert n+len(ids)<=4096 and not any(k in record for k in ['correct','gold_answer','reference'])
     records.append(record)
   # Commit a whole completed chunk atomically before updating canonical output.
   write_json(dest,{'question_ids':[r['question_id'] for r in chunk],
    'manifest_sha256':sha(directory/'manifest.json'),'records':records,'elapsed_seconds':elapsed})
   saved[split]+=records
   order={r['question_id']:i for i,r in enumerate(dict(groups)[split])}
   saved[split].sort(key=lambda r:(order[r['question_id']],r['candidate_index']))
   write_jsonl(directory/'generated.jsonl' if benchmark else raw(name,split),saved[split])
   budget.charge(f'{split}_chunk_{start}')
   status(stage+'_'+name,engine='vllm',split=split,candidates=len(saved[split]),expected=len(dict(groups)[split])*4,
    chunk_seconds=elapsed,chunk_output_tokens=sum(r['output_tokens'] for r in records))
   print(json.dumps({'round':name,'split':split,'candidates':len(saved[split]),
    'seconds':elapsed,'tokens_per_second':sum(r['output_tokens'] for r in records)/elapsed}),flush=True)
  duration=time.perf_counter()-overall
  write_json(directory/'complete.json',{'completed_at':utc(),'load_seconds':loaded-overall,
   'total_seconds':duration,'counts':{s:len(rr) for s,rr in saved.items()},
   'output_tokens':sum(r['output_tokens'] for rr in saved.values() for r in rr),
   'manifest_sha256':sha(directory/'manifest.json'),'disk_adapter_unchanged':model_identity('G',adapter(name))==(base,ah)})
  budget.charge('engine_finalization')


def main():
 p=argparse.ArgumentParser();p.add_argument('stage',choices=['benchmark','generate']);p.add_argument('--round',choices=['G2','G3'],default='G2');a=p.parse_args()
 generate(a.round,benchmark=a.stage=='benchmark')

if __name__=='__main__':main()
