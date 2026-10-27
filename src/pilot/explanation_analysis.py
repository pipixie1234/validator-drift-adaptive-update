"""Exploratory decomposition of the fixed-V trajectory; immutable formal inputs."""
import copy
import hashlib
import itertools
import json
import math
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path

import numpy as np

from .core import ROOT, read_jsonl, write_json, write_jsonl
from .day2_protocol import numeric
from .formal_common import RUN, REPORT, PROTOCOL, sha, utc
from .formal_multigpu_revision import verify_execution_revision
from .v0_drift_analysis import clean, csv_write, interval, count_array, count_metrics
from .explanation_probes import DEST, SUSPECT, order

ROUNDS = ['G0', 'G1', 'G2']
SCHEMES = {'output_length': ['<=512', '513-1024', '>1024'],
           'answer_gap': ['absolute_gap_1', 'absolute_gap_2_to_10', 'other_wrong_answer'],
           'question_length': ['<=128', '129-256', '>256']}


def category(r, scheme):
    if scheme == 'output_length':
        return '<=512' if r['output_tokens'] <= 512 else '513-1024' if r['output_tokens'] <= 1024 else '>1024'
    if scheme == 'question_length':
        return '<=128' if r['prompt_tokens'] <= 128 else '129-256' if r['prompt_tokens'] <= 256 else '>256'
    gap = abs(Fraction(numeric(r['extracted_answer']))-Fraction(numeric(r['gold_answer'])))
    return 'absolute_gap_1' if gap == 1 else 'absolute_gap_2_to_10' if 2 <= gap <= 10 else 'other_wrong_answer'


def symmetric_decomposition(n0, fp0, n1, fp1):
    """Exact midpoint decomposition, NaN when a stratum has one-sided support."""
    n0, fp0, n1, fp1 = [np.asarray(x, dtype=float) for x in [n0, fp0, n1, fp1]]
    w0 = np.divide(n0, n0.sum(-1, keepdims=True), out=np.full_like(n0, np.nan), where=n0.sum(-1, keepdims=True) != 0)
    w1 = np.divide(n1, n1.sum(-1, keepdims=True), out=np.full_like(n1, np.nan), where=n1.sum(-1, keepdims=True) != 0)
    p0 = np.divide(fp0, n0, out=np.full_like(n0, np.nan), where=n0 != 0)
    p1 = np.divide(fp1, n1, out=np.full_like(n1, np.nan), where=n1 != 0)
    empty = (n0 == 0) & (n1 == 0)
    # Both-zero cells contribute zero; missing a supported group's rate is not zero.
    c = np.where(empty, 0., (w1-w0)*(p1+p0)/2)
    h = np.where(empty, 0., (p1-p0)*(w1+w0)/2)
    delta = fp1.sum(-1)/n1.sum(-1)-fp0.sum(-1)/n0.sum(-1)
    return c, h, delta


def question_support_decomposition(n0, fp0, n1, fp1):
    """Question composition/within on common error support, plus explicit entry/exit."""
    n0, fp0, n1, fp1 = [np.asarray(x, dtype=float) for x in [n0, fp0, n1, fp1]]
    w0 = n0/n0.sum(-1, keepdims=True); w1 = n1/n1.sum(-1, keepdims=True)
    common = (n0 > 0) & (n1 > 0)
    p0 = np.divide(fp0, n0, out=np.zeros_like(n0), where=n0 > 0)
    p1 = np.divide(fp1, n1, out=np.zeros_like(n1), where=n1 > 0)
    # Placeholder rates in absent cells never enter composition/within terms.
    # Those cells are kept separately as observed global FP mass, not imputed rates.
    c = np.where(common, (w1-w0)*(p1+p0)/2, 0.).sum(-1)
    h = np.where(common, (p1-p0)*(w1+w0)/2, 0.).sum(-1)
    entry = np.where((n0 == 0) & (n1 > 0), fp1/n1.sum(-1, keepdims=True), 0.).sum(-1)
    exit_ = -np.where((n1 == 0) & (n0 > 0), fp0/n0.sum(-1, keepdims=True), 0.).sum(-1)
    total = fp1.sum(-1)/n1.sum(-1)-fp0.sum(-1)/n0.sum(-1)
    return {'common_composition': c, 'common_within': h, 'new_error_question_entry': entry,
            'old_error_question_exit': exit_, 'total': total}


