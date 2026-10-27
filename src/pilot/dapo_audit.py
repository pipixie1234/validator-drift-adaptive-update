"""Verify preserved evidence, model identity, sealed question pools and run identity."""
import json
import math
from collections import Counter
from .core import ROOT, read_jsonl, write_json, digest
from .runtime import model_identity
from .dapo_common import BASE, RUN, REPORT, config, messages, sha

def main():
    protected=json.loads((RUN/'protected_day2.json').read_text())
    changed=[name for name,h in protected.items() if not (ROOT/name).exists() or sha(ROOT/name)!=h]
    assert not changed,f'Frozen day2 artifact changed: {changed}'
    cfg=config();selection=json.loads((BASE/'manifests/selection_locked.json').read_text())
    assert sha(ROOT/'data/raw/dapo_en/train-00000-of-00001.parquet')==cfg['file_sha256']
    assert selection['config_sha256']==sha(ROOT/'configs/dapo_diagnostic.json')
    for key,path in [('inputs_sha256',BASE/'inputs/dapo100.jsonl'),('labels_sha256',BASE/'labels/dapo100.jsonl'),('inventory_sha256',BASE/'eligible_families.jsonl')]:assert sha(path)==selection[key]
    inputs=read_jsonl(BASE/'inputs/dapo100.jsonl');g=read_jsonl(RUN/'G0_dapo100.jsonl');v=read_jsonl(RUN/'V0_dapo100.jsonl')
    assert len(inputs)==100 and len(g)==len(v)==400
    assert [r['question_id'] for r in inputs]==selection['selected_ids']
    allowed={r['question_id'] for r in inputs}
    assert Counter(r['question_id'] for r in g)==Counter({k:4 for k in allowed})
    assert {r['candidate_id'] for r in g}=={r['candidate_id'] for r in v}
    assert len({r['candidate_id'] for r in g})==400
    assert all({r['candidate_index'] for r in g if r['question_id']==q}==set(range(4)) for q in allowed)
    old=json.loads((ROOT/'configs/formal_protocol.json').read_text())
    assert all(cfg[key]==old['inference_config'][key] for key in ['G_MODEL_DIR','V_MODEL_DIR','G_system','G_user_suffix','V_system'])
    assert {k:val for k,val in cfg['generation'].items() if k!='max_new_tokens'}=={k:val for k,val in old['inference_config']['generation'].items() if k!='max_new_tokens'}
    assert {k:val for k,val in cfg['verifier'].items() if k!='max_context'}=={k:val for k,val in old['inference_config']['verifier'].items() if k!='max_context'}
    for role,rs in [('G',g),('V',v)]:
        fp,adapter=model_identity(role);assert adapter is None
        expected=old[f'{role}0_fingerprint'];assert fp==expected
        assert all(r['model_fingerprint']==fp and r['adapter_fingerprint'] is None for r in rs)
        manifest=json.loads((RUN/f'{role}0_dapo100.manifest.json').read_text())
        assert manifest['runner_sha256']==sha(ROOT/'src/pilot/dapo_run.py')
        assert manifest['runtime_sha256']==sha(ROOT/'src/pilot/runtime.py')
        assert manifest['common_sha256']==sha(ROOT/'src/pilot/dapo_common.py')
        assert manifest['config_sha256']==sha(ROOT/'configs/dapo_diagnostic.json')
    vg={r['candidate_id']:r for r in g}
    for row in g:
        assert 'gold_answer' not in row and 'correct' not in row
        assert row['prompt_tokens']+row['output_tokens']<=4096
        assert row['prompt_sha256']==digest(messages('G',row['question']))
    for row in v:
        assert not any(k in row for k in ['gold_answer','correct','extracted_answer'])
        assert row['prompt_sha256']==digest(messages('V',vg[row['candidate_id']]['question'],vg[row['candidate_id']]['text']))
        assert row['prompt_tokens']+4<=8192 and row['scoring_status']=='ok'
        assert all(math.isfinite(row[key]) for key in ['score','log_likelihood_A','log_likelihood_B'])
        assert 0<=row['score']<=1 and row['accepted']==(row['log_likelihood_A']>=row['log_likelihood_B'])
    labels=read_jsonl(RUN/'labels_dapo100.jsonl');assert len(labels)==400
    byid={r['candidate_id']:r for r in labels}
    assert all(not r['truncated'] or byid[r['candidate_id']]['correct'] is None for r in g)
    revision=json.loads((ROOT/'configs/protocol_revisions/003_dapo_diagnostic.json').read_text())
    assert revision['formal_training_started'] is False and revision['G1_G2_status']=='paused'
    current=json.loads((ROOT/'configs/current_protocol.json').read_text())
    assert current['formal_training']=='paused' and current['active_revision']=='configs/protocol_revisions/003_dapo_diagnostic.json'
    assert revision['metrics_sha256']==sha(REPORT/'dapo100_metrics.json')
    assert revision['source_quality_review_sha256']==sha(REPORT/'source_quality_review.json')
    flagged={r['question_id'] for r in json.loads((REPORT/'source_quality_review.json').read_text())['issues']}
    assert all(r['question_id'] not in flagged or r['correct'] is None for r in labels)
    assert all(sha(ROOT/name)==h for name,h in revision['code_sha256'].items())
    assert revision['preserved_previous_protocol_sha256']==sha(ROOT/'configs/formal_protocol.json')
    if revision['pools_constructed']:
        manifest=json.loads((BASE/'formal/manifest.json').read_text());sets={field:[{r[field] for r in inputs}] for field in ['question_id','question_hash','family_id']}
        uuidsets=[{u for r in inputs for u in r['family_source_uuids']}]
        for split,record in manifest['splits'].items():
            path=BASE/f'formal/inputs/{split}.jsonl';rs=read_jsonl(path)
            assert sha(path)==record['input_sha256'] and len(rs)==cfg['conditional_formal_pool_sizes'][split]
            assert sha(BASE/f'formal/labels/{split}.jsonl')==record['label_sha256']
            assert not allowed & {r['question_id'] for r in rs}
            assert all('gold_answer' not in r for r in rs)
            for field in sets:sets[field].append({r[field] for r in rs})
            uuidsets.append({u for r in rs for u in r['family_source_uuids']})
        assert all(sum(map(len,ss))==len(set.union(*ss)) for ss in [*sets.values(),uuidsets])
    result={'audit':'passed','preserved_day2_files':len(protected),'day2_changed_files':changed,
        'questions':100,'G_candidates':400,'V_scores':400,'same_G0_V0_weights':True,'adapters_used':False,
        'G_prompts_sampling_V_threshold_unchanged':True,'G_native_context_respected':True,
        'gold_absent_from_model_inputs':True,'all_truncations_unknown':True,
        'T_inferred':False,'formal_training_started':False,'formal_pools_constructed':revision['pools_constructed'],
        'semantic_near_duplicate_guarantee':False}
    write_json(REPORT/'artifact_audit.json',result);print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
