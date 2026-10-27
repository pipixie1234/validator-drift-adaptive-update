"""Explicit append-only execution revision audit; never replace the launch freeze."""
import json
from .core import ROOT
from .formal_common import RUN, sha

REVISION = ROOT/'configs/execution_revisions/001_two_generator_processes.json'


def verify_execution_revision():
    freeze = RUN/'source_code_sha256.json'
    original = json.loads(freeze.read_text())
    if not REVISION.exists():
        assert all(sha(ROOT/name) == h for name, h in original.items()), 'Formal source changed without a recorded execution revision'
        return None
    revision = json.loads(REVISION.read_text())
    assert revision['original_source_freeze_sha256'] == sha(freeze)
    for name, h in original.items():
        if name in revision['changed_files']:
            change = revision['changed_files'][name]
            assert change['previous_sha256'] == h
            assert sha(ROOT/change['archived_previous_path']) == h
            assert sha(ROOT/name) == change['active_sha256']
        else:
            assert sha(ROOT/name) == h, f'Unapproved source change: {name}'
    for name, h in revision['added_files_sha256'].items():
        assert sha(ROOT/name) == h, f'Parallel implementation changed after freeze: {name}'
    assert sha(ROOT/revision['benchmark_result_path']) == revision['benchmark_result_sha256']
    assert sha(ROOT/revision['handover_path']) == revision['handover_sha256']
    handover = json.loads((ROOT/revision['handover_path']).read_text())
    for item in handover['preserved_outputs']:
        path = ROOT/item['path']
        import hashlib
        with path.open('rb') as stream: prefix = stream.read(item['bytes'])
        assert len(prefix) == item['bytes'] and hashlib.sha256(prefix).hexdigest() == item['sha256'], f'Pre-handover output changed: {path}'
    return revision