def question_decomposition(rows, reps=2000):
    out=[]
    for split in ['C','B']:
        qs=sorted({r['question_id'] for r in rows if r['split']==split and r['question_id'] != SUSPECT})
        arrays={g:count_array([r for r in rows if r['split']==split and r['G_round']==g and r['question_id'] != SUSPECT],qs) for g in ROUNDS}
        samples=np.random.default_rng(20261022+(split=='B')).integers(len(qs),size=(reps,len(qs)))
        for old,new in [('G0','G1'),('G0','G2'),('G1','G2')]:
            a,b=arrays[old],arrays[new]
            n0=a[:,2]+a[:,3];n1=b[:,2]+b[:,3]
            point=question_support_decomposition(n0,a[:,2],n1,b[:,2])
            boot=question_support_decomposition(n0[samples],a[:,2][samples],n1[samples],b[:,2][samples])
            assert np.isclose(sum(v for k,v in point.items() if k!='total'),point['total'])
            common=int(((n0>0)&(n1>0)).sum());entries=int(((n0==0)&(n1>0)).sum());exits=int(((n1==0)&(n0>0)).sum())
            for k in point:
                out.append({'split':split,'contrast':new+'-'+old,'component':k,'estimate':float(point[k]),**interval(boot[k]),
                            'common_error_questions':common,'new_error_questions':entries,'lost_error_questions':exits,
                            'note':'Question-specific rates use at most4 wrong candidates per round; exploratory decomposition, not semantic error taxonomy or causal within-question mechanism.'})
    return out


def metric(r):
    tp = sum(x['correct'] is True and x['accepted'] for x in r)
    fn = sum(x['correct'] is True and not x['accepted'] for x in r)
    fp = sum(x['correct'] is False and x['accepted'] for x in r)
    tn = sum(x['correct'] is False and not x['accepted'] for x in r)
    ua = sum(x['correct'] is None and x['accepted'] for x in r)
    ur = sum(x['correct'] is None and not x['accepted'] for x in r)
    div = lambda a, b: a/b if b else None
    return {'n': len(r), 'correct': tp+fn, 'wrong': fp+tn, 'unknown': ua+ur,
            'TP': tp, 'FN': fn, 'FP': fp, 'TN': tn, 'unknown_accepted': ua, 'unknown_rejected': ur,
            'G_accuracy_verified': div(tp+fn, tp+fn+fp+tn),
            'G_accuracy_lower': div(tp+fn, len(r)), 'G_accuracy_upper': div(tp+fn+ua+ur, len(r)),
            'FPR': div(fp, fp+tn), 'FNR': div(fn, tp+fn),
            # Sharp identification bounds: make rejected unknowns negative for minimum FPR,
            # accepted unknowns negative for maximum FPR. These are NOT confidence intervals.
            'FPR_unknown_lower': div(fp, fp+tn+ur), 'FPR_unknown_upper': div(fp+ua, fp+tn+ua),
            'unknown_fraction': div(ua+ur, len(r)), 'truncated': sum(x['truncated'] for x in r),
            'mean_output_tokens': float(np.mean([x['output_tokens'] for x in r])) if r else None}


