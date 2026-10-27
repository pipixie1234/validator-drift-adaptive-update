"""Two GPU UUIDs, two independent original-sampler workers per GPU."""
import argparse
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from .core import ROOT, read_jsonl, recover_jsonl, write_json, ensure_run_manifest
from .formal_common import RUN, PLAN, PROTOCOL, inputs, input_path, output_path, adapter_path, messages, protocol, plan, verify_fixed, gpu_lock, progress, BudgetExceeded, sha, utc
from .formal_parallel import partition, merge_ready
from .runtime import model_identity

CONFIG = ROOT/'configs/formal_multigpu.json'


def layout(config):
    assert config['generator_processes_per_gpu'] == 2
    devices = config['devices']
    assert len(devices) == 2 and len({d['uuid'] for d in devices}) == 2
    slots = [dict(d, local_worker_index=i) for d in devices for i in range(2)]
    assert len(slots) == config['generator_processes_total'] == 4
    return slots


def checked_layout():
    config = json.loads(CONFIG.read_text()); slots = layout(config)
    raw = subprocess.check_output(['nvidia-smi', '--query-gpu=index,uuid,name', '--format=csv,noheader'], text=True)
    found = {parts[1].strip(): (int(parts[0]), parts[2].strip()) for parts in [line.split(',', 2) for line in raw.strip().splitlines()]}
    for slot in slots:
        assert found[slot['uuid']] == (slot['physical_index'], slot['name']), 'GPU inventory differs from the recorded resize; audit before resuming'
    return slots


def child_environment(slot):
    env = os.environ.copy()
    env['CUDA_VISIBLE_DEVICES'] = slot['uuid']
    env['CUDA_DEVICE_ORDER'] = 'PCI_BUS_ID'
    return env


class PhysicalGPUBudget:
    """Integrate unique active GPUs, not worker counts or just one wall clock."""
    def __init__(self, stage, devices, clock=None, ledger_path=None, prior_seconds=None, cap_seconds=None):
        self.clock = clock or time.monotonic
        self.ledger_path = ledger_path or RUN/'work_ledger.jsonl'
        self.stage = stage; self.last = self.clock()
        self.active = set(devices); self.total = 0.; self.pending = {}; self.wall = 0.
        self.recorded = sum(r['seconds'] for r in read_jsonl(self.ledger_path))
        self.prior = plan()['prior_work_reserve_hours']*3600 if prior_seconds is None else prior_seconds
        self.cap = plan()['global_gpu_work_cap_hours']*3600 if cap_seconds is None else cap_seconds
        self.check()

    def tick(self, devices):
        now = self.clock(); elapsed = now-self.last; self.last = now
        assert elapsed >= 0
        self.wall += elapsed
        for device in self.active: self.pending[device] = self.pending.get(device, 0.)+elapsed
        self.total += elapsed*len(self.active)
        self.active = set(devices)

    def check(self):
        # Reserve one next work unit per active physical GPU.
        if self.prior+self.recorded+self.total+120*max(1, len(self.active)) > self.cap:
            raise BudgetExceeded('40h work budget reached; saved outputs/checkpoints retained; aggregate physical GPU time counted')

    def charge(self, unit):
        from .core import append_jsonl
        seconds = sum(self.pending.values())
        if self.wall or seconds:
            append_jsonl(self.ledger_path, {'stage': self.stage, 'unit': unit, 'seconds': seconds,
                'elapsed_wall_seconds': self.wall, 'gpu_seconds_by_uuid': self.pending,
                'accounting': 'sum unique active physical GPU elapsed time; two workers on one GPU count once',
                'recorded_at': utc(), 'pid': os.getpid()})
        self.pending = {}; self.wall = 0.


def start_workers(directory, round_name, split, shards, slots):
    children = []; logs = []
    try:
        for rank, (qids, slot) in enumerate(zip(shards, slots)):
            if not qids: continue
            wd = directory/f'worker_{rank}'; wd.mkdir()
            assignment = wd/'assignment.json'
            write_json(assignment, {'round': round_name, 'split': split, 'question_ids': qids, 'directory': str(wd),
                'original_runner_sha256': sha(ROOT/'src/pilot/formal_run.py'),
                'wrapper_sha256': sha(ROOT/'src/pilot/formal_parallel.py'),
                'physical_gpu_uuid': slot['uuid'], 'physical_gpu_index': slot['physical_index'],
                'local_worker_index': slot['local_worker_index'], 'multigpu_coordinator_sha256': sha(Path(__file__))})
            log = (wd/'worker.log').open('ab'); logs.append(log)
            child = subprocess.Popen([str(ROOT/'scripts/pilot'), 'formal_parallel', 'worker', '--assignment', str(assignment)],
                cwd=ROOT, env=child_environment(slot), stdout=log, stderr=subprocess.STDOUT)
            children.append({'process': child, 'uuid': slot['uuid'], 'rank': rank, 'directory': wd})
        return children, logs
    except BaseException:
        finish_workers(children, logs)
        raise


