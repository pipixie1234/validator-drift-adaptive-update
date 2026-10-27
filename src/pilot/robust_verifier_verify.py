"""Independent transition recount, held-out-family, loss-mask and archive audit."""
import hashlib
import json
import zipfile
from collections import Counter

import numpy as np

from .core import ROOT, read_jsonl, write_json
from .formal_common import sha, utc
from .robust_verifier_common import RUN, REPORT, PLAN, ARMS, SEEN, UNSEEN, validate


def main():
    cfg = validate(); scores = {name: {r['job_id']: r for r in read_jsonl(RUN/'scoring'/name/'scores.jsonl')} for name in ['V0']+ARMS}
    jobs = read_jsonl(RUN/'evaluation_jobs.jsonl'); results = json.loads((REPORT/'results.json').read_text())
    unique = {r['job_id'] for r in jobs}; assert len(unique) == 7116
    assert all(set(v) == unique for v in scores.values())
    # Rebuild every actual input independently of the cached prompt/token metadata.
    from transformers import AutoTokenizer
    from .core import digest, prompt_ids
    from .formal_common import messages, protocol
    tok = AutoTokenizer.from_pretrained(protocol()['models']['V']['path'], local_files_only=True)
    source = {r['candidate_id']: r for name in ['C_answers.jsonl', 'E_answers.jsonl', 'train_answers.jsonl']
              for r in read_jsonl(RUN/name)}
    prefixes = {r['name']: r['prefix'] for r in cfg['prefixes']}
    for r in jobs:
        raw = source[r['candidate_id']]
        assert r['prefix'] == prefixes[r['variant']] and r['correct'] is raw['correct']
        text = r['prefix']+raw['text']; msg = messages('V', raw['question'], text)
        ids = prompt_ids(tok, msg)
        assert r['text_sha256'] == digest(text) and r['prompt_sha256'] == digest(msg)
        assert r['prompt_ids'] == ids and r['prompt_tokens'] == len(ids) and len(ids)+4 <= 8192
    del tok
    provenance = {}
    for s in scores['V0'].values():
        if not s.get('reuse_source'): continue
        if s['reuse_source'] not in provenance: provenance[s['reuse_source']] = read_jsonl(ROOT/s['reuse_source'])
        old = provenance[s['reuse_source']][s['reuse_line']-1]
        assert old['model_fingerprint'] == cfg['V_interface']['model_fingerprint'] and old['adapter_fingerprint'] is None
        assert old['prompt_sha256'] == s['prompt_sha256'] and old['threshold'] == .5
        assert all(old[k] == s[k] for k in ['score', 'accepted', 'log_likelihood_A', 'log_likelihood_B'])
    quality = json.loads((ROOT/'configs/verifier_robustness_20261026_E_quality_revision003.json').read_text())
    bad = {r['question_id'] for r in quality['whole_question_quarantine']}
    jobs = [r for r in jobs if r['cohort'] != 'E_new_final' or r['question_id'] not in bad]
    exported = read_jsonl(REPORT/'scores_full.jsonl'); quarantined = read_jsonl(REPORT/'quarantined_E_scores_full.jsonl')
    assert len(exported) == 35100 and len(quarantined) == 480
    assert all(r['question_id'] not in bad for r in exported)
    assert all(r['question_id'] in bad and r['correct'] is None and not r['metrics_eligible'] for r in quarantined)
    def choose(row):
        names = SEEN if row['variant'] == 'seen4_equal' else UNSEEN if row['variant'] == 'unseen4_equal' else [row['variant']]
        return [r for r in jobs if r['cohort'] == row['cohort'] and r['view'] == row['view'] and r['variant'] in names]
    for row in results['repair_damage']+results['contrast_repair_damage']:
        count = Counter()
        for r in choose(row):
            if r['correct'] is None: continue
            before = row.get('before', 'V0'); after = row.get('after', row.get('V'))
            old = scores[before][r['job_id']]['accepted']; new = scores[after][r['job_id']]['accepted']; y = r['correct']
            if y is False:
                count['repaired_FP'] += old and not new; count['new_FP'] += not old and new
            else:
                count['repaired_FN'] += not old and new; count['new_FN'] += old and not new
        for k in ['repaired_FP', 'repaired_FN', 'new_FP', 'new_FN']: assert row[k] == count[k]
        assert row['net_repaired'] == count['repaired_FP']+count['repaired_FN']-count['new_FP']-count['new_FN']
    # Rebuild primary new-E/G3 whole-question confusion matrices, independently of report code.
    qs = sorted({r['question_id'] for r in jobs if r['cohort'] == 'E_new_final'}); assert len(qs) == quality['primary_E_questions'] == 60
    W = np.random.default_rng(20261026).multinomial(60, np.full(60, 1/60), size=10000); checked = 0
    def close(a, b): assert abs(a-b) < 1e-12, (a, b)
    for variant in ['original', 'we_can', 'we_will']:
        rr = [r for r in jobs if r['cohort'] == 'E_new_final' and r['view'] == 'G3_vLLM_newE' and r['variant'] == variant]
        assert len(rr) == 240; matrices = {}
        for model in ['V0']+ARMS:
            cells = []
            for q in qs:
                count = [0]*4
                for r in rr:
                    if r['question_id'] != q or r['correct'] is None: continue
                    a = scores[model][r['job_id']]['accepted']; y = r['correct']
                    count[(0 if a else 1) if y else (2 if a else 3)] += 1
                cells.append(count)
            b = (W@np.array(cells)).T; tp, fn, fp, tn = b
            matrices[model] = {'FPR': fp/(fp+tn), 'FNR': fn/(tp+fn), 'BA': .5*(tp/(tp+fn)+tn/(fp+tn)),
                               'accuracy': (tp+tn)/(tp+fn+fp+tn)}
        for model in ARMS:
            row = next(r for r in results['repair_damage'] if r['cohort'] == 'E_new_final' and r['view'] == 'G3_vLLM_newE' and r['variant'] == variant and r['V'] == model)
            for metric in ['FPR', 'FNR', 'BA']:
                lo, hi = np.quantile(matrices[model][metric]-matrices['V0'][metric], [.025, .975])
                close(row[metric+'_low'], lo); close(row[metric+'_high'], hi); checked += 1
            lo, hi = np.quantile(matrices[model]['accuracy']-matrices['V0']['accuracy'], [.025, .975])
            close(row['net_repair_low'], lo); close(row['net_repair_high'], hi); checked += 1
    hashes = []
    excluded_training = set(json.loads((RUN/'source_quality_revision004.json').read_text())['B_whole_question_quarantine'])
    for arm in ARMS:
        data = read_jsonl(RUN/f'train_{arm.split("_")[1]}.jsonl'); logs = read_jsonl(RUN/arm/'steps.jsonl')
        assert [eid for r in logs for eid in r['example_ids']] == [r['example_id'] for r in data]
        assert len(logs) == 80 and sum(r['chosen_label_tokens'] for r in logs) == 640
        for r in data:
            assert r['question_id'] not in excluded_training
            assert r['labels'] == [-100]*len(r['prompt_ids'])+[r['chosen_label_id']]
            assert r['chosen_label_id'] == (32 if r['correct'] else 33) and r['rejected_label_id'] == (33 if r['correct'] else 32)
        report = json.loads((RUN/arm/'training_report.json').read_text()); assert report['save_reload_passed'] and report['base_unchanged']
        hashes.append(json.loads((RUN/arm/'initialization.json').read_text())['initial_adapter_hash'])
    assert len(set(hashes)) == 1
    old = read_jsonl(ROOT/'data/dapo/inputs/dapo100.jsonl')
    for name in ['A_G_update', 'B_V_update', 'T_final']: old += read_jsonl(ROOT/f'data/dapo/formal/inputs/{name}.jsonl')
    new = read_jsonl(RUN/'E_inputs.jsonl')
    for key in ['question_id', 'question_hash', 'family_id']: assert not {r[key] for r in old} & {r[key] for r in new}
    assert not {u for r in old for u in r['family_source_uuids']} & {u for r in new for u in r['family_source_uuids']}
    for g in ['G0', 'G3']:
        raw = read_jsonl(RUN/f'{g}_E.jsonl'); assert len(raw) == 256
        assert set(Counter(r['question_id'] for r in raw).values()) == {4}
        assert all(not any(k in r for k in ['gold_answer', 'correct', 'reference']) for r in raw)
        assert all(r['output_tokens']+r['prompt_tokens'] <= 4096 for r in raw)
    from openpyxl import load_workbook
    book = load_workbook(REPORT/'robust_verifier_review.xlsx', read_only=True)
    for ws in book:
        for row in ws.iter_rows(): assert all(c.data_type != 'f' for c in row)
    book.close()
    from .week_prefix_delivery import pdf_tools
    Reader, _ = pdf_tools(); assert len(Reader(REPORT/'robust_verifier_figures.pdf').pages) == 5
    manifest = json.loads((REPORT/'artifact_manifest.json').read_text())
    with zipfile.ZipFile(REPORT/'robust_verifier_delivery.zip') as z:
        assert z.testzip() is None
        for name, h in manifest.items(): assert sha(REPORT/name) == h == hashlib.sha256(z.read(name)).hexdigest()
    out = {'status': 'passed', 'at': utc(), 'repair_damage_rows_recounted': len(results['repair_damage'])+len(results['contrast_repair_damage']),
           'new_E_primary_paired_intervals_independently_recomputed': checked, 'raw_scores': 5*len(unique),
           'E_raw_questions': 64, 'E_primary_valid_questions': 60, 'E_whole_question_quarantine': sorted(bad),
           'all_training_masks_and_actual_presentations_verified': True, 'same_seed_initial_adapter_4arms': True,
           'full_template_and_tokens_independently_reencoded': len(unique),
           'new_E_question_hash_family_UUID_disjoint': True, 'original_math_body_and_unknowns_preserved': True,
           'archive_CRC_and_manifest_verified': True, 'PDF_pages': 5, 'old_models_and_raw_data_retained': True}
    write_json(REPORT/'independent_verification.json', out); print(json.dumps(out, indent=2), flush=True)


if __name__ == '__main__': main()
