"""Frozen, separate 2x2 verifier-objective/data-augmentation experiment."""
import json
import random
import re
from collections import defaultdict, Counter
from pathlib import Path

from .core import ROOT, read_jsonl, write_json, write_jsonl, digest, ensure_run_manifest, prompt_ids
from .formal_common import protocol, messages, verify_fixed, sha, utc
from .verifier_update import join_source
from .g3_common import RUN as G3RUN, pool, ENGINE
from .cross_round_prefix import variants
from .day2_protocol import label_response

RUN = ROOT/'runs/verifier_robustness_20261026'
REPORT = ROOT/'reports/verifier_robustness_20261026'
PLAN = ROOT/'configs/verifier_robustness_20261026.json'
ARMS = ['SFT_clean', 'SFT_aug', 'DPO_clean', 'DPO_aug']
SEEN = ['we_need_to', 'we_can', 'it_is_essential_to', 'symbol_comma']
UNSEEN = ['we_will', 'we_have_to', 'thought_process', 'reason_step_by_step']


def validate():
    cfg = json.loads(PLAN.read_text()); verify_fixed()
    assert all(sha(ROOT/p) == h for p, h in cfg['source_sha256'].items())
    assert all(sha(RUN/p) == h for p, h in cfg['input_sha256'].items())
    return cfg


def encode(tok, r, variant):
    text = variant['prefix']+r['text']; msgs = messages('V', r['question'], text)
    ids = prompt_ids(tok, msgs); assert len(ids)+4 <= 8192
    return {'candidate_id': r['candidate_id'], 'question_id': r['question_id'], 'view': r.get('view', r['G_round']),
            'G_round': r['G_round'], 'variant': variant['name'], 'prefix': variant['prefix'],
            'correct': r['correct'], 'prompt_ids': ids, 'prompt_sha256': digest(msgs), 'text_sha256': digest(text),
            'prompt_tokens': len(ids), 'prefix_tokens': len(tok.encode(variant['prefix'], add_special_tokens=False)),
            'job_id': digest([r['candidate_id'], variant['prefix']])}


