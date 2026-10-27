"""Source-quality-only revision; restart all four methods from V0, same budget."""
import argparse
import copy
import json
import random
import shutil
import subprocess

from .core import ROOT, read_jsonl, recover_jsonl, write_json, write_jsonl, digest, ensure_run_manifest
from .formal_common import sha, utc, protocol, messages

OLD = ROOT/'runs/verifier_robustness_20261026'
RUN = ROOT/'runs/verifier_robustness_clean_20261026'
REPORT = ROOT/'reports/verifier_robustness_clean_20261026'
PLAN = ROOT/'configs/verifier_robustness_clean_20261026.json'
OLD_PLAN = ROOT/'configs/verifier_robustness_20261026.json'
BAD_B = {
 'dapo:train:5c02674f-9ec5-47d5-812b-20edff468a50': 'Asked to exhibit a set of five integers with proof; numeric-only reference labels need not answer requested object. Exclude rather than silently redefine target as k.',
 'dapo:train:d33e3a4d-7dbb-46ce-8810-5e850c48f10d': 'Asked all pairs then sum of the pair without a unique pair; (1,2) and(1,3) both force all integers since x=y=1 gives k+2 for arbitrary k, but sums3 and4 differ.',
 'dapo:train:0b7ecc37-28ce-4b23-bd46-c3247a8480fb': 'Explicit as-shown geometry reference without supplied illustration; conservatively quarantine instead of inferring intended arrangement.'}


def configure():
    from . import robust_verifier_common as c, robust_verifier_train as t
    from . import robust_verifier_eval as e, robust_verifier_report as r, robust_verifier_verify as v
    for module in [c, t, e, r, v]:
        module.RUN = RUN
        if hasattr(module, 'REPORT'): module.REPORT = REPORT
        if hasattr(module, 'PLAN'): module.PLAN = PLAN
    return c, t, e, r, v


