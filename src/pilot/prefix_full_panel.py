"""Unconditioned C95 x four cached G3 answers: both truths and both V decisions."""
import argparse
import gc
import json
import time
from collections import Counter
from pathlib import Path

from .core import (ROOT, read_jsonl, recover_jsonl, write_json, write_jsonl,
                   append_jsonl, digest, ensure_run_manifest, prompt_ids)
from .formal_common import protocol, messages, sha, utc, verify_fixed, gpu_lock, WorkBudget
from .fpr_pattern_diagnostics import load as source_rows, CONFIG as SOURCE_CONFIG
from .cross_round_prefix import variants
from .day2_protocol import label_response

RUN = ROOT/'runs/prefix_full_panel_20261026'
REPORT = ROOT/'reports/prefix_full_panel_20261026'
PLAN = ROOT/'configs/prefix_full_panel_20261026.json'
FIELDS = ['log_likelihood_A', 'log_likelihood_B', 'score', 'accepted', 'label_ids', 'prompt_tokens']
CACHE_PATHS = [ROOT/p for p in [
    'runs/g3_extension_20261024/V0_G3_C.jsonl',
    'runs/prefix_fnr_20261025/V0_scores.jsonl',
    'runs/prefix_paraphrase_20261024/V0_scores.jsonl',
    'runs/cross_round_prefix_20261025/V0_scores.jsonl']]


def validate():
    cfg = json.loads(PLAN.read_text()); verify_fixed()
    assert all(sha(ROOT/p) == h for p, h in cfg['source_sha256'].items())
    assert all(sha(RUN/p) == h for p, h in cfg['input_sha256'].items())
    return cfg