def exact_reference_checks():
    import sympy as s
    # Complete solution, not just bounded brute force.
    # d=x-y, u=x+y: (3d-1)u^2+d^2(d+1)=244. d<=0 impossible;
    # d>=1 implies d^2(d+1)<=244, so d<=5.
    cases, solutions = [], []
    for d in range(1, 6):
        sq = Fraction(244-d*d*(d+1), 3*d-1)
        roots = []
        if sq.denominator == 1 and sq >= 0 and math.isqrt(sq.numerator)**2 == sq:
            u0 = math.isqrt(sq.numerator)
            for u in sorted({-u0, u0}):
                if (u+d) % 2 == 0:
                    x, y = (u+d)//2, (u-d)//2
                    assert x**3-y**3 == x*y+61
                    solutions.append([x, y]); roots.append(u)
        cases.append({'d': d, 'u_squared': str(sq), 'admissible_u': roots})
    assert sorted(solutions) == [[-5, -6], [6, 5]]
    x = s.symbols('x')
    p = s.together((x-49)/50+(x-50)/49-49/(x-50)-50/(x-49)).as_numer_denom()[0]
    roots = s.solve(p, x); assert all(r not in [49, 50] for r in roots)
    rational_sum = sum(roots)
    a = 3
    initial = [1, 3]
    for n in range(3, 2014):
        t = a; digits = []
        while t:
            digits.append(t % (n-1)); t //= n-1
        a = sum(d*n**i for i, d in enumerate(digits))+2
        if n <= 8: initial.append(a)
    triples = [(a, b, c) for a in range(1, 10) for b in range(a, 10) for c in range(b, 10) if a*a+b*b+c*c == 81]
    checks = {'integer_cubic': {'question_id': SUSPECT, 'frozen_reference': '6', 'recomputed': '1',
               'complete_solution': solutions, 'finite_cases': cases,
               'proof': '令d=x-y,u=x+y，原式等价于(3d-1)u²+d²(d+1)=244。d≤0时左侧非正；d≥1时d²(d+1)≤244，故1≤d≤5。逐一检查u²并要求u与d同奇偶，只有d=1,u=±11；x=-5,6，和为1。',
               'status': 'model_assisted_exact_proof_pending_user_review_not_silently_relabelled'},
              'rational_equation': {'polynomial': str(s.factor(p)), 'roots': [str(r) for r in roots],
                                    'root_sum': str(rational_sum), 'a_plus_b': int(s.numer(rational_sum)+s.denom(rational_sum)), 'reference': 14801},
              'base_sequence': {'initial_terms': initial, 'a2013': a, 'reference': 23097},
              'three_squares': {'triples': triples, 'count': len(triples), 'reference': 3},
              'colored_tilings': {'formula': '3*4^6-6*3^6+3*2^6', 'count': 3*4**6-6*3**6+3*2**6,
                                 'mod1000': (3*4**6-6*3**6+3*2**6) % 1000, 'reference': 106},
              'license_plates': {'count': 2*9*8*7, 'reference': 1008, 'proof': '固定首字母和末位T后，中间3位依次9、8、7种。'},
              'red_four_door_cars': {'count': 12-(20-15-4), 'reference': 11},
              'exponential_roots': {'positive_roots': ['1', '9/4'], 'sum': '13/4', 'k_plus_m': 17, 'reference': 17,
                                    'domain_note': '实数常规幂定义下x>0；x=1需单列，x=0涉及0^0不作为有效根。'}}
    assert checks['rational_equation']['a_plus_b'] == 14801 and a == 23097
    return checks


