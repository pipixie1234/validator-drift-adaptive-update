"""Matched G0-initialized oracle vs V-feedback diagnostic; isolated from formal runs."""
import argparse
import contextlib
import hashlib
import json
import math
import os
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from .core import ROOT, read_jsonl, write_json, write_jsonl, ensure_run_manifest, training_example, digest
from .formal_common import RUN as FORMAL, REPORT as FORMAL_REPORT, PROTOCOL, protocol, messages, inputs, sha, utc, WorkBudget, gpu_lock
from .formal_multigpu_revision import verify_execution_revision

RUN=ROOT/'runs/oracle_control_20261022'
REPORT=ROOT/'reports/oracle_control_20261022'
CONFIG=ROOT/'configs/oracle_control_20261022.json'
ARMS=['Vmatched_SFT','Oracle_SFT']


def checkpoint(arm):
    assert arm in ARMS
    # Private reuse of the frozen trainer's one-update G1 stage, both initialized at G0.
    return RUN/arm/'checkpoints/G1_SFT'


def prepare():
    from transformers import AutoTokenizer
    from scipy.optimize import milp, Bounds, LinearConstraint
    from scipy.sparse import csc_matrix
    verify_execution_revision();RUN.mkdir(exist_ok=True);REPORT.mkdir(exist_ok=True)
    if CONFIG.exists():
        validate_plan();print('Prepared matched control is immutable and cached.',flush=True);return
    p=protocol();raw=read_jsonl(FORMAL/'G0_A.jsonl')
    labels={r['candidate_id']:r for r in read_jsonl(FORMAL/'labels_G0_A.jsonl')}
    old_selected=read_jsonl(FORMAL/'train/G1/selected.jsonl')
    tok=AutoTokenizer.from_pretrained(p['models']['G']['path'],local_files_only=True)
    eligible=defaultdict(list);exact={}
    for r in raw:
        if labels[r['candidate_id']]['correct'] is not True or r['truncated']:continue
        ex=training_example(tok,messages('G',r['question']),r['text'],4096)
        if ex is None:continue
        exact[r['candidate_id']]=ex;eligible[r['question_id']].append(r)
    byv={r['question_id']:r for r in old_selected}
    qs=sorted(set(byv)&set(eligible));assert len(qs)>0
    vrows=[byv[q] for q in qs]
    vt={r['candidate_id']:r for r in read_jsonl(FORMAL/'train/G1/tokenized.jsonl')}
    vcounts=np.array([sum(t!=-100 for t in vt[r['candidate_id']]['labels'][1:]) for r in vrows])
    options=[];qindices=[];counts=[];cost=[]
    for i,q in enumerate(qs):
        for r in sorted(eligible[q],key=lambda s:s['candidate_id']):
            count=sum(t!=-100 for t in exact[r['candidate_id']]['labels'][1:])
            options.append(r);qindices.append(i);counts.append(count)
            # Minimize within-question length changes; prefer identical already-correct texts on ties.
            tie=int(hashlib.sha256(r['candidate_id'].encode()).hexdigest()[:8],16)/2**32
            cost.append(abs(count-vcounts[i])+(r['candidate_id']!=byv[q]['candidate_id'])*1e-3+tie*1e-6)
    target=int(vcounts.sum());cols=np.arange(len(options))
    mat=csc_matrix((np.r_[np.ones(len(options)),counts],(np.r_[qindices,np.full(len(options),len(qs))],np.r_[cols,cols])),shape=(len(qs)+1,len(options)))
    b=np.r_[np.ones(len(qs)),target]
    solve=milp(np.array(cost),integrality=np.ones(len(options)),bounds=Bounds(0,1),constraints=LinearConstraint(mat,b,b),options={'time_limit':30,'mip_rel_gap':0})
    if solve.x is None:
        # Predeclared fallback: match total within0.5%, still unique complete responses.
        low=b.copy();high=b.copy();low[-1]=target*.995;high[-1]=target*1.005
        solve=milp(np.array(cost),integrality=np.ones(len(options)),bounds=Bounds(0,1),constraints=LinearConstraint(mat,low,high),options={'time_limit':30,'mip_rel_gap':0})
    if solve.x is None:raise RuntimeError('Cannot match total supervised tokens within0.5%; no training started, inspect eligible lengths')
    picked=[r for r,x in zip(options,solve.x) if x>.5]
    byo={r['question_id']:r for r in picked};assert set(byo)==set(qs) and len(picked)==len(qs)
    orows=[byo[q] for q in qs]
    detail=[];summaries={}
    for arm,rows in zip(ARMS,[vrows,orows]):
        train=RUN/arm/'train/G1';train.mkdir(parents=True,exist_ok=True)
        samples=[];tensors=[]
        for r in rows:
            ex=training_example(tok,messages('G',r['question']),r['text'],4096);assert ex is not None and not r.get('truncated',False)
            if arm=='Vmatched_SFT':
                assert ex['input_ids']==vt[r['candidate_id']]['input_ids'] and ex['labels']==vt[r['candidate_id']]['labels']
            sup=sum(t!=-100 for t in ex['labels'][1:])
            samples.append({k:r[k] for k in ['question_id','candidate_id','question','text','G_round','prompt_tokens','output_tokens']}|
                           {'input_tokens':len(ex['input_ids']),'supervised_tokens':sup,
                            'selection_used_gold':arm=='Oracle_SFT'})
            tensors.append({'question_id':r['question_id'],'candidate_id':r['candidate_id'],**ex})
            detail.append({'arm':arm,'question_id':r['question_id'],'candidate_id':r['candidate_id'],
                           'correct':labels[r['candidate_id']]['correct'],'supervised_tokens':sup,
                           'gold_used_to_select_answer':arm=='Oracle_SFT','shared_oracle_eligible_question_restriction':True})
        write_jsonl(train/'selected.jsonl',samples);write_jsonl(train/'tokenized.jsonl',tensors)
        truth=[labels[r['candidate_id']]['correct'] for r in rows]
        summaries[arm]={'examples':len(samples),'questions':len(samples),'correct':sum(t is True for t in truth),
                        'wrong':sum(t is False for t in truth),'unknown':sum(t is None for t in truth),
                        'input_tokens':sum(r['input_tokens'] for r in samples),'supervised_tokens':sum(r['supervised_tokens'] for r in samples),
                        'optimizer_steps':math.ceil(len(samples)/8),'selected_sha256':sha(train/'selected.jsonl'),
                        'tokenized_sha256':sha(train/'tokenized.jsonl')}
    assert summaries['Oracle_SFT']['wrong']==summaries['Oracle_SFT']['unknown']==0
    assert abs(summaries['Oracle_SFT']['supervised_tokens']/target-1)<=.005
    write_jsonl(RUN/'training_quality_audit.jsonl',detail)
    write_jsonl(RUN/'common_A_questions.jsonl',[{'question_id':q} for q in qs])
    original_paths=[FORMAL/f'{prefix}G0_A.jsonl' for prefix in ['', 'V0_', 'labels_']]
    original_paths += [FORMAL/'train/G1'/n for n in ['selected.jsonl','tokenized.jsonl','report.json']]
    original_paths += [FORMAL/f'{prefix}{g}_C.jsonl' for prefix in ['', 'V0_', 'labels_'] for g in ['G0','G1','G2']]
    original_paths += [PROTOCOL]
    cfg={'study':'oracle_feedback_quality_control_v1_20261022','created_at':utc(),'arms':ARMS,'initialization':'Both original G0, fresh independent LoRA, no G1/G2 adapter inheritance',
         'source':'Frozen G0_A existing4 candidates; independent frozen exact final-answer labels',
         'matching':'Identical sorted questions: original G1 actual V0-selected question intersect question with at least one complete correct G0 candidate. V arm keeps original chosen candidate; oracle arm chooses one independently correct candidate per same question.',
         'token_matching':'MILP one complete unique response per question; exact total response loss token target, fallback<=0.5%; minimize sum within-question absolute length differences, prefer identical candidate on ties; no repetition, packing, or truncation',
         'oracle_correct_questions_all':len(eligible),'original_V_questions':len(old_selected),'common_questions':len(qs),
         'oracle_questions_without_original_V':sorted(set(eligible)-set(byv)),
         'original_V_questions_without_correct_G0':sorted(set(byv)-set(eligible)),
         'MILP_status':int(solve.status),'MILP_message':str(solve.message),
         'same_candidate_in_both_arms':sum(v['candidate_id']==o['candidate_id'] for v,o in zip(vrows,orows)),
         'per_question_token_abs_difference':{'mean':float(np.mean([abs(sum(t!=-100 for t in exact[o['candidate_id']]['labels'][1:])-vcounts[i]) for i,o in enumerate(orows)])),
                                              'max':int(max(abs(sum(t!=-100 for t in exact[o['candidate_id']]['labels'][1:])-vcounts[i]) for i,o in enumerate(orows)))},
         'training':p['training']['G'],'optimizer_implementation':json.loads((ROOT/'configs/formal_execution.json').read_text())['training_implementation_choices'],
         'training_order':'Same sorted question order before original seed42 shuffle; same question positions and accumulation groups in both arms',
         'engineering_probe':'Independent fixed text: Compute 17 + 23. No B/C/T content is used by the trainer.',
         'private_frozen_trainer_stage_alias':'G1 stage reused under each isolated arm directory; this does not denote the original formal G1',
         'summaries':summaries,'frozen_protocol_sha256':sha(PROTOCOL),
         'original_sources_sha256':{str(path.relative_to(ROOT)):sha(path) for path in original_paths},
         'wrapper_sha256':sha(Path(__file__)),
         'evaluation':{'split':'C','questions':96,'candidates_per_question':4,'protocol':'Same frozen prompts, sample4,temp0.7,top_p0.9,native4096,max_new3840,question seed42; V0 original A/B scoring threshold0.5',
                       'report_original_C96':True,'primary_reference_sensitivity_C95':True,'reference1_overlay':True,'T_read':False,'B_used':False},
         'limits':['Question set conditioned on G0 having a correct candidate; relatively easier subset, does not cover all A.',
                   'Matched V branch is cleaner than original500-example branch; old G1 is context only, not the matched causal comparator.',
                   'Final-answer oracle labels do not certify reasoning processes or every public source reference.',
                   'Single training seed; development-only evaluation, no final test claims.']}
    write_json(CONFIG,cfg)
    (REPORT/'README.md').write_text('# Oracle反馈质量对照\n\n两组从G0出发；共同题目、监督token和更新预算匹配。原正式结果不修改，T封存。\n\n配置：`configs/oracle_control_20261022.json`；训练与推理：`runs/oracle_control_20261022/`。\n')
    print(json.dumps({'prepared':True,'summaries':summaries,'same_candidates':cfg['same_candidate_in_both_arms']},ensure_ascii=False),flush=True)


