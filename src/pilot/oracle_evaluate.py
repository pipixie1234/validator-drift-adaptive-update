"""Original per-question sampler, isolated oracle-control caches, one-card workers."""
import argparse
import contextlib
import json
import os
import subprocess
import time
from pathlib import Path

from .core import ROOT, read_jsonl, recover_jsonl, write_json, ensure_run_manifest
from .formal_common import inputs, sha, utc, WorkBudget, gpu_lock
from .formal_parallel import merge_ready
from .oracle_control import RUN, REPORT, CONFIG, ARMS, checkpoint, validate_plan


def generated(arm):return RUN/arm/'eval/generated_C.jsonl'
def scored(arm):return RUN/arm/'eval/V0_C.jsonl'


def worker(assignment):
    from . import formal_run as original
    a=json.loads(Path(assignment).read_text());arm=a['arm'];assert arm in ARMS
    assert a['worker_code_sha256']==sha(Path(__file__))
    assert a['study_sha256']==sha(CONFIG)
    pool={r['question_id']:r for r in inputs('C')};rows=[pool[q] for q in a['question_ids']]
    directory=Path(a['directory']);directory.mkdir(parents=True,exist_ok=True)
    original.RUN=directory
    original.inputs=lambda split:rows if split=='C' else (_ for _ in ()).throw(ValueError('C only'))
    original.output_path=lambda *args:directory/'generated.jsonl'
    original.adapter_path=lambda round_name:checkpoint(arm)
    original.gpu_lock=contextlib.nullcontext
    original.progress=lambda stage,**fields:write_json(directory/'progress.json',{'stage':stage,'updated_at':utc(),**fields})
    class CoordinatorBudget:
        def __init__(self,stage):pass
        def check(self):
            if (RUN/'cancel_evaluation').exists():raise RuntimeError('Coordinator requested stop')
        def charge(self,unit):pass
    original.WorkBudget=CoordinatorBudget
    original.run('generate',arm,'C')


def generate():
    validate_plan();rows=inputs('C');expected=len(rows)*4
    descriptor={'study_sha256':sha(CONFIG),'engine':'original Transformers BF16 SDPA',
                'wrapper_sha256':sha(Path(__file__)),'frozen_sampler_sha256':sha(ROOT/'src/pilot/formal_run.py'),
                'single_GPU_workers':4,'workers_per_arm':2,'candidate_sampling_and_question_seed_unchanged':True,
                'V0_scoring_implementation_unchanged':True,'C_questions':len(rows),'candidates_per_arm':expected,
                'vllm':'Not installed: mirrored wheel transfer0.25-0.45MiB/s plus dependencies; preserve original engine in this control.',
                'T_read':False,'B_read':False}
    ensure_run_manifest(RUN/'evaluation.manifest.json',descriptor)
    with gpu_lock():
        budget=WorkBudget('oracle_control/parallel_C_generation')
        children=[];logs=[];worker_paths={arm:[] for arm in ARMS};saved={}
        try:
            for arm in ARMS:
                generated(arm).parent.mkdir(parents=True,exist_ok=True)
                saved[arm]=recover_jsonl(generated(arm))
                for i in range(2):
                    wd=RUN/arm/f'eval/worker_{i}';wd.mkdir(exist_ok=True)
                    worker_paths[arm].append(wd/'generated.jsonl')
                    assignment=wd/'assignment.json'
                    ensure_run_manifest(assignment,{'arm':arm,'question_ids':[r['question_id'] for r in rows[i::2]],
                        'directory':str(wd),'worker_code_sha256':sha(Path(__file__)),'study_sha256':sha(CONFIG)})
                    log=(wd/'worker.log').open('ab');logs.append(log)
                    env=dict(os.environ,CUDA_VISIBLE_DEVICES='0')
                    children.append(subprocess.Popen([str(ROOT/'scripts/pilot'),'oracle_evaluate','worker','--assignment',str(assignment)],
                                                     cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT))
            last=-1
            while True:
                for arm in ARMS:merge_ready(generated(arm),worker_paths[arm],rows,saved[arm])
                total=sum(len(saved[arm]) for arm in ARMS)
                if total!=last:
                    state={'stage':'C_generation','updated_at':utc(),'workers':4,'GPU_count':1,
                           'candidates':{arm:len(saved[arm]) for arm in ARMS},'expected_per_arm':expected,
                           'worker_pids':[p.pid for p in children if p.poll() is None]}
                    write_json(RUN/'progress.json',state);print(json.dumps(state),flush=True)
                    budget.charge(f'candidates_{total}');last=total
                codes=[p.poll() for p in children]
                if any(c is not None and c!=0 for c in codes):raise RuntimeError(f'Evaluation worker failed:{codes}; inspect worker logs')
                if all(c is not None for c in codes):break
                budget.check();time.sleep(1)
            for arm in ARMS:
                merge_ready(generated(arm),worker_paths[arm],rows,saved[arm])
                assert len(saved[arm])==expected
            budget.charge('generation_finalization')
            write_json(RUN/'generation_complete.json',{'completed_at':utc(),'counts':{arm:len(saved[arm]) for arm in ARMS},'worker_exit_codes':[p.returncode for p in children]})
        finally:
            for p in children:
                if p.poll() is None:p.terminate()
            for p in children:
                try:p.wait(timeout=15)
                except subprocess.TimeoutExpired:p.kill();p.wait()
            for log in logs:log.close()


def score(arm):
    from . import formal_run as original
    validate_plan();assert arm in ARMS
    original.RUN=RUN/arm/'eval'
    original.output_path=lambda round_name,split,role='G':generated(arm) if role=='G' else scored(arm)
    original.progress=lambda stage,**fields:write_json(RUN/arm/'eval/progress.json',{'stage':stage,'updated_at':utc(),**fields})
    class ControlBudget(WorkBudget):
        def __init__(self,stage):super().__init__('oracle_control/'+stage)
    original.WorkBudget=ControlBudget
    original.run('score',arm,'C')


def pipeline():
    commands=[['generate']]+[['score','--arm',arm] for arm in ARMS]
    for args in commands:
        command=[str(ROOT/'scripts/pilot'),'oracle_evaluate',*args]
        subprocess.run(command,cwd=ROOT,check=True)
    subprocess.run([str(ROOT/'scripts/pilot'),'oracle_report'],cwd=ROOT,check=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['worker','generate','score','run']);p.add_argument('--assignment');p.add_argument('--arm',choices=ARMS);a=p.parse_args()
    if a.stage=='worker':worker(a.assignment)
    elif a.stage=='generate':generate()
    elif a.stage=='score':score(a.arm)
    else:pipeline()


if __name__=='__main__':main()