def decomposition_analysis(rows, seed=20261022, reps=2000):
    cells, contributions, totals = [], [], []
    for split in ['C', 'B']:
        qs = sorted({r['question_id'] for r in rows if r['split'] == split and r['question_id'] != SUSPECT})
        qi = {q: i for i, q in enumerate(qs)}
        samples = np.random.default_rng(seed+(split == 'B')).integers(len(qs), size=(reps, len(qs)))
        for scheme, groups in SCHEMES.items():
            ki = {k: i for i, k in enumerate(groups)}
            arrays = {}
            for g in ROUNDS:
                a = np.zeros((len(qs), len(groups), 2))
                for r in rows:
                    if r['split'] == split and r['G_round'] == g and r['correct'] is False and r['question_id'] != SUSPECT:
                        k = category(r, scheme)
                        a[qi[r['question_id']], ki[k], 0] += 1
                        a[qi[r['question_id']], ki[k], 1] += r['accepted']
                arrays[g] = a
                sums = a.sum(0)
                for k, (n, fp) in zip(groups, sums):
                    cells.append({'split': split, 'G_round': g, 'scheme': scheme, 'stratum': k,
                                  'wrong_n': int(n), 'false_accepted': int(fp),
                                  'error_share': n/sums[:, 0].sum(), 'FPR_within': fp/n if n else None})
            for old, new in [('G0', 'G1'), ('G0', 'G2'), ('G1', 'G2')]:
                a, b = arrays[old].sum(0), arrays[new].sum(0)
                c, h, delta = symmetric_decomposition(a[:, 0], a[:, 1], b[:, 0], b[:, 1])
                aa, bb = arrays[old][samples].sum(1), arrays[new][samples].sum(1)
                bc, bh, bd = symmetric_decomposition(aa[:, :, 0], aa[:, :, 1], bb[:, :, 0], bb[:, :, 1])
                for i, k in enumerate(groups):
                    contributions.append({'split': split, 'scheme': scheme, 'contrast': new+'-'+old, 'stratum': k,
                        'composition': c[i], 'within_stratum': h[i], 'sum': c[i]+h[i],
                        'composition_ci_low': interval(bc[:, i])['low'], 'composition_ci_high': interval(bc[:, i])['high'],
                        'within_ci_low': interval(bh[:, i])['low'], 'within_ci_high': interval(bh[:, i])['high']})
                cc, hh = c.sum(), h.sum()
                if np.isfinite(cc+hh): assert np.isclose(cc+hh, delta, atol=1e-12)
                for name, value, draws in [('composition', cc, bc.sum(-1)), ('within_stratum', hh, bh.sum(-1)), ('total', delta, bd)]:
                    totals.append({'split': split, 'scheme': scheme, 'contrast': new+'-'+old, 'component': name,
                                   'estimate': value, **interval(draws), 'questions': len(qs), 'reference_suspect_excluded': split == 'C'})
    return clean(cells), clean(contributions), clean(totals)


def behavior(rows):
    summary, transitions = [], []
    for split in ['C', 'B']:
        rs = [r for r in rows if r['split'] == split and r['question_id'] != SUSPECT]
        grouped = {g: [r for r in rs if r['G_round'] == g] for g in ROUNDS}
        for g, gg in grouped.items():
            qmap = defaultdict(list)
            for r in gg: qmap[r['question_id']].append(r)
            m = metric(gg)
            m.update({'split': split, 'G_round': g, 'questions': len(qmap),
                      'any_correct_at4': np.mean([any(r['correct'] is True for r in q) for q in qmap.values()]),
                      'mean_unique_final_answers': np.mean([len({numeric(r['extracted_answer']) for r in q if r['correct'] is not None}) for q in qmap.values()]),
                      'all4_known': sum(all(r['correct'] is not None for r in q) for q in qmap.values()),
                      'all4_wrong': sum(all(r['correct'] is False for r in q) for q in qmap.values()),
                      'answer_format_counts': dict(Counter(r['answer_format'] or 'none' for r in gg))})
            summary.append(clean(m))
        for old, new in [('G0', 'G1'), ('G0', 'G2'), ('G1', 'G2')]:
            a = {(r['question_id'], r['candidate_index']): r for r in grouped[old]}
            b = {(r['question_id'], r['candidate_index']): r for r in grouped[new]}
            known = [(a[k], b[k]) for k in a if a[k]['correct'] is not None and b[k]['correct'] is not None]
            counts = Counter(('correct' if r['correct'] else 'wrong')+'_to_'+('correct' if s['correct'] else 'wrong') for r, s in known)
            wrongpairs = [(r, s) for r, s in known if r['correct'] is False and s['correct'] is False]
            vcounts = Counter(('accept' if r['accepted'] else 'reject')+'_to_'+('accept' if s['accepted'] else 'reject') for r, s in wrongpairs)
            transitions.append({'split': split, 'contrast': new+'-'+old, 'all_pairs': len(a), 'known_pairs': len(known),
                'unknown_pairs': len(a)-len(known), **dict(counts),
                'same_canonical_final_known': sum(numeric(r['extracted_answer']) == numeric(s['extracted_answer']) for r, s in known),
                'identical_full_text': sum(a[k]['text'] == b[k]['text'] for k in a),
                'wrong_to_wrong_n': len(wrongpairs), 'wrong_to_wrong_V_transitions': dict(vcounts),
                'note': 'Same question and candidate index are descriptive pairs, not counterfactual identical random draws or fixed-answer V forgetting.'})
    return summary, transitions