def prepare():
    c, t, e, r, v = configure(); RUN.mkdir(exist_ok=True); REPORT.mkdir(exist_ok=True)
    if PLAN.exists(): return c.validate()
    from collections import defaultdict
    from transformers import AutoTokenizer
    from .verifier_update import join_source
    from .day2_protocol import label_response
    cfg = copy.deepcopy(json.loads(OLD_PLAN.read_text())); tok = AutoTokenizer.from_pretrained(protocol()['models']['V']['path'], local_files_only=True)
    rows, paths = join_source(ROOT/'runs/formal', 'G0', 'B'); byq = defaultdict(lambda: defaultdict(list))
    vv = {x['name']: x for x in cfg['prefixes']}
    for x in rows:
        if x['question_id'] in BAD_B or x['correct'] is None or x['truncated']: continue
        byq[x['question_id']][x['correct']].append(x)
    qs = sorted([q for q in byq if byq[q][True] and byq[q][False]], key=lambda q: digest('robust-V-train:'+q))[:64]
    assert len(qs) == 64 and not set(qs) & set(BAD_B)
    selected = {}
    for q in qs:
        for y in [True, False]: selected[q, y] = min(byq[q][y], key=lambda x: digest('robust-V-answer:'+x['candidate_id']))
    units = [(q, name) for q in qs for name in ['original']+cfg['seen_prefixes']]; random.Random(42).shuffle(units)
    for mode in ['clean', 'aug']:
        out = []
        for i, (q, planned) in enumerate(units):
            for y in [True, False]:
                raw = selected[q, y]; variant = vv[planned if mode == 'aug' else 'original']; job = c.encode(tok, raw, variant)
                before = label_response(raw['text'], False, raw['gold_answer'], 'gsm8k')
                after = label_response(variant['prefix']+raw['text'], False, raw['gold_answer'], 'gsm8k')
                assert before['correct'] is y and all(before[k] == after[k] for k in ['correct', 'value', 'status'])
                chosen, rejected = (32, 33) if y else (33, 32); ids = job['prompt_ids']
                out.append({**job, 'example_id': f'{mode}:{i}:{int(y)}', 'paired_unit': i, 'optimizer_step': i//4+1,
                            'chosen': 'A' if y else 'B', 'rejected': 'B' if y else 'A', 'chosen_label_id': chosen,
                            'rejected_label_id': rejected, 'input_ids': ids+[chosen], 'labels': [-100]*len(ids)+[chosen],
                            'prefix_length': len(ids), 'planned_view': planned})
        assert len(out) == 640; write_jsonl(RUN/f'train_{mode}.jsonl', out)
    picked = list(selected.values()); write_jsonl(RUN/'train_answers.jsonl', picked)
    for name in ['C_answers.jsonl', 'C_jobs.jsonl', 'E_inputs.jsonl', 'E_references.jsonl', 'G0_E.jsonl', 'G3_E.jsonl', 'E_answers.jsonl']:
        shutil.copy2(OLD/name, RUN/name)
    write_json(RUN/'source_quality_revision004.json', {'created_at': utc(), 'supersedes_initial_plan_sha256': sha(OLD_PLAN),
               'B_whole_question_quarantine': BAD_B, 'policy': 'Exclude all candidates/views on bad B questions; next eligible questions in same frozen hash order, no V outcomes used',
               'old_training_question_count': 64, 'new_training_question_count': 64,
               'replaced_ids': sorted({x['question_id'] for x in read_jsonl(OLD/'train_answers.jsonl')}-set(qs)),
               'replacement_ids': sorted(set(qs)-{x['question_id'] for x in read_jsonl(OLD/'train_answers.jsonl')}),
               'replacement_questions_text_reviewed': True, 'algorithm_hyperparameters_unchanged': True,
               'all4_restarted_from_V0': True, 'initial4_adapters_retained_as_superseded_pilot': True,
               'E_generation_reused': 'Generated using frozen G0/G3 independent of V. No E V outcomes inspected to choose training replacements/hyperparameters. Only question text inspected for quality.',
               'E_question_quarantine_revision': 'configs/verifier_robustness_20261026_E_quality_revision003.json'})
    # Retain the full original protection inventory; initial new trial is preserved separately.
    shutil.copy2(OLD/'preserved_prior_files.json', RUN/'preserved_prior_files.json')
    shutil.copy2(OLD/'disk_recovery_20261026.json', RUN/'disk_recovery_20261026.json')
    shutil.copy2(OLD/'scorer_legacy_probe.json', RUN/'scorer_legacy_probe.json')
    exposures = []
    for variant in cfg['prefixes']:
        p = variant['prefix'].strip().casefold()
        if p: exposures.append({'variant': variant['name'], 'explicit_augmentation': variant['name'] in cfg['seen_prefixes'],
                  'natural_original_starts_with_phrase': sum(x['text'].lstrip().casefold().startswith(p) for x in picked),
                  'natural_original_contains_phrase': sum(p in x['text'].casefold() for x in picked), 'base_answer_n': 128})
    write_json(RUN/'natural_training_phrase_exposure.json', exposures)
    cfg.update({'created_at': utc(), 'revision': '004_training_question_quality', 'superseded_plan': str(OLD_PLAN.relative_to(ROOT)),
                'superseded_plan_sha256': sha(OLD_PLAN), 'source_quality_revision': str((RUN/'source_quality_revision004.json').relative_to(ROOT))})
    cfg['train']['eligible_mixed_questions'] = 68; cfg['train']['retain_latest_checkpoint_only'] = True
    cfg['train']['checkpoint_retention'] = 'Every16steps commit then retain latest complete checkpoint only; all final adapters, final optimizer, train data/logs retained'
    cfg['evaluation']['E_rule'] = 'Reuse immutable G answers generated for initial trial; no E V performance inspected before source-quality-only retraining. All new final adapters fixed before comparisons.'
    cfg['evaluation']['E_primary_valid_questions'] = 60
    cfg['V_interface']['implementation_revision'] = 'Revision002: original two label rows and prediction position, sparse LM output if legacy-equivalence audit passes, otherwise original scorer; fixed0.5'
    source_paths = [OLD_PLAN, ROOT/'src/pilot/robust_verifier_clean.py', ROOT/'src/pilot/robust_verifier_train.py',
                    ROOT/'configs/verifier_robustness_20261026_E_quality_revision003.json',
                    ROOT/'configs/verifier_robustness_20261026_scoring_revision002.json',
                    ROOT/'configs/verifier_robustness_20261026_transition_endpoints.json']
    cfg['source_sha256'].update({str(p.relative_to(ROOT)): sha(p) for p in source_paths})
    cfg['input_sha256'] = {p.name: sha(p) for p in RUN.glob('*.jsonl')}
    ensure_run_manifest(PLAN, cfg); c.validate()
    write_json(RUN/'state.json', {'status': 'prepared_clean_revision004', 'at': utc()})
    return cfg


