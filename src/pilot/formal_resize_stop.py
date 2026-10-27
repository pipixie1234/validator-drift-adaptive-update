"""User-authorized, durable handover before stopping this instance to resize GPUs.

Does not shut down the host. Does not change the frozen inference/training code.
"""
import argparse
import fcntl
import hashlib
import json
import os
import signal
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from .core import ROOT, read_jsonl, recover_jsonl, write_json, append_jsonl
from .formal_common import RUN, REPORT, output_path, inputs, sha, utc, preflight
from .formal_parallel import merge_ready, partition
from .formal_revision import verify_execution_revision
from .runtime import model_identity


def process_alive(pid):
    path = Path(f'/proc/{pid}/stat')
    return path.exists() and path.read_text().split()[2] != 'Z'


def command(pid):
    return Path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\0', b' ').decode()


def terminate_stopped(pid):
    if process_alive(pid):
        os.kill(pid, signal.SIGTERM)
        os.kill(pid, signal.SIGCONT)
    until = time.monotonic()+20
    while process_alive(pid) and time.monotonic() < until: time.sleep(.1)
    if process_alive(pid): raise RuntimeError(f'Process did not exit cleanly: {pid}')


def verify_saved():
    pointer = json.loads((RUN/'hardware_resize/latest.json').read_text())
    path = ROOT/pointer['report_path']
    assert sha(path) == pointer['report_sha256']
    report = json.loads(path.read_text())
    for record in report['saved_files']:
        assert sha(ROOT/record['path']) == record['sha256'], f"Saved file changed: {record['path']}"
    verify_execution_revision(); preflight()
    assert model_identity('G', RUN/'checkpoints/G1_SFT')[1] == report['G1_adapter_fingerprint']
    return report


