import numpy as np
from pilot.v0_drift_analysis import score_metrics, count_array, count_metrics, analyze, transition


def row(q, i, y, accepted, score=.7):
    return {'question_id': q, 'candidate_index': i, 'correct': y, 'accepted': accepted,
            'score': score, 'truncated': y is None, 'output_tokens': 100, 'unknown_reason': None, 'answer_format': 'boxed'}


def test_unknown_is_excluded_from_confusion_but_not_retention_or_risk_bounds():
    rows = [row('q', 0, True, True), row('q', 1, True, False), row('q', 2, False, True), row('q', 3, None, True)]
    m = count_metrics(count_array(rows, ['q']).sum(0))
    assert m['known'] == 3 and m['unknown'] == 1
    assert m['FNR'] == .5 and m['FPR'] == 1
    assert m['precision_PPV'] == .5 and m['retention_all'] == .75
    assert m['accepted_error_lower_all'] == 1/3 and m['accepted_error_upper_all'] == 2/3


def test_auc_and_average_precision_handle_ties_and_empty_weighted_groups():
    perfect, _ = score_metrics([0, 1, 0, 1], [.1, .9, .2, .8])
    assert perfect['AUROC'][0] == perfect['average_precision'][0] == 1
    tied, _ = score_metrics([0, 1, 0, 1], [.5]*4)
    assert tied['AUROC'][0] == tied['average_precision'][0] == .5
    # Resampling may omit highest-score groups; zero-weight groups must not invalidate AP.
    weighted, _ = score_metrics([1, 0, 1, 0], [.9, .8, .7, .1], [[0, 0, 2, 2]])
    assert weighted['AUROC'][0] == weighted['average_precision'][0] == 1


def test_weighted_score_metrics_equal_explicit_cluster_replication():
    y = [1, 0, 1, 0, 1]; s = [.8, .8, .6, .2, .1]; weights = [2, 2, 0, 1, 1]
    a, _ = score_metrics(y, s, [weights])
    indices = np.repeat(np.arange(5), weights)
    b, _ = score_metrics(np.array(y)[indices], np.array(s)[indices])
    for name in a: assert np.allclose(a[name], b[name])


def test_zero_class_support_is_nan_not_zero():
    m = count_metrics(np.array([2, 0, 0, 0, 1, 0, 0, 50]))
    assert np.isnan(m['FPR']) and np.isnan(m['balanced_accuracy'])
    assert m['FNR'] == 0


def test_descriptive_transition_excludes_unknown_pairs():
    old = [row('a', 0, True, True), row('b', 0, False, True), row('c', 0, None, False)]
    new = [row('a', 0, True, False), row('b', 0, False, False), row('c', 0, True, True)]
    m = transition(old, new)
    assert (m['a'], m['b'], m['c'], m['d'], m['unknown_pairs']) == (0, 1, 1, 0, 1)