def behavior_intervals(rows, reps=2000):
    out, deltas = [], []
    keys = ['FNR', 'FPR', 'G_accuracy_verified', 'G_accuracy_lower_all', 'unknown_fraction', 'mean_output_tokens']
    for split in ['C', 'B']:
        qs = sorted({r['question_id'] for r in rows if r['split'] == split and r['question_id'] != SUSPECT})
        samples = np.random.default_rng(20261022+(split == 'B')).integers(len(qs), size=(reps, len(qs)))
        points, boots = {}, {}
        for g in ROUNDS:
            rr = [r for r in rows if r['split'] == split and r['G_round'] == g and r['question_id'] != SUSPECT]
            counts = count_array(rr, qs)
            points[g] = count_metrics(counts.sum(0)); boots[g] = count_metrics(counts[samples].sum(1))
            for k in keys:
                out.append({'split': split, 'G_round': g, 'metric': k, 'estimate': float(points[g][k]), **interval(boots[g][k])})
        for old, new in [('G0', 'G1'), ('G0', 'G2'), ('G1', 'G2')]:
            for k in keys:
                deltas.append({'split': split, 'contrast': new+'-'+old, 'metric': k,
                               'estimate': float(points[new][k]-points[old][k]), **interval(boots[new][k]-boots[old][k])})
    return out, deltas


