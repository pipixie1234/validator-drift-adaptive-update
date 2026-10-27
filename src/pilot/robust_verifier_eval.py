"""New sealed-family G0/G3 generation and fixed paired V evaluation."""
import argparse
import gc
import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path

from .core import ROOT, read_jsonl, recover_jsonl, write_json, write_jsonl, append_jsonl, digest, ensure_run_manifest, prompt_ids
from .formal_common import protocol, messages, sha, utc, gpu_lock, WorkBudget
from .runtime import load, model_identity
from .robust_verifier_common import RUN, PLAN, ARMS, validate, encode, score_batch
from .day2_protocol import label_response


def canonical_score(model, r):
    """Identical legacy two-row full inputs and selected position, sparse LM head."""
    import torch
    ids = r['prompt_ids']; n = len(ids)
    inputs = torch.tensor([ids+[32], ids+[33]], device='cuda')
    mask = torch.ones_like(inputs)
    with torch.inference_mode():
        logits = model(input_ids=inputs, attention_mask=mask, use_cache=False,
                       logits_to_keep=torch.tensor([n-1], device='cuda')).logits[:, 0].float()
        ll = logits.log_softmax(-1); a, b = ll[0, 32], ll[1, 33]; pair = torch.stack([a, b])
    return {'log_likelihood_A': float(a), 'log_likelihood_B': float(b), 'score': float(pair.softmax(0)[0]),
            'accepted': bool(a >= b), 'margin': float(a-b), 'prompt_tokens': n}


def frozen_adapters():
    cfg = validate(); out = {}
    for arm in ARMS:
        report = json.loads((RUN/arm/'training_report.json').read_text())
        fp = model_identity('V', RUN/arm/'adapter')[1]
        assert fp == report['adapter_fingerprint'] and report['steps'] == 80 and report['save_reload_passed']
        out[arm] = fp
    return out


def generate(g):
    if importlib.util.find_spec('vllm') is None:
        subprocess.run([str(ROOT/'scripts/pilot-vllm'), '-m', 'pilot.robust_verifier_eval', 'generate', '--round', g], check=True)
        return
    from transformers import AutoTokenizer
    from vllm import LLM, SamplingParams
    from vllm.sampling_params import RequestOutputKind
    from vllm.lora.request import LoRARequest
    cfg = validate(); frozen = frozen_adapters(); rows = read_jsonl(RUN/'E_inputs.jsonl')
    adapter = ROOT/'runs/g3_extension_20261024/checkpoints/G3_SFT' if g == 'G3' else None
    base, ad = model_identity('G', adapter); directory = RUN/'generation'/g; directory.mkdir(parents=True, exist_ok=True)
    ensure_run_manifest(directory/'manifest.json', {'plan_sha256': sha(PLAN), 'all4_V_adapters_frozen_before_E': frozen,
                       'code_sha256': sha(Path(__file__)), 'G': g, 'base': base, 'adapter': ad})
    tok = AutoTokenizer.from_pretrained(protocol()['models']['G']['path'], local_files_only=True)
    saved = {}; pending = {}; order = {r['question_id']: i for i, r in enumerate(rows)}
    for r in rows:
        path = directory/(digest(r['question_id'])+'.json')
        if path.exists():
            old = json.loads(path.read_text()); assert old['manifest_sha256'] == sha(directory/'manifest.json') and len(old['records']) == 4
            saved[r['question_id']] = old['records']
        else: pending[str(len(pending))] = (r, path)
    def flush(): write_jsonl(RUN/f'{g}_E.jsonl', [r for q in sorted(saved, key=order.get) for r in saved[q]])
    if not pending: flush(); return
    with gpu_lock():
        budget = WorkBudget('robust_verifier/new_E_generation/'+g); tick = time.perf_counter()
        llm = LLM(model=protocol()['models']['G']['path'], tokenizer=protocol()['models']['G']['path'], **cfg['generation']['settings'])
        request = LoRARequest('robust_new_E_G3', 1, str(adapter)) if adapter else None; prompts = {}
        for key, (r, path) in pending.items():
            ids = prompt_ids(tok, messages('G', r['question'])); assert 0 < len(ids) <= 1024
            cap = min(3840, 4096-len(ids)); seed = 42+int(digest(r['question_id'])[:8], 16)%1000000
            prompts[key] = (ids, cap, seed)
            params = SamplingParams(n=4, temperature=.7, top_p=.9, top_k=50, max_tokens=cap, seed=seed,
                                    stop_token_ids=[151645, 151643], output_kind=RequestOutputKind.FINAL_ONLY)
            llm.llm_engine.add_request(key, {'prompt_token_ids': ids}, params, lora_request=request)
        last = time.perf_counter()
        while llm.llm_engine.has_unfinished_requests():
            budget.check()
            for output in llm.llm_engine.step():
                if not output.finished: continue
                r, path = pending[output.request_id]; ids, cap, seed = prompts[output.request_id]
                assert output.prompt_token_ids == ids and len(output.outputs) == 4; records = []
                for c in sorted(output.outputs, key=lambda c: c.index):
                    tokens = list(c.token_ids); assert len(ids)+len(tokens) <= 4096
                    record = {**r, 'G_round': g, 'candidate_index': c.index, 'view': g+'_vLLM_newE',
                              'candidate_id': r['question_id']+f':robustV:{g}:{c.index}',
                              'text': tok.decode(tokens, skip_special_tokens=True), 'output_tokens': len(tokens),
                              'prompt_tokens': len(ids), 'max_new_tokens': cap, 'truncated': c.finish_reason == 'length',
                              'finish_reason': c.finish_reason, 'generated_token_ids': tokens, 'generation_seed': seed,
                              'base_fingerprint': base, 'adapter_fingerprint': ad, 'at': utc()}
                    assert not any(k in record for k in ['correct', 'gold_answer', 'reference']); records.append(record)
                write_json(path, {'manifest_sha256': sha(directory/'manifest.json'), 'records': records}); saved[r['question_id']] = records
            if time.perf_counter()-last >= 10:
                flush(); budget.charge('generation_progress'); last = time.perf_counter()
                write_json(RUN/'state.json', {'status': 'new_E_generation', 'G': g, 'questions': len(saved), 'total': 64, 'at': utc()})
                print(g, 'new E', len(saved), '/64', flush=True)
        flush(); assert len(saved) == 64 and model_identity('G', adapter) == (base, ad)
        write_json(directory/'complete.json', {'questions': 64, 'answers': 256, 'seconds': time.perf_counter()-tick, 'at': utc()})
        budget.charge('generation_complete')