def prepare():
    RUN.mkdir(exist_ok=True); REPORT.mkdir(exist_ok=True)
    if PLAN.exists(): return validate()
    verify_fixed()
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(protocol()['models']['V']['path'], local_files_only=True)
    allv = {v['name']: v for v in variants()}; vv = [allv[n] for n in ['original', 'neutral_control']+SEEN+UNSEEN]
    train, paths = join_source(ROOT/'runs/formal', 'G0', 'B'); byq = defaultdict(lambda: defaultdict(list))
    for r in train:
        if r['correct'] is None or r['truncated']: continue
        lab = label_response(r['text'], False, r['gold_answer'], 'gsm8k'); assert lab['correct'] is r['correct']
        if all(len(prompt_ids(tok, messages('V', r['question'], allv[n]['prefix']+r['text'])))+4 <= 8192 for n in ['original']+SEEN):
            byq[r['question_id']][r['correct']].append(r)
    eligible = [q for q, groups in byq.items() if groups[True] and groups[False]]
    questions = sorted(eligible, key=lambda q: digest('robust-V-train:'+q))[:64]; assert len(questions) == 64
    picked = []
    for q in questions:
        for y in [True, False]: picked.append(min(byq[q][y], key=lambda r: digest('robust-V-answer:'+r['candidate_id'])))
    # Each unit is a SAME-question, SAME-prefix positive/negative pair.
    units = [(q, v) for q in questions for v in ['original']+SEEN]; random.Random(42).shuffle(units)
    selected = {(r['question_id'], r['correct']): r for r in picked}; datasets = {}
    for mode in ['clean', 'aug']:
        out = []
        for i, (q, view) in enumerate(units):
            for y in [True, False]:
                r = selected[q, y]; v = allv[view if mode == 'aug' else 'original']
                before = label_response(r['text'], False, r['gold_answer'], 'gsm8k')
                after = label_response(v['prefix']+r['text'], False, r['gold_answer'], 'gsm8k')
                assert all(before[k] == after[k] for k in ['correct', 'value', 'status'])
                job = encode(tok, r, v); chosen = 32 if y else 33; rejected = 33 if y else 32
                assert tok.encode('A' if y else 'B', add_special_tokens=False) == [chosen]
                ids = job['prompt_ids']; full = ids+[chosen]; mask = [-100]*len(ids)+[chosen]
                assert sum(t != -100 for t in mask) == 1
                out.append({**job, 'example_id': f'{mode}:{i}:{int(y)}', 'paired_unit': i, 'optimizer_step': i//4+1,
                            'chosen': 'A' if y else 'B', 'rejected': 'B' if y else 'A',
                            'chosen_label_id': chosen, 'rejected_label_id': rejected, 'input_ids': full, 'labels': mask,
                            'prefix_length': len(ids), 'planned_view': view})
        assert len(out) == 640 and sum(r['correct'] for r in out) == 320
        for k in range(0, len(out), 8): assert sum(r['correct'] for r in out[k:k+8]) == 4
        datasets[mode] = out; write_jsonl(RUN/f'train_{mode}.jsonl', out)
    write_jsonl(RUN/'train_answers.jsonl', picked)
    evaluation = []
    cids = {r['question_id'] for r in pool('C')}; assert not cids & set(questions)
    for g, directory in [('G0', ROOT/'runs/formal'), ('G1', ROOT/'runs/formal'), ('G2', G3RUN), ('G3', G3RUN)]:
        rr, pp = join_source(directory, g, 'C', cids, g+('_TF' if g in ['G0', 'G1'] else '_vLLM'))
        evaluation += rr; paths += pp
    write_jsonl(RUN/'C_answers.jsonl', evaluation)
    jobs = []
    for r in evaluation:
        for v in vv if r['G_round'] == 'G3' else [allv['original']]:
            before = label_response(r['text'], r['truncated'], r['gold_answer'], 'gsm8k')
            after = label_response(v['prefix']+r['text'], r['truncated'], r['gold_answer'], 'gsm8k')
            assert all(before[k] == after[k] for k in ['correct', 'value', 'status'])
            jobs.append(encode(tok, r, v))
    assert len(jobs) == 4940; write_jsonl(RUN/'C_jobs.jsonl', jobs)
    # New question families, unused by any original ABC/T pool. Hash selection uses no gold or scores.
    used = read_jsonl(ROOT/'data/dapo/inputs/dapo100.jsonl')
    for split in ['A_G_update', 'B_V_update', 'T_final']: used += read_jsonl(ROOT/f'data/dapo/formal/inputs/{split}.jsonl')
    usedids = {r['question_id'] for r in used}; usedf = {r['family_id'] for r in used}; usedh = {r['question_hash'] for r in used}
    usedu = {u for r in used for u in r['family_source_uuids']}
    inventory = read_jsonl(ROOT/'data/dapo/eligible_families.jsonl'); fresh = []; exclusions = Counter()
    for r in sorted(inventory, key=lambda r: digest('robust-V-new-final:'+r['question_id'])):
        if r['question_id'] in usedids or r['family_id'] in usedf or r['question_hash'] in usedh or set(r['family_source_uuids']) & usedu:
            exclusions['prior_family'] += 1; continue
        if re.search(r'\[asy\]|<img|!\[|\b(?:diagram|picture|figure)\b|as shown', r['question'], re.I):
            exclusions['possible_missing_figure'] += 1; continue
        fresh.append(r)
        if len(fresh) == 64: break
    assert len(fresh) == 64
    write_jsonl(RUN/'E_inputs.jsonl', [{k: v for k, v in r.items() if k not in ['gold_answer', 'reference_consistent']} | {'split': 'E_new_final'} for r in fresh])
    write_jsonl(RUN/'E_references.jsonl', [{'question_id': r['question_id'], 'gold_answer': r['gold_answer']} for r in fresh])
    paths += [ROOT/'data/dapo/eligible_families.jsonl', ROOT/'src/pilot/runtime.py', ROOT/'src/pilot/day2_protocol.py',
              ROOT/'configs/protocol_revisions/003_dapo_day3.json', Path(__file__)]
    # Protect prior models/configuration/raw results; new products stay in a separate directory.
    protected = {}
    for directory in ['configs', 'runs', 'reports']:
        for p in (ROOT/directory).rglob('*'):
            if not p.is_file() or RUN in p.parents or REPORT in p.parents or p == PLAN: continue
            if p.name.endswith('.log') or p.name in ['work_ledger.jsonl', '.gpu.lock']: continue
            protected[str(p.relative_to(ROOT))] = sha(p)
    write_json(RUN/'preserved_prior_files.json', protected)
    cfg = {'created_at': utc(), 'design': '2x2: full-vocabulary label-only SFT vs reference-V0 DPO, natural-only vs content-preserving prefix augmentation',
           'arms': ARMS, 'initialization': 'Each fresh original V0 + identical seeded new LoRA. No Vrand/Vtarget/G adapter inheritance.',
           'train': {'questions': 64, 'base_answers': 128, 'correct': 64, 'wrong': 64, 'source': 'B / G0 only',
                     'eligible_mixed_questions': len(eligible), 'examples_per_arm': 640, 'steps': 80, 'micro_batch': 1,
                     'gradient_accumulation': 8, 'per_step_correct_wrong': [4, 4], 'epochs_augmented': 1,
                     'natural_control': 'Same128 answers presented5 times, explicitly a5-epoch matched-presentation control; not640 independent answers',
                     'augmentation': 'Original plus each of4 seen prefixes on EVERY positive and negative answer; no V0-error mining',
                     'prefix_only': 'Not in primary training: invalid/no-answer examples are distinct from natural mathematical errors',
                     'seed': 42, 'learning_rate': 2e-5, 'warmup_steps': 8, 'schedule': 'linear warmup then cosine to10%LR',
                     'optimizer': 'AdamW', 'betas': [.9, .999], 'epsilon': 1e-8, 'weight_decay': .01, 'max_grad_norm': 1.,
                     'r': 8, 'alpha': 16, 'dropout': 0., 'target_modules': protocol()['training']['V_smoke']['target_modules'],
                     'dtype': 'BF16 frozen base; PEFT FP32 LoRA', 'max_context': 8192, 'gradient_checkpointing': True,
                     'SFT_loss': 'Ordinary full-vocabulary next-token CE ONLY on chosen A/B, prompt/mathematical response masked; no EOS/newline loss',
                     'DPO_loss': '-logsigmoid(beta*((LLchosen-LLrejected)policy-(LLchosen-LLrejected)V0)), beta0.1, bare A/B one token',
                     'beta': .1, 'reference': 'Frozen original V0 log-likelihoods precomputed on identical full inputs; no trainable reference copy',
                     'checkpoint_every': 16, 'checkpoint_selection': 'Final step80 only. No C/E tuning, early stopping or retraining based on outcomes'},
           'prefixes': vv, 'seen_prefixes': SEEN, 'unseen_prefixes': UNSEEN,
           'V_interface': {'messages': messages('V', '{question}', '{prefix}{unchanged_response}'), 'labels': ['A', 'B'], 'threshold': .5,
                           'model_fingerprint': protocol()['models']['V']['fingerprint'], 'reference_adapter': None,
                           'score': 'Full-vocabulary log-softmax for bare A/B; softmax over those two LL; margin LL_A-LL_B',
                           'implementation_revision': 'Single common prompt next-token forward, last-position logits only, fixed microbatch4 with left padding and explicit position_ids. All V0/new-arm evaluation freshly computed in this implementation; do not mix legacy scores.',
                           'audit': 'Compare to legacy conditional_label_scores on fixed controls; record differences, no unreported scorer substitution'},
           'evaluation': {'C': 'All clean C95x4 cached G0/G1/G2/G3 natural outputs; G3 all10 prefix settings; repeated-use development',
                          'C_generation_engine': 'G0/G1 TF, G2/G3 vLLM. Cross-boundary differences descriptive only.',
                          'E': '64 freshly hash-selected unused question families; G0/G3 both same frozen vLLM,4 answers each; original,we_can,we_will',
                          'E_rule': 'Generate and evaluate after all4 final adapters frozen; no hyperparameter/threshold selection on E; new final pilot cohort, not previously viewed T',
                          'E_preselection_exclusions': dict(exclusions), 'unknown': 'Retain unknowns and truncated answers, never treat as negatives',
                          'primary': 'Joint FPR/FNR and BA on E G3 seen we_can; clean-original retention and unseen we_will transfer alongside; report all groups',
                          'secondary': 'C seen4 and unseen4 equally weighted summaries, invariant decision flip rates, absolute margin shift, AUROC, accepted error risk',
                          'comparisons': 'Within objective augmented-clean, each arm-V0, augmented SFT-DPO; paired whole-question bootstrap10000; interval estimates exploratory one-seed pilot',
                          'success': 'Smaller intervention sensitivity with useful discrimination; never call all-accept/all-reject robust repair. Report both error types and natural BA.',
                          'old_T': 'No new old-T inference or tuning'},
           'generation': {'settings': ENGINE, 'rounds': ['G0', 'G3'], 'questions': 64, 'n': 4, 'max_new_tokens': 3840, 'max_total_tokens': 4096,
                          'temperature': .7, 'top_p': .9, 'top_k': 50, 'seed': '42+hash(question)%1000000'},
           'source_sha256': {str(p.relative_to(ROOT)): sha(p) for p in paths},
           'input_sha256': {p.name: sha(p) for p in RUN.glob('*.jsonl')},
           'template_tokenizer': {'config_sha256': sha(Path(protocol()['models']['V']['path'])/'tokenizer_config.json'),
                                  'chat_template': tok.chat_template},
           'references': ['https://arxiv.org/html/2512.17375v3#A33', 'https://arxiv.org/html/2305.18290'],
           'limits': ['One seed and small training/question budget; no objective universally superior claim',
                      'Independent final-answer labels do not certify complete reasoning', 'Held-out phrases may share words/meaning with seen phrases; lexical holdout, not mechanism-family holdout',
                      'Differences from former V update also include one-token loss, LR, balanced minibatches and data; no unique failure-cause attribution']}
    ensure_run_manifest(PLAN, cfg); validate()
    write_json(RUN/'state.json', {'status': 'prepared', 'at': utc(), 'arms': ARMS, 'steps_each': 80})
    return cfg