def feedback_quality():
    summaries, selection_rows = [], []
    for target, source in [('G1', 'G0'), ('G2', 'G1')]:
        picked = read_jsonl(RUN/f'train/{target}/selected.jsonl')
        raw = read_jsonl(RUN/f'{source}_A.jsonl')
        v = {r['candidate_id']: r for r in read_jsonl(RUN/f'V0_{source}_A.jsonl')}
        labels = {r['candidate_id']: r for r in read_jsonl(RUN/f'labels_{source}_A.jsonl')}
        selected_ids = {r['candidate_id'] for r in picked}
        for group in ['all_A', 'V0_accepted_A', 'actual_SFT']:
            rs = [r for r in raw if group == 'all_A' or (group == 'V0_accepted_A' and v[r['candidate_id']]['accepted']) or (group == 'actual_SFT' and r['candidate_id'] in selected_ids)]
            c = sum(labels[r['candidate_id']]['correct'] is True for r in rs)
            w = sum(labels[r['candidate_id']]['correct'] is False for r in rs)
            u = len(rs)-c-w
            summaries.append({'target': target, 'source': source, 'group': group, 'n': len(rs), 'correct': c, 'wrong': w,
                              'unknown': u, 'verified_error_fraction': w/(c+w), 'correct_lower_all': c/len(rs),
                              'correct_upper_all': (c+u)/len(rs)})
        for r in picked:
            selection_rows.append({'target': target, 'candidate_id': r['candidate_id'], 'question_id': r['question_id'],
                'correct': labels[r['candidate_id']]['correct'], 'extracted_answer': labels[r['candidate_id']]['extracted_answer'],
                'supervised_tokens': r['supervised_tokens'], 'output_tokens': r['output_tokens'], 'V0_score': r['V0_score']})
    byq = {g: {r['question_id']: r for r in selection_rows if r['target'] == g} for g in ['G1', 'G2']}
    common = sorted(set(byq['G1']) & set(byq['G2']))
    known = [q for q in common if byq['G1'][q]['correct'] is not None and byq['G2'][q]['correct'] is not None]
    overlap = {'common_questions': len(common), 'known_both': len(known),
               'known_outcome_transitions': dict(Counter(str(byq['G1'][q]['correct'])+'_to_'+str(byq['G2'][q]['correct']) for q in known)),
               'same_final_answer_known': sum(numeric(byq['G1'][q]['extracted_answer']) == numeric(byq['G2'][q]['extracted_answer']) for q in known),
               'both_wrong_same_final_answer': sum(byq['G1'][q]['correct'] is False and byq['G2'][q]['correct'] is False and numeric(byq['G1'][q]['extracted_answer']) == numeric(byq['G2'][q]['extracted_answer']) for q in known)}
    tokenmass = []
    for g in ['G1', 'G2']:
        rr = [r for r in selection_rows if r['target'] == g]
        for truth in [True, False, None]:
            group = [r for r in rr if r['correct'] is truth]
            tokenmass.append({'target': g, 'correct': truth, 'examples': len(group),
                              'supervised_tokens': sum(r['supervised_tokens'] for r in group),
                              'token_fraction': sum(r['supervised_tokens'] for r in group)/sum(r['supervised_tokens'] for r in rr),
                              'note': 'Token mass is not gradient contribution; original objective averages per-example mean token loss.'})
    return summaries, tokenmass, overlap


def likelihood_summary(reps=2000):
    path = DEST/'likelihood_scores.jsonl'
    if not (DEST/'likelihood_run.json').exists(): return []
    rows = read_jsonl(path)
    assert len(rows) == 3*len(read_jsonl(DEST/'likelihood_inputs.jsonl'))
    cohorts = sorted({r['cohort'] for r in rows})
    result = []
    for cohort in cohorts:
        rr = [r for r in rows if r['cohort'] == cohort]
        byid = {g: {r['probe_id']: r for r in rr if r['model_round'] == g} for g in ROUNDS}
        ids = sorted(byid['G0']); assert all(set(byid[g]) == set(ids) for g in ROUNDS)
        qs = sorted({r['question_id'] for r in rr})
        qi = {q: i for i, q in enumerate(qs)}
        arr = np.zeros((len(qs), 4))
        for pid in ids:
            i = qi[byid['G0'][pid]['question_id']]
            arr[i, :3] += [byid[g][pid]['response_nll_per_token'] for g in ROUNDS]
            arr[i, 3] += 1
        samples = np.random.default_rng(20261022).integers(len(qs), size=(reps, len(qs)))
        boot = arr[samples].sum(1); means = boot[:, :3]/boot[:, 3:4]
        point = arr[:, :3].sum(0)/arr[:, 3].sum()
        for a, b in [(0, 1), (0, 2), (1, 2)]:
            result.append({'cohort': cohort, 'examples': len(ids), 'questions': len(qs), 'contrast': ROUNDS[b]+'-'+ROUNDS[a],
                           'mean_nll_old': point[a], 'mean_nll_new': point[b], 'delta_nll': point[b]-point[a],
                           **interval(means[:, b]-means[:, a]),
                           'interpretation': 'Negative delta means greater likelihood of these exact fixed texts, not higher mathematical accuracy.'})
    return clean(result)


