"""CPU-only, immutable-cache V0/G0-G2 diagnostics with question-cluster inference."""
import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
import numpy as np
from .core import ROOT, read_jsonl, write_jsonl
from .formal_common import RUN, REPORT, PROTOCOL, inputs, sha, utc
from .formal_multigpu_revision import verify_execution_revision

ROUNDS = ['G0', 'G1', 'G2']
SPLITS = ['B', 'C']
COLORS = ['#1976A3', '#D77A21', '#408557']
PRIMARY = ['FNR', 'FPR', 'TPR', 'TNR', 'precision_PPV', 'accepted_error_FDR', 'NPV',
           'V_accuracy', 'balanced_accuracy', 'F1', 'MCC', 'AUROC', 'average_precision',
           'retention_all', 'unknown_fraction', 'G_accuracy_verified', 'Brier_proxy', 'ECE10_proxy']
DELTA = ['FNR', 'FPR', 'balanced_accuracy', 'AUROC', 'precision_PPV', 'accepted_error_FDR',
         'retention_all', 'G_accuracy_verified', 'unknown_fraction']
LABELS = {'FNR': 'False rejection rate (FNR)', 'FPR': 'False acceptance rate (FPR)',
          'precision_PPV': 'Accepted precision (PPV)', 'accepted_error_FDR': 'Accepted error rate (FDR)',
          'balanced_accuracy': 'Balanced accuracy', 'AUROC': 'ROC AUC',
          'G_accuracy_verified': 'G accuracy, verifiable subset', 'retention_all': 'Retention, all outputs'}
LENGTHS = [('<=512', 0, 512), ('513-1024', 513, 1024), ('1025-2048', 1025, 2048), ('>2048', 2049, 100000)]


def divide(a, b):
    a, b = np.broadcast_arrays(np.asarray(a, float), np.asarray(b, float))
    return np.divide(a, b, out=np.full(a.shape, np.nan), where=b != 0)


def clean(value):
    if isinstance(value, dict): return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [clean(v) for v in value]
    if isinstance(value, np.ndarray): return clean(value.tolist())
    if isinstance(value, (np.floating, float)): return float(value) if np.isfinite(value) else None
    if isinstance(value, np.integer): return int(value)
    return value


