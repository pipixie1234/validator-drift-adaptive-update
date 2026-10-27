import numpy as np

from pilot.awl_comparison import membership, variants
from pilot.awl_comparison_report import matrix_contrast
from pilot.day2_protocol import label_response


def test_word_family_membership_includes_derivations_and_not_tone_guesses():
    m=membership()
    assert m['analysis'][0]['headword']=='analyse'
    assert m['formulate'][0]['headword']=='formula'
    assert 'require' in m and 'required' in m
    assert all(w not in m for w in ['essential','necessary','reasoning','determine'])


def test_only_answer_independent_headers_preserve_truth():
    text='Incorrect reasoning. Final answer: \\boxed{3}'
    old=label_response(text,False,'4','gsm8k')
    for v in variants():
        new=label_response(v['prefix']+text,False,'4','gsm8k')
        assert all(new[k]==old[k] for k in ['correct','value','status'])


def test_group_contrast_counts_questions_not_word_injections_as_independent():
    a=np.array([[1,1],[0,1],[0,0],[1,0]],float);b=np.zeros_like(a)
    out=matrix_contrast(a,b,draws=1000)
    assert out['questions']==4 and out['lexical_pairs']==2
    assert out['AWL_mean_flip_rate']==out['delta_AWL_minus_nonAWL']==.5
    swapped=matrix_contrast(b,a,draws=1000)
    assert swapped['delta_AWL_minus_nonAWL']==-.5
    assert out['question_CI_low']==-swapped['question_CI_high']


def test_no_group_difference_never_becomes_significant():
    a=np.array([[1,0],[0,1],[1,1]],float)
    result=matrix_contrast(a,a,draws=100)
    assert result['delta_AWL_minus_nonAWL']==0 and result['p_question_signflip']==1
    assert result['question_CI_low']==result['crossed_CI_high']==0
