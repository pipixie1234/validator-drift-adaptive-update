"""Two independent, question-sharded G processes; original sampler stays unchanged.

Only the coordinator owns canonical output and the GPU lock. Workers use the
original formal_run generation function with isolated paths and question lists.
"""
import argparse
import contextlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from .core import ROOT, read_jsonl, recover_jsonl, write_json, append_jsonl, digest
from .formal_common import RUN, inputs, input_path, output_path, adapter_path, messages, verify_fixed, gpu_lock, WorkBudget, progress, sha, utc, PROTOCOL, PLAN, protocol
from .runtime import model_identity


def worker(assignment_path):
    from . import formal_run as original
    assignment = json.loads(Path(assignment_path).read_text())
    r, s = assignment['round'], assignment['split']
    all_rows = inputs(s)
    wanted = assignment['question_ids']
    assert len(wanted) == len(set(wanted)) and set(wanted) <= {x['question_id'] for x in all_rows}
    by_id = {x['question_id']: x for x in all_rows}
    rows = [by_id[q] for q in wanted]
    directory = Path(assignment['directory'])
    directory.mkdir(parents=True, exist_ok=True)
    assert assignment['original_runner_sha256'] == sha(ROOT/'src/pilot/formal_run.py')
    assert assignment['wrapper_sha256'] == sha(Path(__file__))
    original.inputs = lambda split: rows if split == s else (_ for _ in ()).throw(ValueError(split))
    original.output_path = lambda *args: directory/'generated.jsonl'
    original.RUN = directory
    original.gpu_lock = contextlib.nullcontext  # coordinator holds the exclusive stage lock
    original.progress = lambda stage, **fields: write_json(directory/'progress.json', {'stage': stage, 'updated_at': utc(), 'pid': os.getpid(), **fields})

    class CoordinatorBudget:
        def __init__(self, stage): pass
        def check(self):
            if (directory.parent/'cancel').exists(): raise RuntimeError('Coordinator requested stop')
        def charge(self, unit): pass  # one GPU wall clock is charged by the coordinator, not twice

    original.WorkBudget = CoordinatorBudget
    original.run('generate', r, s)


def partition(rows, old, count=2):
    """Whole questions only; every already saved candidate is retained verbatim."""
    ids = {x['question_id'] for x in rows}
    assert all(x['question_id'] in ids and x['candidate_index'] in range(4) for x in old)
    keys = [(x['question_id'], x['candidate_index']) for x in old]
    assert len(keys) == len(set(keys)), 'Duplicate saved candidate'
    completed = {q for q in ids if sum(x['question_id'] == q for x in old) == 4}
    pending = [x['question_id'] for x in rows if x['question_id'] not in completed]
    return [pending[i::count] for i in range(count)]


def merge_ready(output, worker_paths, rows, old):
    """The only canonical writer. A conflicting duplicate is an error, never overwritten."""
    existing = {(r['question_id'], r['candidate_index']): r for r in old}
    available = {}
    allowed = {r['question_id'] for r in rows}
    for path in worker_paths:
        if not path.exists(): continue
        # A worker can be in the middle of its last append. Do not recover/truncate its live file.
        raw = path.read_bytes()
        for line in raw.splitlines(keepends=True):
            if not line.endswith(b'\n'): break
            r = json.loads(line)
            key = (r['question_id'], r['candidate_index'])
            assert r['question_id'] in allowed and r['candidate_index'] in range(4)
            assert key not in available, 'Question assigned to multiple workers'
            available[key] = r
            if key in existing:
                for field in ['candidate_id', 'cache_key', 'text', 'output_tokens', 'truncated', 'model_fingerprint', 'adapter_fingerprint']:
                    if field in existing[key] or field in r:
                        assert existing[key].get(field) == r.get(field), f'Conflicting saved candidate: {key}/{field}'
    added = 0
    for row in rows:
        q = row['question_id']
        if not all((q, i) in existing or (q, i) in available for i in range(4)): continue
        for i in range(4):
            key = q, i
            if key in existing: continue
            r = available[key]
            assert r['question_id'] == q and r['candidate_index'] == i
            assert not any(k in r for k in ['gold_answer', 'reference', 'correct'])
            append_jsonl(output, r)
            existing[key] = r
            old.append(r)
            added += 1
    return added


