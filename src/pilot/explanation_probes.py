"""Read-only response likelihood probes; separate from the frozen experiment."""
import argparse
import gc
import hashlib
import json
import time
from pathlib import Path

from .core import ROOT, read_jsonl, write_json, write_jsonl, training_example
from .formal_common import RUN, REPORT, PROTOCOL, messages, adapter_path, sha, utc, gpu_lock, WorkBudget
from .runtime import load, model_identity
from .formal_multigpu_revision import verify_execution_revision

DEST = REPORT/'explanations_20261022'
SUSPECT = 'dapo:train:24d751b7-4429-4605-9754-d1451911fb26'


def order(rows, salt):
    return sorted(rows, key=lambda r: hashlib.sha256((salt+r['candidate_id']).encode()).hexdigest())


def prepare():
    DEST.mkdir(exist_ok=True)
    target = DEST/'likelihood_inputs.jsonl'
    if target.exists():
        return read_jsonl(target)
    out = []
    for target_round, source_round in [('G1', 'G0'), ('G2', 'G1')]:
        selected = read_jsonl(RUN/f'train/{target_round}/selected.jsonl')
        labels = {r['candidate_id']: r for r in read_jsonl(RUN/f'labels_{source_round}_A.jsonl')}
        raw = read_jsonl(RUN/f'{source_round}_A.jsonl')
        scores = {r['candidate_id']: r for r in read_jsonl(RUN/f'V0_{source_round}_A.jsonl')}
        for truth in [True, False, None]:
            name = 'correct' if truth is True else 'wrong' if truth is False else 'unknown'
            eligible = [r for r in selected if labels[r['candidate_id']]['correct'] is truth]
            for r in order(eligible, f'20261022:{target_round}:{name}:')[:12]:
                out.append({**r, 'probe_id': target_round+':selected:'+r['candidate_id'],
                            'cohort': target_round+'_selected_'+name, 'correct': truth,
                            'kind': 'selected_A', 'target_update': target_round, 'split': 'A'})
                # Alternative accepted response of the same question and same independent outcome.
                controls = [s for s in raw if s['question_id'] == r['question_id']
                            and s['candidate_id'] != r['candidate_id']
                            and scores[s['candidate_id']]['accepted']
                            and labels[s['candidate_id']]['correct'] is truth and not s['truncated']
                            and s['prompt_tokens']+s['output_tokens']+4 <= 4096]
                if controls:
                    s = order(controls, '20261022:control:')[0]
                    out.append({**s, 'probe_id': target_round+':control:'+s['candidate_id'],
                                'cohort': target_round+'_unselected_control_'+name, 'correct': truth,
                                'kind': 'unselected_A_same_question', 'target_update': target_round,
                                'matched_selected_probe_id': out[-1]['probe_id']})
    c = read_jsonl(REPORT/'v0_drift_analysis_20261022/candidates_full.jsonl')
    for truth in [True, False]:
        eligible = [r for r in c if r['split'] == 'C' and r['G_round'] == 'G0'
                    and r['correct'] is truth and r['question_id'] != SUSPECT and r['candidate_index'] == 0]
        name = 'correct' if truth else 'wrong'
        for r in order(eligible, f'20261022:C:{name}:')[:16]:
            # One fixed G0 answer per question; no B/T and no updated-G sample selection.
            out.append({**r, 'probe_id': 'C:'+r['candidate_id'], 'cohort': 'C_G0_'+name,
                        'kind': 'heldout_C', 'target_update': None})
    assert len({r['probe_id'] for r in out}) == len(out)
    assert all(r['split'] in ['A', 'C'] for r in out)
    write_jsonl(target, out)
    source_paths = [RUN/f'{prefix}{r}_A.jsonl' for r in ['G0', 'G1'] for prefix in ['', 'V0_', 'labels_']]
    source_paths += [RUN/f'train/{r}/selected.jsonl' for r in ['G1', 'G2']]
    source_paths += [REPORT/'v0_drift_analysis_20261022/candidates_full.jsonl', PROTOCOL]
    write_json(DEST/'likelihood_plan.json', {
        'created_at': utc(), 'inputs_sha256': sha(target), 'code_sha256': sha(Path(__file__)),
        'source_sha256': {str(p.relative_to(ROOT)): sha(p) for p in source_paths},
        'sampling': 'SHA256 deterministic within target update and frozen outcome; 12 per selected A stratum, at most one same-question accepted same-label unselected control; 16 each correct/wrong G0 candidate0 C',
        'exploratory_after_observing_formal_metrics': True,
        'truth_used_for_diagnostic_stratification_only': True,
        'teacher_forcing': 'Exact original response-only training template including assistant terminator; full text, no truncation; no gradient, optimizer, generation, or V calls',
        'engine': 'original Transformers BF16 SDPA; vLLM not installed, no autoregressive generation needed',
        'T_read': False, 'B_read_by_probe': False,
        'limitations': 'Stratified selected training examples are not a random population sample; lower NLL is text fitting, not correctness or causal identification. Controls share A questions; C is development, not final test.'})
    return out


