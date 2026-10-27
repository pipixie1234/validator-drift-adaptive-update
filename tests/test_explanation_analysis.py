import itertools
import numpy as np

from pilot.explanation_analysis import symmetric_decomposition, question_support_decomposition, metric, exact_reference_checks


def test_composition_and_within_cancel_even_when_overall_fpr_is_unchanged():
    # Easier error group shrinks while within-group performance improves.
    c, h, d = symmetric_decomposition([80, 20], [16, 16], [50, 50], [5, 27])
    assert np.isclose(d, 0)
    assert c.sum() > 0 and h.sum() < 0
    assert np.isclose(c.sum()+h.sum(), d)


def test_decomposition_support_and_swap_symmetry():
    c, h, d = symmetric_decomposition([10, 0], [3, 0], [10, 5], [4, 1])
    assert np.isnan(c[1]) and np.isnan(h[1])
    a = symmetric_decomposition([12, 17], [3, 9], [9, 15], [4, 6])
    b = symmetric_decomposition([9, 15], [4, 6], [12, 17], [3, 9])
    for x, y in zip(a, b): assert np.allclose(x, -y)
    assert np.isclose(a[0].sum()+a[1].sum(), a[2])


def test_two_sided_absent_cells_do_not_invalidate_decomposition():
    c, h, d = symmetric_decomposition([10, 0], [3, 0], [10, 0], [4, 0])
    assert c[1] == h[1] == 0
    assert np.isclose(c.sum()+h.sum(), d)


def test_question_entry_exit_keeps_unobserved_rates_out_of_common_support_terms():
    r = question_support_decomposition([1,0,1],[1,0,0],[1,1,0],[0,1,0])
    assert r['common_composition']==0 and r['common_within']==-.5
    assert r['new_error_question_entry']==.5 and r['old_error_question_exit']==0
    assert r['total']==0
    assert np.isclose(sum(v for k,v in r.items() if k!='total'),r['total'])


def test_unknown_fpr_bounds_equal_exhaustive_truth_assignments():
    def row(y, accept):
        return {'correct': y, 'accepted': accept, 'truncated': False, 'output_tokens': 10}
    known = [row(False, True), row(False, False), row(True, True)]
    unknown = [row(None, True), row(None, True), row(None, False)]
    m = metric(known+unknown)
    rates = []
    for truth in itertools.product([True, False], repeat=3):
        filled = [row(y, r['accepted']) for y, r in zip(truth, unknown)]
        rates.append(metric(known+filled)['FPR'])
    assert m['FPR_unknown_lower'] == min(rates)
    assert m['FPR_unknown_upper'] == max(rates)


def test_exact_reference_audit_detects_sum_of_all_integer_roots_issue():
    checks = exact_reference_checks()
    c = checks['integer_cubic']
    assert c['frozen_reference'] == '6' and c['recomputed'] == '1'
    assert sorted(c['complete_solution']) == [[-5, -6], [6, 5]]
    assert len(c['finite_cases']) == 5
    assert checks['base_sequence']['a2013'] == 23097
    assert checks['rational_equation']['a_plus_b'] == 14801
