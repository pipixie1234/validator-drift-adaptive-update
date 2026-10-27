import hashlib
import json
import pytest
from pilot.core import write_jsonl, read_jsonl
from pilot.formal_multigpu import layout, child_environment, PhysicalGPUBudget
from pilot.formal_parallel import partition
from pilot.formal_multigpu_revision import apply_revision, verify_files, verify_prefix
from pilot.formal_common import BudgetExceeded


def config():
    return {'generator_processes_per_gpu': 2, 'generator_processes_total': 4,
            'devices': [{'uuid': 'GPU-a', 'physical_index': 0}, {'uuid': 'GPU-b', 'physical_index': 1}]}


def test_four_workers_bind_two_to_each_physical_gpu_without_changing_parent_env(monkeypatch):
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', 'old')
    slots = layout(config())
    assert [child_environment(s)['CUDA_VISIBLE_DEVICES'] for s in slots] == ['GPU-a', 'GPU-a', 'GPU-b', 'GPU-b']
    import os
    assert os.environ['CUDA_VISIBLE_DEVICES'] == 'old'


def test_resume_omits_113_cached_questions_and_distributes_87_pending_questions():
    rows = [{'question_id': f'q{i}'} for i in range(200)]
    cached = [{'question_id': f'q{i}', 'candidate_index': j} for i in range(113) for j in range(4)]
    shards = partition(rows, cached, count=4)
    assert [len(s) for s in shards] == [22, 22, 22, 21]
    assert len(set(q for s in shards for q in s)) == 87
    assert set(q for s in shards for q in s) == {f'q{i}' for i in range(113, 200)}


def test_budget_integrates_unique_cards_and_stops_charging_finished_gpu(tmp_path):
    now = [0.]; path = tmp_path/'ledger.jsonl'
    b = PhysicalGPUBudget('test', ['GPU-a', 'GPU-a', 'GPU-b', 'GPU-b'], clock=lambda: now[0], ledger_path=path, prior_seconds=0, cap_seconds=10000)
    now[0] = 10; b.tick({'GPU-b'}); b.charge('one_card_finished')
    now[0] = 15; b.tick(set()); b.charge('all_finished')
    now[0] = 100; b.tick(set()); b.charge('idle')
    records = read_jsonl(path)
    assert b.total == sum(r['seconds'] for r in records) == 25
    assert records[0]['gpu_seconds_by_uuid'] == {'GPU-a': 10, 'GPU-b': 10}
    assert records[1]['gpu_seconds_by_uuid'] == {'GPU-b': 5}
    assert records[-1]['seconds'] == 0


def test_multigpu_budget_cap_includes_prior_and_existing_work(tmp_path):
    path = tmp_path/'ledger'; write_jsonl(path, [{'seconds': 100}]); now = [0.]
    b = PhysicalGPUBudget('test', ['a', 'b'], clock=lambda: now[0], ledger_path=path, prior_seconds=20, cap_seconds=370)
    now[0] = 10; b.tick({'a', 'b'})
    with pytest.raises(BudgetExceeded): b.check()


def test_revision_chain_rejects_archive_tampering_and_unrecorded_active_changes(tmp_path):
    h = lambda value: hashlib.sha256(value).hexdigest()
    (tmp_path/'old').write_bytes(b'old'); (tmp_path/'active').write_bytes(b'new')
    revision = {'changed_files': {'active': {'previous_sha256': h(b'old'), 'archived_previous_path': 'old', 'active_sha256': h(b'new')}}, 'added_files_sha256': {}}
    expected = apply_revision({'active': h(b'old')}, revision, root=tmp_path); verify_files(expected, root=tmp_path)
    (tmp_path/'active').write_bytes(b'tampered')
    with pytest.raises(AssertionError, match='Active source'): verify_files(expected, root=tmp_path)
    (tmp_path/'old').write_bytes(b'tampered')
    with pytest.raises(AssertionError, match='Archived source'): apply_revision({'active': h(b'old')}, revision, root=tmp_path)


def test_preserved_prefix_accepts_append_and_rejects_overwrite(tmp_path):
    p = tmp_path/'cache'; p.write_bytes(b'old\nnew\n')
    h = hashlib.sha256(b'old\n').hexdigest()
    verify_prefix(p, 4, h)
    p.write_bytes(b'bad\nnew\n')
    with pytest.raises(AssertionError): verify_prefix(p, 4, h)
