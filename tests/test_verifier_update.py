from copy import deepcopy

from pilot.verifier_update import targeted_selection,matched_random
from pilot.verifier_update_report import judgment_transition


def examples():
    return [{'G_round':'G0','correct':y,'question_id':str(i),'candidate_id':str(y)+str(i),
             'V0_accepted':not y if i<4 else y,'V0_score':.4,'prompt_hash':str(y)+str(i),
             'input_tokens':256,'length_bin':'le512'} for y in [True,False] for i in range(8)]


def test_target_prioritizes_misjudgments_with_valid_controls():
    chosen=targeted_selection(examples(),{('G0',True):4,('G0',False):4})
    assert len(chosen)==8
    for y in [True,False]:
        rows=[r for r in chosen if r['correct'] is y]
        assert len(rows)==len({r['question_id'] for r in rows})==4
        assert sum(r['V0_accepted']!=y for r in rows)==3


def test_random_selection_does_not_use_V0_scores_or_decisions():
    rows=examples();target=targeted_selection(rows,{('G0',True):4,('G0',False):4})
    first,_=matched_random(rows,target)
    changed=deepcopy(rows)
    for r in changed:r['V0_accepted']=not r['V0_accepted'];r['V0_score']=1-r['V0_score']
    second,_=matched_random(changed,target)
    assert [r['candidate_id'] for r in first]==[r['candidate_id'] for r in second]


def test_fixed_answer_judgment_transition_excludes_unknown_and_counts_harm():
    rows=[{'candidate_id':str(i),'correct':y} for i,y in enumerate([True,True,False,False,None])]
    old=dict(zip(map(str,range(5)),[True,True,True,True,False]))
    new=dict(zip(map(str,range(5)),[True,False,False,True,True]))
    r=judgment_transition(rows,old,new,'G2_TF','Vtarget')
    assert [r[k] for k in ['a_old_right_new_right','b_old_right_new_wrong','c_old_wrong_new_right','d_old_wrong_new_wrong']]==[1,1,1,1]
    assert r['unknown_excluded']==1 and r['net_repaired']==0