def prepare_eval():
    from transformers import AutoTokenizer
    cfg = validate(); frozen = frozen_adapters()
    amendment = ROOT/'configs/verifier_robustness_20261026_transition_endpoints.json'
    assert json.loads(amendment.read_text())['amends_plan_sha256'] == sha(PLAN)
    refs = {r['question_id']: r['gold_answer'] for r in read_jsonl(RUN/'E_references.jsonl')}
    tok = AutoTokenizer.from_pretrained(protocol()['models']['V']['path'], local_files_only=True)
    vv = {v['name']: v for v in cfg['prefixes']}; eanswers = []; jobs = []
    for g in ['G0', 'G3']:
        raw = read_jsonl(RUN/f'{g}_E.jsonl'); assert len(raw) == 256
        for r in raw:
            label = label_response(r['text'], r['truncated'], refs[r['question_id']], 'gsm8k')
            answer = {**r, 'correct': label['correct'], 'verification_status': label['status'], 'gold_answer': refs[r['question_id']],
                      'extracted_answer': label['value'], 'unknown_reason': label['status'] if label['correct'] is None else None}
            eanswers.append(answer)
            for name in ['original', 'we_can', 'we_will']:
                v = vv[name]; after = label_response(v['prefix']+r['text'], r['truncated'], refs[r['question_id']], 'gsm8k')
                assert all(label[k] == after[k] for k in ['correct', 'value', 'status'])
                jobs.append(encode(tok, answer, v) | {'cohort': 'E_new_final'})
    write_jsonl(RUN/'E_answers.jsonl', eanswers)
    jobs += [{**r, 'cohort': 'C_development'} for r in read_jsonl(RUN/'C_jobs.jsonl')]
    fit = {r['job_id']: r for r in read_jsonl(RUN/'train_aug.jsonl')}
    jobs += [{k: v for k, v in r.items() if k not in ['labels', 'input_ids', 'chosen', 'rejected', 'chosen_label_id', 'rejected_label_id']} |
             {'cohort': 'B_training_fit', 'view': 'G0_B_fit'} for r in fit.values()]
    assert len(jobs) == len({r['job_id'] for r in jobs}) == 7116
    # Same input batch composition for every V, also after resuming a torn batch.
    jobs.sort(key=lambda r: (r['prompt_tokens'], r['job_id'])); write_jsonl(RUN/'evaluation_jobs.jsonl', jobs)
    scoring_revision = ROOT/'configs/verifier_robustness_20261026_scoring_revision002.json'
    ensure_run_manifest(RUN/'evaluation.manifest.json', {'plan_sha256': sha(PLAN), 'analysis_amendment_sha256': sha(amendment),
               'scoring_revision_sha256': sha(scoring_revision),
               'all4_final_adapters': frozen, 'jobs_sha256': sha(RUN/'evaluation_jobs.jsonl'), 'new_E_answers_sha256': sha(RUN/'E_answers.jsonl'),
               'code_sha256': sha(Path(__file__)), 'common_sha256': sha(ROOT/'src/pilot/robust_verifier_common.py'),
               'models': ['V0']+ARMS, 'microbatch': 'one answer, legacy two A/B rows', 'scores_per_model': len(jobs)})
    write_json(RUN/'state.json', {'status': 'evaluation_prepared', 'scores_per_model': len(jobs), 'models': 5, 'at': utc()})


