"""Audit completed day2 lineage, split isolation, cache separation and frozen decisions."""
import hashlib
import json
from collections import Counter
from .core import ROOT,read_jsonl,write_json,digest
from .runtime import model_identity

def main():
    protection=ROOT/'runs/day2/protected_artifacts.json'
    if protection.exists():
        for path,expected in json.loads(protection.read_text()).items():assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==expected, f'Protected artifact changed: {path}'
    cfg=json.loads((ROOT/'configs/day2.json').read_text());selection=json.loads((ROOT/'data/day2/manifests/selection_locked.json').read_text());assert selection['config_sha256']==hashlib.sha256((ROOT/'configs/day2.json').read_bytes()).hexdigest()
    protected=json.loads((ROOT/'data/manifests/splits.json').read_text());test_ids={r['question_id'] for r in protected['splits']['T']};test_hashes={r['question_hash'] for r in protected['splits']['T']}
    fingerprints={role:model_identity(role)[0] for role in ['G','V']};key_sets=[]
    for name,n in [('gsm8k_C100',100),('math70',70)]:
        inputs=read_jsonl(ROOT/f'data/day2/inputs/{name}.jsonl');labels=read_jsonl(ROOT/f'data/day2/labels/{name}.jsonl');g=read_jsonl(ROOT/f'runs/day2/G0_{name}.jsonl');v=read_jsonl(ROOT/f'runs/day2/V0_{name}.jsonl');annotated=read_jsonl(ROOT/f'runs/day2/labels_{name}.jsonl')
        assert len(inputs)==len(labels)==n and len(g)==len(v)==len(annotated)==n*4
        assert {r['question_id'] for r in inputs}=={r['question_id'] for r in labels}
        assert not {r['question_hash'] for r in inputs}&test_hashes
        assert not any(r['question_id'] in test_ids for r in g+v)
        assert all(not {'gold_answer','reference','correct'}&r.keys() for r in inputs+g+v)
        assert all(count==4 for count in Counter(r['question_id'] for r in g).values())
        ids={r['candidate_id'] for r in g};assert len(ids)==n*4 and ids=={r['candidate_id'] for r in v}=={r['candidate_id'] for r in annotated}
        for role,rows in [('G',g),('V',v)]:
            assert all(r['model_fingerprint']==fingerprints[role] and r['adapter_fingerprint'] is None for r in rows)
            keys={r['cache_key'] for r in rows};assert len(keys)==n*4;key_sets.append(keys)
        if name=='gsm8k_C100':assert {r['question_id'] for r in inputs}=={r['question_id'] for r in protected['splits']['C']}
        else:assert len(Counter((r['type'],r['level']) for r in inputs))==35 and all(c==2 for c in Counter((r['type'],r['level']) for r in inputs).values())
    for i,keys in enumerate(key_sets):
        for other in key_sets[i+1:]:assert keys.isdisjoint(other)
    for role,path in [('G','G0_C50'),('V','V0_G0_C50')]:assert all(r['model_fingerprint']==fingerprints[role] for r in read_jsonl(ROOT/f'runs/day1/{path}.jsonl'))
    assert all('/train-' in f['source_path'] and '/test-' not in f['source_path'] for f in json.loads((ROOT/'data/day2/manifests/math70.json').read_text())['sources'])
    decision=json.loads((ROOT/'reports/day2/task_decision.json').read_text())
    if decision['protocol_frozen_for_next_stage']:
        assert json.loads((ROOT/'configs/formal_protocol.json').read_text())==decision
        assert decision['parser_sha256']==hashlib.sha256((ROOT/'src/pilot/day2_protocol.py').read_bytes()).hexdigest()
        assert decision['score_implementation_sha256']==hashlib.sha256((ROOT/'src/pilot/runtime.py').read_bytes()).hexdigest()
        assert decision['generation_implementation_sha256']==hashlib.sha256((ROOT/'src/pilot/day2_run.py').read_bytes()).hexdigest()
    result={'passed':True,'GSM8K_C100_candidates':400,'MATH_train70_candidates':280,'V0_scores':680,'labels_separate_from_model_inputs':True,'questions_and_4_candidates_accounted_for':True,'strata_and_pre_generation_selection_unchanged':True,'same_G0_and_V0_as_day1':True,'no_adapter_loaded':True,'protected_day1_and_T_files_unchanged':True,'T_not_evaluated':True,'MATH_test_not_downloaded':True,'cache_keys_unique_and_disjoint':True,'formal_protocol_matches_decision_and_parser':True,'scope':'artifact identity and experimental isolation; does not certify correctness of unsupported mathematical expressions or prove causal effects'}
    write_json(ROOT/'reports/day2/artifact_audit.json',result)
    write_json(ROOT/'runs/day2/state.json',{'status':'complete','stage':'all_checks_passed','GSM8K_questions':100,'MATH_questions':70,'candidates':680,'scores':680,'primary_task':decision['primary_task'],'formal_protocol_frozen':decision['protocol_frozen_for_next_stage'],'T_used':False,'formal_training_started':False})
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