def finish_workers(children, logs):
    for item in children:
        if item['process'].poll() is None: item['process'].terminate()
    for item in children:
        child = item['process']
        try: child.wait(timeout=15)
        except subprocess.TimeoutExpired: child.kill(); child.wait()
    for log in logs: log.close()


def active_devices(children):
    return {item['uuid'] for item in children if item['process'].poll() is None}


def descriptor(round_name, split, rows):
    base, ah = model_identity('G', adapter_path(round_name)); p = protocol()
    return {'role': 'G', 'G_round': round_name, 'split': split,
        'source_sha256': sha(input_path(split)), 'effective_question_ids': [r['question_id'] for r in rows],
        'model_fingerprint': base, 'adapter_fingerprint': ah, 'settings': p['sampling'],
        'prompt_templates': messages('G', '{question}', '{response}'), 'output_budget_rule': p['G_output_budget_rule'],
        'protocol_sha256': sha(PROTOCOL), 'execution_sha256': sha(PLAN),
        'runner_sha256': sha(ROOT/'src/pilot/formal_run.py'), 'common_sha256': sha(ROOT/'src/pilot/formal_common.py'),
        'fixed_scoring_sha256': sha(ROOT/'src/pilot/runtime.py')}


def generate(round_name, split):
    with gpu_lock():
        verify_fixed(); rows = inputs(split); output = output_path(round_name, split)
        old = recover_jsonl(output); initial_bytes = output.read_bytes() if output.exists() else b''
        previous = RUN/'parallel'/f'{round_name}_{split}'
        for attempt in sorted(previous.glob('attempt_*')):
            marker = json.loads((attempt/'coordinator.json').read_text())
            assert marker['original_runner_sha256'] == sha(ROOT/'src/pilot/formal_run.py')
            merge_ready(output, sorted(attempt.glob('worker_*/generated.jsonl')), rows, old)
        shards = partition(rows, old, count=4)
        if not any(shards):
            print(f'Cached multiGPU G/{round_name}/{split}: {len(old)}', flush=True); return
        slots = checked_layout()
        ensure_run_manifest(output.with_suffix('.manifest.json'), descriptor(round_name, split, rows))
        directory = previous/f'attempt_{time.time_ns()}'; directory.mkdir(parents=True)
        write_json(directory/'coordinator.json', {'pid': os.getpid(), 'started_at': utc(), 'workers': 4, 'physical_GPUs': 2,
            'initial_candidates': len(old), 'initial_output_bytes': len(initial_bytes),
            'initial_output_prefix_sha256': __import__('hashlib').sha256(initial_bytes).hexdigest(),
            'original_runner_sha256': sha(ROOT/'src/pilot/formal_run.py'),
            'wrapper_sha256': sha(ROOT/'src/pilot/formal_parallel.py'), 'coordinator_sha256': sha(Path(__file__)),
            'hardware_config_sha256': sha(CONFIG), 'worker_slots': slots, 'shards': shards,
            'sampling_and_per_question_seed_unchanged': True, 'worker_outputs_are_separate': True,
            'budget_accounting': 'Each active physical GPU counted once per elapsed second, independent of worker count.'})
        devices = {slot['uuid'] for slot, qids in zip(slots, shards) if qids}
        budget = PhysicalGPUBudget(f'G_2GPU_4process/{round_name}/{split}', devices)
        children, logs = [], []
        def stop(signum, frame): raise SystemExit(128+signum)
        handlers = {s: signal.signal(s, stop) for s in [signal.SIGTERM, signal.SIGINT]}
        started = time.monotonic(); last_charge = started; last_count = -1
        try:
            children, logs = start_workers(directory, round_name, split, shards, slots)
            paths = [item['directory']/'generated.jsonl' for item in children]
            while True:
                budget.tick(active_devices(children)); budget.check()
                merge_ready(output, paths, rows, old)
                if len(old) != last_count:
                    print(f'{round_name}/{split} GPUs=2 workers=4 candidates={len(old)}/{len(rows)*4}', flush=True)
                    progress(f'generate_{round_name}_{split}', workers=len(children), physical_GPUs=2,
                        questions_complete=len(old)//4, questions_expected=len(rows), candidates=len(old),
                        worker_pids=[item['process'].pid for item in children],
                        worker_gpu_uuids={str(item['process'].pid): item['uuid'] for item in children})
                    last_count = len(old)
                if time.monotonic()-last_charge >= 10:
                    budget.charge(f'candidates_{len(old)}'); last_charge = time.monotonic()
                codes = [item['process'].poll() for item in children]
                if any(code is not None and code != 0 for code in codes): raise RuntimeError(f'MultiGPU worker failed {codes}; inspect {directory}')
                if all(code is not None for code in codes): break
                time.sleep(1)
            merge_ready(output, paths, rows, old)
            assert len(old) == len(rows)*4
            assert output.read_bytes()[:len(initial_bytes)] == initial_bytes, 'Saved output prefix changed'
            write_json(directory/'complete.json', {'ended_at': utc(), 'candidates': len(old),
                'initial_candidates_preserved_byte_for_byte': True, 'workers_exit_codes': [item['process'].returncode for item in children],
                'occupied_GPU_seconds': budget.total, 'elapsed_wall_seconds': time.monotonic()-started})
        finally:
            finish_workers(children, logs)
            budget.tick(set()); budget.charge('multiGPU_finalization_or_stop')
            for sig, handler in handlers.items(): signal.signal(sig, handler)


