"""One additional fixed-V0 SFT update, with same-engine G2/G3 baselines."""
import argparse
import contextlib
import gc
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from .core import ROOT,read_jsonl,write_json,write_jsonl,ensure_run_manifest,training_example,prompt_ids
from .formal_common import PROTOCOL,protocol,messages,sha,utc,WorkBudget,gpu_lock
from .g3_common import RUN,REPORT,REVISION,BENCH,ENGINE,pool,adapter,raw,scores,status,validate


def freeze():
 from .runtime import model_identity
 from .day2_protocol import extract
 if REVISION.exists():validate();print('G3 protocol already frozen.',flush=True);return
 v=json.loads((RUN/'benchmark/vllm/complete.json').read_text())
 t=json.loads((RUN/'benchmark/transformers/complete.json').read_text())
 vr=read_jsonl(RUN/'benchmark/vllm/generated.jsonl')
 tr=[r for i in range(4) for r in read_jsonl(RUN/f'benchmark/transformers/worker_{i}/generated.jsonl')]
 assert len(vr)==len(tr)==32 and v['disk_adapter_unchanged']
 assert { (r['question_id'],r['candidate_index']) for r in vr }=={(r['question_id'],r['candidate_index']) for r in tr}
 assert all(0<r['output_tokens']<=r['max_new_tokens'] and r['prompt_tokens']+r['output_tokens']<=4096 for r in vr)
 assert all(r['finish_reason'] in ['stop','length'] for r in vr)
 def parse_count(rr):return sum(extract(r['text'],r['truncated'],'gsm8k')['status']=='extracted' for r in rr)
 # Never use correctness or V outcomes to choose an engine.
 engine='vllm' if v['total_seconds']<t['total_seconds'] else 'transformers'
 paths=[PROTOCOL,ROOT/'src/pilot/formal_train.py',ROOT/'src/pilot/formal_run.py',ROOT/'src/pilot/runtime.py',
  ROOT/'src/pilot/day3_protocol.py',ROOT/'src/pilot/day2_protocol.py',
  ROOT/'runs/formal/train/G2/report.json',adapter('G2')/'adapter_model.safetensors',adapter('G2')/'adapter_config.json']
 for g in ['G0','G1']:
  paths += [ROOT/f'runs/formal/{prefix}{g}_A.jsonl' for prefix in ['', 'V0_', 'labels_']]
 for g in ['G0','G1','G2']:
  paths += [ROOT/f'runs/formal/{prefix}{g}_C.jsonl' for prefix in ['', 'V0_', 'labels_']]
 cfg={'revision':'004_G2_to_G3_fixed_V0_extension_20261024','created_at':utc(),
  'purpose':'Test whether one more G update changes fixed V0 error rates; no claim G capability must improve.',
  'parent_checkpoint':'original formal G2_SFT','parent_adapter_fingerprint':model_identity('G',adapter('G2'))[1],
  'parent_path':str(adapter('G2')),'engine':engine,'engine_settings':ENGINE if engine=='vllm' else 'original TF BF16 SDPA plus PEFT',
  'vllm_execution':'Continuous engine.add_request/step, FINAL_ONLY n4; same underlying engine and sampling as benchmark; atomic completed-question caches.',
  'engine_decision':{'matched_questions':8,'candidates_each':4,'vllm':v,'transformers_4_processes':t,
   'vllm_completed':sum(not r['truncated'] for r in vr),'TF_completed':sum(not r['truncated'] for r in tr),
   'correctness_or_V_used_to_choose_engine':False,'vllm_parse_diagnostic':parse_count(vr),'TF_parse_diagnostic':parse_count(tr),
   'matched_wall_speed_ratio_TF_over_vllm':t['total_seconds']/v['total_seconds'],
   'runtime_LoRA_precision_limit':'vLLM BF16 vs original PEFT FP32; new G2 and G3 both use selected engine; old outputs only historical/bridge.'},
  'original_sources_sha256':{str(p.relative_to(ROOT)):sha(p) for p in paths},
  'input_sha256':{s:sha(RUN/f'inputs/{s}.jsonl') for s in ['A','C']},
  'questions':{'A':598,'C':95},'candidates_per_question':4,
  'whole_question_exclusions':{'A':['4b578eb4-1eb5-4e4e-9c89-68c2cffa64e0','93eb13ae-df32-4080-bdbf-36f09f2b6fc6'],
   'C':['original4 quarantine','24d751b7-4429-4605-9754-d1451911fb26']},
  'sampling':{'temperature':.7,'top_p':.9,'top_k':50,'seed':'42+int(digest(question_id)[:8],16)%1000000',
   'n':4,'max_context':4096,'max_new_tokens':'min(3840,4096-exact_prompt_tokens)','EOS':[151645,151643]},
  'fixed_V0':{'parameter_fingerprint':protocol()['models']['V']['fingerprint'],'adapter':None,
   'prompts':protocol()['prompts']['V_system'],'implementation_sha256':sha(ROOT/'src/pilot/runtime.py'),
   'engine':'original Transformers conditional A/B log-likelihood','threshold':.5,'context':8192},
  'selection':'Frozen select_feedback; V0>=0.5, complete nontruncated, at most one seeded random response per question, cap512. Truth read only AFTER selection commit.',
  'training':protocol()['training']['G'],'G3_initialization':'continue actual original G2 adapter; fresh optimizer moments, original seed42 LoRA SFT settings',
  'engineering_probe':'Compute 17 + 23. No B/C/T content in trainer.',
  'private_trainer_aliases':'Private trainer G1 parent directory points to original G2; private target G2 denotes extension G3. Original code unchanged.',
  'analysis':{'contrasts':['G3 minus G2 on A598','G3 minus G2 on C95','same G2 new-engine C minus cached original TF C'],
   'metrics':['FPR','FNR','balanced_accuracy','G_accuracy_verified','G_accuracy_lower_all','unknown_fraction','retention_all'],
   'bootstrap':10000,'seed':20261024,'unit':'paired whole questions, exploratory percentile95; no multiplicity correction or training-seed uncertainty',
   'A_role':'feedback training domain','C_role':'not used in G training; repeatedly used development diagnostic, not final test',
   'no_stopping_based_on_favorable_results':True},'B_used':False,'T_read':False,
  'old_protocol_current_pointer_unchanged':True}
 ensure_run_manifest(REVISION,cfg)
 status('protocol_frozen',engine=engine,A=598,C=95)
 write_json(REPORT/'engine_decision.json',cfg['engine_decision'])
 print(json.dumps({'frozen':True,'engine':engine,'benchmark_speedup':cfg['engine_decision']['matched_wall_speed_ratio_TF_over_vllm']}),flush=True)