def generate(round_name, split):
    with gpu_lock():
        verify_fixed()
        rows = inputs(split)
        output = output_path(round_name, split)
        old = recover_jsonl(output)
        initial_bytes = output.read_bytes() if output.exists() else b''
        # Recover complete worker questions saved just before an interrupted coordinator.
        previous = RUN/'parallel'/f'{round_name}_{split}'
        for attempt in sorted(previous.glob('attempt_*')):
            marker = json.loads((attempt/'coordinator.json').read_text())
            assert marker['original_runner_sha256'] == sha(ROOT/'src/pilot/formal_run.py')
            merge_ready(output, sorted(attempt.glob('worker_*/generated.jsonl')), rows, old)
        shards = partition(rows, old)
        if not any(shards):
            print(f'Cached parallel G/{round_name}/{split}: {len(old)}', flush=True)
            return
        # Preserve original cache manifest identity. New execution provenance is separate.
        from .core import ensure_run_manifest
        base, ah = model_identity('G', adapter_path(round_name))
        p = protocol()
        ensure_run_manifest(output.with_suffix('.manifest.json'), {'role': 'G', 'G_round': round_name, 'split': split,
            'source_sha256': sha(input_path(split)), 'effective_question_ids': [r['question_id'] for r in rows],
            'model_fingerprint': base, 'adapter_fingerprint': ah, 'settings': p['sampling'],
            'prompt_templates': messages('G', '{question}', '{response}'), 'output_budget_rule': p['G_output_budget_rule'],
            'protocol_sha256': sha(PROTOCOL), 'execution_sha256': sha(PLAN),
            'runner_sha256': sha(ROOT/'src/pilot/formal_run.py'), 'common_sha256': sha(ROOT/'src/pilot/formal_common.py'),
            'fixed_scoring_sha256': sha(ROOT/'src/pilot/runtime.py')})
        directory = RUN/'parallel'/f'{round_name}_{split}'/f'attempt_{time.time_ns()}'
        directory.mkdir(parents=True)
        write_json(directory/'coordinator.json', {'pid': os.getpid(), 'started_at': utc(), 'workers': 2,
            'initial_candidates': len(old), 'initial_output_bytes': len(initial_bytes), 'initial_output_prefix_sha256': __import__('hashlib').sha256(initial_bytes).hexdigest(),
            'original_runner_sha256': sha(ROOT/'src/pilot/formal_run.py'), 'wrapper_sha256': sha(Path(__file__)),
            'shards': shards, 'sampling_and_per_question_seed_unchanged': True, 'worker_outputs_are_separate': True,
            'budget_accounting': 'Coordinator charges occupied single-GPU wall time once, including model loads; worker times are not summed.'})
        budget = WorkBudget(f'G_parallel/{round_name}/{split}')
        children, logs, paths = [], [], []
        def stop(signum, frame):
            raise SystemExit(128+signum)
        previous_handlers = {s: signal.signal(s, stop) for s in [signal.SIGTERM, signal.SIGINT]}
        try:
            for i, qids in enumerate(shards):
                if not qids: continue
                wd = directory/f'worker_{i}'
                wd.mkdir()
                assignment = wd/'assignment.json'
                write_json(assignment, {'round': round_name, 'split': split, 'question_ids': qids,
                    'directory': str(wd), 'original_runner_sha256': sha(ROOT/'src/pilot/formal_run.py'), 'wrapper_sha256': sha(Path(__file__))})
                log = (wd/'worker.log').open('ab'); logs.append(log)
                children.append(subprocess.Popen([str(ROOT/'scripts/pilot'), 'formal_parallel', 'worker', '--assignment', str(assignment)], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT))
                paths.append(wd/'generated.jsonl')
            last = 0
            while True:
                budget.check()
                merge_ready(output, paths, rows, old)
                if len(old) != last:
                    print(f'{round_name}/{split} parallel workers=2 candidates={len(old)}/{len(rows)*4}', flush=True)
                    progress(f'generate_{round_name}_{split}', workers=2, questions_complete=len(old)//4, questions_expected=len(rows), candidates=len(old), worker_pids=[p.pid for p in children])
                    budget.charge(f'candidates_{len(old)}'); last = len(old)
                codes = [p.poll() for p in children]
                if any(c is not None and c != 0 for c in codes): raise RuntimeError(f'Parallel worker failed: {codes}; inspect {directory}')
                if all(c is not None for c in codes): break
                time.sleep(1)
            merge_ready(output, paths, rows, old)
            assert len(old) == len(rows)*4
            assert output.read_bytes()[:len(initial_bytes)] == initial_bytes, 'Saved output prefix changed'
            budget.charge('parallel_finalization')
            write_json(directory/'complete.json', {'ended_at': utc(), 'candidates': len(old), 'initial_candidates_preserved_byte_for_byte': True,
                'workers_exit_codes': [p.returncode for p in children]})
        finally:
            for child in children:
                if child.poll() is None: child.terminate()
            for child in children:
                try: child.wait(timeout=15)
                except subprocess.TimeoutExpired: child.kill(); child.wait()
            for log in logs: log.close()
            for sig, handler in previous_handlers.items(): signal.signal(sig, handler)


def main():
    p = argparse.ArgumentParser(); sub = p.add_subparsers(dest='command', required=True)
    g = sub.add_parser('generate'); g.add_argument('--round', dest='round_name', choices=['G0', 'G1', 'G2'], required=True); g.add_argument('--split', choices=['A', 'B', 'C'], required=True)
    w = sub.add_parser('worker'); w.add_argument('--assignment', required=True)
    a = p.parse_args()
    if a.command == 'worker': worker(a.assignment)
    else: generate(a.round_name, a.split)


if __name__ == '__main__': main()