def score():
    import torch
    validate(); frozen = frozen_adapters(); manifest = json.loads((RUN/'evaluation.manifest.json').read_text())
    assert sha(RUN/'evaluation_jobs.jsonl') == manifest['jobs_sha256']; assert frozen == manifest['all4_final_adapters']
    jobs = read_jsonl(RUN/'evaluation_jobs.jsonl'); expected = {r['job_id'] for r in jobs}
    for arm in ['V0']+ARMS:
        directory = RUN/'scoring'/arm; directory.mkdir(parents=True, exist_ok=True); path = directory/'scores.jsonl'
        saved = recover_jsonl(path); done = {r['job_id'] for r in saved}; assert len(done) == len(saved)
        ensure_run_manifest(directory/'manifest.json', {'evaluation_sha256': sha(RUN/'evaluation.manifest.json'),
                   'arm': arm, 'adapter_fingerprint': None if arm == 'V0' else frozen[arm]})
        if done == expected: continue
        adapter = RUN/arm/'adapter' if arm != 'V0' else None
        with gpu_lock():
            budget = WorkBudget('robust_verifier/score/'+arm); tick = time.perf_counter(); model, tok = load('V', adapter)
            base, ad = model_identity('V', adapter); count = 0
            from .runtime import conditional_label_scores
            answers = {r['candidate_id']: r for name in ['C_answers.jsonl', 'E_answers.jsonl', 'train_answers.jsonl'] for r in read_jsonl(RUN/name)}
            audit_path = directory/'legacy_equivalence.json'
            if audit_path.exists(): equivalence = json.loads(audit_path.read_text())
            else:
                probes = jobs[::max(1, len(jobs)//16)][:16]; checks = []
                for r in probes:
                    raw = answers[r['candidate_id']]; msg = messages('V', raw['question'], r['prefix']+raw['text'])
                    old = conditional_label_scores(model, tok, msg); new = canonical_score(model, r)
                    checks.append({'job_id': r['job_id'], 'same_decision': old['accepted'] == new['accepted'],
                                   'score_difference': abs(old['score']-new['score']),
                                   'max_LL_difference': max(abs(old[k]-new[k]) for k in ['log_likelihood_A', 'log_likelihood_B'])})
                use_sparse = all(c['same_decision'] and c['score_difference'] < 2e-6 and c['max_LL_difference'] < 2e-5 for c in checks)
                equivalence = {'checks': checks, 'sparse_output_passed': use_sparse, 'fallback': 'original full conditional_label_scores' if not use_sparse else None}
                write_json(audit_path, equivalence); budget.charge('legacy_equivalence')
            for i, r in enumerate(jobs):
                if r['job_id'] in done: continue
                budget.check()
                if equivalence['sparse_output_passed']: s = canonical_score(model, r)
                else:
                    raw = answers[r['candidate_id']]
                    s = conditional_label_scores(model, tok, messages('V', raw['question'], r['prefix']+raw['text']))
                    s['margin'] = s['log_likelihood_A']-s['log_likelihood_B']
                out = {k: v for k, v in r.items() if k != 'prompt_ids'} | s
                out.update({'V': arm, 'model_fingerprint': base, 'adapter_fingerprint': ad, 'threshold': .5,
                            'evaluation_sha256': sha(RUN/'evaluation.manifest.json'), 'at': utc()})
                append_jsonl(path, out); done.add(r['job_id']); count += 1
                if i%100 == 0:
                    budget.charge(str(len(done))); write_json(RUN/'state.json', {'status': 'scoring', 'V': arm, 'scores': len(done), 'total': len(jobs), 'at': utc()})
                    print('V scoring', arm, len(done), '/', len(jobs), flush=True)
            assert done == expected and model_identity('V', adapter) == (base, ad)
            write_json(directory/'complete.json', {'seconds_this_invocation': time.perf_counter()-tick, 'new_scores': count, 'scores_total': len(done), 'weights_unchanged': True})
            del model; gc.collect(); torch.cuda.empty_cache(); budget.charge('scoring_finalization')


def main():
    p = argparse.ArgumentParser(); p.add_argument('stage', choices=['generate', 'prepare', 'score']); p.add_argument('--round', choices=['G0', 'G3'])
    args = p.parse_args()
    if args.stage == 'generate': generate(args.round)
    elif args.stage == 'prepare': prepare_eval()
    else: score()


if __name__ == '__main__': main()
