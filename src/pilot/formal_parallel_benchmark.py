"""Shadow eight already cached C questions while the original A job stays running."""
import json
import subprocess
import time
from datetime import datetime
from .core import ROOT, read_jsonl, write_json
from .formal_common import RUN, inputs, sha, utc


def main():
    directory = RUN/'parallel_benchmark'
    directory.mkdir(exist_ok=True)
    qids = [r['question_id'] for r in inputs('C')[:8]]
    old = [r for r in read_jsonl(RUN/'G0_C.jsonl') if r['question_id'] in qids]
    assert len(old) == 32
    assignment = directory/'assignment.json'
    write_json(assignment, {'round': 'G0', 'split': 'C', 'question_ids': qids, 'directory': str(directory/'shadow'),
        'original_runner_sha256': sha(ROOT/'src/pilot/formal_run.py'), 'wrapper_sha256': sha(ROOT/'src/pilot/formal_parallel.py')})
    started = utc(); tick = time.monotonic()
    write_json(directory/'started.json', {'started_at': started, 'original_A_continues': True, 'cached_C_not_modified': True,
        'C_sha256': sha(RUN/'G0_C.jsonl'), 'questions': qids, 'budget': 'Overlaps original single GPU stage; existing stage ledger already charges this elapsed GPU wall time once.'})
    with (directory/'shadow.log').open('ab') as log:
        child = subprocess.Popen([str(ROOT/'scripts/pilot'), 'formal_parallel', 'worker', '--assignment', str(assignment)], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        child.wait()
    assert child.returncode == 0
    new = read_jsonl(directory/'shadow/generated.jsonl'); assert len(new) == 32
    by_id = {r['candidate_id']: r for r in old}
    equal = sum(r['text'] == by_id[r['candidate_id']]['text'] and r['output_tokens'] == by_id[r['candidate_id']]['output_tokens'] and r['truncated'] == by_id[r['candidate_id']]['truncated'] for r in new)
    old_s = sum(r['generation_seconds_for_question'] for r in old if r['candidate_index'] == 0)
    new_s = sum(r['generation_seconds_for_question'] for r in new if r['candidate_index'] == 0)
    # Main A questions fully contained in the shadow generation window.
    first_end = min(datetime.fromisoformat(r['recorded_at']).timestamp() for r in new)
    first_q = next(r for r in new if r['candidate_index'] == 0)
    begin = first_end-first_q['generation_seconds_for_question']
    end = max(datetime.fromisoformat(r['recorded_at']).timestamp() for r in new)
    main = []
    for r in read_jsonl(RUN/'G0_A.jsonl'):
        finished = datetime.fromisoformat(r['recorded_at']).timestamp()
        if finished <= end and finished-r['generation_seconds_for_question'] >= begin: main.append(r)
    tokens = sum(r['output_tokens'] for r in new+main)
    result = {'completed_at': utc(), 'wall_seconds_with_load': time.monotonic()-tick,
        'matched_questions': 8, 'matched_candidates': 32, 'exact_text_length_truncation_matches': equal,
        'original_single_process_compute_seconds': old_s, 'shadow_concurrent_compute_seconds': new_s,
        'shadow_latency_ratio_vs_original': new_s/old_s, 'shadow_output_tokens': sum(r['output_tokens'] for r in new),
        'original_matched_tokens_per_second': sum(r['output_tokens'] for r in old)/old_s,
        'concurrent_window_seconds': end-begin, 'fully_contained_main_candidates': len(main),
        'concurrent_aggregate_completed_tokens_per_second_lower_bound': tokens/(end-begin),
        'cached_C_sha256_unchanged': sha(RUN/'G0_C.jsonl') == json.loads((directory/'started.json').read_text())['C_sha256'],
        'formal_outputs_replaced': False,
        'limitation': 'Same cached C questions match exact outputs; concurrent aggregate excludes main questions crossing either boundary. Speed is diagnostic, not a guarantee for later adapters.'}
    write_json(directory/'result.json', result)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__': main()
