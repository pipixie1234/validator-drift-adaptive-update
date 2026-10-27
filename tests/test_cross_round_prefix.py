from pilot.cross_round_prefix import variants,choose
from pilot.day2_protocol import label_response


def row(q,g,i=0,accepted=False,correct=False):
    return {'split':'C','question_id':q,'G_round':g,'candidate_id':q+g+str(i),
            'accepted':accepted,'correct':correct,'truncated':False}


def test_selection_requires_rejected_wrong_answer_in_every_stage():
    rows=[row('common',g) for g in ['G0','G1','G2','G3']]
    rows += [row('missing',g) for g in ['G0','G1','G2']]
    rows += [row('accepted_in_G3',g,accepted=g=='G3') for g in ['G0','G1','G2','G3']]
    chosen,support=choose(rows,'C',48)
    assert support['common_questions']==support['selected_questions']==1
    assert {r['question_id'] for r in chosen}=={'common'} and len(chosen)==4


def test_candidate_choice_is_invariant_to_input_order_and_score_margin():
    rows=[row('q',g,i) for g in ['G0','G1','G2','G3'] for i in range(4)]
    a,_=choose(rows,'C',48)
    for i,r in enumerate(rows):r['score']=i/100
    b,_=choose(list(reversed(rows)),'C',48)
    assert [r['candidate_id'] for r in a]==[r['candidate_id'] for r in b]


def test_all21_prefixes_preserve_a_wrong_explicit_answer():
    text=r'An incorrect calculation gives the final answer \boxed{7}.'
    for v in variants():
        lab=label_response(v['prefix']+text,False,'8','gsm8k')
        assert lab['correct'] is False and lab['value']=='7'
    assert len(variants())==len({v['prefix'] for v in variants()})==21


def test_round_difference_keeps_both_directions_of_question_paired_flips():
    import numpy as np
    from pilot.cross_round_prefix_report import paired_change
    r=paired_change([1,1,0,0],[0,1,1,0],np.array([.5,.75,.25]),np.array([.5,.25,.75]))
    assert r['delta']==0 and r['after_only_flips']==r['before_only_flips']==r['both_flipped']==1
    assert r['p_McNemar']==1
