import numpy as np
from pilot.fpr_pattern_diagnostics import bh, demean, grams, lexical_fit
from pilot.efp_intervention import opening_grams, wilson
from pilot.site_diagnostics import topic


def test_BH_adjustment_ignores_unestimable_tests_and_preserves_order():
    result=bh([.04,.001,float('nan'),.03])
    assert np.allclose(result[[0,1,3]],[.04,.003,.04])
    assert np.isnan(result[2])


def test_fixed_effect_demeaning_handles_multi_feature_matrix():
    x=np.array([[1,2],[3,4],[7,9]],float)
    g=np.array([0,0,1])
    assert np.allclose(demean(x,g),[[-1,-1],[1,1],[0,0]])


def test_lexical_model_removes_pure_question_level_spurious_association():
    rows=[];features=[]
    for q in range(20):
        for g in ['G2','G3']:
            for j in range(4):
                rows.append({'question_id':str(q),'G_round':g,'accepted':q<10,'output_tokens':20+j})
                features.append([q<10])
    r=lexical_fit(rows,np.asarray(features,float),['confounded'])[0]
    assert not r['eligible'] and r['p'] is None and r['coefficient'] is None


def test_lexical_model_detects_within_question_effect_without_crossing_slots():
    rows=[];features=[]
    for q in range(30):
        for g in ['G2','G3']:
            for j in range(4):
                present=j<2
                accepted=present if q%5 else not present
                rows.append({'question_id':str(q),'G_round':g,'accepted':accepted,'output_tokens':20+(j%2)})
                features.append([present])
    r=lexical_fit(rows,np.asarray(features,float),['cue'])[0]
    assert r['eligible'] and r['informative_questions']==30
    assert abs(r['coefficient']-.6)<1e-10 and r['p']<.01


def test_opening_discovery_preserves_stopwords_and_excludes_math_specific_tail():
    f=opening_grams('To solve the problem, we need to find $x=123$.')
    assert 'we need to' in f and 'to solve the problem' in f
    assert not any('123' in k or 'x' in k.split() for k in f)
    assert 'boxed' not in grams(r'Thus \\boxed{42}.')


def test_topic_proxy_does_not_label_perfect_squares_as_geometry():
    assert topic('How many integers are perfect squares?')=='number_theory'
    assert topic('A square with side length 3 has what area?')=='geometry'
    assert topic('Find roots of this quadratic equation.')=='algebra'


def test_zero_flips_still_have_nonzero_upper_uncertainty_bound():
    lo,hi=wilson(0,48)
    assert abs(lo)<1e-12 and .05<hi<.1