def next_logits(model, batch):
    import torch
    length = max(len(r['prompt_ids']) for r in batch); pad = model.config.pad_token_id or 151643
    ids = torch.tensor([[pad]*(length-len(r['prompt_ids']))+r['prompt_ids'] for r in batch], device='cuda')
    mask = torch.tensor([[0]*(length-len(r['prompt_ids']))+[1]*len(r['prompt_ids']) for r in batch], device='cuda')
    positions = mask.long().cumsum(-1)-1; positions.masked_fill_(mask == 0, 0)
    return model(input_ids=ids, attention_mask=mask, position_ids=positions, use_cache=False, logits_to_keep=1).logits[:, -1].float()


def score_batch(model, batch):
    import torch
    with torch.inference_mode():
        logps = next_logits(model, batch).log_softmax(-1)[:, [32, 33]]
        ps = logps.softmax(-1)
    return [{'log_likelihood_A': float(ll[0]), 'log_likelihood_B': float(ll[1]), 'score': float(p[0]),
             'accepted': bool(ll[0] >= ll[1]), 'margin': float(ll[0]-ll[1]), 'prompt_tokens': len(r['prompt_ids'])}
            for ll, p, r in zip(logps, ps, batch)]


if __name__ == '__main__': print(json.dumps(prepare()['train'], indent=2), flush=True)