def smoke():
    """Four cached G1/B questions, separate output, real LoRA on both physical GPUs."""
    with gpu_lock():
        verify_fixed(); slots = checked_layout()
        old = read_jsonl(RUN/'G1_B.jsonl'); available = {r['question_id'] for r in old}
        qids = [r['question_id'] for r in inputs('B') if r['question_id'] in available][:4]
        assert len(qids) == 4
        directory = RUN/'multigpu_smoke'; directory.mkdir()
        write_json(directory/'started.json', {'started_at': utc(), 'question_ids': qids, 'formal_output_before_sha256': sha(RUN/'G1_B.jsonl'),
            'slots': slots, 'original_runner_sha256': sha(ROOT/'src/pilot/formal_run.py'), 'coordinator_sha256': sha(Path(__file__))})
        budget = PhysicalGPUBudget('2GPU_4process_smoke_G1', {s['uuid'] for s in slots})
        children, logs = [], []; last = time.monotonic()
        try:
            children, logs = start_workers(directory, 'G1', 'B', [[q] for q in qids], slots)
            while True:
                budget.tick(active_devices(children)); budget.check()
                codes = [item['process'].poll() for item in children]
                if any(c is not None and c != 0 for c in codes): raise RuntimeError(f'Smoke failed: {codes}')
                if time.monotonic()-last >= 10:
                    budget.charge('smoke_running'); last = time.monotonic()
                if all(c is not None for c in codes): break
                time.sleep(1)
            new = [r for item in children for r in read_jsonl(item['directory']/'generated.jsonl')]
            original = {r['candidate_id']: r for r in old}; assert len(new) == 16
            equal = sum(all(r[k] == original[r['candidate_id']][k] for k in ['text', 'output_tokens', 'truncated', 'generation_seed', 'cache_key']) for r in new)
            assert sha(RUN/'G1_B.jsonl') == json.loads((directory/'started.json').read_text())['formal_output_before_sha256']
            result = {'completed_at': utc(), 'workers': 4, 'physical_GPUs': 2, 'candidate_count': 16,
                'exact_text_length_truncation_seed_cache_matches': equal, 'formal_cache_changed': False,
                'worker_exit_codes': [item['process'].returncode for item in children],
                'worker_device_mapping': {str(item['process'].pid): item['uuid'] for item in children},
                'all_G1_adapters_correct': all(r['adapter_fingerprint'] == model_identity('G', adapter_path('G1'))[1] for r in new),
                'output_tokens': sum(r['output_tokens'] for r in new), 'occupied_GPU_seconds': budget.total}
            write_json(directory/'result.json', result)
            print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
            assert equal == 16, 'Inspect hardware-change output drift before mixing any new formal answers'
        finally:
            finish_workers(children, logs); budget.tick(set()); budget.charge('smoke_finalization')


def main():
    p = argparse.ArgumentParser(); sub = p.add_subparsers(dest='command', required=True)
    g = sub.add_parser('generate'); g.add_argument('--round', dest='round_name', choices=['G0', 'G1', 'G2'], required=True); g.add_argument('--split', choices=['A', 'B', 'C'], required=True)
    sub.add_parser('smoke'); a = p.parse_args()
    if a.command == 'smoke': smoke()
    else: generate(a.round_name, a.split)


if __name__ == '__main__': main()
