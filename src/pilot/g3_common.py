"""Isolated G2->G3 extension. Frozen original experiments remain read-only."""
import json
from pathlib import Path
from .core import ROOT,read_jsonl,write_json,write_jsonl,ensure_run_manifest,digest
from .formal_common import inputs as old_inputs,PROTOCOL,protocol,sha,utc
from .cache_followup import IMAGE,REFERENCE_A
from .explanation_probes import SUSPECT

RUN=ROOT/'runs/g3_extension_20261024'
REPORT=ROOT/'reports/g3_extension_20261024'
REVISION=ROOT/'configs/protocol_revisions/004_g3_extension_20261024.json'
BENCH=RUN/'benchmark_plan.json'
ENGINE={'dtype':'bfloat16','lora_dtype':'bfloat16','max_model_len':4096,
 'tensor_parallel_size':1,'gpu_memory_utilization':.70,'max_num_seqs':64,
 'max_num_batched_tokens':4096,'enable_lora':True,'max_lora_rank':16,'max_loras':1,
 'enforce_eager':True,'seed':42,'generation_config':'vllm'}

def pool(split):
 if split not in ['A','C']:raise ValueError('Only A/C; B reserved and T sealed')
 return read_jsonl(RUN/f'inputs/{split}.jsonl')

def adapter(name):
 assert name in ['G2','G3']
 return ROOT/'runs/formal/checkpoints/G2_SFT' if name=='G2' else RUN/'checkpoints/G3_SFT'

def raw(name,split):return RUN/f'{name}_{split}.jsonl'
def scores(name,split):return RUN/f'V0_{name}_{split}.jsonl'
def status(stage,**kw):write_json(RUN/'progress.json',{'stage':stage,'updated_at':utc(),**kw})

def prepare_benchmark():
 from .formal_multigpu_revision import verify_execution_revision
 verify_execution_revision();RUN.mkdir(exist_ok=True);REPORT.mkdir(exist_ok=True)
 a=[r for r in old_inputs('A') if r['question_id'] not in {IMAGE,REFERENCE_A}]
 c=[r for r in old_inputs('C') if r['question_id']!=SUSPECT]
 assert len(a)==598 and len(c)==95
 for split,rows in [('A',a),('C',c)]:
  path=RUN/f'inputs/{split}.jsonl'
  if path.exists():assert read_jsonl(path)==rows
  else:write_jsonl(path,rows)
 # One maximum-length old G2 output question stresses the frozen native context.
 old=read_jsonl(ROOT/'runs/formal/G2_C.jsonl')
 longest=max((r for r in old if r['question_id']!=SUSPECT),key=lambda r:(r['output_tokens'],r['question_id']))['question_id']
 ordered=sorted(c,key=lambda r:digest('g3-vllm-benchmark-20261024:'+r['question_id']))
 ids=[longest]+[r['question_id'] for r in ordered if r['question_id']!=longest][:7]
 ensure_run_manifest(BENCH,{'created_for':'G3 execution engine decision, not quality selection',
  'question_ids':ids,'G_checkpoint':'original formal G2','candidates_each':4,
  'engine':ENGINE,'batch_questions':16,'maximum_new_tokens':3840,
  'rule':'Same complete prompt tokens, temperature0.7,top_p0.9,top_k50,per-question seeds42+hash,stop151645/151643; native4096 total.',
  'eligibility':'No model-quality criterion; choose vLLM if compatibility succeeds and matched workload wall time improves; otherwise original TF fallback.',
  'LoRA_precision':'vLLM BF16 runtime vs original PEFT FP32; neither exact numerical nor sampling equivalence claimed.',
  'source_protocol_sha256':sha(PROTOCOL),'T_read':False})
 print(json.dumps({'benchmark_ready':True,'A':598,'C':95,'benchmark_questions':8}),flush=True)


def validate():
 cfg=json.loads(REVISION.read_text())
 assert all(sha(ROOT/n)==h for n,h in cfg['original_sources_sha256'].items())
 assert all(sha(RUN/f'inputs/{s}.jsonl')==cfg['input_sha256'][s] for s in ['A','C'])
 from .formal_common import verify_fixed
 verify_fixed();return cfg
