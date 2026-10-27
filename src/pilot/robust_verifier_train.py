"""Resumable label-only SFT and genuine reference-relative label DPO."""
import argparse
import gc
import hashlib
import json
import math
import time

from .core import ROOT, read_jsonl, recover_jsonl, write_json, write_jsonl, append_jsonl, digest, ensure_run_manifest
from .formal_common import sha, utc, gpu_lock, WorkBudget, messages
from .runtime import load, model_identity, seed_all, conditional_label_scores
from .formal_train import save_rng, restore_rng
from .robust_verifier_common import RUN, PLAN, ARMS, prepare, validate, next_logits, score_batch


def references():
    import torch
    cfg = prepare(); out = RUN/'V0_train_reference.jsonl'; existing = recover_jsonl(out)
    done = {r['job_id'] for r in existing}; jobs = {}
    for name in ['clean', 'aug']:
        for r in read_jsonl(RUN/f'train_{name}.jsonl'): jobs.setdefault(r['job_id'], r)
    if len(done) == len(jobs): return
    with gpu_lock():
        budget = WorkBudget('robust_verifier/reference'); tick = time.perf_counter(); model, tok = load('V')
        # Scorer revision is explicit: no historical V0 scores are silently mixed.
        if not (RUN/'scorer_legacy_probe.json').exists():
            chosen = read_jsonl(RUN/'C_jobs.jsonl')[::max(1, 4940//16)][:16]
            answers = {r['candidate_id']: r for r in read_jsonl(RUN/'C_answers.jsonl')}; checks = []
            for i in range(0, len(chosen), 4):
                batch = chosen[i:i+4]; fast = score_batch(model, batch)
                for r, s in zip(batch, fast):
                    base = answers[r['candidate_id']]
                    old = conditional_label_scores(model, tok, messages('V', base['question'], r['prefix']+base['text']))
                    checks.append({'candidate_id': r['candidate_id'], 'variant': r['variant'], 'old_score': old['score'],
                                   'new_score': s['score'], 'same_decision': old['accepted'] == s['accepted'],
                                   'max_LL_difference': max(abs(old[k]-s[k]) for k in ['log_likelihood_A', 'log_likelihood_B'])})
            write_json(RUN/'scorer_legacy_probe.json', {'checks': checks, 'same_decisions': sum(c['same_decision'] for c in checks),
                       'total': len(checks), 'new_baseline_everywhere': True, 'at': utc()}); budget.charge('legacy_probe')
        for i, r in enumerate(jobs.values()):
            if r['job_id'] in done: continue
            budget.check(); s = score_batch(model, [r])[0]
            append_jsonl(out, {k: r[k] for k in ['job_id', 'candidate_id', 'variant', 'prompt_sha256']} | s)
            done.add(r['job_id']); budget.charge(r['job_id'])
            if len(done)%40 == 0: print('reference', len(done), '/', len(jobs), flush=True)
        write_json(RUN/'reference_compute.json', {'seconds': time.perf_counter()-tick, 'unique_full_prompts': len(done), 'model': model_identity('V')[0]})
        del model; gc.collect(); torch.cuda.empty_cache(); budget.charge('reference_finalization')


def loss_for(logits, r, reference, arm, beta):
    import torch
    import torch.nn.functional as F
    if arm.startswith('SFT'):
        return F.cross_entropy(logits, torch.tensor([r['chosen_label_id']], device=logits.device))
    chosen = logits[0, r['chosen_label_id']]; rejected = logits[0, r['rejected_label_id']]
    ref = reference[r['job_id']]; sign = 1 if r['correct'] else -1
    gap = sign*(ref['log_likelihood_A']-ref['log_likelihood_B'])
    # Full-vocabulary normalizers cancel for two one-token completions of ONE input.
    return -F.logsigmoid(beta*((chosen-rejected)-gap))


def train(arm):
    import torch
    from peft import LoraConfig, get_peft_model, PeftModel
    cfg = validate(); settings = cfg['train']; directory = RUN/arm; directory.mkdir(exist_ok=True)
    final = directory/'adapter'; report = directory/'training_report.json'
    if report.exists():
        assert model_identity('V', final)[1] == json.loads(report.read_text())['adapter_fingerprint']; return
    mode = arm.split('_')[1]; data = read_jsonl(RUN/f'train_{mode}.jsonl')
    reference = {r['job_id']: r for r in read_jsonl(RUN/'V0_train_reference.jsonl')}
    assert all(r['job_id'] in reference for r in data)
    ensure_run_manifest(directory/'manifest.json', {'plan_sha256': sha(PLAN), 'code_sha256': sha(ROOT/'src/pilot/robust_verifier_train.py'),
             'common_sha256': sha(ROOT/'src/pilot/robust_verifier_common.py'), 'data_sha256': sha(RUN/f'train_{mode}.jsonl'),
             'reference_sha256': sha(RUN/'V0_train_reference.jsonl'), 'arm': arm})
    checkpoints = directory/'checkpoints'; checkpoints.mkdir(exist_ok=True)
    complete = sorted(p for p in checkpoints.glob('step_*') if (p/'complete.json').exists()); resume = complete[-1] if complete else None
    with gpu_lock():
        budget = WorkBudget('robust_verifier/train/'+arm); tick = time.perf_counter(); seed_all(settings['seed'])
        model, tok = load('V'); seed_all(settings['seed'])
        if resume: model = PeftModel.from_pretrained(model, str(resume), is_trainable=True, local_files_only=True)
        else: model = get_peft_model(model, LoraConfig(task_type='CAUSAL_LM', r=settings['r'], lora_alpha=settings['alpha'],
                                          lora_dropout=settings['dropout'], target_modules=settings['target_modules']))
        params = {n: p for n, p in model.named_parameters() if p.requires_grad}
        frozen = {n: p for n, p in model.named_parameters() if not p.requires_grad}; versions = {n: p._version for n, p in frozen.items()}
        assert params and all('lora_' in n and p.dtype == torch.float32 for n, p in params.items())
        before = {n: p.detach().cpu().clone() for n, p in params.items()}
        if not resume:
            h = digest({n: hashlib.sha256(p.detach().cpu().numpy().tobytes()).hexdigest() for n, p in params.items()})
            ensure_run_manifest(RUN/'shared_initial_adapter.json', {'hash': h, 'base_fingerprint': model_identity('V')[0], 'seed': settings['seed']})
            model.eval(); points = score_batch(model, [data[0]])
            assert abs(points[0]['score']-reference[data[0]['job_id']]['score']) < 2e-6
            write_json(directory/'initialization.json', {'fresh_V0': True, 'initial_adapter_hash': h, 'no_parent_adapter': True})
        optimizer = torch.optim.AdamW(list(params.values()), lr=settings['learning_rate'], betas=tuple(settings['betas']),
                         eps=settings['epsilon'], weight_decay=settings['weight_decay'])
        start = 0; logs = recover_jsonl(directory/'steps.jsonl')
        if resume:
            saved = json.loads((resume/'complete.json').read_text()); assert saved['manifest'] == sha(directory/'manifest.json')
            start = saved['step']; optimizer.load_state_dict(torch.load(resume/'optimizer.pt', map_location='cpu', weights_only=True))
        if logs and any(r['step'] > start for r in logs):
            write_jsonl(directory/f'rolled_back_{time.time_ns()}.jsonl', logs)
            write_jsonl(directory/'steps.jsonl', [r for r in logs if r['step'] <= start])
        model.train(); model.config.use_cache = False
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})
        optimizer.zero_grad(set_to_none=True); torch.cuda.reset_peak_memory_stats()
        if resume: restore_rng(resume)
        else:
            longest = max(data, key=lambda r: r['prompt_tokens']); probe = loss_for(next_logits(model, [longest]), longest, reference, arm, settings['beta'])
            assert torch.isfinite(probe); probe.backward(); optimizer.zero_grad(set_to_none=True)
            write_json(directory/'longest_backward.json', {'prompt_tokens': longest['prompt_tokens'], 'loss': float(probe.detach()),
                       'optimizer_steps': 0, 'no_truncation': True}); del probe; seed_all(settings['seed'])
        for index in range(start, settings['steps']):
            budget.check(); batch = data[index*8:index*8+8]; assert len(batch) == 8 and sum(r['correct'] for r in batch) == 4
            factor = (index+1)/settings['warmup_steps'] if index < settings['warmup_steps'] else .1+.9*.5*(1+math.cos(math.pi*(index-settings['warmup_steps'])/(settings['steps']-settings['warmup_steps'])))
            for group in optimizer.param_groups: group['lr'] = settings['learning_rate']*factor
            losses = []; step_tick = time.perf_counter()
            for r in batch:
                assert r['input_ids'] == r['prompt_ids']+[r['chosen_label_id']]
                assert r['labels'] == [-100]*len(r['prompt_ids'])+[r['chosen_label_id']]
                loss = loss_for(next_logits(model, [r]), r, reference, arm, settings['beta'])
                assert torch.isfinite(loss); (loss/8).backward(); losses.append(float(loss.detach()))
            norm = float(torch.nn.utils.clip_grad_norm_(list(params.values()), settings['max_grad_norm']))
            assert math.isfinite(norm) and norm > 0 and all(p.grad is None for p in frozen.values())
            optimizer.step(); optimizer.zero_grad(set_to_none=True); step = index+1
            append_jsonl(directory/'steps.jsonl', {'step': step, 'mean_loss': sum(losses)/8, 'gradient_L2': norm,
                   'learning_rate': optimizer.param_groups[0]['lr'], 'examples': 8, 'correct': 4, 'wrong': 4,
                   'input_tokens': sum(r['prompt_tokens'] for r in batch), 'chosen_label_tokens': 8,
                   'rejected_label_tokens_used': 8 if arm.startswith('DPO') else 0,
                   'example_ids': [r['example_id'] for r in batch], 'seconds': time.perf_counter()-step_tick})
            budget.charge('step_'+str(step)); write_json(RUN/'state.json', {'status': 'training', 'arm': arm, 'step': step, 'total_steps': 80, 'at': utc()})
            print(arm, step, '/80', sum(losses)/8, flush=True)
            if step%settings['checkpoint_every'] == 0 or step == settings['steps']:
                dest = checkpoints/f'step_{step:04d}'; dest.mkdir(exist_ok=True); model.save_pretrained(dest)
                torch.save(optimizer.state_dict(), dest/'optimizer.pt'); save_rng(dest)
                write_json(dest/'complete.json', {'step': step, 'manifest': sha(directory/'manifest.json'), 'at': utc()}); budget.charge('checkpoint')
                if settings.get('retain_latest_checkpoint_only'):
                    # Only this new arm's fully superseded checkpoints; never prior experiments.
                    import shutil
                    retention = []
                    for previous in sorted(checkpoints.glob('step_*')):
                        if previous == dest or not (previous/'complete.json').exists(): continue
                        retention.append({'step_directory': str(previous.relative_to(RUN)),
                                          'files': {p.name: sha(p) for p in previous.iterdir() if p.is_file()}})
                        shutil.rmtree(previous)
                    if retention:
                        append_jsonl(directory/'checkpoint_retention.jsonl', {'after_complete_step': step,
                                     'removed_superseded_checkpoints': retention, 'latest_full_resume_preserved': True, 'at': utc()})
        assert all(p._version == versions[n] for n, p in frozen.items())
        change = math.sqrt(sum(float((p.detach().cpu()-before[n]).square().sum()) for n, p in params.items())); assert change > 0
        model.eval(); probe_rows = data[:8]; expected = score_batch(model, probe_rows[:4])+score_batch(model, probe_rows[4:])
        model.save_pretrained(final); adapter_hash = model_identity('V', final)[1]
        peak = torch.cuda.max_memory_allocated(); del model, params, frozen, optimizer, before, loss; gc.collect(); torch.cuda.empty_cache()
        model, tok = load('V', final); observed = score_batch(model, probe_rows[:4])+score_batch(model, probe_rows[4:])
        assert all(a['accepted'] == b['accepted'] and abs(a['score']-b['score']) < 2e-6 for a, b in zip(expected, observed))
        logs = read_jsonl(directory/'steps.jsonl'); assert len(logs) == 80 and sum(r['examples'] for r in logs) == 640
        write_json(report, {'arm': arm, 'steps': 80, 'examples_presented': 640, 'unique_math_answers': 128, 'unique_questions': 64,
                   'input_tokens': sum(r['input_tokens'] for r in logs), 'chosen_loss_tokens': 640,
                   'rejected_tokens_used': 640 if arm.startswith('DPO') else 0, 'adapter_delta_L2_this_invocation': change,
                   'base_unchanged': True, 'save_reload_passed': True, 'peak_memory_bytes': peak,
                   'seconds_this_invocation': time.perf_counter()-tick, 'adapter_fingerprint': adapter_hash, 'at': utc()})
        del model; gc.collect(); torch.cuda.empty_cache(); budget.charge('training_finalization')


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('stage', choices=['references', 'train']); parser.add_argument('--arm', choices=ARMS)
    args = parser.parse_args()
    if args.stage == 'references': references()
    else: train(args.arm)


if __name__ == '__main__': main()
