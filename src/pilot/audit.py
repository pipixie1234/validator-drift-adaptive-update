from collections import Counter
from .core import *
from .runtime import model_identity

def main():
    generated=read_jsonl(ROOT/'runs/day1/G0_C50.jsonl');scores=read_jsonl(ROOT/'runs/day1/V0_G0_C50.jsonl')
    assert len(generated)==200 and len(scores)==200
    assert len({r['candidate_id'] for r in generated})==200
    assert len({r['candidate_id'] for r in scores})==200
    assert all(count==4 for count in Counter(r['question_id'] for r in generated).values())
    assert len({r['question_id'] for r in generated})==50
    assert {r['candidate_id'] for r in generated}=={r['candidate_id'] for r in scores}
    assert all(r['split']=='C' for r in generated+scores)
    gfp,_=model_identity('G');vfp,_=model_identity('V')
    assert all(r['model_fingerprint']==gfp for r in generated)
    assert all(r['model_fingerprint']==vfp and r['V_version']=='V0' and r['adapter_fingerprint'] is None for r in scores)
    assert len({r['cache_key'] for r in generated})==200
    assert len({r['cache_key'] for r in scores})==200
    assert {r['cache_key'] for r in generated}.isdisjoint({r['cache_key'] for r in scores})
    for r in generated:
        assert not any(key in r for key in ['gold_answer','reference','correct'])
        e=extract_candidate_answer(r['text'],r['truncated'])
        assert e['answer']==r['extracted_answer'] and e['status']==r['verification_status']
        assert e['format_compliant']==r['format_compliant'] and e['answer_format']==r['answer_format']
    for role in ['G','V']:
        report=json.loads((ROOT/f'runs/day1/smoke_{role}_report.json').read_text())
        assert report['save_reload_passed'] and report['base_parameter_versions_unchanged'] and report['nonzero_gradient']
        adapter=ROOT/f'runs/day1/smoke_{role}_adapter/adapter_config.json'
        assert json.loads(adapter.read_text())['base_model_name_or_path']==CFG[f'{role}_MODEL_DIR']
    test_ids={r['question_id'] for r in json.loads((ROOT/'data/manifests/splits.json').read_text())['splits']['T']}
    for artifact in (ROOT/'runs/day1').glob('*.jsonl'):
        assert not any(r.get('question_id') in test_ids for r in read_jsonl(artifact))
    write_json(ROOT/'reports/day1_audit.json',{'passed':True,'scope':'artifact consistency and lineage; does not certify strict formatting or both training labels','known_limitations':['No generated ####-compliant C responses','B smoke pool has no independently verified negative; V negative-label training path untested'],'questions':50,'candidates':200,'scores':200,'unique_candidates_and_caches':True,'extraction_recomputed_from_raw_text':True,'model_identity_still_matches_loaded_bases':True,'G_and_V_smoke_base_references_correct':True,'truth_not_in_generation_records':True,'final_test_inference_not_run':True})
    print('Day1 artifact audit passed: 50 questions, 200 candidates, 200 V0 scores, adapters and cache identities checked.')

if __name__=='__main__':main()