def tf_worker(assignment):
 from . import formal_run as original
 a=json.loads(Path(assignment).read_text());by={r['question_id']:r for r in pool(a['split'])}
 rows=[by[q] for q in a['question_ids']];wd=Path(a['directory']);wd.mkdir(parents=True,exist_ok=True)
 original.RUN=wd;original.inputs=lambda split:rows if split==a['split'] else (_ for _ in ()).throw(ValueError(split))
 original.input_path=lambda split:RUN/f'inputs/{split}.jsonl'
 original.output_path=lambda *args:wd/'generated.jsonl';original.adapter_path=adapter
 original.gpu_lock=contextlib.nullcontext
 original.progress=lambda stage,**kw:write_json(wd/'progress.json',{'stage':stage,'updated_at':utc(),**kw})
 class Budget:
  def __init__(self,stage):pass
  def check(self):
   if (RUN/'cancel').exists():raise RuntimeError('Extension cancelled')
  def charge(self,unit):pass
 original.WorkBudget=Budget;original.run('generate',a['round'],a['split'])


def tf_generate(name):
 from .formal_parallel import merge_ready
 from .core import recover_jsonl
 for split in ['A','C']:
  rows=pool(split);saved=recover_jsonl(raw(name,split));paths=[];children=[];logs=[]
  with gpu_lock():
   budget=WorkBudget('g3_extension/TF/'+name+'/'+split)
   try:
    for i in range(4):
     wd=RUN/f'engine/{name}_{split}/worker_{i}';wd.mkdir(parents=True,exist_ok=True);paths.append(wd/'generated.jsonl')
     assignment=wd/'assignment.json';ensure_run_manifest(assignment,{'round':name,'split':split,
      'question_ids':[r['question_id'] for r in rows[i::4]],'directory':str(wd),'revision_sha256':sha(REVISION)})
     log=(wd/'worker.log').open('ab');logs.append(log)
     children.append(subprocess.Popen([str(ROOT/'scripts/pilot'),'g3_pipeline','tf_worker','--assignment',str(assignment)],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT))
    last=-1
    while True:
     merge_ready(raw(name,split),paths,rows,saved)
     if len(saved)!=last:status('generate_'+name,engine='transformers',split=split,candidates=len(saved),expected=len(rows)*4);budget.charge(str(len(saved)));last=len(saved)
     codes=[p.poll() for p in children]
     if any(c is not None and c!=0 for c in codes):raise RuntimeError(f'TF child failed:{codes}')
     if all(c is not None for c in codes):break
     budget.check();time.sleep(1)
    merge_ready(raw(name,split),paths,rows,saved);assert len(saved)==len(rows)*4
    budget.charge('finalization')
   finally:
    for p in children:
     if p.poll() is None:p.terminate()
    for p in children:
     try:p.wait(timeout=15)
     except subprocess.TimeoutExpired:p.kill();p.wait()
    for log in logs:log.close()