def prepare():
    verify_execution_revision(); preflight()
    state = json.loads((RUN/'state.json').read_text())
    assert state['status'] == 'running' and state['stage'].startswith('generate_'), 'Use only during a G generation stage'
    _, round_name, split = state['stage'].split('_')
    current = json.loads((RUN/'current_child.json').read_text())
    controller_pid, coordinator_pid = state['pid'], current['pid']
    assert 'pilot.formal_pipeline' in command(controller_pid)
    assert 'pilot.formal_parallel generate' in command(coordinator_pid)
    attempts = [p for p in (RUN/'parallel'/f'{round_name}_{split}').glob('attempt_*')
                if json.loads((p/'coordinator.json').read_text())['pid'] == coordinator_pid]
    assert len(attempts) == 1
    attempt = attempts[0]
    child_pids = [int(x) for x in Path(f'/proc/{coordinator_pid}/task/{coordinator_pid}/children').read_text().split()]
    workers = []
    for pid in child_pids:
        if not process_alive(pid): continue
        cmd = command(pid)
        assert 'pilot.formal_parallel worker' in cmd
        matching = [p.parent for p in attempt.glob('worker_*/assignment.json') if str(p) in cmd]
        assert len(matching) == 1
        workers.append((pid, matching[0]))
    assert workers
    directory = RUN/'hardware_resize'
    directory.mkdir(exist_ok=True)
    if (directory/'latest.json').exists(): raise RuntimeError('An existing resize handover is already present; audit it before another stop')
    stopped = []; retirement_started = False
    try:
        # Prevent a finished stage from scheduling another one. Freeze writers before merging.
        for pid in [controller_pid, coordinator_pid]+[p for p, _ in workers]:
            if process_alive(pid): os.kill(pid, signal.SIGSTOP); stopped.append(pid)
        print('Controller and generation writers held; consolidating saved worker outputs.', flush=True)
        for pid, wd in workers:
            path = wd/'generated.jsonl'
            # If stopped between the four appends of one question, let that question finish saving.
            until = time.monotonic()+120
            while True:
                raw = path.read_bytes() if path.exists() else b''
                complete = raw.endswith(b'\n') and len(raw.splitlines()) % 4 == 0 if raw else True
                if complete:
                    try: rows = [json.loads(x) for x in raw.splitlines()]
                    except (json.JSONDecodeError, UnicodeDecodeError): complete = False
                if complete: break
                if time.monotonic() > until: raise RuntimeError('Worker did not reach a durable question boundary; original processes will be resumed')
                os.kill(pid, signal.SIGCONT); time.sleep(.02); os.kill(pid, signal.SIGSTOP)
            assert all(sum(r['question_id'] == q for r in rows) == 4 for q in {r['question_id'] for r in rows})
        output = output_path(round_name, split)
        before = output.read_bytes() if output.exists() else b''
        # Preserve the exact pre-consolidation bytes, including evidence of any interrupted last write.
        (directory/'canonical_before_consolidation.jsonl').write_bytes(before)
        rows = recover_jsonl(output)
        count_before = len(rows)
        paths = sorted(attempt.glob('worker_*/generated.jsonl'))
        merge_ready(output, paths, inputs(split), rows)
        partition(inputs(split), rows)  # validate unique candidate IDs and authorized question membership
        assert len(rows) % 4 == 0
        write_json(directory/'held_processes.json', {'held_at': utc(), 'controller': controller_pid,
            'coordinator': coordinator_pid, 'workers': [p for p, _ in workers], 'attempt': str(attempt.relative_to(ROOT))})
        retirement_started = True
        for pid, _ in workers: terminate_stopped(pid)
        terminate_stopped(coordinator_pid)
        terminate_stopped(controller_pid)
        # Include the final unsaved wall-clock interval once; no offline time is charged.
        ledger = recover_jsonl(RUN/'work_ledger.jsonl')
        residual = max(0, time.time()-datetime.fromisoformat(ledger[-1]['recorded_at']).timestamp()) if ledger else 0
        append_jsonl(RUN/'work_ledger.jsonl', {'stage': 'user_gpu_resize_handover', 'unit': 'final_single_GPU_wall_interval',
            'seconds': residual, 'recorded_at': utc(), 'pid': os.getpid()})
        for name in ['.controller.lock', '.gpu.lock']:
            with (RUN/name).open('a+') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(lock, fcntl.LOCK_UN)
        verify_execution_revision(); preflight()
        saved = set(RUN.glob('G[012]_[ABC].jsonl')) | set(RUN.glob('V0_G[012]_[ABC].jsonl'))
        saved |= set(RUN.glob('*.manifest.json')) | set((RUN/'checkpoints/G1_SFT').glob('*'))
        for name in ['train/G1/report.json', 'train/G1/selection.manifest.json', 'train/G1/selected.jsonl',
                     'train/G1/tokenized.jsonl', 'work_ledger.jsonl', 'source_code_sha256.json']:
            saved.add(RUN/name)
        records = [{'path': str(p.relative_to(ROOT)), 'bytes': p.stat().st_size, 'sha256': sha(p)} for p in sorted(saved) if p.is_file()]
        report = {'stopped_at_UTC': utc(), 'stopped_at_Asia_Shanghai': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
            'user_request': '当前实例停机扩容，磁盘保留', 'safe_to_power_off_instance': True, 'host_shutdown_issued': False,
            'state_before_stop': state, 'resume_stage': state['stage'], 'candidates_before_consolidation': count_before,
            'candidates_after_consolidation': len(rows), 'questions_complete': len(rows)//4,
            'all_controller_and_GPU_worker_processes_stopped': True, 'controller_and_GPU_locks_released': True,
            'uncommitted_inflight_questions_may_restart': True, 'previously_saved_answers_replaced': False,
            'G1_training_completed': True, 'G1_adapter_fingerprint': model_identity('G', RUN/'checkpoints/G1_SFT')[1],
            'V0_fixed': True, 'T_inferred': False, 'frozen_protocol_and_revision_audits_passed': True,
            'saved_files': records, 'next_action': 'After GPU resize, verify saved files, configure physical GPU scheduling and GPU-hour accounting in a new execution revision, then restart tmux controller.'}
        report_path = directory/'stop_manifest.json'
        write_json(report_path, report)
        write_json(directory/'latest.json', {'report_path': str(report_path.relative_to(ROOT)), 'report_sha256': sha(report_path)})
        write_json(RUN/'state.json', {**state, 'status': 'stopped_for_user_gpu_resize', 'pid': None,
            'previous_controller_pid': controller_pid, 'updated_at': utc(), 'safe_to_power_off_instance': True,
            'handover_manifest': str(report_path.relative_to(ROOT))})
        old_progress = json.loads((RUN/'progress.json').read_text())
        write_json(RUN/'progress.json', {**old_progress, 'pid': None, 'workers': 0, 'worker_pids': [],
            'questions_complete': len(rows)//4, 'candidates': len(rows), 'updated_at': utc(), 'status': 'stopped_for_user_gpu_resize'})
        write_json(RUN/'current_child.json', {'pid': None, 'previous_pid': coordinator_pid, 'stage': state['stage'], 'status': 'stopped_for_user_gpu_resize'})
        write_json(ROOT/'configs/stage_status.json', {'phase': 'stopped_for_user_gpu_resize', 'resume_stage': state['stage'], 'T_inferred': False})
        cp = json.loads((ROOT/'configs/current_protocol.json').read_text()); cp['formal_training'] = 'stopped_for_user_gpu_resize'; write_json(ROOT/'configs/current_protocol.json', cp)
        (REPORT/'RESUME_AFTER_GPU_RESIZE.md').write_text(f'''# 当前实例 GPU 扩容交接

停机准备时间：{report['stopped_at_Asia_Shanghai']}。

已经停止全部实验控制器及GPU工作进程，释放运行锁；服务器关机由用户在平台操作。磁盘保留由用户确认。G1训练已经完成，当前恢复点是{state['stage']}，有效缓存{len(rows)//4}题、{len(rows)}条候选。已完成分片已汇入正式结果；已保存回答不重做，尚未落盘的进行中题目可以重新计算。G0的A/B/C及V0评分、G1 adapter和训练数据完整保留。T封存。

开机后先确认`/root/workspace`保留，在项目目录运行：

```bash
cd /root/workspace/cross_round_pilot
nvidia-smi
scripts/pilot formal_resize_stop verify
scripts/pilot formal_status
```

验证脚本只读核对停止时缓存及G1文件哈希、原协议与执行修订，不启动GPU。硬件扩容后需要配置每个进程所用物理卡，并按实际卡数累计GPU小时；现有双进程程序仍仅使用一张卡。先让助手识别新硬件、测试并记录新的执行修订，再在tmux启动完整流程。tmux不会跨服务器重启保留进程。

原始采样、每题4候选、长度、固定V0提示/评分/0.5阈值和训练方案保持冻结。不要删除runs、修改原始代码冻结清单或从头训练G1。停机清单为`runs/formal/hardware_resize/stop_manifest.json`，包含恢复所需缓存和检查点哈希。
''')
        verified = verify_saved()
        os.sync()
        print(json.dumps({'safe_to_power_off_instance': True, 'resume_stage': state['stage'],
            'questions_complete': verified['questions_complete'], 'candidates_preserved': len(rows),
            'saved_files_verified': len(records), 'manifest': str(report_path)}, ensure_ascii=False), flush=True)
    except BaseException:
        if not retirement_started:
            for pid in reversed(stopped):
                if process_alive(pid): os.kill(pid, signal.SIGCONT)
        raise


def main():
    p = argparse.ArgumentParser(); p.add_argument('action', choices=['prepare', 'verify']); a = p.parse_args()
    if a.action == 'prepare': prepare()
    else:
        r = verify_saved()
        print(json.dumps({'saved_files_verified': len(r['saved_files']), 'resume_stage': r['resume_stage'], 'G1_checkpoint_verified': True, 'T_inferred': False}, ensure_ascii=False), flush=True)


if __name__ == '__main__': main()
