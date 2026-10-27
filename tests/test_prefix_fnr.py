import numpy as np

from pilot.prefix_fnr import correct_population
from pilot.prefix_fnr_report import transitions,cluster_contrast


def test_correct_population_keeps_both_original_decisions():
    rows=[{'G_round':'G3','split':'C','candidate_id':str(i),'question_id':'q','correct':correct,
           'accepted':accepted,'truncated':truncated} for i,(correct,accepted,truncated) in enumerate([
               (True,True,False),(True,False,False),(False,True,False),(None,False,False),(True,False,True)])]
    assert {r['candidate_id'] for r in correct_population(rows)}=={'0','1'}


def test_fnr_net_counts_both_rescue_and_damage():
    result=transitions([False,False,True,True,True],[True,False,False,False,True])
    assert result=={'original_FN':2,'injected_FN':3,'rescued_FN_to_TP':1,'new_TP_to_FN':2,'net_added_FN':1}


def test_question_bootstrap_keeps_sibling_answers_together():
    # Two questions have three and one siblings; sample each question twice in turn.
    W=np.array([[2,0],[0,2],[1,1]])
    point,boot=cluster_contrast([0,0,0,1],[1,1,1,0],np.array([0,0,0,1]),W)
    assert point['delta']==-.5
    np.testing.assert_allclose(boot,[-1,1,-.5])