def score(name,split):
 from . import formal_run as original
 validate();rows=pool(split);directory=RUN/f'V0_scoring/{name}_{split}';directory.mkdir(parents=True,exist_ok=True)
 original.RUN=directory;original.inputs=lambda s:rows if s==split else (_ for _ in ()).throw(ValueError(s))
 original.output_path=lambda n,s,role='G':raw(name,split) if role=='G' else scores(name,split)
 original.progress=lambda stage,**kw:status('score_'+name+'_'+split,**kw)
 class Budget(WorkBudget):
  def __init__(self,stage):super().__init__('g3_extension/'+stage)
 original.WorkBudget=Budget;original.run('score',name,split)


def label(name,split):
 from .formal_labels import reference_path
 from .day2_protocol import label_response,VERSION
 refs={r['question_id']:r['gold_answer'] for r in read_jsonl(reference_path(split))}
 rows=read_jsonl(raw(name,split));assert len(rows)==len(pool(split))*4
 out=[]
 for r in rows:
  lab=label_response(r['text'],r['truncated'],refs[r['question_id']],'gsm8k')
  out.append({'question_id':r['question_id'],'candidate_id':r['candidate_id'],'split':split,'G_round':name,
   'correct':lab['correct'],'extracted_answer':lab['value'],'gold_answer':refs[r['question_id']],
   'verification_status':lab['status'],'unknown_reason':lab['status'] if lab['correct'] is None else None,
   'answer_format':lab.get('format'),'grader_version':VERSION+':dapo_exact_rational_v3',
   'label_source':'independent unchanged reference comparison; not used by selector'})
 write_jsonl(RUN/f'labels_{name}_{split}.jsonl',out);return out


def select():
 from transformers import AutoTokenizer
 from .day3_protocol import select_feedback,quality
 validate();rows=read_jsonl(raw('G2','A'));vs={r['candidate_id']:r for r in read_jsonl(scores('G2','A'))}
 assert len(rows)==len(vs)==2392
 picked=select_feedback(rows,vs,.5,42,512,max_length=4096)
 tok=AutoTokenizer.from_pretrained(protocol()['models']['G']['path'],local_files_only=True)
 selected=[];tensors=[];exclusions=[]
 for r in picked:
  ex=training_example(tok,messages('G',r['question']),r['text'],4096)
  if ex is None:exclusions.append({'candidate_id':r['candidate_id'],'reason':'exact template over4096; no replacement'});continue
  selected.append({k:r[k] for k in ['question_id','candidate_id','question','text','G_round','prompt_tokens','output_tokens']}|
   {'V0_score':vs[r['candidate_id']]['score'],'selection_used_gold':False,'input_tokens':len(ex['input_ids']),
    'supervised_tokens':sum(t!=-100 for t in ex['labels'][1:])})
  tensors.append({'question_id':r['question_id'],'candidate_id':r['candidate_id'],**ex})
 assert 0<len(selected)<=512
 directory=RUN/'stage_train/train/G2';directory.mkdir(parents=True,exist_ok=True)
 descriptor={'actual_source':'original G2','actual_target':'G3','revision_sha256':sha(REVISION),
  'generator_sha256':sha(raw('G2','A')),'V0_scores_sha256':sha(scores('G2','A')),
  'selected_ids':[r['candidate_id'] for r in selected],'truth_used':False,'exact_length_exclusions':exclusions}
 ensure_run_manifest(directory/'selection.manifest.json',descriptor)
 for name,rr in [('selected.jsonl',selected),('tokenized.jsonl',tensors)]:
  dest=directory/name
  if dest.exists():assert read_jsonl(dest)==rr
  else:write_jsonl(dest,rr)
 # Only after immutable selection exists do we join independent answers.
 labs={r['candidate_id']:r for r in label('G2','A')}
 write_json(REPORT/'G3_training_quality.json',{'selected_before_truth_join':True,
  'quality':quality([r['candidate_id'] for r in selected],labs),'questions':len(selected),
  'supervised_tokens':sum(r['supervised_tokens'] for r in selected),
  'optimizer_steps_expected':(len(selected)+7)//8,'exclusions':exclusions,
  'selected_sha256':sha(directory/'selected.jsonl'),'tokenized_sha256':sha(directory/'tokenized.jsonl')})
 status('G3_selection_committed',examples=len(selected),quality=json.loads((REPORT/'G3_training_quality.json').read_text())['quality'])