def prepare_eval():
    c, t, e, r, v = configure(); cfg = c.validate(); frozen = e.frozen_adapters()
    oldjobs = read_jsonl(OLD/'evaluation_jobs.jsonl')
    jobs = [x for x in oldjobs if x['cohort'] != 'B_training_fit']
    fit = {x['job_id']: x for x in read_jsonl(RUN/'train_aug.jsonl')}
    jobs += [{k: val for k, val in x.items() if k not in ['labels', 'input_ids', 'chosen', 'rejected', 'chosen_label_id', 'rejected_label_id']} |
             {'cohort': 'B_training_fit', 'view': 'G0_B_fit'} for x in fit.values()]
    jobs.sort(key=lambda x: (x['prompt_tokens'], x['job_id'])); assert len(jobs) == len({x['job_id'] for x in jobs}) == 7116
    write_jsonl(RUN/'evaluation_jobs.jsonl', jobs)
    ensure_run_manifest(RUN/'evaluation.manifest.json', {'plan_sha256': sha(PLAN), 'source_quality_sha256': sha(RUN/'source_quality_revision004.json'),
               'E_quality_sha256': sha(ROOT/'configs/verifier_robustness_20261026_E_quality_revision003.json'),
               'all4_final_adapters': frozen, 'jobs_sha256': sha(RUN/'evaluation_jobs.jsonl'),
               'new_E_answers_sha256': sha(RUN/'E_answers.jsonl'), 'code_sha256': sha(ROOT/'src/pilot/robust_verifier_eval.py'),
               'common_sha256': sha(ROOT/'src/pilot/robust_verifier_common.py'), 'models': ['V0']+c.ARMS, 'scores_per_model': 7116,
               'microbatch': 'legacy two rows per answer, no cross-answer padding',
               'initial_V0_cache_reuse': 'Only exact full prompt and same original V0/threshold/scorer; no initial updated-model scores reused'})
    destination = RUN/'scoring/V0'; destination.mkdir(parents=True, exist_ok=True)
    if not (destination/'scores.jsonl').exists():
        valid = {x['job_id']: x for x in jobs}; reused = []
        oldpath = OLD/'scoring/V0/scores.jsonl'
        for line, s in enumerate(recover_jsonl(oldpath), 1):
            if s['job_id'] not in valid: continue
            assert s['prompt_sha256'] == valid[s['job_id']]['prompt_sha256'] and s['adapter_fingerprint'] is None and s['threshold'] == .5
            reused.append({**s, 'evaluation_sha256': sha(RUN/'evaluation.manifest.json'),
                           'reuse_source': str(oldpath.relative_to(ROOT)), 'reuse_line': line,
                           'reuse_source_evaluation_sha256': s['evaluation_sha256']})
        write_jsonl(destination/'scores.jsonl', reused)
        shutil.copy2(OLD/'scoring/V0/legacy_equivalence.json', destination/'legacy_equivalence.json')
    write_json(RUN/'state.json', {'status': 'evaluation_prepared', 'scores_per_model': 7116, 'at': utc()})


def main():
    p = argparse.ArgumentParser(); p.add_argument('stage', choices=['prepare', 'references', 'train', 'prepare_eval', 'score', 'report', 'verify']); p.add_argument('--arm')
    args = p.parse_args(); c, t, e, r, v = configure()
    if args.stage == 'prepare': prepare()
    elif args.stage == 'references': t.references()
    elif args.stage == 'train': t.train(args.arm)
    elif args.stage == 'prepare_eval': prepare_eval()
    elif args.stage == 'score': e.score()
    elif args.stage == 'report': r.main()
    else: v.main()


if __name__ == '__main__': main()