def prepare():
    RUN.mkdir(exist_ok=True); REPORT.mkdir(exist_ok=True)
    if PLAN.exists(): return validate()
    verify_fixed()
    rr = sorted([r for r in source_rows() if r['G_round'] == 'G3' and r['split'] == 'C'],
                key=lambda r: (r['question_id'], r['candidate_index']))
    assert len(rr) == 380 and set(Counter(r['question_id'] for r in rr).values()) == {4}
    names = ['original', 'neutral_control', 'we_need_to', 'we_can', 'we_will',
             'it_is_essential_to', 'symbol_comma', 'reason_step_by_step']
    lookup = {v['name']: v for v in variants()}; vv = [lookup[n] for n in names]
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(protocol()['models']['V']['path'], local_files_only=True)
    checks = []; jobs = []
    for i, r in enumerate(rr):
        before = label_response(r['text'], r['truncated'], r['gold_answer'], 'gsm8k')
        assert before['correct'] is r['correct']
        for v in vv:
            text = v['prefix'] + r['text']; msgs = messages('V', r['question'], text)
            after = label_response(text, r['truncated'], r['gold_answer'], 'gsm8k')
            assert all(before[k] == after[k] for k in ['correct', 'value', 'status'])
            n = len(prompt_ids(tok, msgs)); assert n + 4 <= 8192
            jobs.append({'candidate_id': r['candidate_id'], 'question_id': r['question_id'],
                         'variant': v['name'], 'prefix': v['prefix'], 'correct': r['correct'],
                         'prompt_sha256': digest(msgs), 'text_sha256': digest(text), 'prompt_tokens': n,
                         'prefix_tokens_standalone': len(tok.encode(v['prefix'], add_special_tokens=False)),
                         'response_tokens_V_tokenizer': len(tok.encode(text, add_special_tokens=False))})
        checks.append({'candidate_id': r['candidate_id'], 'all_labels_and_final_answers_unchanged': True,
                       'all_full_contexts_fit': True, 'label_status': before['status']})
        if (i+1) % 50 == 0: print(f'prepare {i+1}/{len(rr)}', flush=True)
    write_jsonl(RUN/'answers.jsonl', rr); write_jsonl(RUN/'jobs.jsonl', jobs)
    write_jsonl(RUN/'answer_context_checks.jsonl', checks)
    source_cfg = json.loads(SOURCE_CONFIG.read_text())
    paths = [SOURCE_CONFIG, Path(__file__), ROOT/'src/pilot/runtime.py', *CACHE_PATHS]
    cfg = {'created_at': utc(), 'purpose': 'Net prefix effect on ALL wrong and correct answers, not original TN only',
           'cohort': 'ALL 95 previously cleaned development C questions, all four existing G3 answers each; no selection by truth, V decision, word presence, length or intervention result',
           'support': {'questions': 95, 'answers': 380, 'correct': 147, 'wrong': 218, 'unknown': 15,
                       'TP': 126, 'FN': 21, 'FP': 119, 'TN': 99},
           'variants': vv, 'prefix_selection': 'Six inherited representative settings fixed before new scores: three short modal phrases, one longer modal phrase, punctuation, step-by-step wording; plus original and neutral control. Exploratory, not a random or exhaustive prefix sample.',
           'source_sha256': source_cfg['source_sha256'] | {str(p.relative_to(ROOT)): sha(p) for p in paths},
           'input_sha256': {p.name: sha(p) for p in RUN.glob('*.jsonl')},
           'fixed_V0': {'fingerprint': protocol()['models']['V']['fingerprint'], 'adapter': None,
                        'threshold': .5, 'scoring': protocol()['V_scoring'], 'max_context': 8192},
           'template': messages('V', '{question}', '{actual_prefix}{unchanged_response}'),
           'score': 'softmax(LL_A,LL_B)[A]; margin=LL_A-LL_B, bare A/B continuation, no EOS likelihood',
           'primary': 'FPR(prefix)-FPR(original) and FNR(prefix)-FNR(original), 12 comparisons BH; paired whole-question bootstrap10000, question-cluster influence t tests',
           'secondary': 'Same 12 contrasts vs neutral, separate BH12; wrong-minus-correct mean continuous score change and margin change with paired question intervals; BA and accepted risk descriptive',
           'interpretation': 'Wrong-answer acceptance advantage and correct-answer benefit both counted. A uniform acceptance bias is not selective cheating. Undoing an artificial prefix is an exact reverse intervention, not evidence that banning natural expressions helps.',
           'unknown': 'Score and preserve all15; never count as wrong/correct; report acceptance and error bounds',
           'reuse': 'Only identical full rendered-message hash, original V0 fingerprint, no adapter, threshold0.5, same frozen scorer; persist source file/line. Sparse cached sentinels fresh rechecked.',
           'sentinels': 'First hash-sorted example in each original TP/FN/FP/TN/unknown stratum; original and we_can, if cached. Fixed without inspecting new scores.',
           'bootstrap': 10000, 'seed': 20261026, 'logical_scores': len(jobs),
           'limits': ['Repeated-use C development, not new confirmatory test', 'One G3 state only; not a causal mediation test of G training',
                      'Final-answer labels do not verify complete reasoning', 'Prefix may change perceived discourse, even with math body unchanged',
                      'Sparse flips and zero bootstrap width do not prove population zero risk'],
           'G_training': False, 'G_generation': False, 'V_training': False, 'B_read': False, 'T_read': False}
    ensure_run_manifest(PLAN, cfg); validate()
    write_json(RUN/'state.json', {'status': 'prepared', 'logical_scores': len(jobs), 'at': utc()})
    return cfg


def historical_cache(cfg, jobs):
    wanted = {j['prompt_sha256']: j for j in jobs}; cache = {}; conflicts = 0
    for path in CACHE_PATHS:
        for line, s in enumerate(read_jsonl(path), 1):
            ph = s.get('prompt_sha256')
            if ph not in wanted: continue
            assert s['model_fingerprint'] == cfg['fixed_V0']['fingerprint']
            assert s['adapter_fingerprint'] is None and s['threshold'] == .5
            assert s['accepted'] == (s['score'] >= .5) and s['label_ids'] == [[32], [33]]
            assert s['prompt_tokens'] == wanted[ph]['prompt_tokens']
            if ph in cache:
                old = cache[ph]
                assert old['accepted'] == s['accepted'] and abs(old['score']-s['score']) < 2e-6
                assert max(abs(old[k]-s[k]) for k in FIELDS[:2]) < 2e-4
                conflicts += 1
            else:
                cache[ph] = {**{k: s[k] for k in FIELDS}, 'reuse_file': str(path.relative_to(ROOT)), 'reuse_line': line}
    return cache, conflicts