def save_json(path, value):
    path.write_text(json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def interval(values):
    values = np.asarray(values); values = values[np.isfinite(values)]
    return {'low': float(np.quantile(values, .025)) if len(values) else None,
            'high': float(np.quantile(values, .975)) if len(values) else None, 'valid_replicates': len(values)}


def count_array(rows, question_ids):
    index = {q: i for i, q in enumerate(question_ids)}
    a = np.zeros((len(question_ids), 8), float)
    for r in rows:
        # TP,FN,FP,TN,unknown_accept,unknown_reject,truncated,length_sum
        y, accepted = r['correct'], r['accepted']
        cell = (0 if accepted else 1) if y is True else (2 if accepted else 3) if y is False else (4 if accepted else 5)
        a[index[r['question_id']], cell] += 1
        a[index[r['question_id']], 6] += r['truncated']; a[index[r['question_id']], 7] += r['output_tokens']
    return a


def count_metrics(a):
    tp, fn, fp, tn, ua, ur, trunc, length = np.moveaxis(np.asarray(a), -1, 0)
    known = tp+fn+fp+tn; total = known+ua+ur
    p = tp+fn; n = fp+tn; accepted = tp+fp+ua
    return {'TP': tp, 'FN': fn, 'FP': fp, 'TN': tn, 'known': known, 'unknown': ua+ur,
        'correct': p, 'wrong': n, 'total': total, 'accepted_total': accepted, 'accepted_unknown': ua,
        'FNR': divide(fn, p), 'FPR': divide(fp, n), 'TPR': divide(tp, p), 'TNR': divide(tn, n),
        'precision_PPV': divide(tp, tp+fp), 'accepted_error_FDR': divide(fp, tp+fp),
        'NPV': divide(tn, tn+fn), 'rejected_correct_FOR': divide(fn, tn+fn),
        'V_accuracy': divide(tp+tn, known), 'balanced_accuracy': (divide(tp, p)+divide(tn, n))/2,
        'F1': divide(2*tp, 2*tp+fp+fn), 'MCC': divide(tp*tn-fp*fn, np.sqrt((tp+fp)*(tp+fn)*(tn+fp)*(tn+fn))),
        'G_accuracy_verified': divide(p, known), 'all_accept_baseline_accuracy': divide(p, known),
        'retention_all': divide(accepted, total), 'retention_verified': divide(tp+fp, known),
        'verifiable_coverage': divide(known, total), 'unknown_fraction': divide(ua+ur, total),
        'G_accuracy_lower_all': divide(p, total), 'G_accuracy_upper_all': divide(p+ua+ur, total),
        'accepted_label_coverage': divide(tp+fp, accepted), 'accepted_error_lower_all': divide(fp, accepted),
        'accepted_error_upper_all': divide(fp+ua, accepted), 'truncated': trunc,
        'truncated_fraction': divide(trunc, total), 'mean_output_tokens': divide(length, total)}


def score_metrics(y, scores, weights=None):
    """Tie-aware ROC AUC and noninterpolated AP, supporting cluster multiplicity weights."""
    y = np.asarray(y, float); scores = np.asarray(scores, float)
    weights = np.ones((1, len(y))) if weights is None else np.atleast_2d(weights)
    order = np.argsort(-scores, kind='stable'); s = scores[order]; truth = y[order]; w = weights[:, order]
    starts = np.r_[0, np.flatnonzero(np.diff(s) != 0)+1]
    positive = np.add.reduceat(w*truth, starts, axis=1)
    negative = np.add.reduceat(w*(1-truth), starts, axis=1)
    tp = np.cumsum(positive, axis=1); fp = np.cumsum(negative, axis=1)
    p = tp[:, -1]; n = fp[:, -1]; total = p+n
    auc = divide(np.sum(negative*(tp-positive/2), axis=1), p*n)
    ap = np.sum(np.nan_to_num(divide(positive, p[:, None])*divide(tp, tp+fp)), axis=1)
    ap = np.where(p > 0, ap, np.nan)
    brier = divide(weights @ ((scores-y)**2), total)
    ece = np.zeros(len(weights)); bins = np.minimum((scores*10).astype(int), 9)
    for b in range(10):
        use = bins == b
        if not use.any(): continue
        count = weights[:, use].sum(1)
        ece += np.nan_to_num(divide(np.abs(weights[:, use] @ scores[use]-weights[:, use] @ y[use]), total))
    result = {'AUROC': auc, 'average_precision': ap, 'Brier_proxy': brier, 'ECE10_proxy': ece}
    curves = {'threshold': s[starts], 'TPR': divide(tp[0], p[0]), 'FPR': divide(fp[0], n[0]),
              'precision': divide(tp[0], tp[0]+fp[0]), 'recall': divide(tp[0], p[0])}
    return result, curves


def load_rows(round_name, split):
    assert split in SPLITS and round_name in ROUNDS
    g = read_jsonl(RUN/f'{round_name}_{split}.jsonl')
    v = {r['candidate_id']: r for r in read_jsonl(RUN/f'V0_{round_name}_{split}.jsonl')}
    labels = {r['candidate_id']: r for r in read_jsonl(RUN/f'labels_{round_name}_{split}.jsonl')}
    assert len(g) == len(v) == len(labels) == len(inputs(split))*4
    out = []
    for r in g:
        vr, lab = v[r['candidate_id']], labels[r['candidate_id']]
        assert vr['threshold'] == .5 and bool(vr['score'] >= .5) == vr['accepted']
        assert not r['truncated'] or lab['correct'] is None
        out.append({**r, 'correct': lab['correct'], 'score': vr['score'], 'accepted': vr['accepted'],
            'V_prompt_tokens': vr['prompt_tokens'], 'verification_status': lab['verification_status'],
            'unknown_reason': lab['unknown_reason'], 'answer_format': lab['answer_format'],
            'extracted_answer': lab['extracted_answer'], 'gold_answer': lab['gold_answer'],
            'V_model_fingerprint': vr['model_fingerprint']})
    return out


def analyze(rows, question_ids, bootstrap_indices):
    qcounts = count_array(rows, question_ids); point = count_metrics(qcounts.sum(0))
    replicates = count_metrics(qcounts[bootstrap_indices].sum(1))
    qindex = {q: i for i, q in enumerate(question_ids)}
    counts = np.zeros((len(bootstrap_indices), len(question_ids)))
    for i, sample in enumerate(bootstrap_indices): np.add.at(counts[i], sample, 1)
    known = [r for r in rows if r['correct'] is not None]
    y = np.array([r['correct'] for r in known], float); scores = np.array([r['score'] for r in known])
    s, curves = score_metrics(y, scores)
    bs, _ = score_metrics(y, scores, counts[:, [qindex[r['question_id']] for r in known]])
    point.update({k: v[0] for k, v in s.items()}); replicates.update(bs)
    ci = {k: interval(replicates[k]) for k in PRIMARY}
    point.update({'questions': len(question_ids), 'median_output_tokens': np.median([r['output_tokens'] for r in rows]),
        'mean_proxy_score_known': float(np.mean(scores)),
        'p95_output_tokens': np.quantile([r['output_tokens'] for r in rows], .95),
        'error_questions': len({r['question_id'] for r in rows if r['correct'] is False}),
        'false_accept_questions': len({r['question_id'] for r in rows if r['correct'] is False and r['accepted']}),
        'false_reject_questions': len({r['question_id'] for r in rows if r['correct'] is True and not r['accepted']}),
        'unknown_reasons': dict(Counter(r['unknown_reason'] for r in rows if r['correct'] is None)),
        'answer_formats': dict(Counter(r['answer_format'] or 'none' for r in rows))})
    return clean(point), ci, replicates, clean(curves)


def transition(old, new):
    a = {r['question_id']: r for r in old if r['candidate_index'] == 0}
    b = {r['question_id']: r for r in new if r['candidate_index'] == 0}
    assert set(a) == set(b)
    matrix = np.zeros((2, 2), int); unknown = 0
    for q, r in a.items():
        s = b[q]
        if r['correct'] is None or s['correct'] is None: unknown += 1; continue
        matrix[int(r['accepted'] != r['correct']), int(s['accepted'] != s['correct'])] += 1
    aa, bb, cc, dd = matrix.ravel()
    return clean({'a': aa, 'b': bb, 'c': cc, 'd': dd, 'unknown_pairs': unknown,
                  'retained_judgement_correctness': divide(aa, aa+bb), 'degraded_judgement_correctness': divide(bb, aa+bb),
                  'repaired_judgement_correctness': divide(cc, cc+dd),
                  'note': 'candidate_index0, same questions but different G answers/truth; descriptive, not V forgetting on fixed answers'})


def threshold_rows(rows):
    # Descriptive only: never change deployed threshold or select a new operating point.
    thresholds = np.r_[np.inf, sorted({r['score'] for r in rows}, reverse=True), -np.inf]
    out = []
    for threshold in thresholds:
        accepted = [r for r in rows if r['score'] >= threshold]
        c = sum(r['correct'] is True for r in accepted); w = sum(r['correct'] is False for r in accepted); u = len(accepted)-c-w
        out.append({'threshold': threshold, 'endpoint': 'accept_none' if threshold == np.inf else 'accept_all' if threshold == -np.inf else None,
            'retention_all': len(accepted)/len(rows), 'accepted_known': c+w,
            'accepted_unknown': u, 'risk_verified': divide(w, c+w), 'risk_lower_all': divide(w, len(accepted)),
            'risk_upper_all': divide(w+u, len(accepted))})
    return clean(out)


def csv_write(path, rows):
    if not rows: return
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(clean(rows))


def main():
    p = argparse.ArgumentParser(); p.add_argument('--bootstrap', type=int, default=2000); p.add_argument('--seed', type=int, default=20261022)
    a = p.parse_args(); assert a.bootstrap >= 100
    verify_execution_revision()
    destination = REPORT/'v0_drift_analysis_20261022'; destination.mkdir(exist_ok=True); (destination/'figures').mkdir(exist_ok=True)
    raw_paths = [RUN/f'{prefix}{r}_{s}.jsonl' for r in ROUNDS for s in SPLITS for prefix in ['', 'V0_', 'labels_']]
    source_hashes = {str(path.relative_to(ROOT)): sha(path) for path in raw_paths}
    manifest = {'analysis_started_at': utc(), 'analysis_code_sha256': sha(Path(__file__)), 'protocol_sha256': sha(PROTOCOL),
        'raw_source_sha256': source_hashes, 'splits': {'B': 200, 'C': 96}, 'rounds': ROUNDS, 'threshold': .5,
        'bootstrap_replicates': a.bootstrap, 'seed': a.seed, 'resampling_unit': 'whole question including four candidates; paired same question indices across G rounds',
        'confidence_interval': 'pointwise percentile95, exploratory; no multiplicity adjustment, no equivalence claim',
        'reference_labels': 'frozen independent final-answer comparison; unknown never negative',
        'T_read_or_evaluated': False, 'GPU_inference_or_training': False}
    save_json(destination/'analysis_manifest.json', manifest)
    data = {}; result = {'settings': manifest, 'metrics': {}, 'paired_deltas': {}, 'transitions': {}}
    summaries = []; long = []; paired = []; lengths = []; per_question = []; curves = {}; risks = {}; bootstrap = {}
    for split in SPLITS:
        qs = sorted(r['question_id'] for r in inputs(split))
        rng = np.random.default_rng(a.seed+(0 if split == 'B' else 1)); indices = rng.integers(0, len(qs), (a.bootstrap, len(qs)))
        for r in ROUNDS:
            key = f'{r}_{split}'; rows = load_rows(r, split); data[key] = rows
            point, ci, boot, curve = analyze(rows, qs, indices)
            # Audit primary counts against the frozen formal analysis.
            old = json.loads((REPORT/f'{key}_metrics.json').read_text())
            assert all(point[k] == old[k] for k in ['TP', 'FN', 'FP', 'TN'])
            assert point['total'] == old['total_candidates'] and point['unknown'] == old['unknown']
            result['metrics'][key] = {'point': point, 'ci95': ci}; bootstrap[key] = boot; curves[key] = curve
            risks[key] = threshold_rows(rows)
            summaries.append({'split': split, 'G_round': r, **{k: v for k, v in point.items() if not isinstance(v, dict)}})
            for metric in PRIMARY:
                long.append({'split': split, 'G_round': r, 'metric': metric, 'estimate': point[metric], **ci[metric], 'known': point['known'], 'unknown': point['unknown'], 'questions': len(qs)})
            for label, lo, hi in LENGTHS:
                subset = [x for x in rows if lo <= x['output_tokens'] <= hi]
                m = clean(count_metrics(count_array(subset, qs).sum(0)))
                lengths.append({'split': split, 'G_round': r, 'length_bin': label, **m})
            for q in qs:
                rs = [x for x in rows if x['question_id'] == q]
                m = clean(count_metrics(count_array(rs, [q]).sum(0)))
                per_question.append({'split': split, 'G_round': r, 'question_id': q, **m})
            print(f'Computed {key}: FNR={point["FNR"]:.4f} FPR={point["FPR"]:.4f} AUROC={point["AUROC"]:.4f}', flush=True)
        for old_round, new_round in [('G0', 'G1'), ('G0', 'G2'), ('G1', 'G2')]:
            k0, k1 = f'{old_round}_{split}', f'{new_round}_{split}'
            for metric in DELTA:
                ci = interval(bootstrap[k1][metric]-bootstrap[k0][metric])
                record = {'split': split, 'comparison': f'{new_round}-{old_round}', 'metric': metric,
                    'delta': result['metrics'][k1]['point'][metric]-result['metrics'][k0]['point'][metric], **ci,
                    'CI_excludes_zero': bool(ci['low'] > 0 or ci['high'] < 0)}
                paired.append(record)
        result['transitions'][split] = {r: transition(data[f'G0_{split}'], data[f'{r}_{split}']) for r in ['G1', 'G2']}
    result['paired_deltas'] = paired
    result['length_strata'] = lengths
    # All-round fully verifiable question subset is a sensitivity check, not the primary sample.
    sensitivity = []
    for split in SPLITS:
        common = set.intersection(*[{q for q in {x['question_id'] for x in data[f'{r}_{split}']} if all(x['correct'] is not None for x in data[f'{r}_{split}'] if x['question_id'] == q)} for r in ROUNDS])
        for r in ROUNDS:
            subset = [x for x in data[f'{r}_{split}'] if x['question_id'] in common]
            m = clean(count_metrics(count_array(subset, sorted(common)).sum(0)))
            sensitivity.append({'split': split, 'G_round': r, 'common_fully_verifiable_questions': len(common), **m})
    result['common_complete_question_sensitivity'] = sensitivity
    save_json(destination/'metrics_and_inference.json', result)
    save_json(destination/'curves.json', {'ROC_PR': curves, 'risk_coverage': risks})
    csv_write(destination/'metrics_summary.csv', summaries); csv_write(destination/'metrics_ci95.csv', long)
    csv_write(destination/'paired_deltas.csv', paired); csv_write(destination/'length_strata.csv', lengths)
    csv_write(destination/'per_question_metrics.csv', per_question); csv_write(destination/'complete_question_sensitivity.csv', sensitivity)
    write_jsonl(destination/'candidates_full.jsonl', [row for rows in data.values() for row in rows])
    from .v0_drift_visuals import build_outputs
    build_outputs(destination, result, data, curves, risks, summaries, long, paired, lengths, per_question, sensitivity)
    assert all(sha(ROOT/name) == h for name, h in source_hashes.items()), 'Analysis modified raw results'
    manifest['analysis_finished_at'] = utc(); manifest['raw_inputs_unchanged_after_analysis'] = True
    manifest['figures_code_sha256'] = sha(ROOT/'src/pilot/v0_drift_visuals.py')
    save_json(destination/'analysis_manifest.json', manifest)
    import zipfile
    bundle = destination.parent/'v0_drift_analysis_20261022.zip'
    with zipfile.ZipFile(bundle, 'w', zipfile.ZIP_DEFLATED) as z:
        for path in sorted(destination.rglob('*')):
            if path.is_file(): z.write(path, arcname=str(path.relative_to(destination.parent)))
        for name in ['v0_drift_analysis.py', 'v0_drift_visuals.py']:
            z.write(ROOT/'src/pilot'/name, arcname=f'analysis_code/{name}')
    print(f'COMPLETE {destination}; bundle={bundle}', flush=True)


if __name__ == '__main__': main()