def train():
 import torch
 from . import formal_train as frozen
 from .runtime import load,model_identity
 cfg=validate();private=RUN/'stage_train';parent=private/'checkpoints/G1_SFT'
 parent.parent.mkdir(parents=True,exist_ok=True)
 if parent.is_symlink():assert parent.resolve()==adapter('G2').resolve()
 elif not parent.exists():parent.symlink_to(adapter('G2'),target_is_directory=True)
 else:raise RuntimeError('Unexpected private parent path; never overwrite parent')
 parent_stage=private/'train/G1';parent_stage.mkdir(parents=True,exist_ok=True)
 original_report=json.loads((ROOT/'runs/formal/train/G2/report.json').read_text())
 ensure_run_manifest(parent_stage/'report.json',original_report)
 if not (parent_stage/'probe_logits.pt').exists():
  with gpu_lock():
   budget=WorkBudget('g3_extension/independent_parent_probe')
   model,tok=load('G',adapter('G2'));ids=prompt_ids(tok,messages('G','Compute 17 + 23.'))[:128]
   with torch.inference_mode():logits=model(input_ids=torch.tensor([ids],device='cuda'),use_cache=False).logits[:,-1].float().cpu()
   torch.save(logits,parent_stage/'probe_logits.pt');del model;gc.collect();torch.cuda.empty_cache();budget.charge('probe_saved')
 frozen.RUN=private
 frozen.inputs=lambda split:[{'question':'Compute 17 + 23.'}] if split=='B' else (_ for _ in ()).throw(ValueError('Independent probe only'))
 frozen.progress=lambda stage,**kw:status('train_G3',**kw)
 class Budget(WorkBudget):
  def __init__(self,stage):super().__init__('g3_extension/'+stage)
 frozen.WorkBudget=Budget
 ensure_run_manifest(private/'actual_lineage.json',{'actual_source':'G2','actual_target':'G3',
  'parent_adapter_fingerprint':cfg['parent_adapter_fingerprint'],'independent_probe':'Compute 17 + 23.',
  'stage_aliases':cfg['private_trainer_aliases'],'frozen_trainer_sha256':sha(ROOT/'src/pilot/formal_train.py')})
 frozen.train('G2')
 r=json.loads((private/'train/G2/report.json').read_text())
 assert r['parent_adapter_fingerprint']==cfg['parent_adapter_fingerprint'] and r['save_reload_passed']
 assert r['parent_probe_max_logit_difference_before_update']<=.02
 final=RUN/'checkpoints/G3_SFT';final.parent.mkdir(exist_ok=True)
 if not final.exists():final.symlink_to(private/'checkpoints/G2_SFT',target_is_directory=True)
 assert final.resolve()==(private/'checkpoints/G2_SFT').resolve()
 write_json(RUN/'training_complete.json',{'actual_source':'G2','actual_target':'G3',
  'parent_adapter_fingerprint':r['parent_adapter_fingerprint'],'G3_adapter_fingerprint':model_identity('G',final)[1],
  'completed_at':utc(),'private_stage_alias_report':str(private/'train/G2/report.json')})


def pipeline():
 RUN.mkdir(exist_ok=True)
 try:
  freeze();cfg=validate()
  def call(args):subprocess.run(args,cwd=ROOT,check=True)
  for name in ['G2','G3']:
   if cfg['engine']=='vllm':call([str(ROOT/'scripts/pilot-vllm'),'-m','pilot.g3_stream_engine','--round',name])
   else:call([str(ROOT/'scripts/pilot'),'g3_pipeline','tf_generate','--round',name])
   for split in ['A','C']:
    call([str(ROOT/'scripts/pilot'),'g3_pipeline','score','--round',name,'--split',split])
   if name=='G2':
    call([str(ROOT/'scripts/pilot'),'g3_pipeline','select']);call([str(ROOT/'scripts/pilot'),'g3_pipeline','train'])
  call([str(ROOT/'scripts/pilot'),'g3_report'])
 except Exception as error:
  write_json(RUN/'state.json',{'status':'failed','failed_at':utc(),'error':str(error),
   'resume':'scripts/pilot g3_pipeline run','checkpoints_and_complete_chunks_retained':True,'T_read':False})
  raise


def main():
 p=argparse.ArgumentParser();p.add_argument('stage',choices=['freeze','run','tf_generate','tf_worker','score','select','train'])
 p.add_argument('--round',choices=['G2','G3']);p.add_argument('--split',choices=['A','C']);p.add_argument('--assignment');a=p.parse_args()
 if a.stage=='run':pipeline()
 elif a.stage=='freeze':freeze()
 elif a.stage=='tf_generate':tf_generate(a.round)
 elif a.stage=='tf_worker':tf_worker(a.assignment)
 elif a.stage=='score':score(a.round,a.split)
 elif a.stage=='select':select()
 else:train()

if __name__=='__main__':main()