def run():
    import torch
    from .runtime import load, model_identity, conditional_label_scores
    cfg = prepare(); rr = read_jsonl(RUN/'answers.jsonl'); jobs = read_jsonl(RUN/'jobs.jsonl')
    rows = {r['candidate_id']: r for r in rr}; cache, duplicates = historical_cache(cfg, jobs)
    saved = recover_jsonl(RUN/'V0_scores.jsonl'); done = {(s['candidate_id'], s['variant']) for s in saved}
    assert len(done) == len(saved)
    ensure_run_manifest(RUN/'runtime.manifest.json', {'plan_sha256': sha(PLAN), 'runner_sha256': sha(Path(__file__)),
                                                  'runtime_sha256': sha(ROOT/'src/pilot/runtime.py')})
    # Audit cached scores on a small predeclared mixture of truths and decisions.
    strata = {}
    for r in rr:
        stratum = str(r['correct']) + '/' + str(r['accepted']) if r['correct'] is not None else 'unknown'
        if stratum not in strata or digest(r['candidate_id']) < digest(strata[stratum]['candidate_id']): strata[stratum] = r
    checks = {(r['candidate_id'], v) for r in strata.values() for v in ['original', 'we_can']}
    sentinel_done = {(s['candidate_id'], s['variant']) for s in recover_jsonl(RUN/'sentinel_rechecks.jsonl')}
    fresh = reused = rechecked = 0
    print(f'logical={len(jobs)} exact historical prompt cache={len(cache)} duplicates_checked={duplicates}', flush=True)
    with gpu_lock():
        budget = WorkBudget('prefix_full_panel/fixed_V0'); start = time.perf_counter()
        model, tok = load('V'); fp, ad = model_identity('V')
        assert fp == cfg['fixed_V0']['fingerprint'] and ad is None
        for i, j in enumerate(jobs):
            key = (j['candidate_id'], j['variant']); r = rows[j['candidate_id']]
            text = j['prefix'] + r['text']; msgs = messages('V', r['question'], text); old = cache.get(j['prompt_sha256'])
            if key in checks and old and key not in sentinel_done:
                budget.check(); test = conditional_label_scores(model, tok, msgs, cfg['fixed_V0']['scoring']['labels'])
                assert test['accepted'] == old['accepted'] and abs(test['score']-old['score']) < 2e-6
                assert max(abs(test[k]-old[k]) for k in FIELDS[:2]) < 2e-4
                append_jsonl(RUN/'sentinel_rechecks.jsonl', {**j, **test, 'cached_score': old['score'], 'passed': True, 'at': utc()})
                sentinel_done.add(key); rechecked += 1; budget.charge('sentinel/'+str(key))
            if key in done: continue
            if old:
                sc = {k: old[k] for k in FIELDS}; origin = 'exact_historical_full_prompt'; reused += 1
            else:
                budget.check(); sc = conditional_label_scores(model, tok, msgs, cfg['fixed_V0']['scoring']['labels'])
                origin = 'new_fixed_V0_score'; fresh += 1
                budget.charge(str(key))
            assert sc['prompt_tokens'] == j['prompt_tokens'] and sc['accepted'] == (sc['score'] >= .5)
            if j['variant'] == 'original':
                assert sc['accepted'] == r['accepted'] and abs(sc['score']-r['score']) < 2e-6
            s = {**j, **sc, 'source': origin, 'split': 'C', 'G_round': 'G3', 'at': utc(),
                 'model_fingerprint': fp, 'adapter_fingerprint': None, 'threshold': .5,
                 'cache_key': digest({'plan': sha(PLAN), 'candidate_id': key[0], 'variant': key[1]})}
            if old: s.update({k: old[k] for k in ['reuse_file', 'reuse_line']})
            append_jsonl(RUN/'V0_scores.jsonl', s); done.add(key)
            if (i+1) % 80 == 0:
                state = {'status': 'scoring', 'scores': len(done), 'total': len(jobs), 'fresh': fresh, 'reused': reused, 'at': utc()}
                write_json(RUN/'state.json', state); print(json.dumps(state), flush=True)
        assert len(done) == cfg['logical_scores'] and model_identity('V') == (fp, ad)
        write_json(RUN/'compute.json', {'seconds_this_invocation': time.perf_counter()-start, 'fresh_this_invocation': fresh,
                   'reused_this_invocation': reused, 'sentinel_rechecks_this_invocation': rechecked,
                   'logical_scores': len(done), 'cache_duplicate_agreements': duplicates, 'V0_unchanged': True})
        budget.charge('finalization'); del model; gc.collect(); torch.cuda.empty_cache()
    validate()
    from .prefix_full_panel_report import main as report
    report()


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('stage', choices=['prepare', 'run']); args = parser.parse_args()
    if args.stage == 'prepare': print(json.dumps(prepare()['support'], ensure_ascii=False), flush=True)
    else: run()


if __name__ == '__main__': main()