def validate_plan():
    cfg=json.loads(CONFIG.read_text());assert cfg['wrapper_sha256']==sha(Path(__file__))
    verify_execution_revision()
    assert sha(PROTOCOL)==cfg['frozen_protocol_sha256']
    assert all(sha(ROOT/name)==h for name,h in cfg['original_sources_sha256'].items())
    for arm in ARMS:
        assert all(sha(RUN/arm/'train/G1'/name)==cfg['summaries'][arm][key] for name,key in [('selected.jsonl','selected_sha256'),('tokenized.jsonl','tokenized_sha256')])
    return cfg


def train(arm):
    from . import formal_train as frozen
    cfg=validate_plan();assert arm in ARMS
    ensure_run_manifest(RUN/arm/'control_provenance.json',{'study_sha256':sha(CONFIG),'arm':arm,'wrapper_sha256':sha(Path(__file__)),
        'frozen_trainer_sha256':sha(ROOT/'src/pilot/formal_train.py'),'independent_probe':cfg['engineering_probe'],
        'fresh_G0':True,'truth_fields_absent_from_token_tensors':True})
    frozen.RUN=RUN/arm
    frozen.inputs=lambda split:[{'question':'Compute 17 + 23.'}] if split=='B' else (_ for _ in ()).throw(ValueError('Training probe must not read data pools'))
    frozen.progress=lambda stage,**fields:write_json(RUN/arm/'progress.json',{'stage':stage,'updated_at':utc(),**fields})
    class ControlBudget(WorkBudget):
        def __init__(self,stage):super().__init__(f'oracle_control/{arm}/{stage}')
    frozen.WorkBudget=ControlBudget
    frozen.train('G1')
    result=json.loads((RUN/arm/'train/G1/report.json').read_text())
    assert result['source_generator']=='G0' and result['parent_adapter_fingerprint'] is None
    assert result['supervised_tokens']==cfg['summaries'][arm]['supervised_tokens']
    assert result['optimizer_steps']==cfg['summaries'][arm]['optimizer_steps']
    write_json(RUN/arm/'training_complete.json',{'arm':arm,'fresh_G0':True,'report_sha256':sha(RUN/arm/'train/G1/report.json'),'ended_at':utc()})


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['prepare','train']);p.add_argument('--arm',choices=ARMS)
    a=p.parse_args()
    if a.stage=='prepare':prepare()
    else:assert a.arm;train(a.arm)


if __name__=='__main__':main()