def matched_likelihood(reps=2000):
    if not (DEST/'likelihood_run.json').exists(): return []
    scores = {(r['probe_id'], r['model_round']): r['response_nll_per_token'] for r in read_jsonl(DEST/'likelihood_scores.jsonl')}
    probes = read_jsonl(DEST/'likelihood_inputs.jsonl')
    out = []
    for target, old in [('G1', 'G0'), ('G2', 'G1')]:
        for truth in [True, False, None]:
            controls = [r for r in probes if r['kind'] == 'unselected_A_same_question' and r['target_update'] == target and r['correct'] is truth]
            ds, dc = [], []
            for r in controls:
                selected = r['matched_selected_probe_id']; control = r['probe_id']
                ds.append(scores[selected, target]-scores[selected, old])
                dc.append(scores[control, target]-scores[control, old])
            if not ds: continue
            differences = np.array(ds)-np.array(dc)
            samples = np.random.default_rng(20261022).integers(len(ds), size=(reps, len(ds)))
            out.append({'target': target, 'correct': truth, 'pairs': len(ds),
                        'selected_delta_nll': np.mean(ds), 'control_delta_nll': np.mean(dc),
                        'selected_minus_control_delta': np.mean(differences), **interval(differences[samples].mean(1)),
                        'limitation': 'Same-question accepted same-outcome alternatives, not randomized no-update/noiseless-feedback training controls.'})
    return clean(out)


