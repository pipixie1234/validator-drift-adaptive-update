"""Read-only frozen settings, formal cache paths and single-GPU work accounting."""
import contextlib
import fcntl
import json
import os
import time
from datetime import datetime,timezone
from .core import ROOT,read_jsonl,write_json,append_jsonl,digest
from .dapo_common import sha
from .day3_protocol import quarantined_ids,formal_generation_settings
from .runtime import model_identity

RUN=ROOT/'runs/formal'
REPORT=ROOT/'reports/formal'
PLAN=ROOT/'configs/formal_execution.json'
PROTOCOL=ROOT/'configs/protocol_revisions/003_dapo_day3.json'

def protocol():return json.loads(PROTOCOL.read_text())
def plan():return json.loads(PLAN.read_text())
def utc():return datetime.now(timezone.utc).isoformat()

def messages(role,question,response=None):
    p=protocol()['prompts']
    if role=='G':return [{'role':'system','content':p['G_system']},{'role':'user','content':question+p['G_user_suffix']}]
    return [{'role':'system','content':p['V_system']},{'role':'user','content':f'Question:\n{question}\n\nCandidate response:\n{response}'}]

def inputs(split):
    if split not in ['A','B','C']:raise ValueError('Only A/B/C authorized; T remains sealed')
    path=input_path(split);rows=read_jsonl(path)
    bad=quarantined_ids();rows=[r for r in rows if r['question_id'] not in bad]
    expected={'A':600,'B':200,'C':96}[split];assert len(rows)==expected
    assert all(not any(k in r for k in ['gold_answer','reference','correct']) for r in rows)
    return rows

def input_path(split):
    if split=='C':return ROOT/'data/dapo/inputs/dapo100.jsonl'
    if split not in ['A','B']:raise ValueError('T is sealed')
    return ROOT/f'data/dapo/formal/inputs/{"A_G_update" if split=="A" else "B_V_update"}.jsonl'

def output_path(round_name,split,role='G'):
    assert round_name in ['G0','G1','G2'] and split in ['A','B','C']
    return RUN/f'{round_name}_{split}.jsonl' if role=='G' else RUN/f'V0_{round_name}_{split}.jsonl'

def adapter_path(round_name):
    assert round_name in ['G0','G1','G2']
    if round_name=='G0':return None
    path=RUN/f'checkpoints/{round_name}_SFT'
    assert (path/'adapter_model.safetensors').exists(),f'Missing completed checkpoint:{path}'
    assert (RUN/f'train/{round_name}/report.json').exists(),'Do not infer from an incomplete training checkpoint'
    return path

def verify_fixed():
    p=protocol();ex=plan();assert sha(PROTOCOL)==ex['protocol_sha256']
    frozen=json.loads((ROOT/'reports/day3/artifact_audit.json').read_text())
    assert all(sha(ROOT/name)==h for name,h in frozen['frozen_implementation_sha256'].items())
    for role in ['G','V']:
        fp,a=model_identity(role);assert a is None and fp==p['models'][role]['fingerprint']
    for role,entry in json.loads((ROOT/'reports/day3/generation_defaults.json').read_text())['models'].items():
        assert sha(__import__('pathlib').Path(entry['path']))==entry['sha256']
    assert p['V_scoring']['accept_threshold']==.5
    assert sha(ROOT/p['formal_split_manifest'])==p['formal_split_sha256']
    assert sha(ROOT/p['answer_protocol_file'])==p['answer_protocol_sha256']
    assert sha(ROOT/p['quarantine_file'])==p['quarantine_sha256']

@contextlib.contextmanager
def gpu_lock():
    RUN.mkdir(exist_ok=True)
    with (RUN/'.gpu.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        try:yield
        finally:fcntl.flock(lock,fcntl.LOCK_UN)

class BudgetExceeded(RuntimeError):pass

class WorkBudget:
    def __init__(self,stage):
        self.stage=stage;self.start=time.perf_counter();self.last=self.start
        self.prior=plan()['prior_work_reserve_hours']*3600
        self.recorded=sum(r['seconds'] for r in read_jsonl(RUN/'work_ledger.jsonl'))
        self.cap=plan()['global_gpu_work_cap_hours']*3600
        self.check()
    def check(self):
        if self.prior+self.recorded+time.perf_counter()-self.start+120>self.cap:
            raise BudgetExceeded('40h work budget reached; saved outputs/checkpoints retained, no settings or samples silently reduced')
    def charge(self,unit):
        now=time.perf_counter();append_jsonl(RUN/'work_ledger.jsonl',{'stage':self.stage,'unit':unit,
            'seconds':now-self.last,'recorded_at':utc(),'pid':os.getpid()});self.last=now

def progress(stage,**fields):
    write_json(RUN/'progress.json',{'stage':stage,'updated_at':utc(),'pid':os.getpid(),**fields})

def preflight():
    verify_fixed();p=protocol();frozen=json.loads((ROOT/'reports/day3/artifact_audit.json').read_text())
    old=json.loads((ROOT/'runs/dapo/protected_day2.json').read_text())
    assert all(sha(ROOT/name)==h for name,h in old.items())
    assert sha(ROOT/'runs/dapo/G0_dapo100.jsonl')==p['preserved_original_G400_sha256']
    assert sha(ROOT/'runs/dapo/V0_dapo100.jsonl')==p['preserved_original_V400_sha256']
    splits=json.loads((ROOT/p['formal_split_manifest']).read_text());sets=[]
    for name,r in splits['splits'].items():
        path=ROOT/f'data/dapo/formal/inputs/{name}.jsonl'
        assert sha(path)==r['input_sha256']
        assert sha(ROOT/f'data/dapo/formal/labels/{name}.jsonl')==r['labels_sha256']
        # T is hashed only; neither its questions nor its labels are decoded/evaluated.
        sets.append(set(r['question_ids']))
    c={r['question_id'] for r in read_jsonl(ROOT/'data/dapo/inputs/dapo100.jsonl')}
    sets.append(c);assert len(set.union(*sets))==sum(map(len,sets))
    assert {s:len(inputs(s)) for s in ['A','B','C']}=={'A':600,'B':200,'C':96}
    g=read_jsonl(ROOT/'runs/dapo/G0_dapo100.jsonl');v=read_jsonl(ROOT/'runs/dapo/V0_dapo100.jsonl')
    assert len(g)==len(v)==400 and {r['candidate_id'] for r in g}=={r['candidate_id'] for r in v}
    assert all(r['model_fingerprint']==p['models']['V']['fingerprint'] and r['adapter_fingerprint'] is None for r in v)
    assert all(r['prompt_tokens']+4<=8192 for r in v)
    assert len(read_jsonl(ROOT/'runs/day3/quarantined_C_candidates.jsonl'))==16
    for role in ['G','V']:
        report=json.loads((ROOT/f'runs/day3/smoke_{role}_report.json').read_text())
        assert report['save_reload_passed'] and report['base_parameter_versions_unchanged']
    result={'passed':True,'performed_at':utc(),'protocol_sha256':sha(PROTOCOL),'day2_preserved_files':len(old),
        'fixed_V0_model_prompts_scoring_threshold_checked':True,'frozen_implementation_files_checked':len(frozen['frozen_implementation_sha256']),
        'effective_questions':{'A':600,'B':200,'C':96},'new_generation_budget':p['G_output_budget_rule'],
        'T_hashed_only_not_decoded':True,'smoke_adapters_excluded_from_trajectory':True,
        'previous_3072_outputs_not_used_as_formal_baseline':True}
    REPORT.mkdir(exist_ok=True);write_json(REPORT/'preflight_audit.json',result);return result
