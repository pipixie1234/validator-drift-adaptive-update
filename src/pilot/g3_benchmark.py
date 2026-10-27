"""Matched eight-question original-TF benchmark; isolated from research caches."""
import argparse
import contextlib
import json
import os
import subprocess
import time
from pathlib import Path
from .core import ROOT,read_jsonl,write_json,ensure_run_manifest
from .formal_common import gpu_lock,WorkBudget,utc,sha
from .g3_common import RUN,BENCH,pool,status


def worker(index):
 from . import formal_run as old
 ids=json.loads(BENCH.read_text())['question_ids'][index::4]
 by={r['question_id']:r for r in pool('C')};rows=[by[q] for q in ids]
 wd=RUN/f'benchmark/transformers/worker_{index}';wd.mkdir(parents=True,exist_ok=True)
 old.RUN=wd;old.inputs=lambda split:rows if split=='C' else (_ for _ in ()).throw(ValueError(split))
 old.output_path=lambda *args:wd/'generated.jsonl';old.gpu_lock=contextlib.nullcontext
 old.progress=lambda stage,**kw:write_json(wd/'progress.json',{'stage':stage,'updated_at':utc(),**kw})
 class Budget:
  def __init__(self,stage):pass
  def check(self):pass
  def charge(self,unit):pass
 old.WorkBudget=Budget;old.run('generate','G2','C')


def run():
 directory=RUN/'benchmark/transformers';directory.mkdir(parents=True,exist_ok=True)
 if (directory/'complete.json').exists():print('TF benchmark cached.',flush=True);return
 ensure_run_manifest(directory/'manifest.json',{'benchmark_plan_sha256':sha(BENCH),'workers':4,
  'engine':'original Transformers BF16 SDPA + PEFT FP32 adapter','code_sha256':sha(Path(__file__)),
  'generator_sha256':sha(ROOT/'src/pilot/formal_run.py'),'question_count':8,'candidates':32})
 with gpu_lock():
  budget=WorkBudget('g3_extension/matched_TF_benchmark');start=time.perf_counter();children=[];logs=[]
  try:
   for i in range(4):
    wd=directory/f'worker_{i}';wd.mkdir(exist_ok=True);log=(wd/'worker.log').open('ab');logs.append(log)
    children.append(subprocess.Popen([str(ROOT/'scripts/pilot'),'g3_benchmark','worker','--index',str(i)],
     cwd=ROOT,env=dict(os.environ,CUDA_VISIBLE_DEVICES='0'),stdout=log,stderr=subprocess.STDOUT))
   last=-1
   while True:
    n=sum(len(read_jsonl(directory/f'worker_{i}/generated.jsonl')) for i in range(4))
    if n!=last:status('TF_matched_benchmark',candidates=n,expected=32);budget.charge(f'candidates_{n}');last=n
    codes=[p.poll() for p in children]
    if any(c is not None and c!=0 for c in codes):raise RuntimeError(f'TF benchmark failed:{codes}')
    if all(c is not None for c in codes):break
    budget.check();time.sleep(1)
   rows=[r for i in range(4) for r in read_jsonl(directory/f'worker_{i}/generated.jsonl')]
   assert len(rows)==32
   elapsed=time.perf_counter()-start
   write_json(directory/'complete.json',{'completed_at':utc(),'total_seconds':elapsed,
    'output_tokens':sum(r['output_tokens'] for r in rows),'candidates':32,
    'engine':'4 original Transformers processes, whole-question sharded',
    'tokens_per_wall_second':sum(r['output_tokens'] for r in rows)/elapsed,
    'manifest_sha256':sha(directory/'manifest.json')})
   budget.charge('finalization');print(json.dumps({'TF_benchmark_complete':True,'seconds':elapsed}),flush=True)
  finally:
   for p in children:
    if p.poll() is None:p.terminate()
   for p in children:
    try:p.wait(timeout=15)
    except subprocess.TimeoutExpired:p.kill();p.wait()
   for log in logs:log.close()


def main():
 p=argparse.ArgumentParser();p.add_argument('stage',choices=['run','worker']);p.add_argument('--index',type=int);a=p.parse_args()
 worker(a.index) if a.stage=='worker' else run()

if __name__=='__main__':main()