def main():
    verify_execution_revision()
    DEST.mkdir(exist_ok=True)
    source = REPORT/'v0_drift_analysis_20261022/candidates_full.jsonl'
    original_paths = [RUN/f'{prefix}{g}_{s}.jsonl' for g in ROUNDS for s in ['B', 'C'] for prefix in ['', 'V0_', 'labels_']]
    original_paths += [RUN/f'{prefix}{g}_A.jsonl' for g in ['G0', 'G1'] for prefix in ['', 'V0_', 'labels_']]
    original_paths += [RUN/f'train/{g}/{name}.jsonl' for g in ['G1', 'G2'] for name in ['selected', 'tokenized', 'steps']]
    original_paths += [PROTOCOL, source]
    hashes = {str(p.relative_to(ROOT)): sha(p) for p in original_paths}
    plan = {'analysis': 'Exploratory competitive explanations; C primary, B separately supplementary',
            'created_at': utc(), 'primary_C_policy': 'Temporarily exclude the one newly suspect reference question (all12 candidates); retain original96-question results and a reference=1 sensitivity overlay separately. Not a frozen protocol edit.',
            'reference_suspect': SUSPECT, 'group_definitions': SCHEMES,
            'groups_are_observable_proxies_not_semantic_error_mechanisms': True,
            'semantic_review': '6 wrong candidate0 responses per G, C only, deterministic SHA256 20261022-review; chosen without V score; assistant annotations, not human gold or population proportions',
            'formula': 'w_tk=P_t(k|Y=0);p_tk=P_t(accept|Y=0,k); delta=sum[(w_t-w_0)*(p_t+p_0)/2]+sum[(p_t-p_0)*(w_t+w_0)/2]',
            'missing_support': 'One-sided missing rate is unidentifiable, never zero-filled. Both-zero cells contribute0; finite bootstrap replicate counts reported.',
            'bootstrap': {'unit': 'question, all4 candidates together, same resampled questions across G', 'replicates': 2000, 'seed': 20261022,
                          'interval': 'exploratory pointwise95 percentile, no multiple comparison adjustment or equivalence assertion'},
            'no_new_G_update_or_V_update': True, 'T_read': False, 'formal_sources_sha256': hashes}
    write_json(DEST/'analysis_plan.json', plan)
    rows = read_jsonl(source); assert len(rows) == 3552
    checks = exact_reference_checks(); write_json(DEST/'reference_checks.json', checks)
    affected = [r for r in rows if r['question_id'] == SUSPECT]
    assert len(affected) == 12 and {r['gold_answer'] for r in affected} == {'6'}
    write_jsonl(DEST/'reference_suspect_all12.jsonl', affected)
    sensitivity = []
    for g in ROUNDS:
        orig = [r for r in rows if r['split'] == 'C' and r['G_round'] == g]
        for mode in ['original96', 'exclude_suspect95', 'reference1_overlay96', 'exclude_truncated_and_suspect']:
            rr = copy.deepcopy(orig)
            if mode in ['exclude_suspect95', 'exclude_truncated_and_suspect']: rr = [r for r in rr if r['question_id'] != SUSPECT]
            if mode == 'exclude_truncated_and_suspect': rr = [r for r in rr if not r['truncated']]
            if mode == 'reference1_overlay96':
                for r in rr:
                    if r['question_id'] == SUSPECT and r['correct'] is not None:
                        r['correct'] = numeric(r['extracted_answer']) == '1'
            sensitivity.append({'G_round': g, 'scenario': mode, **metric(rr)})
    cells, contributions, totals = decomposition_analysis(rows)
    question_parts = question_decomposition(rows)
    behavior_rows, transitions = behavior(rows)
    behavior_ci, behavior_delta = behavior_intervals(rows)
    feedback, tokenmass, overlap = feedback_quality()
    likelihood = likelihood_summary()
    matched = matched_likelihood()
    unknown = []
    for split in ['C', 'B']:
        for g in ROUNDS:
            rr = [r for r in rows if r['split'] == split and r['G_round'] == g and r['question_id'] != SUSPECT]
            for reason in sorted({r['unknown_reason'] for r in rr if r['correct'] is None}):
                qq = [r for r in rr if r['unknown_reason'] == reason]
                unknown.append({'split': split, 'G_round': g, 'reason': reason, 'n': len(qq),
                                'questions': len({r['question_id'] for r in qq}), 'V0_accepted': sum(r['accepted'] for r in qq)})
    # Blind-to-V semantic review packets; scored/accepted fields deliberately absent.
    review = []
    for g in ROUNDS:
        eligible = [r for r in rows if r['split'] == 'C' and r['G_round'] == g and r['correct'] is False and r['candidate_index'] == 0]
        chosen = order(eligible, '20261022-review:')[:6]
        review += [{k: r[k] for k in ['question_id', 'candidate_id', 'G_round', 'question', 'text', 'gold_answer', 'extracted_answer']} for r in chosen]
    write_jsonl(DEST/'semantic_review_blind18.jsonl', review)
    for name, values in [('strata', cells), ('decomposition_by_stratum', contributions), ('decomposition_totals', totals), ('question_support_decomposition', question_parts),
                         ('behavior', behavior_rows), ('behavior_ci95', behavior_ci), ('behavior_paired_deltas', behavior_delta), ('transitions', transitions), ('reference_sensitivity', sensitivity),
                         ('feedback_quality', feedback), ('training_token_mass', tokenmass), ('likelihood_summary', likelihood), ('likelihood_matched_controls', matched), ('unknown_reasons', unknown)]:
        csv_write(DEST/f'{name}.csv', values)
    result = {'plan': plan, 'decomposition': totals, 'strata': cells, 'contributions': contributions,
              'question_support_decomposition': question_parts,
              'behavior': behavior_rows, 'transitions': transitions, 'feedback_quality': feedback,
              'behavior_ci95': behavior_ci, 'behavior_paired_deltas': behavior_delta,
              'training_token_mass': tokenmass, 'training_overlap': overlap,
              'reference_sensitivity': sensitivity, 'unknown_reasons': unknown, 'likelihood_summary': likelihood,
              'likelihood_matched_controls': matched,
              'reference_checks': checks, 'completed_at': utc()}
    write_json(DEST/'analysis.json', clean(result))
    assert all(sha(ROOT/p) == h for p, h in hashes.items())
    write_json(DEST/'source_integrity.json', {'checked_at': utc(), 'original_files_checked': len(hashes), 'unchanged': True, 'T_read': False})
    print(json.dumps({'status': 'complete', 'source_files_unchanged': len(hashes), 'likelihood_cohort_contrasts': len(likelihood)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
