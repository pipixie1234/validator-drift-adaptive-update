"""Audit launch freeze -> execution001 -> execution002, with immutable ancestry."""
import hashlib
import json
from pathlib import Path
from .core import ROOT
from .formal_common import RUN, sha

REVISION1 = ROOT/'configs/execution_revisions/001_two_generator_processes.json'
REVISION2 = ROOT/'configs/execution_revisions/002_two_gpus_four_generator_processes.json'


def apply_revision(expected, revision, root=ROOT):
    result = dict(expected)
    for name, change in revision['changed_files'].items():
        assert result[name] == change['previous_sha256'], f'Revision ancestry mismatch: {name}'
        assert sha(root/change['archived_previous_path']) == change['previous_sha256'], f'Archived source changed: {name}'
        result[name] = change['active_sha256']
    for name, h in revision['added_files_sha256'].items():
        assert name not in result, f'Added file overwrites existing history: {name}'
        result[name] = h
    return result


def verify_files(expected, root=ROOT):
    for name, h in expected.items():
        assert sha(root/name) == h, f'Active source changed outside the execution revisions: {name}'


def verify_prefix(path, size, h):
    with path.open('rb') as stream: data = stream.read(size)
    assert len(data) == size and hashlib.sha256(data).hexdigest() == h, f'Pre-resize data prefix changed: {path}'


def verify_execution_revision():
    if not REVISION2.exists():
        from .formal_revision import verify_execution_revision as previous
        return previous()
    original_path = RUN/'source_code_sha256.json'
    original = json.loads(original_path.read_text())
    first = json.loads(REVISION1.read_text()); second = json.loads(REVISION2.read_text())
    assert first['original_source_freeze_sha256'] == sha(original_path)
    assert second['previous_revision_path'] == str(REVISION1.relative_to(ROOT))
    assert second['previous_revision_sha256'] == sha(REVISION1)
    assert second['original_source_freeze_sha256'] == sha(original_path)
    expected = apply_revision(apply_revision(original, first), second)
    verify_files(expected)
    assert sha(ROOT/first['benchmark_result_path']) == first['benchmark_result_sha256']
    assert sha(ROOT/first['handover_path']) == first['handover_sha256']
    handover = json.loads((ROOT/first['handover_path']).read_text())
    for record in handover['preserved_outputs']:
        verify_prefix(ROOT/record['path'], record['bytes'], record['sha256'])
    stop_path = ROOT/second['resize_stop_manifest_path']
    assert sha(stop_path) == second['resize_stop_manifest_sha256']
    stop = json.loads(stop_path.read_text())
    for record in stop['saved_files']:
        path = ROOT/record['path']
        if path.suffix == '.jsonl' and path.parent == RUN and (path.name.startswith(('G0_', 'G1_', 'G2_', 'V0_')) or path.name == 'work_ledger.jsonl'):
            verify_prefix(path, record['bytes'], record['sha256'])
        else:
            assert sha(path) == record['sha256'], f'Pre-resize immutable artifact changed: {path}'
    smoke_path = ROOT/second['smoke_result_path']
    assert sha(smoke_path) == second['smoke_result_sha256']
    smoke = json.loads(smoke_path.read_text())
    assert smoke['candidate_count'] == smoke['exact_text_length_truncation_seed_cache_matches'] == 16
    assert smoke['all_G1_adapters_correct'] and not smoke['formal_cache_changed']
    return second


def main():
    revision = verify_execution_revision()
    print(json.dumps({'execution_revision': revision['revision'], 'source_history_verified': True,
        'pre_resize_cache_prefixes_and_G1_checkpoint_verified': True, 'T_inferred': False}, ensure_ascii=False), flush=True)


if __name__ == '__main__': main()