def score():
    import torch
    verify_execution_revision()
    rows = prepare()
    plan = json.loads((DEST/'likelihood_plan.json').read_text())
    assert sha(DEST/'likelihood_inputs.jsonl') == plan['inputs_sha256']
    assert all(sha(ROOT/name) == h for name, h in plan['source_sha256'].items())
    if (DEST/'likelihood_run.json').exists():
        print('Completed likelihood probe already cached; no GPU work repeated.', flush=True)
        return
    start = time.perf_counter()
    output = DEST/'likelihood_scores.jsonl'
    existing = read_jsonl(output)
    done = {(r['probe_id'], r['model_round']) for r in existing}
    assert len(done) == len(existing)
    models = {}
    with gpu_lock():
        budget = WorkBudget('explanation_response_likelihood')
        for round_name in ['G0', 'G1', 'G2']:
            if all((r['probe_id'], round_name) in done for r in rows):
                continue
            budget.check()
            model, tok = load('G', adapter_path(round_name))
            base_fp, adapter_fp = model_identity('G', adapter_path(round_name))
            torch.cuda.reset_peak_memory_stats()
            tick = time.perf_counter()
            with torch.inference_mode():
                for i, r in enumerate(rows):
                    if (r['probe_id'], round_name) in done:
                        continue
                    budget.check()
                    ex = training_example(tok, messages('G', r['question']), r['text'], 4096)
                    assert ex is not None, 'No silent truncation of likelihood probe'
                    ids = torch.tensor([ex['input_ids']], device='cuda')
                    logits = model(input_ids=ids, use_cache=False).logits[0]
                    begin = ex['prefix_length']
                    # Chunk CE avoids materializing the entire float32 vocabulary tensor.
                    nll = torch.zeros((), device='cuda', dtype=torch.float64)
                    for a in range(begin, len(ex['input_ids']), 64):
                        b = min(a+64, len(ex['input_ids']))
                        nll += torch.nn.functional.cross_entropy(logits[a-1:b-1].float(), ids[0, a:b], reduction='sum').double()
                    count = len(ex['input_ids'])-begin
                    record = {'probe_id': r['probe_id'], 'candidate_id': r['candidate_id'],
                              'question_id': r['question_id'], 'cohort': r['cohort'], 'kind': r['kind'],
                              'correct': r['correct'], 'model_round': round_name, 'input_tokens': len(ex['input_ids']),
                              'response_tokens': count, 'response_nll_sum': float(nll),
                              'response_nll_per_token': float(nll)/count,
                              'model_fingerprint': base_fp, 'adapter_fingerprint': adapter_fp,
                              'inputs_sha256': plan['inputs_sha256'], 'recorded_at': utc()}
                    from .core import append_jsonl
                    append_jsonl(output, record)
                    done.add((r['probe_id'], round_name))
                    del logits, ids, nll
                    if (i+1) % 20 == 0:
                        print(f'{round_name} response likelihood {i+1}/{len(rows)}', flush=True)
                torch.cuda.synchronize()
            models[round_name] = {'seconds': time.perf_counter()-tick,
                                  'peak_allocated_bytes': torch.cuda.max_memory_allocated(),
                                  'model_fingerprint': base_fp, 'adapter_fingerprint': adapter_fp}
            budget.charge(round_name+'_teacher_forcing')
            del model, tok
            gc.collect()
            torch.cuda.empty_cache()
    assert len(done) == len(rows)*3
    assert all(sha(ROOT/name) == h for name, h in plan['source_sha256'].items())
    write_json(DEST/'likelihood_run.json', {'completed_at': utc(), 'examples': len(rows), 'scores': len(done),
               'total_seconds': time.perf_counter()-start, 'models_this_invocation': models,
               'new_generation': False, 'training': False, 'frozen_sources_unchanged': True,
               'scores_sha256': sha(output)})
    print(json.dumps({'status': 'complete', 'examples': len(rows), 'seconds': time.perf_counter()-start}), flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('stage', choices=['prepare', 'score'])
    a = p.parse_args()
    print(f'Prepared {len(prepare())} fixed response probes', flush=True) if a.stage == 'prepare' else score()


if __name__ == '__main__':
    main()
