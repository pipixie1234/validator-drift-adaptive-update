import json
import pytest
from pilot.core import write_jsonl, read_jsonl
from pilot.formal_parallel import partition, merge_ready
from pilot.day3_protocol import select_feedback


def candidates(q):
    return [{'question_id': q, 'candidate_index': i, 'candidate_id': f'{q}:{i}',
             'cache_key': f'{q}-{i}', 'text': 'saved answer', 'prompt_tokens': 10,
             'output_tokens': 20, 'truncated': False} for i in range(4)]


def test_sharding_is_disjoint_and_never_repeats_completed_question():
    rows = [{'question_id': f'q{i}'} for i in range(7)]
    old = candidates('q0')+candidates('q1')[:2]
    a, b = partition(rows, old)
    assert set(a).isdisjoint(b)
    assert set(a+b) == {f'q{i}' for i in range(1, 7)}
    with pytest.raises(AssertionError): partition(rows, old+[old[0]])


def test_single_writer_preserves_prefix_ignores_live_torn_record_and_resumes(tmp_path):
    output = tmp_path/'canonical.jsonl'; w0 = tmp_path/'w0.jsonl'; w1 = tmp_path/'w1.jsonl'
    initial = candidates('q0'); write_jsonl(output, initial); prefix = output.read_bytes()
    write_jsonl(w0, candidates('q1')); write_jsonl(w1, candidates('q2')[:3])
    with w1.open('ab') as stream: stream.write(b'{"question_id":')
    before = w1.read_bytes()
    rows = [{'question_id': f'q{i}'} for i in range(3)]
    assert merge_ready(output, [w0, w1], rows, initial) == 4
    assert w1.read_bytes() == before  # reader must not truncate a live worker's last write
    assert output.read_bytes().startswith(prefix)
    assert merge_ready(output, [w0, w1], rows, initial) == 0
    write_jsonl(w1, candidates('q2'))
    assert merge_ready(output, [w0, w1], rows, initial) == 4
    assert len(read_jsonl(output)) == 12
    assert output.read_bytes().startswith(prefix)


def test_duplicate_worker_assignment_is_an_error(tmp_path):
    paths = [tmp_path/'a', tmp_path/'b']
    for p in paths: write_jsonl(p, candidates('q'))
    with pytest.raises(AssertionError, match='multiple workers'):
        merge_ready(tmp_path/'out', paths, [{'question_id': 'q'}], [])


def test_conflicting_recovered_candidate_never_overwrites_saved_answer(tmp_path):
    old = candidates('q'); output = tmp_path/'out'; write_jsonl(output, old); before = output.read_bytes()
    wrong = candidates('q'); wrong[0]['text'] = 'different answer'
    w = tmp_path/'worker'; write_jsonl(w, wrong)
    with pytest.raises(AssertionError, match='Conflicting saved candidate'):
        merge_ready(output, [w], [{'question_id': 'q'}], old)
    assert output.read_bytes() == before


def test_async_completion_order_does_not_change_frozen_selection():
    rows = [r for i in range(550) for r in candidates(f'q{i}')]
    scores = {r['candidate_id']: {'score': .7} for r in rows}
    original = [r['candidate_id'] for r in select_feedback(rows, scores)]
    reordered = [r['candidate_id'] for r in select_feedback(list(reversed(rows)), scores)]
    assert len(original) == 512 and original == reordered
